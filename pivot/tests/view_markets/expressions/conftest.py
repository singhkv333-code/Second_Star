"""Phase-3 expression-engine test scaffold.

Reuses the parent ``tests/view_markets/conftest.py`` (auto-discovered by pytest):
the in-memory SQLite ``db`` fixture, the View-Markets table ``create_all``, and
the lifecycle-engine rebind. This adds a small seam the expression-engine tests
build on — a factory that persists a curated ``MarketView`` (the input
``suggest_expressions`` consumes) plus three ready-made event/relative/theme
views.

No commits (the parent ``db`` fixture flushes within a rolled-back txn); these
fixtures only ``flush`` so generated ids are available, matching
``curation.create_view``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator, Optional

import pytest
from sqlalchemy.orm import Session

from backend.models import MarketView
from backend.view_markets import curation


@pytest.fixture
def make_curated_view(
    view_db: Session,
) -> Iterator[Callable[..., MarketView]]:
    """Factory: persist a curated ``MarketView`` (status ``open``, no user).

    Usage::

        view = make_curated_view(
            view_type="relative", title="IT beats Nifty over 6m",
            thesis="...", category="relative_value", time_horizon="6m",
        )

    Returns the flushed ``MarketView`` with a generated id, ready for
    ``suggest_expressions(db, view)``.
    """
    from backend.schemas import MarketViewCreate

    def _make(
        *,
        view_type: str = "event",
        title: str = "Test view",
        thesis: Optional[str] = "Seed thesis for an expression-engine test.",
        category: Optional[str] = None,
        time_horizon: Optional[str] = "3m",
        resolution_date: Optional[datetime] = None,
    ) -> MarketView:
        payload = MarketViewCreate(
            view_type=view_type,  # type: ignore[arg-type]
            title=title,
            thesis=thesis,
            category=category,
            time_horizon=time_horizon,
            resolution_date=resolution_date,
        )
        view = curation.create_view(view_db, payload, user_id=None)
        view_db.flush()
        return view

    yield _make


@pytest.fixture
def event_view(make_curated_view: Callable[..., MarketView]) -> MarketView:
    """A curated EVENT view (RBI rate cut) with a resolution date."""
    return make_curated_view(
        view_type="event",
        title="RBI cuts the repo rate at the next MPC",
        thesis="Dovish guidance + softening CPI → a 25bp cut at the next MPC.",
        category="rates",
        time_horizon="1m",
        resolution_date=datetime.now(timezone.utc) + timedelta(days=21),
    )


@pytest.fixture
def relative_view(make_curated_view: Callable[..., MarketView]) -> MarketView:
    """A curated RELATIVE view (IT beats Nifty over 6 months)."""
    return make_curated_view(
        view_type="relative",
        title="IT outperforms the Nifty over 6 months",
        thesis="USD strength + AI-services demand → IT beats the broad index.",
        category="relative_value",
        time_horizon="6m",
    )


@pytest.fixture
def theme_view(make_curated_view: Callable[..., MarketView]) -> MarketView:
    """A curated THEME view (India manufacturing upcycle)."""
    return make_curated_view(
        view_type="theme",
        title="India manufacturing upcycle",
        thesis="PLI + China+1 + capex → a multi-year manufacturing upcycle.",
        category="manufacturing",
        time_horizon="2y+",
    )
