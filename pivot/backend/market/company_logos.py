"""Company logo resolution (logo.dev), keyed by domain.

Resolution order for a user symbol/ticker:
  1. Curated override (``logo_domain_overrides.json``) — a hand/script-built
     symbol→correct-domain map that corrects the symbols whose guessed domain
     points at the wrong brand. Highest priority.
  2. Derived from ``enrich.company_profile.website`` — extract the bare
     domain and build the img.logo.dev URL on the fly. The real website
     domain returns the correct logo or a neutral monogram, never a
     different company.
  3. ``mc.companies.logo_url`` — the precomputed img.logo.dev URL (2.8k+
     companies), whose domain was guessed from the name. Last resort: it is
     correct for most large caps and the override map (step 1) fixes the
     known-wrong ones, so it beats a monogram where the enrichment DB has
     no website row.
  4. ``None`` — the frontend falls back to a first-letter monogram.

Everything fails closed: a disabled / unreachable DB, a missing token, or
any exception returns ``None`` rather than raising, so a logo lookup can
never break a quote response. Results are Redis-cached (logos are stable).

Attribution: img.logo.dev's free tier requires a visible "Logos provided
by Logo.dev" link wherever logos render — the frontend shows it in the
footer (see pivot-next AppFooter). The pk_ token is publishable.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from backend.cache import redis_client
from backend.config import settings

logger = logging.getLogger(__name__)

# Bumped on each resolution-logic change so a week's worth of cached values
# (including *misses* cached as the none-sentinel) is ignored rather than
# served stale: v2 = override layer landed; v3 = precomputed-column fallback
# restored, so symbols that had regressed to a monogram re-resolve at once.
_CACHE_TTL_SECONDS = 7 * 24 * 3600  # logos change rarely; cache a week
_CACHE_PREFIX = "company_logo:v3:"
# Curated symbol→domain corrections, loaded once. See _load_overrides.
_OVERRIDES_PATH = Path(__file__).with_name("logo_domain_overrides.json")
# Sentinel cached for "we looked, found nothing" so a miss doesn't re-hit
# the DBs every quote. Distinct from a real URL.
_NONE_SENTINEL = "\x00none"


def _load_overrides() -> dict[str, str]:
    """Load the curated symbol(UPPER)→bare-domain override map. The naive
    ``<name>.com`` guess baked into ``mc.companies.logo_url`` is wrong for
    many Indian listings (``reliance.com``/``ntpc.com`` resolve to foreign
    brands on logo.dev); these corrections take priority. Fails closed to an
    empty map so a bad/missing file can never break logo resolution."""
    try:
        raw = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[company_logos] overrides load failed err=%s", exc)
        return {}
    out: dict[str, str] = {}
    for sym, dom in raw.items():
        if sym.startswith("_") or not isinstance(dom, str):
            continue  # skip "_comment" and any non-string value
        out[sym.strip().upper()] = dom.strip().lower()
    return out


# Loaded once at import; small file, stable for the process lifetime.
_DOMAIN_OVERRIDES: dict[str, str] = _load_overrides()


def override_logo_url(symbol: str) -> Optional[str]:
    """img.logo.dev URL for a symbol whose domain we've curated, else None.
    Lets list/search callers apply the same correction as get_logo_url
    without paying a DB round-trip for the precomputed (wrong) column."""
    domain = _DOMAIN_OVERRIDES.get((symbol or "").strip().upper())
    return logo_url_for_domain(domain) if domain else None


def logo_url_for_domain(domain: str) -> Optional[str]:
    """Build an img.logo.dev URL for a bare domain (e.g. ``reliance.com``).
    Returns None when no publishable token is configured."""
    token = (settings.logodev_publishable_token or "").strip()
    if not token or not domain:
        return None
    return f"https://img.logo.dev/{domain}?token={token}&size=128&format=png"


def _domain_from_website(website: Optional[str]) -> Optional[str]:
    """Extract a bare registrable-ish domain from a website URL/string.
    ``https://www.reliance.com/about`` -> ``reliance.com``. Best-effort."""
    if not website:
        return None
    raw = website.strip()
    if not raw:
        return None
    if "//" not in raw:
        raw = "//" + raw  # let urlsplit treat it as netloc, not path
    host = (urlsplit(raw).hostname or "").lower().strip()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    # A plausible domain has at least one dot.
    return host if "." in host else None


def get_logo_url(symbol_or_sc_id: str) -> Optional[str]:
    """Resolve a logo URL for a symbol/ticker/sc_id. See module docstring
    for the resolution order. Redis-cached; never raises."""
    key_in = (symbol_or_sc_id or "").strip().upper()
    if not key_in:
        return None

    cache_key = _CACHE_PREFIX + key_in
    try:
        cached = redis_client.get(cache_key)
    except Exception:  # noqa: BLE001
        cached = None
    if cached is not None:
        val = cached.decode() if isinstance(cached, (bytes, bytearray)) else cached
        return None if val == _NONE_SENTINEL else val

    result = _resolve_uncached(key_in)

    try:
        redis_client.set(cache_key, result or _NONE_SENTINEL,
                         ex=_CACHE_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        pass
    return result


def get_logo_urls(symbols: list[str]) -> dict[str, Optional[str]]:
    """Batch logo resolution for a page of symbols in ≤2 round-trips total.

    ``get_logo_url`` resolves ONE name per call and, on a Redis miss, does a
    financials ``get_company`` + an ``enrich_db`` lookup — so a cold page of N
    rows fires ~2·N sequential (remote) DB round-trips, which is the screener
    tab's cold-start cost. This collapses a whole page to: one Redis **MGET**,
    ONE batched ``enrich`` website lookup for the cold misses, one Redis
    **MSET**. Keyed by ``UPPER(symbol)``; an unresolved name maps to ``None``
    (FE monogram), never a guessed/misattributed logo — same honest contract as
    :func:`get_logo_url`. Never raises.

    Note: the cold misses are resolved by **ticker** (the ``get_by_ticker``
    fallback path), skipping the per-name sc_id hop; a name resolvable only via
    sc_id degrades to a monogram rather than a per-row round-trip.
    """
    uniq: list[str] = []
    seen: set[str] = set()
    for s in symbols:
        k = (s or "").strip().upper()
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    if not uniq:
        return {}

    out: dict[str, Optional[str]] = {}

    # 1. One Redis MGET for the whole page (vs one GET per row).
    try:
        cached = redis_client.mget([_CACHE_PREFIX + k for k in uniq])
    except Exception:  # noqa: BLE001
        cached = [None] * len(uniq)
    misses: list[str] = []
    for k, v in zip(uniq, cached):
        if v is None:
            misses.append(k)
            continue
        val = v.decode() if isinstance(v, (bytes, bytearray)) else v
        out[k] = None if val == _NONE_SENTINEL else val

    if not misses:
        return out

    # 2. Resolve the cold misses' websites in ONE batched enrich query.
    websites: dict[str, Optional[str]] = {}
    try:
        from backend.market import enrich_db

        if enrich_db.is_enabled():
            websites = enrich_db.get_websites_by_tickers(misses)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[company_logos] batch website lookup failed: %s", exc)
        websites = {}

    resolved: dict[str, Optional[str]] = {}
    for k in misses:
        domain = _domain_from_website(websites.get(k))
        resolved[k] = logo_url_for_domain(domain) if domain else None
    out.update(resolved)

    # 3. One Redis MSET (pipeline) to warm the cache for next time.
    try:
        pipe = redis_client.pipeline()
        for k, url in resolved.items():
            pipe.set(_CACHE_PREFIX + k, url or _NONE_SENTINEL,
                     ex=_CACHE_TTL_SECONDS)
        pipe.execute()
    except Exception:  # noqa: BLE001
        pass

    return out


def _resolve_uncached(symbol_or_sc_id: str) -> Optional[str]:
    # 0) curated override — the corrected real domain for symbols whose name
    #    resolves to the wrong brand on logo.dev. Highest priority.
    override = override_logo_url(symbol_or_sc_id)
    if override:
        return override

    # Prefer the company's REAL website domain (enrich.company_profile.website).
    # Rationale: img.logo.dev is keyed by domain and NEVER 404s — an unknown
    # domain returns a generated placeholder, and a *wrong* domain that happens
    # to belong to another company returns THAT company's logo. The precomputed
    # mc.companies.logo_url column was built by guessing a domain from the
    # (often abbreviated) name — e.g. Britannia -> bi.com — so it can serve a
    # different company's logo. The real website domain returns the correct
    # logo or a neutral monogram, but never a different company.
    sc_id: Optional[str] = None
    precomputed: Optional[str] = None
    try:
        from backend.market.financials_db import get_company

        company = get_company(symbol_or_sc_id)
        if company is not None:
            sc_id = company.sc_id
            precomputed = getattr(company, "logo_url", None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[company_logos] financials lookup failed sym=%s err=%s",
                     symbol_or_sc_id, exc)

    try:
        from backend.market import enrich_db

        if enrich_db.is_enabled():
            enr = None
            if sc_id:
                enr = enrich_db.get_by_sc_id(sc_id)
            if enr is None:
                enr = enrich_db.get_by_ticker(symbol_or_sc_id)
            domain = _domain_from_website(getattr(enr, "website", None) if enr else None)
            if domain:
                return logo_url_for_domain(domain)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[company_logos] enrich lookup failed sym=%s err=%s",
                     symbol_or_sc_id, exc)

    # Last resort: the precomputed mc.companies.logo_url. Its domain was guessed
    # from the name so it's occasionally the wrong brand — but the curated
    # override map above already corrects the known-wrong symbols, and for the
    # large-cap majority (HDFCBANK -> hdfcbank.com, TCS -> tcs.com, …) the guess
    # is correct. Serving that is better than a monogram for every symbol the
    # enrichment DB has no website row for. Wrong ones → add to the override map.
    return precomputed or None
