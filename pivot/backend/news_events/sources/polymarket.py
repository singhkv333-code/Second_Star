"""Polymarket Gamma API client.

Free, no auth (verified in Phase-0 research). Used as the Tier-3
prediction-market cross-check. We only consume the parts we need —
``search_markets`` for first-time resolution and ``get_market`` for
the periodic price check — and intentionally stay narrow on the
surface so a future swap (Kalshi, Manifold, etc.) is a small
implementation rather than a refactor.

Endpoint shape (May 2026):

  GET https://gamma-api.polymarket.com/markets?search=<query>&closed=false
  GET https://gamma-api.polymarket.com/markets/{id_or_slug}

A binary YES/NO market response carries an ``outcomes`` array
(e.g. ``["Yes", "No"]``) and an ``outcomePrices`` array
(``["0.62", "0.38"]``). The YES price is the implied probability
the event resolves YES.

This module never raises — every error path returns ``None`` so the
aggregator can keep moving on a single market-down moment.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)


_BASE_URL = "https://gamma-api.polymarket.com"
_TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True)
class PolymarketSnapshot:
    """One point-in-time read of a Polymarket binary market.

    Used both for the aggregator's threshold check and for the
    audit payload persisted on ``news_fired_events.prediction_market_snapshot``.
    """

    market_id: str
    slug: Optional[str]
    question: Optional[str]
    yes_price: float
    closed: bool
    raw: dict


def _user_agent() -> str:
    return settings.news_events_user_agent


def _client() -> httpx.AsyncClient:
    """Builds the AsyncClient with our identifying UA + a sane timeout.
    Polymarket has been generous about anonymous reads; we still send
    a real UA so they can rate-limit cleanly if needed."""
    headers = {"User-Agent": _user_agent(), "Accept": "application/json"}
    return httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, headers=headers)


def _parse_yes_price(market: dict) -> Optional[float]:
    """Tolerant parser. Polymarket's payload has historically been
    ``outcomes`` + ``outcomePrices`` as parallel arrays of strings.
    The Gamma API also exposes some markets with a flatter ``tokens``
    array; handle both shapes."""
    outcomes = market.get("outcomes")
    prices = market.get("outcomePrices")

    if isinstance(outcomes, str):
        # Some Gamma responses return outcomes as JSON-string. Defensive.
        import json
        try:
            outcomes = json.loads(outcomes)
        except json.JSONDecodeError:
            outcomes = None
    if isinstance(prices, str):
        import json
        try:
            prices = json.loads(prices)
        except json.JSONDecodeError:
            prices = None

    if isinstance(outcomes, list) and isinstance(prices, list) and len(outcomes) == len(prices):
        for label, price in zip(outcomes, prices):
            if isinstance(label, str) and label.strip().lower() in {"yes", "true"}:
                try:
                    return float(price)
                except (TypeError, ValueError):
                    return None

    # ``tokens`` array fallback. Each entry typically has
    # {"outcome": "Yes", "price": 0.62}.
    tokens = market.get("tokens")
    if isinstance(tokens, list):
        for t in tokens:
            if not isinstance(t, dict):
                continue
            label = str(t.get("outcome", "")).strip().lower()
            if label in {"yes", "true"}:
                try:
                    return float(t.get("price"))
                except (TypeError, ValueError):
                    return None
    return None


def _snapshot_from_payload(market: dict) -> Optional[PolymarketSnapshot]:
    yes = _parse_yes_price(market)
    if yes is None:
        return None
    market_id = str(market.get("id") or market.get("conditionId") or "").strip()
    if not market_id:
        return None
    return PolymarketSnapshot(
        market_id=market_id,
        slug=market.get("slug") or None,
        question=market.get("question") or None,
        yes_price=max(0.0, min(1.0, yes)),
        closed=bool(market.get("closed", False)),
        raw=market,
    )


async def search_markets(
    query: str, *, limit: int = 5
) -> list[PolymarketSnapshot]:
    """Search open markets matching ``query``.

    The Gamma ``search`` parameter is broad — it matches on question
    text, slug, and tags. We cap the result size at ``limit`` and
    skip closed markets unless none of the matches are open.
    """
    q = (query or "").strip()
    if not q:
        return []
    params = {"search": q, "limit": max(1, limit), "closed": "false"}
    try:
        async with _client() as client:
            resp = await client.get(f"{_BASE_URL}/markets", params=params)
    except httpx.HTTPError as exc:
        logger.warning(
            "[news_events.polymarket] search network error query=%r err=%s",
            query, exc,
        )
        return []

    if resp.status_code != 200:
        logger.info(
            "[news_events.polymarket] search status=%s query=%r",
            resp.status_code, query,
        )
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    if not isinstance(data, list):
        # Some Gamma endpoints wrap results in {"data": [...]}.
        data = data.get("data") if isinstance(data, dict) else None
    if not isinstance(data, list):
        return []

    out: list[PolymarketSnapshot] = []
    for m in data:
        if not isinstance(m, dict):
            continue
        snap = _snapshot_from_payload(m)
        if snap is not None:
            out.append(snap)
        if len(out) >= limit:
            break
    return out


async def get_market(market_id_or_slug: str) -> Optional[PolymarketSnapshot]:
    """Fetch one market by id or slug. Returns None on any failure
    so callers can fall back gracefully."""
    ident = (market_id_or_slug or "").strip()
    if not ident:
        return None
    try:
        async with _client() as client:
            resp = await client.get(f"{_BASE_URL}/markets/{ident}")
    except httpx.HTTPError as exc:
        logger.warning(
            "[news_events.polymarket] get_market network error id=%r err=%s",
            ident, exc,
        )
        return None
    if resp.status_code != 200:
        logger.info(
            "[news_events.polymarket] get_market status=%s id=%r",
            resp.status_code, ident,
        )
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return _snapshot_from_payload(data)
