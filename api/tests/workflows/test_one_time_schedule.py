"""One-time ("just for tomorrow") scheduling — schema + macro hydration.

The schedule trigger now expresses EITHER a recurring clock (`cron`) or a
single fire (`run_at`). The LLM resolves a relative ask ("just tomorrow at
1pm") to an absolute `run_at` itself; these tests cover the capability that
lets it do so, end to end through the builder macro.
"""
import pytest

from backend.services.workflow_macros import hydrate_scheduled_order
from backend.workflows.schemas import TriggerScheduleConfig


# ── schema: exactly one of cron / run_at ──────────────────────────────────

def test_schedule_config_accepts_cron_only() -> None:
    cfg = TriggerScheduleConfig(cron="0 13 * * 1-5")
    assert cfg.cron == "0 13 * * 1-5"
    assert cfg.run_at is None


def test_schedule_config_accepts_run_at_only() -> None:
    cfg = TriggerScheduleConfig(run_at="2026-06-22T13:00:00")
    assert cfg.run_at == "2026-06-22T13:00:00"
    assert cfg.cron is None


def test_schedule_config_rejects_both() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        TriggerScheduleConfig(cron="0 13 * * 1-5", run_at="2026-06-22T13:00:00")


def test_schedule_config_rejects_neither() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        TriggerScheduleConfig()


def test_schedule_config_rejects_bad_run_at() -> None:
    with pytest.raises(ValueError, match="ISO 8601"):
        TriggerScheduleConfig(run_at="tomorrow afternoon")


# ── macro: one-time hydration ─────────────────────────────────────────────

def test_hydrate_one_time_builds_run_at_trigger() -> None:
    draft = hydrate_scheduled_order(
        symbol="reliance", side="buy", quantity=10,
        run_at="2026-06-22T13:00:00",
    )
    trig = draft["steps"][0]
    assert trig["step_type"] == "trigger.schedule"
    assert trig["config"]["run_at"] == "2026-06-22T13:00:00"
    assert "cron" not in trig["config"]
    # human-facing labels reflect the one-time, dated nature
    assert "Once on 22 Jun 2026" in trig["label"]
    assert draft["name"].lower().startswith("once on 22 jun 2026")
    # and it still validates against the registry schema
    TriggerScheduleConfig(**trig["config"])


def test_hydrate_recurring_still_builds_cron() -> None:
    draft = hydrate_scheduled_order(
        symbol="reliance", side="buy", quantity=10,
        days=["weekday"], time_ist="13:00",
    )
    trig = draft["steps"][0]
    assert trig["config"]["cron"] == "0 13 * * 1-5"
    assert "run_at" not in trig["config"]
    assert "every weekday" in trig["label"]


def test_hydrate_rejects_both_days_and_run_at() -> None:
    with pytest.raises(ValueError, match="not both"):
        hydrate_scheduled_order(
            symbol="reliance", side="buy", quantity=10,
            days=["weekday"], run_at="2026-06-22T13:00:00",
        )


def test_hydrate_rejects_neither_days_nor_run_at() -> None:
    with pytest.raises(ValueError, match="must specify"):
        hydrate_scheduled_order(symbol="reliance", side="buy", quantity=10)
