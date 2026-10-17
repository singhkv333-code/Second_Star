"""Chat tool for the multi-position portfolio backtester (Phase 2.4).

``backtest_portfolio`` — cross-sectional momentum portfolio over a universe, with
max-names / gross / sector-cap constraints, long-only or dollar-neutral L/S, judged
through the rigor battery. Returns a COMPACT, verdict-led summary (the heavy equity
series stays on the REST endpoint for any FE card).
"""
from __future__ import annotations

import asyncio

from backend.services.backtest.portfolio import run_portfolio_backtest
from backend.services.backtest.portfolio.engine import PortfolioError


def _fmt(x) -> str:
    return f"{x:+.1f}%" if isinstance(x, (int, float)) else "n/a"


async def backtest_portfolio(args: dict) -> dict:
    """Backtest a cross-sectional momentum portfolio. See module docstring."""
    args = args or {}
    symbols = args.get("symbols") or []
    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.replace(",", " ").split() if s.strip()]
    symbols = [s for s in symbols if s]
    if len(symbols) < 3:
        raise ValueError(
            "backtest_portfolio needs a 'symbols' list of at least 3 tickers "
            "(the universe to rank), e.g. ['RELIANCE','TCS','INFY','SBIN','ITC','LT']."
        )
    top_n = int(args.get("top_n") or 5)
    long_short = bool(args.get("long_short") or False)
    sector_cap = args.get("sector_cap")
    sector_cap = float(sector_cap) if sector_cap not in (None, "") else None
    rebalance = (args.get("rebalance") or "M").upper()
    period = (args.get("period") or "5y").strip()

    try:
        res = await asyncio.to_thread(
            run_portfolio_backtest, symbols,
            period=period, top_n=top_n, rebalance=rebalance,
            long_short=long_short, sector_cap=sector_cap,
        )
    except PortfolioError as e:
        raise ValueError(str(e))

    m = res["metrics"]
    fs = m.get("forward_stats") or {}
    tv = m.get("trust_verdict") or {}
    kind = "dollar-neutral long/short" if long_short else "long-only"
    cap_txt = f", ≤{int(sector_cap * 100)}%/sector" if sector_cap else ""
    summary = (
        f"Momentum portfolio over {len(res['symbols'])} names — top {top_n}, "
        f"{rebalance} rebalance, {kind}{cap_txt} ({period}): "
        f"{_fmt(m.get('total_return_pct'))}, max drawdown "
        f"{_fmt(m.get('max_drawdown_pct'))}, gross {m.get('avg_gross')} / net "
        f"{m.get('avg_net')}. Trust verdict: {tv.get('verdict', 'n/a')} "
        f"(PSR {fs.get('psr')}, DSR {fs.get('deflated_sharpe')}). "
        f"{m.get('n_rebalances')} rebalances."
    )
    return {
        "_render_hint": "portfolio_backtest",
        "summary": summary,
        "symbols": res["symbols"],
        "params": res["params"],
        "metrics": {
            "total_return_pct": m.get("total_return_pct"),
            "max_drawdown_pct": m.get("max_drawdown_pct"),
            "avg_gross": m.get("avg_gross"),
            "avg_net": m.get("avg_net"),
            "n_rebalances": m.get("n_rebalances"),
            "turnover_total": m.get("turnover_total"),
            "psr": fs.get("psr"),
            "deflated_sharpe": fs.get("deflated_sharpe"),
            "trust_verdict": tv,
        },
    }
