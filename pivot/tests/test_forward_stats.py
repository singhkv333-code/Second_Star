"""Unit tests for ``backend.services.forward_stats``.

Pure-math module — no DB, no fixtures needed beyond the project conftest's
env-var bootstrap. Coverage:

  * normal CDF / PPF round-trip
  * observed_sharpe vs hand-computed values
  * skewness / kurtosis on a known sample
  * max_drawdown_pct (monotone, single peak-trough)
  * PSR rises monotonically with both n and SR_hat; PSR in (0, 1)
  * MinTRL falls as SR_hat rises (more skill -> less data needed)
  * DSR <= PSR for N > 1 (deflation); DSR == PSR(0) when N == 1
  * degenerate inputs collapse to None (no exceptions)
"""
from __future__ import annotations

import math

import pytest

from backend.services.forward_stats import (
    _norm_cdf,
    _norm_ppf,
    deflated_sharpe_ratio,
    kurtosis,
    max_drawdown_pct,
    min_track_record_length,
    observed_sharpe,
    psr,
    skewness,
)

# A deterministic "interesting" daily-return series: positive drift, mild
# negative skew, slight excess kurtosis. Used across multiple PSR/DSR tests
# so changing the series in one place reshapes every expected ordering.
_SERIES = [
    0.012, -0.005, 0.008, 0.003, -0.011, 0.015, 0.002, -0.002, 0.006, 0.001,
    -0.008, 0.004, 0.009, -0.003, 0.007, 0.000, -0.006, 0.011, 0.005, -0.004,
    0.013, -0.009, 0.006, 0.002, -0.001, 0.008, -0.005, 0.010, 0.003, -0.007,
]


# ---------------------------------------------------------------------------
# Normal CDF / PPF
# ---------------------------------------------------------------------------

class TestNormCdfPpf:
    def test_cdf_anchor_values(self) -> None:
        # Phi(0) = 0.5, Phi(1.96) ~= 0.975, Phi(-1.96) ~= 0.025
        assert _norm_cdf(0.0) == pytest.approx(0.5, abs=1e-12)
        assert _norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
        assert _norm_cdf(-1.96) == pytest.approx(0.025, abs=1e-3)

    def test_ppf_anchor_values(self) -> None:
        assert _norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)
        assert _norm_ppf(0.95) == pytest.approx(1.6448536269514722, abs=1e-6)
        assert _norm_ppf(0.025) == pytest.approx(-1.959963984540054, abs=1e-6)

    @pytest.mark.parametrize("p", [0.001, 0.05, 0.25, 0.5, 0.75, 0.95, 0.999])
    def test_round_trip(self, p: float) -> None:
        # cdf(ppf(p)) == p across the whole valid range
        z = _norm_ppf(p)
        assert _norm_cdf(z) == pytest.approx(p, abs=1e-6)

    def test_ppf_boundaries(self) -> None:
        assert _norm_ppf(0.0) == -math.inf
        assert _norm_ppf(1.0) == math.inf
        assert _norm_ppf(-0.1) == -math.inf
        assert _norm_ppf(1.5) == math.inf


# ---------------------------------------------------------------------------
# observed_sharpe
# ---------------------------------------------------------------------------

class TestObservedSharpe:
    def test_basic_value(self) -> None:
        # mean / std (ddof=1) for [1, 2, 3, 4, 5]
        rs = [1.0, 2.0, 3.0, 4.0, 5.0]
        # mean = 3, std (ddof=1) = sqrt(10/4) = sqrt(2.5) ≈ 1.5811
        expected = 3.0 / math.sqrt(2.5)
        assert observed_sharpe(rs) == pytest.approx(expected, rel=1e-9)

    def test_no_annualization_no_rounding(self) -> None:
        # A *positive* SR in fractional space should be small (< 1) for daily
        # returns ~1% — i.e. obviously NOT the annualized x sqrt(252) value.
        sr = observed_sharpe(_SERIES)
        assert sr is not None
        assert 0.0 < sr < 1.0  # raw per-period
        # And it should not be rounded to 2dp — verify it has more precision
        # than the display Sharpe would.
        assert abs(sr - round(sr, 2)) > 1e-9

    def test_degenerate(self) -> None:
        assert observed_sharpe([]) is None
        assert observed_sharpe([0.5]) is None  # n < 2
        assert observed_sharpe([0.01, 0.01, 0.01]) is None  # zero std
        # Non-finite entries dropped; only [0.1] remains -> n<2 -> None
        assert observed_sharpe([0.1, float("nan"), None]) is None  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# skewness / kurtosis
# ---------------------------------------------------------------------------

class TestSkewKurt:
    def test_symmetric_sample_near_zero_skew(self) -> None:
        rs = [-2.0, -1.0, 0.0, 1.0, 2.0]
        sk = skewness(rs)
        assert sk is not None
        assert sk == pytest.approx(0.0, abs=1e-9)

    def test_right_skewed_sample(self) -> None:
        # Heavy right tail
        rs = [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 5.0]
        sk = skewness(rs)
        assert sk is not None
        assert sk > 0.5

    def test_kurtosis_normal_like(self) -> None:
        # Uniform-ish symmetric sample — kurtosis should be < 3 (platykurtic)
        rs = [-2.0, -1.0, 0.0, 1.0, 2.0]
        k = kurtosis(rs)
        assert k is not None
        assert 0.0 < k < 3.0

    def test_excess_kurtosis_subtracts_three(self) -> None:
        rs = _SERIES
        raw = kurtosis(rs)
        ex = kurtosis(rs, excess=True)
        assert raw is not None and ex is not None
        assert ex == pytest.approx(raw - 3.0, abs=1e-12)

    def test_degenerate(self) -> None:
        assert skewness([]) is None
        assert kurtosis([]) is None
        assert skewness([0.05]) is None
        assert skewness([1.0, 1.0, 1.0]) is None  # zero std
        assert kurtosis([1.0, 1.0, 1.0]) is None


# ---------------------------------------------------------------------------
# Max drawdown
# ---------------------------------------------------------------------------

class TestMaxDrawdown:
    def test_monotone_curve(self) -> None:
        assert max_drawdown_pct([100.0, 101.0, 102.0, 103.0]) == pytest.approx(0.0)

    def test_single_peak_trough(self) -> None:
        # Peak 120, trough 60 -> -50%
        assert max_drawdown_pct([100.0, 120.0, 60.0, 90.0]) == pytest.approx(-50.0)

    def test_uses_running_peak(self) -> None:
        # The 80 follows the 100 peak (not 120 yet), then 120 peak, then 90.
        # Worst DD is (60 / 120) - 1 = -50%, NOT (80/100) - 1 = -20%.
        assert max_drawdown_pct([100.0, 80.0, 120.0, 60.0]) == pytest.approx(-50.0)

    def test_degenerate(self) -> None:
        assert max_drawdown_pct([]) is None
        assert max_drawdown_pct([100.0]) is None
        assert max_drawdown_pct([None, None]) is None  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# PSR
# ---------------------------------------------------------------------------

class TestPsr:
    def _moments(self, rs: list[float]) -> tuple[float, float, float]:
        sr = observed_sharpe(rs)
        sk = skewness(rs)
        kt = kurtosis(rs)
        assert sr is not None and sk is not None and kt is not None
        return sr, sk, kt

    def test_psr_in_unit_interval(self) -> None:
        sr, sk, kt = self._moments(_SERIES)
        p = psr(sr, len(_SERIES), sk, kt)
        assert p is not None
        assert 0.0 < p < 1.0

    def test_psr_rises_with_n(self) -> None:
        # Same moments, more observations -> tighter confidence -> higher PSR.
        sr, sk, kt = self._moments(_SERIES)
        assert sr > 0  # the test logic depends on a positive SR
        p_small = psr(sr, 30, sk, kt)
        p_mid = psr(sr, 100, sk, kt)
        p_big = psr(sr, 500, sk, kt)
        assert p_small is not None and p_mid is not None and p_big is not None
        assert p_small < p_mid < p_big

    def test_psr_rises_with_sr_hat(self) -> None:
        # Hold moments + n fixed; vary SR_hat -> higher SR -> higher PSR.
        sr, sk, kt = self._moments(_SERIES)
        n = len(_SERIES)
        p_low = psr(sr * 0.5, n, sk, kt)
        p_mid = psr(sr, n, sk, kt)
        p_high = psr(sr * 2.0, n, sk, kt)
        assert p_low is not None and p_mid is not None and p_high is not None
        assert p_low < p_mid < p_high

    def test_psr_threshold_drops_probability(self) -> None:
        sr, sk, kt = self._moments(_SERIES)
        n = len(_SERIES)
        p0 = psr(sr, n, sk, kt, sr_threshold=0.0)
        p_high = psr(sr, n, sk, kt, sr_threshold=sr * 0.95)
        assert p0 is not None and p_high is not None
        # Harder bar (higher threshold) -> lower probability of beating it.
        assert p_high < p0

    def test_psr_degenerate_inputs_return_none(self) -> None:
        sr, sk, kt = self._moments(_SERIES)
        assert psr(None, 30, sk, kt) is None
        assert psr(sr, 1, sk, kt) is None
        assert psr(sr, 30, None, kt) is None
        assert psr(sr, 30, sk, None) is None


# ---------------------------------------------------------------------------
# Minimum Track Record Length
# ---------------------------------------------------------------------------

class TestMinTRL:
    def test_min_trl_drops_as_sr_rises(self) -> None:
        # Same skew/kurt, vary SR_hat — higher skill -> less data needed.
        rs = _SERIES
        sk = skewness(rs)
        kt = kurtosis(rs)
        assert sk is not None and kt is not None
        sr_low = 0.05
        sr_mid = 0.10
        sr_high = 0.20
        m_low = min_track_record_length(sr_low, sk, kt)
        m_mid = min_track_record_length(sr_mid, sk, kt)
        m_high = min_track_record_length(sr_high, sk, kt)
        assert m_low is not None and m_mid is not None and m_high is not None
        assert m_low > m_mid > m_high
        assert m_high > 1.0  # always at least 1

    def test_min_trl_undefined_when_skill_below_threshold(self) -> None:
        sk = skewness(_SERIES)
        kt = kurtosis(_SERIES)
        assert sk is not None and kt is not None
        # SR_hat <= sr_threshold means the gate cannot be passed -> None
        assert min_track_record_length(0.0, sk, kt) is None
        assert min_track_record_length(-0.1, sk, kt) is None

    def test_min_trl_higher_confidence_needs_more_obs(self) -> None:
        sk = skewness(_SERIES)
        kt = kurtosis(_SERIES)
        assert sk is not None and kt is not None
        m_90 = min_track_record_length(0.10, sk, kt, confidence=0.90)
        m_99 = min_track_record_length(0.10, sk, kt, confidence=0.99)
        assert m_90 is not None and m_99 is not None
        assert m_99 > m_90

    def test_min_trl_degenerate(self) -> None:
        assert min_track_record_length(None, 0.0, 3.0) is None
        assert min_track_record_length(0.1, None, 3.0) is None
        assert min_track_record_length(0.1, 0.0, None) is None
        assert min_track_record_length(0.1, 0.0, 3.0, confidence=1.5) is None


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio
# ---------------------------------------------------------------------------

class TestDSR:
    def _moments(self) -> tuple[float, float, float, int]:
        rs = _SERIES
        sr = observed_sharpe(rs)
        sk = skewness(rs)
        kt = kurtosis(rs)
        assert sr is not None and sk is not None and kt is not None
        return sr, sk, kt, len(rs)

    def test_dsr_le_psr_for_many_trials(self) -> None:
        sr, sk, kt, n = self._moments()
        p = psr(sr, n, sk, kt)
        d = deflated_sharpe_ratio(sr, n, sk, kt, num_trials=50)
        assert p is not None and d is not None
        # Deflation -> DSR is strictly less than the un-deflated PSR.
        assert d <= p
        assert d < p  # for N > 1 the deflation must bite

    def test_dsr_decreases_with_more_trials(self) -> None:
        # More trials searched -> higher SR0 bar -> lower DSR.
        sr, sk, kt, n = self._moments()
        d_few = deflated_sharpe_ratio(sr, n, sk, kt, num_trials=2)
        d_many = deflated_sharpe_ratio(sr, n, sk, kt, num_trials=100)
        d_lots = deflated_sharpe_ratio(sr, n, sk, kt, num_trials=1000)
        assert d_few is not None and d_many is not None and d_lots is not None
        assert d_few > d_many > d_lots

    def test_dsr_n1_equals_psr_zero(self) -> None:
        # N <= 1 -> no deflation -> DSR collapses to PSR(sr_threshold=0).
        sr, sk, kt, n = self._moments()
        baseline = psr(sr, n, sk, kt, sr_threshold=0.0)
        d_n1 = deflated_sharpe_ratio(sr, n, sk, kt, num_trials=1)
        d_n0 = deflated_sharpe_ratio(sr, n, sk, kt, num_trials=0)
        assert baseline is not None and d_n1 is not None and d_n0 is not None
        assert d_n1 == pytest.approx(baseline, abs=1e-12)
        assert d_n0 == pytest.approx(baseline, abs=1e-12)

    def test_dsr_respects_explicit_sr_variance(self) -> None:
        sr, sk, kt, n = self._moments()
        # Larger sr_variance -> larger E[max SR] -> stricter bar -> smaller DSR.
        d_small = deflated_sharpe_ratio(sr, n, sk, kt, num_trials=10, sr_variance=0.001)
        d_big = deflated_sharpe_ratio(sr, n, sk, kt, num_trials=10, sr_variance=0.1)
        assert d_small is not None and d_big is not None
        assert d_small > d_big

    def test_dsr_degenerate(self) -> None:
        sr, sk, kt, n = self._moments()
        assert deflated_sharpe_ratio(None, n, sk, kt, 10) is None
        assert deflated_sharpe_ratio(sr, 1, sk, kt, 10) is None
        assert deflated_sharpe_ratio(sr, n, None, kt, 10) is None
        assert deflated_sharpe_ratio(sr, n, sk, None, 10) is None
