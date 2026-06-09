"""Option-chain snapshot service (F&O P0).

One entrypoint: ``get_chain(db, underlying, expiry=None, width=10)`` →
an ATM-centered slice of the chain with per-strike quotes, IV (status-
flagged, never fabricated) and Black-76 Greeks priced off the same-
expiry future (synthetic forward when the future is illiquid).

Data path & rate-limit posture:
  * Strikes/expiries/lots come from ``instrument_master`` (daily dump).
  * Quotes come from ONE batched ``kite.quote()`` sweep — ≤200
    instruments per call (Kite's hard cap is 500; 200 leaves headroom
    and bounds tail latency), so a 41-row slice is a single call.
  * The decorated payload is cached in Redis for 5s under
    ``optchain:{U}:{EXPIRY}:{WIDTH}`` — Kite's feed is itself a ~1s
    snapshot, so 5s loses nothing while one fetch fans out to every
    concurrent viewer of the same chain. A short NX lock prevents a
    thundering herd on cache expiry. Workflow watchers and screeners
    MUST read through this cache — never call Kite directly.
  * Mock mode synthesizes a deterministic chain (Black-76 premiums on a
    smile, bell-curve OI) so dev/tests run credential-free.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from typing import Any, Optional

import numpy as np
import pytz
from sqlalchemy.orm import Session

from backend.cache import redis_client
from backend.kite.system import get_system_kite
from backend.market.greeks import (
    black76_price,
    compute_chain_greeks,
    synthetic_forward,
    year_fraction,
)
from backend.market import instrument_master as im

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

CHAIN_CACHE_TTL_S = 5
_QUOTE_BATCH = 200
_LOCK_TTL_S = 4
_DEFAULT_WIDTH = 10
_RISK_FREE = 0.065

# Mock spots per underlying — only consulted in mock mode. The fallback
# for an unknown mock underlying derives a spot from its strike ladder,
# so even mock mode has no required symbol list.
_MOCK_SPOTS = {
    "NIFTY": 23456.0, "BANKNIFTY": 51200.0, "RELIANCE": 1320.0,
    "SENSEX": 77000.0, "CRUDEOIL": 5800.0,
}


def _cache_key(underlying: str, expiry: date, width: int) -> str:
    return f"optchain:{underlying}:{expiry.isoformat()}:{width}"


def _read_cache(key: str) -> Optional[dict]:
    try:
        raw = redis_client.get(key)
        if raw:
            return json.loads(raw if isinstance(raw, str) else raw.decode())
    except Exception:
        pass
    return None


def _write_cache(key: str, payload: dict) -> None:
    try:
        redis_client.set(key, json.dumps(payload), ex=CHAIN_CACHE_TTL_S)
    except Exception:
        pass


def _acquire_lock(key: str) -> bool:
    """Best-effort NX lock; on any Redis weirdness, fetch anyway."""
    try:
        return bool(redis_client.set(key, "1", ex=_LOCK_TTL_S, nx=True))
    except TypeError:  # MockRedis pre-nx signature safety net
        return True
    except Exception:
        return True


# ── Mock quotes ──────────────────────────────────────────────────────


def _mock_smile_iv(strike: np.ndarray, F: float, base_iv: float) -> np.ndarray:
    """Symmetric smile with a put-side tilt — visually plausible, stable."""
    m = strike / F - 1.0
    return base_iv + 1.6 * m * m + 0.08 * np.maximum(-m, 0.0)


def _mock_quotes(
    rows: list, F: float, T: float, lot: int,
) -> dict[int, dict[str, Any]]:
    """Deterministic synthetic quotes per instrument_token."""
    strikes = np.array([float(r.strike) for r in rows])
    flags = np.array([1.0 if r.instrument_type == "CE" else -1.0 for r in rows])
    base_iv = 0.12 if F > 10_000 else 0.22
    iv = _mock_smile_iv(strikes, F, base_iv)
    mids = black76_price(F, strikes, iv, T, r=_RISK_FREE, flag=flags)
    out: dict[int, dict[str, Any]] = {}
    for r, mid in zip(rows, mids):
        mid = float(max(mid, 0.05))
        spread = max(0.05, mid * (0.006 if F > 10_000 else 0.02))
        moneyness = abs(float(r.strike) / F - 1.0)
        oi = int(max(0.0, 1.0 - 8.0 * moneyness) * 4_000_000)
        # Round-number strikes attract OI — mirrors real chains.
        if float(r.strike) % 500 == 0:
            oi = int(oi * 1.5)
        out[int(r.instrument_token)] = {
            "ltp": round(mid, 2),
            "bid": round(max(mid - spread / 2.0, 0.05), 2),
            "ask": round(mid + spread / 2.0, 2),
            "oi": oi,
            "volume": oi // 10,
        }
    return out


# ── Live quotes ──────────────────────────────────────────────────────


def _kite_quotes(kite, instruments: list[str]) -> dict[str, dict]:
    quotes: dict[str, dict] = {}
    for i in range(0, len(instruments), _QUOTE_BATCH):
        batch = instruments[i:i + _QUOTE_BATCH]
        try:
            quotes.update(kite.quote(batch) or {})
        except Exception as exc:
            logger.warning("[option-chain] quote batch failed: %s", exc)
    return quotes


def _depth_top(q: dict, side: str) -> float:
    try:
        return float(q["depth"][side][0]["price"] or 0.0)
    except Exception:
        return 0.0


def _exchange_prefix(segment: str) -> str:
    return segment.split("-", 1)[0]


def _chain_aggregates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """max_pain / pcr_oi / pcr_volume over the ATM-centred slice.

    Honest absence: a field is omitted (left None) when the inputs aren't
    present, never fabricated. These are trivially derivable from the
    per-strike OI/volume already on ``rows`` and must ship on the payload
    so the chat layer quotes real numbers instead of hand-waving prose."""
    put_oi = sum(float((r.get("pe") or {}).get("oi") or 0) for r in rows)
    call_oi = sum(float((r.get("ce") or {}).get("oi") or 0) for r in rows)
    put_vol = sum(float((r.get("pe") or {}).get("volume") or 0) for r in rows)
    call_vol = sum(float((r.get("ce") or {}).get("volume") or 0) for r in rows)

    pcr_oi = round(put_oi / call_oi, 2) if call_oi > 0 else None
    pcr_volume = round(put_vol / call_vol, 2) if call_vol > 0 else None

    # max pain = strike minimising total writer payout (intrinsic·OI) over
    # both sides, scanned across every strike in the slice.
    max_pain = None
    if any((r.get("ce") or {}).get("oi") or (r.get("pe") or {}).get("oi") for r in rows):
        best_pain = None
        for r_test in rows:
            s = r_test["strike"]
            pain = 0.0
            for r in rows:
                k = r["strike"]
                ce_oi = float((r.get("ce") or {}).get("oi") or 0)
                pe_oi = float((r.get("pe") or {}).get("oi") or 0)
                pain += max(0.0, s - k) * ce_oi + max(0.0, k - s) * pe_oi
            if best_pain is None or pain < best_pain:
                best_pain, max_pain = pain, s

    return {
        "max_pain": max_pain,
        "pcr_oi": pcr_oi,
        "pcr_volume": pcr_volume,
        "total_call_oi": int(call_oi) if call_oi else None,
        "total_put_oi": int(put_oi) if put_oi else None,
    }


# ── Public API ───────────────────────────────────────────────────────


def get_chain(
    db: Session,
    underlying: str,
    expiry: Optional[str] = None,
    *,
    width: int = _DEFAULT_WIDTH,
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """ATM-centered chain slice with quotes + IV + Greeks.

    ``width`` = strikes each side of ATM. Returns ``None`` when the
    underlying/expiry isn't in the master (caller surfaces "unknown
    underlying", never a fabricated chain)."""
    underlying = (underlying or "").strip().upper()
    resolved = im.resolve_expiry(db, underlying, expiry)
    if resolved is None:
        return None
    width = max(1, min(int(width), 40))

    key = _cache_key(underlying, resolved, width)
    cached = _read_cache(key)
    if cached:
        return cached

    # Thundering-herd guard: only one fetcher per (chain, ttl window);
    # losers briefly poll the cache, then fetch anyway as a last resort.
    if not _acquire_lock(f"{key}:lock"):
        for _ in range(6):
            time.sleep(0.25)
            cached = _read_cache(key)
            if cached:
                return cached

    instruments = im.chain_instruments(db, underlying, resolved)
    if not instruments:
        return None
    segment = instruments[0].segment
    lot = im.get_lot_size(db, underlying, resolved) or 0
    T = year_fraction(resolved, segment=segment, now=now)

    fut = im.future_instrument(db, underlying, resolved)
    kite = get_system_kite(db)
    source = "kite" if kite is not None else "mock"

    spot: Optional[float] = None
    future_ltp: Optional[float] = None
    token_quotes: dict[int, dict[str, Any]] = {}

    if kite is None:
        F0 = _MOCK_SPOTS.get(
            underlying,
            float(np.median([float(r.strike) for r in instruments])),
        )
        future_ltp = F0 * 1.001  # tiny positive basis
        spot = F0
        token_quotes = _mock_quotes(instruments, future_ltp, T, lot)
    else:
        prefix = _exchange_prefix(segment)
        keys = [f"{prefix}:{r.tradingsymbol}" for r in instruments]
        if fut is not None:
            keys.append(f"{_exchange_prefix(fut.segment)}:{fut.tradingsymbol}")
        raw = _kite_quotes(kite, keys)
        by_symbol = {k.split(":", 1)[1]: v for k, v in raw.items()}
        if fut is not None:
            fq = by_symbol.get(fut.tradingsymbol)
            if fq:
                future_ltp = float(fq.get("last_price") or 0.0) or None
        for r in instruments:
            q = by_symbol.get(r.tradingsymbol)
            if not q:
                continue
            token_quotes[int(r.instrument_token)] = {
                "ltp": float(q.get("last_price") or 0.0),
                "bid": _depth_top(q, "buy"),
                "ask": _depth_top(q, "sell"),
                "oi": int(q.get("oi") or 0),
                "volume": int(q.get("volume") or q.get("volume_traded") or 0),
            }

    # Assemble per-strike rows (full ladder first; slice after ATM is known).
    by_strike: dict[float, dict[str, Any]] = {}
    for r in instruments:
        strike = float(r.strike or 0.0)
        if strike <= 0.0:
            continue
        row = by_strike.setdefault(strike, {"strike": strike, "ce": None, "pe": None})
        q = token_quotes.get(int(r.instrument_token))
        if q is not None:
            side = "ce" if r.instrument_type == "CE" else "pe"
            row[side] = {**q, "tradingsymbol": r.tradingsymbol,
                         "instrument_token": int(r.instrument_token)}
    all_rows = [by_strike[k] for k in sorted(by_strike)]
    if not all_rows:
        return None

    # Forward: same-expiry future LTP → synthetic (put-call parity) → spot.
    def _mid_of(row_side: Optional[dict]) -> float:
        if not row_side:
            return 0.0
        bid, ask = row_side.get("bid") or 0.0, row_side.get("ask") or 0.0
        if bid > 0 and ask >= bid:
            return (bid + ask) / 2.0
        return float(row_side.get("ltp") or 0.0)

    forward = future_ltp
    forward_source = "future"
    if not forward:
        anchor = spot or float(np.median([r["strike"] for r in all_rows]))
        syn = synthetic_forward(
            [r["strike"] for r in all_rows],
            [_mid_of(r["ce"]) for r in all_rows],
            [_mid_of(r["pe"]) for r in all_rows],
            anchor, T, r=_RISK_FREE,
        )
        if syn:
            forward, forward_source = syn, "synthetic"
        elif spot:
            forward, forward_source = spot, "spot"
        else:
            forward, forward_source = anchor, "strike_median"

    atm_strike = min(all_rows, key=lambda r: abs(r["strike"] - forward))["strike"]
    atm_idx = next(i for i, r in enumerate(all_rows) if r["strike"] == atm_strike)
    rows = all_rows[max(0, atm_idx - width):atm_idx + width + 1]

    compute_chain_greeks(rows, forward, T, r=_RISK_FREE)

    # Expected move (1σ to expiry) off ATM IV; straddle fallback.
    expected_move = None
    atm_row = next((r for r in rows if r["strike"] == atm_strike), None)
    if atm_row:
        ivs = [
            atm_row[s]["iv"] for s in ("ce", "pe")
            if atm_row.get(s) and atm_row[s].get("iv")
        ]
        if ivs and T > 0:
            em = forward * (sum(ivs) / len(ivs)) * float(np.sqrt(T))
        else:
            straddle = _mid_of(atm_row.get("ce")) + _mid_of(atm_row.get("pe"))
            em = 0.8 * straddle if straddle > 0 else None
        if em:
            expected_move = {
                "low": round(forward - em, 2),
                "high": round(forward + em, 2),
                "abs": round(em, 2),
                "pct": round(em / forward * 100.0, 2),
            }

    aggregates = _chain_aggregates(rows)

    payload = {
        "underlying": underlying,
        "segment": segment,
        "exchange": instruments[0].exchange,
        "expiry": resolved.isoformat(),
        "expiries": im.list_expiries(db, underlying)[:6],
        "lot_size": lot or None,
        "spot": spot,
        "forward": round(float(forward), 2),
        "forward_source": forward_source,
        "atm_strike": atm_strike,
        "expected_move": expected_move,
        "max_pain": aggregates["max_pain"],
        "pcr_oi": aggregates["pcr_oi"],
        "pcr_volume": aggregates["pcr_volume"],
        "total_call_oi": aggregates["total_call_oi"],
        "total_put_oi": aggregates["total_put_oi"],
        "t_years": round(T, 6),
        "rows": rows,
        "research_only": segment == "MCX-OPT",
        "source": source,
        "asof": datetime.now(IST).isoformat(timespec="seconds"),
    }
    _write_cache(key, payload)
    return payload
