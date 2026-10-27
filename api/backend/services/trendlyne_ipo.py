"""Trendlyne IPO data source — richer enrichment layer over the NSE feed.

Why this exists
---------------
NSE's IPO API (see ``ipo_feed.py``) carries the bare skeleton: name, symbol,
price band, open/close dates. It does NOT reliably carry subscription
breakdown, allotment date/status, RHP links, or post-listing performance —
exactly the fields a retail user asks about ("how subscribed is it?", "when's
allotment?", "did it list at a gain?").

Trendlyne publishes all of that through its public IPO *web-widget*
(``/web-widget/ipo-widget/...``). That endpoint returns server-rendered HTML
(no JSON API), so this module fetches and parses the HTML into the same
normalized record shape ``ipo_feed`` uses, keyed by a normalized company name
for merging.

We consume Trendlyne's *data*, render it in Pivot's own cards (we never embed
their widget). Source is always attributed (``data_source: "trendlyne"``).

Sections parsed
---------------
- UPCOMING/OPEN   → price band, open/close, RHP link, subscription breakdown
- LISTING SOON    → allotment date + status + allotment-check link
- RECENTLY LISTED → listing date, issue price, listing-gain %, current return %
- BEST / WORST    → same post-listing shape (long-run current return)

Honesty
-------
On any fetch/parse failure the module returns an empty map + a ``note`` with
the exact error — it never fabricates. Trendlyne exposes NO pre-listing grey-
market premium here (only post-listing returns), so this module deliberately
produces no GMP value; Pivot's GMP path stays OFF.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from typing import Any, Optional

import httpx

from backend.cache import redis_client

logger = logging.getLogger(__name__)

# Generic IPO-dashboard widget (font=Poppins is just the theme). Carries the
# full Indian IPO universe across all sections — not a single company.
_WIDGET_URL = (
    "https://trendlyne.com/web-widget/ipo-widget/Poppins/"
    "?activeCol=006AFF&linksCol=006CFF&primary=202020"
    "&secondary=666666&positive=00a25b&negative=ff4e54"
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Referer": "https://trendlyne.com/",
    "Accept": "text/html,application/xhtml+xml",
}
_TIMEOUT_S = 15.0

_CACHE_KEY = "ipo_feed:trendlyne"
_CACHE_TTL_S = 30 * 60  # 30 minutes — subscription/returns move intraday

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


# ── name normalisation (for merging against NSE records) ────────────────────
_SUFFIX_RE = re.compile(
    r"\b(limited|ltd|private|pvt|the|ipo|sme|industries|india|enterprises)\b",
    re.I,
)


def normalize_name(name: str) -> str:
    """Collapse a company/display name to a stable match key.
    'Avience Biomedicals Limited' / 'AVIENCE BIOMEDICALS' → 'aviencebiomedicals'."""
    if not name:
        return ""
    s = _SUFFIX_RE.sub(" ", str(name).lower())
    return re.sub(r"[^a-z0-9]", "", s)


# ── small parse helpers ─────────────────────────────────────────────────────
def _num(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", str(text).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _mult(text: Optional[str]) -> Optional[float]:
    """'2.6x' → 2.6 ; '-' → None."""
    if not text or "-" == text.strip():
        return None
    return _num(text)


def _pct(text: Optional[str]) -> Optional[float]:
    """'46.6%' → 46.6 ; '-16.7%' → -16.7."""
    return _num(text)


def _parse_date(token: str, *, default_year: Optional[int] = None) -> Optional[_dt.date]:
    """Parse Trendlyne date tokens: "24 Jun '26", "18 Jun", "02 Jul '24"."""
    if not token:
        return None
    t = token.strip().replace("’", "'")
    m = re.search(r"(\d{1,2})\s*([A-Za-z]{3})(?:\s*'?(\d{2,4}))?", t)
    if not m:
        return None
    day = int(m.group(1))
    mon = _MONTHS.get(m.group(2).lower())
    if not mon:
        return None
    yr_raw = m.group(3)
    if yr_raw:
        yr = int(yr_raw)
        if yr < 100:
            yr += 2000
    else:
        yr = default_year or _dt.date.today().year
    try:
        return _dt.date(yr, mon, day)
    except ValueError:
        return None


def _parse_open_close(token: str) -> tuple[Optional[_dt.date], Optional[_dt.date]]:
    """"18 - 22 Jun" → (18 Jun, 22 Jun); "28 Jun - 02 Jul '26" handled too."""
    if not token:
        return None, None
    parts = re.split(r"\s*[-–to]+\s*", token.strip(), maxsplit=1)
    if len(parts) == 2:
        left, right = parts
        close = _parse_date(right)
        yr = close.year if close else None
        # left may lack a month ("18" in "18 - 22 Jun"): borrow the right's.
        if not re.search(r"[A-Za-z]", left) and close:
            left = f"{left.strip()} {close.strftime('%b')} '{str(close.year)[2:]}"
        open_d = _parse_date(left, default_year=yr)
        return open_d, close
    d = _parse_date(token)
    return d, d


def _subscription(cell) -> Optional[dict[str, Any]]:
    """Parse the subscription cell: overall multiple + Total/Retail/HNI/QIB.
    The cell embeds a nested tooltip table whose <td>s carry the breakdown."""
    if cell is None:
        return None
    inner = [td.get_text(strip=True) for td in cell.find_all("td")]
    out: dict[str, Any] = {}
    # nested tds come as label/value pairs: Total: 2.6x Retail: 1.4x ...
    key_map = {"total": "total", "retail": "retail", "hni": "hni",
               "nii": "hni", "qib": "qib"}
    i = 0
    while i < len(inner) - 1:
        label = inner[i].rstrip(":").strip().lower()
        if label in key_map:
            out[key_map[label]] = _mult(inner[i + 1])
            i += 2
        else:
            i += 1
    if "total" not in out:
        span = cell.find("span")
        if span:
            out["total"] = _mult(span.get_text(strip=True))
    return out or None


# ── fetch + parse ───────────────────────────────────────────────────────────
def _fetch_html() -> tuple[Optional[str], Optional[str]]:
    try:
        with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=True) as c:
            r = c.get(_WIDGET_URL, headers=_HEADERS)
            r.raise_for_status()
            return r.text, None
    except Exception as e:  # noqa: BLE001 — relay exact failure, never raise
        return None, f"{type(e).__name__}: {str(e)[:160]}"


def _cells(row) -> list:
    """Direct (logical) cells of a row, skipping the nested tooltip tables."""
    return row.find_all("td", recursive=False)


def _parse(html: str) -> dict[str, dict[str, Any]]:
    """Parse the widget HTML → {normalized_name: record}. Later sections only
    fill fields missing from earlier (richer-status-first) ones."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    out: dict[str, dict[str, Any]] = {}

    def upsert(name: str, fields: dict[str, Any], section: str) -> None:
        key = normalize_name(name)
        if not key:
            return
        rec = out.setdefault(
            key, {"name": name.strip(), "data_source": "trendlyne", "_sections": []}
        )
        rec["_sections"].append(section)
        for k, v in fields.items():
            if v is not None and rec.get(k) in (None, ""):
                rec[k] = v

    for table in soup.find_all("table"):
        ths = [th.get_text(" ", strip=True) for th in table.find_all("th")]
        if len(ths) < 8:
            continue
        heading_el = table.find_previous(["h1", "h2", "h3", "h4", "h5"])
        heading = (heading_el.get_text(strip=True).lower() if heading_el else "")
        body = table.find("tbody")
        rows = body.find_all("tr") if body else table.find_all("tr")[1:]
        hl = [h.lower() for h in ths]

        for row in rows:
            tds = _cells(row)
            if len(tds) < len(ths):
                continue
            cellmap = dict(zip(hl, tds))

            def gtxt(*keys: str) -> Optional[str]:
                for kk in keys:
                    for h, td in cellmap.items():
                        if kk in h:
                            return td.get_text(" ", strip=True)
                return None

            def gcell(*keys: str):
                for kk in keys:
                    for h, td in cellmap.items():
                        if kk in h:
                            return td
                return None

            name = gtxt("company name")
            if not name:
                continue
            typ_raw = (gtxt("type") or "").lower()
            typ = "sme" if "sme" in typ_raw else "mainboard"
            fields: dict[str, Any] = {
                "type": typ,
                "market_cap_cr": _num(gtxt("market cap")),
                "issue_size_cr": _num(gtxt("issue size")),
                "min_investment": _num(gtxt("min investment")),
                "subscription": _subscription(gcell("total subscription", "subscription")),
            }

            # Section-specific columns.
            if "open" in heading or "upcoming" in heading or any("open/close" in h for h in hl):
                open_d, close_d = _parse_open_close(gtxt("open/close", "open") or "")
                fields["open_date"] = open_d.isoformat() if open_d else None
                fields["close_date"] = close_d.isoformat() if close_d else None
                pr = gtxt("price range", "price band")
                fields["price_band"] = pr.strip() if pr else None
                rhp = gcell("rhp")
                if rhp and rhp.find("a"):
                    fields["rhp_url"] = rhp.find("a").get("href")

            if "listing soon" in heading or "allotment" in " ".join(hl):
                ld = _parse_date(gtxt("listing date") or "")
                fields["listing_date"] = ld.isoformat() if ld else None
                ad = _parse_date(gtxt("allotment date") or "")
                fields["allotment_date"] = ad.isoformat() if ad else None
                acell = gcell("allotment status")
                if acell:
                    fields["allotment_status"] = acell.get_text(" ", strip=True) or None
                    a = acell.find("a")
                    if a:
                        fields["allotment_check_url"] = a.get("href")

            if "listed" in heading or "performer" in heading or "performers" in heading:
                ld = _parse_date(gtxt("listing date") or "")
                fields["listing_date"] = ld.isoformat() if ld else None
                fields["issue_price"] = _num(gtxt("issue price"))
                fields["listing_gain_pct"] = _pct(gtxt("listing gain"))
                fields["current_return_pct"] = _pct(gtxt("current return"))

            upsert(name, fields, heading[:24] or "?")

    return out


# ── public API ───────────────────────────────────────────────────────────────
def fetch_trendlyne_ipos(*, use_cache: bool = True) -> dict[str, Any]:
    """Return the parsed Trendlyne IPO universe.

    Shape::
        {
          "ipos": { normalized_name: {name, type, price_band, open_date,
                    close_date, subscription, allotment_*, listing_*,
                    rhp_url, ...}, ... },
          "count": int,
          "source": "trendlyne" | "unreachable",
          "note": str | None,
        }
    Honest on failure: ``source: "unreachable"`` + exact ``note``; never
    fabricates. Cached in Redis for 30 minutes.
    """
    if use_cache:
        try:
            raw = redis_client.get(_CACHE_KEY)
            if raw:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode()
                return {**json.loads(raw), "cached": True}
        except Exception:
            pass

    html, err = _fetch_html()
    if err is not None:
        return {"ipos": {}, "count": 0, "source": "unreachable",
                "note": f"Trendlyne IPO widget unreachable: {err}", "cached": False}

    try:
        parsed = _parse(html)
    except Exception as e:  # noqa: BLE001
        logger.warning("Trendlyne IPO parse failed: %s", e)
        return {"ipos": {}, "count": 0, "source": "unreachable",
                "note": f"Trendlyne IPO HTML parse failed: {type(e).__name__}: {str(e)[:120]}",
                "cached": False}

    body = {"ipos": parsed, "count": len(parsed), "source": "trendlyne", "note": None}
    try:
        redis_client.setex(_CACHE_KEY, _CACHE_TTL_S, json.dumps(body))
    except Exception:
        pass
    return {**body, "cached": False}
