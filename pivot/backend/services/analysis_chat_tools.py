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
from backend.market import enrich_db


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

_YF_FUND_CACHE_PREFIX = "fund_yf:"
_YF_FUND_CACHE_TTL_S = 60 * 60 * 12  # 12h — fundamentals move at most quarterly


def _yfinance_fundamentals(symbol: str) -> dict[str, Any]:
    """Fallback fundamentals from yfinance `.info` for the (many) large/
    mid caps the Moneycontrol DB leaves sparse. Returns values already
    converted to this module's unit conventions (roe/npm/payout as %, de
    as a ratio); {} on any error. Cached 12h (the `.info` call is slow).
    ROCE is not exposed by yfinance — left to the DB.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return {}
    ckey = _YF_FUND_CACHE_PREFIX + sym
    try:
        raw = redis_client.get(ckey)
        if raw:
            return json.loads(raw)
    except Exception:  # noqa: BLE001
        pass
    out: dict[str, Any] = {}
    try:
        import yfinance as yf
        from backend.market.yfinance_service import resolve_symbol
        yf_sym = resolve_symbol(sym)
        if not yf_sym.endswith(".NS") and not yf_sym.startswith("^"):
            yf_sym = f"{sym}.NS"
        info = yf.Ticker(yf_sym).info or {}

        def _f(v):
            try:
                return round(float(v), 2) if v is not None else None
            except (TypeError, ValueError):
                return None

        pe = _f(info.get("trailingPE"))
        roe = info.get("returnOnEquity")
        npm = info.get("profitMargins")
        payout = info.get("payoutRatio")
        de = info.get("debtToEquity")
        out = {
            "pe": pe,
            "roe": round(roe * 100, 2) if isinstance(roe, (int, float)) else None,
            "npm": round(npm * 100, 2) if isinstance(npm, (int, float)) else None,
            "dividend_payout": round(payout * 100, 2) if isinstance(payout, (int, float)) else None,
            # yfinance reports debtToEquity as a percentage (e.g. 36.65) →
            # our `de` is a ratio (0.37).
            "de": round(de / 100.0, 2) if isinstance(de, (int, float)) else None,
            "eps": _f(info.get("trailingEps")),
            "book_value": _f(info.get("bookValue")),
            # Bonus context the LLM can use; not in _METRICS.
            "roa": round(info.get("returnOnAssets") * 100, 2)
                   if isinstance(info.get("returnOnAssets"), (int, float)) else None,
            "dividend_yield": _f(info.get("dividendYield")),
            "pb": _f(info.get("priceToBook")),
        }
        out = {k: v for k, v in out.items() if v is not None}
    except Exception as e:  # noqa: BLE001
        logger.info("yfinance fundamentals fallback failed for %s: %s", sym, str(e)[:120])
        return {}
    try:
        redis_client.set(ckey, json.dumps(out), ex=_YF_FUND_CACHE_TTL_S)
    except Exception:  # noqa: BLE001
        pass
    return out


_YF_PROFILE_CACHE_PREFIX = "profile_yf:"
_YF_PROFILE_CACHE_TTL_S = 60 * 60 * 24  # 24h — profile/sector move very rarely


def _yfinance_profile(symbol: str) -> dict[str, Any]:
    """Fallback company profile from yfinance `.info` for names absent from
    the enrich DB (e.g. listed large-caps whose ticker is NULL in
    mc.companies, like ITC). Returns the same field shape `_apply_enrichment`
    consumes (sector/industry/business_summary/promoter_holding_pct/...), or
    {} on any error. Cached 24h. promoter_holding_pct is the heldPercentInsiders
    proxy, consistent with the enrich DB.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return {}
    ckey = _YF_PROFILE_CACHE_PREFIX + sym
    try:
        raw = redis_client.get(ckey)
        if raw:
            return json.loads(raw)
    except Exception:  # noqa: BLE001
        pass
    out: dict[str, Any] = {}
    try:
        import yfinance as yf
        from backend.market.yfinance_service import resolve_symbol
        yf_sym = resolve_symbol(sym)
        if not yf_sym.endswith(".NS") and not yf_sym.startswith("^"):
            yf_sym = f"{sym}.NS"
        info = yf.Ticker(yf_sym).info or {}
        ins = info.get("heldPercentInsiders")
        inst = info.get("heldPercentInstitutions")
        out = {
            "sector": info.get("sectorDisp") or info.get("sector"),
            "industry": info.get("industryDisp") or info.get("industry"),
            "business_summary": info.get("longBusinessSummary"),
            "website": info.get("website"),
            "employees": info.get("fullTimeEmployees"),
            "long_name": info.get("longName") or info.get("shortName"),
            "promoter_holding_pct": round(ins * 100, 2) if isinstance(ins, (int, float)) else None,
            "institution_holding_pct": round(inst * 100, 2) if isinstance(inst, (int, float)) else None,
        }
        out = {k: v for k, v in out.items() if v is not None}
    except Exception as e:  # noqa: BLE001
        logger.info("yfinance profile fallback failed for %s: %s", sym, str(e)[:120])
        return {}
    try:
        redis_client.set(ckey, json.dumps(out), ex=_YF_PROFILE_CACHE_TTL_S)
    except Exception:  # noqa: BLE001
        pass
    return out


_NEWS_CACHE_PREFIX = "symbol_news:"
_NEWS_CACHE_TTL_S = 60 * 60  # 1 hour


# ── Fundamentals ──────────────────────────────────────────────────────────


# Internal plumbing keys that must never reach the LLM / user. fetch_fundamentals
# carries these for routing/provenance; the tool boundary strips them.
_INTERNAL_FUND_KEYS = {
    "sc_id", "enriched", "enrichment_source", "source", "resolved", "basis",
}
_SUMMARY_MAX = 480


def public_fundamentals_view(d: dict[str, Any]) -> dict[str, Any]:
    """LLM/user-facing projection of a fetch_fundamentals dict: drop internal
    identifiers and clip the business summary so it stays a tight 2-3 lines."""
    if not isinstance(d, dict):
        return d
    out = {k: v for k, v in d.items() if k not in _INTERNAL_FUND_KEYS}
    bs = out.get("business_summary")
    if isinstance(bs, str) and len(bs) > _SUMMARY_MAX:
        cut = bs[:_SUMMARY_MAX].rsplit(" ", 1)[0].rstrip(" ,;.")
        out["business_summary"] = cut + "…"
    return out


def _apply_enrichment(out: dict[str, Any]) -> None:
    """Merge yfinance-derived company profile into a fundamentals snapshot.

    Adds sector/industry, a business-summary profile, and a promoter-holding
    proxy from the `pivot_enrich` DB (see backend/market/enrich_db.py). The
    Moneycontrol `mc.companies.sector` column is empty in practice, so this is
    where `sector` actually gets populated for the chat layer.

    Additive and best-effort: never raises. Prefers the enrich DB; falls back
    to a live cached yfinance profile for names absent from it. Existing
    (DB-sourced) values win.
    """
    try:
        rec = None
        if enrich_db.is_enabled():
            if out.get("sc_id"):
                rec = enrich_db.get_by_sc_id(out["sc_id"])
            if rec is None and out.get("symbol"):
                rec = enrich_db.get_by_ticker(out["symbol"])
        if rec is None:
            # Not in the enrich DB (e.g. ticker is NULL in mc.companies, like
            # ITC). Fall back to a live, cached yfinance profile so listed
            # names still get sector/profile/promoter.
            prof = _yfinance_profile(out.get("symbol", ""))
            if prof:
                if not out.get("sector") and prof.get("sector"):
                    out["sector"] = prof["sector"]
                if prof.get("industry"):
                    out["industry"] = prof["industry"]
                if prof.get("business_summary"):
                    out["business_summary"] = prof["business_summary"]
                if prof.get("website"):
                    out["website"] = prof["website"]
                if prof.get("employees") is not None:
                    out["employees"] = prof["employees"]
                if prof.get("promoter_holding_pct") is not None:
                    out["promoter_holding_pct"] = prof["promoter_holding_pct"]
                if prof.get("institution_holding_pct") is not None:
                    out["institution_holding_pct"] = prof["institution_holding_pct"]
                if prof.get("long_name"):
                    out["long_name"] = prof["long_name"]
                    if not out.get("name"):
                        out["name"] = prof["long_name"]
                out["enriched"] = True
                out["enrichment_source"] = "yfinance_live"
            return
        out["enrichment_source"] = "enrich_db"
        if not out.get("sector") and rec.sector:
            out["sector"] = rec.sector
        if rec.industry:
            out["industry"] = rec.industry
        if rec.long_business_summary:
            out["business_summary"] = rec.long_business_summary
        if rec.website:
            out["website"] = rec.website
        if rec.full_time_employees is not None:
            out["employees"] = rec.full_time_employees
        if rec.promoter_holding_pct is not None:
            # Proxy for SEBI promoter holding (yfinance heldPercentInsiders).
            out["promoter_holding_pct"] = rec.promoter_holding_pct
        if rec.institution_holding_pct is not None:
            out["institution_holding_pct"] = rec.institution_holding_pct
        if not out.get("name") and (rec.long_name or rec.company_name):
            out["name"] = rec.long_name or rec.company_name
        # Moneycontrol display names are often truncated ("Asia Pack",
        # "Reliance"); keep the clean yfinance long name for the digest.
        if rec.long_name:
            out["long_name"] = rec.long_name
        out["enriched"] = True
    except Exception as e:  # noqa: BLE001 — enrichment must never break analysis
        logger.debug("enrichment merge failed for %s: %s", out.get("symbol"), e)


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
            # Not in the Moneycontrol DB — still try yfinance before giving up.
            yf_fund = _yfinance_fundamentals(sym)
            if yf_fund:
                for key, _ in _METRICS:
                    if yf_fund.get(key) is not None:
                        out[key] = yf_fund[key]
                for bonus in ("roa", "dividend_yield", "pb"):
                    if yf_fund.get(bonus) is not None:
                        out[bonus] = yf_fund[bonus]
                out["available"] = sum(1 for k, _ in _METRICS if out.get(k) is not None)
                out["source"] = "yfinance"
                _apply_enrichment(out)
                out["summary"] = _summarise(out)
                out["note"] = (
                    "Not in the fundamentals DB; metrics sourced from yfinance."
                )
                return out
            # Not in mc.companies and no yfinance fundamentals — the profile DB
            # may still cover it (sector/profile/promoter), so try before giving up.
            _apply_enrichment(out)
            if out.get("enriched"):
                out["summary"] = _summarise(out)
                out["note"] = (
                    "No fundamental metrics; company profile sourced from the "
                    "enrichment DB (yfinance)."
                )
                return out
            out["summary"] = (
                f"{sym}: not found in the fundamentals database or yfinance."
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

        # ── yfinance fallback ──────────────────────────────────────────
        # The Moneycontrol DB is sparse for many large/mid caps (HDFCBANK
        # came back with only EPS). Fill any still-missing metric from
        # yfinance `.info` so the chat analysis isn't hamstrung by
        # "PE/ROE unavailable". DB values win (point-in-time correct);
        # yfinance only fills the gaps.
        filled_from_yf = 0
        if available < len(_METRICS):
            yf_fund = _yfinance_fundamentals(sym)
            for key, _ in _METRICS:
                if out.get(key) is None and yf_fund.get(key) is not None:
                    out[key] = yf_fund[key]
                    filled_from_yf += 1
            # Bonus context fields (not in _METRICS) when available.
            for bonus in ("roa", "dividend_yield", "pb"):
                if yf_fund.get(bonus) is not None:
                    out[bonus] = yf_fund[bonus]
            available = sum(1 for k, _ in _METRICS if out.get(k) is not None)
            out["available"] = available

        out["source"] = (
            "moneycontrol+yfinance" if filled_from_yf else "moneycontrol"
        )
        # Sector/industry/profile/promoter-holding from the enrichment DB.
        # (mc.companies.sector is empty, so this is where sector comes from.)
        _apply_enrichment(out)
        out["summary"] = _summarise(out)
        if available == 0:
            out["note"] = (
                "No fundamental metrics available for this symbol from "
                "either the Moneycontrol DB or yfinance."
            )
        elif available < len(_METRICS):
            missing = [k for k, _ in _METRICS if out.get(k) is None]
            out["note"] = (
                f"Partial data: {available}/{len(_METRICS)} metrics "
                f"available ({'incl. yfinance fallback' if filled_from_yf else 'DB only'}); "
                f"unavailable: {', '.join(missing)}."
            )
        return out
    finally:
        session.close()


def _summarise(d: dict) -> str:
    """One-line human digest of the populated metrics."""
    name = d.get("long_name") or d.get("name") or d.get("symbol")
    # Profile context (from the enrichment DB) leads the digest when present.
    ctx: list[str] = []
    if d.get("sector"):
        sec = d["sector"]
        if d.get("industry") and d["industry"] != sec:
            sec = f"{sec} / {d['industry']}"
        ctx.append(sec)
    if d.get("promoter_holding_pct") is not None:
        ctx.append(f"promoter ~{d['promoter_holding_pct']}%")
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
    prefix = f"{name}"
    if ctx:
        prefix = f"{name} ({'; '.join(ctx)})"
    if not parts:
        if ctx:
            return f"{prefix}: no fundamental metrics available."
        return f"{name}: no fundamental metrics available."
    return f"{prefix}: " + ", ".join(parts) + "."


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
