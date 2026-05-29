"""Chat-surface read tools for single-symbol analysis intents.

Why this exists
---------------
The chat layer can already build/backtest workflows and fetch live prices,
but reasoning prompts like:

  - "should I buy reliance"
  - "compare wipro vs infy"  (one fetch_fundamentals per leg)
  - "recent news on RELIANCE"

had no plain function surface that returns a *snapshot* of a company's
fundamentals or its latest headlines. Those facts live behind:

  - backend/market/financials_db.py  (Moneycontrol-derived `mc.*` Postgres,
    point-in-time fundamentals via resolve_metric / get_fundamental)
  - yfinance .news                    (already wrapped by backend/routers/news.py)

This module wraps both into two thin, import-safe, sync functions the
orchestrator can dispatch directly. Neither writes anything.

Data reality (probed 2026-05-29 against the live financials DB)
---------------------------------------------------------------
The `mc.statement_lines` table is sparsely populated. resolve_symbol works
for RELIANCE/TCS/INFY/HDFCBANK (SBIN -> None), but only RELIANCE returns a
full ratio set. TCS/INFY/HDFCBANK currently resolve only `eps_basic`; the
rest come back None. We surface None gracefully and let the LLM say "not
available" rather than inventing numbers. See get_symbol_news for the
live-headline path that does have broad coverage.

Style
-----
- Sync SQLAlchemy: a single FinancialsSessionLocal session is opened per
  fetch_fundamentals call and shared across every metric lookup (passed via
  the resolver's `session=` kwarg) so we make one connection, not eight.
- yfinance news is reused via the same dual-shape normaliser logic as
  backend/routers/news.py (flat vs nested-under-`content`).
- 1-hour Redis cache for news (mirrors web_tools._read_cache/_write_cache),
  since the same "news on X" query repeats within a session.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from backend.cache import redis_client
from backend.database import FinancialsSessionLocal
from backend.market import financials_db as fdb


logger = logging.getLogger(__name__)


# Curated metric set surfaced to the chat layer. Keys are the human-facing
# names in the returned dict; values are the `resolve_metric` argument.
# `pe` is synthesised from earnings_yield inside financials_db.resolve_metric.
_METRICS: tuple[tuple[str, str], ...] = (
    ("pe", "pe"),
    ("roe", "roe"),
    ("roce", "roce"),
    ("de", "debt_to_equity"),
    ("npm", "net_profit_margin"),       # net profit margin (%)
    ("eps", "eps_basic"),
    ("book_value", "book_value_per_share"),
    ("dividend_payout", "dividend_payout"),
)

# Pretty labels + units for the human summary line.
_LABELS: dict[str, str] = {
    "pe": "P/E",
    "roe": "ROE",
    "roce": "ROCE",
    "de": "D/E",
    "npm": "net margin",
    "eps": "EPS",
    "book_value": "book value",
    "dividend_payout": "payout",
}
_PCT_FIELDS = {"roe", "roce", "npm", "dividend_payout"}


_NEWS_CACHE_PREFIX = "symbol_news:"
_NEWS_CACHE_TTL_S = 60 * 60  # 1 hour


# ── Fundamentals ──────────────────────────────────────────────────────────


def fetch_fundamentals(symbol: str, *, basis: str = "consolidated") -> dict:
    """Snapshot of curated fundamentals for a single symbol.

    Resolves the symbol to an `sc_id` then pulls each metric in `_METRICS`
    point-in-time-latest (as_of_date=None) from the Moneycontrol DB. Any
    metric the DB lacks comes back as None — never fabricated.

    Returns
    -------
    {
      "symbol": "<UPPER input>",
      "resolved": bool,            # did the symbol map to an sc_id?
      "sc_id": str | None,
      "name": str | None,          # company display name when resolvable
      "sector": str | None,
      "basis": "consolidated" | "standalone",
      "pe": float | None, "roe": float | None, "roce": float | None,
      "de": float | None, "npm": float | None, "eps": float | None,
      "book_value": float | None, "dividend_payout": float | None,
      "available": int,            # count of non-None metrics
      "summary": str,              # one-line human-readable digest
      "note": str | None,          # set when data is missing/sparse
    }
    """
    sym = (symbol or "").strip().upper()
    out: dict[str, Any] = {
        "symbol": sym,
        "resolved": False,
        "sc_id": None,
        "name": None,
        "sector": None,
        "basis": basis,
    }
    for key, _ in _METRICS:
        out[key] = None
    out["available"] = 0

    if not sym:
        out["summary"] = "No symbol supplied."
        out["note"] = "empty symbol"
        return out

    session = FinancialsSessionLocal()
    try:
        company = fdb.get_company(sym, session=session)
        if company is None:
            out["summary"] = (
                f"{sym}: not found in the fundamentals database "
                f"(no sc_id mapping)."
            )
            out["note"] = "symbol did not resolve to an sc_id"
            return out

        out["resolved"] = True
        out["sc_id"] = company.sc_id
        out["name"] = company.name
        out["sector"] = company.sector

        available = 0
        for key, metric in _METRICS:
            try:
                val = fdb.resolve_metric(
                    company.sc_id, metric, basis=basis, session=session
                )
            except Exception as e:  # noqa: BLE001 — never let one bad metric kill the snapshot
                logger.debug("resolve_metric(%s,%s) failed: %s", sym, metric, e)
                val = None
            if val is not None:
                val = round(float(val), 2)
                available += 1
            out[key] = val
        out["available"] = available

        out["summary"] = _summarise(out)
        if available == 0:
            out["note"] = (
                "Symbol resolved but no fundamental metrics are populated "
                "for it in the Moneycontrol DB (sparse coverage)."
            )
        elif available < len(_METRICS):
            out["note"] = (
                f"Partial data: {available}/{len(_METRICS)} metrics "
                f"available; the rest are not populated for this symbol."
            )
        return out
    finally:
        session.close()


def _summarise(d: dict) -> str:
    """One-line human digest of the populated metrics."""
    name = d.get("name") or d.get("symbol")
    parts: list[str] = []
    for key, _ in _METRICS:
        v = d.get(key)
        if v is None:
            continue
        label = _LABELS[key]
        if key in _PCT_FIELDS:
            parts.append(f"{label} {v}%")
        else:
            parts.append(f"{label} {v}")
    if not parts:
        return f"{name}: no fundamental metrics available."
    return f"{name}: " + ", ".join(parts) + "."


# ── News ──────────────────────────────────────────────────────────────────


def _news_cache_key(symbol: str, limit: int) -> str:
    h = hashlib.md5(f"{symbol}|{limit}".encode("utf-8")).hexdigest()
    return f"{_NEWS_CACHE_PREFIX}{h}"


def _read_cache(key: str) -> dict | None:
    try:
        raw = redis_client.get(key)
    except Exception:  # noqa: BLE001
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw if isinstance(raw, str) else raw.decode())
    except (TypeError, json.JSONDecodeError):
        return None


def _write_cache(key: str, value: dict) -> None:
    try:
        redis_client.set(key, json.dumps(value), ex=_NEWS_CACHE_TTL_S)
    except Exception as e:  # noqa: BLE001
        logger.debug("symbol_news cache write failed: %s", e)


def _normalize_article(item: dict[str, Any]) -> dict | None:
    """yfinance news item -> {title, publisher, link, published}.

    yfinance has shipped two payload shapes; we tolerate both, mirroring
    backend/routers/news.py._normalize:
      - flat:   {title, publisher, link, providerPublishTime}
      - nested: {content: {title, provider:{displayName},
                           clickThroughUrl:{url}, pubDate}}
    `published` is an ISO-8601 string (UTC) or None.
    """
    if not isinstance(item, dict):
        return None

    content = item.get("content")
    if isinstance(content, dict):
        title = str(content.get("title") or "").strip()
        if not title:
            return None
        publisher = None
        prov = content.get("provider")
        if isinstance(prov, dict):
            publisher = prov.get("displayName")
        url = None
        click = content.get("clickThroughUrl")
        if isinstance(click, dict):
            url = click.get("url")
        if not url:
            canon = content.get("canonicalUrl")
            url = canon.get("url") if isinstance(canon, dict) else canon
        published = None
        pub = content.get("pubDate")
        if isinstance(pub, str):
            try:
                published = (
                    datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    .astimezone(timezone.utc)
                    .isoformat()
                )
            except ValueError:
                published = pub or None
        return {
            "title": title,
            "publisher": str(publisher) if publisher else None,
            "link": str(url) if url else None,
            "published": published,
        }

    # Flat shape.
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    published = None
    ts = item.get("providerPublishTime")
    if isinstance(ts, (int, float)):
        published = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    return {
        "title": title,
        "publisher": item.get("publisher") or None,
        "link": item.get("link") or None,
        "published": published,
    }


def get_symbol_news(symbol: str, limit: int = 5, *, exchange: str = "NSE") -> dict:
    """Recent news headlines for a symbol via yfinance.

    Mirrors backend/routers/news.py's ticker-suffix + dual-shape handling,
    but as a plain dict-returning function for the chat orchestrator.
    1-hour Redis cache keyed by (symbol, limit).

    Returns
    -------
    {
      "symbol": "<UPPER input>",
      "articles": [{title, publisher, link, published}, ...],
      "count": int,
      "cached": bool,
      "note": str | None,   # set when the feed is empty or errored
    }
    """
    sym = (symbol or "").strip().upper()
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 5
    lim = max(1, min(lim, 50))

    if not sym:
        return {
            "symbol": sym, "articles": [], "count": 0,
            "cached": False, "note": "empty symbol",
        }

    key = _news_cache_key(sym, lim)
    cached = _read_cache(key)
    if cached is not None:
        return {**cached, "cached": True}

    suffix = ".NS" if exchange == "NSE" else ".BO"
    yf_sym = sym if sym.endswith((".NS", ".BO")) else f"{sym}{suffix}"

    try:
        import yfinance as yf  # type: ignore[import-untyped]
        raw = yf.Ticker(yf_sym).news or []
    except Exception as e:  # noqa: BLE001
        return {
            "symbol": sym, "articles": [], "count": 0, "cached": False,
            "note": f"yfinance news lookup failed for {sym}: {str(e)[:160]}",
        }

    articles: list[dict] = []
    for r in raw:
        art = _normalize_article(r)
        if art is not None:
            articles.append(art)
        if len(articles) >= lim:
            break

    body: dict[str, Any] = {
        "symbol": sym,
        "articles": articles,
        "count": len(articles),
    }
    if not articles:
        body["note"] = (
            f"No news returned for {sym} (yfinance feed empty — may be "
            f"network-restricted in this environment, or the ticker has "
            f"no recent coverage)."
        )
    else:
        # Only cache non-empty results so a transient empty feed isn't pinned.
        _write_cache(key, body)
    body["cached"] = False
    return body
