"""Phase-4 ``compare_tiers`` unit tests.

Mock the real engine (``backtest_expression``) and the Phase-3 builder
(``dispatch.suggest_expressions``) so these assert ONLY ``compare``'s wiring:

  * it backtests every present tier through ONE shared ``trial_group`` (the
    best-of-three DSR deflation),
  * it ranks by Trust verdict → alignment dial → confidence (higher risk never
    auto-wins),
  * it recommends honestly — never an ``insufficient_data`` / ``no_edge`` tier,
    and ``None`` when nothing clears the bar,
  * a COMMODITY tier whose MCX history is missing degrades to
    ``insufficient_data`` and is excluded from the recommendation (no fabricated
    curve, no order placed — compare arms nothing),
  * it builds a missing tier via ``suggest_expressions`` and skips a tier the
    catalog cannot express.

register-not-execute: compare only evaluates — no workflow/order primitive is
touched, so there is nothing to mock away on the execution path.
"""
from __future__ import annotations

from typing import Callable

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    ExpressionKind,
    ExpressionTier,
    MarketView,
    ViewExpression,
)
from backend.view_markets.deployment import compare as compare_mod
from backend.view_markets.deployment import compare_tiers


def _trust(
    verdict: str,
    *,
    alignment: float | None = None,
    confidence: float | None = None,
    engine: str = "portfolio",
    degraded: bool = False,
    data_note: str | None = None,
) -> dict:
    """A minimal but contract-shaped trust block (the value backtest_expression
    returns / stores at config.scores.trust)."""
    return {
        "verdict": verdict,
        "label": verdict.replace("_", " "),
        "confidence": confidence,
        "rationale": f"{verdict} rationale",
        "flags": [],
        "engine": engine,
        "backtest_run_id": "run-" + verdict,
        "metrics": {
            "total_return_pct": None,
            "max_drawdown_pct": None,
            "n_trades": None,
            "benchmark_return_pct": None,
            "forward_stats": None,
            "monte_carlo": None,
            "sub_periods": None,
        },
        "alignment": (
            {"expression_score": alignment} if alignment is not None else None
        ),
        "degraded": degraded,
        "data_note": data_note,
        "as_of": "2026-06-29T00:00:00+00:00",
    }


def _add_expr(
    db: Session,
    view: MarketView,
    tier: ExpressionTier,
    kind: ExpressionKind = ExpressionKind.basket,
) -> ViewExpression:
    row = ViewExpression(
        view_id=view.id,
        tier=tier,
        expression_kind=kind,
        config={},
        rationale="why",
        risk_profile="rp",
        capital_intensity="ci",
        historical_strength="hs",
        time_horizon="th",
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def three_tier_view(
    view_db: Session,
    make_curated_view: Callable[..., MarketView],
) -> MarketView:
    view = make_curated_view(view_type="relative", title="IT vs Nifty")
    for tier in ExpressionTier:
        _add_expr(view_db, view, tier)
    view_db.flush()
    return view


def test_backtests_every_tier_under_one_shared_trial_group(
    view_db: Session,
    three_tier_view: MarketView,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_groups: list[str] = []

    def fake_backtest(db, expr, *, trial_group=None, period=None, persist=True):
        seen_groups.append(trial_group)
        return _trust("unproven", alignment=50.0, confidence=55.0)

    monkeypatch.setattr(compare_mod, "backtest_expression", fake_backtest)

    out = compare_tiers(view_db, three_tier_view)

    # one backtest per tier, all sharing the SAME trial group (DSR best-of-3).
    assert len(seen_groups) == 3
    assert len(set(seen_groups)) == 1
    assert seen_groups[0] == f"view:{three_tier_view.id}:tier-compare"
    assert {t["tier"] for t in out["tiers"]} == {
        "conservative", "balanced", "aggressive",
    }
    assert out["view_id"] == three_tier_view.id
    assert "as_of" in out


def test_caller_trial_group_is_honored(
    view_db: Session,
    three_tier_view: MarketView,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        compare_mod,
        "backtest_expression",
        lambda db, e, *, trial_group=None, period=None, persist=True: (
            seen.append(trial_group) or _trust("no_edge")
        ),
    )
    compare_tiers(view_db, three_tier_view, trial_group="custom-group")
    assert set(seen) == {"custom-group"}


def test_ranks_by_verdict_then_alignment_recommends_best(
    view_db: Session,
    three_tier_view: MarketView,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Aggressive has the best raw verdict; Balanced unproven; Conservative no_edge.
    by_tier = {
        "conservative": _trust("no_edge", alignment=30.0, confidence=20.0),
        "balanced": _trust("promising", alignment=70.0, confidence=80.0),
        "aggressive": _trust("unproven", alignment=90.0, confidence=60.0),
    }

    def fake_backtest(db, expr, *, trial_group=None, period=None, persist=True):
        return by_tier[str(expr.tier.value)]

    monkeypatch.setattr(compare_mod, "backtest_expression", fake_backtest)

    out = compare_tiers(view_db, three_tier_view)

    # promising > unproven > no_edge — Balanced wins despite Aggressive's higher
    # alignment dial (verdict dominates; higher risk does not auto-win).
    assert out["ranking"] == ["balanced", "aggressive", "conservative"]
    assert out["recommended_tier"] == "balanced"
    assert "balanced" in out["recommendation_rationale"].lower()


def test_alignment_dial_breaks_a_verdict_tie(
    view_db: Session,
    three_tier_view: MarketView,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    by_tier = {
        "conservative": _trust("unproven", alignment=40.0, confidence=50.0),
        "balanced": _trust("unproven", alignment=75.0, confidence=50.0),
        "aggressive": _trust("unproven", alignment=10.0, confidence=50.0),
    }
    monkeypatch.setattr(
        compare_mod,
        "backtest_expression",
        lambda db, e, *, trial_group=None, period=None, persist=True: by_tier[
            str(e.tier.value)
        ],
    )
    out = compare_tiers(view_db, three_tier_view)
    assert out["ranking"] == ["balanced", "conservative", "aggressive"]
    assert out["recommended_tier"] == "balanced"


def test_never_recommends_below_unproven(
    view_db: Session,
    three_tier_view: MarketView,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Best available is no_edge — nothing clears the bar → no recommendation.
    by_tier = {
        "conservative": _trust("no_edge", alignment=80.0, confidence=30.0),
        "balanced": _trust("no_edge", alignment=20.0, confidence=10.0),
        "aggressive": _trust("insufficient_data"),
    }
    monkeypatch.setattr(
        compare_mod,
        "backtest_expression",
        lambda db, e, *, trial_group=None, period=None, persist=True: by_tier[
            str(e.tier.value)
        ],
    )
    out = compare_tiers(view_db, three_tier_view)
    assert out["recommended_tier"] is None
    assert "none" in out["recommendation_rationale"].lower()
    # the no_edge tier with the higher alignment still ranks first, but is NOT
    # recommended — ranking != recommendation.
    assert out["ranking"][0] == "conservative"


def test_commodity_missing_history_degrades_and_is_not_recommended(
    view_db: Session,
    three_tier_view: MarketView,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The aggressive tier is a leveraged MCX commodity expression whose price
    # history is missing — backtest_expression hands back an honest degrade.
    by_tier = {
        "conservative": _trust("unproven", alignment=55.0, confidence=50.0),
        "balanced": _trust("no_edge", alignment=30.0, confidence=20.0),
        "aggressive": _trust(
            "insufficient_data",
            engine="none",
            degraded=True,
            data_note="No MCX price history for CRUDEOIL — cannot backtest.",
        ),
    }

    def fake_backtest(db, expr, *, trial_group=None, period=None, persist=True):
        return by_tier[str(expr.tier.value)]

    monkeypatch.setattr(compare_mod, "backtest_expression", fake_backtest)

    out = compare_tiers(view_db, three_tier_view)

    aggr = next(t for t in out["tiers"] if t["tier"] == "aggressive")
    assert aggr["trust"]["degraded"] is True
    assert aggr["trust"]["verdict"] == "insufficient_data"
    assert aggr["engine"] == "none"
    # honest degrade never wins: the proven-enough Conservative tier is chosen,
    # the degraded commodity tier ranks last and is never recommended.
    assert out["recommended_tier"] == "conservative"
    assert out["ranking"][-1] == "aggressive"


def test_builds_missing_tier_via_dispatch(
    view_db: Session,
    make_curated_view: Callable[..., MarketView],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = make_curated_view(view_type="relative", title="needs building")
    # Only the balanced tier exists up front.
    _add_expr(view_db, view, ExpressionTier.balanced)
    view_db.flush()

    built_for: list[str] = []

    def fake_suggest(db, v, tier=None):
        built_for.append(tier)
        return [_add_expr(db, v, ExpressionTier(tier))]

    import backend.view_markets.expressions.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "suggest_expressions", fake_suggest)
    monkeypatch.setattr(
        compare_mod,
        "backtest_expression",
        lambda db, e, *, trial_group=None, period=None, persist=True: _trust(
            "unproven", alignment=50.0, confidence=50.0
        ),
    )

    out = compare_tiers(view_db, view)

    # the two absent tiers were built; all three end up compared.
    assert set(built_for) == {"conservative", "aggressive"}
    assert {t["tier"] for t in out["tiers"]} == {
        "conservative", "balanced", "aggressive",
    }


def test_unexpressable_tier_is_honestly_skipped(
    view_db: Session,
    make_curated_view: Callable[..., MarketView],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = make_curated_view(view_type="event", title="only balanced builds")
    _add_expr(view_db, view, ExpressionTier.balanced, kind=ExpressionKind.pair)
    view_db.flush()

    import backend.view_markets.expressions.dispatch as dispatch_mod

    def fake_suggest(db, v, tier=None):
        raise dispatch_mod.ExpressionDispatchError(
            f"no archetype expresses this at {tier}"
        )

    monkeypatch.setattr(dispatch_mod, "suggest_expressions", fake_suggest)
    monkeypatch.setattr(
        compare_mod,
        "backtest_expression",
        lambda db, e, *, trial_group=None, period=None, persist=True: _trust(
            "promising", alignment=60.0, confidence=70.0
        ),
    )

    out = compare_tiers(view_db, view)

    # only the one expressable tier appears — the rest are honestly absent.
    assert {t["tier"] for t in out["tiers"]} == {"balanced"}
    assert out["ranking"] == ["balanced"]
    assert out["recommended_tier"] == "balanced"


def test_no_expressable_tiers_recommends_none_honestly(
    view_db: Session,
    make_curated_view: Callable[..., MarketView],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = make_curated_view(view_type="event", title="nothing builds")

    import backend.view_markets.expressions.dispatch as dispatch_mod

    monkeypatch.setattr(
        dispatch_mod,
        "suggest_expressions",
        lambda db, v, tier=None: (_ for _ in ()).throw(
            dispatch_mod.ExpressionDispatchError("none")
        ),
    )
    called = []
    monkeypatch.setattr(
        compare_mod,
        "backtest_expression",
        lambda *a, **k: called.append(1),
    )

    out = compare_tiers(view_db, view)

    assert out["tiers"] == []
    assert out["ranking"] == []
    assert out["recommended_tier"] is None
    assert "nothing to recommend" in out["recommendation_rationale"].lower()
    # nothing to backtest — and certainly nothing armed/placed.
    assert called == []
