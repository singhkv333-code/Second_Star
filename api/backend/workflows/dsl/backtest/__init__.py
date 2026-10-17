"""DSL-tree backtester (Phase B).

Same evaluator the live watcher uses, plus a bar-strict data
accessor so the SAME tree produces SAME-shaped trades over
historical bars.

Public surface (re-exported for callers):

  - ``BacktestDataAccessor``      bar-strict accessor (no-lookahead)
  - ``bar_loader.load_bars``      tree → OHLCV per (symbol, exchange)
  - ``engine.run_backtest``       sync wrapper around the loop
  - ``schema.{BacktestRequest, BacktestResult, ExitPolicy, ...}``

See ``docs/backtest_plan.md`` for design rationale.
"""
from __future__ import annotations
