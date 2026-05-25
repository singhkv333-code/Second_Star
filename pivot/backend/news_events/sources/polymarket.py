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


async def search_via_public_search(
    query: str, *, limit: int = 8,
) -> list[PolymarketSnapshot]:
    """Search markets via Polymarket's /public-search endpoint.

    IMPORTANT — why this exists alongside ``search_markets``:

    The ``/markets?search=<q>`` parameter that ``search_markets`` uses
    is silently ignored by Gamma — every query returns the same
    "default trending" list. The real search endpoint is
    ``/public-search?q=<q>``, which returns events:

        {
          "events": [
            {
              "title": "Presidential Election Winner 2028",
              "slug": "presidential-election-winner-2028",
              "markets": [
                {"question": "Will Donald Trump win...?", "outcomes": ...},
                {"question": "Will Eric Trump win...?", "outcomes": ...},
                ...
              ],
              ...
            },
            ...
          ]
        }

    This function flattens events[*].markets[*] into a single
    list of ``PolymarketSnapshot``, filtered to active open binary
    YES/NO markets, ranked by ``volume24hr`` desc (busiest first).
    Used by the LLM contract matcher in ``parsing/polymarket_match.py``.

    ``search_markets`` is left alone (its tests + the existing
    Tier-3 cross-check assert the old /markets?search= shape; a
    follow-up should migrate that path too).
    """
    q = (query or "").strip()
    if not q:
        return []
    params = {"q": q, "limit_per_type": max(1, limit * 2)}
    try:
        async with _client() as client:
            resp = await client.get(
                f"{_BASE_URL}/public-search", params=params,
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "[news_events.polymarket] public-search network error "
            "query=%r err=%s",
            query, exc,
        )
        return []
    if resp.status_code != 200:
        logger.info(
            "[news_events.polymarket] public-search status=%s query=%r",
            resp.status_code, query,
        )
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    events = data.get("events")
    if not isinstance(events, list):
        return []

    candidates: list[tuple[float, PolymarketSnapshot]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        markets = ev.get("markets") or []
        if not isinstance(markets, list):
            continue
        for m in markets:
            if not isinstance(m, dict):
                continue
            # Filter to active, open, non-archived binary markets.
            if not m.get("active") or m.get("closed") or m.get("archived"):
                continue
            snap = _snapshot_from_payload(m)
            if snap is None:
                continue
            try:
                vol = float(m.get("volume24hr") or 0.0)
            except (TypeError, ValueError):
                vol = 0.0
            candidates.append((vol, snap))

    # Rank by volume desc — busiest matching markets surface first.
    candidates.sort(key=lambda t: t[0], reverse=True)
    return [snap for _vol, snap in candidates[:limit]]


async def browse_events(
    topic: Optional[str] = None,
    *,
    limit: int = 10,
    markets_per_event: int = 3,
) -> list[dict]:
    """Browse open events on Polymarket — catalog discovery for the
    chat browse tool.

    Two modes:
      - topic given: hits ``/public-search?q=topic`` and flattens the
        returned events. Results are already keyword-ranked by Gamma.
      - topic empty: hits ``/events?closed=false&active=true&
        order=volume24hr`` for the busiest live events overall.

    Returns a list of event dicts shaped for chat rendering:
        {
          "title": "...",
          "slug": "...",
          "end_date": "2026-12-31T00:00:00Z" | None,
          "volume_24h": float,
          "tags": ["Bitcoin", "Crypto", ...],
          "markets": [
            {
              "market_id": "...",
              "question": "Will Bitcoin hit $150k by Dec 31, 2026?",
              "yes_price": 0.014,
              "yes_token_id": "...",
              "no_token_id": "...",
              "volume_24h": float,
            },
            ...
          ]
        }

    Never raises — network or parse failures return ``[]`` so the
    chat tool degrades cleanly to "no markets right now".
    """
    topic = (topic or "").strip()
    try:
        async with _client() as client:
            if topic:
                resp = await client.get(
                    f"{_BASE_URL}/public-search",
                    params={"q": topic, "limit_per_type": max(1, limit * 2)},
                )
            else:
                resp = await client.get(
                    f"{_BASE_URL}/events",
                    params={
                        "closed": "false",
                        "active": "true",
                        "limit": max(1, limit),
                        "order": "volume24hr",
                        "ascending": "false",
                    },
                )
    except httpx.HTTPError as exc:
        logger.warning(
            "[news_events.polymarket] browse network error topic=%r err=%s",
            topic, exc,
        )
        return []
    if resp.status_code != 200:
        logger.info(
            "[news_events.polymarket] browse status=%s topic=%r",
            resp.status_code, topic,
        )
        return []
    try:
        data = resp.json()
    except ValueError:
        return []

    # /public-search wraps under "events"; /events returns a bare list.
    raw_events: list
    if isinstance(data, dict):
        raw_events = data.get("events") or []
    elif isinstance(data, list):
        raw_events = data
    else:
        return []

    out: list[dict] = []
    for ev in raw_events:
        if not isinstance(ev, dict):
            continue
        # Drop closed / archived at the event level — Gamma sometimes
        # returns closed events under topic search.
        if ev.get("closed") or ev.get("archived"):
            continue
        try:
            vol = float(ev.get("volume24hr") or 0.0)
        except (TypeError, ValueError):
            vol = 0.0
        tags_raw = ev.get("tags") or []
        tag_labels: list[str] = []
        for t in tags_raw:
            if isinstance(t, dict) and t.get("label"):
                tag_labels.append(str(t["label"]))
            elif isinstance(t, str):
                tag_labels.append(t)
        markets_view: list[dict] = []
        for m in (ev.get("markets") or []):
            if not isinstance(m, dict):
                continue
            if not m.get("active") or m.get("closed") or m.get("archived"):
                continue
            snap = _snapshot_from_payload(m)
            if snap is None:
                continue
            # Pull token ids from raw outcomes/clobTokenIds parallel arrays.
            yes_tok, no_tok = _yes_no_token_ids(m)
            try:
                m_vol = float(m.get("volume24hr") or 0.0)
            except (TypeError, ValueError):
                m_vol = 0.0
            markets_view.append({
                "market_id": snap.market_id,
                "question": snap.question,
                "yes_price": snap.yes_price,
                "yes_token_id": yes_tok,
                "no_token_id": no_tok,
                "volume_24h": m_vol,
            })
        if not markets_view:
            continue
        # Sort markets within the event by their own volume, then keep top N.
        markets_view.sort(key=lambda x: x["volume_24h"], reverse=True)
        markets_view = markets_view[:markets_per_event]
        out.append({
            "title": ev.get("title"),
            "slug": ev.get("slug"),
            "end_date": ev.get("endDate"),
            "volume_24h": vol,
            "tags": tag_labels[:4],
            "markets": markets_view,
        })
        if len(out) >= limit:
            break
    return out


def _yes_no_token_ids(market: dict) -> tuple[Optional[str], Optional[str]]:
    """Pull (yes_token_id, no_token_id) out of a raw Gamma market dict.
    Mirrors the parsing in parsing/polymarket_match._extract_token_ids
    but operates on the raw market dict directly so browse_events
    doesn't need to round-trip through PolymarketSnapshot.raw."""
    import json as _json
    outcomes = market.get("outcomes")
    tokens = market.get("clobTokenIds")
    if isinstance(outcomes, str):
        try:
            outcomes = _json.loads(outcomes)
        except _json.JSONDecodeError:
            outcomes = None
    if isinstance(tokens, str):
        try:
            tokens = _json.loads(tokens)
        except _json.JSONDecodeError:
            tokens = None
    if not (isinstance(outcomes, list) and isinstance(tokens, list)
            and len(outcomes) == len(tokens)):
        return None, None
    yes_tok = no_tok = None
    for label, tok in zip(outcomes, tokens):
        norm = str(label).strip().lower()
        if norm in {"yes", "true"} and yes_tok is None:
            yes_tok = str(tok)
        elif norm in {"no", "false"} and no_tok is None:
            no_tok = str(tok)
    return yes_tok, no_tok


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
