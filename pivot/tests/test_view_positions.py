"""Tests for the My Views position ledger (/api/views/positions).

Prices are injected by monkeypatching ``positions_svc.get_mark_price`` (the
same seam paper-trading tests use for marks) — no network, fully
deterministic. Conftest's dev-mode auth resolves user_id=1.
"""
from __future__ import annotations

import pytest

from backend.config import settings
from backend.models import (
    ExpressionKind,
    ExpressionTier,
    MarketView,
    ViewExpression,
    ViewPosition,
    ViewStatus,
    ViewType,
)
from backend.view_markets import positions as positions_svc


@pytest.fixture(autouse=True)
def _enable_flag(monkeypatch):
    monkeypatch.setattr(settings, "view_markets_enabled", True)
    # Conftest pins APP_ENV=test; the ledger endpoints resolve the dev
    # fallback user (id=1) only in development — mirror that here.
    monkeypatch.setattr(settings, "app_env", "development")


@pytest.fixture(autouse=True)
def _fixed_marks(monkeypatch):
    """Deterministic live prices: entry 100, now 110 (HAL) / 95 (BEL)."""
    prices = {"HAL": 110.0, "BEL": 95.0}
    monkeypatch.setattr(
        positions_svc, "get_mark_price", lambda sym, *a, **k: prices.get(sym)
    )
    return prices


def _seed_view_with_expression(db, *, kind=ExpressionKind.basket):
    v = MarketView(
        title="Will defence stocks rally?",
        view_type=ViewType.event,
        status=ViewStatus.open,
        category="equity_rotation",
        thesis="t",
        time_horizon="3-6m",
    )
    db.add(v)
    db.flush()
    e = ViewExpression(
        view_id=v.id,
        tier=ExpressionTier.conservative,
        expression_kind=kind,
        config={
            "label": "Cons basket",
            "instruments": [
                {"symbol": "HAL", "exchange": "NSE", "tradeable": True},
                {"symbol": "BEL", "exchange": "NSE", "tradeable": True},
            ],
            "structure": {
                "scheme": "equal_weight",
                "weights": {"HAL": 0.5, "BEL": 0.5},
            },
        },
    )
    db.add(e)
    db.flush()
    return v, e


def _seed_position(db, *, capital=100000.0, entry=100.0):
    v, e = _seed_view_with_expression(db)
    pos = ViewPosition(
        user_id=1,
        view_id=str(v.id),
        expression_id=str(e.id),
        capital_inr=capital,
        open_fraction=1.0,
        legs=[
            {"symbol": "HAL", "side": "long", "weight": 0.5,
             "entry_price": entry},
            {"symbol": "BEL", "side": "long", "weight": 0.5,
             "entry_price": entry},
        ],
        exits=[],
    )
    db.add(pos)
    db.flush()
    db.commit()
    return v, e, pos


# ── ledger creation on deploy ────────────────────────────────────────────────


def test_create_position_snapshots_legs_with_real_marks(db):
    v, e = _seed_view_with_expression(db)
    pos = positions_svc.create_position(db, v, e, user_id=1, capital_inr=50000)
    assert {leg["symbol"] for leg in pos.legs} == {"HAL", "BEL"}
    by_sym = {leg["symbol"]: leg for leg in pos.legs}
    assert by_sym["HAL"]["entry_price"] == 110.0   # injected mark
    assert by_sym["BEL"]["entry_price"] == 95.0
    assert pos.capital_inr == 50000
    assert float(pos.open_fraction) == 1.0


def test_members_long_fallback_builds_equal_weight_legs(db, monkeypatch):
    """The live v3 research structures carry ``members_long`` (equal weight)
    instead of explicit ``weights`` — the ledger must still snapshot legs."""
    v, e = _seed_view_with_expression(db)
    e.config = {
        **dict(e.config),
        "structure": {"scheme": "equal_weight",
                      "members_long": ["HAL", "BEL"]},
    }
    db.flush()
    pos = positions_svc.create_position(db, v, e, user_id=1)
    assert {leg["symbol"] for leg in pos.legs} == {"HAL", "BEL"}
    assert all(leg["weight"] == 0.5 for leg in pos.legs)


def test_option_expression_gets_no_priced_legs_and_honest_note(db):
    v, e = _seed_view_with_expression(db, kind=ExpressionKind.option_strategy)
    pos = positions_svc.create_position(db, v, e, user_id=1)
    assert pos.legs == []
    assert "Priced" in (pos.note or "") or "strikes" in (pos.note or "")


# ── list endpoint: live returns math ─────────────────────────────────────────


def test_list_positions_returns_live_weighted_return(client, db):
    _seed_position(db)  # entry 100 → HAL 110 (+10%), BEL 95 (−5%) → +2.5%
    r = client.get("/api/views/positions")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["view_title"] == "Will defence stocks rally?"
    assert item["view_status"] == "open"
    assert item["view_resolved"] is False
    assert item["status"] == "open"
    assert item["return_pct"] == pytest.approx(2.5)
    assert item["open_value_inr"] == pytest.approx(102500.0)
    assert item["unrealized_pnl_inr"] == pytest.approx(2500.0)
    legs = {leg["symbol"]: leg for leg in item["legs"]}
    assert legs["HAL"]["return_pct"] == pytest.approx(10.0)
    assert legs["BEL"]["return_pct"] == pytest.approx(-5.0)


def test_resolved_view_flag_and_unpriceable_legs_are_honest(client, db, monkeypatch):
    v, e, pos = _seed_position(db)
    v.status = ViewStatus.resolved
    db.commit()
    # Kill the price feed — the return must be None, NEVER a fabricated 0.
    monkeypatch.setattr(
        positions_svc, "get_mark_price", lambda sym, *a, **k: None
    )
    r = client.get("/api/views/positions")
    item = r.json()["items"][0]
    assert item["view_resolved"] is True
    assert item["return_pct"] is None
    assert item["unrealized_pnl_inr"] is None
    # Principal-only value (capital × open fraction) — no invented return.
    assert item["open_value_inr"] == pytest.approx(100000.0)


# ── edit: take-profit / stop-loss / size ─────────────────────────────────────


def test_patch_sets_and_clears_tp_sl(client, db):
    _, _, pos = _seed_position(db)
    r = client.patch(
        f"/api/views/positions/{pos.id}",
        json={"take_profit_pct": 12, "stop_loss_pct": 6},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["take_profit_pct"] == 12
    assert body["stop_loss_pct"] == 6
    # +2.5% live: neither level is hit.
    assert body["take_profit_hit"] is False
    assert body["stop_loss_hit"] is False

    # Tight TP below the live return → hit, computed at read time.
    r = client.patch(
        f"/api/views/positions/{pos.id}", json={"take_profit_pct": 2}
    )
    assert r.json()["take_profit_hit"] is True

    # Explicit null clears the level.
    r = client.patch(
        f"/api/views/positions/{pos.id}", json={"take_profit_pct": None}
    )
    body = r.json()
    assert body["take_profit_pct"] is None
    assert body["take_profit_hit"] is False


def test_patch_rejects_nonpositive_levels(client, db):
    _, _, pos = _seed_position(db)
    r = client.patch(
        f"/api/views/positions/{pos.id}", json={"stop_loss_pct": -3}
    )
    assert r.status_code in (400, 422)


# ── exits: partial and full ──────────────────────────────────────────────────


def test_partial_exit_records_realized_pnl_and_fraction(client, db):
    _, _, pos = _seed_position(db)  # live +2.5% on ₹100,000
    r = client.post(f"/api/views/positions/{pos.id}/exit", json={"pct": 40})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exited_pct"] == 40
    assert "broker app" in body["note"]  # register-not-execute reminder
    p = body["position"]
    assert p["status"] == "open"
    assert p["open_fraction"] == pytest.approx(0.6)
    # Realized on the exited slice: 100000 × 0.4 × 2.5% = ₹1,000.
    assert p["realized_pnl_inr"] == pytest.approx(1000.0)
    assert len(p["exits"]) == 1
    assert p["exits"][0]["return_pct"] == pytest.approx(2.5)


def test_full_exit_closes_the_position(client, db):
    _, _, pos = _seed_position(db)
    r = client.post(f"/api/views/positions/{pos.id}/exit", json={"pct": 100})
    p = r.json()["position"]
    assert p["status"] == "exited"
    assert p["open_fraction"] == 0.0
    assert p["exited_at"] is not None
    assert p["realized_pnl_inr"] == pytest.approx(2500.0)

    # A second exit on a closed position is a clean validation error.
    r = client.post(f"/api/views/positions/{pos.id}/exit", json={"pct": 10})
    assert r.status_code in (400, 422)


def test_exit_pct_bounds_enforced(client, db):
    _, _, pos = _seed_position(db)
    for bad in (0, -5, 101):
        r = client.post(
            f"/api/views/positions/{pos.id}/exit", json={"pct": bad}
        )
        assert r.status_code in (400, 422), f"pct={bad} should be rejected"


# ── deploy → ledger wiring ───────────────────────────────────────────────────


def test_deploy_creates_ledger_position(client, db, monkeypatch):
    v, e = _seed_view_with_expression(db)
    db.commit()
    # deploy_expression needs the full workflow stack; stub it — this test
    # asserts the LEDGER wiring, not the workflow synthesis.
    import backend.routers.views as views_router

    monkeypatch.setattr(
        views_router,
        "deploy_expression",
        lambda *a, **k: {
            "workflow_id": "wf-1",
            "status": "draft",
            "steps": [{"step_type": "trigger.schedule"}],
            "activated": False,
        },
    )
    r = client.post(
        f"/api/views/expressions/{e.id}/deploy",
        json={"capital_inr": 75000},
    )
    assert r.status_code == 200, r.text
    rows = db.query(ViewPosition).filter(ViewPosition.user_id == 1).all()
    assert len(rows) == 1
    assert rows[0].capital_inr == 75000
    assert rows[0].workflow_id == "wf-1"
    assert {leg["symbol"] for leg in rows[0].legs} == {"HAL", "BEL"}

    # Deploying the same expression again does NOT duplicate the ledger row.
    e.workflow_id = "wf-1"
    from backend.models import Workflow, WorkflowStatus

    db.add(Workflow(id="wf-1", user_id=1, name="x",
                    status=WorkflowStatus.draft))
    db.commit()
    r = client.post(f"/api/views/expressions/{e.id}/deploy", json={})
    assert r.status_code == 200, r.text
    rows = db.query(ViewPosition).filter(ViewPosition.user_id == 1).all()
    assert len(rows) == 1


def test_positions_flag_gated(client, db, monkeypatch):
    monkeypatch.setattr(settings, "view_markets_enabled", False)
    r = client.get("/api/views/positions")
    assert r.status_code == 404
