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
from typing import Literal, Optional


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


# Aliases the user might type. Maps to the canonical SectorName above.
_SECTOR_ALIASES: dict[str, SectorName] = {
    "steel": "steel",
    "metals": "metals",
    "metal": "metals",
    "mining": "metals",
    "banking": "banking",
    "banks": "banking",
    "private banks": "private_bank",
    "private banking": "private_bank",
    "psu banks": "psu_bank",
    "psu banking": "psu_bank",
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
    """
    if not raw:
        return None
    key = raw.strip().lower()
    return _SECTOR_ALIASES.get(key)


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
    """
    normalized = normalize_sector(sector) if sector else None
    rows: list[_SectorEntry] = list(_UNIVERSE)

    if normalized is not None:
        if normalized == "metals":
            # Promote steel into the metals bucket for "metals" queries.
            rows = [r for r in rows if r.sector in ("metals", "steel")]
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
