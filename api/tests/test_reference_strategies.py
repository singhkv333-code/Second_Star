"""Phase 2.5 — reference-strategy acceptance tests, one per class.

A regression guard: one canonical strategy for each class Pivot can now backtest,
each asserted to come back **through the Phase-1 rigor ladder** (forward_stats +
monte_carlo + sub_periods + trust_verdict, with a coherent verdict). If a future
change breaks an engine, the look-ahead handling, or the rigor wiring, the matching
reference test fails.

- The single-symbol technical strategy runs on a SYNTHETIC fetcher (no network) —
  an always-on guard.
- The pairs / portfolio / factor-screen / cointegration strategies run on live
  data (yfinance / the financials DB) and skip cleanly when it's unreachable.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

VERDICTS = {"insufficient_data", "no_edge", "unproven", "promising"}


# ── shared: assert a result carries a coherent rigor ladder ──────────

def _as_dict(x):
    if x is None:
        return None
    if isinstance(x, dict):
        return x
    if hasattr(x, "model_dump"):
        return x.model_dump()
    return {k: getattr(x, k) for k in dir(x) if not k.startswith("_")}


def _assert_rigor_ladder(fs, mc, sp, tv, *, label: str):
    fs, mc, sp, tv = _as_dict(fs), _as_dict(mc), _as_dict(sp), _as_dict(tv)
    # forward_stats present with the Bailey/LdP keys
    assert fs is not None, f"{label}: forward_stats missing"
    for k in ("n_obs", "psr", "deflated_sharpe", "min_trl"):
        assert k in fs, f"{label}: forward_stats lacks {k}"
    assert (fs.get("n_obs") or 0) > 0, f"{label}: forward_stats n_obs == 0"
    # trust verdict present + in the allowed ladder
    assert tv is not None, f"{label}: trust_verdict missing"
    assert tv.get("verdict") in VERDICTS, f"{label}: bad verdict {tv.get('verdict')!r}"
    # monte-carlo + sub-periods attach for a normal-length daily strategy
    assert mc is not None, f"{label}: monte_carlo missing"
    assert sp is not None, f"{label}: sub_periods missing"


# ── reachability probes (skip live tests cleanly) ────────────────────

def _yf_ok() -> bool:
    try:
        from backend.market.yfinance_service import fetch_price_history
        return bool(fetch_price_history("RELIANCE", "1mo", "1d"))
    except Exception:
        return False


def _financials_dsn_or_skip():
    from tests.test_mc_field_contract import _real_financials_dsn
    dsn = _real_financials_dsn()
    if not dsn:
        pytest.skip("no Postgres financials DSN; skipping factor-screen reference test")
    return dsn


# ── 1 · single-symbol technical (synthetic fetcher — always runs) ────

def _oscillating(n=260):
    t = np.arange(n)
    closes = 100.0 + 15.0 * np.sin(t / 8.0)
    idx = pd.date_range("2023-01-02", periods=n, freq="B").normalize()
    return pd.DataFrame({"open": closes, "high": closes + 0.1, "low": closes - 0.1,
                         "close": closes, "volume": np.full(n, 1e6)}, index=idx)


def test_ref_single_symbol_rsi_mean_reversion():
    """Canonical: RSI(14) < 30 mean-reversion, 5-day hold. Deterministic synthetic
    data, so this guards the engine + the rigor wiring on every CI run."""
    from backend.workflows.dsl.backtest.engine import run_backtest
    from backend.workflows.dsl.backtest.schema import (
        BacktestRequest, ExitPolicyDeclarative as ExitPolicy,
    )

    def fetcher(symbol, start, end):
        df = _oscillating()
        m = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
        return df.loc[m].copy()

    tree = {"type": "comparison", "op": "<",
            "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
            "right": {"type": "constant", "value": 30}}
    req = BacktestRequest(
        tree=tree, primary_symbol="TCS",
        start_date=date(2023, 1, 1), end_date=date(2023, 12, 31),
        starting_capital=100_000.0, quantity=10,
        exit_policy=ExitPolicy(kind="n_day_hold", bars=5), save=False,
    )
    res = run_backtest(request=req, user_id=1, fetcher=fetcher)
    m = res.metrics
    assert m.total_trades > 0
    _assert_rigor_ladder(m.forward_stats, m.monte_carlo, m.sub_periods,
                         m.trust_verdict, label="single_symbol_rsi")


# ── 2 · pairs / stat-arb (live, skip if no yfinance) ─────────────────

def test_ref_pairs_cointegration():
    if not _yf_ok():
        pytest.skip("yfinance unreachable; skipping pairs reference test")
    from backend.services.backtest.pairs import run_pairs_backtest
    r = run_pairs_backtest("HDFCBANK", "ICICIBANK", period="3y", lookback=60)
    c = r["cointegration"]
    assert "is_cointegrated" in c and "adf_tstat" in c        # the cointegration verdict
    m = r["metrics"]
    _assert_rigor_ladder(m["forward_stats"], m["monte_carlo"], m["sub_periods"],
                         m["trust_verdict"], label="pairs")


# ── 3 · momentum portfolio, long-only (live) ─────────────────────────

_BASKET = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
           "ITC", "LT", "MARUTI", "AXISBANK"]


def test_ref_momentum_portfolio_long_only():
    if not _yf_ok():
        pytest.skip("yfinance unreachable; skipping portfolio reference test")
    from backend.services.backtest.portfolio import run_portfolio_backtest
    r = run_portfolio_backtest(_BASKET, period="5y", top_n=5, rebalance="M")
    m = r["metrics"]
    assert m["avg_gross"] <= 1.01                              # gross budget honoured
    _assert_rigor_ladder(m["forward_stats"], m["monte_carlo"], m["sub_periods"],
                         m["trust_verdict"], label="portfolio_long")


# ── 4 · momentum portfolio, dollar-neutral long/short (live) ─────────

def test_ref_momentum_portfolio_long_short():
    if not _yf_ok():
        pytest.skip("yfinance unreachable; skipping L/S portfolio reference test")
    from backend.services.backtest.portfolio import run_portfolio_backtest
    r = run_portfolio_backtest(_BASKET, period="5y", top_n=4, rebalance="M",
                               long_short=True)
    m = r["metrics"]
    assert abs(m["avg_net"]) < 0.1                             # dollar-neutral
    _assert_rigor_ladder(m["forward_stats"], m["monte_carlo"], m["sub_periods"],
                         m["trust_verdict"], label="portfolio_ls")


# ── 5 · fundamental factor screen (live DB) ──────────────────────────

def test_ref_fundamental_factor_screen():
    import asyncio
    import asyncpg
    from backtester.universe import universe_at
    dsn = _financials_dsn_or_skip()

    async def run():
        try:
            conn = await asyncpg.connect(dsn=dsn, timeout=5)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"financials DB unreachable: {e}")
        try:
            # canonical factor strategy: top industry-neutral RoE decile among
            # low-leverage names.
            snap = await universe_at(
                conn,
                "decile(neutralize(return_on_equity)) == 10 AND debt_to_equity_ratio < 0.6",
                date(2024, 6, 3), basis="consolidated",
            )
            return len(snap.rows)
        finally:
            await conn.close()

    n = asyncio.run(run())
    assert n >= 5, f"factor screen returned {n} names (expected a non-trivial universe)"


# ── 6 · cointegration basket / Johansen (live) ───────────────────────

def test_ref_johansen_basket():
    if not _yf_ok():
        pytest.skip("yfinance unreachable; skipping Johansen reference test")
    from backend.services.backtest.pairs import run_johansen
    r = run_johansen(["RELIANCE", "ONGC", "BPCL"], period="3y")
    assert "rank" in r and "is_cointegrated" in r              # the rank verdict
    assert isinstance(r["rank"], int) and 0 <= r["rank"] <= 3
    assert r["eigenvalues"] and len(r["trace_stats"]) == len(r["symbols"])
