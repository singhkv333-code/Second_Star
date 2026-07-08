"""Tests for /api/stocks/{symbol}/automations (#52).

The Phase 3 stock detail surface overlays trigger lines, past fires,
and upcoming scheduled dates on top of a price chart. This endpoint
extracts that overlay data from the user's active+paused workflows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import (
    RunStatus,
    Workflow,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)


def _user_id(auth_headers: dict[str, str]) -> int:
    from backend.auth.jwt_handler import get_user_id_from_token
    return int(
        get_user_id_from_token(
            auth_headers["Authorization"].replace("Bearer ", ""),
        )
    )


def _seed_wf(
    db: Session, user_id: int, name: str, *,
    status: WorkflowStatus = WorkflowStatus.active,
) -> Workflow:
    wf = Workflow(user_id=user_id, name=name, status=status)
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return wf


def _add_step(
    db: Session, wf: Workflow, idx: int, step_type: str, cfg: dict,
) -> None:
    db.add(WorkflowStep(
        workflow_id=wf.id, step_index=idx, step_type=step_type, config=cfg,
    ))
    db.commit()


def test_unauth(client: TestClient) -> None:
    r = client.get("/api/stocks/RELIANCE/automations")
    assert r.status_code == 401


def test_no_workflows_returns_empty_overlays(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.get(
        "/api/stocks/RELIANCE/automations", headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "RELIANCE"
    assert body["automations"] == []
    assert body["triggers"] == []
    assert body["past_fires"] == []
    assert body["scheduled"] == []


def test_extracts_trigger_price_level(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    uid = _user_id(auth_headers)
    wf = _seed_wf(db, uid, "RELIANCE buy on dip")
    _add_step(db, wf, 0, "trigger.price", {
        "symbol": "RELIANCE", "operator": ">", "value": 2400.0,
    })

    r = client.get(
        "/api/stocks/RELIANCE/automations", headers=auth_headers,
    )
    body = r.json()
    assert len(body["automations"]) == 1
    assert body["automations"][0]["matched_steps"] == 1
    assert len(body["triggers"]) == 1
    t = body["triggers"][0]
    assert t["kind"] == "price"
    assert t["level"] == 2400.0
    assert "Buy trigger" in t["label"]
    assert "₹2,400" in t["label"]


def test_extracts_stoploss_and_limit_order(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    uid = _user_id(auth_headers)
    wf = _seed_wf(db, uid, "RELIANCE protected entry")
    _add_step(db, wf, 0, "trigger.manual", {})
    _add_step(db, wf, 1, "action.place_order", {
        "symbol": "RELIANCE", "side": "buy",
        "quantity": 10, "order_type": "limit", "limit_price": 2380.0,
    })
    _add_step(db, wf, 2, "action.set_stoploss", {
        "symbol": "RELIANCE", "trigger_price": 2200.0,
    })

    body = client.get(
        "/api/stocks/RELIANCE/automations", headers=auth_headers,
    ).json()
    kinds = sorted(t["kind"] for t in body["triggers"])
    assert kinds == ["limit_buy", "stoploss"]
    levels = {t["kind"]: t["level"] for t in body["triggers"]}
    assert levels["limit_buy"] == 2380.0
    assert levels["stoploss"] == 2200.0


def test_skips_steps_for_other_symbols(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    uid = _user_id(auth_headers)
    wf = _seed_wf(db, uid, "INFY workflow")
    _add_step(db, wf, 0, "trigger.price", {
        "symbol": "INFY", "operator": "<", "value": 1500.0,
    })
    body = client.get(
        "/api/stocks/RELIANCE/automations", headers=auth_headers,
    ).json()
    assert body["automations"] == []
    assert body["triggers"] == []


def test_indicator_trigger_emits_indicator_kind(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    uid = _user_id(auth_headers)
    wf = _seed_wf(db, uid, "RELIANCE RSI oversold")
    _add_step(db, wf, 0, "trigger.indicator", {
        "symbol": "RELIANCE", "indicator": "rsi", "period": 14,
        "operator": "<", "value": 30.0,
    })
    triggers = client.get(
        "/api/stocks/RELIANCE/automations", headers=auth_headers,
    ).json()["triggers"]
    assert len(triggers) == 1
    assert triggers[0]["kind"] == "indicator"
    assert triggers[0]["level"] == 30.0
    assert "RSI" in triggers[0]["label"]


def test_archived_workflows_excluded(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    uid = _user_id(auth_headers)
    wf = _seed_wf(db, uid, "old", status=WorkflowStatus.archived)
    _add_step(db, wf, 0, "trigger.price", {
        "symbol": "RELIANCE", "operator": ">", "value": 1.0,
    })
    body = client.get(
        "/api/stocks/RELIANCE/automations", headers=auth_headers,
    ).json()
    assert body["automations"] == []


def test_paused_workflows_included(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    """Paused = user has the agent set up but isn't running it. Still
    valuable on the chart as a 'planned' overlay."""
    uid = _user_id(auth_headers)
    wf = _seed_wf(db, uid, "paused agent", status=WorkflowStatus.paused)
    _add_step(db, wf, 0, "trigger.price", {
        "symbol": "RELIANCE", "operator": ">", "value": 2500.0,
    })
    body = client.get(
        "/api/stocks/RELIANCE/automations", headers=auth_headers,
    ).json()
    assert len(body["automations"]) == 1
    assert body["automations"][0]["status"] == "paused"


def test_includes_past_fires(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    uid = _user_id(auth_headers)
    wf = _seed_wf(db, uid, "RELIANCE intraday")
    _add_step(db, wf, 0, "trigger.price", {
        "symbol": "RELIANCE", "operator": ">", "value": 2400.0,
    })
    # Insert past runs.
    db.add(WorkflowRun(
        workflow_id=wf.id,
        workflow_version=1,
        triggered_by="price_alert",
        status=RunStatus.succeeded,
        started_at=datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc) + timedelta(seconds=2),
    ))
    db.add(WorkflowRun(
        workflow_id=wf.id,
        workflow_version=1,
        triggered_by="manual",
        status=RunStatus.failed,
        started_at=datetime(2026, 3, 18, 11, 0, tzinfo=timezone.utc),
    ))
    db.commit()

    fires = client.get(
        "/api/stocks/RELIANCE/automations", headers=auth_headers,
    ).json()["past_fires"]
    assert len(fires) == 2
    # Newest first.
    assert fires[0]["status"] == "failed"
    assert fires[0]["triggered_by"] == "manual"
    assert fires[1]["status"] == "succeeded"


def test_scheduled_emits_upcoming_for_cron(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    uid = _user_id(auth_headers)
    wf = _seed_wf(db, uid, "RELIANCE morning agent")
    # Step 0 is trigger.schedule, step 1 references RELIANCE.
    _add_step(db, wf, 0, "trigger.schedule", {
        "cron": "55 9 * * 1-5", "timezone": "Asia/Kolkata",
    })
    _add_step(db, wf, 1, "fetch.quote", {"symbol": "RELIANCE"})

    body = client.get(
        "/api/stocks/RELIANCE/automations", headers=auth_headers,
    ).json()
    assert len(body["scheduled"]) == 5  # cap at 5 forward fires
    for f in body["scheduled"]:
        assert f["fire_time_local"].endswith("IST")


def test_other_users_workflows_excluded(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    other = client.post(
        "/auth/register",
        json={"email": "stock-other@pivot.com", "password": "password123"},
    )
    other_uid = other.json()["user_id"]
    wf = _seed_wf(db, other_uid, "not yours")
    _add_step(db, wf, 0, "trigger.price", {
        "symbol": "RELIANCE", "operator": ">", "value": 9999.0,
    })

    body = client.get(
        "/api/stocks/RELIANCE/automations", headers=auth_headers,
    ).json()
    assert body["automations"] == []
