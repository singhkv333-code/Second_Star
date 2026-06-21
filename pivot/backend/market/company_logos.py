"""Company logo resolution (logo.dev), keyed by domain.

Resolution order for a user symbol/ticker:
  1. ``mc.companies.logo_url`` — the precomputed img.logo.dev URL the
     enrichment job already wrote (2.8k+ companies). Authoritative.
  2. Derived from ``enrich.company_profile.website`` — extract the bare
     domain and build the img.logo.dev URL on the fly. This lifts coverage
     to every company that has a website in the enrichment DB (~5k) even
     where the precomputed column is null.
  3. ``None`` — the frontend falls back to a first-letter monogram.

Everything fails closed: a disabled / unreachable DB, a missing token, or
any exception returns ``None`` rather than raising, so a logo lookup can
never break a quote response. Results are Redis-cached (logos are stable).

Attribution: img.logo.dev's free tier requires a visible "Logos provided
by Logo.dev" link wherever logos render — the frontend shows it in the
footer (see pivot-next AppFooter). The pk_ token is publishable.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlsplit

from backend.cache import redis_client
from backend.config import settings

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 7 * 24 * 3600  # logos change rarely; cache a week
_CACHE_PREFIX = "company_logo:"
# Sentinel cached for "we looked, found nothing" so a miss doesn't re-hit
# the DBs every quote. Distinct from a real URL.
_NONE_SENTINEL = "\x00none"


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


def _resolve_uncached(symbol_or_sc_id: str) -> Optional[str]:
    sc_id: Optional[str] = None
    # 1) precomputed mc.companies.logo_url (also gives us the sc_id for step 2)
    try:
        from backend.market.financials_db import get_company

        company = get_company(symbol_or_sc_id)
        if company is not None:
            sc_id = company.sc_id
            logo = getattr(company, "logo_url", None)
            if logo:
                return logo
    except Exception as exc:  # noqa: BLE001
        logger.debug("[company_logos] financials lookup failed sym=%s err=%s",
                     symbol_or_sc_id, exc)

    # 2) derive from the enrichment DB's website column
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

    return None
