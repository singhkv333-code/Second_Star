"""Phase-4 deployment-package test scaffold.

Reuses the parent ``tests/view_markets/conftest.py`` (in-memory SQLite ``db`` /
``view_db``, View-Markets ``create_all``, lifecycle rebind). pytest does NOT
share the *sibling* ``tests/view_markets/expressions/conftest.py``, so the
``make_curated_view`` factory the BUILD agents' backtest / compare / deploy
tests need is re-provided here (mirroring the expressions scaffold). Like that
one it only ``flush``es (the parent ``db`` fixture rolls back its txn), so
generated ids are available without committing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterator, Optional

import pytest
from sqlalchemy.orm import Session

from backend.models import MarketView
from backend.view_markets import curation


@pytest.fixture
def make_curated_view(
    view_db: Session,
) -> Iterator[Callable[..., MarketView]]:
    """Factory: persist a curated ``MarketView`` (status ``open``, no user),
    flushed so its id is available for ``compare_tiers`` / ``suggest_expressions``.
    """
    from backend.schemas import MarketViewCreate

    def _make(
        *,
        view_type: str = "event",
        title: str = "Test view",
        thesis: Optional[str] = "Seed thesis for a deployment test.",
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
