"""Focused unit tests for the two-dial confidence scorer.

Covers the load-bearing honesty rules (testing doc §2):
  * letter bands + suppression mapping,
  * each dial = weighted blend CAPPED by the Trust verdict (statistics can only
    cap, never inflate),
  * suppression below MinTRL / on ``insufficient_data``,
  * the DSR ≤ 0 selection-bias hard cap on the expression dial,
  * two_dial_score derives + keeps the dials SEPARATE (never averaged),
  * persist_confidence upserts exactly one row per dimension (UNIQUE).

Siblings (``EventStudyResult`` / ``SurpriseFraming``) are stand-in lightweight
namespaces so the test is self-contained and doesn't depend on mid-build modules.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.view_markets import confidence as conf


# ── letter_band + VERDICT_CEILING ─────────────────────────────────────────

@pytest.mark.parametrize(
    "score,letter",
    [(None, None), (0, "E"), (19, "E"), (20, "D"), (39, "D"),
     (40, "C"), (59, "C"), (60, "B"), (79, "B"), (80, "A"), (100, "A")],
)
def test_letter_band(score, letter):
    assert conf.letter_band(score) == letter


def test_verdict_ceiling_table():
    assert conf.VERDICT_CEILING == {
        "insufficient_data": None,
        "no_edge": 39,
        "unproven": 79,
        "promising": 100,
    }


# ── score_outcome_dial ─────────────────────────────────────────────────────

def test_outcome_suppressed_on_insufficient_data():
    dial = conf.score_outcome_dial(
        hit_rate=0.9, sample_n=3, verdict="insufficient_data"
    )
    assert dial.suppressed is True
    assert dial.score is None and dial.letter is None
    assert "N=3" in dial.rationale


def test_outcome_suppressed_below_min_trl():
    dial = conf.score_outcome_dial(
        hit_rate=0.9, sample_n=4, min_trl=12, verdict="promising"
    )
    assert dial.suppressed is True
    assert dial.score is None


def test_outcome_capped_by_verdict():
    # Strong soft inputs but a no_edge verdict -> hard-capped at 39 (band D).
    dial = conf.score_outcome_dial(
        hit_rate=0.95, edge_vs_priced=0.3, sample_n=20, min_trl=12,
        verdict="no_edge",
    )
    assert dial.suppressed is False
    assert dial.score == 39          # ceiling, not the higher raw blend
    assert dial.letter == "D"
    assert dial.components["raw"] > dial.score  # statistics only capped


def test_outcome_promising_uncapped_blend():
    dial = conf.score_outcome_dial(
        hit_rate=0.75, edge_vs_priced=0.05, sample_n=12, min_trl=12,
        verdict="promising",
    )
    assert dial.suppressed is False
    # blend = (.40*.75 + .30*.55 + .15*1.0) / (.40+.30+.15) = .615/.85 = .724
    # -> 72 (B), under the 100 ceiling (renormalised over present weights).
    assert dial.score == 72
    assert dial.letter == "B"


def test_outcome_no_inputs_suppressed():
    dial = conf.score_outcome_dial(verdict="promising")
    assert dial.suppressed is True
    assert "No scorable" in dial.rationale


# ── score_expression_dial ──────────────────────────────────────────────────

def test_expression_dsr_hard_cap():
    # Great alignment/significance but DSR<=0 -> capped at no_edge ceiling (39).
    dial = conf.score_expression_dial(
        caar_bhar_alignment=0.9, significance_p=0.01, cost_survival=0.9,
        payoff_pop=0.6, verdict="unproven", deflated_sharpe=-0.2, n_obs=120,
        min_trl=60,
    )
    assert dial.suppressed is False
    assert dial.score == 39
    assert "selection bias" in dial.rationale


def test_expression_suppressed_below_min_trl():
    dial = conf.score_expression_dial(
        caar_bhar_alignment=0.8, n_obs=20, min_trl=60, verdict="unproven"
    )
    assert dial.suppressed is True
    assert dial.score is None


def test_expression_blend_and_band():
    dial = conf.score_expression_dial(
        caar_bhar_alignment=0.7, significance_p=0.04, cost_survival=0.6,
        payoff_pop=0.5, verdict="promising", deflated_sharpe=0.8, n_obs=120,
        min_trl=60,
    )
    # blend = .35*.7 + .25*.6 + .25*.6 + .15*.5 = .245+.15+.15+.075 = .62 -> 62
    assert dial.suppressed is False
    assert dial.score == 62
    assert dial.letter == "B"


# ── two_dial_score (derivation + never-averaged) ────────────────────────────

def _fake_event_study(*, verdict, caar, mean_bhar, bmp_p, nonparam_p,
                      both_agree, n_events, dsr, n_obs, min_trl, cars):
    car_by_event = tuple(
        SimpleNamespace(car=c) for c in cars
    )
    significance = SimpleNamespace(
        bmp_p=bmp_p, nonparam_p=nonparam_p, both_agree=both_agree,
    )
    return SimpleNamespace(
        verdict={"verdict": verdict, "flags": ["selection_bias"]},
        forward_stats={"deflated_sharpe": dsr, "n_obs": n_obs,
                       "min_trl": min_trl},
        significance=significance,
        caar=caar,
        mean_bhar=mean_bhar,
        n_events=n_events,
        car_by_event=car_by_event,
    )


def test_two_dial_derives_both_separately():
    es = _fake_event_study(
        verdict="promising", caar=0.012, mean_bhar=0.02, bmp_p=0.02,
        nonparam_p=0.03, both_agree=True, n_events=12, dsr=0.7, n_obs=120,
        min_trl=60, cars=[0.01, 0.02, -0.005, 0.03, 0.01, 0.02, -0.01, 0.04,
                          0.01, 0.02, 0.03, 0.01],
    )
    surprise = SimpleNamespace(implied_probability=0.70, hidden_prior=0.65)
    td = conf.two_dial_score(event_study=es, surprise=surprise)

    assert td.outcome.dimension == "outcome"
    assert td.expression.dimension == "expression"
    # Two distinct dials, no combined/averaged scalar exists on the result.
    assert not hasattr(td, "combined")
    assert "selection_bias" in td.flags
    # hit_rate = 10/12 positive ≈ .833; edge = .833 - .70 = +.133 fed in.
    assert td.outcome.components["hit_rate"] == pytest.approx(10 / 12)
    assert td.outcome.components["edge_vs_priced"] == pytest.approx(
        10 / 12 - 0.70
    )
    assert td.outcome.score is not None
    assert td.expression.score is not None


def test_two_dial_overrides_and_suppression():
    es = _fake_event_study(
        verdict="insufficient_data", caar=0.0, mean_bhar=0.0, bmp_p=None,
        nonparam_p=None, both_agree=False, n_events=2, dsr=None, n_obs=2,
        min_trl=60, cars=[0.01, -0.02],
    )
    # Curator supplies relationship strength + option payoff via overrides; the
    # insufficient_data verdict must still SUPPRESS both dials.
    td = conf.two_dial_score(
        event_study=es,
        outcome_overrides={"relationship_strength": 0.9},
        expression_overrides={"cost_survival": 0.8, "payoff_pop": 0.6},
    )
    assert td.outcome.suppressed is True and td.outcome.score is None
    assert td.expression.suppressed is True and td.expression.score is None


def test_two_dial_uses_hidden_prior_when_no_option_implied():
    es = _fake_event_study(
        verdict="promising", caar=0.01, mean_bhar=0.01, bmp_p=0.02,
        nonparam_p=0.03, both_agree=True, n_events=10, dsr=0.7, n_obs=100,
        min_trl=60, cars=[0.01] * 8 + [-0.01] * 2,
    )
    surprise = SimpleNamespace(implied_probability=None, hidden_prior=0.5)
    td = conf.two_dial_score(event_study=es, surprise=surprise)
    # hit_rate = 8/10 = .8; falls back to hidden_prior .5 -> edge +.3
    assert td.outcome.components["edge_vs_priced"] == pytest.approx(0.8 - 0.5)


# ── persist_confidence (upsert, no commit) ──────────────────────────────────

def _seed_view(db):
    from backend.models import MarketView, ViewType

    view = MarketView(view_type=ViewType.event, title="RBI holds -> BANKNIFTY up")
    db.add(view)
    db.flush()
    return view


def test_persist_confidence_upserts_one_row_per_dimension(view_db):
    from backend.models import ConfidenceDimension, ViewConfidence

    view = _seed_view(view_db)
    td = conf.TwoDialScore(
        outcome=conf.DialScore(
            "outcome", 72, "B", False, "promising", {}, "outcome ok"
        ),
        expression=conf.DialScore(
            "expression", None, None, True, "insufficient_data", {},
            "suppressed"
        ),
    )
    rows = conf.persist_confidence(view_db, view.id, td)
    assert len(rows) == 2

    stored = {
        r.dimension: r
        for r in view_db.query(ViewConfidence)
        .filter(ViewConfidence.view_id == view.id)
        .all()
    }
    assert stored[ConfidenceDimension.outcome].score == pytest.approx(0.72)
    assert stored[ConfidenceDimension.expression].score is None

    # Re-running upserts in place (UNIQUE(view_id, dimension)) — no duplicates.
    td2 = conf.TwoDialScore(
        outcome=conf.DialScore(
            "outcome", 40, "C", False, "unproven", {}, "downgraded"
        ),
        expression=conf.DialScore(
            "expression", 30, "D", False, "no_edge", {}, "now scored"
        ),
    )
    conf.persist_confidence(view_db, view.id, td2)
    after = (
        view_db.query(ViewConfidence)
        .filter(ViewConfidence.view_id == view.id)
        .all()
    )
    assert len(after) == 2  # still two rows, not four
    stored2 = {r.dimension: r for r in after}
    assert stored2[ConfidenceDimension.outcome].score == pytest.approx(0.40)
    assert stored2[ConfidenceDimension.expression].score == pytest.approx(0.30)
