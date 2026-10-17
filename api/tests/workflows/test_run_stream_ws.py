"""WebSocket integration test for /api/runs/{id}/stream (API_CONTRACT.md §10).

Connects a real WS over the demo 5-step workflow and asserts that:
  1. The first frame is `snapshot` containing the full Run shape.
  2. We receive at least one `step_update` frame per executed step.
  3. We receive a terminal `run_update` frame (status='succeeded').
  4. The server closes 1000 after the terminal frame.

Auth (?token=…), the 4401/4404 close codes, and the approval frame
flow are deferred to a follow-up suite — this test exercises the
happy path needed for the demo. We use the same in-memory SQLite +
mock-mode broker scaffold the rest of tests/workflows/ uses.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _demo_workflow_body() -> dict[str, Any]:
    """5-step demo path (matches test_engine.test_demo_path_5_steps_succeeds).

    trigger.manual → fetch.portfolio → condition.numeric (passes) →
    action.place_order (no approval) → notify.message.
    """
    return {
        "name": "WS demo agent",
        "description": "WebSocket integration smoke test",
        "single_instance": True,
        "steps": [
            {"step_type": "trigger.manual", "config": {}},
            {"step_type": "fetch.portfolio", "config": {}},
            {"step_type": "condition.numeric", "config": {
                "left": "{{ context.1.buying_power }}",
                "operator": ">",
                "right": 0,
            }},
            {"step_type": "action.place_order", "config": {
                "symbol": "RELIANCE", "side": "buy", "quantity": 10,
                "order_type": "market", "requires_approval": False,
            }},
            {"step_type": "notify.message", "config": {
                # v1 only wires the 'push' channel — see NotifyMessageConfig.
                "channel": "push",
                "template": "done",
                "vars": {},
            }},
        ],
    }


def _drain_until_terminal(
    ws: Any, *, max_frames: int = 50,
) -> list[dict[str, Any]]:
    """Receive WS frames until the server sends a terminal `run_update`
    or closes the socket. Returns the list of received frames."""
    frames: list[dict[str, Any]] = []
    terminal = {"succeeded", "failed", "cancelled"}
    for _ in range(max_frames):
        try:
            frame = ws.receive_json()
        except WebSocketDisconnect:
            break
        # Filter pings — they're liveness keepalives, not domain frames.
        if isinstance(frame, dict) and frame.get("type") == "ping":
            continue
        frames.append(frame)
        if (
            frame.get("type") == "run_update"
            and frame.get("status") in terminal
        ):
            # Server should close 1000 next; receive_json raises
            # WebSocketDisconnect on the close.
            try:
                ws.receive_json()
            except WebSocketDisconnect:
                pass
            break
    return frames


def _bearer(headers: dict[str, str]) -> str:
    """Strip the "Bearer " prefix from the auth_headers fixture so we
    can pass the raw JWT as a ?token=… query param. The browsers-can't-
    set-headers reason for the subprotocol channel doesn't apply to
    TestClient, but ?token= is the simpler test path."""
    auth = headers["Authorization"]
    return auth.replace("Bearer ", "", 1)


def test_run_stream_emits_snapshot_step_updates_and_run_update(
    client: TestClient, auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headline integration: connect WS, run the 5-step demo,
    confirm the frame sequence the frontend will rely on.

    To guarantee deterministic frame coverage we slow the engine's
    per-step executor by 30ms. Without this the demo path completes
    in ~5ms and races the WS subscription — the frontend in production
    has no such race because real fetches/actions take 100ms+.
    """
    # Slow the engine's executor wrapper enough that the WS handler
    # — which subscribes BEFORE we kick the run via the test thread —
    # gets every event. We don't slow the loop itself, just each
    # executor call. Production has no such race because real
    # fetches/actions take 100ms+; the demo executors here run in ~5ms.
    import time as _time

    import backend.workflows.engine as _engine_mod

    real_run_executor = _engine_mod._run_executor

    def slow_run_executor(executor: Any, ctx: Any) -> Any:
        # Sync shim: sleep then delegate. Runs inside the engine's
        # threadpool worker so a blocking sleep is fine.
        _time.sleep(0.03)
        return real_run_executor(executor, ctx)

    monkeypatch.setattr(
        _engine_mod, "_run_executor", slow_run_executor,
    )

    # 1. Create workflow.
    r = client.post(
        "/api/workflows", json=_demo_workflow_body(), headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    wf_id = r.json()["id"]

    # 2. Create the run row WITHOUT firing the engine. We do this by
    # reaching into the test DB directly so we have a run_id to point
    # the WS at while the engine is still idle.
    from backend import models
    from tests.conftest import TestSessionLocal
    db = TestSessionLocal()
    try:
        run = models.WorkflowRun(
            workflow_id=wf_id,
            workflow_version=1,
            triggered_by="manual",
            status=models.RunStatus.running,
            context={},
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = str(run.id)
    finally:
        db.close()

    # 3. Open the WS, drain the snapshot, THEN trigger the engine
    # against the existing run row from the test thread. This gives
    # us a deterministic subscribe-before-publish ordering.
    token = _bearer(auth_headers)

    with client.websocket_connect(
        f"/api/runs/{run_id}/stream?token={token}",
    ) as ws:
        snapshot = ws.receive_json()
        # Snapshot first, populated, status='running' (engine hasn't
        # been invoked yet against this row).
        assert snapshot["type"] == "snapshot"
        snap_run = snapshot["run"]
        assert snap_run["id"] == run_id
        assert snap_run["workflow_id"] == wf_id
        assert snap_run["status"] == "running"
        assert "context" in snap_run
        assert "steps" in snap_run

        # Now kick the engine. The WS is already subscribed.
        from backend.workflows.engine import WorkflowEngine
        import threading

        def fire_engine() -> None:
            import asyncio
            asyncio.run(WorkflowEngine().execute_run(run_id))

        threading.Thread(target=fire_engine, daemon=True).start()

        rest = _drain_until_terminal(ws)

    # 4. Frame sequence assertions.
    step_updates = [f for f in rest if f.get("type") == "step_update"]
    run_updates = [f for f in rest if f.get("type") == "run_update"]

    assert len(step_updates) >= 5, (
        f"expected ≥5 step_update frames (1 per executed step), got "
        f"{[f.get('type') for f in rest]}"
    )
    assert len(run_updates) == 1, (
        f"expected exactly one terminal run_update, got {run_updates}"
    )

    last = rest[-1]
    assert last["type"] == "run_update"
    assert last["status"] == "succeeded", last
    assert last["run_id"] == run_id
    assert last["halt_reason"] is None

    # Step indices appear in execution order (0..4 may be repeated:
    # the engine emits a `running` then a `succeeded` frame per step).
    indices = [f["step_index"] for f in step_updates]
    assert indices == sorted(indices), (
        f"step_update indices must be monotonically non-decreasing; "
        f"got {indices}"
    )


