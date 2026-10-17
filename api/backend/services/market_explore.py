"""The Markets page: the whole market in one pass.

`GET /api/markets/explore` answers one question for the Markets tab: what is
the market doing, broken down so a person can find something worth a look.
It reads the same four maps the screener reads, so a number here and the same
number in the screener grid can never disagree:

    universe      every verified NSE name, its sector and market cap
    fundamentals  P/E, ROE, ROCE, D/E                (financials DB)
    prices        live day change                     (Kite, else Yahoo)
    technicals    end-of-day returns, RSI, 52-week distance, volume,
                  trend and volume-profile position   (the dataserver's daily matrix)

The page covers the 500 largest companies by market cap. Below that the tape
is thin enough that a "top gainer" is usually an illiquid name on a circuit,
which is noise, not news.

One price basis per page. Live changes are used only when they cover at least
80% of the 500; otherwise every change is the last end-of-day session. A list
that ranks a live +3% beside a stale +3% is ranking two different days.

Code computes the market snapshot. market_brief.py combines these facts with
timestamped cross-asset quotes and headlines for the shared hourly bulletin;
the grounding helpers below validate its numbers.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

TOP_N = 500
LIVE_MIN_COVERAGE = 0.8
NEAR_EXTREME_PCT = 1.0          # within 1% of the 52-week high or low
_CACHE_KEY = "markets:explore:v1"
_CACHE_TTL = 30 * 60         # how long a stale payload may still be served

# Bins for the distribution of today's moves, in percent.
HIST_EDGES = (-5.0, -3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 5.0)

_SUFFIX = re.compile(r"\s+(limited|ltd\.?)$", re.I)


def short_name(name: str) -> str:
    """'HCL Technologies Limited' -> 'HCL Technologies'."""
    out = _SUFFIX.sub("", (name or "").strip()).strip().rstrip(",")
    return out or name


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _r(v: Any, nd: int = 2) -> Optional[float]:
    f = _num(v)
    return None if f is None else round(f, nd)


def eod_day(as_of: Any) -> Optional[str]:
    """the dataserver's matrix stamps its session as an IST day number (days since
    1970-01-01). An ISO date is the only form the page shows."""
    n = _num(as_of)
    if n is None:
        s = str(as_of or "").strip()
        return s[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else None
    return (date(1970, 1, 1) + timedelta(days=int(n))).isoformat()


def _median(xs: list[float]) -> Optional[float]:
    return round(statistics.median(xs), 2) if xs else None


def compose(
    universe: list[dict],
    fundamentals: dict[str, dict],
    metrics: dict[str, dict],
    metrics_source: str,
    tech: dict[str, dict],
    tech_as_of: Any,
    logos: dict[str, Optional[str]],
    eps: Optional[dict[str, float]] = None,
    top_n: int = TOP_N,
) -> dict:
    """The page payload from the four maps. Pure: no I/O, so tests can feed it."""
    top = [r for r in universe if r.get("mcap_cr")]
    top.sort(key=lambda r: -(r.get("mcap_cr") or 0))
    top = top[:top_n]
    syms = [r["symbol"].upper() for r in top]

    live_n = sum(1 for s in syms if _num((metrics.get(s) or {}).get("change_pct")) is not None)
    live = (
        metrics_source in ("kite", "yfinance")
        and bool(syms)
        and live_n / len(syms) >= LIVE_MIN_COVERAGE
    )

    stocks: list[dict] = []
    for r in top:
        s = r["symbol"].upper()
        f = fundamentals.get(s) or {}
        m = metrics.get(s) or {}
        t = tech.get(s) or {}
        price = _r(m.get("price")) if live else _r(t.get("close"))
        change = _r(m.get("change_pct")) if live else _r(t.get("ret_1d"))
        # P/E as price over trailing EPS where the EPS is known: it moves with
        # the price. Moneycontrol's earnings yield is stored to two decimals,
        # so 1/EY snaps to 20, 25, 33.3 — kept only as the fallback.
        e = _num((eps or {}).get(s))
        pe = round(price / e, 1) if price and e and e > 0 and price / e <= 500 else _r(f.get("pe"), 1)
        stocks.append({
            "symbol": s,
            "name": short_name(r.get("name") or s),
            "sector": r.get("sector") or "other",
            "mcap_cr": r.get("mcap_cr"),
            "price": price,
            "change": change,
            "ret_1w": _r(t.get("ret_1w")),
            "ret_1m": _r(t.get("ret_1m")),
            "ret_3m": _r(t.get("ret_3m")),
            "ret_1y": _r(t.get("ret_1y")),
            "pe": pe if pe is not None and pe > 0 else None,
            "roe": _r(f.get("roe"), 1),
            "rsi": _r(t.get("rsi14"), 1),
            "vol_ratio": _r(t.get("vol_ratio20")),
            "turnover_cr": _r(t.get("turnover_20d_cr"), 1),
            "dist_high": _r(t.get("dist_52w_high")),
            "dist_low": _r(t.get("dist_52w_low")),
            "streak": t.get("streak") if isinstance(t.get("streak"), int) else None,
            "bb_width": _r(t.get("bb_width_pct")),
            "adx": _r(t.get("adx14"), 1),
            "sma50": _r(t.get("sma50_rel")),
            "sma200": _r(t.get("sma200_rel")),
            "breakout": _r(t.get("hi20_break_pct")),
            "vp_pos": _r(t.get("vp20_pos"), 1),
            "vp_shift": _r(t.get("vp20_poc_shift_pct")),
            "logo": logos.get(s),
        })

    sector_labels: dict[str, str] = {}
    for r in top:
        sector_labels.setdefault(r.get("sector") or "other", r.get("sector_label") or "Other")

    return {
        "as_of": {
            "prices": "live" if live else "eod",
            "price_source": metrics_source if live else "dataserver_daily_matrix",
            "live_coverage": round(live_n / len(syms), 3) if syms else 0,
            "eod_day": eod_day(tech_as_of),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "universe": {"basis": "market_cap", "size": len(stocks)},
        "breadth": breadth(stocks),
        "sectors": sectors(stocks, sector_labels),
        "stocks": stocks,
    }


def breadth(stocks: list[dict]) -> dict:
    ch = [x["change"] for x in stocks if x["change"] is not None]
    s50 = [x["sma50"] for x in stocks if x["sma50"] is not None]
    s200 = [x["sma200"] for x in stocks if x["sma200"] is not None]
    hi = [x["dist_high"] for x in stocks if x["dist_high"] is not None]
    lo = [x["dist_low"] for x in stocks if x["dist_low"] is not None]
    bins = [0] * (len(HIST_EDGES) + 1)
    for v in ch:
        i = 0
        while i < len(HIST_EDGES) and v > HIST_EDGES[i]:
            i += 1
        bins[i] += 1
    return {
        "total": len(ch),
        "advancers": sum(1 for v in ch if v > 0),
        "decliners": sum(1 for v in ch if v < 0),
        "unchanged": sum(1 for v in ch if v == 0),
        "median_change": _median(ch),
        "above_sma50": sum(1 for v in s50 if v > 0),
        "sma50_total": len(s50),
        "above_sma200": sum(1 for v in s200 if v > 0),
        "sma200_total": len(s200),
        "near_high": sum(1 for v in hi if v >= -NEAR_EXTREME_PCT),
        "near_low": sum(1 for v in lo if v <= NEAR_EXTREME_PCT),
        "extremes_total": len(hi),
        "histogram": {"edges": list(HIST_EDGES), "counts": bins},
    }


def sectors(stocks: list[dict], labels: dict[str, str]) -> list[dict]:
    """Per sector: cap-weighted day change, median returns, breadth, leaders,
    and where it sits on the rotation map (relative to the median stock)."""
    by: dict[str, list[dict]] = {}
    for x in stocks:
        by.setdefault(x["sector"], []).append(x)
    all_1w = _median([x["ret_1w"] for x in stocks if x["ret_1w"] is not None])
    all_1m = _median([x["ret_1m"] for x in stocks if x["ret_1m"] is not None])

    out = []
    for key, xs in by.items():
        w = [(x["mcap_cr"], x["change"]) for x in xs if x["change"] is not None and x["mcap_cr"]]
        tw = sum(m for m, _ in w)
        change = round(sum(m * c for m, c in w) / tw, 2) if tw else None
        r1w = _median([x["ret_1w"] for x in xs if x["ret_1w"] is not None])
        r1m = _median([x["ret_1m"] for x in xs if x["ret_1m"] is not None])
        ranked = sorted(xs, key=lambda x: -(x["mcap_cr"] or 0))
        movers = sorted((x for x in xs if x["change"] is not None), key=lambda x: x["change"])
        out.append({
            "key": key,
            "label": "Other" if key == "other" else labels.get(key, key.replace("_", " ").title()),
            "count": len(xs),
            "mcap_cr": sum(x["mcap_cr"] or 0 for x in xs),
            "change": change,
            "ret_1w": r1w,
            "ret_1m": r1m,
            "ret_1y": _median([x["ret_1y"] for x in xs if x["ret_1y"] is not None]),
            "advancers": sum(1 for x in xs if (x["change"] or 0) > 0),
            "decliners": sum(1 for x in xs if (x["change"] or 0) < 0),
            "pe_median": _median([x["pe"] for x in xs if x["pe"] is not None]),
            "leaders": [x["symbol"] for x in ranked[:3]],
            "best": movers[-1]["symbol"] if movers else None,
            "worst": movers[0]["symbol"] if movers else None,
            # Rotation: strength over a month and pace over a week, both
            # against the median stock of the 500. Leading = both positive.
            "rs_1m": None if r1m is None or all_1m is None else round(r1m - all_1m, 2),
            "rs_1w": None if r1w is None or all_1w is None else round(r1w - all_1w, 2),
        })
    out.sort(key=lambda s: (s["key"] == "other", -s["mcap_cr"]))
    return out


# ── sectors ────────────────────────────────────────────────────────────
# India's own split of the market, close to NSE's sectoral indices, from
# Moneycontrol's industry slug (present for every NSE name). The screener's
# yfinance sectors ("Consumer Defensive") are missing for SBI, ITC and HUL,
# whose enrichment rows were never fetched, and are US categories besides.

SECTORS: dict[str, str] = {
    "banks": "Banks", "financials": "Financials", "it": "IT",
    "pharma": "Pharma & Healthcare", "fmcg": "FMCG", "auto": "Auto",
    "energy": "Oil & Gas", "power": "Power", "metals": "Metals & Mining",
    "materials": "Cement & Materials", "chemicals": "Chemicals",
    "capital_goods": "Capital Goods", "infra": "Infrastructure",
    "realty": "Realty", "telecom": "Telecom", "media": "Media",
    "consumer_durables": "Consumer Durables",
    "consumer_services": "Consumer Services",
    "logistics": "Transport & Logistics", "textiles": "Textiles",
    "diversified": "Diversified",
}

# First match wins, so the narrower rules come first.
_SLUG_RULES: tuple[tuple[str, str], ...] = (
    ("realty", r"realestate|residential|constructionhousing|realty"),
    ("infra", r"^infrastructure|engineeringconstruction|transportinfra|^constructioncontracting|^constructioninfra"),
    ("consumer_services", r"automobiledealer|retail|onlineservice|^hotel|travelservice|educational|restaurant"),
    ("logistics", r"logistic|shipping|courier|freight|airline|^port"),
    ("banks", r"^bank"),
    ("financials", r"^finance|insurance|stockbroking|nbfc|assetmanagement"),
    ("it", r"^computer|software|^itservices|^bpo|^iteducation"),
    ("pharma", r"^pharma|hospital|medical|^labs|healthcare|diagnostic"),
    ("fmcg", r"personalcare|householdpersonal|^food|consumerfood|cigarette|brewer|distiller|"
             r"plantation|^sugar|edibleoil|vanaspati|vegetableoil|detergent|aquaculture|dairy|agricultur"),
    ("capital_goods", r"equipment|^engineering|^electrical|electrode|machinetool|compressor|^pump|"
                      r"^cables(?!telephone)|castingsforgings$|aerospace|defence|transmissiontower|"
                      r"bearing|fastener|^engines|textilesmachinery"),
    ("auto", r"^auto|tyre|automobile|tractor"),
    ("energy", r"refiner|^oil|petrochem|lubricant|^gas|industrialgases"),
    ("power", r"^power|renewable"),
    ("metals", r"steel|^iron|^metal|alumin|mining"),
    ("chemicals", r"chemical|dyes|pesticide|agrochem|fertili|^paint"),
    ("materials", r"cement|ceramic|^glass|constructionmaterial|laminate|^paper|packag|^plastic|"
                  r"^rubber|container|refractor|abrasive"),
    ("telecom", r"telecom|cablestelephone"),
    ("media", r"media|^film|broadcast|printing|entertainment"),
    ("consumer_durables", r"consumergoods|domesticappliance|electronicgoods|whitegoods|batter|"
                          r"drycell|electronicscomponent|jewel|diamond|footwear"),
    ("textiles", r"textile|leather|apparel"),
)

# Moneycontrol files a few large names under their own slug ("itc",
# "licindia") or under the wrong industry (Titan under glass).
SECTOR_OVERRIDES: dict[str, str] = {
    "ITC": "fmcg", "LICI": "financials", "BAJFINANCE": "financials",
    "ETERNAL": "consumer_services", "ZYDUSLIFE": "pharma", "BEL": "capital_goods",
    "TATAPOWER": "power", "TMPV": "auto", "TMCV": "auto", "TITAN": "consumer_durables",
    "IRCTC": "consumer_services", "NYKAA": "consumer_services",
    "NAUKRI": "consumer_services", "SRF": "chemicals", "SIEMENS": "capital_goods",
    "ABB": "capital_goods", "GRASIM": "materials", "PAYTM": "financials",
    "HINDALCO": "metals", "GALLANTT": "metals", "MOTHERSON": "auto", "UNOMINDA": "auto",
    "ZFCVINDIA": "auto", "CIEINDIA": "auto", "MCX": "financials", "BSE": "financials",
    "NAM-INDIA": "financials", "CAMS": "financials", "IDBI": "banks",
    "GMRAIRPORT": "infra", "IRB": "infra", "IRBIT-RE": "infra", "JSWINFRA": "logistics",
    "SHADOWFAX": "logistics", "ATGL": "energy", "VOLTAS": "consumer_durables",
    "AMBER": "consumer_durables", "WELSPUNLIV": "textiles", "AFFLE": "it",
    "URBANCO": "consumer_services", "SUPREMEPWR": "capital_goods",
    "ACUTAAS": "chemicals", "FINEORG": "chemicals",
}

_YF_SECTOR = {
    "technology": "it", "healthcare": "pharma", "financial_services": "financials",
    "consumer_defensive": "fmcg", "energy": "energy", "utilities": "power",
    "basic_materials": "materials", "industrials": "capital_goods",
    "consumer_cyclical": "consumer_services", "real_estate": "realty",
    "communication_services": "telecom",
}


def sector_of(symbol: str, slug: Optional[str], yf_sector: Optional[str] = None) -> str:
    sym = (symbol or "").upper()
    if sym in SECTOR_OVERRIDES:
        return SECTOR_OVERRIDES[sym]
    sl = (slug or "").lower()
    for key, pat in _SLUG_RULES:
        if sl and re.search(pat, sl):
            return key
    return _YF_SECTOR.get((yf_sector or "").lower(), "diversified")


# ── the live build ─────────────────────────────────────────────────────


def build() -> dict:
    """The payload, at most a minute old when anyone is reading the page.

    A cold compose takes seconds (fundamentals top-up, EPS, logos), so a stale
    payload is served while one background thread rebuilds it; only the very
    first request on a cold cache waits.
    """
    from backend.cache import redis_client

    cached = None
    try:
        raw = redis_client.get(_CACHE_KEY)
        if raw:
            cached = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception as exc:  # noqa: BLE001 — the cache is an optimisation
        logger.debug("[markets] explore cache read failed: %s", exc)
    if cached and cached.get("stocks"):
        age = time.time() - float(cached.get("_built", 0))
        if age > _FRESH_S:
            _kick_rebuild()
        return cached
    return _rebuild()


_FRESH_S = 60
_rebuild_lock = threading.Lock()
_rebuilding = False


def _kick_rebuild() -> None:
    global _rebuilding
    with _rebuild_lock:
        if _rebuilding:
            return
        _rebuilding = True

    def run() -> None:
        global _rebuilding
        try:
            _rebuild()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[markets] background rebuild failed: %s", exc)
        finally:
            with _rebuild_lock:
                _rebuilding = False

    threading.Thread(target=run, name="markets-explore", daemon=True).start()


def _rebuild() -> dict:
    from backend.cache import redis_client
    from backend.routers import screener as sc

    mmap, msrc = sc._market_metrics_cached()
    tech, tech_as_of, _src = sc._technical_features_cached()
    universe = ranked_universe(sc._full_universe(), reference(), mmap, tech)
    syms = [r["symbol"] for r in universe[:TOP_N]]

    fmap = dict(sc._fundamentals_map_cached())
    missing_f = [s for s in syms if s not in fmap]
    if missing_f:
        fresh = sc._fetch_fundamentals_map(missing_f)
        if fresh:
            sc._merge_fundamentals_cache(fresh)
            fmap.update(fresh)

    missing_m = [s for s in syms if s not in mmap]
    if missing_m:
        # The screener's metrics refresh covers its curated list; the rest of
        # the 500 fill in the background and the next build uses them.
        sc._kick_page_metrics_warm(missing_m)

    logos = sc._logo_map(syms)
    out = compose(universe, fmap, mmap, msrc, tech, tech_as_of, logos, eps_map())
    out["_built"] = time.time()
    if out["stocks"]:
        try:
            redis_client.set(_CACHE_KEY, json.dumps(out), ex=_CACHE_TTL)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[markets] explore cache write failed: %s", exc)
    return out


def ranked_universe(screener_universe: list[dict], ref: dict, metrics: dict, tech: dict) -> list[dict]:
    """Every NSE stock with a name, a sector and a market cap, largest first.

    Market cap is price x shares from the latest shareholding filing. The
    enrichment snapshot's cap is only the fallback: it has no row at all for
    SBI, ITC or UltraTech, which left them out of any market-cap ranking.
    """
    enrich = {r["symbol"].upper(): r for r in screener_universe}
    out = []
    for sym, row in ref.items():
        if row.get("etf"):
            continue
        e = enrich.get(sym) or {}
        price = _num((metrics.get(sym) or {}).get("price")) or _num((tech.get(sym) or {}).get("close"))
        shares = _num(row.get("shares"))
        mcap = round(price * shares / 1e7) if price and shares else None
        # The enrichment cap is joined by Moneycontrol id, and some ids point
        # at another company (Loyal Textile carried a six-figure cap). Use it
        # only when its company name agrees with ours.
        if not mcap and e.get("mcap_cr") and _same_company(e.get("name"), row.get("name")):
            mcap = e["mcap_cr"]
        if not mcap:
            continue
        key = sector_of(sym, row.get("slug"), (e.get("sector") if e.get("sector") != "other" else None))
        out.append({
            "symbol": sym,
            "name": row.get("name") or e.get("name") or sym,
            "sector": key,
            "sector_label": SECTORS[key],
            "mcap_cr": int(mcap),
        })
    out.sort(key=lambda r: -r["mcap_cr"])
    return out


def _same_company(a: Optional[str], b: Optional[str]) -> bool:
    words = lambda n: [w for w in re.findall(r"[a-z0-9]+", (n or "").lower())  # noqa: E731
                       if w not in ("the", "limited", "ltd", "india")]
    wa, wb = words(a), words(b)
    return bool(wa and wb and wa[0] == wb[0])


_REF_KEY = "markets:reference:v1"
_REF_TTL = 6 * 60 * 60


def reference() -> dict[str, dict]:
    """{SYMBOL: {name, slug, shares, etf}} for every NSE name: Moneycontrol's
    company list and industry, the verified company name, and the share count
    from the latest shareholding filing. Six-hourly; fails open to {}."""
    from backend.cache import redis_client

    try:
        raw = redis_client.get(_REF_KEY)
        if raw:
            return json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:  # noqa: BLE001
        pass
    out: dict[str, dict] = {}
    try:
        from sqlalchemy import text

        from backend.database import FinancialsSessionLocal, SessionLocal

        fin = FinancialsSessionLocal()
        try:
            for sym, name, slug in fin.execute(text(
                    "SELECT upper(nse_symbol), company_name, industry_slug FROM mc.companies "
                    "WHERE nse_symbol IS NOT NULL AND nse_symbol <> ''")):
                out.setdefault(sym, {"name": name, "slug": slug, "etf": slug == "etf"})
            for sym, shares in fin.execute(text(
                    "SELECT DISTINCT ON (symbol) upper(symbol), total_shares FROM shp.filings "
                    "WHERE total_shares > 0 ORDER BY symbol, quarter_end DESC")):
                if sym in out:
                    out[sym]["shares"] = float(shares)
        finally:
            fin.close()
        db = SessionLocal()
        try:
            for sym, name in db.execute(text(
                    "SELECT DISTINCT ON (verified_symbol) upper(verified_symbol), verified_name "
                    "FROM company_identity WHERE verified_exchange = 'NSE' "
                    "ORDER BY verified_symbol, mc_is_primary DESC, mc_metric_count DESC NULLS LAST")):
                if sym in out and name:
                    out[sym]["name"] = name
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[markets] reference load failed: %s", exc)
    if out:
        try:
            redis_client.set(_REF_KEY, json.dumps(out), ex=_REF_TTL)
        except Exception:  # noqa: BLE001
            pass
    return out


_EPS_KEY = "markets:eps:v1"
_EPS_TTL = 6 * 60 * 60


def eps_map() -> dict[str, float]:
    """{SYMBOL: trailing EPS} from the yfinance enrichment snapshot, joined to
    NSE symbols through mc.companies. Fails open to {}."""
    from backend.cache import redis_client

    try:
        raw = redis_client.get(_EPS_KEY)
        if raw:
            return json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:  # noqa: BLE001
        pass
    out: dict[str, float] = {}
    try:
        from sqlalchemy import text

        from backend.database import EnrichSessionLocal, FinancialsSessionLocal

        if EnrichSessionLocal is None:
            return {}
        fin = FinancialsSessionLocal()
        try:
            sym = {r[0]: str(r[1]).upper() for r in fin.execute(text(
                "SELECT sc_id, nse_symbol FROM mc.companies "
                "WHERE nse_symbol IS NOT NULL AND nse_symbol <> ''"))}
        finally:
            fin.close()
        edb = EnrichSessionLocal()
        try:
            for sc_id, e in edb.execute(text(
                    "SELECT sc_id, (raw_info->>'trailingEps')::float "
                    "FROM enrich.company_profile "
                    "WHERE raw_info->>'trailingEps' ~ '^-?[0-9]+(\\.[0-9]+)?$'")):
                if sc_id in sym and e is not None:
                    out.setdefault(sym[sc_id], float(e))
        finally:
            edb.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[markets] trailing EPS load failed: %s", exc)
        return {}
    if out:
        try:
            redis_client.set(_EPS_KEY, json.dumps(out), ex=_EPS_TTL)
        except Exception:  # noqa: BLE001
            pass
    return out


# ── the brief ──────────────────────────────────────────────────────────


def brief_facts(data: dict) -> dict:
    """The only numbers the brief may use, already rounded as they will read."""
    b = data["breadth"]
    stocks = [x for x in data["stocks"] if x["change"] is not None]
    secs = [s for s in data["sectors"] if s["change"] is not None and s["key"] != "other"]
    secs.sort(key=lambda s: s["change"])
    up = sorted(stocks, key=lambda x: -x["change"])[:3]
    down = sorted(stocks, key=lambda x: x["change"])[:3]

    def pct(n: int, d: int) -> Optional[int]:
        return round(n / d * 100) if d else None

    return {
        "basis": "today's session" if data["as_of"]["prices"] == "live" else
                 f"the session of {data['as_of']['eod_day']}",
        "stocks_counted": b["total"],
        "advancers": b["advancers"],
        "decliners": b["decliners"],
        "median_change_pct": b["median_change"],
        "pct_above_200_day_average": pct(b["above_sma200"], b["sma200_total"]),
        "near_52_week_high": b["near_high"],
        "near_52_week_low": b["near_low"],
        "strongest_sectors": [{"sector": s["label"], "change_pct": s["change"]} for s in secs[::-1][:2]],
        "weakest_sectors": [{"sector": s["label"], "change_pct": s["change"]} for s in secs[:2]],
        "top_gainers": [{"name": x["name"], "change_pct": x["change"]} for x in up],
        "top_losers": [{"name": x["name"], "change_pct": x["change"]} for x in down],
    }


def _fact_numbers(facts: Any) -> set[float]:
    out: set[float] = set()
    if isinstance(facts, dict):
        for v in facts.values():
            out |= _fact_numbers(v)
    elif isinstance(facts, list):
        for v in facts:
            out |= _fact_numbers(v)
    elif isinstance(facts, (int, float)) and not isinstance(facts, bool):
        out.add(abs(round(float(facts), 2)))
    elif isinstance(facts, str):
        # Headlines use grouped figures ("8,000", "1,23,456.78") too.
        # Parse evidence and output identically, or real quoted numbers are
        # incorrectly rejected as inventions.
        for m in _NUM_IN_TEXT.findall(facts):
            out.add(abs(round(float(m.replace(",", "")), 2)))
    return out


_NUM_IN_TEXT = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def grounded(text: str, facts: dict) -> bool:
    """Every number in the text is a fact number (or one rounded from it)."""
    # the names of the measures themselves: "200-day average", "52-week high"
    allowed = _fact_numbers(facts) | {200.0, 52.0, 50.0}
    for tok in _NUM_IN_TEXT.findall(text):
        v = abs(float(tok.replace(",", "")))
        if not any(abs(v - a) < 0.051 or (v == round(a) and abs(v - a) < 0.5) for a in allowed):
            return False
    return True


def fallback_brief(f: dict) -> str:
    """The same facts, in sentences code can write on its own."""
    parts: list[str] = []
    adv, dec = f["advancers"], f["decliners"]
    if adv == dec:
        parts.append(f"Advancers and decliners were level at {adv} each across the "
                     f"{f['stocks_counted']} largest companies.")
    else:
        lead, a, b = ("Advancers", adv, dec) if adv > dec else ("Decliners", dec, adv)
        parts.append(f"{lead} led {a} to {b} across the {f['stocks_counted']} largest companies.")
    if f["strongest_sectors"] and f["weakest_sectors"]:
        s, w = f["strongest_sectors"][0], f["weakest_sectors"][0]
        parts.append(f"{s['sector']} was the strongest sector at {s['change_pct']:+.2f}%, "
                     f"{w['sector']} the weakest at {w['change_pct']:+.2f}%.")
    return " ".join(parts)
