"""Non-equity instrument autosuggest — ETFs, MCX commodities, indices.

Merged into ``GET /api/companies/search`` (backend/routers/companies.py) so
the ONE search endpoint every FE search box already uses (global bar, chart
"Compare to…") surfaces the full instrument palette Pivot offers, not just
`mc.companies` equities.

Sources (all already in the repo — nothing fetched at runtime):
  * ETFs        → backend/view_markets/etf_catalog.json (38 categories with
                  primaries + alternates, keyword `matches`, `tracks` label).
  * Commodities → backend.view_markets.expressions.commodities.MCX_COMMODITIES
                  (register-not-execute tradeable per the India scope).
  * Indices     → small static list (NIFTY / BANKNIFTY / SENSEX / FINNIFTY).

Off-exchange mutual funds are OUT of scope by product principle — the listed
ETF proxy IS the offering, so MF-ish queries ("index fund", "gold fund")
resolve to the corresponding ETF rows via keywords.

The result rows reuse the CompanySearchResult shape: ``sector`` carries the
human type label ("ETF — Nifty 50", "Commodity — MCX", "Index"), so the FE
dropdown renders them with zero changes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_ETF_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "view_markets" / "etf_catalog.json"
)


@dataclass(frozen=True)
class InstrumentHit:
    symbol: str
    name: str
    type_label: str  # rendered in the FE's `sector` slot
    keywords: tuple[str, ...]  # lowercase match terms beyond symbol/name


_INDICES: tuple[InstrumentHit, ...] = (
    InstrumentHit("NIFTY", "Nifty 50", "Index",
                  ("nifty", "nifty50", "nifty 50", "index")),
    InstrumentHit("BANKNIFTY", "Nifty Bank", "Index",
                  ("banknifty", "bank nifty", "banking index", "index")),
    InstrumentHit("SENSEX", "BSE Sensex", "Index", ("sensex", "bse", "index")),
    InstrumentHit("FINNIFTY", "Nifty Financial Services", "Index",
                  ("finnifty", "financial services index", "index")),
)

# MF-ish phrasings resolve to ETF rows (the listed proxy IS the offering).
_MF_SYNONYMS = ("mutual fund", "mf", "fund")


@lru_cache(maxsize=1)
def _catalog() -> tuple[InstrumentHit, ...]:
    """The full static instrument list, built once per process."""
    hits: list[InstrumentHit] = list(_INDICES)

    # ── ETFs ──────────────────────────────────────────────────────────
    try:
        raw = json.loads(_ETF_CATALOG_PATH.read_text())
        for cat in (raw.get("categories") or {}).values():
            tracks = str(cat.get("tracks") or "").strip()
            kw = tuple(
                str(m).lower() for m in (cat.get("matches") or [])
            ) + (tracks.lower(), "etf") + _MF_SYNONYMS
            primary = str(cat.get("symbol") or "").strip().upper()
            if primary:
                hits.append(InstrumentHit(
                    primary, f"{tracks} ETF" if tracks else f"{primary} ETF",
                    f"ETF — {tracks}" if tracks else "ETF", kw,
                ))
            for alt in cat.get("alternates") or []:
                alt_sym = str(alt.get("symbol") or "").strip().upper()
                if alt_sym:
                    hits.append(InstrumentHit(
                        alt_sym,
                        f"{tracks} ETF" if tracks else f"{alt_sym} ETF",
                        f"ETF — {tracks}" if tracks else "ETF", kw,
                    ))
    except Exception:  # noqa: BLE001 — a broken catalog must not kill search
        pass

    # ── MCX commodities ───────────────────────────────────────────────
    try:
        from backend.view_markets.expressions.commodities import MCX_COMMODITIES

        for spec in MCX_COMMODITIES.values():
            hits.append(InstrumentHit(
                spec.symbol, spec.name, "Commodity — MCX",
                (spec.name.lower(), spec.group.replace("_", " "),
                 "commodity", "mcx"),
            ))
    except Exception:  # noqa: BLE001
        pass

    # De-dupe by symbol, first (primary) definition wins.
    seen: set[str] = set()
    out: list[InstrumentHit] = []
    for h in hits:
        if h.symbol not in seen:
            seen.add(h.symbol)
            out.append(h)
    return tuple(out)


def _score(h: InstrumentHit, qu: str, ql: str) -> int | None:
    """Lower is better; None = no match."""
    if h.symbol == qu:
        return 0
    if h.symbol.startswith(qu):
        return 1
    if any(k == ql for k in h.keywords):
        return 2
    if ql in h.name.lower():
        return 3
    if len(ql) >= 3 and any(ql in k for k in h.keywords):
        return 4
    if len(qu) >= 3 and qu in h.symbol:
        return 4
    # Token-AND: every query token appears somewhere in the hit's text
    # ("silver etf" → SILVERBEES via keywords "silver" + "etf").
    tokens = ql.split(" ")
    if len(tokens) > 1:
        haystack = " ".join((h.symbol.lower(), h.name.lower(), *h.keywords))
        if all(t in haystack for t in tokens):
            return 2
    return None


def search_instruments(q: str, limit: int = 10) -> list[dict]:
    """Rank the static catalog against ``q``.

    Returns CompanySearchResult-shaped dicts (``sector`` = type label,
    ``has_fundamentals`` False, ``logo_url`` None → FE monogram).
    """
    ql = re.sub(r"\s+", " ", (q or "").strip().lower())
    if not ql:
        return []
    qu = ql.upper()
    scored = [
        (s, h) for h in _catalog() if (s := _score(h, qu, ql)) is not None
    ]
    scored.sort(key=lambda t: (t[0], t[1].symbol))
    return [
        {
            "symbol": h.symbol,
            "name": h.name,
            "sector": h.type_label,
            "has_fundamentals": False,
            "logo_url": None,
            "_score": s,
        }
        for s, h in scored[: max(1, limit)]
    ]
