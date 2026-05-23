"""Tree → OHLCV per (symbol, exchange).

Walks a Tree once to find every leaf that references market data,
then batch-loads daily bars for the requested date range. Returned
mapping is keyed by ``(symbol_upper, exchange_upper)`` for O(1)
accessor lookups.

We reuse ``backend.backtester.engine._fetch_ohlcv`` so live ↔
backtest data sources stay identical (any future swap — Kite
historical, a cached parquet store — touches one helper, both
engines).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

from backend.workflows.dsl.schema import (
    IndicatorNode,
    PriceNode,
    VolumeNode,
)
from backend.workflows.dsl.validators import _walk_all

logger = logging.getLogger(__name__)


SymbolKey = tuple[str, str]   # (symbol_upper, exchange_upper)


@dataclass(frozen=True)
class LoadedBars:
    """Output of ``load_bars``. Holds OHLCV per symbol plus the
    master calendar (intersection of all symbols' available trading
    days)."""

    by_symbol: dict[SymbolKey, pd.DataFrame]
    master_dates: pd.DatetimeIndex

    def primary(self) -> Optional[pd.DataFrame]:
        """Convenience — the first symbol's bars. The engine uses this
        for entry/exit prices when only one symbol is in the tree."""
        if not self.by_symbol:
            return None
        first_key = next(iter(self.by_symbol))
        return self.by_symbol[first_key]


def collect_symbols(tree) -> list[SymbolKey]:
    """Find every market-data leaf in the tree and return its
    (symbol, exchange) tuple. Deduplicated, deterministic order."""
    seen: dict[SymbolKey, None] = {}   # preserves insertion order
    for node in _walk_all(tree):
        if isinstance(node, (IndicatorNode, PriceNode, VolumeNode)):
            key = (node.symbol.upper(), node.exchange.upper())
            seen.setdefault(key, None)
    return list(seen.keys())


def load_bars(
    tree,
    *,
    start: date,
    end: date,
    fetcher=None,
) -> LoadedBars:
    """Load daily OHLCV for every symbol referenced in ``tree``.

    ``fetcher`` is the per-symbol fetch function. Defaults to
    ``backend.backtester.engine._fetch_ohlcv`` — pass a different
    callable in tests so we don't hit yfinance. Signature:
    ``fetcher(symbol: str, start: date, end: date) -> pd.DataFrame``.

    Raises ``ValueError`` if the tree references no market data
    (only constants), if the master calendar would be empty, or if
    any individual fetch fails outright.
    """
    keys = collect_symbols(tree)
    if not keys:
        raise ValueError(
            "tree has no market-data leaves — nothing to backtest. "
            "Add at least one indicator / price / volume node."
        )

    if fetcher is None:
        from backend.backtester.engine import _fetch_ohlcv as _real_fetcher
        fetcher = _real_fetcher

    by_symbol: dict[SymbolKey, pd.DataFrame] = {}
    for sym, exch in keys:
        df = fetcher(sym, start, end)
        if df is None or df.empty:
            raise ValueError(f"no bars returned for {sym}")
        # Defensive: lowercase column names + ensure expected cols.
        df = df.rename(columns={c: str(c).lower() for c in df.columns})
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                # Volume is optional for price-only trees, but the
                # engine and indicator math need OHLC at minimum.
                if col == "volume":
                    df["volume"] = 0.0
                else:
                    raise ValueError(
                        f"{sym} bars missing required column {col!r}"
                    )
        # Drop tz so the master calendar can intersect cleanly.
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df = df.copy()
            df.index = df.index.tz_localize(None)
        by_symbol[(sym, exch)] = df

    # Master calendar = INTERSECTION of all symbols' dates. The
    # engine only sees bars where ALL referenced symbols have data,
    # so a multi-symbol tree never evaluates on a partial state.
    master = None
    for df in by_symbol.values():
        idx = pd.DatetimeIndex(df.index).normalize()
        master = idx if master is None else master.intersection(idx)
    if master is None or master.empty:
        raise ValueError(
            "no overlapping trading days across the referenced symbols "
            "for the requested date range"
        )

    # Re-align every per-symbol DataFrame on the master calendar.
    aligned: dict[SymbolKey, pd.DataFrame] = {}
    for key, df in by_symbol.items():
        df = df.copy()
        df.index = pd.DatetimeIndex(df.index).normalize()
        aligned[key] = df.reindex(master)

    return LoadedBars(by_symbol=aligned, master_dates=master.sort_values())
