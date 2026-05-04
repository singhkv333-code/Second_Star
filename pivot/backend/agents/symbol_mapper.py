"""Map Pivot sc_ids → NSE tickers using Sarvam, verify via yfinance.

The pivot-mc-scraper discover pass leaves ``mc.companies.nse_symbol`` NULL.
yfinance needs an NSE symbol like ``RELIANCE.NS`` to fetch prices, so we
have to bridge ``sc_id='RI'`` → ``RELIANCE``. Approach:

  1. Skip companies that already have a non-null ``nse_symbol``.
  2. Batch the rest into groups of ~30 and ask Sarvam to emit strict JSON
     ``[{"sc_id":..., "nse_symbol":"..."|null}]``.
  3. For each non-null answer, verify by issuing a 5-day ``yf.download`` —
     a missing/empty result means Sarvam hallucinated, mark NULL.
  4. Persist verified mappings to ``mc.companies.nse_symbol``.

Works on demand (not on the full 11k-row table). Callers should pass only
the sc_ids that actually appear in a screen / backtest universe.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Iterable

import asyncpg

from .sarvam_client import call_sarvam


logger = logging.getLogger(__name__)


_BATCH_SIZE = 30                       # safe Sarvam json payload size
_VERIFY_BATCH = 40                     # yfinance multi-symbol cap
_NSE_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")


_SYSTEM_PROMPT = """\
You convert Indian company names into their NSE (National Stock Exchange of India) cash-segment ticker symbols.

Rules:
- Reply with ONLY a JSON array, no prose, no markdown, no <think> blocks.
- Each element: {"sc_id": "<id>", "nse_symbol": "<TICKER>"} or {"sc_id": "<id>", "nse_symbol": null}.
- TICKER must be the exact uppercase NSE symbol used on www.nseindia.com (e.g. RELIANCE, INFY, TCS, HDFCBANK, BHARTIARTL, MARUTI, BAJFINANCE, HINDUNILVR, ITC, LT).
- Do NOT include the ".NS" suffix — only the bare ticker.
- Only emit a non-null symbol when you are confident the company is listed on NSE. If the company is BSE-only, delisted, a private subsidiary, a mutual-fund AMC scheme, or you genuinely don't know — set nse_symbol to null.
- Preserve the input order. Return one object per input sc_id, no duplicates, no extras.
- Do not invent tickers from the company name; if uncertain, return null.

Examples:
Input:
[{"sc_id": "RI", "company_name": "Reliance Industries"},
 {"sc_id": "X9", "company_name": "Some Tiny Unknown Co Ltd"}]

Output:
[{"sc_id": "RI", "nse_symbol": "RELIANCE"},
 {"sc_id": "X9", "nse_symbol": null}]
"""


async def map_and_persist(
    pool: asyncpg.Pool,
    sc_ids: list[str],
    *,
    force: bool = False,
    skip_verify: bool = False,
) -> dict:
    """Map the given sc_ids to NSE tickers, verify, and persist.

    Returns a summary dict with counts and the per-sc_id outcome. Idempotent —
    safe to call repeatedly; rows that already have a verified ``nse_symbol``
    are skipped unless ``force`` is set.
    """
    if not sc_ids:
        return {"total": 0, "already_mapped": 0, "asked_sarvam": 0,
                "verified": 0, "rejected": 0, "results": []}

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT sc_id, company_name, nse_symbol "
            "FROM mc.companies WHERE sc_id = ANY($1::text[])",
            sc_ids,
        )

    by_id = {r["sc_id"]: dict(r) for r in rows}
    targets = [
        (i, by_id[i]) for i in sc_ids
        if i in by_id and (force or not by_id[i]["nse_symbol"])
    ]
    already = len(sc_ids) - len(targets)

    if not targets:
        return {"total": len(sc_ids), "already_mapped": already, "asked_sarvam": 0,
                "verified": 0, "rejected": 0, "results": []}

    proposals: dict[str, str | None] = {}
    for batch in _chunks(targets, _BATCH_SIZE):
        batch_proposals = await _ask_sarvam(batch)
        proposals.update(batch_proposals)

    # Filter to plausible-looking tickers before spending yfinance calls.
    candidates = {
        sc: sym for sc, sym in proposals.items()
        if sym and _NSE_SYMBOL_RE.match(sym)
    }

    verified: dict[str, str] = {}
    rejected: dict[str, str] = {sc: "sarvam_null"
                                for sc, sym in proposals.items() if not sym}

    if skip_verify:
        verified = candidates
    else:
        verified, more_rejected = await _verify_batched(candidates)
        rejected.update(more_rejected)

    # Persist verified ones.
    if verified:
        async with pool.acquire() as conn:
            await conn.executemany(
                "UPDATE mc.companies SET nse_symbol = $2 WHERE sc_id = $1",
                list(verified.items()),
            )

    results = []
    for sc, _ in targets:
        sym = verified.get(sc)
        results.append({
            "sc_id": sc,
            "nse_symbol": sym,
            "status": "verified" if sym else "rejected",
            "reason": rejected.get(sc) if not sym else None,
        })

    return {
        "total": len(sc_ids),
        "already_mapped": already,
        "asked_sarvam": len(targets),
        "verified": len(verified),
        "rejected": len(rejected),
        "results": results,
    }


# ---- Sarvam call -------------------------------------------------------


async def _ask_sarvam(batch: list[tuple[str, dict]]) -> dict[str, str | None]:
    """Single Sarvam JSON-mode call for a batch of (sc_id, row)."""
    payload_in = [
        {"sc_id": sc, "company_name": row.get("company_name") or sc}
        for sc, row in batch
    ]
    user_msg = (
        "Map these companies. Reply with the JSON array only.\n\n"
        + json.dumps(payload_in, ensure_ascii=False)
    )

    try:
        resp = await call_sarvam(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=2000,
            json_mode=False,         # JSON object mode rejects top-level arrays.
        )
    except Exception as e:
        logger.warning("Sarvam mapping call failed: %s", e)
        return {sc: None for sc, _ in batch}

    content = (resp.get("content") or "").strip()
    parsed = _coerce_json_array(content)
    if not isinstance(parsed, list):
        logger.warning("Sarvam returned non-list mapping: %r", content[:200])
        return {sc: None for sc, _ in batch}

    out: dict[str, str | None] = {sc: None for sc, _ in batch}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        sc = item.get("sc_id")
        sym = item.get("nse_symbol")
        if sc in out:
            if isinstance(sym, str) and sym.strip():
                out[sc] = sym.strip().upper()
            else:
                out[sc] = None
    return out


def _coerce_json_array(text: str):
    """Sarvam sometimes wraps the array in markdown fences — peel them off."""
    if not text:
        return None
    # Strip ```json ... ``` fences if present.
    fence = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    # Find the first [ and last ] and parse that span — defensive.
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


# ---- yfinance verification ---------------------------------------------


async def _verify_batched(
    candidates: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (verified, rejected). Rejected reasons: 'no_yfinance_data'."""
    if not candidates:
        return {}, {}

    items = list(candidates.items())
    verified: dict[str, str] = {}
    rejected: dict[str, str] = {}

    # yfinance is sync + thread-pooled; run in an executor to keep us async.
    loop = asyncio.get_running_loop()
    for batch in _chunks(items, _VERIFY_BATCH):
        ok_syms = await loop.run_in_executor(None, _yf_verify_batch, [s for _, s in batch])
        for sc, sym in batch:
            if sym in ok_syms:
                verified[sc] = sym
            else:
                rejected[sc] = "no_yfinance_data"
    return verified, rejected


def _yf_verify_batch(symbols: list[str]) -> set[str]:
    """Return the subset of `symbols` for which yfinance has data."""
    import yfinance as yf
    import pandas as pd

    if not symbols:
        return set()
    tickers = " ".join(f"{s}.NS" for s in symbols)
    try:
        df = yf.download(
            tickers,
            period="1mo",
            interval="1d",
            auto_adjust=True,
            threads=False,
            progress=False,
            group_by="ticker",
        )
    except Exception as e:
        logger.warning("yfinance verification batch failed: %s", e)
        return set()

    if df is None or df.empty:
        return set()

    ok: set[str] = set()
    if isinstance(df.columns, pd.MultiIndex):
        # Multi-symbol response: each top-level column == "<SYM>.NS".
        for sym in symbols:
            key = f"{sym}.NS"
            if key in df.columns.get_level_values(0):
                sub = df[key]
                if not sub.empty and sub["Close"].notna().any():
                    ok.add(sym)
    else:
        # Single-symbol response (yfinance flattens for length-1 inputs).
        if not df.empty and df["Close"].notna().any() and len(symbols) == 1:
            ok.add(symbols[0])
    return ok


# ---- utils ------------------------------------------------------------


def _chunks(seq, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]
