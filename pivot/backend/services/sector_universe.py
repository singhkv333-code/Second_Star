"""Static sector universe for fetch.screener.

We back the v1 screener with a hand-curated list of NIFTY sectoral
index constituents and approximate market caps (₹ crore, mid-2025
snapshots). This is good enough for prompts like *"top 10 steel
stocks by market cap"* — the rankings rarely change month-to-month
and absolute precision isn't needed for a workflow that's about to
let the user review and edit.

Data sources / refresh policy:
  - Constituents: NIFTY sectoral index official lists.
  - Market caps: rounded to nearest ₹100 cr, refreshed manually when
    drift becomes meaningful. Live mcap fetching is deferred to v2.
  - When yfinance is reachable AND `live_mcap=True`, the screener can
    overlay live caps on top of this static base — implemented but
    off by default since yfinance calls add ~1s/symbol.

Why static and not live: at workflow-build time, the model needs the
universe to fill the symbols list; speed beats freshness here. The
*orders* still execute at live prices, so a slightly stale ranking
doesn't translate into a slightly stale fill.

Sector taxonomy intentionally short and coarse — the user thinks in
NSE-friendly buckets ("steel", "banking", "IT") not GICS subindustries.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, get_args


SectorName = Literal[
    "banking", "it", "auto", "pharma", "fmcg", "energy",
    "metals", "steel", "cement", "infra", "realty", "defence",
    "telecom", "consumer_durables", "media", "chemicals",
    "psu_bank", "private_bank", "financial_services",
]


@dataclass(frozen=True)
class _SectorEntry:
    symbol: str
    sector: SectorName
    mcap_cr: int                    # market cap in ₹ crore (approx)
    name: Optional[str] = None      # display name; defaults to symbol


# ── The universe ────────────────────────────────────────────────────
#
# Sorted-anywhere ordering — the screener sorts on demand. Caps are
# rounded to nearest ₹500 cr to make stale-data lies less likely.

_UNIVERSE: list[_SectorEntry] = [
    # ── Steel + Metals ──────────────────────────────────────────────
    _SectorEntry("TATASTEEL",     "steel",   162000, "Tata Steel"),
    _SectorEntry("JSWSTEEL",      "steel",   232000, "JSW Steel"),
    _SectorEntry("JINDALSTEL",    "steel",    91000, "Jindal Steel & Power"),
    _SectorEntry("SAIL",          "steel",    52000, "Steel Authority of India"),
    _SectorEntry("NMDC",          "steel",    63000, "NMDC"),
    _SectorEntry("APLAPOLLO",     "steel",    42000, "APL Apollo Tubes"),
    _SectorEntry("WELCORP",       "steel",    16000, "Welspun Corp"),
    _SectorEntry("RATNAMANI",     "steel",    18500, "Ratnamani Metals & Tubes"),
    _SectorEntry("LLOYDSME",      "steel",     6000, "Lloyds Metals"),
    _SectorEntry("KIRLOSKARFER",  "steel",     3500, "Kirloskar Ferrous"),
    # Wider metals (aluminium, copper, mining)
    _SectorEntry("HINDALCO",      "metals",  155000, "Hindalco Industries"),
    _SectorEntry("VEDL",          "metals",  168000, "Vedanta"),
    _SectorEntry("HINDZINC",      "metals",  220000, "Hindustan Zinc"),
    _SectorEntry("COALINDIA",     "metals",  290000, "Coal India"),
    _SectorEntry("NATIONALUM",    "metals",    37000, "National Aluminium"),
    _SectorEntry("HINDCOPPER",    "metals",    33000, "Hindustan Copper"),

    # ── Banking ────────────────────────────────────────────────────
    _SectorEntry("HDFCBANK",      "private_bank", 1320000, "HDFC Bank"),
    _SectorEntry("ICICIBANK",     "private_bank",  920000, "ICICI Bank"),
    _SectorEntry("KOTAKBANK",     "private_bank",  370000, "Kotak Mahindra Bank"),
    _SectorEntry("AXISBANK",      "private_bank",  370000, "Axis Bank"),
    _SectorEntry("INDUSINDBK",    "private_bank",  108000, "IndusInd Bank"),
    _SectorEntry("BANDHANBNK",    "private_bank",   31000, "Bandhan Bank"),
    _SectorEntry("SBIN",          "psu_bank",      710000, "State Bank of India"),
    _SectorEntry("BANKBARODA",    "psu_bank",      130000, "Bank of Baroda"),
    _SectorEntry("PNB",           "psu_bank",      138000, "Punjab National Bank"),
    _SectorEntry("CANBK",         "psu_bank",       95000, "Canara Bank"),
    _SectorEntry("UNIONBANK",     "psu_bank",       85000, "Union Bank of India"),

    # ── IT ─────────────────────────────────────────────────────────
    _SectorEntry("TCS",           "it", 1485000, "Tata Consultancy Services"),
    _SectorEntry("INFY",          "it",  790000, "Infosys"),
    _SectorEntry("HCLTECH",       "it",  470000, "HCL Technologies"),
    _SectorEntry("WIPRO",         "it",  280000, "Wipro"),
    _SectorEntry("LTIM",          "it",  150000, "LTIMindtree"),
    _SectorEntry("TECHM",         "it",  155000, "Tech Mahindra"),
    _SectorEntry("PERSISTENT",    "it",   85000, "Persistent Systems"),
    _SectorEntry("COFORGE",       "it",   55000, "Coforge"),
    _SectorEntry("MPHASIS",       "it",   54000, "Mphasis"),

    # ── Auto ───────────────────────────────────────────────────────
    _SectorEntry("MARUTI",        "auto", 380000, "Maruti Suzuki"),
    _SectorEntry("M&M",           "auto", 360000, "Mahindra & Mahindra"),
    _SectorEntry("TATAMOTORS",    "auto", 320000, "Tata Motors"),
    _SectorEntry("BAJAJ-AUTO",    "auto", 300000, "Bajaj Auto"),
    _SectorEntry("EICHERMOT",     "auto", 130000, "Eicher Motors"),
    _SectorEntry("HEROMOTOCO",    "auto", 110000, "Hero MotoCorp"),
    _SectorEntry("TVSMOTOR",      "auto", 130000, "TVS Motor"),
    _SectorEntry("ASHOKLEY",      "auto",  74000, "Ashok Leyland"),

    # ── Pharma ─────────────────────────────────────────────────────
    _SectorEntry("SUNPHARMA",     "pharma", 415000, "Sun Pharmaceutical"),
    _SectorEntry("DRREDDY",       "pharma", 110000, "Dr. Reddy's Laboratories"),
    _SectorEntry("CIPLA",         "pharma", 130000, "Cipla"),
    _SectorEntry("DIVISLAB",      "pharma", 165000, "Divi's Laboratories"),
    _SectorEntry("LUPIN",         "pharma",  93000, "Lupin"),
    _SectorEntry("AUROPHARMA",    "pharma",  77000, "Aurobindo Pharma"),
    _SectorEntry("BIOCON",        "pharma",  47000, "Biocon"),

    # ── FMCG ───────────────────────────────────────────────────────
    _SectorEntry("HINDUNILVR",    "fmcg",  600000, "Hindustan Unilever"),
    _SectorEntry("ITC",           "fmcg",  570000, "ITC"),
    _SectorEntry("NESTLEIND",     "fmcg",  240000, "Nestle India"),
    _SectorEntry("BRITANNIA",     "fmcg",  130000, "Britannia Industries"),
    _SectorEntry("DABUR",         "fmcg",   91000, "Dabur India"),
    _SectorEntry("MARICO",        "fmcg",   85000, "Marico"),
    _SectorEntry("GODREJCP",      "fmcg",  113000, "Godrej Consumer"),
    _SectorEntry("COLPAL",        "fmcg",   77000, "Colgate-Palmolive India"),

    # ── Energy / Oil & Gas ─────────────────────────────────────────
    _SectorEntry("RELIANCE",      "energy", 1850000, "Reliance Industries"),
    _SectorEntry("ONGC",          "energy",  300000, "Oil & Natural Gas Corp"),
    _SectorEntry("IOC",           "energy",  220000, "Indian Oil Corp"),
    _SectorEntry("NTPC",          "energy",  340000, "NTPC"),
    _SectorEntry("POWERGRID",     "energy",  290000, "Power Grid Corporation"),
    _SectorEntry("GAIL",          "energy",  120000, "GAIL India"),
    _SectorEntry("BPCL",          "energy",  130000, "Bharat Petroleum"),
    _SectorEntry("HINDPETRO",     "energy",   85000, "Hindustan Petroleum"),

    # ── Cement ─────────────────────────────────────────────────────
    _SectorEntry("ULTRACEMCO",    "cement", 290000, "UltraTech Cement"),
    _SectorEntry("SHREECEM",      "cement",  93000, "Shree Cement"),
    _SectorEntry("ACC",           "cement",  39000, "ACC"),
    _SectorEntry("AMBUJACEM",     "cement", 130000, "Ambuja Cements"),
    _SectorEntry("DALBHARAT",     "cement",  39000, "Dalmia Bharat"),

    # ── Defence ────────────────────────────────────────────────────
    _SectorEntry("HAL",           "defence", 280000, "Hindustan Aeronautics"),
    _SectorEntry("BEL",           "defence", 230000, "Bharat Electronics"),
    _SectorEntry("BHEL",          "defence",  90000, "Bharat Heavy Electricals"),
    _SectorEntry("MAZDOCK",       "defence",  78000, "Mazagon Dock Shipbuilders"),
    _SectorEntry("COCHINSHIP",    "defence",  47000, "Cochin Shipyard"),

    # ── Telecom ────────────────────────────────────────────────────
    _SectorEntry("BHARTIARTL",    "telecom", 920000, "Bharti Airtel"),
    _SectorEntry("VODAFONEIDEA",  "telecom",  53000, "Vodafone Idea"),
    _SectorEntry("INDUSTOWER",    "telecom",  91000, "Indus Towers"),
]


# ── PSU (govt-owned) membership — spans MULTIPLE sectors ────────────
#
# "PSU" is an ownership tag, not a sector: only `psu_bank` carries it in the
# `SectorName` taxonomy above, but real PSUs also sit in `energy` (ONGC, OIL,
# IOC, NTPC, POWERGRID, GAIL, BPCL, HINDPETRO), `metals`/`steel` (COALINDIA,
# SAIL, NMDC), and `defence` (HAL, BEL, BHEL, MAZDOCK, COCHINSHIP — all
# govt-owned). A caller that only checks `"psu" in sector` (matching just
# `psu_bank`) silently keeps every non-bank PSU in an "exclude PSU" ask —
# the confirmed bug this set exists to close (build_strategy's exclusion
# filter let ONGC/IOC/COALINDIA through an explicit "no PSU exposure" ask).
# Kept here (not in strategy_builder / fundamentals_screen) so both callers
# share one definition instead of drifting.
_PSU_SYMBOLS: frozenset[str] = frozenset({
    # psu_bank
    "SBIN", "BANKBARODA", "PNB", "CANBK", "UNIONBANK",
    # energy PSUs
    "ONGC", "OIL", "IOC", "NTPC", "POWERGRID", "GAIL", "BPCL", "HINDPETRO",
    # metals/steel PSUs
    "COALINDIA", "SAIL", "NMDC",
    # defence PSUs (govt-owned manufacturers, not private defence contractors)
    "HAL", "BEL", "BHEL", "MAZDOCK", "COCHINSHIP",
})


def is_psu(symbol: str) -> bool:
    """True when ``symbol`` is a known government-owned (PSU) company,
    regardless of which sector bucket it lives in. Conservative: only the
    curated set above returns True; an unrecognised symbol returns False
    rather than guessing."""
    return (symbol or "").strip().upper() in _PSU_SYMBOLS


# Aliases the user might type. Maps to the canonical SectorName above.
_CANONICAL_SECTORS: frozenset[str] = frozenset(get_args(SectorName))
"""Every canonical SectorName — `normalize_sector` maps each to itself so a
caller passing the taxonomy's own name is never read as 'no filter'."""

_SECTOR_ALIASES: dict[str, SectorName] = {
    "steel": "steel",
    "metals": "metals",
    "metal": "metals",
    "mining": "metals",
    "banking": "banking",
    "banks": "banking",
    "bank": "banking",
    # Singular/“sector” phrasings resolve too — "private bank" silently fell
    # through to the broad cross-sector pool while "private banks" worked
    # (2026-07-17 eval, B12: a private-bank ask built BRITANNIA/HINDZINC/TCS).
    "private bank": "private_bank",
    "private banks": "private_bank",
    "private banking": "private_bank",
    "private sector bank": "private_bank",
    "private sector banks": "private_bank",
    "private sector banking": "private_bank",
    "psu bank": "psu_bank",
    "psu banks": "psu_bank",
    "psu banking": "psu_bank",
    "public sector bank": "psu_bank",
    "public sector banks": "psu_bank",
    "financial": "financial_services",
    "financial services": "financial_services",
    "finance": "financial_services",
    "it": "it",
    "tech": "it",
    "technology": "it",
    "software": "it",
    "auto": "auto",
    "automotive": "auto",
    "automobile": "auto",
    "pharma": "pharma",
    "pharmaceutical": "pharma",
    "pharmaceuticals": "pharma",
    "healthcare": "pharma",
    "fmcg": "fmcg",
    "consumer staples": "fmcg",
    "consumer goods": "fmcg",
    "energy": "energy",
    "oil": "energy",
    "oil and gas": "energy",
    "oil & gas": "energy",
    "power": "energy",
    "utilities": "energy",
    "cement": "cement",
    "cements": "cement",
    "defence": "defence",
    "defense": "defence",
    "infra": "infra",
    "infrastructure": "infra",
    "realty": "realty",
    "real estate": "realty",
    "telecom": "telecom",
    "telecommunications": "telecom",
}


def normalize_sector(raw: str) -> Optional[SectorName]:
    """Map a free-form user sector phrase to a canonical SectorName.
    Returns None if the phrase isn't recognised — caller can either
    ask the user or fall back to no filter.

    A CANONICAL name always maps to itself. This used to consult the alias
    dict only, so the six underscored names (private_bank, psu_bank,
    financial_services, consumer_durables, media, chemicals) returned None —
    and since `query_screener` treats None as "no sector filter", asking it for
    sector="private_bank" silently returned the WHOLE universe by market cap
    rather than nothing. That is how a private-bank basket came back holding
    HINDZINC and TCS (2026-07-17 eval, B12). Underscores/spaces/hyphens are
    interchangeable so "private bank" and "private_bank" behave the same.
    """
    if not raw:
        return None
    key = raw.strip().lower()
    if key in _CANONICAL_SECTORS:
        return key  # type: ignore[return-value]
    hit = _SECTOR_ALIASES.get(key)
    if hit is not None:
        return hit
    # "private bank" ⇄ "private_bank" ⇄ "private-bank"
    flat = key.replace("-", " ").replace("_", " ").strip()
    if flat in _SECTOR_ALIASES:
        return _SECTOR_ALIASES[flat]
    under = flat.replace(" ", "_")
    if under in _CANONICAL_SECTORS:
        return under  # type: ignore[return-value]
    return None


# ── Theme → sector(s) mapping ──────────────────────────────────────
#
# Themes the user types ("AI stocks", "EV plays", "green energy") that
# have no clean 1:1 sector in the static universe. Each theme maps to
# one or more canonical sectors and a confidence label:
#   - "exact"        → the theme IS the sector under a different name
#                      (e.g. "semiconductors" ≈ IT in India today)
#   - "approximate"  → caller should disclose the mapping to the user
#                      because reasonable people would map it differently
#                      (e.g. "AI" → IT misses some non-IT plays)
#
# Caller contract: if `confidence == "approximate"`, the chat layer
# should mention the mapping ("I'll use IT, the closest sector") and
# accept user pushback on the next turn rather than silently routing.
@dataclass(frozen=True)
class ThemeMapping:
    sectors: tuple[SectorName, ...]
    confidence: Literal["exact", "approximate"]
    note: str


_THEME_ALIASES: dict[str, ThemeMapping] = {
    # AI / ML / data — Indian listed pure-plays barely exist; map to IT
    # because that's where the revenue exposure lives, but flag it.
    "ai": ThemeMapping(
        ("it",), "approximate",
        "no pure-play AI sector on NSE; using IT as the closest proxy",
    ),
    "artificial intelligence": ThemeMapping(
        ("it",), "approximate",
        "no pure-play AI sector on NSE; using IT as the closest proxy",
    ),
    "ml": ThemeMapping(
        ("it",), "approximate", "ML mapped to IT (no pure-play sector)",
    ),
    "machine learning": ThemeMapping(
        ("it",), "approximate", "ML mapped to IT (no pure-play sector)",
    ),
    "data": ThemeMapping(
        ("it",), "approximate", "data theme mapped to IT",
    ),
    # EV — auto sector covers Tata Motors / M&M / TVS, all of which have
    # meaningful EV revenue, but pure-play EVs are rare.
    "ev": ThemeMapping(
        ("auto",), "approximate",
        "EV theme covered by auto sector top names (Tata Motors, M&M, TVS)",
    ),
    "electric vehicles": ThemeMapping(
        ("auto",), "approximate",
        "EV theme covered by auto sector top names",
    ),
    # Green / clean energy → energy sector (NTPC, Power Grid, ONGC) is
    # an imperfect proxy. The user might mean only renewables.
    "green": ThemeMapping(
        ("energy",), "approximate",
        "no dedicated renewables sector; using energy",
    ),
    "green energy": ThemeMapping(
        ("energy",), "approximate",
        "no dedicated renewables sector; using energy",
    ),
    "clean energy": ThemeMapping(
        ("energy",), "approximate",
        "no dedicated renewables sector; using energy",
    ),
    "renewable": ThemeMapping(
        ("energy",), "approximate",
        "no dedicated renewables sector; using energy",
    ),
    "renewables": ThemeMapping(
        ("energy",), "approximate",
        "no dedicated renewables sector; using energy",
    ),
    # Semiconductors — almost no pure plays; IT is the sole available
    # bucket today. Approximate, not exact.
    "semiconductor": ThemeMapping(
        ("it",), "approximate",
        "no pure-play semiconductor sector on NSE",
    ),
    "semiconductors": ThemeMapping(
        ("it",), "approximate",
        "no pure-play semiconductor sector on NSE",
    ),
    "chips": ThemeMapping(
        ("it",), "approximate",
        "no pure-play semiconductor sector on NSE",
    ),
    # Fintech — straddles private banks + IT; default to private_bank
    # as the higher-AUM exposure.
    "fintech": ThemeMapping(
        ("private_bank", "it"), "approximate",
        "fintech spans private banks + IT services in India",
    ),
}


def resolve_theme(raw: str) -> Optional[ThemeMapping]:
    """Map a free-form theme word ('AI', 'EV', 'green energy') to one
    or more canonical sectors with a confidence label.

    Returns None if the word isn't a known theme — the caller should
    then check `normalize_sector` (it might be a sector phrase
    proper) before falling back to ASK_USER. The split is intentional:
    themes are interpretive ("approximately IT") while sectors are
    definitional ("the IT bucket").
    """
    if not raw:
        return None
    return _THEME_ALIASES.get(raw.strip().lower())


# ── Query ──────────────────────────────────────────────────────────

# Umbrella sector -> the concrete sectors that actually carry members.
# A sector named in `SectorName` but absent from `_UNIVERSE` matches nothing
# on a literal filter, so any user-facing bucket that is really a family of
# finer buckets must be listed here or it silently returns an empty universe.
_SECTOR_PROMOTIONS: dict[str, tuple[str, ...]] = {
    "metals": ("metals", "steel"),
    "banking": ("private_bank", "psu_bank"),
    "financial_services": ("private_bank", "psu_bank"),
}


def query_screener(
    *,
    sector: Optional[str] = None,
    mcap_min_cr: Optional[int] = None,
    mcap_max_cr: Optional[int] = None,
    sort_by: Literal["mcap", "symbol"] = "mcap",
    descending: bool = True,
    limit: int = 10,
) -> list[dict[str, object]]:
    """Filter + sort the static universe. Returns a list of dicts:

        [{"symbol": ..., "name": ..., "sector": ..., "mcap_cr": ...}, ...]

    Defaults give "top N by market cap descending" — the most common
    user ask. `sector` accepts canonical names or aliases.

    Note: when sector="metals" the steel subset is included by promotion
    (steel IS a kind of metal in the user's mental model). The reverse
    isn't true — sector="steel" returns ONLY steel, not all metals.
    The same holds for "banking", which spans private + PSU banks.
    """
    normalized = normalize_sector(sector) if sector else None
    rows: list[_SectorEntry] = list(_UNIVERSE)

    if normalized is not None:
        # Umbrella sectors hold no members of their own — every bank in the
        # universe is tagged private_bank or psu_bank, so a "banking" query
        # matched literally returned ZERO rows and the caller fell through to
        # a broad cross-sector pool: a "banking basket" that wasn't banks
        # (found 2026-07-17 — it was silent because the fallback disclosed
        # itself in `assumptions`, which reads as honest while being wrong).
        parents = _SECTOR_PROMOTIONS.get(normalized)
        if parents:
            rows = [r for r in rows if r.sector in parents]
        else:
            rows = [r for r in rows if r.sector == normalized]

    if mcap_min_cr is not None:
        rows = [r for r in rows if r.mcap_cr >= mcap_min_cr]
    if mcap_max_cr is not None:
        rows = [r for r in rows if r.mcap_cr <= mcap_max_cr]

    if sort_by == "mcap":
        rows.sort(key=lambda r: r.mcap_cr, reverse=descending)
    else:
        rows.sort(key=lambda r: r.symbol, reverse=descending)

    rows = rows[: max(1, int(limit))]
    return [
        {
            "symbol": r.symbol,
            "name": r.name or r.symbol,
            "sector": r.sector,
            "mcap_cr": r.mcap_cr,
        }
        for r in rows
    ]


def known_sectors() -> list[str]:
    """All canonical sector names present in the universe — useful
    for surfacing 'I don't know that sector' errors."""
    return sorted({r.sector for r in _UNIVERSE})


def symbol_sector_map() -> dict[str, str]:
    """``{NSE symbol: canonical sector}`` from the curated universe — a
    network-free sector lookup (used by the portfolio engine's sector caps).
    Symbols not present here resolve to ``None`` at the call site."""
    return {r.symbol: r.sector for r in _UNIVERSE}


# ── The REAL listed universe (symbol existence + name + mcap) ──────
#
# `_UNIVERSE` above is 80 hand-curated names across 12 sectors — a
# ranking convenience, NOT the market. Treating it as the universe is
# what let a caller pin "BERGERPAINT"/"HPCL" (neither is a live NSE
# symbol; the real ones are BERGEPAINT and HINDPETRO) and have them
# ship as basket legs with a real ₹ slice: absent from an 80-name list
# is not evidence of anything.
#
# `mc.companies.nse_symbol` IS authoritative — ~4,600 verified NSE
# symbols — so existence questions resolve against it and nothing else.
# Sector is the awkward part: pivot_enrich carries only yfinance's
# coarse 11-bucket taxonomy ("Financial Services" can't distinguish a
# private bank from a PSU bank), so canonical sector still comes from
# the curated map when it has an opinion, with the coarse label as a
# clearly-marked fallback. Callers that need a canonical sector must
# check `sector_is_canonical` rather than assume.

_NSE_UNIVERSE_TTL_SECONDS = 3600.0
_nse_universe_cache: Optional[dict[str, dict]] = None
_nse_universe_cached_at: float = 0.0


def _load_nse_universe() -> dict[str, dict]:
    """Build ``{SYMBOL: row}`` by merging the two source DBs. Raises on a
    DB failure — the caller decides how to degrade."""
    from sqlalchemy import text as _text

    from backend.database import EnrichSessionLocal, FinancialsSessionLocal

    fin = FinancialsSessionLocal()
    try:
        sym_rows = fin.execute(
            _text(
                "SELECT sc_id, upper(nse_symbol) AS sym, company_name "
                "FROM mc.companies "
                "WHERE nse_symbol IS NOT NULL AND nse_symbol <> ''"
            )
        ).fetchall()
    finally:
        fin.close()

    enrich: dict[str, dict] = {}
    if EnrichSessionLocal is not None:
        edb = EnrichSessionLocal()
        try:
            for r in edb.execute(
                _text(
                    "SELECT sc_id, long_name, sector, market_cap "
                    "FROM enrich.v_company_enriched"
                )
            ).fetchall():
                m = r._mapping
                enrich[m["sc_id"]] = {
                    "name": m["long_name"],
                    "sector": (m["sector"] or "").strip(),
                    # enrich stores market cap ABSOLUTE; the builder thinks in ₹ cr.
                    "mcap_cr": (float(m["market_cap"]) / 1e7
                                if m["market_cap"] else None),
                }
        except Exception:  # noqa: BLE001 — enrichment is optional, existence isn't
            enrich = {}
        finally:
            edb.close()

    curated_sector = symbol_sector_map()
    out: dict[str, dict] = {}
    for row in sym_rows:
        m = row._mapping
        sym = m["sym"]
        if not sym:
            continue
        e = enrich.get(m["sc_id"]) or {}
        canon = curated_sector.get(sym)
        out[sym] = {
            "symbol": sym,
            "name": e.get("name") or m["company_name"] or sym,
            "sector": canon or (e.get("sector") or "unknown"),
            "sector_is_canonical": canon is not None,
            "mcap_cr": e.get("mcap_cr"),
        }
    return out


def nse_universe_map(*, refresh: bool = False) -> dict[str, dict]:
    """``{SYMBOL: {symbol, name, sector, sector_is_canonical, mcap_cr}}`` for
    every NSE symbol we can VERIFY is listed (~4,600).

    Membership is the point: a symbol absent from this map is one we cannot
    confirm trades, and money must not be allocated to it. Presence with
    ``mcap_cr=None`` or ``sector="unknown"`` is an honest data gap on a real
    name — a different thing entirely, and not a reason to drop it.

    Process-cached for an hour. On a DB failure this degrades to the curated
    80-name universe, which means EXISTENCE ANSWERS GET WEAKER, not wrong-
    but-confident: callers should treat a miss as "unverified", never as
    "does not exist".
    """
    global _nse_universe_cache, _nse_universe_cached_at
    import time as _time

    now = _time.monotonic()
    if (
        not refresh
        and _nse_universe_cache is not None
        and now - _nse_universe_cached_at < _NSE_UNIVERSE_TTL_SECONDS
    ):
        return _nse_universe_cache

    try:
        loaded = _load_nse_universe()
    except Exception:  # noqa: BLE001
        loaded = {}
    if not loaded:
        # Degrade to the curated list rather than claiming an empty market.
        loaded = {
            r.symbol: {
                "symbol": r.symbol,
                "name": r.name or r.symbol,
                "sector": r.sector,
                "sector_is_canonical": True,
                "mcap_cr": r.mcap_cr,
            }
            for r in _UNIVERSE
        }
    _nse_universe_cache = loaded
    _nse_universe_cached_at = now
    return loaded


def is_listed_symbol(symbol: str) -> bool:
    """True when ``symbol`` is a verified listed NSE symbol. False means
    UNVERIFIED (usually invented/mis-spelled/delisted), not proven absent."""
    return str(symbol or "").replace(".NS", "").strip().upper() in nse_universe_map()


# ── Macro beneficiary tagging (crude up/down, INR weak, etc.) ──────
#
# The static `energy` sector lumps producers and refiners together, but
# for thematic asks ("profits from rising oil") the two groups move in
# OPPOSITE directions — upstream producers benefit when crude rises;
# refiners/marketers (oil marketing companies, OMCs) see margins
# compress because retail fuel prices are politically administered and
# they can't pass through the cost in real time. The same split applies
# in reverse for "profits from falling oil".
#
# This tagging is INTENTIONALLY narrow — only the names where the
# producer-vs-refiner split is unambiguous on NSE. Reliance is omitted
# because it is integrated (upstream + refining + retail + telecom) and
# doesn't cleanly map to either bucket; we don't guess.
#
# Caller contract: if a symbol isn't in either set, do NOT assume it is
# the opposite — it is unclassified. Falling back silently leads to
# IOC-for-rising-crude bugs.

# Upstream crude / gas producers. Sell what they pull out of the ground;
# revenue tracks the realised crude price (lagged + subject to windfall
# tax mechanics, but the directional sign is correct).
_OIL_UPSTREAM_PRODUCERS: tuple[str, ...] = (
    "ONGC",       # Oil & Natural Gas Corporation — flagship upstream PSU
    "OIL",        # Oil India Ltd — the second listed upstream PSU
    # NOTE: Reliance has meaningful upstream (KG-D6) but its consolidated
    #       margin is dominated by refining + retail + Jio. Do not classify
    #       it here. Cairn / Vedanta is the next candidate but the listed
    #       parent (VEDL) is diversified; left out for the same reason.
)

# Oil marketing / refining (OMCs). Buy crude as input, sell retail fuel
# at administered prices → gross refining margin gets squeezed when crude
# rises and expands when crude falls. The literal OPPOSITE direction
# from the upstream producers.
_OIL_REFINERS_MARKETERS: tuple[str, ...] = (
    "IOC",        # Indian Oil Corporation
    "BPCL",       # Bharat Petroleum
    "HINDPETRO",  # Hindustan Petroleum (HPCL)
)


def crude_up_beneficiaries() -> list[str]:
    """Return the NSE symbols whose earnings BENEFIT when crude oil
    prices rise. Upstream producers only — not refiners.

    Used by the planner / drafter when the user asks for an automation
    that "profits from rising oil / crude". Picking IOC/BPCL/HPCL here
    is a textbook backwards trade because their margins compress when
    crude rises. We never include them in this list.
    """
    return list(_OIL_UPSTREAM_PRODUCERS)


def crude_down_beneficiaries() -> list[str]:
    """Return the NSE symbols whose earnings BENEFIT when crude oil
    prices fall. Refiners / oil marketing companies whose gross
    refining margin expands when input costs drop.

    Heavy crude-consuming sectors (paints, aviation, tyres) also
    benefit from falling crude, but they live outside the energy
    bucket — they aren't returned here. TODO: extend with a wider
    `crude_consumers` group when the screener supports cross-sector
    queries.
    """
    return list(_OIL_REFINERS_MARKETERS)


def oil_role(symbol: str) -> Optional[Literal["producer", "refiner"]]:
    """Classify a symbol as upstream producer, downstream refiner, or
    unclassified within the oil & gas value chain.

    Returns None for any energy-sector name that isn't unambiguous
    (Reliance, GAIL, NTPC, POWERGRID etc.) — callers must NOT default
    "unclassified" to either side.
    """
    sym = (symbol or "").strip().upper()
    if sym in _OIL_UPSTREAM_PRODUCERS:
        return "producer"
    if sym in _OIL_REFINERS_MARKETERS:
        return "refiner"
    return None


# Higher-level intent → beneficiary basket. Centralises the
# producer-vs-refiner logic so the planner, the macro hydrators, and
# any future screener intent route through one source of truth.
#
# Intent vocabulary (kept tight on purpose — extending requires a real
# economic rationale, not a guess):
#
#   "crude_up"    → benefits from a rising crude/oil/Brent print
#   "crude_down"  → benefits from a falling crude/oil/Brent print
#
# When the requested intent isn't in the map we return an empty list +
# leave a documented TODO rather than fabricate a basket — caller is
# expected to surface "I can't cleanly map this view to instruments".

_THEMATIC_BENEFICIARIES: dict[str, tuple[str, ...]] = {
    "crude_up": _OIL_UPSTREAM_PRODUCERS,
    "crude_down": _OIL_REFINERS_MARKETERS,
    # TODO(prompt-priorities): add "inr_weak" → IT/pharma exporters,
    #   "rate_cut" → NBFCs/housing/autos, "gold_up" → gold financiers
    #   once the planner is wired to call this helper. Don't enumerate
    #   until the call site is real — leaving stale tags around is
    #   how IOC-for-rising-oil happened in the first place.
}


def beneficiaries_of(intent: str) -> list[str]:
    """Return NSE symbols that benefit from a tagged macro/thematic
    move. Empty list when the intent isn't a known tag — caller MUST
    treat empty as "I don't know" rather than as "no beneficiaries".

    Tag vocabulary today: "crude_up", "crude_down". Anything else
    returns []. Extend `_THEMATIC_BENEFICIARIES` (with a real economic
    rationale, not a guess) before adding tags to a calling site.
    """
    key = (intent or "").strip().lower().replace(" ", "_")
    return list(_THEMATIC_BENEFICIARIES.get(key, ()))
