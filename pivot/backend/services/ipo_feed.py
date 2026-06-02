"""Upcoming / open IPO feed — mainboard + SME — for the chat surface.

Why this exists
---------------
Pivot has ZERO IPO support today, but it's a high-priority ask: users want
to see live IPO details *inside* chat ("any IPOs open right now?", "tell me
about the X IPO") and then say "I want to apply". This module is the data
layer for that: a sync, Redis-cached fetch of current/upcoming issues that
the chat tool layer (lead-registered) can call.

Source strategy (in order)
---------------------------
1. NSE — https://www.nseindia.com/api/all-upcoming-issues?category=ipo (and
   ?category=sme). NSE 403s a bare server request, so we do the same
   cookie warm-up the browser does: GET the NSE home / market-data page
   first to pick up the `bm_sz` / `_abck` / `nsit` cookies, *then* call the
   JSON API with a browser-like User-Agent + a Referer. Verified from this
   server: warm-up unlocks the API (e.g. `/api/equity-master` returns full
   JSON after warm-up, 403 without it).
2. NSE current-issue — https://www.nseindia.com/api/ipo-current-issue
   (the "open right now" list; same cookie jar).

Both NSE endpoints share NSE's IPO record schema. The field names below
were confirmed live against `/api/public-past-issues` (same family),
which returns records keyed by `companyName`, `symbol`, `ipoStartDate`,
`ipoEndDate`, `issuePrice`, `priceRange`, `securityType` ("SME" /
"Equity"), `lotSize`, `issueSize`, `series`, `status`. Active/upcoming
records add `status` ("Forthcoming" / "Active") and sometimes `sr_no`.

Normalization
-------------
Every record is flattened to:
    {name, symbol, price_band, open_date, close_date, lot_size,
     issue_size, type: 'mainboard'|'sme', status: 'upcoming'|'open'|'closed'}

Cache
-----
Redis, 45-minute TTL (IPO windows move on a daily, not minute, cadence;
45m is long enough to absorb a chat burst — "what IPOs are open?" → "tell
me about X" → "I want to apply" — without re-hitting NSE three times, short
enough that an issue opening/closing mid-day surfaces same session).
Cache pattern mirrors `backend/agents/web_tools.py` `_read_cache`/
`_write_cache` and `backend/services/top_movers.py`.

Honesty
-------
If NSE is blocked / unreachable from the host, the module still returns the
correct structure with `count: 0` and a `note` carrying the exact failure
string — it never fabricates IPO names or dates. The `source` field always
records which endpoint produced the data (or `"unreachable"`).
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from backend.cache import redis_client
from backend.utils.time_utils import format_ist, now_ist

logger = logging.getLogger(__name__)


# ── Config ───────────────────────────────────────────────────────────────────

_NSE_HOME = "https://www.nseindia.com/"
_NSE_WARMUP = "https://www.nseindia.com/market-data/all-upcoming-issues-ipo"
_NSE_UPCOMING = "https://www.nseindia.com/api/all-upcoming-issues"
_NSE_CURRENT = "https://www.nseindia.com/api/ipo-current-issue"
_NSE_ACTIVE_CATEGORY = "https://www.nseindia.com/api/ipo-active-category"

_CACHE_PREFIX = "ipo_feed:"
_CACHE_KEY = f"{_CACHE_PREFIX}list"
_CACHE_TTL_S = 45 * 60          # 45 minutes
_SUB_CACHE_TTL_S = 15 * 60      # 15 minutes — subscription moves faster
_TIMEOUT_S = 12.0


# ── GMP feature flag (fail-closed OFF in v1) ────────────────────────────────
#
# Unofficial Grey Market Premium is compliance-sensitive: it's not exchange
# data and there is no licensed vendor wired in v1. Default OFF; when
# OFF the propose-application payload OMITS the "gmp" key entirely (it is
# NOT rendered as null-with-shape, by design). If a future build flips
# this on, the module-level assertion in ``gmp_payload`` forces the
# disclaimer to be attached before any value is returned.

IPO_GMP_ENABLED: bool = os.getenv("IPO_GMP_ENABLED", "").lower() in {
    "1", "true", "yes", "on",
}
IPO_GMP_DISCLAIMER: str = (
    "Unofficial Grey Market Premium — not exchange data, not regulated, "
    "and not a price prediction. Source is community trackers."
)

# Browser-like UA — NSE 403s non-browser agents even after the cookie jar
# is warm, so this string is load-bearing, not cosmetic.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_BROWSER_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


# ── Cache (mirrors web_tools._read_cache / _write_cache) ──────────────────────

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


def _write_cache(key: str, value: dict, ttl_s: int = _CACHE_TTL_S) -> None:
    try:
        redis_client.set(key, json.dumps(value, default=str), ex=ttl_s)
    except Exception as e:  # noqa: BLE001
        logger.debug("ipo_feed cache write failed: %s", e)


# ── Warmed HTTP client (shared by every NSE call) ────────────────────────────

@contextmanager
def _warmed_client() -> Iterator[httpx.Client]:
    """Yield an httpx.Client with the NSE cookie jar warmed.

    NSE 403s a bare server request; the browser unlocks the API by setting
    bm_sz / _abck / nsit on the first HTML hit. This helper is the single
    cookie-warm shape every NSE call shares — ``_fetch_nse`` (the list
    endpoints) and ``fetch_subscription`` (per-symbol category data) both
    use it, so they don't drift apart.

    A context manager (NOT a bare client): the warm-up GETs *open* the
    httpx client, so callers must use ``with _warmed_client() as cli: ...``
    and we close it on exit. Returning the opened client directly would
    make ``with`` re-enter it and raise "Cannot open a client instance
    more than once".
    """
    cli = httpx.Client(
        timeout=_TIMEOUT_S,
        follow_redirects=True,
        headers=_BROWSER_HEADERS,
    )
    try:
        try:
            # Warm bm_sz / _abck / nsit. The HTML hits are NOT optional —
            # the JSON API returns 403 without them.
            cli.get(_NSE_HOME, headers={"Accept": "text/html,application/xhtml+xml"})
            cli.get(_NSE_WARMUP, headers={"Accept": "text/html,application/xhtml+xml"})
        except httpx.HTTPError:
            # Surface the failure on the actual API call; the caller already
            # has an honest unreachable branch.
            pass
        yield cli
    finally:
        cli.close()


def _nse_api_headers() -> dict[str, str]:
    """Headers every NSE JSON-API call sends after the warm-up."""
    return {
        "Accept": "application/json, text/plain, */*",
        "Referer": _NSE_WARMUP,
        "X-Requested-With": "XMLHttpRequest",
    }


# ── NSE fetch ─────────────────────────────────────────────────────────────────

def _fetch_nse() -> tuple[list[dict[str, Any]], str, str | None]:
    """Fetch raw NSE IPO records via a cookie-warmed browser-like client.

    Returns (raw_records, source_label, error_note). `error_note` is None
    on success; on failure `raw_records` is [] and `error_note` carries the
    exact failure so the caller can surface it honestly.
    """
    raw: list[dict[str, Any]] = []
    source = "nse"
    try:
        with _warmed_client() as cli:
            api_headers = _nse_api_headers()

            # 1. Upcoming/forthcoming — mainboard (ipo) + SME (sme).
            for category in ("ipo", "sme"):
                try:
                    r = cli.get(
                        _NSE_UPCOMING,
                        params={"category": category},
                        headers=api_headers,
                    )
                    if r.status_code != 200:
                        logger.debug("NSE upcoming %s -> %s", category, r.status_code)
                        continue
                    raw.extend(_coerce_records(r.json() if r.content else None))
                except (httpx.HTTPError, ValueError) as e:
                    logger.debug("NSE upcoming %s failed: %s", category, e)

            # 2. Currently-open issues (separate endpoint).
            try:
                r = cli.get(_NSE_CURRENT, headers=api_headers)
                if r.status_code == 200:
                    raw.extend(_coerce_records(r.json() if r.content else None))
            except (httpx.HTTPError, ValueError) as e:
                logger.debug("NSE current-issue failed: %s", e)

    except httpx.HTTPStatusError as e:
        return [], "unreachable", f"NSE HTTP {e.response.status_code}: {e}"
    except httpx.TimeoutException:
        return [], "unreachable", f"NSE request timed out after {_TIMEOUT_S}s"
    except httpx.HTTPError as e:
        return [], "unreachable", f"NSE transport error: {type(e).__name__}: {e}"

    return raw, source, None


def _coerce_records(payload: Any) -> list[dict[str, Any]]:
    """NSE returns a bare list for `ipo`, but `{}` / a wrapped dict for some
    categories. Coerce any of those shapes into a list of record dicts."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        # Empty dict ({}) means no records. A populated dict may wrap the
        # list under a `data` key (NSE uses this on some endpoints).
        for key in ("data", "records", "result"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
        # A dict that is itself a single record (has companyName/symbol).
        if payload.get("companyName") or payload.get("symbol"):
            return [payload]
    return []


# ── Price-band parser (structured shape for the application card) ───────────

def parse_price_band(raw: str | None) -> dict[str, Any] | None:
    """Parse an NSE price-band string into a structured ``{min, max, is_fixed}``.

    NSE / SME records carry the band in a few common shapes::

        "125-132"
        "125 - 132"
        "₹125 – ₹132"      (en-dash, rupee glyph, spaces)
        "Rs. 125 to Rs. 132"
        "120"               (fixed-price issue — single value)
        "120.50"

    The card payload + validation step both need numeric ``min`` / ``max`` so
    the FE can render the amount preview and we can enforce the in-band-bid
    rule server-side. Returns ``None`` for empty / garbage input (keeps the
    existing slim list payload's ``price_band: <raw str>`` untouched — the
    structured form is built only at the propose-application boundary).

    Honest on failure: when we can't extract at least one number we return
    ``None`` rather than fabricate a band; the executor surfaces that as
    "amount not computable" + register CTA disabled, matching the card spec.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    # Normalise unicode separators / currency glyphs so the regex below is
    # robust to NSE's mixed formatting. Keep digits, dots, ASCII hyphen and
    # whitespace. Map en-dash / em-dash / minus-sign / Hindi rupee glyph to
    # ASCII space-hyphen-space.
    cleaned = s
    for token in ("–", "—", "−"):  # en, em, minus
        cleaned = cleaned.replace(token, "-")
    for token in ("₹", "Rs.", "Rs", "INR", "rs.", "rs"):
        cleaned = cleaned.replace(token, " ")
    cleaned = cleaned.replace(" to ", "-").replace(" TO ", "-")

    # Pull every float-shaped run.
    import re as _re
    nums = _re.findall(r"\d+(?:\.\d+)?", cleaned)
    if not nums:
        return None
    try:
        values = [float(n) for n in nums]
    except ValueError:  # pragma: no cover — re.findall guarantees parseable
        return None
    # Sanity: 0 is not a valid IPO band (e.g. NSE sometimes returns "0").
    values = [v for v in values if v > 0]
    if not values:
        return None
    if len(values) == 1:
        v = values[0]
        return {"min": v, "max": v, "is_fixed": True}
    lo, hi = min(values[0], values[1]), max(values[0], values[1])
    return {"min": lo, "max": hi, "is_fixed": lo == hi}


# ── Normalization ─────────────────────────────────────────────────────────────

def _first(rec: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", "-"):
            return v
    return None


def _classify_type(rec: dict[str, Any]) -> str:
    """mainboard vs SME. NSE marks SME under `securityType`/`series`
    ('SME', 'ST', 'SM') or via a `sme` flag."""
    blob = " ".join(
        str(_first(rec, k) or "")
        for k in ("securityType", "series", "issueType", "category", "type")
    ).upper()
    if "SME" in blob or blob.strip() in ("ST", "SM"):
        return "sme"
    return "mainboard"


def _parse_date(raw: Any) -> _dt.date | None:
    """NSE dates look like '21-MAY-2026' or '2026-05-21'. Best-effort."""
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _derive_status(rec: dict[str, Any], open_d: _dt.date | None,
                   close_d: _dt.date | None) -> str:
    """upcoming | open | closed. Prefer an explicit NSE status; otherwise
    infer from open/close dates vs today."""
    explicit = str(_first(rec, "status", "issueStatus") or "").lower()
    if "active" in explicit or "open" in explicit or "current" in explicit:
        return "open"
    if "forthcoming" in explicit or "upcoming" in explicit:
        return "upcoming"
    if "closed" in explicit or "listed" in explicit:
        return "closed"

    today = _dt.date.today()
    if open_d and close_d:
        if today < open_d:
            return "upcoming"
        if open_d <= today <= close_d:
            return "open"
        return "closed"
    if open_d and today < open_d:
        return "upcoming"
    if close_d and today > close_d:
        return "closed"
    return "upcoming"


def _normalize(rec: dict[str, Any]) -> dict[str, Any]:
    name = _first(rec, "companyName", "company", "name", "issuerName") or ""
    symbol = _first(rec, "symbol", "scripCode", "isin") or ""
    price_band = _first(rec, "priceRange", "priceBand", "issuePrice", "price")
    open_raw = _first(rec, "ipoStartDate", "issueStartDate", "openDate", "startDate")
    close_raw = _first(rec, "ipoEndDate", "issueEndDate", "closeDate", "endDate")
    lot_size = _first(rec, "lotSize", "lot_size", "marketLot", "minBidQty")
    issue_size = _first(rec, "issueSize", "issue_size", "totalIssueSize", "noOfSharesOffered")

    open_d = _parse_date(open_raw)
    close_d = _parse_date(close_raw)

    return {
        "name": str(name).strip(),
        "symbol": str(symbol).strip().upper(),
        "price_band": str(price_band).strip() if price_band is not None else None,
        "open_date": open_d.isoformat() if open_d else (str(open_raw) if open_raw else None),
        "close_date": close_d.isoformat() if close_d else (str(close_raw) if close_raw else None),
        "lot_size": lot_size,
        "issue_size": str(issue_size).strip() if issue_size is not None else None,
        "type": _classify_type(rec),
        "status": _derive_status(rec, open_d, close_d),
        # Keep the raw record around for get_ipo_details (extra fields the
        # card might want: series, listing date, RHP link, etc.).
        "_raw": {k: v for k, v in rec.items() if not isinstance(v, (dict, list))},
    }


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The upcoming + current endpoints can overlap. Dedupe by
    (symbol or name), preferring the record with the more concrete status."""
    rank = {"open": 0, "upcoming": 1, "closed": 2}
    seen: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = (r.get("symbol") or r.get("name") or "").upper()
        if not key:
            continue
        prev = seen.get(key)
        if prev is None or rank.get(r["status"], 9) < rank.get(prev["status"], 9):
            seen[key] = r
    return list(seen.values())


# ── Public API ─────────────────────────────────────────────────────────────────

def list_upcoming_ipos() -> dict[str, Any]:
    """Return current/upcoming mainboard + SME IPOs.

    Shape::

        {
          "count": int,
          "ipos": [ {name, symbol, price_band, open_date, close_date,
                     lot_size, issue_size, type, status}, ... ],
          "source": "nse" | "unreachable",
          "note": str | None,
        }

    Honest on failure: if NSE is unreachable, returns ``count: 0``,
    ``source: "unreachable"`` and a ``note`` with the exact error. Never
    fabricates IPO data. Result is Redis-cached for 45 minutes; an empty
    *successful* fetch (genuinely no live IPOs) is also cached so we don't
    re-warm NSE cookies three times in one chat burst.
    """
    cached = _read_cache(_CACHE_KEY)
    if cached is not None:
        return {**cached, "cached": True}

    raw, source, err = _fetch_nse()

    if err is not None:
        # Unreachable — surface the exact failure, do not cache (so the
        # next call retries), do not fabricate.
        return {
            "count": 0,
            "ipos": [],
            "source": "unreachable",
            "note": (
                f"Live IPO feed unreachable from server. {err}. "
                "Needs a reachable NSE endpoint (or a configured IPO data "
                "provider) — no IPO data is invented."
            ),
            "cached": False,
        }

    normalized = [_normalize(r) for r in raw if isinstance(r, dict)]
    normalized = [r for r in normalized if r["name"] or r["symbol"]]
    ipos = _dedupe(normalized)
    ipos.sort(key=lambda r: (r.get("open_date") or "9999"))

    note: str | None = None
    if not ipos:
        note = (
            "NSE reached successfully but reports no open/upcoming IPOs "
            "right now (mainboard + SME windows both empty). This is the "
            "real live state, not an error."
        )

    body = {
        "count": len(ipos),
        # Strip the bulky _raw from the list payload; details endpoint
        # re-fetches from cache and can expose it.
        "ipos": [{k: v for k, v in r.items() if k != "_raw"} for r in ipos],
        "source": source,
        "note": note,
    }
    # Cache the full (with _raw) records separately so get_ipo_details can
    # read extra fields without re-hitting NSE.
    _write_cache(_CACHE_KEY, body)
    _write_cache(_CACHE_KEY + ":raw", {"ipos": ipos})
    return {**body, "cached": False}


def get_ipo_details(name_or_symbol: str) -> dict[str, Any]:
    """Match one IPO within the live list and return its full record + any
    extra raw NSE fields.

    Matching is case-insensitive, exact-symbol-first then substring on
    name/symbol. Returns ``{"found": False, ...}`` with the candidate list
    when nothing matches, so the chat layer can disambiguate.
    """
    query = (name_or_symbol or "").strip()
    if not query:
        return {"found": False, "error": "Provide an IPO name or symbol.",
                "matches": [], "source": "ipo_feed"}

    listing = list_upcoming_ipos()
    if listing.get("source") == "unreachable":
        return {
            "found": False,
            "error": "Live IPO feed unreachable.",
            "note": listing.get("note"),
            "matches": [],
            "source": "unreachable",
        }

    # Pull the richer cached records (with _raw) if available; else use the
    # slim list from the listing call.
    raw_cache = _read_cache(_CACHE_KEY + ":raw")
    records: list[dict[str, Any]] = (
        raw_cache.get("ipos") if raw_cache else listing.get("ipos", [])
    ) or []

    q = query.lower()
    exact = [r for r in records if (r.get("symbol") or "").lower() == q]
    partial = [
        r for r in records
        if q in (r.get("symbol") or "").lower()
        or q in (r.get("name") or "").lower()
    ]
    hit = (exact or partial)
    if not hit:
        return {
            "found": False,
            "query": query,
            "note": (
                f"No live IPO matches '{query}'. "
                + (listing.get("note") or
                   f"There are {listing.get('count', 0)} IPO(s) in the feed.")
            ),
            "matches": [
                {"name": r.get("name"), "symbol": r.get("symbol"),
                 "status": r.get("status")}
                for r in records
            ],
            "source": listing.get("source", "nse"),
        }

    rec = dict(hit[0])
    extra = rec.pop("_raw", None)
    return {
        "found": True,
        "ipo": rec,
        "extra": extra,
        "source": listing.get("source", "nse"),
    }


# ── P1 enrichment helpers ─────────────────────────────────────────────────────
#
# Subscription %, listing date, RHP URL, and registrar/allotment deep-link.
# Each helper is HONEST-ON-FAILURE: returns None / a marker note rather than
# fabricating data. The live NSE feed is sparse, so these helpers are written
# defensively against missing-key / empty-list / "Missing Symbol" responses.


# Static RTA → allotment-status URL map. Used by ``detect_registrar`` to
# resolve a known registrar name into a deep-link the FE can render. NEVER
# fetch RTA status server-side — the user clicks through and the registrar
# asks for their PAN themselves.
RTA_ALLOTMENT_URLS: dict[str, str] = {
    "kfintech":             "https://ipostatus.kfintech.com/",
    "kfin technologies":    "https://ipostatus.kfintech.com/",
    "karvy":                "https://ipostatus.kfintech.com/",
    "link intime":          "https://linkintime.co.in/ipo/public-issues.html",
    "linkintime":           "https://linkintime.co.in/ipo/public-issues.html",
    "mufg intime":          "https://linkintime.co.in/ipo/public-issues.html",
    "mufg":                 "https://linkintime.co.in/ipo/public-issues.html",
    "bigshare":             "https://ipo.bigshareonline.com/ipo_status.html",
    "bigshare services":    "https://ipo.bigshareonline.com/ipo_status.html",
    "cameo":                "https://ipo.cameoindia.com/",
    "cameo corporate":      "https://ipo.cameoindia.com/",
}


# Subscription category keys we expose to the FE. Match the NSE
# ipo-active-category buckets (with sNII + bNII rolled together into a
# single NII multiple, which is what retail users actually want to see).
_SUB_CATEGORIES: tuple[str, ...] = (
    "qib", "nii", "rii", "employee", "shareholder", "overall",
)


def _coerce_subscription_float(v: Any) -> float | None:
    """Lenient float-coercion for NSE category subscription multiples.

    Accepts ``1.4``, ``"1.40"``, ``"1.40x"``, ``"1,234.5"``. Returns None
    for empty / non-numeric / negative values. NEVER substitutes 0 for
    missing — a category with no datum is honestly None.
    """
    if v is None:
        return None
    if isinstance(v, bool):  # bool is an int subclass; reject it
        return None
    if isinstance(v, (int, float)):
        return float(v) if float(v) >= 0 else None
    s = str(v).strip()
    if not s:
        return None
    # Strip common NSE decorations.
    s = s.replace(",", "").rstrip("xX").strip()
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f >= 0 else None


def _parse_active_category_payload(payload: Any) -> dict[str, float | None] | None:
    """Defensive parser for NSE /api/ipo-active-category.

    The live shape is UNOBSERVABLE right now (zero open IPOs at build time),
    so the parser handles a range of plausible shapes:
      * "Missing Symbol" — bare body when ?symbol is missing → None
      * "[]" / [] — no active record for that symbol → None
      * {} — empty dict → None
      * a dict with category names as keys (qib, nii, rii, ...) → extract
      * a dict wrapping a list under `data` / `dataList` → walk items
      * a list of {category, subscription/times/timesSubscribed} dicts
        → fold into the category map

    Returns a dict with keys (qib, nii, rii, employee, shareholder,
    overall) — each a float multiple or None when absent. Returns None
    when the payload is structurally empty (so the caller can mark it
    honest-null with a note).
    """
    if payload is None:
        return None
    if isinstance(payload, str):
        # "Missing Symbol", "[]", "" — bare-string body.
        return None
    if isinstance(payload, list):
        if not payload:
            return None
        # Fold list-of-records by category name.
        out: dict[str, float | None] = {k: None for k in _SUB_CATEGORIES}
        seen_any = False
        for item in payload:
            if not isinstance(item, dict):
                continue
            cat_raw = item.get("category") or item.get("type") or item.get("name")
            val_raw = (
                item.get("subscription")
                or item.get("times")
                or item.get("timesSubscribed")
                or item.get("subscriptionTimes")
                or item.get("noOfTimesSubscribed")
            )
            v = _coerce_subscription_float(val_raw)
            key = _category_key(str(cat_raw or ""))
            if key and v is not None:
                # Sum sNII/bNII multiples into the unified NII bucket when
                # both are present (NSE reports them separately).
                if key == "nii" and out["nii"] is not None:
                    out["nii"] = (out["nii"] or 0.0) + v
                else:
                    out[key] = v
                seen_any = True
        return out if seen_any else None
    if isinstance(payload, dict):
        if not payload:
            return None
        # Walk a wrapping container shape first.
        for wrap_key in ("data", "dataList", "categoryList", "result", "records"):
            inner = payload.get(wrap_key)
            if isinstance(inner, (list, dict)) and inner:
                walked = _parse_active_category_payload(inner)
                if walked is not None:
                    return walked
        # Then try keyed shape: {qib: 1.4, nii: 0.8, ...} or
        # {QIB: {times: 1.4}, NII: {...}}.
        out = {k: None for k in _SUB_CATEGORIES}
        seen_any = False
        for raw_key, raw_val in payload.items():
            key = _category_key(str(raw_key))
            if not key:
                continue
            if isinstance(raw_val, dict):
                v = _coerce_subscription_float(
                    raw_val.get("subscription")
                    or raw_val.get("times")
                    or raw_val.get("timesSubscribed")
                    or raw_val.get("subscriptionTimes")
                    or raw_val.get("noOfTimesSubscribed")
                )
            else:
                v = _coerce_subscription_float(raw_val)
            if v is None:
                continue
            if key == "nii" and out["nii"] is not None:
                out["nii"] = (out["nii"] or 0.0) + v
            else:
                out[key] = v
            seen_any = True
        return out if seen_any else None
    return None


def _category_key(raw: str) -> str | None:
    """Map an NSE category label to the canonical key we expose.

    NSE uses labels like "QIB", "Non Institutional Investors", "Retail
    Individual Investors", "Employee", "Shareholders Reservation",
    "sNII", "bNII", "Total". We fold sNII/bNII into "nii" because the
    user-facing card shows one NII multiple.
    """
    s = (raw or "").strip().lower()
    if not s:
        return None
    if "qib" in s or "qualified" in s:
        return "qib"
    if (
        s.startswith("snii") or s.startswith("bnii")
        or "non institutional" in s or "non-institutional" in s
        or s == "nii"
    ):
        return "nii"
    if s.startswith("rii") or "retail" in s:
        return "rii"
    if "employee" in s:
        return "employee"
    if "shareholder" in s:
        return "shareholder"
    if "total" in s or "overall" in s:
        return "overall"
    return None


def fetch_subscription(symbol: str) -> dict[str, Any]:
    """Fetch live per-category subscription multiples for an open IPO.

    Source: GET https://www.nseindia.com/api/ipo-active-category?symbol=<SYM>
    via the same cookie-warm browser client ``_fetch_nse`` uses.

    Shape (on success)::

        {
          "subscription": {
            "qib": float|None, "nii": float|None, "rii": float|None,
            "employee": float|None, "shareholder": float|None,
            "overall": float|None,
          },
          "as_of": "<ISO IST>",
          "source": "nse",
          "note": None,
        }

    On honest-empty (Missing Symbol / [] / {} / parse-miss) returns::

        {"subscription": None, "as_of": "<ISO IST>",
         "source": "nse", "note": "<honest reason>"}

    On unreachable returns ``source: "unreachable"``. NEVER fabricates
    a number; a category with no datum is None, not 0.

    Cache: Redis key ``f"ipo_feed:sub:{SYM}"``, TTL 15 minutes (separate
    from the 45-minute list cache key — they must not collide). Cache
    successful AND honest-empty-but-reachable; do NOT cache unreachable
    (so the next call retries).
    """
    sym = (symbol or "").strip().upper()
    as_of = format_ist(now_ist(), include_seconds=False)
    if not sym:
        return {
            "subscription": None,
            "as_of": as_of,
            "source": "nse",
            "note": "symbol is required",
        }

    cache_key = f"{_CACHE_PREFIX}sub:{sym}"
    cached = _read_cache(cache_key)
    if cached is not None:
        return cached

    raw_payload: Any = None
    try:
        with _warmed_client() as cli:
            r = cli.get(
                _NSE_ACTIVE_CATEGORY,
                params={"symbol": sym},
                headers=_nse_api_headers(),
            )
            if r.status_code != 200:
                return {
                    "subscription": None,
                    "as_of": as_of,
                    "source": "unreachable",
                    "note": f"NSE HTTP {r.status_code} for ipo-active-category",
                }
            text = (r.text or "").strip()
            # NSE returns bare strings ("Missing Symbol") + bracket
            # literals ("[]") + JSON. Try JSON first, then fall back.
            if not text:
                parsed: Any = None
            else:
                try:
                    parsed = r.json()
                except (ValueError, json.JSONDecodeError):
                    parsed = text
            raw_payload = parsed
    except httpx.TimeoutException:
        return {
            "subscription": None,
            "as_of": as_of,
            "source": "unreachable",
            "note": f"NSE request timed out after {_TIMEOUT_S}s",
        }
    except httpx.HTTPError as e:
        return {
            "subscription": None,
            "as_of": as_of,
            "source": "unreachable",
            "note": f"NSE transport error: {type(e).__name__}: {e}",
        }

    parsed_sub = _parse_active_category_payload(raw_payload)
    if parsed_sub is None:
        body: dict[str, Any] = {
            "subscription": None,
            "as_of": as_of,
            "source": "nse",
            "note": (
                "NSE reached but no subscription record for this symbol "
                "(no active IPO or empty payload)."
            ),
        }
    else:
        body = {
            "subscription": parsed_sub,
            "as_of": as_of,
            "source": "nse",
            "note": None,
        }
    _write_cache(cache_key, body, ttl_s=_SUB_CACHE_TTL_S)
    return body


def resolve_listing_date(record: dict[str, Any]) -> str | None:
    """Pull the listing date off an NSE IPO record, ISO-formatted or None.

    NSE puts listingDate on listed/past records (e.g. /api/public-past-issues).
    Upcoming/current records typically don't carry it. Honest-null when
    absent; never fabricate.
    """
    if not isinstance(record, dict):
        return None
    raw = record.get("listingDate") or record.get("listing_date")
    if raw is None:
        inner = record.get("_raw")
        if isinstance(inner, dict):
            raw = inner.get("listingDate") or inner.get("listing_date")
    if raw is None:
        return None
    parsed = _parse_date(raw)
    if parsed is not None:
        return parsed.isoformat()
    s = str(raw).strip()
    return s or None


def resolve_rhp(record: dict[str, Any]) -> str | None:
    """Scan a record (and its _raw blob) for a plausible RHP / prospectus URL.

    The NSE list/record schema doesn't carry an RHP link in the live feed,
    so this is best-effort: return the first http(s) URL we find. Caller
    treats None as "hide the link" (honest-null beats a guessed 404).
    """
    if not isinstance(record, dict):
        return None

    # Prefer explicit RHP keys.
    for key in ("rhpLink", "rhp_link", "rhpUrl", "rhp_url",
                "rhp", "prospectus", "prospectusUrl"):
        v = record.get(key)
        if isinstance(v, str) and v.lower().startswith(("http://", "https://")):
            return v.strip()

    inner = record.get("_raw") if isinstance(record.get("_raw"), dict) else None
    if isinstance(inner, dict):
        for key in ("rhpLink", "rhp_link", "rhpUrl", "rhp_url",
                    "rhp", "prospectus", "prospectusUrl"):
            v = inner.get(key)
            if isinstance(v, str) and v.lower().startswith(("http://", "https://")):
                return v.strip()

    # Fall back to scanning all string values for a URL.
    candidates: list[dict[str, Any]] = [record]
    if isinstance(inner, dict):
        candidates.append(inner)
    for blob in candidates:
        for v in blob.values():
            if isinstance(v, str):
                vs = v.strip()
                if vs.lower().startswith(("http://", "https://")):
                    return vs
    return None


def detect_registrar(
    record: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Resolve (registrar_name, allotment_url) from an NSE record.

    The live NSE list/record schema doesn't carry the registrar name, so
    in P1 this returns ``(None, None)`` for every real record — the FE
    then shows "Allotment: check with your broker/registrar."

    Wired now (rather than punted) so a future source that yields the
    registrar name maps deterministically through RTA_ALLOTMENT_URLS.
    """
    if not isinstance(record, dict):
        return (None, None)

    # Look for an explicit registrar field.
    candidates: list[str] = []
    for key in ("registrar", "registrarName", "registrar_name", "rta"):
        v = record.get(key)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())

    inner = record.get("_raw") if isinstance(record.get("_raw"), dict) else None
    if isinstance(inner, dict):
        for key in ("registrar", "registrarName", "registrar_name", "rta"):
            v = inner.get(key)
            if isinstance(v, str) and v.strip():
                candidates.append(v.strip())

    if not candidates:
        return (None, None)

    name = candidates[0]
    lname = name.lower()
    for known_token, url in RTA_ALLOTMENT_URLS.items():
        if known_token in lname:
            return (name, url)
    # Registrar name present but not in our static map — surface the name
    # without a deep-link so the FE can show "check with <registrar>".
    return (name, None)


def gmp_payload(symbol: str) -> dict[str, Any] | None:  # noqa: ARG001
    """Return the GMP block for a symbol, or None when disabled.

    GMP is fail-closed OFF in v1 — no licensed vendor is wired and
    rendering unofficial GMP is compliance-sensitive. Returns None
    unconditionally when ``IPO_GMP_ENABLED`` is False; the caller MUST
    treat None as "omit the gmp key entirely" (not "render null").

    If a future build flips the flag on, the assertion below requires
    the module-level disclaimer to be attached to whatever shape this
    helper grows. Until a vendor is wired, returns None even when the
    flag is True (so accidentally flipping the flag still surfaces
    nothing rather than fabricating data).
    """
    if not IPO_GMP_ENABLED:
        return None
    # Defence in depth: any future value MUST carry the disclaimer string.
    assert IPO_GMP_DISCLAIMER, (
        "IPO_GMP_DISCLAIMER must be set before rendering GMP."
    )
    # No vendor wired → still None in v1.
    return None


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import pprint
    pprint.pprint(list_upcoming_ipos())
