"""View Markets — event-study engine (CAR / CAAR / BHAR + significance + Trust).

Classical event-study methodology (testing doc §1.1) applied to a sample of
ANALOG events on the CHOSEN instruments, conditioned on surprise sign /
magnitude, then run through the EXISTING Trust Battery so an event view's
expression is judged on the same statistical axis as any backtest.

Pipeline per event, per instrument:
  1. Estimation window (~120 trading days, ending ``gap_days`` before t=0) ->
     market-model OLS ``R_it = α_i + β_i·R_mt + ε_it`` vs the benchmark
     (NIFTY default — we already have Kite OHLCV + NIFTY).
  2. Event window ``[-pre, +post]`` (and optional drift ``[+1, +drift]``) ->
     AR = actual − (α̂ + β̂·R_mt), CAR = Σ AR, BHAR = Π(1+actual) − Π(1+expected).
  3. Aggregate across the (surprise-conditioned) sample -> AAR / CAAR / mean
     BHAR.
  4. Significance: BMP standardized cross-sectional t (default; neutralises
     event-induced volatility) + a non-parametric sign/rank test. Call an
     effect reliable ONLY when both agree.
  5. Trust Battery on the CAAR path / per-event CAR distribution ->
     ``forward_stats_block`` + Monte-Carlo + sub-periods -> ``trust_verdict``.

Report BHAR as the user-facing "what would I have made"; CAR for the
significance test (BHARs are right-skewed / overlapping). Joint-hypothesis
caveat: results are MODEL-CONDITIONAL, never proof.

Reuses (real interfaces, pinned 2026-06-29):
  * ``backend.core.data.historical.get_close_dict(symbols, period="1y") ->
    dict[str, pd.Series]`` (Kite-primary daily closes; failed symbols dropped).
  * ``backend.core.calculations.risk_metrics.correlation_matrix(price_dict)``
    and per-series vol — used to derive market-model β (β = ρ·σ_i/σ_m) when an
    explicit OLS isn't run.
  * ``backend.services.forward_stats.forward_stats_block(equity, *,
    num_trials=1, sr_threshold=0.0, confidence=0.95) -> dict`` (PSR / MinTRL /
    DSR battery).
  * ``backend.services.backtest.validation.monte_carlo.monte_carlo_robustness(
    period_returns, *, n_sims=1000, ...) -> dict | None``.
  * ``backend.services.backtest.validation.sub_periods.sub_period_robustness(
    equity_values, *, n_periods=4) -> dict | None``.
  * ``backend.services.backtest.validation.verdict.trust_verdict(*,
    forward_stats, monte_carlo, sub_periods, total_return_pct, n_trades) ->
    dict`` (insufficient_data / no_edge / unproven / promising + flags).
  * ``backend.services.backtest.validation.trials.{strategy_fingerprint,
    record_and_deflate}`` — optional DSR selection-bias deflation across a
    research session.
  * ``backend.view_markets.feeds.AnalogEvent`` — the sample input shape.

Pure-stats heavy lifting may use numpy/pandas, imported INSIDE the function
bodies so this module stays import-cheap.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Optional, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.view_markets.feeds import AnalogEvent

# Below this many conditioned events the cross-sectional significance test is
# noise; we still compute it but the Trust verdict (n_trades < _MIN_TRADES /
# n_obs < _MIN_OBS) will independently collapse to ``insufficient_data``.
_MIN_EVENTS = 1


@dataclass(frozen=True)
class EventStudyWindows:
    """Estimation / event / drift window spec (trading days). Defaults follow
    the short-horizon standard in testing doc §1.1."""

    estimation_days: int = 120
    gap_days: int = 10        # leave 10-20d between estimation and event windows
    pre_days: int = 5         # event window lower bound  (t = -pre_days)
    post_days: int = 5        # event window upper bound  (t = +post_days)
    drift_days: int = 60      # post-event drift window [+1, +drift_days]; 0 = off


@dataclass(frozen=True)
class AbnormalReturnSeries:
    """Per-(instrument, event) abnormal-return result."""

    symbol: str
    event_date: date
    ar: tuple[float, ...]                 # AR per event-window day
    car: float                            # Σ AR over [-pre, +post]
    bhar: float                           # buy-and-hold abnormal return
    drift_car: Optional[float]            # Σ AR over [+1, +drift] (None if off)
    alpha: float
    beta: float
    sigma_ar: Optional[float]             # estimation-window AR std (for BMP)
    surprise_sign: Optional[str] = None
    surprise_magnitude: Optional[float] = None


@dataclass(frozen=True)
class SignificanceResult:
    """Parametric (BMP) + non-parametric agreement on the CAAR."""

    n: int
    caar: float
    bmp_t: Optional[float]                # standardized cross-sectional t
    bmp_p: Optional[float]
    nonparam_name: str                    # "sign" | "rank"
    nonparam_stat: Optional[float]
    nonparam_p: Optional[float]
    both_agree: bool                      # reliable only when both reject H0


@dataclass(frozen=True)
class EventStudyResult:
    """Full event-study output, including the Trust verdict."""

    instruments: tuple[str, ...]
    benchmark: str
    n_events: int                          # surprise-conditioned sample size
    windows: EventStudyWindows
    caar: float                            # cumulative average AR
    aar: tuple[float, ...]                 # average AR per event-window day
    mean_bhar: float                       # user-facing "what would I have made"
    car_by_event: tuple[AbnormalReturnSeries, ...]
    significance: SignificanceResult
    forward_stats: dict                    # forward_stats_block(...)
    monte_carlo: Optional[dict]
    sub_periods: Optional[dict]
    verdict: dict                          # trust_verdict(...)
    conditioned_on: dict                   # {surprise_sign, min_surprise_magnitude}
    notes: tuple[str, ...] = field(default_factory=tuple)


def _two_sided_normal_p(stat: Optional[float]) -> Optional[float]:
    """Two-sided p-value of a test statistic under the standard-normal null.

    Small-sample event studies conventionally lean on the asymptotic normal
    approximation for the BMP / sign tests; we reuse the C-accurate ``math.erf``
    rather than pull in scipy. ``None`` in -> ``None`` out (degrade honestly)."""
    if stat is None or not math.isfinite(stat):
        return None
    phi = 0.5 * (1.0 + math.erf(abs(stat) / math.sqrt(2.0)))
    return max(0.0, min(1.0, 2.0 * (1.0 - phi)))


def abnormal_returns_for_event(
    db: "Session",
    *,
    symbol: str,
    event_date: date,
    benchmark: str = "NIFTY",
    windows: EventStudyWindows = EventStudyWindows(),
) -> Optional[AbnormalReturnSeries]:
    """Market-model abnormal returns for ONE (symbol, event).

    Fetches symbol + benchmark closes spanning the estimation+event window via
    ``get_close_dict``, fits α/β on the estimation window, and computes AR / CAR
    / BHAR (+ drift CAR) over the event window. Returns ``None`` when history is
    too short or data is missing for either leg (no fabrication).
    """
    from datetime import date as _date

    import numpy as np
    import pandas as pd

    from backend.core.data.historical import get_close_dict

    # How far back (calendar days) we must reach: from today past the event, plus
    # the full estimation+gap+pre window before t=0 (≈1.6× to convert trading
    # days -> calendar days) and a margin. ``get_close_dict`` accepts an "Nd"
    # arbitrary span.
    today = _date.today()
    pre_span_trading = windows.estimation_days + windows.gap_days + windows.pre_days
    lookback_days = (today - event_date).days + int(pre_span_trading * 1.6) + 20
    if lookback_days <= 0:
        return None
    closes = get_close_dict([symbol, benchmark], period=f"{lookback_days}d")
    if symbol not in closes or benchmark not in closes:
        return None

    df = pd.concat(
        {"sym": closes[symbol], "mkt": closes[benchmark]}, axis=1
    ).dropna()
    if df.shape[0] < 3:
        return None

    rets = df.pct_change().dropna()
    if rets.empty:
        return None
    ret_dates = rets.index
    ret_sym = rets["sym"].to_numpy(dtype=float)
    ret_mkt = rets["mkt"].to_numpy(dtype=float)
    n = len(ret_sym)

    # t=0 = last trading day on or before the event date (in returns space).
    mask = np.asarray(ret_dates <= pd.Timestamp(event_date))
    hit = np.flatnonzero(mask)
    if hit.size == 0:
        return None
    pos = int(hit[-1])

    pre, post = windows.pre_days, windows.post_days
    gap, est_len = windows.gap_days, windows.estimation_days

    est_end = pos - pre - gap
    est_start = est_end - est_len + 1
    ev_start = pos - pre
    ev_end = pos + post

    # Require the full estimation + event windows to exist (no partial fabrication).
    if est_start < 0 or est_end < est_start or ev_start < 0 or ev_end > n - 1:
        return None

    x = ret_mkt[est_start : est_end + 1]
    y = ret_sym[est_start : est_end + 1]
    if len(x) < 2 or not np.isfinite(x).all() or not np.isfinite(y).all():
        return None
    if float(np.std(x)) == 0.0:
        return None

    # Market model OLS: R_i = alpha + beta * R_m  (numpy polyfit, deg 1).
    beta, alpha = (float(v) for v in np.polyfit(x, y, 1))

    # Estimation-window residual std -> sigma(AR) for the BMP standardization.
    est_resid = y - (alpha + beta * x)
    sigma_ar = float(np.std(est_resid, ddof=2)) if len(est_resid) > 2 else None
    if sigma_ar is not None and not math.isfinite(sigma_ar):
        sigma_ar = None

    ev_sym = ret_sym[ev_start : ev_end + 1]
    ev_mkt = ret_mkt[ev_start : ev_end + 1]
    expected = alpha + beta * ev_mkt
    ar = ev_sym - expected
    car = float(np.sum(ar))
    bhar = float(np.prod(1.0 + ev_sym) - np.prod(1.0 + expected))

    drift_car: Optional[float] = None
    if windows.drift_days > 0:
        d_start = pos + 1
        d_end = min(pos + windows.drift_days, n - 1)
        if d_end >= d_start:
            d_sym = ret_sym[d_start : d_end + 1]
            d_mkt = ret_mkt[d_start : d_end + 1]
            drift_car = float(np.sum(d_sym - (alpha + beta * d_mkt)))

    return AbnormalReturnSeries(
        symbol=symbol,
        event_date=event_date,
        ar=tuple(float(v) for v in ar),
        car=car,
        bhar=bhar,
        drift_car=drift_car,
        alpha=alpha,
        beta=beta,
        sigma_ar=sigma_ar,
    )


def bmp_significance(
    car_series: Sequence[AbnormalReturnSeries],
) -> SignificanceResult:
    """BMP standardized cross-sectional t + a non-parametric sign/rank test.

    Standardizes each event's CAR by its estimation-window AR std (BMP), takes
    the cross-sectional t over the sample, and pairs it with a generalized-sign
    or Corrado-rank test. ``both_agree`` is True only when both reject H0 at the
    conventional level — the "historically reliable" bar. Small N is itself a
    guardrail signal (surfaced via ``n``).
    """
    import numpy as np

    series = list(car_series)
    n = len(series)
    cars = [s.car for s in series if s.car is not None and math.isfinite(s.car)]
    caar = float(np.mean(cars)) if cars else 0.0

    if n < 2:
        # One (or zero) event: report CAAR but never claim significance.
        return SignificanceResult(
            n=n,
            caar=caar,
            bmp_t=None,
            bmp_p=None,
            nonparam_name="sign",
            nonparam_stat=None,
            nonparam_p=None,
            both_agree=False,
        )

    # --- BMP standardized cross-sectional test ---
    # SCAR_i = CAR_i / (sigma_AR_i * sqrt(L_i)); t = mean(SCAR)*sqrt(n)/std(SCAR).
    scar: list[float] = []
    for s in series:
        if s.sigma_ar and s.sigma_ar > 0 and s.ar:
            denom = s.sigma_ar * math.sqrt(len(s.ar))
            if denom > 0:
                scar.append(s.car / denom)
    bmp_t: Optional[float] = None
    bmp_p: Optional[float] = None
    if len(scar) >= 2:
        arr = np.asarray(scar, dtype=float)
        sd = float(np.std(arr, ddof=1))
        if sd > 0:
            bmp_t = float(np.mean(arr) * math.sqrt(len(arr)) / sd)
            bmp_p = _two_sided_normal_p(bmp_t)

    # --- Non-parametric generalized-sign test on CAR signs ---
    pos = sum(1 for c in cars if c > 0)
    m = len(cars)
    nonparam_stat: Optional[float] = None
    nonparam_p: Optional[float] = None
    if m >= 1:
        # H0: P(CAR>0)=0.5 -> z = (pos - m/2)/sqrt(m/4).
        nonparam_stat = (pos - m / 2.0) / math.sqrt(m / 4.0)
        nonparam_p = _two_sided_normal_p(nonparam_stat)

    both_agree = bool(
        bmp_p is not None
        and bmp_p < 0.05
        and nonparam_p is not None
        and nonparam_p < 0.05
    )

    return SignificanceResult(
        n=n,
        caar=caar,
        bmp_t=bmp_t,
        bmp_p=bmp_p,
        nonparam_name="sign",
        nonparam_stat=nonparam_stat,
        nonparam_p=nonparam_p,
        both_agree=both_agree,
    )


def run_event_study(
    db: "Session",
    *,
    instruments: Sequence[str],
    analog_events: Sequence["AnalogEvent"],
    benchmark: str = "NIFTY",
    windows: EventStudyWindows = EventStudyWindows(),
    surprise_sign: Optional[str] = None,
    min_surprise_magnitude: Optional[float] = None,
    num_trials: int = 1,
    trial_group: Optional[str] = None,
) -> EventStudyResult:
    """Run the full event study over a surprise-conditioned analog sample.

    Filters ``analog_events`` by ``surprise_sign`` / ``min_surprise_magnitude``,
    computes per-(instrument, event) ARs via :func:`abnormal_returns_for_event`,
    aggregates AAR / CAAR / mean BHAR, runs :func:`bmp_significance`, then feeds
    the CAAR path to ``forward_stats_block`` (with ``num_trials`` /
    ``trial_group`` for DSR deflation), ``monte_carlo_robustness`` (per-event
    CARs as the resample population), and ``sub_period_robustness`` -> a single
    ``trust_verdict``.

    When the conditioned sample is below MinTRL / ``_MIN_OBS`` the verdict comes
    back ``insufficient_data`` — the confidence scorer then SUPPRESSES the dial
    (never a confident number on a thin sample).
    """
    import numpy as np

    from backend.services.backtest.validation.monte_carlo import (
        monte_carlo_robustness,
    )
    from backend.services.backtest.validation.sub_periods import (
        sub_period_robustness,
    )
    from backend.services.backtest.validation.trials import record_and_deflate
    from backend.services.backtest.validation.verdict import trust_verdict
    from backend.services.forward_stats import forward_stats_block

    instruments_t = tuple(dict.fromkeys(instruments))  # de-dupe, keep order
    conditioned_on = {
        "surprise_sign": surprise_sign,
        "min_surprise_magnitude": min_surprise_magnitude,
    }
    notes: list[str] = []

    # --- Condition the analog sample on surprise sign / magnitude ---
    sampled = list(analog_events)
    conditioned = []
    for ev in sampled:
        if surprise_sign is not None and ev.surprise_sign != surprise_sign:
            continue
        if min_surprise_magnitude is not None:
            mag = ev.surprise_magnitude
            if mag is None or abs(mag) < min_surprise_magnitude:
                continue
        conditioned.append(ev)

    if not sampled:
        notes.append("empty analog sample -> insufficient_data")
    elif not conditioned:
        notes.append(
            "no analog events matched the surprise condition -> insufficient_data"
        )

    # --- Per-(instrument, event) abnormal returns ---
    car_by_event: list[AbnormalReturnSeries] = []
    n_attempted = 0
    n_dropped = 0
    for ev in conditioned:
        for sym in instruments_t:
            n_attempted += 1
            series = abnormal_returns_for_event(
                db,
                symbol=sym,
                event_date=ev.event_date,
                benchmark=benchmark,
                windows=windows,
            )
            if series is None:
                n_dropped += 1
                continue
            # Carry the conditioning variables onto the per-event result.
            series = AbnormalReturnSeries(
                symbol=series.symbol,
                event_date=series.event_date,
                ar=series.ar,
                car=series.car,
                bhar=series.bhar,
                drift_car=series.drift_car,
                alpha=series.alpha,
                beta=series.beta,
                sigma_ar=series.sigma_ar,
                surprise_sign=ev.surprise_sign,
                surprise_magnitude=ev.surprise_magnitude,
            )
            car_by_event.append(series)
    if n_dropped:
        notes.append(
            f"{n_dropped}/{n_attempted} (instrument, event) legs dropped for "
            f"insufficient/missing history"
        )

    n_events = len(car_by_event)

    # --- Aggregate AAR / CAAR / mean BHAR (aligned over the event window) ---
    if car_by_event:
        # Align AR series by event-window day length; truncate to the common
        # minimum so the average is over a rectangular grid.
        min_len = min(len(s.ar) for s in car_by_event)
        ar_matrix = np.asarray([s.ar[:min_len] for s in car_by_event], dtype=float)
        aar_arr = ar_matrix.mean(axis=0)
        aar = tuple(float(v) for v in aar_arr)
        caar = float(np.sum(aar_arr))
        mean_bhar = float(np.mean([s.bhar for s in car_by_event]))
    else:
        aar = tuple()
        caar = 0.0
        mean_bhar = 0.0

    significance = bmp_significance(car_by_event)

    # --- Trust Battery ---
    # CAAR path as an equity curve (1.0 start, compounded by the average AR/day).
    equity = [1.0]
    for v in aar:
        equity.append(equity[-1] * (1.0 + v))
    forward_stats = forward_stats_block(equity, num_trials=num_trials)
    if trial_group:
        from backend.services.backtest.validation.trials import (
            strategy_fingerprint,
        )

        fingerprint = strategy_fingerprint(
            "event_study",
            tuple(instruments_t),
            benchmark,
            surprise_sign,
            min_surprise_magnitude,
            tuple(s.event_date.isoformat() for s in car_by_event),
        )
        forward_stats = record_and_deflate(
            forward_stats, trial_group, fingerprint
        )

    # Per-event CARs are the Monte-Carlo resample population.
    per_event_cars = [s.car for s in car_by_event]
    monte_carlo = monte_carlo_robustness(per_event_cars) if per_event_cars else None
    sub_periods = sub_period_robustness(equity) if len(equity) > 3 else None

    verdict = trust_verdict(
        forward_stats=forward_stats,
        monte_carlo=monte_carlo,
        sub_periods=sub_periods,
        total_return_pct=caar * 100.0,
        n_trades=n_events,
    )

    return EventStudyResult(
        instruments=instruments_t,
        benchmark=benchmark,
        n_events=n_events,
        windows=windows,
        caar=caar,
        aar=aar,
        mean_bhar=mean_bhar,
        car_by_event=tuple(car_by_event),
        significance=significance,
        forward_stats=forward_stats,
        monte_carlo=monte_carlo,
        sub_periods=sub_periods,
        verdict=verdict,
        conditioned_on=conditioned_on,
        notes=tuple(notes),
    )


__all__ = [
    "EventStudyWindows",
    "AbnormalReturnSeries",
    "SignificanceResult",
    "EventStudyResult",
    "abnormal_returns_for_event",
    "bmp_significance",
    "run_event_study",
]
