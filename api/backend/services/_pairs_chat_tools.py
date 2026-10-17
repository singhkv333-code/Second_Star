"""Chat tools for pairs / stat-arb (Phase 2.3).

  ``backtest_pairs`` — cointegration test + mean-reversion spread backtest for a
                       named pair, judged through the rigor battery.
  ``scan_pairs``      — pairwise cointegration scan over a list of symbols.

Both wrap ``backend.services.backtest.pairs`` (yfinance OHLCV) and return a
COMPACT, verdict-led summary for the chat surface — the heavy spread/equity
series stay on the REST endpoints (``/api/backtest/pairs/run`` + ``/scan``) for
any FE card; the chat result leads with the cointegration + Trust verdict so the
model relays an honest call, not a vanity return number.
"""
from __future__ import annotations

import asyncio

from backend.services.backtest.pairs import (
    run_johansen,
    run_pairs_backtest,
    scan_pairs as _scan_pairs,
)
from backend.services.backtest.pairs.engine import PairsError


def _fmt_pct(x) -> str:
    return f"{x:+.1f}%" if isinstance(x, (int, float)) else "n/a"


async def backtest_pairs(args: dict) -> dict:
    """Cointegration + spread backtest for a pair. See module docstring."""
    args = args or {}
    a = (args.get("symbol_a") or "").strip()
    b = (args.get("symbol_b") or "").strip()
    if not a or not b:
        raise ValueError(
            "backtest_pairs needs 'symbol_a' and 'symbol_b' (the two stocks of "
            "the pair, e.g. symbol_a='HDFCBANK', symbol_b='ICICIBANK')."
        )
    period = (args.get("period") or "2y").strip()
    lookback = int(args.get("lookback") or 60)
    entry_z = float(args.get("entry_z") or 2.0)
    exit_z = float(args.get("exit_z") or 0.5)
    stop_z = float(args.get("stop_z") or 4.0)

    try:
        r = await asyncio.to_thread(
            run_pairs_backtest, a, b,
            period=period, lookback=lookback,
            entry_z=entry_z, exit_z=exit_z, stop_z=stop_z,
        )
    except PairsError as e:
        raise ValueError(str(e))

    c = r["cointegration"]
    m = r["metrics"]
    fs = m.get("forward_stats") or {}
    tv = m.get("trust_verdict") or {}
    pa, pb = r["pair"]["a"], r["pair"]["b"]

    coint_txt = (
        f"cointegrated at {c['cointegrated_at']}"
        if c["cointegrated_at"]
        else "NOT cointegrated"
    )
    hl = c.get("half_life_days")
    summary = (
        f"Pairs backtest {pa}/{pb} ({period}): {coint_txt} "
        f"(Engle-Granger ADF {c.get('adf_tstat')}, hedge β {c.get('beta')}"
        + (f", half-life {hl}d" if hl else "")
        + f"). Trust verdict: {tv.get('verdict', 'n/a')}. "
        f"Spread strategy {_fmt_pct(m.get('total_return_pct'))} over "
        f"{m.get('n_trades')} trades ({m.get('win_rate_pct')}% win), "
        f"max drawdown {_fmt_pct(m.get('max_drawdown_pct'))}; "
        f"PSR {fs.get('psr')}, DSR {fs.get('deflated_sharpe')}."
    )
    if not c["is_cointegrated"]:
        summary += (
            " The legs are not cointegrated over this window, so the spread has "
            "no statistical basis to mean-revert — treat any positive return as "
            "luck, not edge."
        )

    return {
        "_render_hint": "pairs_backtest",
        "summary": summary,
        "pair": r["pair"],
        "period": period,
        "params": r["params"],
        "cointegration": c,
        "metrics": {
            "total_return_pct": m.get("total_return_pct"),
            "n_trades": m.get("n_trades"),
            "win_rate_pct": m.get("win_rate_pct"),
            "max_drawdown_pct": m.get("max_drawdown_pct"),
            "psr": fs.get("psr"),
            "deflated_sharpe": fs.get("deflated_sharpe"),
            "min_trl": fs.get("min_trl"),
            "trust_verdict": tv,
        },
    }


async def test_cointegration(args: dict) -> dict:
    """Johansen cointegration-rank test on a BASKET (3+ stocks). See module docstring."""
    args = args or {}
    symbols = args.get("symbols") or []
    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.replace(",", " ").split() if s.strip()]
    symbols = [s for s in symbols if s]
    if len(symbols) < 2:
        raise ValueError(
            "test_cointegration needs a 'symbols' list of 2+ tickers (best for "
            "baskets of 3+, e.g. ['RELIANCE','ONGC','IOC','BPCL'])."
        )
    period = (args.get("period") or "2y").strip()
    try:
        res = await asyncio.to_thread(run_johansen, symbols, period=period)
    except PairsError as e:
        raise ValueError(str(e))

    syms = res["symbols"]
    rank = res["rank"]
    if rank >= 1:
        w = res.get("cointegrating_weights") or {}
        wtxt = " ".join(f"{k} {v:+.2f}" for k, v in w.items())
        summary = (
            f"Johansen test on {syms} ({period}): COINTEGRATED — rank {rank} of "
            f"{len(syms)} at 95% (trace {res['trace_stats'][0]} vs crit "
            f"{res['crit_95'][0]}). A stationary combination exists"
            + (f": {wtxt} (the tradable basket spread)." if wtxt else ".")
        )
    else:
        summary = (
            f"Johansen test on {syms} ({period}): NOT cointegrated (rank 0) — no "
            "stationary combination, so there's no mean-reverting basket spread to "
            "trade here."
        )
    return {"_render_hint": "cointegration_test", "summary": summary, **res}


async def scan_pairs(args: dict) -> dict:
    """Scan a list of symbols for cointegrated pairs, ranked by ADF strength."""
    args = args or {}
    symbols = args.get("symbols") or []
    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.replace(",", " ").split() if s.strip()]
    symbols = [s for s in symbols if s]
    if len(symbols) < 2:
        raise ValueError(
            "scan_pairs needs a 'symbols' list of at least 2 tickers "
            "(e.g. ['SBIN','PNB','BANKBARODA','CANBK'])."
        )
    period = (args.get("period") or "2y").strip()
    min_level = (args.get("min_level") or "5%").strip()

    res = await asyncio.to_thread(
        _scan_pairs, symbols, period=period, min_level=min_level, top=15
    )
    rows = res.get("cointegrated", [])
    if rows:
        top = rows[0]
        lead = (
            f"Scanned {res['tested']} pairs across {res['n_symbols']} symbols — "
            f"{len(rows)} cointegrated at {min_level}. Strongest: "
            f"{top['dependent']}/{top['independent']} (ADF {top['adf_tstat']}, "
            f"{top['cointegrated_at']}"
            + (f", half-life {top['half_life_days']}d" if top.get('half_life_days') else "")
            + "). Cointegration here is in-sample — confirm with a causal "
            "backtest (backtest_pairs) before trusting any pair."
        )
    else:
        lead = (
            f"Scanned {res['tested']} pairs across {res['n_symbols']} symbols — "
            f"none cointegrated at {min_level}. No tradable mean-reverting pair "
            "in this set over this window."
        )
    return {
        "_render_hint": "pairs_scan",
        "summary": lead,
        "tested": res.get("tested"),
        "n_symbols": res.get("n_symbols"),
        "min_level": min_level,
        "cointegrated": rows,
    }
