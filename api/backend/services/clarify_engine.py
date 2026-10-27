"""Dynamic, VOI-ranked clarifying-question engine (Workstream A).

Authoritative spec: ``docs/plans/STRATEGY_BUILDER_AND_QUESTIONS_PLAN.md`` §2
(§2a metric, §2b generate→rank→validate→render, §2c stopping rule, §2d data
shape). This module owns the *centrepiece* of the strategy feature: asking
**only** what would change the build, generating every question **per request**
(never a hardcoded list), and folding the answers back into the
:class:`SlotState` the Workstream-B builder consumes.

What lives here (pure-ish; the only I/O is one — at most two — LLM calls plus a
read-only ``screen_fundamentals`` / ``sector_universe`` peek to ground options):

  * **Slot inference** (§2b.1) — classify each strategy-build slot as
    *specified*, *inferable*, or *unknown-AND-decision-relevant* from the
    request text. Only the third class is eligible to become a question.
  * **Candidate generation** (§2b.2) — one LLM call emits ~8-10 candidate
    questions covering ONLY the unknown+decision-relevant slots, each with
    4-5 options **grounded in the concrete request** (the real tickers /
    structures / sectors the request implies), enforcing MECE + usage-framing.
    The prompt holds *how to generate*, never *the questions themselves*.
  * **VOI ranking** (§2a) — ``score(q) = StrategyEIG(q) − λ·BurdenCost(q)``
    using the single-pass surrogate: the same LLM call estimates, per
    candidate, 0-1 how much the answer would change the build (no N full
    builds). Burden penalises already-specified / already-asked / high-load
    aspects. Keep the top ``k``.
  * **Validation** (§2b.4) — MECE / dedupe / grounding rejection of bad
    options; de-dup against the request and prior turns.
  * **Stopping rule** (§2c) — a skip-entirely gate (:func:`should_ask`, run
    FIRST), a per-question ``τ_q`` gate, a hard budget of
    :data:`~backend.services.strategy_contracts.MAX_CLARIFY_QUESTIONS`, and an
    early-stop when marginal VOI falls below ``α·confidence``. "Just build it"
    / skip are honoured by the caller via the returned card's affordances.

Public API (implements the :class:`ClarifyEngine` Protocol from
``strategy_contracts``, dropping ``self`` for module-level functions):

  * :func:`should_ask`              — the skip-entirely gate.
  * :func:`generate_clarify_card`   — generate→rank→validate→stop → ClarifyCard.

Cross-cutting contracts that DO NOT change: register-not-execute, the
not-advice disclaimer (both enforced downstream by the builder), and per-session
isolation (the card carries ``session_slot_state`` so the FE round-trips the
slots in-band — there is no new endpoint, per the approved open-decision
default).

Style: ``from __future__ import annotations``, Pydantic v2, ``Literal`` enums,
strict typing throughout. No I/O at import time.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Literal, Optional

from backend.llm import LLMMessage, get_llm_client
from backend.services.strategy_contracts import (
    MAX_CLARIFY_QUESTIONS,
    ClarifyCard,
    ClarifyOption,
    ClarifyQuestion,
    SlotState,
)

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from backend.llm import LLMResponse

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Tunables (the VOI metric constants from plan §2a / §2c)
# ════════════════════════════════════════════════════════════════════════════

# score(q) = StrategyEIG(q) − λ·BurdenCost(q)   (plan §2a)
_LAMBDA_BURDEN: float = 0.35
"""Burden weight λ. Penalises cognitive load + redundancy so a high-EIG but
heavy question can still be pruned. SAGE-Agent's ``Cost(q)=λ·Σ_a n_a`` term."""

_TAU_Q: float = 0.34
"""Per-question gate ``τ_q`` (plan §2c). Keep ``q`` only if its post-burden VOI
clears this — auto-prunes low-value questions ("its answer must materially
change the build")."""

_TAU_HIGH_SPECIFICITY: float = 0.74
"""Skip-entirely threshold ``τ_high`` (plan §2c). When the request is already
this specific (few decision-relevant unknowns remain), ask nothing.

Tuned to 0.74 (was 0.78) so a request that pins ~4 of the 5 high-leverage
build levers (risk + capital + horizon + theme, e.g. "aggressive ₹2L 5-year
quality-compounder portfolio" → spec≈0.78) builds DIRECTLY instead of
over-asking about the one remaining low/mid-VOI slot. The genuinely-vague band
("build me a strategy" → spec≈0.44; "invest ₹2L for me" → spec≈0.64) sits well
below this line and still triggers the clarify card. See the self-check at the
foot of this module."""

_ALPHA_EARLY_STOP: float = 0.55
"""Early-stop coefficient α (plan §2c): stop adding questions once the next
candidate's VOI < α · (1 − running specificity). Keeps the card short."""

_N_CANDIDATES_TARGET: int = 9
"""Target candidate pool size before ranking (~8-10, plan §2b.2)."""

_MIN_OPTIONS: int = 3
"""A grounded MECE question needs at least this many concrete options (before
the catch-all + skip affordances) to be worth surfacing."""

_MAX_OPTIONS: int = 5
"""Cap on concrete options per question (4-5, plan §2b.2). Excess is trimmed."""


# The decision-relevant strategy-build slots (plan §2b.1 / §3 builder inputs).
# These are the ONLY slots a generated question may target — there is no
# hardcoded *question* list anywhere, only this closed set of build levers.
_SLOT_FIELDS: tuple[str, ...] = (
    "view",
    "risk",
    "horizon",
    "capital_inr",
    "asset_prefs",
    "theme",
)

SlotClass = Literal["specified", "inferable", "unknown_relevant"]
"""Per-slot inference verdict (plan §2b.1). Only ``unknown_relevant`` slots are
eligible to become questions."""


# ════════════════════════════════════════════════════════════════════════════
# Slot inference (plan §2b.1)
# ════════════════════════════════════════════════════════════════════════════

# Lightweight, deterministic cue lexicons. These do NOT decide the *questions*
# (those are generated); they only decide which build levers the request has
# already pinned, so we never ask about something the user already stated.
_VIEW_CUES: tuple[str, ...] = (
    "bull", "bullish", "bear", "bearish", "long ", "short ", "rise", "fall",
    "up ", "down ", "rally", "crash", "drop", "neutral", "sideways", "range",
    "expect", "think it", "view", "rate cut", "rate-cut", "recover",
)
_RISK_CUES: tuple[str, ...] = (
    "conservative", "aggressive", "balanced", "safe", "low risk", "low-risk",
    "high risk", "high-risk", "defensive", "risky", "cautious", "moonshot",
    "preserve", "capital preservation", "don't want to lose", "stable",
)
_HORIZON_CUES: tuple[str, ...] = (
    "long term", "long-term", "short term", "short-term", "tactical",
    "swing", "intraday", "year", "yr", "month", "decade", "retire", "sip",
    "hold for", "5 year", "10 year", "1 year", "medium term",
)
_THEME_CUES: tuple[str, ...] = (
    "quality", "value", "momentum", "compounder", "dividend", "growth",
    "defensive", "esg", "psu", "rate-cut", "rate cut", "ev ", "green",
    "ai ", "infra", "consumption", "export", "monsoon", "inflation",
)
# A STATED factor / macro-scenario / event-positioning view. Any of these
# FILLS the view slot on its own (doctrine: baskets.md / events.md) — the ask
# is then *sufficiently specified* to build directly with assumed capital +
# horizon, so we must NOT open with a clarify card. Deterministic, narrow: a
# named factor, a recognised macro theme, or a named macro/event beneficiary.
_FACTOR_SCENARIO_VIEW_CUES: tuple[str, ...] = (
    # factors
    "momentum", "low vol", "low-vol", "min vol", "min-vol", "low volatility",
    "quality", "value", "dividend", "compounder", "growth stocks",
    "high beta", "high-beta", "defensive",
    # macro scenarios / themes
    "monsoon", "drought", "rural", "rate cut", "rate-cut", "rupee",
    "depreciat", "crude", "oil spike", "war", "conflict", "inflation",
    "slowdown", "recession",
    # growth-story / sector themes
    "ev ", "electric vehicle", "supply chain", "semiconductor", "chip",
    "renewable", "solar", "clean energy", "hydrogen", "manufacturing",
    "defence", "defense", "infra", "consumption", "export", "capex",
    "upcycle", "supercycle",
    # named macro / policy events (event-positioning fills the view slot)
    "rbi", "mpc", "fomc", "fed ", "the fed", "cpi", "budget", "election",
    "earnings season", "results season",
)
_GOLD_CUES: tuple[str, ...] = ("gold", "sgb", "goldbees", "bullion")
_ETF_CUES: tuple[str, ...] = ("etf", "index fund", "mutual fund", "nifty bees",
                              "niftybees", "mf ", "passive")
_OPTIONS_CUES: tuple[str, ...] = ("option", "call", "put", "spread", "straddle",
                                  "strangle", "iron condor", "f&o", "fno")

# Capital is detected with the project's existing ₹-parser, imported lazily to
# keep this module import-light.


def _detect_capital(request: str) -> Optional[int]:
    """Reuse the project's ₹ parser so '2 lakh', '50k', '₹1,00,000' all land."""
    try:
        from backend.services.thematic_map import extract_capital_inr
    except Exception:  # pragma: no cover - defensive import guard
        return None
    try:
        return extract_capital_inr(request)
    except Exception:  # pragma: no cover
        return None


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(n in haystack for n in needles)


def classify_slots(request: str, slots: SlotState) -> dict[str, SlotClass]:
    """Classify every build slot as ``specified`` / ``inferable`` /
    ``unknown_relevant`` (plan §2b.1).

    A slot is:
      * ``specified``       — the user pinned it (request cue) OR a non-assumed
        value already sits in ``slots`` (an earlier answer this conversation).
      * ``inferable``       — a sensible default exists and the slot is NOT
        decision-relevant for this particular request (so a default is safe and
        we should NOT spend a question on it).
      * ``unknown_relevant``— neither pinned nor safely defaultable: its answer
        could materially change the build. ONLY these become questions.

    The verdict is deterministic and cheap; it gates *which* slots the LLM may
    generate questions for. It never authors the questions.
    """
    text = (request or "").lower()
    verdict: dict[str, SlotClass] = {}

    # A slot already carries a real (non-assumed) answer ⇒ specified, never ask.
    assumed = slots.assumed

    # ── risk (classified first — gates view + asset_prefs below) ─────────────
    risk_specified = bool(not assumed.risk) or _contains_any(text, _RISK_CUES)
    if risk_specified:
        verdict["risk"] = "specified"
    else:
        # Risk drives the weighting scheme + defined-vs-undefined ⇒ relevant.
        verdict["risk"] = "unknown_relevant"

    # ── view ────────────────────────────────────────────────────────────────
    # A directional view flips structure ONLY for a tactical/directional bet.
    # For a passive long-horizon portfolio ("build me a long-term basket"),
    # "neutral / no strong view" is a SAFE default ⇒ don't burn a question on
    # it. We only treat an unstated view as decision-relevant when the request
    # itself reads tactical/aggressive (where the view materially tilts the
    # build) AND nothing else pins it. This stops a risk+horizon+capital-
    # specified portfolio from being held back to ask about a view it doesn't
    # need (the over-asking bug).
    if not assumed.view and slots.view.direction != "none":
        verdict["view"] = "specified"
    elif _contains_any(text, _VIEW_CUES):
        verdict["view"] = "specified"
    elif _contains_any(text, ("aggressive", "tactical", "swing", "moonshot",
                              "high risk", "high-risk", "bet")):
        # Tactical / aggressive framing → an unstated view is high-VOI.
        verdict["view"] = "unknown_relevant"
    else:
        # Passive / unstated-risk build → neutral view is a safe default.
        verdict["view"] = "inferable"

    # ── horizon ───────────────────────────────────────────────────────────────
    if not assumed.horizon:
        verdict["horizon"] = "specified"
    elif _contains_any(text, _HORIZON_CUES):
        verdict["horizon"] = "specified"
    else:
        # Horizon mostly nudges the gold ballast; 'medium' is a safe default
        # UNLESS the request reads conservative/long (then it earns a question).
        if _contains_any(text, ("safe", "preserve", "retire", "long")):
            verdict["horizon"] = "unknown_relevant"
        else:
            verdict["horizon"] = "inferable"

    # ── capital ───────────────────────────────────────────────────────────────
    if slots.capital_inr is not None or _detect_capital(request) is not None:
        verdict["capital_inr"] = "specified"
    else:
        # Capital gates #names / lots / SGB tickets and feasibility — a big
        # build lever ⇒ relevant when unknown.
        verdict["capital_inr"] = "unknown_relevant"

    # ── asset_prefs ───────────────────────────────────────────────────────────
    pinned_assets = (
        _contains_any(text, _GOLD_CUES)
        or _contains_any(text, _ETF_CUES)
        or _contains_any(text, _OPTIONS_CUES)
        or _contains_any(text, ("only stocks", "just stocks", "equity only",
                                "no gold", "no derivatives", "no options"))
    )
    if not assumed.asset_prefs or pinned_assets:
        verdict["asset_prefs"] = "specified"
    elif risk_specified:
        # Once risk is pinned, the gold/ETF ballast is DERIVABLE (conservative
        # / long ⇒ add a ballast sleeve; aggressive ⇒ none). A safe default
        # exists ⇒ don't spend a question on it — avoids over-asking on a
        # request that already named its risk + capital + horizon.
        verdict["asset_prefs"] = "inferable"
    else:
        # Risk also unknown ⇒ whether to include a gold/ETF sleeve is genuinely
        # open and changes the structure ⇒ relevant.
        verdict["asset_prefs"] = "unknown_relevant"

    # ── theme ─────────────────────────────────────────────────────────────────
    if slots.theme or _contains_any(text, _THEME_CUES):
        verdict["theme"] = "specified"
    else:
        # Theme is a tilt; default 'broad quality' is fine ⇒ inferable, not a
        # question (avoids over-asking on a low-VOI slot).
        verdict["theme"] = "inferable"

    return verdict


# ════════════════════════════════════════════════════════════════════════════
# Grounding peek (read-only sector / fundamentals context for the LLM prompt)
# ════════════════════════════════════════════════════════════════════════════


def _extract_symbols(request: str) -> list[str]:
    """Pull candidate NSE tickers the request names verbatim (e.g. TCS, INFY).

    Conservative: uppercase tokens 2-12 chars that aren't English/Hinglish
    filler. Used only to *ground* generated options in real instruments — a
    miss just means the LLM grounds from the sector peek instead.
    """
    if not request:
        return []
    raw = re.findall(r"\b[A-Z][A-Z&]{1,11}\b", request)
    stop = {
        "AND", "OR", "THE", "FOR", "WITH", "BUY", "SELL", "SIP", "ETF", "MF",
        "PE", "ROE", "PB", "RSI", "SMA", "IV", "OI", "PCR", "NSE", "BSE",
        "INR", "RS", "I", "A", "TO", "IN", "ON", "AT", "IF", "F", "O",
    }
    out: list[str] = []
    for tok in raw:
        if tok in stop or len(tok) < 2:
            continue
        if tok not in out:
            out.append(tok)
    return out[:6]


def _grounding_context(request: str, slots: SlotState, ctx: object) -> dict[str, Any]:
    """Assemble a small, read-only grounding block so generated options point at
    *real* instruments/sectors the request implies (plan §2b.2 grounding).

    Pulls, best-effort and never raising:
      * named tickers in the request,
      * the request's sector/theme universe (top names by mcap) via the static
        ``sector_universe`` screener — no DB round-trip, microseconds,
      * the catalogue of known sectors (so a sector question stays MECE).

    The fundamentals DB is intentionally NOT hit here (latency); the builder
    does the heavy gate later. This is just enough to keep options concrete.
    """
    block: dict[str, Any] = {
        "named_symbols": _extract_symbols(request),
        "sector_universe_sample": [],
        "known_sectors": [],
    }
    text = (request or "").lower()
    try:
        from backend.services import sector_universe as su

        block["known_sectors"] = su.known_sectors()

        # Resolve a theme/sector the request hints at and pull a few real names
        # so e.g. "banking basket" grounds in HDFCBANK/ICICIBANK, not abstractions.
        sector: Optional[str] = None
        if slots.theme:
            mapping = su.resolve_theme(slots.theme)
            if mapping is not None and mapping.sectors:
                sector = mapping.sectors[0]
        if sector is None:
            for s in su.known_sectors():
                if s.replace("_", " ") in text or s in text:
                    sector = s
                    break
        sample = su.query_screener(sector=sector, limit=6) if sector else \
            su.query_screener(limit=6)
        block["sector_universe_sample"] = [
            {"symbol": r["symbol"], "name": r["name"], "sector": r["sector"]}
            for r in sample
        ]
        if sector:
            block["resolved_sector"] = sector
    except Exception as exc:  # pragma: no cover - grounding is best-effort
        logger.info("clarify grounding peek failed (continuing): %s", exc)
    return block


# ════════════════════════════════════════════════════════════════════════════
# Candidate generation + single-pass VOI surrogate (one LLM call) — plan §2b.2/§2a
# ════════════════════════════════════════════════════════════════════════════

# The prompt holds HOW TO GENERATE + HOW TO SCORE — never the questions. This is
# the structural guarantee against a hardcoded questionnaire (plan §6).
_GENERATOR_SYSTEM_PROMPT: str = """\
You are Pivot's clarifying-question generator for an Indian-retail strategy/
basket builder. Your job is to propose the SHORT LIST of questions that — if
answered — would most change HOW WE BUILD the user's strategy. You do NOT
answer; you only generate candidate questions and estimate their value.

HARD RULES
- Generate questions ONLY for the slots listed in `eligible_slots`. Never ask
  about a slot the user already specified.
- Every question must use USAGE-FRAMING ("what do you want to do / what's your
  read?"), never abstract attribute quizzing.
- Each question's options MUST be GROUNDED in this concrete request: use the
  real tickers in `grounding.named_symbols`, the real names in
  `grounding.sector_universe_sample`, and the real `grounding.known_sectors`.
  For an options/structure question, name concrete structures implied by the
  request — not "stocks vs bonds".
- Options must be MECE: mutually exclusive (no two options overlap) and, with
  the implicit "Something else"/"Skip" affordances, collectively exhaustive.
  Give 4-5 concrete options per question.
- Do NOT duplicate a question already asked (see `already_asked`).

VOI ESTIMATION (single-pass surrogate — do NOT simulate full builds)
For each candidate, estimate two numbers in [0,1]:
- `eig`  = StrategyEIG: if the user answered each way, how DIFFERENTLY would the
  resulting strategy be built (direction, weighting scheme, leg/sleeve
  structure, sizing, instrument)? 1.0 = the answer flips the whole build,
  0.0 = the build is unchanged whatever they say.
- `burden` = cognitive load + redundancy of asking it (0 = trivial/obvious,
  1 = heavy or partly already implied).

OUTPUT — return ONLY a JSON object, no prose:
{
  "questions": [
    {
      "slot": "<one of eligible_slots>",
      "prompt": "<the question text, usage-framed>",
      "eig": <0..1>,
      "burden": <0..1>,
      "options": [{"id": "<stable_snake_id>", "label": "<chip text>"}, ...]
    }
  ]
}
Produce about 8-10 candidates spread across the eligible slots, best first.
"""


def _build_generator_user_payload(
    request: str,
    slots: SlotState,
    eligible: list[str],
    grounding: dict[str, Any],
    already_asked: list[str],
) -> str:
    """Serialise the request context the generator reasons over. Holds only the
    *inputs* — the LLM authors the questions."""
    payload = {
        "request": request,
        "eligible_slots": eligible,
        "slot_descriptions": {
            "view": "directional read (bull/bear/neutral/none) and its target",
            "risk": "conservative / balanced / aggressive appetite",
            "horizon": "tactical (<1y) / medium (1-5y) / long (5y+)",
            "capital_inr": "investable ₹ amount (gates #names, lots, SGB tickets)",
            # Only name sleeves the builder can actually construct today
            # (equity screen + a gold SGB/GOLDBEES sleeve) — the generator
            # LLM otherwise echoes whatever this hint lists as if it were
            # buildable, and it isn't (build_strategy is equity+gold only;
            # ETF/MF sleeves aren't implemented). Never re-add "ETF-MF" here
            # without also building that sleeve in strategy_builder.py.
            "asset_prefs": "which sleeves to allow: equity / gold",
            "theme": "thematic tilt (quality, value, rate-cut beneficiaries, …)",
        },
        "grounding": grounding,
        "already_asked": already_asked,
        "target_candidate_count": _N_CANDIDATES_TARGET,
        "options_per_question": f"{_MIN_OPTIONS}-{_MAX_OPTIONS}",
    }
    return json.dumps(payload, ensure_ascii=False)


async def _generate_candidates(
    request: str,
    slots: SlotState,
    eligible: list[str],
    grounding: dict[str, Any],
    already_asked: list[str],
) -> list[dict[str, Any]]:
    """One LLM call → raw candidate questions WITH their eig/burden estimates
    (the single-pass VOI surrogate, plan §2a/§2b). Returns ``[]`` on any error
    so the caller degrades to "no clarification" rather than failing the turn.
    """
    if not eligible:
        return []
    client = get_llm_client()
    messages = [
        LLMMessage(role="system", content=_GENERATOR_SYSTEM_PROMPT),
        LLMMessage(
            role="user",
            content=_build_generator_user_payload(
                request, slots, eligible, grounding, already_asked
            ),
        ),
    ]
    try:
        resp: "LLMResponse" = await client.complete(
            messages=messages,
            tools=None,
            tool_choice="none",
            max_output_tokens=3000,
            temperature=0.4,
            reasoning_effort="low",
            response_format="json_object",
            prompt_cache_key="clarify_generate_v1",
        )
    except Exception as exc:
        logger.info("clarify candidate generation LLM call failed: %s", exc)
        return []

    if resp.finish_reason == "error":
        logger.info("clarify generation returned error finish_reason")
        return []

    raw = (resp.content or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.info("clarify generation returned non-JSON; discarding")
        return []

    candidates = parsed.get("questions") if isinstance(parsed, dict) else None
    if not isinstance(candidates, list):
        return []
    return [c for c in candidates if isinstance(c, dict)]


# ════════════════════════════════════════════════════════════════════════════
# Validation — MECE / dedupe / grounding (plan §2b.4)
# ════════════════════════════════════════════════════════════════════════════


def _norm_label(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _slugify(s: str, fallback: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
    return out or fallback


def _validate_options(
    raw_options: list[Any], grounding: dict[str, Any]
) -> list[ClarifyOption]:
    """MECE + grounding filter on one question's options (plan §2b.4).

    Rejects: duplicate/overlapping labels (MECE), empty labels. Caps at
    :data:`_MAX_OPTIONS`. Grounding is enforced softly — at least one option
    must reference a concrete instrument/sector OR the question is dropped by
    the caller when fewer than :data:`_MIN_OPTIONS` survive.
    """
    seen: set[str] = set()
    out: list[ClarifyOption] = []
    for idx, opt in enumerate(raw_options or []):
        if not isinstance(opt, dict):
            continue
        label = str(opt.get("label") or "").strip()
        if not label:
            continue
        norm = _norm_label(label)
        if not norm or norm in seen:
            continue  # MECE: drop exact/normalised duplicates
        seen.add(norm)
        oid = str(opt.get("id") or "").strip() or _slugify(label, f"opt_{idx}")
        out.append(ClarifyOption(id=oid, label=label))
        if len(out) >= _MAX_OPTIONS:
            break
    return out


def _question_is_grounded(q_options: list[ClarifyOption], grounding: dict[str, Any]) -> bool:
    """Soft grounding check: at least one option references a real instrument /
    sector / concrete number from the grounding block, OR the option set is a
    clean qualitative axis (risk/horizon/view words). Pure-abstraction option
    sets with no concrete anchor are rejected."""
    anchors: set[str] = set()
    for sym in grounding.get("named_symbols", []) or []:
        anchors.add(_norm_label(str(sym)))
    for row in grounding.get("sector_universe_sample", []) or []:
        anchors.add(_norm_label(str(row.get("symbol", ""))))
        anchors.add(_norm_label(str(row.get("name", ""))))
    for sec in grounding.get("known_sectors", []) or []:
        anchors.add(_norm_label(str(sec)))
    anchors.discard("")

    # Qualitative axes are inherently grounded (they ARE the build lever).
    qualitative = {
        "bull", "bear", "neutral", "conservative", "balanced", "aggressive",
        "long", "medium", "tactical", "term", "gold", "equity", "etf", "safe",
        "growth", "value", "quality", "momentum", "dividend",
    }
    for opt in q_options:
        nl = _norm_label(opt.label)
        if any(a and a in nl for a in anchors):
            return True
        if any(w in nl for w in qualitative):
            return True
        # A concrete number (₹ amount, %, count) is also an anchor.
        if re.search(r"\d", nl):
            return True
    return False


def _voi_score(eig: float, burden: float) -> float:
    """``score(q) = StrategyEIG(q) − λ·BurdenCost(q)`` (plan §2a), clamped to
    [0,1] so it composes with the per-question gate and early-stop cleanly."""
    raw = eig - _LAMBDA_BURDEN * burden
    return max(0.0, min(1.0, raw))


def _coerce_unit(x: Any, default: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


# ════════════════════════════════════════════════════════════════════════════
# Specificity / skip-entirely signal (plan §2c)
# ════════════════════════════════════════════════════════════════════════════


def _request_specificity(request: str, slots: SlotState) -> float:
    """A 0-1 "how pinned-down is this build already?" score, derived from the
    slot classification — the operational form of the routing-confidence signal
    the plan references (no numeric confidence is emitted by the router, so we
    compute decision-relevant specificity directly).

    1.0 ⇒ every build lever is specified/inferable (nothing worth asking) ⇒
    skip-entirely. Lower ⇒ more unknown+decision-relevant slots remain.
    """
    verdict = classify_slots(request, slots)
    if not verdict:
        return 1.0
    # Weight the high-leverage slots (view, capital, risk) more heavily.
    weights = {
        "view": 1.3,
        "risk": 1.1,
        "capital_inr": 1.2,
        "asset_prefs": 1.0,
        "horizon": 0.7,
        "theme": 0.6,
    }
    total = 0.0
    pinned = 0.0
    for slot, cls in verdict.items():
        w = weights.get(slot, 1.0)
        total += w
        if cls != "unknown_relevant":
            pinned += w
    if total <= 0:
        return 1.0
    return pinned / total


# ════════════════════════════════════════════════════════════════════════════
# PUBLIC API — implements the ClarifyEngine Protocol (strategy_contracts)
# ════════════════════════════════════════════════════════════════════════════


def should_ask(request: str, slots: SlotState, ctx: object) -> bool:
    """The **skip-entirely gate** (plan §2c) — run FIRST, before any LLM call.

    Returns ``False`` (build directly, ask nothing) when the request is already
    specific enough that clarifying would not change the build: its derived
    specificity ≥ ``τ_high``, OR there are no unknown+decision-relevant slots
    at all. Returns ``True`` only when at least one high-leverage build lever is
    both unknown and decision-relevant — i.e. an answer could materially change
    the structure. "Don't ask on reflex."

    Cheap and deterministic (no I/O) so the caller can gate the expensive
    generation call behind it.
    """
    # A STATED factor / macro-scenario / event-positioning view FILLS the view
    # slot on its own (doctrine: baskets.md / events.md). Such an ask is
    # sufficiently specified — build directly with assumed capital + horizon,
    # never open with a clarify card. (Capital/horizon are soft defaults, not
    # blockers, when a view is present.) Reserve clarify for asks with NO view.
    if _contains_any((request or "").lower(), _FACTOR_SCENARIO_VIEW_CUES):
        return False
    verdict = classify_slots(request, slots)
    unknown_relevant = [s for s, c in verdict.items() if c == "unknown_relevant"]
    if not unknown_relevant:
        return False
    specificity = _request_specificity(request, slots)
    if specificity >= _TAU_HIGH_SPECIFICITY:
        return False
    return True


async def generate_clarify_card(
    request: str,
    slots: SlotState,
    ctx: object,
) -> Optional[ClarifyCard]:
    """Generate→rank→validate→stop and return a :class:`ClarifyCard` (or ``None``
    to skip clarification entirely). Plan §2b/§2c.

    Pipeline:
      1. **Skip-entirely gate** (:func:`should_ask`) — bail to ``None`` fast when
         the request is specific, sparing the LLM call.
      2. **Slot inference** — the unknown+decision-relevant slots are the ONLY
         eligible question targets.
      3. **Grounding peek** — read-only sector/ticker context so options are
         concrete.
      4. **Candidate generation + single-pass VOI surrogate** — one LLM call
         emits ~8-10 candidates each carrying its own ``eig``/``burden``.
      5. **VOI rank + validate** — ``score = eig − λ·burden``; MECE/dedupe/
         grounding reject; de-dup slots against prior turns.
      6. **Stopping rule** — per-question ``τ_q`` gate, hard budget
         (:data:`MAX_CLARIFY_QUESTIONS`), early-stop on marginal VOI.

    The returned card embeds the current ``session_slot_state`` so the FE
    round-trips it in-band on the next user message (no new endpoint).
    """
    # ── 1. Skip-entirely gate ────────────────────────────────────────────────
    if not should_ask(request, slots, ctx):
        logger.info("clarify: skip-entirely gate fired; building directly")
        return None

    # ── 2. Slot inference → eligible targets ─────────────────────────────────
    verdict = classify_slots(request, slots)
    eligible = [s for s in _SLOT_FIELDS if verdict.get(s) == "unknown_relevant"]
    if not eligible:
        return None

    already_asked = _prior_asked_slots(ctx)

    # ── 3. Grounding peek ────────────────────────────────────────────────────
    grounding = _grounding_context(request, slots, ctx)

    # ── 4. Candidate generation + VOI surrogate (one LLM call) ───────────────
    raw_candidates = await _generate_candidates(
        request, slots, eligible, grounding, already_asked
    )
    if not raw_candidates:
        logger.info("clarify: no candidates generated; skipping")
        return None

    # ── 5. Validate + score each candidate ───────────────────────────────────
    eligible_set = set(eligible)
    asked_set = set(already_asked)
    scored: list[tuple[float, ClarifyQuestion]] = []
    seen_slots: set[str] = set()
    seen_prompts: set[str] = set()

    for idx, cand in enumerate(raw_candidates):
        slot = str(cand.get("slot") or "").strip()
        if slot not in eligible_set:
            continue  # only the unknown+decision-relevant slots are askable
        if slot in seen_slots or slot in asked_set:
            continue  # one question per slot; never re-ask a prior-turn slot

        prompt_text = str(cand.get("prompt") or "").strip()
        if not prompt_text:
            continue
        norm_prompt = _norm_label(prompt_text)
        if norm_prompt in seen_prompts:
            continue  # dedupe near-identical prompts

        options = _validate_options(cand.get("options", []), grounding)
        if len(options) < _MIN_OPTIONS:
            continue  # not enough MECE options to be a real question
        if not _question_is_grounded(options, grounding):
            continue  # reject pure-abstraction option sets

        eig = _coerce_unit(cand.get("eig"), default=0.5)
        burden = _coerce_unit(cand.get("burden"), default=0.3)
        score = _voi_score(eig, burden)
        if score < _TAU_Q:
            continue  # per-question gate: must materially change the build

        question = ClarifyQuestion(
            id=f"q_{slot}",
            slot=slot,
            prompt=prompt_text,
            voi=round(score, 4),
            options=options,
            free_text=True,
            skippable=True,
        )
        scored.append((score, question))
        seen_slots.add(slot)
        seen_prompts.add(norm_prompt)

    if not scored:
        logger.info("clarify: all candidates pruned below τ_q; skipping")
        return None

    # ── 6. Rank + stopping rule (budget + early-stop) ────────────────────────
    scored.sort(key=lambda t: t[0], reverse=True)
    base_specificity = _request_specificity(request, slots)

    kept: list[ClarifyQuestion] = []
    running_specificity = base_specificity
    for score, question in scored:
        if len(kept) >= MAX_CLARIFY_QUESTIONS:
            break
        # Early-stop: once the next question's VOI no longer clears
        # α·(remaining ambiguity), the marginal value is too low to ask.
        remaining_ambiguity = max(0.0, 1.0 - running_specificity)
        if kept and score < _ALPHA_EARLY_STOP * remaining_ambiguity:
            break
        kept.append(question)
        # Asking this question is expected to pin its slot ⇒ specificity rises.
        running_specificity = min(1.0, running_specificity + score * 0.15)

    if not kept:
        return None

    return ClarifyCard(
        session_slot_state=slots,
        total=len(kept),
        index=0,
        questions=kept,
    )


# ════════════════════════════════════════════════════════════════════════════
# Prior-turn bookkeeping (de-dup against questions already asked)
# ════════════════════════════════════════════════════════════════════════════


def _prior_asked_slots(ctx: object) -> list[str]:
    """Best-effort list of slots already asked this conversation, so we never
    re-ask one (plan §2b.4 / §2c "never re-ask it").

    ``ctx`` is loose-typed; we read an optional ``asked_clarify_slots`` (a list
    of slot names) if the turn context carries one, otherwise return ``[]``.
    The caller (chat_service) owns persisting this into the in-band slot-state
    round-trip; this module only consumes it defensively.
    """
    if ctx is None:
        return []
    candidate = getattr(ctx, "asked_clarify_slots", None)
    if candidate is None and isinstance(ctx, dict):
        candidate = ctx.get("asked_clarify_slots")
    if not isinstance(candidate, (list, tuple, set)):
        return []
    out: list[str] = []
    for s in candidate:
        name = str(s).strip()
        if name and name in _SLOT_FIELDS and name not in out:
            out.append(name)
    return out


# ════════════════════════════════════════════════════════════════════════════
# Answer ingestion (plan §2f) — fold one clarify answer into the slot-state
# ════════════════════════════════════════════════════════════════════════════

# Closed vocabularies the slot enums accept. Used to map a chip id / label /
# free-text answer onto a valid slot value (anything unrecognised leaves the
# slot at its default + assumed, never fabricating an enum the contract forbids).
_VIEW_DIR_VALUES = ("bull", "bear", "neutral", "none")
_RISK_VALUES = ("conservative", "balanced", "aggressive")
_HORIZON_VALUES = ("tactical", "medium", "long")

# Free-text synonyms → canonical enum value. Lets "bullish"/"safe"/"long term"
# resolve even when the user types prose instead of tapping a chip.
_VIEW_SYNONYMS: dict[str, str] = {
    "bull": "bull", "bullish": "bull", "up": "bull", "rise": "bull",
    "rising": "bull", "rally": "bull", "rallies": "bull", "uptrend": "bull",
    "long": "bull", "positive": "bull", "higher": "bull", "grow": "bull",
    "bear": "bear", "bearish": "bear", "down": "bear", "fall": "bear",
    "falling": "bear", "decline": "bear", "downtrend": "bear", "crash": "bear",
    "short": "bear", "negative": "bear", "lower": "bear", "drop": "bear",
    "neutral": "neutral", "sideways": "neutral", "range": "neutral",
    "rangebound": "neutral", "flat": "neutral", "choppy": "neutral",
    "none": "none", "no view": "none", "passive": "none", "own it": "none",
    "no opinion": "none", "agnostic": "none",
}
_RISK_SYNONYMS: dict[str, str] = {
    "conservative": "conservative", "safe": "conservative",
    "defensive": "conservative", "low": "conservative", "low risk": "conservative",
    "cautious": "conservative", "preserve": "conservative", "stable": "conservative",
    "balanced": "balanced", "moderate": "balanced", "medium": "balanced",
    "aggressive": "aggressive", "high": "aggressive", "high risk": "aggressive",
    "risky": "aggressive", "moonshot": "aggressive", "growth": "aggressive",
}
_HORIZON_SYNONYMS: dict[str, str] = {
    "tactical": "tactical", "short": "tactical", "short term": "tactical",
    "swing": "tactical", "trade": "tactical", "intraday": "tactical",
    "medium": "medium", "medium term": "medium", "few years": "medium",
    "long": "long", "long term": "long", "decade": "long", "retire": "long",
    "forever": "long", "hold": "long",
}


def _resolve_enum(answer: str, values: tuple[str, ...],
                  synonyms: dict[str, str]) -> Optional[str]:
    """Map a free-text / chip answer onto a closed enum value, or None."""
    a = (answer or "").strip().lower()
    if not a:
        return None
    if a in values:
        return a
    if a in synonyms:
        return synonyms[a]
    # Substring fallback: the chip label often embeds the canonical word
    # ("Bullish — expect it to rise" → "bull").
    for key, val in synonyms.items():
        if key in a:
            return val
    return None


def normalize_answer_into_slots(
    question: "ClarifyQuestion", answer: str, slots: SlotState,
) -> SlotState:
    """Fold ONE clarify answer into the travelling :class:`SlotState` (plan §2f).

    ``answer`` may be a chip ``id``/``label`` or free text. We resolve it to the
    target slot's value and clear that slot's ``assumed`` flag. An unrecognised
    answer leaves the slot at its default (still ``assumed``) — never fabricating
    a value the contract enums forbid. Mutates and returns ``slots``.

    Only the closed set of build levers (:data:`_SLOT_FIELDS`) is fillable; an
    answer to an unknown slot is ignored. The slot vocabulary lives HERE so the
    chat layer never has to know the enum shapes."""
    slot = (question.slot or "").strip()
    raw = (answer or "").strip()
    if not slot or not raw:
        return slots

    # If the answer is a chip id, prefer the chip's human label for matching
    # (ids are already canonical for view/risk/horizon, but labels carry richer
    # free-text cues for the synonym fallback).
    label = raw
    for opt in question.options:
        if opt.id.strip().lower() == raw.lower():
            label = opt.label
            raw = opt.id  # canonical id wins for direct enum match
            break

    if slot == "view":
        direction = (
            _resolve_enum(raw, _VIEW_DIR_VALUES, _VIEW_SYNONYMS)
            or _resolve_enum(label, _VIEW_DIR_VALUES, _VIEW_SYNONYMS)
        )
        if direction is not None:
            slots.view.direction = direction  # type: ignore[assignment]
            slots.mark_assumed("view", value=False)
    elif slot == "risk":
        risk = (
            _resolve_enum(raw, _RISK_VALUES, _RISK_SYNONYMS)
            or _resolve_enum(label, _RISK_VALUES, _RISK_SYNONYMS)
        )
        if risk is not None:
            slots.risk = risk  # type: ignore[assignment]
            slots.mark_assumed("risk", value=False)
    elif slot == "horizon":
        horizon = (
            _resolve_enum(raw, _HORIZON_VALUES, _HORIZON_SYNONYMS)
            or _resolve_enum(label, _HORIZON_VALUES, _HORIZON_SYNONYMS)
        )
        if horizon is not None:
            slots.horizon = horizon  # type: ignore[assignment]
            slots.mark_assumed("horizon", value=False)
    elif slot == "capital_inr":
        cap = _detect_capital(label) or _detect_capital(raw)
        if cap is not None:
            slots.capital_inr = float(cap)
            slots.mark_assumed("capital_inr", value=False)
    elif slot == "theme":
        # Free-form tilt — take the label/text verbatim (the builder resolves it
        # against the thematic map). Reject pure catch-all answers.
        theme = label.strip()
        if theme and theme.lower() not in {"something else", "other", "skip"}:
            slots.theme = theme
            slots.mark_assumed("theme", value=False)
    elif slot == "asset_prefs":
        # Map common asset-class words into allow/deny; additive (we never wipe
        # the default allow-list, only extend deny/exclusions on an opt-out).
        low = label.lower()
        wants_gold = "gold" in low or "sgb" in low
        denies_gold = "no gold" in low or "without gold" in low
        if wants_gold and not denies_gold:
            if "gold" not in slots.asset_prefs.allow:
                slots.asset_prefs.allow.append("gold")  # type: ignore[arg-type]
            # A direct "yes" to gold must build the sleeve — the builder's
            # risk/horizon heuristic is for when the user DIDN'T say either
            # way, not a second vote that can override an explicit answer.
            slots.asset_prefs.gold_requested = True
            slots.mark_assumed("asset_prefs", value=False)
        if denies_gold:
            slots.asset_prefs.deny.append("gold")  # type: ignore[arg-type]
            slots.mark_assumed("asset_prefs", value=False)
        if "etf" in low or "mutual fund" in low or "index fund" in low:
            if "etf_mf" not in slots.asset_prefs.allow:
                slots.asset_prefs.allow.append("etf_mf")  # type: ignore[arg-type]
            slots.mark_assumed("asset_prefs", value=False)

    return slots


# ── Multi-slot free-text folding (resume path) ──────────────────────────────
# Unambiguous directional/risk words only (NO "long"/"short"/"up"/"down"/
# "growth"/"high"/"low" — those collide with horizon/capital and would
# mis-fire when a user answers several slots in one free-text line).
_VIEW_WORD_RE = re.compile(
    r"\b(bullish|bull|bearish|bear|rally|rallies|rallying|uptrend|downtrend|"
    r"crash|crashing|neutral|sideways|range[- ]?bound|choppy)\b", re.IGNORECASE)
_RISK_WORD_RE = re.compile(
    r"\b(aggressive|conservative|balanced|defensive|cautious|moderate|risky|"
    r"moonshot|safe|low[- ]risk|high[- ]risk|capital preservation|preserve)\b",
    re.IGNORECASE)
# Horizon by explicit duration. Years ≥5 → long; 1-4y (or "1-5") → medium;
# weeks/months → tactical.
_YEARS_RE = re.compile(r"(\d+)\s*(?:\+|plus)?\s*(?:\+\s*)?(?:y\b|yrs?\b|years?\b)",
                       re.IGNORECASE)
_HORIZON_PHRASE_RE = re.compile(
    r"\b(long[- ]?term|short[- ]?term|medium[- ]?term|tactical|swing|intraday|"
    r"decade|retire\w*|forever|few years)\b", re.IGNORECASE)
_SUB_YEAR_RE = re.compile(r"\b(\d+)?\s*(week|month|quarter)s?\b", re.IGNORECASE)


def _view_from_word(w: str) -> Optional[str]:
    low = w.lower().replace("-", " ").replace("bearish", "bear").replace(
        "bullish", "bull")
    if "bull" in low or "rally" in low or "rallies" in low or "uptrend" in low:
        return "bull"
    if "bear" in low or "crash" in low or "downtrend" in low:
        return "bear"
    return "neutral"


def _risk_from_word(w: str) -> Optional[str]:
    low = w.lower()
    if any(k in low for k in ("aggressive", "risky", "moonshot", "high")):
        return "aggressive"
    if any(k in low for k in ("conservative", "defensive", "cautious", "safe",
                              "low", "preserv")):
        return "conservative"
    if any(k in low for k in ("balanced", "moderate")):
        return "balanced"
    return None


def _horizon_from_text(t: str) -> Optional[str]:
    ph = _HORIZON_PHRASE_RE.search(t)
    yrs = [int(m.group(1)) for m in _YEARS_RE.finditer(t)]
    if ph:
        p = ph.group(1).lower().replace("-", " ").replace(" ", "")
        if p in ("longterm", "decade", "forever") or p.startswith("retire"):
            return "long"
        if p in ("shortterm", "tactical", "swing", "intraday"):
            return "tactical"
        if p in ("mediumterm", "fewyears"):
            return "medium"
    if yrs:
        return "long" if max(yrs) >= 5 else "medium"
    if _SUB_YEAR_RE.search(t) and not yrs:
        return "tactical"
    return None


def fold_free_text_into_slots(text: str, slots: SlotState) -> SlotState:
    """Fold a FREE-TEXT clarify answer across EVERY slot it mentions — capital,
    horizon, risk, view — not just the currently-asked question. A user often
    answers several slots in one line ("Around 3 lakh, 5 plus years, equities
    only."); without this the resume path re-asks a slot the user already
    answered (the observed bug). Only fills slots still flagged ``assumed`` /
    unset — never overrides a real prior answer. Mutates and returns ``slots``."""
    t = (text or "").strip()
    if not t:
        return slots
    assumed = slots.assumed

    # capital — reuse the project ₹ parser ("3 lakh" → 300000).
    if slots.capital_inr is None or getattr(assumed, "capital_inr", True):
        cap = _detect_capital(t)
        if cap is not None:
            slots.capital_inr = float(cap)
            slots.mark_assumed("capital_inr", value=False)

    # horizon
    if getattr(assumed, "horizon", True):
        h = _horizon_from_text(t)
        if h is not None:
            slots.horizon = h  # type: ignore[assignment]
            slots.mark_assumed("horizon", value=False)

    # risk
    if getattr(assumed, "risk", True):
        m = _RISK_WORD_RE.search(t)
        r = _risk_from_word(m.group(1)) if m else None
        if r is not None:
            slots.risk = r  # type: ignore[assignment]
            slots.mark_assumed("risk", value=False)

    # view
    if getattr(assumed, "view", True):
        m = _VIEW_WORD_RE.search(t)
        v = _view_from_word(m.group(1)) if m else None
        if v is not None:
            slots.view.direction = v  # type: ignore[assignment]
            slots.mark_assumed("view", value=False)

    # asset prefs — additive opt-in/opt-out from words in the same line.
    low = t.lower()
    if any(k in low for k in ("equity only", "equities only", "only stocks",
                              "just stocks", "stocks only", "no gold",
                              "no derivatives", "no options", "gold", "sgb",
                              "etf", "mutual fund", "index fund")):
        if ("gold" in low or "sgb" in low) and "no gold" not in low:
            if "gold" not in slots.asset_prefs.allow:
                slots.asset_prefs.allow.append("gold")  # type: ignore[arg-type]
            slots.asset_prefs.gold_requested = True
        if "no gold" in low:
            slots.asset_prefs.deny.append("gold")  # type: ignore[arg-type]
        if "etf" in low or "mutual fund" in low or "index fund" in low:
            if "etf_mf" not in slots.asset_prefs.allow:
                slots.asset_prefs.allow.append("etf_mf")  # type: ignore[arg-type]
        slots.mark_assumed("asset_prefs", value=False)

    return slots


__all__ = [
    "classify_slots",
    "should_ask",
    "generate_clarify_card",
    "normalize_answer_into_slots",
    "fold_free_text_into_slots",
    "SlotClass",
]


def _self_check() -> None:
    """Cheap unit-style self-check on the skip-entirely gate (runs only when
    this module is executed as a script: ``python -m backend.services.
    clarify_engine``). Asserts the two failure modes the gate must avoid:

      * UNDER-asking — a genuinely vague strategy ask builds silently. These
        MUST return ``should_ask == True`` (surface the clarify card).
      * OVER-asking — an already-specified ask gets held back for questions.
        These MUST return ``should_ask == False`` (build directly).
    """
    from backend.services.strategy_contracts import SlotState

    must_ask = [
        "build me a strategy",
        "build a strategy",
        "build a strategy for reliance",
        "design a portfolio",
        "make me a portfolio",
        "a basket of undervalued banks",
        "invest 2 lakh for me",
    ]
    must_not_ask = [
        "conservative long-term portfolio of 2L in quality stocks with some gold",
        "build a balanced long-term basket of 3 lakh, mostly stocks, dividend tilt",
        "bullish 1 lakh aggressive momentum basket for 1 year",
        "conservative 5 lakh portfolio for retirement",
        "aggressive 2 lakh 5-year quality compounder portfolio",
    ]
    for req in must_ask:
        assert should_ask(req, SlotState(), None) is True, (
            f"UNDER-ASK regression: vague {req!r} should ask but didn't"
        )
    for req in must_not_ask:
        assert should_ask(req, SlotState(), None) is False, (
            f"OVER-ASK regression: specific {req!r} should build but asked"
        )
    print("clarify_engine self-check OK "
          f"({len(must_ask)} ask + {len(must_not_ask)} build)")


if __name__ == "__main__":  # pragma: no cover - manual self-check entrypoint
    _self_check()
