"""Offline option-universe lookups — every listed option underlying + its lot.

``option_universe.json`` is snapshotted from a full Kite instruments dump by
``scripts/build_universe.py --instruments``: for each NFO/BFO/MCX option
underlying, the FRONT-expiry lot size (the lot a ticket bought today actually
trades), whether it is a stock/index/commodity, and its expiry span.

This is the offline complement to the live instrument master: the pack
pipeline and precompute can price real premium-x-lot tickets without a DB or
Kite session, from dated exchange data instead of guesses. When an underlying
is not in the snapshot the answer is ``None`` — never a fabricated lot.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Optional

_JSON = os.path.join(os.path.dirname(__file__), "option_universe.json")


@lru_cache(maxsize=1)
def load_universe() -> dict[str, Any]:
    try:
        with open(_JSON) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"exchanges": {}}


def entry(underlying: str, exchange: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The snapshot row for ``underlying`` (``.NS`` suffixes tolerated).
    Searches NFO first, then BFO, then MCX unless ``exchange`` pins one."""
    sym = str(underlying).upper().replace(".NS", "").lstrip("^")
    exchanges = load_universe().get("exchanges", {})
    order = [exchange] if exchange else ["NFO", "BFO", "MCX"]
    for exch in order:
        row = (exchanges.get(exch) or {}).get(sym)
        if row:
            return {**row, "underlying": sym, "exchange": exch}
    return None


def lot_for(underlying: str) -> Optional[int]:
    row = entry(underlying)
    return int(row["lot"]) if row else None


def underlyings(exchange: str = "NFO", kind: Optional[str] = None) -> list[str]:
    """All snapshotted option underlyings on ``exchange`` (optionally filtered
    to ``kind`` in {"stock", "index", "commodity"})."""
    seg = load_universe().get("exchanges", {}).get(exchange, {})
    return sorted(
        sym for sym, row in seg.items()
        if kind is None or row.get("kind") == kind
    )


def as_of() -> Optional[str]:
    return load_universe().get("generated_on")
