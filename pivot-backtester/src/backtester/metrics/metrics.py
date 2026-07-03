"""Headline metrics — pragmatic v1.

We compute on the equity curve produced by the engine. No external deps
beyond numpy.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class MetricsResult:
    total_return_pct: float
    cagr_pct: float
    annualised_vol_pct: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    calmar: float
    n_days: int
    n_unique_companies: int
    benchmark_return_pct: float | None
    benchmark_cagr_pct: float | None
    alpha_vs_benchmark_pct: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def compute_metrics(
    equity_curve: list[dict],
    *,
    benchmark_curve: list[dict] | None = None,
    risk_free_rate: float = 0.065,
    trades: list[dict] | None = None,
) -> MetricsResult:
    if not equity_curve:
        raise ValueError("equity_curve is empty")
    values = np.array([row["value"] for row in equity_curve], dtype=float)
    if len(values) < 2:
        return MetricsResult(0, 0, 0, 0, 0, 0, 0, len(values), 0, None, None, None)

    # Returns
    rets = np.diff(values) / np.where(values[:-1] == 0, 1, values[:-1])
    total_return = (values[-1] / values[0]) - 1

    # Year fraction — CALENDAR span between the first and last bar (365.25
    # days/yr), matching backend.services.backtest_metrics.calendar_cagr_pct so
    # CAGR is comparable across every engine. (Was bar-count/252, which both
    # overstated CAGR on short windows and diverged from the signal engines.)
    from datetime import date as _date, datetime as _dt

    def _as_date(x: object) -> _date:
        if isinstance(x, _dt):
            return x.date()
        if isinstance(x, _date):
            return x
        return _dt.fromisoformat(str(x)[:10]).date()

    n_days = len(values)
    try:
        _span_days = max(
            (_as_date(equity_curve[-1]["date"]) - _as_date(equity_curve[0]["date"])).days,
            1,
        )
    except Exception:
        _span_days = n_days  # fall back to the bar count if dates are missing
    years = max(_span_days / 365.25, 1e-9)
    cagr = (values[-1] / values[0]) ** (1 / years) - 1 if values[0] > 0 else 0.0

    # Vol / Sharpe / Sortino — daily → annualised
    vol = float(np.std(rets, ddof=1)) * math.sqrt(252) if len(rets) > 1 else 0.0
    excess = rets - (risk_free_rate / 252)
    sharpe = (
        float(np.mean(excess) / np.std(excess, ddof=1)) * math.sqrt(252)
        if np.std(excess, ddof=1) > 0 else 0.0
    )
    downside = excess[excess < 0]
    sortino = (
        float(np.mean(excess) / np.std(downside, ddof=1)) * math.sqrt(252)
        if len(downside) > 1 and np.std(downside, ddof=1) > 0 else 0.0
    )

    # Max drawdown
    peak = np.maximum.accumulate(values)
    dd = (values - peak) / np.where(peak == 0, 1, peak)
    max_dd = float(dd.min())

    calmar = (cagr / abs(max_dd)) if max_dd < 0 else 0.0

    # Universe coverage
    n_unique = 0
    if trades is not None:
        n_unique = len({t["sc_id"] for t in trades})

    # Benchmark
    bench_total = bench_cagr = alpha = None
    if benchmark_curve:
        bvals = np.array([row["value"] for row in benchmark_curve], dtype=float)
        if len(bvals) >= 2 and bvals[0] > 0:
            bench_total = (bvals[-1] / bvals[0]) - 1
            bench_cagr = (bvals[-1] / bvals[0]) ** (1 / years) - 1
            alpha = cagr - bench_cagr

    return MetricsResult(
        total_return_pct=round(total_return * 100, 2),
        cagr_pct=round(cagr * 100, 2),
        annualised_vol_pct=round(vol * 100, 2),
        sharpe=round(sharpe, 2),
        sortino=round(sortino, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        calmar=round(calmar, 2),
        n_days=n_days,
        n_unique_companies=n_unique,
        benchmark_return_pct=round(bench_total * 100, 2) if bench_total is not None else None,
        benchmark_cagr_pct=round(bench_cagr * 100, 2) if bench_cagr is not None else None,
        alpha_vs_benchmark_pct=round(alpha * 100, 2) if alpha is not None else None,
    )
