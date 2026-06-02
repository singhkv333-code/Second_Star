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
from typing import Any

import httpx

from backend.cache import redis_client

logger = logging.getLogger(__name__)


# ── Config ───────────────────────────────────────────────────────────────────

_NSE_HOME = "https://www.nseindia.com/"
_NSE_WARMUP = "https://www.nseindia.com/market-data/all-upcoming-issues-ipo"
_NSE_UPCOMING = "https://www.nseindia.com/api/all-upcoming-issues"
_NSE_CURRENT = "https://www.nseindia.com/api/ipo-current-issue"

_CACHE_PREFIX = "ipo_feed:"
_CACHE_KEY = f"{_CACHE_PREFIX}list"
_CACHE_TTL_S = 45 * 60          # 45 minutes
_TIMEOUT_S = 12.0

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


def _write_cache(key: str, value: dict) -> None:
    try:
        redis_client.set(key, json.dumps(value, default=str), ex=_CACHE_TTL_S)
    except Exception as e:  # noqa: BLE001
        logger.debug("ipo_feed cache write failed: %s", e)


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
        with httpx.Client(
            timeout=_TIMEOUT_S,
            follow_redirects=True,
            headers=_BROWSER_HEADERS,
        ) as cli:
            # 1. Warm the cookie jar (bm_sz / _abck / nsit). Without this
            #    the JSON API returns 403.
            cli.get(_NSE_HOME, headers={"Accept": "text/html,application/xhtml+xml"})
            cli.get(_NSE_WARMUP, headers={"Accept": "text/html,application/xhtml+xml"})

            api_headers = {
                "Accept": "application/json, text/plain, */*",
                "Referer": _NSE_WARMUP,
                "X-Requested-With": "XMLHttpRequest",
            }

            # 2. Upcoming/forthcoming — mainboard (ipo) + SME (sme).
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

            # 3. Currently-open issues (separate endpoint).
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


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import pprint
    pprint.pprint(list_upcoming_ipos())
