"""Layman content layer for the Views API.

The Views FE must NEVER see raw enums, statistical jargon (CAAR / t / p /
DSR / MinTRL), or hand-typed thesis prose dense with abbreviations. This
module is the single curated source of de-jargoned, plain-English copy for the
three live views, plus *pure* helper functions that:

  * return curated copy when a view / expression is in the map, and
  * fall back to a SAFE humanized projection otherwise — so any FUTURE view
    can never leak a raw enum or a jargon thesis to the FE.

No numbers are invented here. The clean numbers on screen come from the
backtest payload (see :func:`backend.routers.views`); this module only owns the
WORDS (labels, one-liners, why/risk, badges) and the curated thesis prose whose
every figure has been verified against the live payload.

Disclaimer convention: every curated ``plain_thesis`` ends with the exact
sentence "This is analysis, not financial advice." per the non-negotiables.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# ── view ids (the three live, curated views) ────────────────────────────────

VIEW_MONSOON = "81809245-feeb-4ead-9f35-eb8166757cb7"
VIEW_IT = "4f40f896-0953-4d66-bf6f-1932667b531e"
VIEW_CRUDE = "19f04e99-b704-4166-b99a-697049885d44"

_DISCLAIMER = "This is analysis, not financial advice."


# ── curated per-view plain copy ─────────────────────────────────────────────
#
# headline_tier = which expression tier is the HERO (the number on the card
# must describe THIS expression). ``None`` means "no finished hero yet" (a
# developing view): the FE renders the hero as '—'.

VIEW_COPY: dict[str, dict[str, Any]] = {
    VIEW_MONSOON: {
        "short_title": "A good monsoon lifts rural-economy stocks",
        "description": (
            "A healthy monsoon raises rural incomes, which historically lifts "
            "the companies that sell to rural India — tractors, two-wheelers, "
            "tyres and everyday-consumer brands. The simplest way to play it is "
            "an equal-weighted basket of those names held through the season."
        ),
        "bullets": [
            "What drives it: a good monsoon boosts rural spending power, which "
            "tends to help rural-linked stocks beat the broad market.",
            "How to play it: hold an equal-weighted basket of six rural-economy "
            "stocks through the monsoon season; a market-neutral version hedges "
            "out the Nifty.",
            "Main caveat: only four seasons tested, so the edge is unproven — a "
            "weak or delayed monsoon would undercut it.",
        ],
        "plain_one_liner": (
            "When a healthy monsoon is expected, India's rural-economy stocks "
            "- tractors, two-wheelers, tyres and everyday-consumer brands - "
            "tend to do better than the overall market."
        ),
        "plain_summary": (
            "Rural-linked companies usually outperform the wider market around "
            "a good monsoon season."
        ),
        "plain_thesis": (
            "We think India's annual monsoon creates a repeatable seasonal "
            "pattern: ahead of and during a good monsoon, rural-linked "
            "companies (consumer brands, tyres, two-wheelers, farm inputs) "
            "usually outperform the wider market. In the four monsoon seasons "
            "we tested, an equal-weighted basket of six such stocks gained "
            "about 46% versus roughly 15% for the Nifty over the same periods, "
            "and it beat the Nifty in all four of those seasons; the worst dip "
            "along the way was about 12%. Our confidence in the idea is graded "
            "B, but with only four years of data the track record is still "
            "rated 'unproven', so think of these results as encouraging rather "
            "than a promise. " + _DISCLAIMER
        ),
        "benchmark_label": "Nifty 50",
        "headline_tier": "conservative",
    },
    VIEW_IT: {
        "short_title": "Weak IT guidance rotates money into domestic stocks",
        "description": (
            "When India's large IT exporters warn of a weak quarter, money has "
            "tended to rotate out of IT and into home-grown sectors that don't "
            "depend on US tech spending — power, infrastructure, railways and "
            "financials. The trade owns those domestic names directly around "
            "the IT print."
        ),
        "bullets": [
            "What drives it: a weak IT-guidance print signals a rotation out of "
            "export-heavy IT into domestic, less US-dependent sectors.",
            "How to play it: hold an equal-weighted basket of five domestic "
            "stocks for a few weeks around the print; a hedged version shorts "
            "the Nifty.",
            "Main caveat: only eight past events exist, so the track record is "
            "unproven, and a broad sell-off can drag the basket down.",
        ],
        "plain_one_liner": (
            "When India's big IT firms warn of a weak quarter, investors tend "
            "to rotate money into domestic, less export-dependent names - "
            "power, infrastructure, railways and financials."
        ),
        "plain_summary": (
            "A weak IT-guidance quarter tends to push money into home-grown "
            "sectors that don't depend on US tech spending."
        ),
        "plain_thesis": (
            "We think a weak guidance quarter from India's large IT companies "
            "is a signal that money rotates out of IT and into home-grown "
            "sectors that don't depend on US tech spending. Across eight past "
            "weak-guidance events, an equal-weighted basket of five such "
            "domestic stocks rose about 49% while the Nifty was roughly "
            "flat-to-slightly-down over the same windows, beating the Nifty in "
            "five of the eight events; the worst dip was about 14%. The belief "
            "itself is graded C (moderate) and the track record is rated "
            "'unproven' because only eight events exist, so treat this as a "
            "pattern worth watching, not a sure thing. " + _DISCLAIMER
        ),
        "benchmark_label": "Nifty 50",
        "headline_tier": "conservative",
    },
    VIEW_CRUDE: {
        "short_title": "Cheaper oil lifts India's importers",
        "description": (
            "India imports most of its oil, so a sharp fall in crude — often as "
            "geopolitical tensions cool — lowers fuel and input costs and widens "
            "margins for import-heavy businesses like paints, airlines and "
            "oil-marketing companies. This view is still developing: our strict "
            "test left only one name clearly passing, so it's a watch, not a "
            "finished basket."
        ),
        "bullets": [
            "What drives it: cheaper crude cuts input and fuel costs, helping "
            "import-heavy Indian firms' margins.",
            "How to play it: a cheaper-oil beneficiary basket (paints, airlines, "
            "oil marketers) — still being refined.",
            "Main caveat: only Asian Paints clearly passed the strict test and "
            "the trade-quality score wasn't strong enough to publish.",
        ],
        "plain_one_liner": (
            "When crude oil falls sharply - often as geopolitical tensions "
            "cool - India, which imports most of its oil, tends to benefit, "
            "and import-cost-sensitive companies do better."
        ),
        "plain_summary": (
            "Cheaper oil is a tailwind for import-heavy Indian businesses like "
            "paints, airlines and oil marketers."
        ),
        "plain_thesis": (
            "We think a sharp drop in crude oil helps India because lower fuel "
            "and input costs widen margins for import-heavy businesses such as "
            "paints, airlines and oil-marketing companies. The broad belief is "
            "reasonably supported (graded B), and in twelve past episodes the "
            "approach modestly beat the Nifty - roughly +25% versus +14% - "
            "winning about two-thirds of the time, with a worst dip near 10%. "
            "But this one is still developing: our strict test left only one "
            "stock (Asian Paints) clearly passing, the trade-quality score "
            "wasn't strong enough to publish, and the more aggressive "
            "'oil-up' versions did not hold up - so treat it as an idea to "
            "watch, not a finished basket. " + _DISCLAIMER
        ),
        "benchmark_label": "Nifty 50",
        # Developing: NO finished basket -> hero renders as '—'.
        "headline_tier": None,
    },
}


# ── curated per-expression plain copy, keyed by (view_id, tier) ─────────────
#
# Only the HEADLINE (and the honest developing-state) expressions are curated;
# every other tier falls through to the generated plain-language projection.

EXPRESSION_COPY: dict[tuple[str, str], dict[str, Optional[str]]] = {
    (VIEW_MONSOON, "conservative"): {
        "plain_label": "Steady — equal-weighted basket of 6 rural stocks",
        "plain_one_liner": (
            "Buy an equal-weighted basket of 6 rural-economy stocks and hold "
            "through the monsoon season (you decide the amount)."
        ),
        "plain_why": (
            "A good monsoon tends to lift rural incomes, which has "
            "historically helped tyre, two-wheeler and everyday-consumer "
            "brands outperform the wider market."
        ),
        "plain_risk": (
            "Based on only four seasons, so the edge is unproven; a weak or "
            "delayed monsoon would undercut the idea, and the basket can fall "
            "if the whole market sells off."
        ),
    },
    (VIEW_IT, "conservative"): {
        "plain_label": "Steady — equal-weighted basket of 5 domestic stocks",
        "plain_one_liner": (
            "Buy an equal-weighted basket of 5 domestic, less export-dependent "
            "stocks and hold for a few weeks around the IT print (you decide "
            "the amount)."
        ),
        "plain_why": (
            "When IT guidance disappoints, money has tended to rotate into "
            "home-grown sectors like power, infrastructure and railways - this "
            "owns those names directly."
        ),
        "plain_risk": (
            "Only eight past events exist, so the track record is unproven; if "
            "the whole market sells off, the basket can fall with it."
        ),
    },
    (VIEW_CRUDE, "conservative"): {
        "plain_label": "Cheaper-oil beneficiary basket (still being refined)",
        "plain_one_liner": (
            "A cheaper-oil beneficiary basket is still being refined - only "
            "one name (Asian Paints) clearly passed our strict test, so this "
            "isn't a finished basket yet."
        ),
        "plain_why": (
            "Lower crude widens margins for import-heavy businesses like "
            "paints, airlines and oil-marketing companies."
        ),
        "plain_risk": (
            "Still developing - the trade-quality score wasn't strong enough "
            "to publish, so treat it as a watch, not a recommendation."
        ),
    },
}


# ── curated transmission node display labels ────────────────────────────────

NODE_LABELS: dict[str, str] = {
    "IMD_monsoon_forecast": "Monsoon forecast (IMD)",
    "normal_monsoon_LPA": "Normal monsoon rainfall",
    "rural_discretionary_demand": "Rural spending picks up",
    "agri_input_demand": "Demand for seeds and fertiliser",
    "M&M_stock_performance": "Tractor and farm-equipment makers",
    "IT_weak_guidance": "IT firms warn of a weak quarter",
    "INFY_downside": "IT share prices slip",
    "defensive_outperformance": "Domestic, defensive sectors do better",
    "Brent_crude_decline_10d_minus8pct": "Crude oil falls sharply",
    "geopolitical_de_escalation": "Geopolitical tensions cool",
    "importer_margin_expansion": "Import-heavy firms' margins widen",
}


# ── stock display names ─────────────────────────────────────────────────────

STOCK_NAMES: dict[str, str] = {
    "RECLTD": "REC",
    "ADANIPOWER": "Adani Power",
    "JPPOWER": "JP Power",
    "RVNL": "RVNL",
    "ENGINERSIN": "Engineers India",
    "BRITANNIA": "Britannia",
    "MRF": "MRF",
    "MARICO": "Marico",
    "APOLLOTYRE": "Apollo Tyres",
    "GODREJCP": "Godrej Consumer",
    "HINDUNILVR": "HUL",
    "ASIANPAINT": "Asian Paints",
    "BERGEPAINT": "Berger Paints",
    "INDIGO": "IndiGo",
    "HINDPETRO": "HPCL",
    "BPCL": "BPCL",
    "IOC": "IOC",
    "TVSMOTOR": "TVS Motor",
    "M&M": "M&M",
    "COROMANDEL": "Coromandel",
    "RALLIS": "Rallis India",
    "UPL": "UPL",
}


# ── verdict / strength / capital / source humanizers ────────────────────────

_TRUST_BADGE = {
    "insufficient_data": "Not enough data",
    "no_edge": "No edge yet",
    "unproven": "Unproven",
    "promising": "Promising",
}

_SOURCE_LABEL = {
    "polymarket": "Prediction market (Polymarket)",
    "kalshi": "Prediction market (Kalshi)",
    "consensus": "Analyst consensus",
    "model": "Pivot model estimate",
}

_CAPITAL_LABEL = {
    "low": "Low",
    "low_medium": "Low-medium",
    "low-medium": "Low-medium",
    "moderate": "Low-medium",
    "medium": "Medium",
    "high": "Medium",
}

_TIER_PREFIX = {
    "conservative": "Steady",
    "balanced": "Balanced",
    "aggressive": "Aggressive",
}


def trust_badge(verdict: Optional[str]) -> Optional[str]:
    """Plain word for a trust-ladder verdict (jargon -> layman)."""
    if not verdict:
        return None
    return _TRUST_BADGE.get(str(verdict).lower())


def strength_label(strength: Optional[float]) -> Optional[str]:
    """Map a 0..1 edge strength to a plain link strength."""
    if strength is None:
        return None
    try:
        s = float(strength)
    except (TypeError, ValueError):
        return None
    if s >= 0.66:
        return "strong link"
    if s >= 0.4:
        return "moderate link"
    return "weak link"


def capital_label(capital_intensity: Optional[str]) -> Optional[str]:
    """Plain capital LABEL only ('Low' / 'Low-medium' / 'Medium') — never rupees.

    The stored ``capital_intensity`` may be a full sentence that begins with the
    plain word (e.g. "Low-medium — equity CNC delivery only…" or "Medium: …").
    We match the LEADING label token (longest key first so "low-medium" wins
    over "low"); the rest of the sentence (which can mention rupees / F&O) is
    discarded so only a clean label ever reaches the FE.
    """
    if not capital_intensity:
        return None
    text = str(capital_intensity).strip().lower()
    if not text:
        return None
    direct = _CAPITAL_LABEL.get(text)
    if direct:
        return direct
    for key in sorted(_CAPITAL_LABEL, key=len, reverse=True):
        if text.startswith(key):
            return _CAPITAL_LABEL[key]
    return None


def source_label(source: Optional[str]) -> str:
    """Closed map of expectation sources; unknown -> 'Market estimate'."""
    if not source:
        return "Market estimate"
    return _SOURCE_LABEL.get(str(source).strip().lower(), "Market estimate")


def _humanize_token(raw: Optional[str]) -> Optional[str]:
    """Safe fallback humanizer for a snake_case node/label (no stats leak)."""
    if not raw:
        return None
    text = str(raw).replace("_", " ").replace("->", "→").replace("  ", " ")
    text = text.strip()
    if not text:
        return None
    # Sentence-case the first letter only; keep acronyms intact.
    return text[0].upper() + text[1:]


def node_label(node: Optional[str]) -> Optional[str]:
    """Plain display label for a transmission node (curated, else humanized)."""
    if not node:
        return None
    return NODE_LABELS.get(str(node)) or _humanize_token(node)


def plain_evidence(edge_label: Optional[str]) -> Optional[str]:
    """De-jargoned one-liner for a transmission edge.

    Built ONLY from ``edge_label`` (snake_case relationship) — NEVER from the
    raw ``evidence`` string, which embeds CAAR / t / p statistics. Returns
    ``None`` when there is nothing plain to say.
    """
    return _humanize_token(edge_label)


def stock_name(symbol: Optional[str]) -> Optional[str]:
    """Plain display name for a tradeable equity symbol (e.g. RECLTD.NS -> REC)."""
    if not symbol:
        return None
    raw = str(symbol).strip()
    # Drop a yfinance/exchange suffix and any descriptive parenthetical.
    base = raw.split("(")[0].strip()
    base = base.split(".")[0].strip()
    if not base:
        return None
    return STOCK_NAMES.get(base, base)


def basket_members(expr_cfg: dict[str, Any]) -> list[str]:
    """Plain stock display names for the basket's LONG leg.

    Prefers ``structure.members_long``; falls back to long-role tradeable
    equity instruments. Non-equity legs (index hedges, synthetic short legs)
    are excluded. Never invents names.
    """
    cfg = expr_cfg if isinstance(expr_cfg, dict) else {}
    structure = cfg.get("structure") if isinstance(cfg.get("structure"), dict) else {}
    symbols: list[str] = []
    members_long = structure.get("members_long")
    if isinstance(members_long, list) and members_long:
        symbols = [str(s) for s in members_long if s]
    else:
        instruments = cfg.get("instruments")
        if isinstance(instruments, list):
            for ins in instruments:
                if not isinstance(ins, dict):
                    continue
                if ins.get("role") not in (None, "long"):
                    continue
                if ins.get("instrument_type") not in (None, "equity"):
                    continue
                sym = ins.get("symbol")
                if sym:
                    symbols.append(str(sym))
    out: list[str] = []
    for sym in symbols:
        name = stock_name(sym)
        if name and name not in out:
            out.append(name)
    return out


# ── warning de-jargon layer ─────────────────────────────────────────────────
#
# The stored ``config.warnings`` are honest, but some were authored by the
# curation/backtest pipeline in quant shorthand (DSR / num_trials / CAAR /
# "multiple-testing"). The Views FE must never see that jargon (DESIGN LAW),
# so each warning is rewritten to plain English where we have a known mapping,
# and any warning that STILL carries hard statistical jargon after rewriting is
# dropped rather than leaked. No numbers are invented or removed: targeted
# regex rewrites preserve any real figure (e.g. "8 episodes") the line carries.

# Ordered (pattern, replacement) rewrites — applied in sequence to each warning.
_WARNING_REWRITES: list[tuple[re.Pattern[str], str]] = [
    # Whole-line: an internal methodology note about screening many candidates.
    (
        re.compile(r"full[- ]universe screen.*$", re.I),
        "Screened against a wide list of candidates with a stricter bar, so the "
        "result is less likely to be a fluke from over-searching.",
    ),
    # Strip the "Trust verdict" shorthand but keep the rest (e.g. "— 8 episodes…").
    (
        re.compile(
            r"trust verdict caps at ['‘’\"]?unproven['‘’\"]?",
            re.I,
        ),
        "Track record is rated only 'unproven'",
    ),
    (re.compile(r"\bnum[_ ]trials\b", re.I), "the number of strategies tried"),
    (
        re.compile(r"\bmultiple[- ]testing(?:[- ]honest)?\b", re.I),
        "many-candidate testing",
    ),
]

# Hard statistical jargon — if any survives the rewrites, the warning is dropped
# rather than shown to a retail user. (Leading spaces / word-boundaries avoid
# matching inside ordinary words.)
_WARNING_DROP_TOKENS: tuple[str, ...] = (
    "dsr",
    "caar",
    "bhar",
    "mintrl",
    "psr",
    "deflat",
    "sharpe",
    "monte carlo",
    "walk-forward",
    "walk forward",
    "permutation",
    "p-value",
    "p=",
    "t=",
    "selection bias",
    "trust battery",
    "trust verdict",
)


def plain_warnings(raw: Any) -> list[str]:
    """De-jargon the stored expression warnings for the FE.

    Rewrites known quant shorthand to plain English (preserving real numbers),
    then drops any line that still carries hard statistical jargon. Order is
    preserved and duplicates are removed.
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for w in raw:
        if not isinstance(w, str):
            continue
        text = w.strip()
        if not text:
            continue
        for pat, rep in _WARNING_REWRITES:
            text = pat.sub(rep, text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        low = text.lower()
        if any(tok in low for tok in _WARNING_DROP_TOKENS):
            continue
        if text not in out:
            out.append(text)
    return out


# ── view-level projection ───────────────────────────────────────────────────


def plain_for_view(view: Any) -> dict[str, Optional[str]]:
    """Curated plain copy for a view, else a SAFE humanized fallback.

    Fallback (any future / unseeded view): ``plain_one_liner = title`` and the
    longer prose fields are ``None`` so the FE never renders a raw enum or a
    jargon-laden thesis.
    """
    vid = str(getattr(view, "id", "") or "")
    curated = VIEW_COPY.get(vid)
    if curated:
        return {
            "plain_one_liner": curated.get("plain_one_liner"),
            "plain_summary": curated.get("plain_summary"),
            "plain_thesis": curated.get("plain_thesis"),
            "benchmark_label": curated.get("benchmark_label") or "Nifty 50",
        }
    return {
        "plain_one_liner": getattr(view, "title", None),
        "plain_summary": None,
        "plain_thesis": None,
        "benchmark_label": "Nifty 50",
    }


def headline_tier(view: Any) -> tuple[bool, Optional[str]]:
    """Return (is_curated, headline_tier).

    ``is_curated`` False -> the router uses its automatic best-expression pick.
    ``is_curated`` True with tier ``None`` -> a developing view with NO finished
    hero (render '—'). With a tier string -> force the hero to that tier.
    """
    vid = str(getattr(view, "id", "") or "")
    if vid in VIEW_COPY:
        return True, VIEW_COPY[vid].get("headline_tier")
    return False, None


# ── expression-level projection ─────────────────────────────────────────────


def _str_enum(val: Any) -> str:
    return str(getattr(val, "value", val)) if val is not None else ""


def _generated_expression_copy(
    expr: Any, members: list[str],
) -> dict[str, Optional[str]]:
    """SAFE plain-language projection from REAL expression fields.

    Uses tier / kind / member count / time horizon only — NEVER invents a
    number. This is the fallback for any expression not in EXPRESSION_COPY.
    """
    tier = _str_enum(getattr(expr, "tier", "")).lower()
    kind = _str_enum(getattr(expr, "expression_kind", "")).lower()
    horizon = getattr(expr, "time_horizon", None)
    n = len(members)
    prefix = _TIER_PREFIX.get(tier, "")

    if kind == "basket":
        body = f"equal-weighted basket of {n} stocks" if n else "equal-weighted basket"
        hold = f" and hold for {horizon}" if horizon else ""
        label = f"{prefix} — {body}".strip(" —")
        one_liner = (
            f"Buy an {body}{hold} (you decide the amount)."
            if body.startswith("equal")
            else f"Buy a {body}{hold} (you decide the amount)."
        )
        why = (
            "The simplest, lowest-maintenance way to express this view - you "
            "own the names directly."
        )
        risk = "If the broader market falls, the basket can fall with it."
    elif kind == "pair":
        label = f"{prefix} — market-neutral pair trade".strip(" —")
        one_liner = (
            "Go long the basket and short the weaker side so broad market "
            "swings largely cancel out (you decide the size)."
        )
        why = "Aims to isolate the view by cancelling out broad-market direction."
        risk = "If both legs move against you together, the pair can still lose."
    elif kind == "hedge":
        label = f"{prefix} — basket with an index hedge".strip(" —")
        one_liner = (
            "Hold the basket but offset broad-market moves with an index hedge "
            "(you decide the size)."
        )
        why = "Keeps the basket's view while damping overall market direction."
        risk = "The hedge costs something and can cap gains in a strong market."
    elif kind == "multi_asset":
        label = f"{prefix} — leveraged directional basket".strip(" —")
        one_liner = (
            "A more aggressive, leveraged version of the basket - higher "
            "potential reward and higher risk (you decide the size)."
        )
        why = "For higher conviction: more upside if the view plays out."
        risk = "Leverage magnifies losses as well as gains."
    elif kind == "option_strategy":
        label = f"{prefix} — defined-risk options position".strip(" —")
        one_liner = (
            "A defined-risk options position - your maximum loss is capped at "
            "what you pay upfront."
        )
        why = "Caps the downside while keeping upside if the view plays out."
        risk = "Options can expire worthless - you can lose the full premium paid."
    else:
        label = (prefix or None)
        one_liner = None
        why = None
        risk = None

    return {
        "plain_label": label or None,
        "plain_one_liner": one_liner,
        "plain_why": why,
        "plain_risk": risk,
    }


def plain_for_expression(view: Any, expr: Any) -> dict[str, Optional[str]]:
    """Curated per-(view,tier) copy when present, else a generated projection."""
    vid = str(getattr(view, "id", "") or "")
    tier = _str_enum(getattr(expr, "tier", "")).lower()
    cfg = getattr(expr, "config", None)
    members = basket_members(cfg if isinstance(cfg, dict) else {})
    curated = EXPRESSION_COPY.get((vid, tier))
    if curated:
        return {
            "plain_label": curated.get("plain_label"),
            "plain_one_liner": curated.get("plain_one_liner"),
            "plain_why": curated.get("plain_why"),
            "plain_risk": curated.get("plain_risk"),
        }
    return _generated_expression_copy(expr, members)


# ── view extras (short title / description / bullets / similar) ───────────────


def view_extras(view: Any) -> dict[str, Any]:
    """Curated short_title / description / bullets for a view (else safe blanks).

    ``short_title`` is a 7-8-word Polymarket-style headline; ``description`` is
    2-3 plain sentences; ``bullets`` is exactly three (drives it / how to play /
    caveat). Unseeded views get ``short_title = title`` and empty long-form.
    """
    vid = str(getattr(view, "id", "") or "")
    curated = VIEW_COPY.get(vid)
    if curated:
        return {
            "short_title": curated.get("short_title"),
            "description": curated.get("description"),
            "bullets": list(curated.get("bullets") or []),
        }
    return {
        "short_title": getattr(view, "title", None),
        "description": None,
        "bullets": [],
    }


def short_title(view_id: Optional[str]) -> Optional[str]:
    """The curated short_title for a view id, else None."""
    if not view_id:
        return None
    curated = VIEW_COPY.get(str(view_id))
    return curated.get("short_title") if curated else None


def similar_views(view_id: Optional[str]) -> list[dict[str, Optional[str]]]:
    """The OTHER two curated views (id + short_title) for the 'Similar Views' rail."""
    vid = str(view_id or "")
    if vid not in VIEW_COPY:
        return []
    out: list[dict[str, Optional[str]]] = []
    for other in (VIEW_IT, VIEW_MONSOON, VIEW_CRUDE):
        if other == vid:
            continue
        out.append({"id": other, "short_title": short_title(other)})
    return out


# ── strategy identity (honest name / type / option legs) ─────────────────────
#
# The stored expression has only a tier + kind + long members — NOT a strategy
# name or option legs. This layer gives each tier a PROPER, differentiated name
# (never "basket"/"basket" duplicates), a coarse type, and — for the single
# options expression — a concrete defined-risk structure built from the option
# template engine, labelled honestly as illustrative.

_STRATEGY_TYPE_BY_KIND = {
    "basket": "Basket",
    "multi_asset": "Basket",
    "pair": "Pair (market-neutral)",
    "hedge": "Pair (market-neutral)",
    "option_strategy": "Options (defined-risk)",
}

# Curated proper names per (view, tier). Differentiates Conservative (a real
# basket) from Balanced (the long-basket / short-Nifty market-neutral pair).
STRATEGY_NAME: dict[tuple[str, str], str] = {
    (VIEW_MONSOON, "conservative"): "Rural-demand basket",
    (VIEW_MONSOON, "balanced"): "Long basket / short Nifty hedge",
    (VIEW_MONSOON, "aggressive"): "Bull call spread",
    (VIEW_IT, "conservative"): "Domestic-rotation basket",
    (VIEW_IT, "balanced"): "Long basket / short Nifty hedge",
    (VIEW_IT, "aggressive"): "Domestic basket, Nifty-hedged",
    (VIEW_CRUDE, "conservative"): "Cheaper-oil beneficiary basket",
    (VIEW_CRUDE, "balanced"): "Long basket / short Nifty hedge",
    (VIEW_CRUDE, "aggressive"): "Leveraged oil-down basket",
}

_NAME_BY_KIND = {
    "basket": "Equal-weighted basket",
    "multi_asset": "Leveraged directional basket",
    "pair": "Long basket / short Nifty hedge",
    "hedge": "Basket with Nifty hedge",
    "option_strategy": "Defined-risk options",
}

_OPTION_LEGS_NOTE = (
    "Illustrative structure — the exact strikes are set when you deploy."
)


def _build_option_legs(template_name: str) -> Optional[list[dict[str, Any]]]:
    """Read concrete legs from an option template (no live chain needed).

    Returns ``[{action, option_type, strike_rule, delta?, strike_offset?}, ...]``
    or ``None`` if the template can't be loaded. Strikes are intentionally
    expressed as RULES (atm / ~Δ / offset), not numbers — honest because the
    real strikes are only known at deploy against the live chain.
    """
    try:
        from backend.services.option_strategies import TEMPLATES
    except Exception:  # noqa: BLE001
        return None
    tmpl = TEMPLATES.get(template_name)
    if tmpl is None:
        return None
    legs: list[dict[str, Any]] = []
    for leg in tmpl.legs:
        item: dict[str, Any] = {
            "action": leg.side,                 # BUY | SELL
            "option_type": leg.option_type,     # CE | PE
            "strike_rule": leg.strike_rule,     # atm | delta | atm_offset
        }
        if leg.strike_rule == "delta" and leg.delta:
            item["delta"] = leg.delta
        if leg.strike_rule == "atm_offset" and leg.offset:
            item["strike_offset"] = leg.offset
        legs.append(item)
    return legs or None


def strategy_identity(view: Any, expr: Any) -> dict[str, Any]:
    """Honest strategy name / type / option legs for an expression.

    For the options expression we build ONE concrete defined-risk structure via
    the option templates aligned to the view direction (bullish → bull call
    spread). If the template can't build, we fall back to "Defined-risk options"
    with NO invented legs.
    """
    vid = str(getattr(view, "id", "") or "")
    tier = _str_enum(getattr(expr, "tier", "")).lower()
    kind = _str_enum(getattr(expr, "expression_kind", "")).lower()
    stype = _STRATEGY_TYPE_BY_KIND.get(kind, "Basket")

    option_legs: Optional[list[dict[str, Any]]] = None
    option_note: Optional[str] = None
    if kind == "option_strategy":
        # All three curated views are LONG their beneficiaries → bullish.
        legs = _build_option_legs("bull_call_spread")
        if legs:
            option_legs = legs
            option_note = _OPTION_LEGS_NOTE
            name = STRATEGY_NAME.get((vid, tier)) or "Bull call spread"
        else:
            name = "Defined-risk options"
    else:
        name = STRATEGY_NAME.get((vid, tier)) or _NAME_BY_KIND.get(kind) or None

    return {
        "strategy_name": name,
        "strategy_type": stype,
        "option_legs": option_legs,
        "option_legs_note": option_note,
    }


__all__ = [
    "plain_for_view",
    "plain_for_expression",
    "headline_tier",
    "trust_badge",
    "strength_label",
    "capital_label",
    "source_label",
    "node_label",
    "plain_evidence",
    "plain_warnings",
    "stock_name",
    "basket_members",
    "view_extras",
    "short_title",
    "similar_views",
    "strategy_identity",
]
