"""Thematic-scenario detector + seed winners/losers map (GAN R4 keystone).

WHY this module exists
----------------------
The GAN R4 thematic-thesis class scored 3.29/10 because there was NO rule
anywhere that mapped a *"strategy that profits from / benefits from / hedges
against / position me for <macro scenario>"* ask to a decode-and-propose
path. Every such prompt degraded into the nearest generic shape — an option
spread (monsoon, crude), a bare `ask_user` punt (india-pak, rupee), a
sector-rotation schedule (rbi), or prose-only (el-nino). The `vix_gated`
session PROVED the DSL can already express a gated thematic basket
(`propose_workflow` with one `action.place_order` per ticker); the gap was
purely routing + reply contract, never the engine.

This module is the deterministic layer the chat service consults BEFORE the
LLM, so the model is steered into:
  * forcing the basket / workflow toolset (never a bare `ask_user`),
  * never gating the whole turn on a live-quote success, and
  * a system-directive that mandates the thesis → winners/losers table →
    turn-1 ₹-split basket → confirm/invalidation → caveat shape.

It is pure functions + frozen data: no I/O, no LLM, microseconds. It is
imported by `chat_service.py` and unit-tested directly.

REFUSAL CALIBRATION
-------------------
Lawful scenario positioning — a conflict hedge via defence/gold/vol, a
drought play via irrigation/agri — is a LEGITIMATE analysis ask. This module
deliberately treats those as buildable theses. Genuinely harmful/illegal
asks (insider information, market manipulation) are NOT scenario-positioning
and are handled elsewhere; this detector does not green-light them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ThematicScenario:
    """A recognised macro scenario with its winners/losers seed map.

    `key`      — stable identifier (used in traces / tests).
    `label`    — human label for the directive ("a falling rupee").
    `winners`  — long-leg NSE tickers (UPPERCASE) with a one-line WHY.
    `losers`   — avoid-leg NSE tickers with a one-line WHY (shorting isn't
                 wired, so the loser side is an AVOID list, named in text).
    `thesis`   — the 1-2 line causal chain the directive seeds.
    `confirm`  — checkable data that confirms the thesis.
    `invalidate` — checkable data that kills it.
    """

    key: str
    label: str
    thesis: str
    winners: tuple[tuple[str, str], ...]
    losers: tuple[tuple[str, str], ...]
    confirm: str
    invalidate: str


# ── Seed map (research-grounded; see GOLD/thematic-thesis.md sources) ──
# Tickers are real NSE symbols. The WHY strings are the causal hooks the
# model is told to render per row.
_SCENARIOS: tuple[ThematicScenario, ...] = (
    ThematicScenario(
        key="monsoon_drought",
        label="a deficient monsoon / drought",
        thesis=(
            "A deficient monsoon weakens kharif sowing, stresses farm "
            "incomes and rural consumption (FMCG, two-wheelers, tractors, "
            "fertiliser) while irrigation/pump demand SPIKES as farmers and "
            "state schemes (PM-KUSUM) substitute for failed rains."
        ),
        winners=(
            ("SHAKTIPUMP", "Solar/agri pumps — direct beneficiary of the irrigation push when rains fail"),
            ("KSB", "Industrial & agri pump maker — irrigation capex substitute for rainfall"),
            ("KIRLOSBROS", "Pumps + fluid management — same irrigation-substitution demand"),
            ("JISLJALEQS", "Drip/micro-irrigation pure play — highest beta to drought-driven spend (note: leveraged balance sheet)"),
        ),
        losers=(
            ("M&M", "Tractor volumes track kharif sentiment — onset-delay fear sells these first"),
            ("ESCORTS", "Tractor-heavy — same rural-demand hit"),
            ("HINDUNILVR", "Rural-heavy FMCG — weak farm incomes cut volume growth"),
            ("DABUR", "Rural-skewed FMCG — same volume drag"),
            ("COROMANDEL", "Fertiliser offtake falls with sown area"),
            ("HEROMOTOCO", "Two-wheelers are the most rural-exposed auto segment"),
        ),
        confirm="IMD June-Sep forecast below ~95% of LPA, cumulative all-India deficit >10% by mid-July, falling reservoir storage, weak M&M monthly tractor despatches",
        invalidate="IMD upgrade to normal/above-normal, deficit closing by end-July, sowing acreage catching up YoY",
    ),
    ThematicScenario(
        key="conflict_war",
        label="an India-Pakistan / geopolitical conflict",
        thesis=(
            "Open conflict drives emergency defence procurement and a "
            "multi-year defence-budget re-rating (direct earnings tailwind to "
            "state defence manufacturers), a safe-haven gold bid, a crude/INR "
            "risk premium, an India-VIX spike, and a broad-market drawdown "
            "concentrated in aviation, tourism and high-beta financials. The "
            "May-2025 escalation (Operation Sindoor) is the live playbook."
        ),
        winners=(
            ("HAL", "Fighter/helicopter prime — emergency orders + budget re-rating"),
            ("BEL", "Defence electronics/radar — fastest order-flow pass-through"),
            ("BDL", "Missiles/munitions — the most direct consumable in a conflict (+7.7% single session, May-2025)"),
            ("MAZDOCK", "Naval shipbuilding — longer-cycle but re-rates with the theme"),
            ("GOLDBEES", "Safe-haven bid; also hedges the INR leg (pays even if equities gap down together)"),
        ),
        losers=(
            ("INDIGO", "Airspace closures + fuel/INR shock — double hit"),
            ("IRCTC", "Travel/tourism demand collapses on conflict risk"),
            ("INDHOTEL", "Hospitality demand falls with travel"),
            ("BAJFINANCE", "Risk-off drawdown hits high-multiple lenders hardest"),
        ),
        confirm="sustained escalation (cross-border strikes, mobilisation), India VIX closing above ~20, defence-ministry emergency-procurement headlines",
        invalidate="de-escalation/ceasefire — conflict-premium names round-trip fast; defence PSUs also already carry a big multi-year run-up, so entry valuation is the main risk",
    ),
    ThematicScenario(
        key="inr_depreciation",
        label="a falling rupee (INR depreciation)",
        thesis=(
            "A falling rupee transfers margin to dollar-earners — IT and "
            "pharma exporters book USD revenue against an INR cost base, so "
            "every leg down in INR is direct margin expansion — while "
            "dollar-cost importers (OMCs, airlines paying USD fuel/leases) get "
            "squeezed."
        ),
        winners=(
            ("INFY", "USD revenue, INR cost base — cleanest large-cap FX beneficiary"),
            ("TCS", "Same USD-revenue FX tailwind, top-tier IT"),
            ("SUNPHARMA", "Largest pharma exporter — USD/EM revenue vs INR costs"),
            ("CIPLA", "US generics exposure, same FX margin tailwind"),
        ),
        losers=(
            ("IOC", "Crude import bill rises in INR; marketing margins compress"),
            ("BPCL", "Same OMC import-cost squeeze"),
            ("INDIGO", "USD fuel + lease liabilities — double FX hit"),
            ("NESTLEIND", "Imported input costs (edible oil, packaging) squeeze gross margin"),
        ),
        confirm="USDINR making sustained new highs, FII debt outflows, a widening trade deficit",
        invalidate="RBI defending a level hard, a dollar-index rollover, or crude falling (improves the current account and lifts INR)",
    ),
    ThematicScenario(
        key="crude_spike",
        label="a crude-oil spike",
        thesis=(
            "A sustained crude spike lifts the import bill (CAD/INR pressure), "
            "boosts upstream realisations (ONGC/OIL) but squeezes OMC "
            "marketing margins, crude-derivative input costs (paints, tyres) "
            "and aviation fuel."
        ),
        winners=(
            ("ONGC", "Upstream producer — realisations rise directly with crude"),
            ("OIL", "Upstream pure play — same realisation tailwind"),
        ),
        losers=(
            ("IOC", "Marketing-margin squeeze when pump prices lag crude"),
            ("BPCL", "Same OMC margin compression"),
            ("HPCL", "Same OMC margin compression"),
            ("ASIANPAINT", "Crude-derivative inputs raise COGS"),
            ("INDIGO", "ATF is the largest cost line — direct hit"),
            ("MRF", "Rubber/carbon-black inputs track crude"),
            ("APOLLOTYRE", "Same tyre input-cost squeeze"),
        ),
        confirm="Brent holding above the post-spike level, OMC daily price-revision news, widening trade-deficit prints",
        invalidate="the flare-up resolves and Brent falls back below the pre-spike level",
    ),
    ThematicScenario(
        key="rate_cut",
        label="an RBI rate-cut cycle",
        thesis=(
            "A rate-cut cycle lowers funding costs and supports credit growth "
            "and EMI-sensitive demand — banks/NBFCs (loan growth, treasury "
            "gains), autos and rate-sensitive realty lead, while NIM-"
            "compression-prone lenders lag."
        ),
        winners=(
            ("HDFCBANK", "Credit-growth + treasury-gain beneficiary, top private bank"),
            ("ICICIBANK", "Same loan-growth tailwind, strong franchise"),
            ("BAJFINANCE", "NBFC — funding cost falls, lending spread/AUM growth"),
            ("MARUTI", "Lower EMIs lift auto demand"),
        ),
        losers=(
            ("HDFCLIFE", "Lower yields pressure investment income for insurers"),
        ),
        confirm="successive MPC cuts / dovish guidance, falling system deposit/lending rates, reviving credit growth",
        invalidate="a hawkish pivot on sticky inflation, a pause, or a liquidity squeeze that re-widens funding costs",
    ),
    ThematicScenario(
        key="slowdown",
        label="an economic / growth slowdown",
        thesis=(
            "A growth slowdown rewards defensive earnings (staples, pharma, "
            "utilities) and punishes cyclicals (high-beta financials, autos, "
            "metals, discretionary) whose earnings are most growth-sensitive."
        ),
        winners=(
            ("HINDUNILVR", "Defensive staples — earnings hold up in a slowdown"),
            ("NESTLEIND", "Consumer staples — low demand elasticity"),
            ("SUNPHARMA", "Healthcare demand is defensive"),
            ("GOLDBEES", "Safe-haven / lower-correlation ballast"),
        ),
        losers=(
            ("TATAMOTORS", "Discretionary auto demand is highly growth-sensitive"),
            ("TATASTEEL", "Metals are deep cyclicals"),
            ("BAJFINANCE", "High-multiple lender — earnings and asset quality hit hardest"),
        ),
        confirm="falling GDP/PMI prints, weak high-frequency demand data, downgrade cycle in cyclical earnings",
        invalidate="growth re-acceleration, a policy/fiscal stimulus, or a sharp rate-cut response that revives cyclicals",
    ),
)


_SCENARIO_BY_KEY: dict[str, ThematicScenario] = {s.key: s for s in _SCENARIOS}


# ── Scenario-noun matchers ───────────────────────────────────────────
# Each scenario's trigger nouns. Hinglish cues are folded in inline.
_SCENARIO_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(
        r"\b(?:monsoon|drought|rainfall|below[\s-]?normal\s+rain|deficient\s+rain|"
        r"kharif|el[\s-]?ni[nñ]o|el\s+nino|dry\s+spell|weak\s+rain)\b",
        re.IGNORECASE), "monsoon_drought"),
    (re.compile(
        r"\b(?:war|conflict|shooting\s+war|india[\s-]?pak(?:istan)?|"
        r"cross[\s-]?border|escalation|ceasefire|military|geopolit\w*|"
        r"border\s+tension|attack|strike|hostilit\w*)\b",
        re.IGNORECASE), "conflict_war"),
    (re.compile(
        r"\b(?:rupee|inr|usdinr|usd[\s/-]?inr|dollar\s+rising|currency\s+"
        r"depreciat\w*|weak\s+rupee|falling\s+rupee|rupee\s+(?:fall|crash|weak))\b"
        r"|rupee\b.{0,30}\b(?:gir|girr|gira)\b"  # Hinglish: rupee gir raha hai
        r"|\bgir\s+raha\b",
        re.IGNORECASE), "inr_depreciation"),
    (re.compile(
        r"\b(?:crude|oil\s+(?:price|spike|shock)|brent|wti|opec|"
        r"middle[\s-]?east\s+flare|fuel\s+price\s+spike)\b",
        re.IGNORECASE), "crude_spike"),
    (re.compile(
        r"\b(?:rate[\s-]?cut|repo\s+(?:rate\s+)?cut|rbi\s+cut|easing\s+cycle|"
        r"rate[\s-]?cut\s+cycle|dovish|liquidity\s+easing|lower\s+rates?)\b",
        re.IGNORECASE), "rate_cut"),
    (re.compile(
        r"\b(?:slowdown|recession|growth\s+slow\w*|china\s+slow\w*|"
        r"economic\s+slow\w*|downturn|hard\s+landing)\b",
        re.IGNORECASE), "slowdown"),
)


# Scenario-POSITIONING verbs/phrasings — the user wants a strategy built
# AROUND a macro scenario, not a generic data lookup. Includes Hinglish
# build cues ("banao", "bana do", "kuch banao").
_POSITIONING_RE = re.compile(
    r"\b(?:profits?\s+from|benefits?\s+from|gains?\s+from|"
    r"hedge\s+(?:against|me\s+against|my\s+portfolio)|"
    r"position\s+(?:me|my\s+portfolio|for)|positioning\s+for|"
    r"play\s+(?:on|the|for)|trade\s+(?:on|the|for|this)|"
    r"strategy\s+(?:that|for|around|to)|"
    r"ride\s+the|capitalis?e\s+on|capitalize\s+on|"
    r"build\s+(?:me\s+)?(?:something|a\s+strategy|a\s+basket|me\s+a)|"
    r"what'?s?\s+the\s+trade|what\s+stocks?\s+(?:benefit|win|gain)|"
    r"which\s+stocks?\s+(?:benefit|win|gain|jeet\w*)|"
    r"who\s+(?:wins?|benefits?)|"
    # Hinglish: "kuch banao", "bana do", "banao mere liye", "kaunse stocks"
    r"ban[ao]\s*(?:do|dijiye)?\b|kuch\s+ban\w*|"
    r"kaun[\s-]?se?\s+stocks?|stocks?\s+jeet\w*"
    r")",
    re.IGNORECASE,
)


def detect_thematic_scenario(message: str) -> Optional[ThematicScenario]:
    """Return the recognised macro scenario when the user is asking to
    POSITION FOR / PROFIT FROM / HEDGE a scenario, else None.

    Two conditions must hold:
      1. A scenario noun is present (war, monsoon, rupee, crude, …).
      2. A positioning verb is present ("profits from", "hedge against",
         "build me something", "kaunse stocks jeetenge", …) — OR the
         scenario noun co-occurs with a build/strategy verb.

    This is intentionally conservative on the positioning side: a bare
    "what's the rupee at" must NOT trigger the thematic path (no
    positioning verb → returns None, the normal quote route handles it).
    """
    if not message:
        return None
    msg = message.strip()
    if len(msg) < 4:
        return None

    matched_key: Optional[str] = None
    for pat, key in _SCENARIO_PATTERNS:
        if pat.search(msg):
            matched_key = key
            break
    if matched_key is None:
        return None

    if not _POSITIONING_RE.search(msg):
        return None

    return _SCENARIO_BY_KEY.get(matched_key)


def winners_losers_block(s: ThematicScenario) -> str:
    """Render the seed winners/losers as a directive fragment the chat
    guard injects so the model has the real NSE names + WHY to table.
    The model may refine these, but it must NOT drop the table or invent
    a generic staples basket in their place."""
    win = "\n".join(f"  - Winner | {tk} | {why}" for tk, why in s.winners)
    los = "\n".join(f"  - Avoid  | {tk} | {why}" for tk, why in s.losers)
    return (
        f"Seed winners (long leg, real NSE tickers — use these, refine "
        f"only with cause):\n{win}\n"
        f"Seed losers (AVOID list — name them, shorting is not wired):\n{los}"
    )


def basket_weights(s: ThematicScenario) -> tuple[tuple[str, int], ...]:
    """A sensible default ₹-split over the winner leg. The first name is
    overweighted; a safe-haven leg (GOLDBEES) keeps its weight. Weights
    sum to 100. The model may adjust, but the card must carry every
    winner with a stated split."""
    n = len(s.winners)
    if n == 0:
        return ()
    if n == 1:
        return ((s.winners[0][0], 100),)
    # Equal-ish with the lead name slightly heavier; round to sum 100.
    base = 100 // n
    weights = [base] * n
    weights[0] += 100 - sum(weights)
    return tuple((s.winners[i][0], weights[i]) for i in range(n))


# ── Vague-onboarding detector (GAN R4 F2/C2) ──────────────────────────
# Zero-spec "where do I start / make money / first salary / what should I
# buy" asks must lead with VALUE: a prefilled NIFTYBEES SIP draft + a
# 3-path table + exactly ONE compound question — never an explainer-only
# blurb or a bare ask_user. This detector fires when the message has an
# onboarding/help shape AND carries NO concrete spec (no explicit ticker,
# no trigger, no order verb that would route it elsewhere).

_VAGUE_ONBOARDING_RE = re.compile(
    r"\b(?:where\s+(?:do\s+i|to)\s+(?:start|begin)|"
    r"how\s+(?:do\s+i|to)\s+(?:start|begin|invest|get\s+started)|"
    r"want\s+(?:my\s+money\s+to\s+grow|to\s+(?:make\s+money|invest|grow))|"
    r"(?:make|grow)\s+(?:me\s+)?money|"
    r"what\s+should\s+i\s+(?:buy|do|invest|pick)|"
    r"help\s+me\s+(?:invest|start|grow|get\s+started)|"
    r"(?:good|best)\s+strategy\s+(?:to\s+start|for\s+(?:a\s+)?beginner)|"
    r"first\s+salary|just\s+got\s+(?:my\s+)?(?:first\s+)?salary|"
    r"new\s+to\s+(?:invest|the\s+market|stocks)|"
    r"beginner|just\s+starting|getting\s+started|"
    # Hinglish: "bas batao kya karu", "kuch acha batao"
    r"bas\s+batao|kya\s+(?:karu|karoon|kharidu)|kuch\s+(?:acha|achha|solid)\s+batao|"
    r"something\s+solid|what\s+to\s+buy"
    r")",
    re.IGNORECASE,
)

# Spec markers that mean the ask is NOT vague — an explicit instrument,
# a trigger, or a concrete order shape. Their presence vetoes the vague
# path so a real spec routes to its proper handler.
_HAS_SPEC_RE = re.compile(
    r"\b(?:rsi|sma|ema|macd|when\s+\w|if\s+\w+\s+(?:rises|falls|drops|crosses)|"
    r"every\s+(?:monday|tuesday|wednesday|thursday|friday|day|week|month)|"
    r"option|call|put|strike|expiry|straddle|strangle|iron\s+condor|"
    r"backtest|simulate)\b",
    re.IGNORECASE,
)


def is_vague_onboarding(message: str) -> bool:
    """True for zero-spec onboarding asks that must get a value-first
    reply (prefilled SIP card + 3-path table + one compound question).
    Returns False the moment a concrete spec (ticker rule / trigger /
    option leg / backtest) is present — those route to their handler."""
    if not message:
        return False
    msg = message.strip()
    if len(msg) < 3:
        return False
    if not _VAGUE_ONBOARDING_RE.search(msg):
        return False
    if _HAS_SPEC_RE.search(msg):
        return False
    return True


def extract_capital_inr(message: str) -> Optional[int]:
    """Best-effort ₹ amount the user already stated, for the vague path
    to USE the capital instead of re-asking it. Handles '2 lakh', '50k',
    '₹1,00,000', '75k', '1 lakh', 'Rs 2L'."""
    if not message:
        return None
    m = message.lower().replace(",", "")
    # lakh / lakhs / l-suffix
    lk = re.search(r"(\d+(?:\.\d+)?)\s*(?:lakh|lakhs|lac|l\b)", m)
    if lk:
        try:
            return int(float(lk.group(1)) * 100_000)
        except ValueError:
            pass
    # k-suffix (50k, 75k)
    kk = re.search(r"(\d+(?:\.\d+)?)\s*k\b", m)
    if kk:
        try:
            return int(float(kk.group(1)) * 1_000)
        except ValueError:
            pass
    # bare rupee figure (₹1,00,000 / rs 50000 / 50000)
    rs = re.search(r"(?:₹|rs\.?|inr)\s*(\d{4,})", m)
    if rs:
        try:
            return int(rs.group(1))
        except ValueError:
            pass
    big = re.search(r"\b(\d{5,})\b", m)
    if big:
        try:
            return int(big.group(1))
        except ValueError:
            pass
    return None


# ── Scared-idle-cash detector (GAN R4 F4) ─────────────────────────────
# "2 lakh idle in savings, scared of losing money, FD feels pathetic, do
# something" must NOT route to compare_yields/get_yield_recommendation
# (FD/G-Sec products are out of scope, register-not-execute can't touch
# them). It gets a scope-honesty line + a phased NIFTYBEES SIP card for
# the riskable slice + a GOLDBEES leg + a paper-mode offer.

_IDLE_CASH_RE = re.compile(
    r"\b(?:idle|sitting|lying|parked)\b.{0,30}\b(?:cash|money|savings|funds?|lakh|account)\b"
    r"|\b(?:cash|money|savings|funds?)\b.{0,20}\b(?:idle|sitting|lying)\b"
    r"|in\s+(?:my\s+)?savings\s+account",
    re.IGNORECASE,
)
_SCARED_RE = re.compile(
    r"\bscared\b|\bafraid\b|\bnervous\b|\bdon'?t\s+want\s+to\s+lose\b"
    r"|fd\s+(?:returns?|rates?)\s+(?:feel|are|seem)\s+(?:pathetic|low|bad|poor)"
    r"|\bdo\s+something\b|\bsafe(?:r)?\b|\blow[\s-]?risk\b",
    re.IGNORECASE,
)


def is_scared_idle_cash(message: str) -> bool:
    """True for risk-averse idle-cash asks that must get the scope-honesty
    + phased-SIP path, not the out-of-scope FD/yield-product route."""
    if not message:
        return False
    msg = message.strip()
    return bool(_IDLE_CASH_RE.search(msg) and _SCARED_RE.search(msg))


# ── Unrealistic-return detector (GAN R4 F5) ───────────────────────────
# "1% a day", "double my money in a month", "guaranteed 5% a week",
# "200% returns" must get a no-mockery math refutation + a real
# RSI-mean-reversion backtest artifact + a SIP fallback — NEVER an
# ask_user agent menu treating the impossible target as a buildable spec.

_UNREALISTIC_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*%?\s*(?:a|per|every|each)\s+(?:day|daily|week|weekly)\b"
    r"|\b(?:double|triple|2x|3x|10x)\b.{0,30}\b(?:money|capital|investment|in\s+a\s+(?:day|week|month))\b"
    r"|\bdouble\s+(?:my\s+)?money\b"
    r"|\bguarantee\w*\b.{0,20}\b(?:return|profit|\d+\s*%)\b"
    r"|\b(?:make\s+me|i\s+want)\s+\d+\s*%\s*(?:a|per)\s+(?:day|week)\b"
    r"|\bmake\s+me\s+1\s*%\s*a\s+day\b"
    r"|\b\d{3,}\s*%\s*(?:returns?|profits?|gains?)\b"  # 200% returns
    r"|\b(?:get\s+)?rich\s+(?:quick|fast)\b",
    re.IGNORECASE,
)


def is_unrealistic_return(message: str) -> bool:
    """True for impossible-return targets ('1% a day', 'double in a
    month', 'guaranteed N%'). The chat layer routes these to a math
    refutation + a real backtest artifact + a SIP fallback."""
    if not message:
        return False
    return bool(_UNREALISTIC_RE.search(message.strip()))
