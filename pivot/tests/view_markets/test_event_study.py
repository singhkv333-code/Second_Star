"""Focused unit tests for ``backend.view_markets.event_study``.

Self-contained: the only external dependency (``get_close_dict``) is monkey-
patched with deterministic synthetic OHLC-close series, so no Kite/yfinance
network call happens. We assert the market-model AR/CAR/BHAR maths, the BMP +
non-parametric significance pairing, surprise conditioning, and that the Trust
Battery verdict degrades to ``insufficient_data`` on the (necessarily short)
event-window sample.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backend.view_markets import event_study as es
from backend.view_markets.event_study import (
    AbnormalReturnSeries,
    EventStudyWindows,
    abnormal_returns_for_event,
    bmp_significance,
    run_event_study,
)
from backend.view_markets.feeds import AnalogEvent

_WINDOWS = EventStudyWindows()


def _make_closes(
    *,
    event_pos: int = 200,
    n_days: int = 320,
    beta: float = 1.0,
    event_jump: float = 0.05,
    seed: int = 7,
) -> tuple[dict[str, pd.Series], date]:
    """Build aligned symbol+benchmark close series with a known abnormal jump.

    The symbol tracks the benchmark with ``beta`` plus idiosyncratic noise; on
    the trading day at ``event_pos`` we inject a positive ``event_jump`` excess
    return so the event-window CAR is reliably positive.
    """
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n_days)
    mkt_ret = rng.normal(0.0003, 0.01, size=n_days)
    idio = rng.normal(0.0, 0.006, size=n_days)
    sym_ret = beta * mkt_ret + idio
    sym_ret[event_pos] += event_jump  # the abnormal shock at t=0

    mkt_px = 100.0 * np.cumprod(1.0 + mkt_ret)
    sym_px = 250.0 * np.cumprod(1.0 + sym_ret)
    closes = {
        "INFY": pd.Series(sym_px, index=idx),
        "NIFTY": pd.Series(mkt_px, index=idx),
    }
    event_date = idx[event_pos].date()
    return closes, event_date


def _patch_closes(monkeypatch: pytest.MonkeyPatch, closes: dict[str, pd.Series]) -> None:
    monkeypatch.setattr(
        "backend.core.data.historical.get_close_dict",
        lambda symbols, period="1y": {s: closes[s] for s in symbols if s in closes},
    )


# ---------------------------------------------------------------------------
# abnormal_returns_for_event
# ---------------------------------------------------------------------------

def test_abnormal_returns_positive_event(monkeypatch: pytest.MonkeyPatch) -> None:
    closes, event_date = _make_closes(event_jump=0.06)
    _patch_closes(monkeypatch, closes)

    out = abnormal_returns_for_event(
        None, symbol="INFY", event_date=event_date, benchmark="NIFTY", windows=_WINDOWS
    )
    assert out is not None
    # Event window length = pre + post + 1.
    assert len(out.ar) == _WINDOWS.pre_days + _WINDOWS.post_days + 1
    # The +6% shock dominates the window -> positive CAR and BHAR.
    assert out.car > 0.03
    assert out.bhar > 0.0
    # Beta recovered near the simulated 1.0; sigma_ar populated for BMP.
    assert 0.5 < out.beta < 1.5
    assert out.sigma_ar is not None and out.sigma_ar > 0
    assert out.drift_car is not None  # drift window fits inside the sample


def test_abnormal_returns_missing_benchmark(monkeypatch: pytest.MonkeyPatch) -> None:
    closes, event_date = _make_closes()
    closes.pop("NIFTY")
    _patch_closes(monkeypatch, closes)
    out = abnormal_returns_for_event(
        None, symbol="INFY", event_date=event_date, benchmark="NIFTY"
    )
    assert out is None  # no fabrication when a leg is missing


def test_abnormal_returns_insufficient_history(monkeypatch: pytest.MonkeyPatch) -> None:
    # Far too few bars for the estimation window -> None.
    closes, event_date = _make_closes(event_pos=20, n_days=40)
    _patch_closes(monkeypatch, closes)
    out = abnormal_returns_for_event(
        None, symbol="INFY", event_date=event_date, benchmark="NIFTY"
    )
    assert out is None


# ---------------------------------------------------------------------------
# bmp_significance
# ---------------------------------------------------------------------------

def _series(car: float, sigma: float = 0.01) -> AbnormalReturnSeries:
    return AbnormalReturnSeries(
        symbol="X",
        event_date=date(2025, 1, 1),
        ar=(car / 5,) * 5,
        car=car,
        bhar=car,
        drift_car=None,
        alpha=0.0,
        beta=1.0,
        sigma_ar=sigma,
    )


def test_bmp_single_event_no_significance() -> None:
    res = bmp_significance([_series(0.05)])
    assert res.n == 1
    assert res.bmp_t is None
    assert res.both_agree is False
    assert res.caar == pytest.approx(0.05)


def test_bmp_strong_consistent_positive() -> None:
    # Many tightly-clustered positive CARs -> both tests reject H0.
    series = [_series(0.04 + 0.001 * i, sigma=0.005) for i in range(8)]
    res = bmp_significance(series)
    assert res.n == 8
    assert res.bmp_t is not None and res.bmp_t > 0
    assert res.bmp_p is not None and res.bmp_p < 0.05
    assert res.nonparam_p is not None and res.nonparam_p < 0.05
    assert res.both_agree is True


def test_bmp_mixed_signs_not_significant() -> None:
    series = [_series(0.02), _series(-0.02), _series(0.01), _series(-0.015)]
    res = bmp_significance(series)
    # Sign test on a balanced split should not reject.
    assert res.both_agree is False


# ---------------------------------------------------------------------------
# run_event_study
# ---------------------------------------------------------------------------

def test_run_event_study_aggregates_and_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    closes, event_date = _make_closes(event_jump=0.05)
    _patch_closes(monkeypatch, closes)
    events = [AnalogEvent(tag="rbi_mpc", event_date=event_date, surprise_sign="positive")]

    res = run_event_study(
        None,
        instruments=["INFY"],
        analog_events=events,
        benchmark="NIFTY",
        surprise_sign="positive",
    )
    assert res.n_events == 1
    assert res.instruments == ("INFY",)
    assert res.caar > 0.0
    assert len(res.aar) == _WINDOWS.pre_days + _WINDOWS.post_days + 1
    assert res.car_by_event[0].surprise_sign == "positive"
    # Short event-window sample -> the Trust Battery refuses to over-claim.
    assert res.verdict["verdict"] == "insufficient_data"
    assert res.conditioned_on["surprise_sign"] == "positive"
    assert "n_obs" in res.forward_stats


def test_run_event_study_surprise_filter_drops_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closes, event_date = _make_closes()
    _patch_closes(monkeypatch, closes)
    events = [AnalogEvent(tag="rbi_mpc", event_date=event_date, surprise_sign="negative")]

    res = run_event_study(
        None,
        instruments=["INFY"],
        analog_events=events,
        surprise_sign="positive",  # filters the lone (negative) event out
    )
    assert res.n_events == 0
    assert res.verdict["verdict"] == "insufficient_data"
    assert any("surprise condition" in n for n in res.notes)


def test_run_event_study_empty_sample() -> None:
    res = run_event_study(None, instruments=["INFY"], analog_events=[])
    assert res.n_events == 0
    assert res.caar == 0.0
    assert res.verdict["verdict"] == "insufficient_data"
    assert any("empty analog sample" in n for n in res.notes)


def test_run_event_study_min_magnitude_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    closes, event_date = _make_closes()
    _patch_closes(monkeypatch, closes)
    events = [
        AnalogEvent(
            tag="rbi_mpc",
            event_date=event_date,
            surprise_sign="positive",
            surprise_magnitude=0.1,
        )
    ]
    res = run_event_study(
        None,
        instruments=["INFY"],
        analog_events=events,
        min_surprise_magnitude=0.5,  # 0.1 < 0.5 -> dropped
    )
    assert res.n_events == 0


def test_module_imports_clean() -> None:
    # Guards the import-cheap contract (numpy/pandas live inside bodies).
    assert hasattr(es, "run_event_study")
