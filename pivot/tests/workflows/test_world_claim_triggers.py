"""World-claim triggers are refused at the propose boundary (2026-07-17).

Pivot watches PRICES, not events. Triggers that fire on something happening out
there — a rate decision, a headline, an earnings print, a prediction-market
contract resolving — are not supported for now.

Why a code gate rather than prompt text: the same boundary was tried in prose
for the alert lane and did not hold. And the failure mode here is SILENCE — an
unwired trigger validates, activates, and then simply never fires, which is
indistinguishable from one patiently waiting. `trigger.earnings` is the sharp
case: it IS in the step registry, so it passes registry validation cleanly while
its watcher sits behind a default-off flag.
"""
from __future__ import annotations

import pytest

from backend.workflows.propose import (
    ProposalValidationError,
    _reject_unavailable_triggers,
    build_system_prompt,
)


@pytest.mark.parametrize("step_type", [
    "trigger.polymarket",
    "trigger.kalshi",
    "trigger.scheduled_macro",
    "trigger.event",
    "trigger.earnings",
])
def test_world_claim_trigger_is_refused(step_type):
    with pytest.raises(ProposalValidationError) as e:
        _reject_unavailable_triggers({"steps": [
            {"step_type": step_type, "config": {}},
            {"step_type": "action.place_order", "config": {}},
        ]})
    # The refusal must TEACH, not just deny — the model needs the next move.
    msg = str(e.value)
    assert "NOT available" in msg
    assert "PRICE" in msg and "SCHEDULE" in msg


def test_price_and_schedule_agents_are_untouched():
    draft = {"steps": [
        {"step_type": "trigger.price", "config": {}},
        {"step_type": "action.place_order", "config": {}},
    ]}
    assert _reject_unavailable_triggers(draft) is draft


def test_earnings_boundary_tracks_the_flag_not_a_hardcode(monkeypatch):
    """Flip the watcher on and the refusal lifts — the boundary describes the
    real capability rather than freezing today's answer."""
    import backend.workflows.propose as P

    monkeypatch.setattr(P.settings, "earnings_events_enabled", True, raising=False)
    draft = {"steps": [{"step_type": "trigger.earnings", "config": {}}]}
    assert _reject_unavailable_triggers(draft) is draft
    # ...and the planner prompt advertises it again, instead of drifting.
    assert "trigger.earnings — a NAMED company" in build_system_prompt()


def test_planner_prompt_does_not_advertise_unavailable_lanes():
    p = build_system_prompt()
    assert "Earnings-outcome triggers" in p          # named as unavailable
    assert "trigger.earnings — a NAMED company" not in p
    # Polymarket may only appear on the NOT-AVAILABLE line.
    for line in p.splitlines():
        if "polymarket" in line.lower() or "kalshi" in line.lower():
            assert "Prediction-market triggers" in line
