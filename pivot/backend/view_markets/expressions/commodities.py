"""View Markets — Phase 3 MCX commodity universe + leverage-note convention.

Commodities became TRADEABLE via register-not-execute on 2026-06-29 (the
"MCX research-only" hard-block was lifted across the option-chain / safety /
option-strategies / paper-routing / instrument-master / workflow-action layers).
Phase 3 was originally built under the old assumption, so the expression engine
gets this small foundation module the commodity archetypes + builders code to.

What this module owns (pure data + light, lazy helpers — importing it is
side-effect-free):

  * :data:`MCX_COMMODITIES` — the liquid MCX F&O commodity SEED (symbol → group /
    options-availability / mini-variant). The authoritative universe + lot sizes
    come from the live MCX instrument-master dump (``kite.instruments("MCX")``);
    this seed is the documented fallback + the keyword/normalisation source, the
    same shape ``honest_short`` keeps its index/foreign seeds in. We never
    fabricate a commodity that doesn't list.
  * :func:`is_commodity` / :func:`is_fno` / :func:`commodity_group` /
    :func:`normalize_commodity` — classify + resolve a free-text commodity ask to
    its DIRECT MCX symbol (``GOLD``, ``SILVER``, ``CRUDEOIL`` …), kept DISTINCT
    from the listed ETF proxies (``GOLDBEES`` / ``SILVERBEES``).
  * :func:`lot_size` — delegates to the instrument master (the only legal source);
    ``None`` on a miss — never a fabricated lot.
  * :data:`LEVERAGE_NOTE` + :func:`leverage_note` — the commodity leverage-risk
    note CONVENTION: every commodity expression MUST carry a leverage note in
    ``config.warnings`` + its ``risk_profile`` disclosure, and MUST NEVER
    auto-size (register-not-execute; the user confirms the lots).
  * :func:`price_history_available` — the HONEST data gate. The pairs / basket
    data layer (``core.data.historical.get_close_dict`` →
    ``market.yfinance_service.fetch_multi_symbol``) is yfinance-``.NS``/NSE-only:
    DIRECT MCX commodity futures have NO aligned daily OHLCV there, so a commodity
    PAIR / RELATIVE construct on raw MCX legs must degrade to "construct-only,
    backtest-unavailable" rather than fabricate a cointegration. The listed
    gold/silver ETF proxies DO have history → those backtest. This function tells
    a builder which leg can be backtested.

No DB connect / network / scheduler at import time; ``lot_size`` lazily imports
the instrument master only when called.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datetime import date

    from sqlalchemy.orm import Session

CommodityGroup = Literal["energy", "bullion", "base_metal"]


@dataclass(frozen=True)
class CommoditySpec:
    """One MCX F&O commodity — the DATA unit the engine classifies on.

    ``symbol`` is the MCX underlying (e.g. ``CRUDEOIL``); ``has_options`` is
    whether MCX lists options on it (most of the liquid set do — minis are
    futures-only); ``mini_of`` points a "mini" contract at its full-size sibling
    (``GOLDM`` → ``GOLD``) so the engine can offer a smaller-lot variant.
    """

    symbol: str
    name: str
    group: CommodityGroup
    has_options: bool = True
    mini_of: Optional[str] = None


# ── The liquid MCX F&O commodity seed (fallback + normalisation source) ───────
# Authoritative universe + lot sizes = the live MCX dump; this is the documented
# seed. Minis (GOLDM/SILVERM/CRUDEOILM/NATGASMINI) are futures-first — the engine
# can size a smaller leg with them; options route to the full-size sibling.
_COMMODITIES: tuple[CommoditySpec, ...] = (
    # Energy
    CommoditySpec("CRUDEOIL", "Crude Oil", "energy", has_options=True),
    CommoditySpec("CRUDEOILM", "Crude Oil Mini", "energy", has_options=False,
                  mini_of="CRUDEOIL"),
    CommoditySpec("NATURALGAS", "Natural Gas", "energy", has_options=True),
    CommoditySpec("NATGASMINI", "Natural Gas Mini", "energy", has_options=False,
                  mini_of="NATURALGAS"),
    # Bullion
    CommoditySpec("GOLD", "Gold", "bullion", has_options=True),
    CommoditySpec("GOLDM", "Gold Mini", "bullion", has_options=False,
                  mini_of="GOLD"),
    CommoditySpec("SILVER", "Silver", "bullion", has_options=True),
    CommoditySpec("SILVERM", "Silver Mini", "bullion", has_options=False,
                  mini_of="SILVER"),
    # Base metals
    CommoditySpec("COPPER", "Copper", "base_metal", has_options=True),
    CommoditySpec("ZINC", "Zinc", "base_metal", has_options=True),
    CommoditySpec("ALUMINIUM", "Aluminium", "base_metal", has_options=True),
    CommoditySpec("LEAD", "Lead", "base_metal", has_options=False),
    CommoditySpec("NICKEL", "Nickel", "base_metal", has_options=False),
)

#: Public registry keyed by MCX symbol (frozen-by-convention; do not mutate).
MCX_COMMODITIES: dict[str, CommoditySpec] = {c.symbol: c for c in _COMMODITIES}

# Free-text → DIRECT MCX symbol. Keys are normalised (upper, alnum-only). Kept
# DISTINCT from the ETF proxies: "gold" → GOLD (the MCX future), NOT GOLDBEES.
_ALIASES: dict[str, str] = {
    "CRUDE": "CRUDEOIL",
    "CRUDEOIL": "CRUDEOIL",
    "OIL": "CRUDEOIL",
    "WTI": "CRUDEOIL",
    "BRENT": "CRUDEOIL",      # India lists WTI-style crude; Brent maps to the same MCX leg
    "NATURALGAS": "NATURALGAS",
    "NATGAS": "NATURALGAS",
    "NG": "NATURALGAS",
    "GOLD": "GOLD",
    "GOLDM": "GOLDM",
    "SILVER": "SILVER",
    "SILVERM": "SILVERM",
    "COPPER": "COPPER",
    "ZINC": "ZINC",
    "ALUMINIUM": "ALUMINIUM",
    "ALUMINUM": "ALUMINIUM",
    "LEAD": "LEAD",
    "NICKEL": "NICKEL",
}

# ── Direct-MCX vs ETF-proxy bullion legs (the two routes the spec distinguishes).
# DIRECT legs are leveraged MCX futures with NO aligned OHLCV in the pairs/basket
# data layer (→ construct-only). The ETF proxies are cash NSE ETFs WITH yfinance
# history (→ backtestable). A bullion ratio / sleeve picks its route deliberately.
GOLD_SILVER_RATIO_LEGS: tuple[str, str] = ("GOLD", "SILVER")
GOLD_SILVER_ETF_PROXY_LEGS: tuple[str, str] = ("GOLDBEES", "SILVERBEES")
#: Direct MCX bullion symbol → its listed ETF proxy (the backtestable route).
BULLION_ETF_PROXY: dict[str, str] = {
    "GOLD": "GOLDBEES",
    "GOLDM": "GOLDBEES",
    "SILVER": "SILVERBEES",
    "SILVERM": "SILVERBEES",
}

# ── Leverage-risk note CONVENTION ─────────────────────────────────────────────
# Commodities are leveraged. Every commodity expression MUST surface this note in
# ``config.warnings`` and fold it into the ``risk_profile`` disclosure, and MUST
# NEVER auto-size — register-not-execute (the user confirms the lots in-broker).
LEVERAGE_NOTE: str = (
    "Commodity (MCX) leg — LEVERAGED via futures/options margin (a small SPAN + "
    "exposure deposit controls a large notional). Tradeable via register-not-"
    "execute: Pivot arms the structure and you confirm the lots in your broker "
    "app — it is NEVER auto-sized. Mind the margin/roll/overnight-gap risk."
)


def _norm(symbol: str) -> str:
    """Upper-case + keep alphanumerics (``crude oil`` → ``CRUDEOIL`` key space)."""
    return "".join(ch for ch in (symbol or "").upper() if ch.isalnum())


def _resolve_token(token: str) -> Optional[str]:
    """Exact (whole-token) resolution of a normalised token to an MCX symbol."""
    if token in MCX_COMMODITIES:
        return token
    return _ALIASES.get(token)


def normalize_commodity(text: str) -> Optional[str]:
    """Resolve a free-text commodity ask to its DIRECT MCX symbol, or ``None``.

    Matches on WORD boundaries (never an arbitrary substring), so the listed ETF
    proxies stay distinct: ``"gold"`` → ``GOLD`` but ``"GOLDBEES"`` → ``None``.
    Resolution order: the whole alnum-joined string (``"crude oil"`` →
    ``CRUDEOIL``, ``"wti"`` → ``CRUDEOIL``) → a single word token → an adjacent
    two-word window (``"natural gas"`` → ``NATURALGAS``). Returns the MCX future
    symbol — NOT an ETF proxy — so callers choose the direct vs proxy route.
    """
    if not text:
        return None
    joined = _norm(text)
    if not joined:
        return None
    hit = _resolve_token(joined)
    if hit is not None:
        return hit
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", str(text).upper()) if t]
    for tok in tokens:
        hit = _resolve_token(tok)
        if hit is not None:
            return hit
    # Adjacent two-word window so a split multi-word name still resolves
    # ("NATURAL" + "GAS" → NATURALGAS) without matching inside one long token.
    for i in range(len(tokens) - 1):
        hit = _resolve_token(tokens[i] + tokens[i + 1])
        if hit is not None:
            return hit
    return None


def is_commodity(symbol: str) -> bool:
    """True when ``symbol`` resolves to a known MCX commodity (direct or alias)."""
    return normalize_commodity(symbol) is not None


def is_fno(symbol: str) -> bool:
    """True when the commodity lists MCX OPTIONS (the option archetypes need this).

    Minis (futures-only) and any unknown symbol → ``False``. The full-size
    bullion/energy/base-metal commodities carry liquid monthly options.
    """
    resolved = normalize_commodity(symbol)
    spec = MCX_COMMODITIES.get(resolved) if resolved else None
    return bool(spec and spec.has_options)


def commodity_group(symbol: str) -> Optional[CommodityGroup]:
    """The macro group (``energy`` / ``bullion`` / ``base_metal``) or ``None``."""
    resolved = normalize_commodity(symbol)
    spec = MCX_COMMODITIES.get(resolved) if resolved else None
    return spec.group if spec else None


def options_underlying(symbol: str) -> Optional[str]:
    """The symbol to resolve an OPTION chain on for ``symbol``.

    A mini (``GOLDM``) has no options → route to its full-size sibling
    (``GOLD``); a full-size commodity returns itself; an unknown symbol → ``None``
    (never a fabricated chain target).
    """
    resolved = normalize_commodity(symbol)
    spec = MCX_COMMODITIES.get(resolved) if resolved else None
    if spec is None:
        return None
    if spec.has_options:
        return spec.symbol
    return spec.mini_of  # the option-bearing full-size sibling


def etf_proxy(symbol: str) -> Optional[str]:
    """The listed ETF proxy for a bullion commodity (the backtestable route).

    ``GOLD``/``GOLDM`` → ``GOLDBEES``, ``SILVER``/``SILVERM`` → ``SILVERBEES``.
    Energy/base-metals have no liquid retail ETF proxy → ``None`` (don't pretend).
    """
    resolved = normalize_commodity(symbol)
    return BULLION_ETF_PROXY.get(resolved) if resolved else None


def price_history_available(symbol: str) -> bool:
    """Honest gate: can this leg be BACKTESTED on the pairs/basket data layer?

    The pairs / basket history layer (``core.data.historical.get_close_dict`` →
    yfinance ``.NS``) carries NSE equities/ETFs only — DIRECT MCX commodity
    futures have NO aligned daily OHLCV there. So a direct MCX commodity leg
    returns ``False`` (the builder degrades to construct-only,
    backtest-unavailable — never a fabricated cointegration), while a listed ETF
    proxy (GOLDBEES/SILVERBEES) or any non-commodity NSE symbol returns ``True``.
    """
    if not is_commodity(symbol):
        return True  # an NSE equity/ETF leg — the data layer covers it
    # A bullion ETF proxy passed through as the symbol is NSE-listed → available.
    return _norm(symbol) in {_norm(p) for p in BULLION_ETF_PROXY.values()}


def lot_size(
    db: "Session", symbol: str, expiry: Optional["date"] = None,
) -> Optional[int]:
    """Contract lot size from the instrument master (the ONLY legal source).

    Delegates to ``instrument_master.get_lot_size`` (lazy import). Returns
    ``None`` honestly when the symbol/expiry isn't in the master — never a
    fabricated lot. The option-bearing sibling is resolved first so a mini routes
    to a real chain's lot.
    """
    from backend.market import instrument_master

    target = normalize_commodity(symbol) or symbol
    try:
        return instrument_master.get_lot_size(db, target, expiry)
    except Exception:  # pragma: no cover - master-shape defensive
        return None


def leverage_note(symbol: Optional[str] = None) -> str:
    """The commodity leverage-risk note (the convention). ``symbol`` is accepted
    for a future per-commodity refinement; today it returns the canonical
    :data:`LEVERAGE_NOTE` every commodity expression must carry."""
    return LEVERAGE_NOTE


__all__ = [
    "CommodityGroup",
    "CommoditySpec",
    "MCX_COMMODITIES",
    "GOLD_SILVER_RATIO_LEGS",
    "GOLD_SILVER_ETF_PROXY_LEGS",
    "BULLION_ETF_PROXY",
    "LEVERAGE_NOTE",
    "normalize_commodity",
    "is_commodity",
    "is_fno",
    "commodity_group",
    "options_underlying",
    "etf_proxy",
    "price_history_available",
    "lot_size",
    "leverage_note",
]
