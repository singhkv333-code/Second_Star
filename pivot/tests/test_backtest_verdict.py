"""Trust verdict — the rigor battery synthesised into one actionable call."""
from __future__ import annotations

from backend.services.backtest.validation import trust_verdict


def _fs(psr=None, dsr=None, min_trl=None, n_obs=250, num_trials=1) -> dict:
    return {
        "psr": psr, "deflated_sharpe": dsr, "min_trl": min_trl,
        "n_obs": n_obs, "num_trials": num_trials,
        "observed_sharpe": None, "skew": None, "kurtosis": None,
    }


def _v(fs, *, mc=None, sp=None, ret=10.0, trades=20):
    return trust_verdict(
        forward_stats=fs, monte_carlo=mc, sub_periods=sp,
        total_return_pct=ret, n_trades=trades,
    )


def test_insufficient_data_short_or_few_trades():
    assert _v(_fs(n_obs=10))["verdict"] == "insufficient_data"
    assert _v(_fs(n_obs=250), trades=2)["verdict"] == "insufficient_data"


def test_no_edge_on_loss_or_low_psr():
    assert _v(_fs(psr=0.99), ret=-2.0)["verdict"] == "no_edge"       # loss short-circuits
    assert _v(_fs(psr=0.50), ret=3.0)["verdict"] == "no_edge"        # Sharpe not probably +ve


def test_promising_requires_strong_psr_dsr_and_track_record():
    v = _v(_fs(psr=0.98, dsr=0.97, min_trl=100), ret=30.0)
    assert v["verdict"] == "promising"
    assert v["confidence"] >= 95


def test_unproven_when_track_record_too_short():
    v = _v(_fs(psr=0.97, dsr=0.96, min_trl=400, n_obs=250), ret=15.0)
    assert v["verdict"] == "unproven"  # MinTRL 400 > 250 obs


def test_unproven_middle_confidence():
    v = _v(_fs(psr=0.80, dsr=0.78, min_trl=100), ret=10.0)
    assert v["verdict"] == "unproven"


def test_all_risk_flags_fire():
    v = _v(
        _fs(psr=0.80, dsr=0.40, min_trl=100, num_trials=6),
        mc={"dd_p95_severity_pct": -45.0, "prob_loss": 0.7},
        sp={"concentration": 0.8},
    )
    assert set(v["flags"]) == {
        "selection_bias", "drawdown_risk", "loss_likely", "return_concentrated",
    }


def test_promising_with_flags_is_downgraded_in_label():
    v = _v(_fs(psr=0.98, dsr=0.97, min_trl=100), sp={"concentration": 0.8}, ret=30.0)
    assert v["verdict"] == "promising"
    assert "return_concentrated" in v["flags"]
    assert "watch the risks" in v["label"].lower()


def test_rationale_is_nonempty_for_every_verdict():
    for fs, kw in [
        (_fs(n_obs=10), {}),
        (_fs(psr=0.50), {"ret": 3.0}),
        (_fs(psr=0.98, dsr=0.97, min_trl=100), {"ret": 30.0}),
        (_fs(psr=0.80, dsr=0.78, min_trl=100), {}),
    ]:
        v = _v(fs, **kw)
        assert isinstance(v["rationale"], str) and v["rationale"]
        assert 0 <= v["confidence"] <= 100
