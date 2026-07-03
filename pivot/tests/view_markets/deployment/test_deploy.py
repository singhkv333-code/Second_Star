"""Focused unit tests for ``deploy_expression`` (Phase-4 deploy wiring).

Guardrails under test (the binding contract):
  * routing — each ``ExpressionKind`` deploys to its ``ACTION_STEP_BY_KIND``
    action step (basket→allocate_basket, option/hedge→place_option_strategy,
    pair→long order + honest short),
  * register-not-execute — every order/option/basket step is
    ``requires_approval=True``, live option strategies use ``book='live'``, and
    NO order is ever placed (deploy only arms a draft + persists it),
  * honest degrade — an AVOID short becomes long + ``notify.message`` (never a
    fabricated short); a leveraged MCX leg can't ride an equity basket and is
    surfaced (``deferred_legs``) instead of silently armed; a structure with no
    tradeable leg raises ``ValueError`` rather than inventing one,
  * commodity — MCX expressions route to the MCX underlying, carry the leverage
    note, and are never auto-sized.

Persistence rides the in-memory SQLite ``db`` (the parent conftest ``create_all``
builds the workflow tables); the schedule-arming side effect (``activate``) is
mocked so no real cron/scheduler runs.
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    ExpressionKind,
    ExpressionTier,
    MarketView,
    ViewExpression,
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from backend.view_markets.deployment.deploy import deploy_expression

# ── builders ────────────────────────────────────────────────────────────────


def _persist_expression(
    db: Session,
    view: MarketView,
    *,
    kind: str,
    config: dict,
    tier: str = "balanced",
) -> ViewExpression:
    expr = ViewExpression(
        view_id=view.id,
        tier=ExpressionTier(tier),
        expression_kind=ExpressionKind(kind),
        config=config,
        rationale="why",
        risk_profile="risk",
        capital_intensity="cap",
        historical_strength="hist",
        time_horizon="3m",
    )
    db.add(expr)
    db.flush()
    return expr


def _timing(mode: str = "pre_position") -> dict:
    return {
        "mode": mode,
        "tranches": [
            {
                "pct": 100,
                "trigger": {
                    "step_type": "trigger.schedule",
                    "config": {"run_at": "2026-07-01T09:20:00", "timezone": "Asia/Kolkata"},
                },
            }
        ],
        "invalidation": None,
        "note": "armed",
    }


def _basket_config() -> dict:
    return {
        "label": "Crude-up beneficiaries basket",
        "expression_kind": "basket",
        "structure": {"scheme": "equal", "weights": {"ONGC": 0.5, "OIL": 0.5}},
        "instruments": [
            {"symbol": "ONGC", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True},
            {"symbol": "OIL", "exchange": "NSE", "segment": "EQ",
             "instrument_type": "equity", "role": "long", "tradeable": True},
        ],
        "timing": _timing(),
    }


def _option_config(*, commodity: bool = False) -> dict:
    underlying = "CRUDEOIL" if commodity else "BANKNIFTY"
    seg = "MCX-OPT" if commodity else "NFO-OPT"
    itype = "commodity_option" if commodity else "index_option"
    structure: dict[str, Any] = {
        "template": "bull_call_spread",
        "underlying": underlying,
        "qty_lots": 1,
        "legs": [],
        "net_premium": 1.0,
        "max_loss": 100.0,
        "max_profit": 200.0,
        "pop": 0.5,
        "breakevens": [1.0],
        "net_greeks": {},
        "capital_required": 100.0,
    }
    if commodity:
        from backend.view_markets.expressions import commodities

        structure["leverage_note"] = commodities.LEVERAGE_NOTE
    return {
        "label": f"{underlying} debit spread",
        "expression_kind": "option_strategy",
        "structure": structure,
        "instruments": [
            {"symbol": f"{underlying}25CE", "exchange": "MCX" if commodity else "NFO",
             "segment": seg, "instrument_type": itype, "role": "long",
             "tradeable": True},
        ],
        "timing": _timing(),
    }


def _pair_config(short_mode: str) -> dict:
    return {
        "label": "TCS vs INFY pair",
        "expression_kind": "pair",
        "structure": {
            "a": "TCS",
            "b": "INFY",
            "beta": 1.0,
            "z_entry": 2.0,
            "z_exit": 0.5,
            "z_stop": 4.0,
            "leg_a": {"symbol": "TCS", "side": "long", "notional": 50000.0},
            "leg_b": {"symbol": "INFY", "side": "short"},
            "short_leg": {
                "symbol": "INFY",
                "mode": short_mode,
                "instrument": "INFY FUT" if short_mode != "avoid" else "INFY",
                "tradeable": short_mode != "avoid",
                "degraded": short_mode == "avoid",
                "note": "honest short note",
            },
        },
        "instruments": [],
        "timing": _timing(),
    }


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def view(make_curated_view) -> MarketView:
    return make_curated_view(view_type="theme", title="Crude oil higher into winter")


# ── routing ─────────────────────────────────────────────────────────────────


def test_basket_routes_to_allocate_basket_and_links_workflow(
    db: Session, view: MarketView
) -> None:
    expr = _persist_expression(db, view, kind="basket", config=_basket_config())
    out = deploy_expression(db, expr, user_id=7)

    assert out["register_not_execute"] is True
    assert out["status"] == "draft"
    assert out["activated"] is False
    action_steps = [s for s in out["steps"] if s["step_type"].startswith("action.")]
    assert [s["step_type"] for s in action_steps] == ["action.allocate_basket"]
    basket = action_steps[0]["config"]
    assert basket["requires_approval"] is True
    assert {leg["symbol"] for leg in basket["legs"]} == {"ONGC", "OIL"}
    # never auto-sized — total_inr is a user-filled placeholder ref, not a number.
    assert isinstance(basket["total_inr"], str)

    # workflow_id linked + the draft persisted with the trigger first.
    assert expr.workflow_id == out["workflow_id"]
    wf = db.get(Workflow, out["workflow_id"])
    assert wf is not None and wf.status == WorkflowStatus.draft and wf.user_id == 7
    rows = (
        db.query(WorkflowStep)
        .filter(WorkflowStep.workflow_id == wf.id)
        .order_by(WorkflowStep.step_index)
        .all()
    )
    assert rows[0].step_type == "trigger.schedule"
    assert any(r.step_type == "action.allocate_basket" for r in rows)


def test_option_routes_to_place_option_strategy_book_live(
    db: Session, view: MarketView
) -> None:
    expr = _persist_expression(
        db, view, kind="option_strategy", config=_option_config()
    )
    out = deploy_expression(db, expr, user_id=7)

    opt = next(s for s in out["steps"] if s["step_type"] == "action.place_option_strategy")
    assert opt["config"]["book"] == "live"        # register-not-execute
    assert opt["config"]["requires_approval"] is True
    assert opt["config"]["underlying"] == "BANKNIFTY"
    assert opt["config"]["template"] == "bull_call_spread"
    assert out["leverage_note"] is None           # not a commodity


# ── honest short / degrade ──────────────────────────────────────────────────


def test_pair_long_plus_honest_future_short(db: Session, view: MarketView) -> None:
    expr = _persist_expression(
        db, view, kind="pair", config=_pair_config("index_future")
    )
    out = deploy_expression(db, expr, user_id=7)

    orders = [s for s in out["steps"] if s["step_type"] == "action.place_order"]
    assert len(orders) == 2
    sides = {o["config"]["side"] for o in orders}
    assert sides == {"buy", "short"}
    assert all(o["config"]["requires_approval"] is True for o in orders)
    # the long leg uses the builder-computed notional; the short is never auto-sized.
    long_leg = next(o for o in orders if o["config"]["side"] == "buy")
    assert long_leg["config"]["notional_inr"] == 50000.0
    short_leg = next(o for o in orders if o["config"]["side"] == "short")
    assert isinstance(short_leg["config"]["notional_inr"], str)


def test_pair_avoid_short_becomes_notify_not_a_fabricated_short(
    db: Session, view: MarketView
) -> None:
    expr = _persist_expression(db, view, kind="pair", config=_pair_config("avoid"))
    out = deploy_expression(db, expr, user_id=7)

    orders = [s for s in out["steps"] if s["step_type"] == "action.place_order"]
    notifies = [s for s in out["steps"] if s["step_type"] == "notify.message"]
    assert len(orders) == 1 and orders[0]["config"]["side"] == "buy"
    assert len(notifies) == 1  # the one-sided expression is explained, not shorted


def test_basket_with_only_untradeable_legs_degrades_honestly(
    db: Session, view: MarketView
) -> None:
    cfg = _basket_config()
    for inst in cfg["instruments"]:
        inst["tradeable"] = False
    expr = _persist_expression(db, view, kind="basket", config=cfg)
    with pytest.raises(ValueError):
        deploy_expression(db, expr, user_id=7)


# ── commodity ───────────────────────────────────────────────────────────────


def test_commodity_option_routes_to_mcx_underlying_with_leverage_note(
    db: Session, view: MarketView
) -> None:
    expr = _persist_expression(
        db, view, kind="option_strategy", config=_option_config(commodity=True)
    )
    out = deploy_expression(db, expr, user_id=7)

    opt = next(s for s in out["steps"] if s["step_type"] == "action.place_option_strategy")
    assert opt["config"]["underlying"] == "CRUDEOIL"   # the MCX underlying
    assert opt["config"]["book"] == "live"
    assert opt["config"]["requires_approval"] is True
    assert out["leverage_note"]                         # leverage note carried
    assert out["leverage_note"] in out["note"]


def test_commodity_leg_cannot_ride_an_equity_basket_and_is_deferred(
    db: Session, view: MarketView
) -> None:
    """Honest degrade: a leveraged MCX leg is surfaced (deferred), never silently
    armed inside an equity allocate_basket — and the leverage note rides along."""
    cfg = _basket_config()
    cfg["structure"]["weights"]["CRUDEOIL"] = 0.3
    cfg["instruments"].append(
        {"symbol": "CRUDEOIL", "exchange": "MCX", "segment": "MCX-FUT",
         "instrument_type": "commodity_future", "role": "long", "tradeable": True}
    )
    expr = _persist_expression(db, view, kind="basket", config=cfg)
    out = deploy_expression(db, expr, user_id=7)

    basket = next(s for s in out["steps"] if s["step_type"] == "action.allocate_basket")
    assert "CRUDEOIL" not in {leg["symbol"] for leg in basket["config"]["legs"]}
    assert "CRUDEOIL" in out["deferred_legs"]
    assert out["leverage_note"]


# ── register-not-execute: no order placed, approval gating end-to-end ────────


def test_no_order_is_placed_and_every_order_step_is_approval_gated(
    db: Session, view: MarketView, monkeypatch: pytest.MonkeyPatch
) -> None:
    expr = _persist_expression(db, view, kind="basket", config=_basket_config())

    # If deploy ever tried to actually execute, it would have to go through one of
    # the order-placement seams. Trip-wire them so any call fails the test.
    import backend.routers.orders as orders_router

    for name in dir(orders_router):
        if name.startswith("place") or name.startswith("register"):
            obj = getattr(orders_router, name, None)
            if callable(obj):
                monkeypatch.setattr(
                    orders_router, name,
                    lambda *a, **k: pytest.fail("deploy must never place an order"),
                    raising=False,
                )

    out = deploy_expression(db, expr, user_id=7)
    order_like = [
        s for s in out["steps"]
        if s["step_type"] in {
            "action.allocate_basket", "action.place_order",
            "action.place_option_strategy",
        }
    ]
    assert order_like
    assert all(s["config"].get("requires_approval") is True for s in order_like)


def test_activate_arms_the_schedule_without_placing_an_order(
    db: Session, view: MarketView, monkeypatch: pytest.MonkeyPatch
) -> None:
    expr = _persist_expression(db, view, kind="basket", config=_basket_config())

    calls: dict[str, Any] = {}

    def _fake_upsert(_db: Session, wf: Workflow) -> None:
        calls["wf_id"] = wf.id

    import backend.workflows.scheduler as scheduler

    monkeypatch.setattr(scheduler, "upsert_workflow_schedule", _fake_upsert)

    out = deploy_expression(db, expr, user_id=7, activate=True)
    assert out["activated"] is True
    assert out["status"] == "active"
    assert calls.get("wf_id") == out["workflow_id"]   # arming path was taken


def test_missing_user_id_is_rejected(db: Session, view: MarketView) -> None:
    expr = _persist_expression(db, view, kind="basket", config=_basket_config())
    with pytest.raises(ValueError):
        deploy_expression(db, expr)  # curated view has no user → no owner
