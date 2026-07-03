"""Pydantic v2 models for the news library.

After the dedicated ``/api/triggers/*`` REST/WS stack was retired (see
``backend/triggers/__init__.py``), the only model this package still
exposes is ``NewsArticle`` — consumed by ``news_client.fetch_news`` and
``classifier.classify_article``, which together back the
``fetch.news`` and ``trigger.event`` workflow step executors.

The other shapes (``Workflow``, ``ParsedWorkflow``, ``WorkflowStep``)
were tied to the in-memory monitor / store / parser modules and were
removed alongside them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class NewsArticle(BaseModel):
    """A single fetched article, possibly classified.

    ``matched``, ``match_confidence`` and ``reason`` are populated by
    ``backend.triggers.classifier.classify_article`` after fetch. The
    ``fetch.news`` step executor returns the full list and an aggregate
    ``matched`` flag so a downstream ``condition.boolean`` can branch.
    """
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str = ""
    source: str = ""
    source_id: str = ""
    url: str = ""
    published_at: datetime
    credibility_score: float = 0.0
    match_confidence: Optional[float] = None
    matched: bool = False
    reason: Optional[str] = None
