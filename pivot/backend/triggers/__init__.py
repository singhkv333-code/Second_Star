"""News-classification library.

This package used to host a dedicated ``/api/triggers/*`` REST/WS stack
with an in-memory monitor task, store, basket builder, parser and
fire-rule. That surface has been retired — news-driven workflows now
live as ordinary steps (``fetch.news``, ``trigger.event``) inside the
existing workflow engine, proposed by the chatbot's ``propose_workflow``
tool.

What remains is a small library used by those step executors:

  - ``models.NewsArticle``                 Pydantic shape for one article
  - ``news_client.fetch_news``             NewsAPI fetch (mock-safe)
  - ``classifier.classify_article``        LLM verification per article
  - ``credibility.score_source``           tiered source-trust score
  - ``credibility.source_brand_domain``    NewsAPI source-id → logo domain
"""
from __future__ import annotations
