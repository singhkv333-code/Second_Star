"""Tests for the IPO P2 step types (trigger.ipo_open + action.arm_ipo_intent).

Coverage:
  (a) registry catalog exposes both step types; the workflows API
      ``_validate_steps`` accepts a [trigger.ipo_open, action.arm_ipo_intent,
      notify.message] workflow and REJECTS action.arm_ipo_intent at step 0.
  (b) ``execute_action_arm_ipo_intent`` writes an ``intent_armed`` row
      with ``autonomous=True`` and a sentinel proves NO broker / paper /
      kite function is called on the action's hot path.
  (c) ``build_ipo_reminder_draft`` returns a registry-valid draft.
  (d) The IPO-open watcher fires once: simulate scan -> match open ->
      fire, assert ``_fire_watch_run`` called once and the
      ``_ipo_open_fired`` latch is set; a second poll does NOT re-fire.
  (e) ``GET /ipo-calendar`` returns the items from a stubbed feed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    IPOApplication,
    RunStatus,
    User,
    Workflow,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from backend.workflows.registry import STEP_REGISTRY, get_catalog
from backend.workflows import scheduler as scheduler_mod


# ── shared IPO feed stub (mirrors tests/test_ipo_applications.py) ─────

_OPEN_IPO = {
    "found": True,
    "ipo": {
        "name": "Tikona Infinet",
        "symbol": "TIKONA",
        "price_band": "125-132",
        "open_date": "2026-06-03",
        "close_date": "2026-06-05",
        "lot_size": 110,
        "issue_size": "₹1,200 cr",
        "type": "mainboard",
        "status": "open",
    },
    "extra": {"rhpLink": "https://example.com/rhp.pdf"},
    "source": "nse",
}


def _stub_get_ipo_details(_query: str) -> dict[str, Any]:
    return dict(_OPEN_IPO)


def _stub_list_upcoming_ipos(*, status: str = "open") -> dict[str, Any]:
    """Builds the shape ``ipo_feed.list_upcoming_ipos()`` returns."""
    ipo_record = dict(_OPEN_IPO["ipo"])
    ipo_record["status"] = status
    return {
        "count": 1,
        "ipos": [ipo_record],
        "source": "nse",
        "note": None,
        "cached": False,
    }


# ─────────────────────────────────────────────────────────────────────
# (a) registry + _validate_steps
# ─────────────────────────────────────────────────────────────────────

def test_registry_exposes_ipo_step_types() -> None:
    """Both new step types must appear in the registry + catalog."""
    catalog = get_catalog()
    types_in_catalog = {s["step_type"] for s in catalog["step_types"]}
    assert "trigger.ipo_open" in types_in_catalog
    assert "action.arm_ipo_intent" in types_in_catalog

    # And the version bump on registry.CATALOG_VERSION is reflected
    # in the catalog payload (FE uses this to invalidate its cache).
    assert catalog["catalog_version"].startswith("2026-")

    # Sanity: the configs validate happy-path values.
    trig_cfg = STEP_REGISTRY["trigger.ipo_open"].config_model.model_validate(
        {"symbol": "TIKONA"},
    )
    assert trig_cfg.symbol == "TIKONA"  # type: ignore[attr-defined]

    arm_cfg = STEP_REGISTRY["action.arm_ipo_intent"].config_model.model_validate(
        {
            "ipo_symbol": "TIKONA",
            "quantity_lots": 1,
            "category": "retail",
            "bid_price_mode": "cutoff",
        },
    )
    assert arm_cfg.ipo_symbol == "TIKONA"  # type: ignore[attr-defined]


def test_validate_steps_accepts_full_ipo_workflow() -> None:
    """The full 3-step IPO reminder workflow passes router validation."""
    from backend.routers.workflows import _validate_steps

    steps = [
        {
            "step_type": "trigger.ipo_open",
            "config": {"symbol": "TIKONA"},
        },
        {
            "step_type": "action.arm_ipo_intent",
            "config": {
                "ipo_symbol": "TIKONA",
                "quantity_lots": 1,
                "category": "retail",
                "bid_price_mode": "cutoff",
            },
        },
        {
            "step_type": "notify.message",
            "config": {
                "channel": "push",
                "template": "Pivot has NOT applied — apply yourself by 5 PM.",
                "vars": {},
            },
        },
    ]

    # Must not raise.
    _validate_steps(steps)


def test_validate_steps_rejects_arm_ipo_intent_at_step_0() -> None:
    """action.arm_ipo_intent is NOT a trigger — step 0 must be a trigger.*."""
    from backend.routers.workflows import _validate_steps

    steps = [
        {
            "step_type": "action.arm_ipo_intent",
            "config": {
                "ipo_symbol": "TIKONA",
                "quantity_lots": 1,
                "category": "retail",
                "bid_price_mode": "cutoff",
            },
        },
    ]
    with pytest.raises(Exception) as ei:
        _validate_steps(steps)
    # The router raises a validation_error envelope wrapping HTTPException;
    # the message mentions step 0 must be a trigger.
    msg = str(ei.value).lower()
    assert "trigger" in msg


# ─────────────────────────────────────────────────────────────────────
# (b) execute_action_arm_ipo_intent — writes intent_armed + no broker call
# ─────────────────────────────────────────────────────────────────────


def _make_workflow_ctx(
    db: Session, user_id: int, *, config: dict[str, Any],
):
    """Build a minimal real Workflow/Step/Run + ctx stub the executor reads.
    Mirrors the pattern in tests/test_paper_routing.py::_ctx.
    """
    wf = Workflow(
        user_id=user_id, name="ipo-arm-test",
        status=WorkflowStatus.active, version=1,
    )
    db.add(wf)
    db.flush()
    step = WorkflowStep(
        workflow_id=wf.id, step_index=1,
        step_type="action.arm_ipo_intent",
        config=config,
    )
    run = WorkflowRun(
        workflow_id=wf.id, workflow_version=1, triggered_by="event_alert",
        triggered_step_index=0,
        status=RunStatus.running, context={},
    )
    db.add_all([step, run])
    db.flush()
    db.refresh(wf)
    db.refresh(step)
    db.refresh(run)
    return SimpleNamespace(
        db=db, workflow=wf, step=step, run=run,
        config=config, attempts=1, client_request_id="ctx-test",
    )


def test_arm_ipo_intent_writes_intent_armed_row(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: action writes a row with autonomous=True, intent_armed,
    source='workflow-arm', and amount_estimate computed from the feed.
    """
    # Stub feed: see _OPEN_IPO above.
    monkeypatch.setattr(
        "backend.services.ipo_feed.get_ipo_details", _stub_get_ipo_details,
    )

    user = User(email="ipo_arm@pivot.com", hashed_password="x")
    db.add(user)
    db.flush()

    ctx = _make_workflow_ctx(
        db, user.id,
        config={
            "ipo_symbol": "TIKONA",
            "quantity_lots": 1,
            "category": "retail",
            "bid_price_mode": "cutoff",
        },
    )

    from backend.workflows.steps.actions import execute_action_arm_ipo_intent
    result = asyncio.run(execute_action_arm_ipo_intent(ctx))

    assert result is not None
    assert result["ipo_symbol"] == "TIKONA"
    assert result["status"] == "intent_armed"
    assert result["applied"] is False  # load-bearing: Pivot has NOT applied.
    # 1 * 110 * 132 = 14520
    assert result["amount_estimate"] == 14520.0

    row = db.query(IPOApplication).one()
    assert row.status == "intent_armed"
    assert row.autonomous is True
    assert row.source == "workflow-arm"
    assert row.user_id == user.id
    assert row.ipo_symbol == "TIKONA"
    assert row.amount_estimate == 14520.0
    assert row.upi_id_masked is None  # not collected on the autonomous path.


def test_arm_ipo_intent_no_broker_call(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hard P2 rule: arming MUST NOT call any broker / Kite / paper-broker
    submit function. Sentinel-wrap every plausible entry point and assert
    none fire.
    """
    monkeypatch.setattr(
        "backend.services.ipo_feed.get_ipo_details", _stub_get_ipo_details,
    )

    user = User(email="ipo_sentinel@pivot.com", hashed_password="x")
    db.add(user)
    db.flush()

    sentinels: list[str] = []

    def _make_sentinel(name: str):
        def _boom(*a: Any, **kw: Any) -> Any:
            sentinels.append(name)
            raise AssertionError(
                f"P2 violation: {name} was invoked on the arm path"
            )
        return _boom

    for dotted in (
        "backend.paper.routing.submit_order",
        "backend.paper.routing.submit_gtt",
        "backend.paper.routing.submit_order_for_user",
        "backend.kite.orders.place_order",
    ):
        try:
            monkeypatch.setattr(dotted, _make_sentinel(dotted))
        except (AttributeError, ModuleNotFoundError):
            continue

    ctx = _make_workflow_ctx(
        db, user.id,
        config={
            "ipo_symbol": "TIKONA",
            "quantity_lots": 1,
            "category": "retail",
            "bid_price_mode": "cutoff",
        },
    )

    from backend.workflows.steps.actions import execute_action_arm_ipo_intent
    asyncio.run(execute_action_arm_ipo_intent(ctx))

    assert sentinels == [], (
        f"P2 violation: broker functions invoked: {sentinels!r}"
    )


def test_arm_ipo_intent_stale_when_feed_unreachable(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feed unreachable → still arm (autonomous path can't block on NSE)
    with stale=True. amount_estimate must NOT be fabricated; the row
    stores 0.0 + the result dict's amount_estimate is None.
    """
    def _unreachable(_q: str) -> dict[str, Any]:
        return {
            "found": False,
            "source": "unreachable",
            "note": "NSE feed unreachable in test stub",
            "matches": [],
        }
    monkeypatch.setattr(
        "backend.services.ipo_feed.get_ipo_details", _unreachable,
    )

    user = User(email="ipo_stale@pivot.com", hashed_password="x")
    db.add(user)
    db.flush()

    ctx = _make_workflow_ctx(
        db, user.id,
        config={
            "ipo_symbol": "TIKONA",
            "quantity_lots": 1,
            "category": "retail",
            "bid_price_mode": "cutoff",
        },
    )

    from backend.workflows.steps.actions import execute_action_arm_ipo_intent
    result = asyncio.run(execute_action_arm_ipo_intent(ctx))
    assert result is not None
    assert result["status"] == "intent_armed"
    assert result["stale"] is True
    assert result["amount_estimate"] is None  # never fabricated

    row = db.query(IPOApplication).one()
    assert row.stale is True
    assert row.status == "intent_armed"
    assert row.autonomous is True


# ─────────────────────────────────────────────────────────────────────
# (c) build_ipo_reminder_draft — registry-valid
# ─────────────────────────────────────────────────────────────────────


def test_build_ipo_reminder_draft_validates_against_registry() -> None:
    """Drafts the macro emits must pass validate_draft_against_registry."""
    from backend.services.workflow_macros import build_ipo_reminder_draft
    from backend.workflows.propose import validate_draft_against_registry

    draft = build_ipo_reminder_draft(
        "TIKONA",
        dict(_OPEN_IPO["ipo"]),
        quantity_lots=1,
        category="retail",
        bid_price_mode="cutoff",
    )

    assert draft["_render_hint"] == "workflow_draft_card"
    assert len(draft["steps"]) == 3
    assert draft["steps"][0]["step_type"] == "trigger.ipo_open"
    assert draft["steps"][1]["step_type"] == "action.arm_ipo_intent"
    assert draft["steps"][2]["step_type"] == "notify.message"

    # Notify template must lead with the non-execution disclaimer.
    template = draft["steps"][2]["config"]["template"]
    assert template.startswith("Pivot has NOT applied")

    # Round-trip through the registry validator (already called inside
    # _validate_or_raise but assert here to catch any regression in
    # the macro that bypasses it).
    parsed = validate_draft_against_registry(draft)
    assert len(parsed.steps) == 3


# ─────────────────────────────────────────────────────────────────────
# (d) watcher fires once + does not re-fire on subsequent polls
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def _scheduler_db_shared(
    monkeypatch: pytest.MonkeyPatch, db: Session,
) -> None:
    """Mirror the share-session pattern in test_watcher.py so the
    watcher's SessionLocal lands on the test fixture's connection."""
    class _SharedSession:
        def __init__(self, real: Session) -> None:
            self._real = real

        def __enter__(self) -> Session:
            return self._real

        def __exit__(self, *a: object) -> None:
            pass

        def __getattr__(self, name: str) -> object:
            attr = getattr(self._real, name)
            if name == "close":
                return lambda: None
            return attr

    monkeypatch.setattr(
        "backend.workflows.scheduler.SessionLocal",
        lambda: _SharedSession(db),
    )

    async def _inline(func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)
    monkeypatch.setattr("asyncio.to_thread", _inline)


def _make_ipo_open_workflow(db: Session, *, symbol: str = "TIKONA") -> Workflow:
    wf = Workflow(
        user_id=1, name="ipo-open-test",
        status=WorkflowStatus.active, version=1,
    )
    db.add(wf)
    db.flush()
    step = WorkflowStep(
        workflow_id=wf.id, step_index=0,
        step_type="trigger.ipo_open",
        config={"symbol": symbol},
    )
    db.add(step)
    db.flush()
    db.refresh(wf)
    return wf


@pytest.mark.asyncio
async def test_ipo_open_watcher_fires_once_and_latches(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    _scheduler_db_shared: None,
) -> None:
    """End-to-end watcher test:
      1. Workflow with trigger.ipo_open(symbol=TIKONA) is active.
      2. Stub the feed to report TIKONA status='open'.
      3. First poll → _fire_watch_run invoked exactly once + latch set.
      4. Second poll (immediately) → no new fire (latch holds).
    """
    wf = _make_ipo_open_workflow(db, symbol="TIKONA")

    # Stub the feed call invoked inside the watcher.
    monkeypatch.setattr(
        "backend.services.ipo_feed.list_upcoming_ipos",
        lambda: _stub_list_upcoming_ipos(status="open"),
    )

    # Count calls to _fire_watch_run without actually launching the
    # engine task.
    fired: list[tuple[str, int, str]] = []

    async def _stub_fire(
        workflow_id: str, triggered_step_index: int,
        triggered_by: str, fired_at: datetime,
        audit_context: dict[str, Any] | None = None,
    ) -> str | None:
        fired.append((workflow_id, triggered_step_index, triggered_by))
        return "fake-run-id"

    monkeypatch.setattr(scheduler_mod, "_fire_watch_run", _stub_fire)

    # First poll: fires.
    await scheduler_mod._poll_ipo_open_triggers()
    assert len(fired) == 1
    assert fired[0][0] == str(wf.id)
    assert fired[0][1] == 0
    assert fired[0][2] == "event_alert"

    # Latch must now be set on the step.
    db.expire_all()
    step = (
        db.query(WorkflowStep)
        .filter(WorkflowStep.workflow_id == str(wf.id))
        .filter(WorkflowStep.step_index == 0)
        .first()
    )
    assert step is not None
    assert step.config.get(scheduler_mod._IPO_OPEN_FIRED_KEY) == "1"

    # Second poll: must NOT re-fire (latch holds).
    await scheduler_mod._poll_ipo_open_triggers()
    assert len(fired) == 1, "watcher must not re-fire after the latch is set"


@pytest.mark.asyncio
async def test_ipo_open_watcher_skips_when_feed_unreachable(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    _scheduler_db_shared: None,
) -> None:
    """Feed unreachable → log + return (try next tick). Never fire,
    never fabricate."""
    _make_ipo_open_workflow(db, symbol="TIKONA")

    monkeypatch.setattr(
        "backend.services.ipo_feed.list_upcoming_ipos",
        lambda: {
            "count": 0, "ipos": [], "source": "unreachable",
            "note": "NSE unreachable (test stub)", "cached": False,
        },
    )

    fired: list[Any] = []

    async def _stub_fire(*a: Any, **kw: Any) -> str | None:
        fired.append(a)
        return None

    monkeypatch.setattr(scheduler_mod, "_fire_watch_run", _stub_fire)
    await scheduler_mod._poll_ipo_open_triggers()
    assert fired == []


@pytest.mark.asyncio
async def test_ipo_open_watcher_skips_when_status_not_open(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    _scheduler_db_shared: None,
) -> None:
    """Status='upcoming' / 'closed' → no fire."""
    _make_ipo_open_workflow(db, symbol="TIKONA")

    monkeypatch.setattr(
        "backend.services.ipo_feed.list_upcoming_ipos",
        lambda: _stub_list_upcoming_ipos(status="upcoming"),
    )

    fired: list[Any] = []

    async def _stub_fire(*a: Any, **kw: Any) -> str | None:
        fired.append(a)
        return None

    monkeypatch.setattr(scheduler_mod, "_fire_watch_run", _stub_fire)
    await scheduler_mod._poll_ipo_open_triggers()
    assert fired == []


# ─────────────────────────────────────────────────────────────────────
# (e) GET /ipo-calendar
# ─────────────────────────────────────────────────────────────────────


def test_ipo_calendar_returns_stubbed_items(
    client, auth_headers, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The /ipo-calendar endpoint surfaces the stubbed feed shape and
    filters by [from, to] when supplied.
    """
    monkeypatch.setattr(
        "backend.routers.ipo_applications.list_upcoming_ipos",
        lambda: _stub_list_upcoming_ipos(status="open"),
    )

    # No date filter → returns the one row.
    r = client.get("/ipo-calendar", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["source"] == "nse"
    item = body["items"][0]
    assert item["ipo_symbol"] == "TIKONA"
    assert item["name"] == "Tikona Infinet"
    assert item["open_date"] == "2026-06-03"
    assert item["close_date"] == "2026-06-05"
    assert item["status"] == "open"
    assert item["type"] == "mainboard"

    # Window that includes the IPO → still returned.
    r2 = client.get(
        "/ipo-calendar?from=2026-06-01&to=2026-06-10",
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["count"] == 1

    # Window that ends BEFORE the IPO opens → excluded.
    r3 = client.get(
        "/ipo-calendar?from=2026-05-01&to=2026-05-30",
        headers=auth_headers,
    )
    assert r3.status_code == 200
    assert r3.json()["count"] == 0


def test_ipo_calendar_passes_through_unreachable_note(
    client, auth_headers, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feed unreachable → honest empty + note (never fabricate)."""
    monkeypatch.setattr(
        "backend.routers.ipo_applications.list_upcoming_ipos",
        lambda: {
            "count": 0, "ipos": [], "source": "unreachable",
            "note": "NSE unreachable (test stub)", "cached": False,
        },
    )
    r = client.get("/ipo-calendar", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["items"] == []
    assert body["source"] == "unreachable"
    assert "unreachable" in (body.get("note") or "").lower()
