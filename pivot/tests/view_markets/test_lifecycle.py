"""View Markets — lifecycle worker unit tests.

The full status ladder (developing -> consensus -> resolved -> archived with the
verifier outcome + ``resolved_value`` backfill) is exercised end-to-end in
``test_pipeline_integration.py``. This file covers what that does NOT:

  * the flag-gated scheduler registration (production-safety: the job must not
    exist when ``view_markets_enabled`` is off), and
  * the early-exit edge cases of :func:`advance_one_view` (unpublished drafts and
    the terminal ``archived`` status never advance; a published ``open`` view
    moves to ``developing``).

The ``lifecycle`` build agent died mid-response in the Phase-2 workflow before it
wrote its unit test; this closes that gap.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.config import settings
from backend.models import MarketView, ViewStatus, ViewType
from backend.view_markets import lifecycle


class _FakeScheduler:
    """Minimal AsyncIOScheduler stand-in that records ``add_job`` calls."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, **kwargs) -> None:  # noqa: ANN001 - test double
        self.jobs.append({"func": func, **kwargs})


# ── registration gating (the bit the integration test can't reach) ──────────

def test_register_lifecycle_is_noop_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "view_markets_enabled", False, raising=False)
    sched = _FakeScheduler()
    lifecycle.register_view_markets_lifecycle(sched)
    assert sched.jobs == []  # no job exists when the flag is off


def test_register_lifecycle_adds_module_level_job_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "view_markets_enabled", True, raising=False)
    sched = _FakeScheduler()
    lifecycle.register_view_markets_lifecycle(sched)

    assert len(sched.jobs) == 1
    job = sched.jobs[0]
    # MUST register the module-level coroutine, never a closure (jobstore
    # serializes by textual reference — a closure silently kills the scheduler).
    assert job["func"] is lifecycle.advance_view_lifecycle
    assert job["id"] == lifecycle._LIFECYCLE_JOB_ID
    assert job["max_instances"] == 1
    assert job["coalesce"] is True
    assert job["replace_existing"] is True


# ── advance_one_view early-exits ────────────────────────────────────────────

def _view(**kw) -> MarketView:
    base = dict(title="t", view_type=ViewType.event, status=ViewStatus.open)
    base.update(kw)
    return MarketView(**base)


def test_unpublished_draft_never_advances(view_db) -> None:
    view = _view(published_at=None, status=ViewStatus.open)
    assert lifecycle.advance_one_view(view_db, view) is None
    assert view.status == ViewStatus.open


def test_archived_is_terminal(view_db) -> None:
    view = _view(
        published_at=datetime.now(timezone.utc), status=ViewStatus.archived,
    )
    assert lifecycle.advance_one_view(view_db, view) is None
    assert view.status == ViewStatus.archived


def test_published_open_view_moves_to_developing(view_db) -> None:
    view = _view(
        published_at=datetime.now(timezone.utc), status=ViewStatus.open,
    )
    new = lifecycle.advance_one_view(view_db, view)
    assert new == ViewStatus.developing.value
    assert view.status == ViewStatus.developing
