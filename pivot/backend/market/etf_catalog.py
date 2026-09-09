"""Reader for the live-verified NSE ETF catalog (``etf_catalog.json``).

The catalog is BUILT (never hand-typed) by ``scripts/build_universe.py``:
every entry was verified against the Kite exchange dump and yfinance, and the
most liquid fund in each category won on real 20-day median traded value.
This module is the lookup layer the expression/affordability engines use to
substitute a cheap, liquid ETF for a basket exposure ("US tech" → MON100,
"bank rally" → BANKBEES) — the register-not-execute answer to tiny entries.

Honesty contract: if the catalog file is missing or a category can't be
matched, lookups return ``None`` — callers must degrade honestly, never
invent a ticker.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Optional

_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "etf_catalog.json")


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    """The full catalog payload ({generated_on, source, categories}); empty
    dict when the file has not been built."""
    if not os.path.exists(_CATALOG_PATH):
        return {}
    with open(_CATALOG_PATH) as fh:
        return json.load(fh)


def categories() -> dict[str, dict[str, Any]]:
    return dict(load_catalog().get("categories") or {})


def entry(category: str) -> Optional[dict[str, Any]]:
    """The catalog entry for an exact category key (e.g. ``"gold"``)."""
    e = categories().get(category)
    return dict(e, category=category) if e else None


def etf_for(*hints: str) -> Optional[dict[str, Any]]:
    """Best catalog entry for free-text hints (sector names, theme words).

    Matching: exact category key first, then case-insensitive containment
    against each entry's ``matches`` tag list. Ties break on liquidity
    (``adv_cr``). ``None`` when nothing matches — never a guess.
    """
    cats = categories()
    cleaned = [h.strip().lower() for h in hints if h and h.strip()]
    if not cleaned:
        return None
    for h in cleaned:
        if h in cats:
            return dict(cats[h], category=h)
    scored: list[tuple[float, str]] = []
    for key, e in cats.items():
        tags = [str(t).lower() for t in (e.get("matches") or [])] + [key]
        for h in cleaned:
            if any(h in t or t in h for t in tags):
                scored.append((float(e.get("adv_cr") or 0.0), key))
                break
    if not scored:
        return None
    _, best = max(scored)
    return dict(cats[best], category=best)
