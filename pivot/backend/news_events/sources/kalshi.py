"""Kalshi public market-data REST client.

No auth required for market-data reads (prices, market details, events,
series). Mirrors the narrow, never-raise surface of
``backend/news_events/sources/polymarket.py`` so the matcher and the
Slice-5 REST poll worker swap in cleanly.

Kalshi differs from Polymarket in three ways we paper over here:

  1. **One ticker per binary market** (no per-side CLOB token ids). We
     synthesize a per-side ``asset_id = f"{ticker}:{side}"`` so the
     venue-agnostic evaluator gets a unique key per (market, side), just
     like Polymarket's two token ids. ``kalshi_asset_id`` /
     ``split_kalshi_asset_id`` build and unpack it.
  2. **Prices in integer cents (0–100)**, plus newer parallel
     ``*_dollars`` decimal-string fields. ``_cents`` reads the int field
     first and falls back to ``round(float(<f>_dollars) * 100)``. YES
     probability is the mid of yes_bid/yes_ask (or yes_ask, or last_price)
     divided by 100 and clamped to [0, 1].
  3. **No server-side free-text search.** Discovery flattens
     ``GET /events?with_nested_markets=true`` and filters client-side on
     title/subtitle — analogous to Polymarket's /public-search flatten.

Endpoint shape (verified June 2026):
  GET {base}/markets/{ticker}          -> {"market": {...}}
  GET {base}/markets?tickers=A,B,C     -> {"markets": [...], "cursor": ...}
  GET {base}/events?with_nested_markets=true&status=open -> {"events": [...]}

Never raises — every error path returns ``None`` / ``[]``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)


_TIMEOUT_SECONDS = 12.0
# Statuses that mean "still trading" (not closed/settled). Kept permissive
# across Kalshi's payload-version vocabularies.
_OPEN_STATUSES = frozenset({"open", "active", "unopened"})
_SETTLED_STATUSES = frozenset({"settled", "finalized", "determined"})


@dataclass(frozen=True)
class KalshiSnapshot:
    """One point-in-time read of a Kalshi binary market.

    ``market_id`` is the Kalshi ``ticker`` (the opaque market id). Shape
    parallels ``PolymarketSnapshot`` so the matcher + worker are
    venue-agnostic.
    """

    market_id: str            # == ticker
    slug: Optional[str]       # == event_ticker (closest analog)
    question: Optional[str]   # == title
    yes_price: float          # 0..1, clamped
    closed: bool
    raw: dict

    @property
    def status(self) -> str:
        return str(self.raw.get("status", "")).strip().lower()

    @property
    def result(self) -> str:
        return str(self.raw.get("result", "")).strip().lower()

    @property
    def settled(self) -> bool:
        return self.status in _SETTLED_STATUSES and self.result in {"yes", "no"}


def _base_url() -> str:
    return str(
        getattr(settings, "kalshi_api_base_url",
                "https://api.elections.kalshi.com/trade-api/v2")
    ).rstrip("/")


def _client() -> httpx.AsyncClient:
    headers = {
        "User-Agent": settings.news_events_user_agent,
        "Accept": "application/json",
    }
    return httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, headers=headers)


# ── asset_id synthesis (Kalshi has one ticker, not per-side tokens) ──


def kalshi_asset_id(ticker: str, side: str) -> str:
    """Build the per-side asset id the evaluator subscribes on."""
    return f"{ticker}:{str(side or 'YES').upper()}"


def split_kalshi_asset_id(asset_id: str) -> tuple[str, str]:
    """Inverse of ``kalshi_asset_id``: (ticker, side). Defaults side YES."""
    tk, _, sd = str(asset_id or "").partition(":")
    return tk, (sd or "YES").upper()


# ── price parsing ────────────────────────────────────────────────────


def _cents(market: dict, key: str) -> Optional[float]:
    """Integer-cent field first (0–100); fall back to the newer decimal
    ``<key>_dollars`` string (USD) ×100. None when neither is present."""
    v = market.get(key)
    if isinstance(v, (int, float)):
        return float(v)
    d = market.get(f"{key}_dollars")
    if d is None:
        return None
    try:
        return round(float(d) * 100.0)
    except (TypeError, ValueError):
        return None


def _parse_yes_price(market: dict) -> Optional[float]:
    """YES probability 0..1: mid of yes_bid/yes_ask, then yes_ask, then
    last_price. Clamped to [0, 1]."""
    yb = _cents(market, "yes_bid")
    ya = _cents(market, "yes_ask")
    if yb is not None and ya is not None:
        return max(0.0, min(1.0, ((yb + ya) / 2.0) / 100.0))
    if ya is not None:
        return max(0.0, min(1.0, ya / 100.0))
    lp = _cents(market, "last_price")
    if lp is not None:
        return max(0.0, min(1.0, lp / 100.0))
    return None


def _snapshot_from_payload(market: dict) -> Optional[KalshiSnapshot]:
    if not isinstance(market, dict):
        return None
    ticker = str(market.get("ticker") or "").strip()
    if not ticker:
        return None
    yes = _parse_yes_price(market)
    # A settled market may have no live bid/ask; derive yes from result.
    if yes is None:
        result = str(market.get("result", "")).strip().lower()
        if result == "yes":
            yes = 1.0
        elif result == "no":
            yes = 0.0
        else:
            return None
    status = str(market.get("status", "")).strip().lower()
    return KalshiSnapshot(
        market_id=ticker,
        slug=str(market.get("event_ticker") or "") or None,
        question=str(market.get("title") or "") or None,
        yes_price=max(0.0, min(1.0, yes)),
        closed=status not in _OPEN_STATUSES,
        raw=market,
    )


# ── REST reads ───────────────────────────────────────────────────────


async def get_market(ticker: str) -> Optional[KalshiSnapshot]:
    """Fetch one market by ticker. Returns None on any failure."""
    tk = (ticker or "").strip()
    if not tk:
        return None
    try:
        async with _client() as client:
            resp = await client.get(f"{_base_url()}/markets/{tk}")
    except httpx.HTTPError as exc:
        logger.warning("[news_events.kalshi] get_market network error "
                       "ticker=%r err=%s", tk, exc)
        return None
    if resp.status_code != 200:
        logger.info("[news_events.kalshi] get_market status=%s ticker=%r",
                    resp.status_code, tk)
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    market = data.get("market") if isinstance(data.get("market"), dict) else data
    return _snapshot_from_payload(market)


async def get_markets(tickers: list[str]) -> dict[str, KalshiSnapshot]:
    """Batch-fetch many markets in one call (rate-limit friendly).
    Returns {ticker -> snapshot} for those that parsed. Never raises."""
    tks = sorted({t.strip() for t in (tickers or []) if t and t.strip()})
    if not tks:
        return {}
    if len(tks) > 1000:
        # The endpoint caps at 1000 per call; we don't paginate in beta.
        # Log the truncation rather than silently dropping watched markets.
        logger.warning(
            "[news_events.kalshi] get_markets watching %d tickers > 1000 "
            "page cap; the tail will not be polled this tick", len(tks),
        )
        tks = tks[:1000]
    params = {"tickers": ",".join(tks), "limit": 1000}
    try:
        async with _client() as client:
            resp = await client.get(f"{_base_url()}/markets", params=params)
    except httpx.HTTPError as exc:
        logger.warning("[news_events.kalshi] get_markets network error err=%s", exc)
        return {}
    if resp.status_code == 429:
        # Rate-limited. Return empty → the worker skips this tick and
        # retries after its (≥10s) interval — no hammering, no backoff loop.
        logger.warning("[news_events.kalshi] get_markets rate-limited (429); "
                       "skipping tick")
        return {}
    if resp.status_code != 200:
        logger.info("[news_events.kalshi] get_markets status=%s", resp.status_code)
        return {}
    try:
        data = resp.json()
    except ValueError:
        return {}
    rows = data.get("markets") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    out: dict[str, KalshiSnapshot] = {}
    for m in rows:
        snap = _snapshot_from_payload(m)
        if snap is not None:
            out[snap.market_id] = snap
    return out


async def _fetch_open_events(limit: int) -> list[dict]:
    """GET open events with nested markets. Returns the raw event list."""
    params = {
        "status": "open",
        "with_nested_markets": "true",
        "limit": max(1, min(limit, 200)),
    }
    try:
        async with _client() as client:
            resp = await client.get(f"{_base_url()}/events", params=params)
    except httpx.HTTPError as exc:
        logger.warning("[news_events.kalshi] events network error err=%s", exc)
        return []
    if resp.status_code != 200:
        logger.info("[news_events.kalshi] events status=%s", resp.status_code)
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    events = data.get("events") if isinstance(data, dict) else None
    return events if isinstance(events, list) else []


def _query_tokens(query: str) -> list[str]:
    return [t for t in (query or "").lower().split() if len(t) > 1]


async def search_via_public_search(
    query: str, *, limit: int = 8,
) -> list[KalshiSnapshot]:
    """Best-effort topic search. Kalshi has no server-side free-text
    search, so we flatten open events' nested markets and filter
    client-side on title/subtitle token overlap, ranked by volume desc.

    Mirrors the SHAPE of polymarket.search_via_public_search so the
    matcher's four-tier query chain works unchanged over it.
    """
    q = (query or "").strip()
    if not q:
        return []
    tokens = _query_tokens(q)
    if not tokens:
        return []
    events = await _fetch_open_events(limit=200)

    scored: list[tuple[float, KalshiSnapshot]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        markets = ev.get("markets") or []
        if not isinstance(markets, list):
            continue
        ev_title = str(ev.get("title") or "")
        for m in markets:
            if not isinstance(m, dict):
                continue
            status = str(m.get("status", "")).strip().lower()
            if status not in _OPEN_STATUSES:
                continue
            snap = _snapshot_from_payload(m)
            if snap is None:
                continue
            hay = " ".join([
                ev_title,
                str(m.get("title") or ""),
                str(m.get("subtitle") or m.get("yes_sub_title") or ""),
            ]).lower()
            overlap = sum(1 for t in tokens if t in hay)
            if overlap == 0:
                continue
            try:
                vol = float(m.get("volume") or m.get("volume_24h") or 0.0)
            except (TypeError, ValueError):
                vol = 0.0
            # Rank: token overlap first, then volume.
            scored.append((overlap * 1e9 + vol, snap))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [snap for _s, snap in scored[:limit]]


# Alias so the matcher can call the same name as the Polymarket module.
search_markets = search_via_public_search


async def browse_events(
    topic: Optional[str] = None, *, limit: int = 10,
    markets_per_event: int = 3,
) -> list[dict]:
    """Browse open Kalshi events for the chat browse surface. Same dict
    shape as polymarket.browse_events; token ids are the synthesized
    per-side asset ids. Never raises."""
    events = await _fetch_open_events(limit=max(limit, 40))
    topic_tokens = _query_tokens(topic or "")
    out: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ev_title = str(ev.get("title") or "")
        markets_view: list[dict] = []
        for m in (ev.get("markets") or []):
            if not isinstance(m, dict):
                continue
            if str(m.get("status", "")).strip().lower() not in _OPEN_STATUSES:
                continue
            snap = _snapshot_from_payload(m)
            if snap is None:
                continue
            if topic_tokens:
                hay = (ev_title + " " + (snap.question or "")).lower()
                if not any(t in hay for t in topic_tokens):
                    continue
            try:
                m_vol = float(m.get("volume") or 0.0)
            except (TypeError, ValueError):
                m_vol = 0.0
            markets_view.append({
                "market_id": snap.market_id,
                "question": snap.question,
                "yes_price": snap.yes_price,
                "yes_token_id": kalshi_asset_id(snap.market_id, "YES"),
                "no_token_id": kalshi_asset_id(snap.market_id, "NO"),
                "volume_24h": m_vol,
            })
        if not markets_view:
            continue
        markets_view.sort(key=lambda x: x["volume_24h"], reverse=True)
        out.append({
            "title": ev_title,
            "slug": ev.get("event_ticker"),
            "end_date": ev.get("close_time"),
            "volume_24h": 0.0,
            "tags": [],
            "markets": markets_view[:markets_per_event],
        })
        if len(out) >= limit:
            break
    return out


def resolution_payload(snapshot: KalshiSnapshot) -> Optional[dict]:
    """Build an evaluator on_resolved payload from a settled market.
    None when the market is not (yet) settled. Mirrors the shape of
    Polymarket's market_resolved frame (``winner`` ∈ {YES, NO})."""
    if not snapshot.settled:
        return None
    return {
        "winner": snapshot.result.upper(),  # "YES" | "NO"
        "market": snapshot.market_id,
    }
