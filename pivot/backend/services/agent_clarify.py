"""Structured clarifying card for UNDER-SPECIFIED automation / agent builds.

The companion to ``clarify_engine`` (which clarifies *portfolio / basket*
builds). This module clarifies *agent / workflow* builds — "make me an agent
that buys options in RELIANCE", "build an agent for TCS", "agent that profits
from rising oil but is risk neutral" — where the user named an intent but left
the **instrument / structure / sizing** under-specified.

Before this, that case produced a free-text ``ASK_USER`` question
(``system.md`` §"Build an agent for X with no other context"). Now it emits the
SAME ``clarify_card`` widget the portfolio builder uses — paginated one-click
chips — so the user taps an answer instead of typing prose.

Design (INTENT-AWARE generator + deterministic fallback):
  * A short, fast LLM call frames 1–3 questions to the ACTUAL request — it
    reasons about the theme (single name vs basket vs theme like 'oil'), the
    directional view (bullish / bearish / neutral), any risk constraint
    ('risk neutral' / 'hedge'), and emits questions that fit that shape (e.g.
    "which oil exposure — upstream producers basket / options structure /
    single name?"), instead of "what should the stock agent do?".
  * On ANY LLM error / timeout / shape mismatch we fall back to the original
    deterministic templates (action-kind / size). The deterministic path stays
    cheap and always-on so latency / provider outages never break the flow.
  * Questions reuse the ``ClarifyQuestion`` / ``ClarifyOption`` wire shape
    verbatim (``strategy_contracts``) so the existing FE ``ClarifyCard`` and the
    in-band resume machinery render / round-trip them unchanged.
  * Answers fold into a plain-dict agent slot-state and, when the flow stops,
    are assembled into an enriched natural-language intent that
    ``propose_workflow`` builds the draft from (``register-not-execute`` and the
    not-advice disclaimer are enforced downstream by the builder exactly as for
    any other workflow draft). The intent string carries an **instrument-
    correctness hint** (e.g. producers vs refiners for rising crude) and a
    **risk-constraint note** ("the user asked for risk-neutral — a plain long
    is NOT risk-neutral; propose a hedged / defined-risk structure or disclose
    the limitation"), so the downstream planner does not silently ship a naive
    long-only SIP under a "hedge" / "risk neutral" framing.

The chat layer (``chat_service._try_resume_clarify``) branches on the
``ClarifyState.kind == "agent"`` discriminator: it uses
:func:`normalize_agent_answer_into_slots` to fold answers and
:func:`build_agent_intent` + ``propose_workflow`` to build, instead of the
portfolio ``normalize_answer_into_slots`` + ``build_strategy``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The agent-build slot vocabulary. Closed set — these are the ONLY things a
# generated question may target (mirrors clarify_engine._SLOT_FIELDS discipline).
AGENT_SLOT_ACTION = "action"
AGENT_SLOT_SIZE = "size"
# Intent-aware slots the LLM generator may emit on top of action/size. They are
# free-form English strings that ``build_agent_intent`` folds verbatim into the
# enriched intent (the planner already parses natural language).
AGENT_SLOT_EXPOSURE = "exposure"
AGENT_SLOT_STRUCTURE = "structure"
AGENT_SLOT_CAPITAL = "capital"
_INTENT_AWARE_SLOTS = {
    AGENT_SLOT_ACTION,
    AGENT_SLOT_SIZE,
    AGENT_SLOT_EXPOSURE,
    AGENT_SLOT_STRUCTURE,
    AGENT_SLOT_CAPITAL,
}

# ── Symbol / asset detection (light, deterministic) ─────────────────────────

_TICKER_RE = re.compile(r"\b[A-Z][A-Z&]{1,11}\b")
_TICKER_STOP = {
    "AND", "OR", "THE", "FOR", "WITH", "BUY", "SELL", "SIP", "ETF", "MF",
    "PE", "ROE", "PB", "RSI", "SMA", "EMA", "IV", "OI", "PCR", "NSE", "BSE",
    "INR", "RS", "I", "A", "TO", "IN", "ON", "AT", "IF", "F", "O", "ME", "AN",
    "AGENT", "BOT", "ALERT", "WHEN", "EVERY", "MAKE", "BUILD", "CREATE",
}
_OPTIONS_CUES = (
    "option", "call", "put", "spread", "straddle", "strangle",
    "iron condor", "f&o", "fno", "strike", "expiry",
)


_LOWER_FILLER = {
    "it", "that", "this", "them", "the", "stock", "stocks", "share", "shares",
    "market", "my", "me", "something", "anything", "everything", "options",
    "option", "call", "put", "schedule", "condition", "alert", "sip", "agent",
}


def extract_symbol(request: str) -> Optional[str]:
    """Best-effort single NSE ticker the request names (e.g. RELIANCE, TCS).

    Two passes: (1) the first uppercase ticker-shaped token, (2) a fallback for
    lowercase prompts — a word after in/for/of/on/into, upper-cased as a
    candidate. The model usually passes ``symbol`` explicitly to the tool, so
    this is a backstop; a miss just frames the chips generically ("the stock"),
    never wrong.
    """
    if not request:
        return None
    for tok in _TICKER_RE.findall(request):
        if tok in _TICKER_STOP or len(tok) < 2:
            continue
        return tok
    m = re.search(
        r"\b(?:in|for|of|on|into)\s+([A-Za-z][A-Za-z&]{2,15})\b",
        request, re.IGNORECASE,
    )
    if m:
        cand = m.group(1).upper()
        if cand not in _TICKER_STOP and cand.lower() not in _LOWER_FILLER:
            return cand
    return None


def detect_asset(request: str) -> str:
    """``"options"`` when the request mentions an option/F&O instrument, else
    ``"equity"``. Drives whether the size chips read in lots or shares."""
    text = (request or "").lower()
    return "options" if any(cue in text for cue in _OPTIONS_CUES) else "equity"


# ── Theme / view / risk detection ───────────────────────────────────────────
#
# Light deterministic priors the LLM prompt is grounded against AND that the
# downstream intent enricher uses to inject instrument-correctness hints. These
# are intentionally conservative — a miss just means we don't inject a hint, we
# never inject a WRONG hint.

# theme key → (regex of cues, NL human label, instrument-correctness hint for
# the downstream planner). The hint phrasing is deliberately blunt because
# `propose_workflow` plans from English: it has to read "do NOT pick refiners"
# and act on it.
_THEME_HINTS: dict[str, tuple[re.Pattern[str], str, str]] = {
    "oil_up": (
        re.compile(
            r"\b(rising|increas\w*|higher|surge|rally|bull(?:ish)?)\b[^.]{0,40}"
            r"\b(oil|crude|brent|wti|petroleum)\b"
            r"|\b(oil|crude|brent|wti)\b[^.]{0,20}\b(rising|increas\w*|up|higher|surge|rally)\b",
            re.IGNORECASE,
        ),
        "rising crude oil",
        (
            "Instrument correctness — beneficiaries of RISING crude are "
            "UPSTREAM producers / exploration & production names "
            "(e.g. ONGC, OIL India); DO NOT pick downstream refiners / "
            "marketers like IOC, BPCL, HPCL whose marketing margins COMPRESS "
            "when crude rises. An oil-services / E&P basket or a long-crude "
            "MCX crude future (tradeable, register-not-execute) are the nearest real things."
        ),
    ),
    "oil_down": (
        re.compile(
            r"\b(falling|drop\w*|lower|crash|bear(?:ish)?|decline)\b[^.]{0,40}"
            r"\b(oil|crude|brent|wti|petroleum)\b",
            re.IGNORECASE,
        ),
        "falling crude oil",
        (
            "Instrument correctness — beneficiaries of FALLING crude are "
            "downstream refiners / marketers (IOC, BPCL, HPCL) and aviation "
            "(InterGlobe / SpiceJet), NOT upstream producers (ONGC / OIL "
            "India) whose realisations fall with crude."
        ),
    ),
    "rates_down": (
        re.compile(
            r"\b(rate\s*cut|rbi\s*cut|lower\s*rates|falling\s*rates|"
            r"dovish)\b",
            re.IGNORECASE,
        ),
        "RBI rate cuts / falling rates",
        (
            "Instrument correctness — rate-cut beneficiaries are rate-sensitive "
            "names: NBFCs (BAJFINANCE, CHOLAFIN), banks with low CASA, "
            "real-estate (DLF, GODREJPROP), autos (M&M, MARUTI). Avoid "
            "insurers whose float yields compress with rates."
        ),
    ),
    "rates_up": (
        re.compile(
            r"\b(rate\s*hike|rbi\s*hike|higher\s*rates|rising\s*rates|"
            r"hawkish)\b",
            re.IGNORECASE,
        ),
        "rising rates",
        (
            "Instrument correctness — rising-rate beneficiaries are large "
            "private banks with high CASA (HDFCBANK, ICICIBANK, KOTAKBANK) "
            "and life insurers (HDFCLIFE, SBILIFE) whose float reinvests at "
            "higher yields."
        ),
    ),
}

_RISK_NEUTRAL_RE = re.compile(
    r"\b(risk[\s-]*neutral|market[\s-]*neutral|delta[\s-]*neutral|"
    r"hedge[d]?|hedging|defined[\s-]*risk|capped[\s-]*risk)\b",
    re.IGNORECASE,
)
_BULLISH_RE = re.compile(
    r"\b(profit\w*\s+from\s+(?:rising|increas\w*|higher)|long|bull(?:ish)?|"
    r"upside|going\s+up)\b",
    re.IGNORECASE,
)
_BEARISH_RE = re.compile(
    r"\b(profit\w*\s+from\s+(?:falling|drop\w*|lower)|short|bear(?:ish)?|"
    r"downside|going\s+down)\b",
    re.IGNORECASE,
)


def _detect_theme(request: str) -> Optional[tuple[str, str, str]]:
    """Return ``(theme_key, human_label, instrument_hint)`` when a known theme
    fires, else ``None``. Cheap regex grounding the LLM prompt + the intent
    enricher; on miss we simply skip injection (never inject wrong)."""
    if not request:
        return None
    for key, (pat, label, hint) in _THEME_HINTS.items():
        if pat.search(request):
            return key, label, hint
    return None


def _detect_view(request: str) -> str:
    """``"bullish"`` / ``"bearish"`` / ``"unspecified"`` directional view."""
    if not request:
        return "unspecified"
    if _BULLISH_RE.search(request):
        return "bullish"
    if _BEARISH_RE.search(request):
        return "bearish"
    return "unspecified"


def _is_risk_neutral_ask(request: str) -> bool:
    return bool(request) and bool(_RISK_NEUTRAL_RE.search(request))


# ── Under-spec gate ─────────────────────────────────────────────────────────

# An action VERB is present (buy/sell/sip/alert) but ...
_ACTION_VERB_RE = re.compile(
    r"\b(buy|buys|buying|sell|sells|selling|sip|invest|alert|notify|short|"
    r"profit\w*|hedge\w*)\b",
    re.IGNORECASE,
)
# ... a TRIGGER (when/every/if/cross/below/above/schedule/RSI<n) would make it
# buildable, as would a concrete SIZE (n shares/lots/₹n). When NEITHER is
# present the build kind is genuinely open → ask.
_TRIGGER_RE = re.compile(
    r"\b(when|every|if|cross(?:es)?|below|above|drops?|rises?|"
    r"daily|weekly|monthly|schedule|at\s+open|at\s+close|"
    r"rsi|sma|ema|macd|breaks?|hits?|reaches?)\b",
    re.IGNORECASE,
)
_SIZE_RE = re.compile(
    r"\b\d+\s*(?:share|shares|lot|lots|qty|quantity|units?)\b"
    r"|\b(?:buy|buys|sell|sells|short)\s+\d+\b"  # "buy 50 TCS" — bare qty
    r"|₹\s*[\d,]+|\b(?:rs\.?|inr)\s*[\d,]+\b|\bworth\b",
    re.IGNORECASE,
)
# "build an agent for X" / "make me a bot" with no action verb at all — still
# an agent build that must be clarified rather than fabricated.
_AGENT_BUILD_RE = re.compile(
    r"\b(build|make|create|set\s*up|setup|design|want|need)\b"
    r"[^.]{0,30}\b(agent|automation|bot|rule|workflow)\b",
    re.IGNORECASE,
)


def should_ask_agent(request: str) -> bool:
    """True when an agent ask leaves BOTH the trigger and the size unstated —
    the build kind is open and a default would be a guess.

    Fires when the request either names an action verb (buy/sell/SIP/alert) OR
    is an explicit "build an agent" phrasing, AND carries neither a trigger nor
    a size. Returns False (let the model build / ask directly) the moment a
    trigger or size anchors the draft. Deterministic and cheap."""
    if not request:
        return False
    if not (_ACTION_VERB_RE.search(request) or _AGENT_BUILD_RE.search(request)):
        return False
    if _TRIGGER_RE.search(request) or _SIZE_RE.search(request):
        return False
    return True


# ── Deterministic question generation (hard fallback) ───────────────────────


def _action_question(symbol: Optional[str], asset: str) -> dict[str, Any]:
    who = symbol or "stock"
    instr = f"{who} options" if asset == "options" else who
    opts = [
        {"id": "schedule",
         "label": f"Buy {instr} on a schedule (e.g. every Friday)"},
        {"id": "condition",
         "label": f"Buy {instr} when a price / indicator condition hits"},
        {"id": "sip",
         "label": f"Run a recurring SIP into {instr}"},
        {"id": "alert",
         "label": f"Just alert me about {who} — place no order"},
    ]
    # Phrase the prompt without doubling "the" or narrowing thematic asks to a
    # single stock when no symbol was named — "What should the agent do?" reads
    # cleanly whether the instrument is a name, a basket, or options.
    if symbol:
        prompt = f"What should the {instr} agent do?"
    else:
        prompt = "What should the agent do?"
    return {
        "id": "q_action",
        "slot": AGENT_SLOT_ACTION,
        "prompt": prompt,
        "voi": 1.0,
        "options": opts,
        "free_text": True,
        "skippable": False,
    }


def _size_question(symbol: Optional[str], asset: str) -> dict[str, Any]:
    if asset == "options":
        opts = [
            {"id": "lot_1", "label": "1 lot"},
            {"id": "lot_2", "label": "2 lots"},
            {"id": "lot_5", "label": "5 lots"},
        ]
        prompt = f"How many lots of {symbol} per trade?" if symbol \
            else "How many lots per trade?"
    else:
        opts = [
            {"id": "qty_10", "label": "10 shares"},
            {"id": "qty_25", "label": "25 shares"},
            {"id": "inr_10000", "label": "₹10,000 worth"},
        ]
        prompt = f"How much {symbol} per trade?" if symbol \
            else "How much per trade?"
    return {
        "id": "q_size",
        "slot": AGENT_SLOT_SIZE,
        "prompt": prompt,
        "voi": 0.7,
        "options": opts,
        "free_text": True,
        # Skippable: an alert-only agent or an undecided user can move on; the
        # draft then carries a sensible default size the user edits on the card.
        "skippable": True,
    }


def _deterministic_questions(
    symbol: Optional[str], asset: str,
) -> list[dict[str, Any]]:
    """The original always-on deterministic question pair — the LLM fallback."""
    return [_action_question(symbol, asset), _size_question(symbol, asset)]


# ── Intent-aware (LLM) question generation ──────────────────────────────────

# Keep the LLM short. We want quick, grounded questions, not a planner.
_GENERATOR_TIMEOUT_S = 6.0
_GENERATOR_MAX_TOKENS = 500
_GENERATOR_MIN_QUESTIONS = 1
_GENERATOR_MAX_QUESTIONS = 3
_GENERATOR_MAX_OPTIONS = 4

_GENERATOR_SYSTEM_PROMPT = """You frame 1-3 short clarifying questions for an
investment-agent build request from an Indian retail user. Pivot REGISTERS
orders (does not auto-execute) and supports NSE/BSE equity, NSE options (NFO),
and indices.

Your job: read the user's request and emit questions that fit the ACTUAL
intent. Do NOT default every ask to "what should the stock agent do?". In
particular:

1. If the request names a THEME (e.g. "profits from rising oil"), do NOT
   collapse it to a single stock. Ask about EXPOSURE — e.g. an upstream
   producers basket vs an options structure vs a single name — using
   instrument-correct examples for that theme. For "rising oil" the
   beneficiaries are UPSTREAM producers (ONGC, OIL India), NOT refiners
   (IOC, BPCL, HPCL).
2. If the request says "risk neutral" / "market neutral" / "hedged" /
   "defined risk", ask about STRUCTURE — a hedged / spread / defined-risk
   structure vs a directional long. Surface that a plain long is NOT
   risk-neutral as one of the option labels.
3. If sizing / capital is unstated, ask ONE concrete capital question with
   ₹-denominated chips (e.g. ₹25k / ₹1L / ₹5L) — not "how much it per trade".
4. Never invent specific price levels, strikes, or expiries — those are
   defaults the planner fills and the user edits on the card.

Output STRICT JSON with this shape and nothing else:

{
  "questions": [
    {
      "slot": "exposure" | "structure" | "capital" | "action" | "size",
      "prompt": "string, <= 90 chars, no double 'the', no 'the stock' when
                 the ask is thematic",
      "options": [
        {"id": "short_snake_case_id", "label": "concrete grounded NL label"},
        ...  // 2-4 options
      ],
      "free_text": true,
      "skippable": true | false
    }
    // 1-3 questions, most decision-relevant first
  ]
}

Rules:
- 1 to 3 questions total. Each with 2-4 options.
- Slot values MUST be one of: exposure, structure, capital, action, size.
- Option labels are concrete English (e.g. "Upstream producers basket
  (ONGC, OIL India)" not "the upstream basket").
- The FIRST question is NOT skippable; later ones may be.
- Do NOT include any prose outside the JSON.
"""


def _build_generator_user_payload(
    request: str,
    symbol: Optional[str],
    asset: str,
    theme: Optional[tuple[str, str, str]],
    view: str,
    risk_neutral: bool,
) -> str:
    payload: dict[str, Any] = {
        "request": request,
        "detected": {
            "symbol": symbol,
            "asset": asset,
            "theme": theme[1] if theme else None,
            "instrument_hint": theme[2] if theme else None,
            "view": view,
            "risk_neutral_ask": risk_neutral,
        },
        "constraints": {
            "min_questions": _GENERATOR_MIN_QUESTIONS,
            "max_questions": _GENERATOR_MAX_QUESTIONS,
            "max_options_per_question": _GENERATOR_MAX_OPTIONS,
            "register_not_execute": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False)


async def _llm_generate_questions(
    request: str,
    symbol: Optional[str],
    asset: str,
    theme: Optional[tuple[str, str, str]],
    view: str,
    risk_neutral: bool,
) -> Optional[list[dict[str, Any]]]:
    """One short LLM call that returns 1-3 intent-aware questions, or ``None``
    on any error / shape mismatch. Never raises — the deterministic fallback is
    the cost ceiling for this hop."""
    from backend.llm import LLMMessage, get_llm_client

    client = get_llm_client()
    messages = [
        LLMMessage(role="system", content=_GENERATOR_SYSTEM_PROMPT),
        LLMMessage(
            role="user",
            content=_build_generator_user_payload(
                request, symbol, asset, theme, view, risk_neutral,
            ),
        ),
    ]
    try:
        resp = await asyncio.wait_for(
            client.complete(
                messages=messages,
                tools=None,
                tool_choice="none",
                max_output_tokens=_GENERATOR_MAX_TOKENS,
                temperature=0.3,
                reasoning_effort="minimal",
                response_format="json_object",
                prompt_cache_key="agent_clarify_generate_v1",
            ),
            timeout=_GENERATOR_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        logger.info("agent_clarify LLM generate failed: %s", exc)
        return None

    if resp.finish_reason == "error":
        logger.info("agent_clarify LLM generate returned error finish_reason")
        return None

    raw = (resp.content or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.info("agent_clarify LLM generate returned non-JSON; discarding")
        return None
    questions = parsed.get("questions") if isinstance(parsed, dict) else None
    if not isinstance(questions, list) or not questions:
        return None
    return [q for q in questions if isinstance(q, dict)]


def _coerce_llm_questions(
    raw_questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate + coerce LLM-emitted questions to the wire shape the FE
    ``ClarifyCard`` and the resume path expect. Drops any malformed entry.
    Returns ``[]`` if nothing survives — caller falls back to deterministic."""
    out: list[dict[str, Any]] = []
    for idx, q in enumerate(raw_questions[:_GENERATOR_MAX_QUESTIONS]):
        slot = str(q.get("slot") or "").strip().lower()
        if slot not in _INTENT_AWARE_SLOTS:
            continue
        prompt = str(q.get("prompt") or "").strip()
        if not prompt or "the the " in prompt.lower():
            # Reject the exact regression we're fixing here.
            continue
        raw_opts = q.get("options")
        if not isinstance(raw_opts, list):
            continue
        opts: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for j, opt in enumerate(raw_opts[:_GENERATOR_MAX_OPTIONS]):
            if not isinstance(opt, dict):
                continue
            label = str(opt.get("label") or "").strip()
            if not label:
                continue
            oid = str(opt.get("id") or "").strip().lower()
            if not oid:
                oid = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
            if not oid or oid in seen_ids:
                oid = f"{oid or 'opt'}_{j}"
            seen_ids.add(oid)
            opts.append({"id": oid, "label": label})
        if len(opts) < 2:
            continue
        out.append({
            "id": f"q_{slot}_{idx}",
            "slot": slot,
            "prompt": prompt,
            "voi": 1.0 - 0.1 * idx,
            "options": opts,
            "free_text": bool(q.get("free_text", True)),
            # First question is not skippable; later ones may be.
            "skippable": bool(q.get("skippable", idx > 0)),
        })
    # The first question is never skippable — guarantee it regardless of what
    # the model emitted.
    if out:
        out[0]["skippable"] = False
    return out


async def generate_agent_clarify_card(request: str) -> Optional[dict[str, Any]]:
    """Build the ``clarify_card`` payload dict for an under-specified agent ask,
    or ``None`` to let the model build directly.

    Calls a fast LLM to frame intent-aware questions (theme / structure /
    capital) and falls back to the deterministic action+size template on any
    error or shape mismatch. Returns the SAME shape the FE ClarifyCard renders:
    ``{session_slot_state, total, index, questions:[...]}`` where each question
    matches :class:`ClarifyQuestion`. ``session_slot_state`` carries the agent
    slot-state (symbol + asset + theme/view/risk priors + empty answers) which
    the resume path round-trips via Redis ``ClarifyState`` — the FE ignores it.
    """
    if not should_ask_agent(request):
        return None
    symbol = extract_symbol(request)
    asset = detect_asset(request)
    theme = _detect_theme(request)
    view = _detect_view(request)
    risk_neutral = _is_risk_neutral_ask(request)

    questions: list[dict[str, Any]] = []
    # Intent-aware LLM pass, defended by a hard timeout + try/except. On any
    # failure we degrade silently to the deterministic templates so a provider
    # outage never breaks the clarify turn.
    try:
        # Awaited directly on the caller's event loop — the LLM client is
        # async-native and ``_llm_generate_questions`` already enforces a hard
        # timeout + swallows errors to None, so this never blocks the loop on a
        # worker-thread join and never breaks the clarify turn.
        raw = await _llm_generate_questions(
            request, symbol, asset, theme, view, risk_neutral,
        )
    except Exception as exc:  # noqa: BLE001 — defence in depth; fall back deterministic
        logger.info("agent_clarify LLM generate failed: %s", exc)
        raw = None
    if isinstance(raw, list):
        questions = _coerce_llm_questions(raw)

    used_llm = bool(questions)
    if not questions:
        questions = _deterministic_questions(symbol, asset)

    slots: dict[str, Any] = {
        "symbol": symbol,
        "asset": asset,
        "theme_key": theme[0] if theme else None,
        "theme_label": theme[1] if theme else None,
        "instrument_hint": theme[2] if theme else None,
        "view": view,
        "risk_neutral": risk_neutral,
        "intent_aware": used_llm,
        "answers": {},
    }
    return {
        "session_slot_state": slots,
        "total": len(questions),
        "index": 0,
        "questions": questions,
    }


# ── Answer ingestion ────────────────────────────────────────────────────────


def normalize_agent_answer_into_slots(
    question: dict[str, Any], answer: str, slots: dict[str, Any],
) -> dict[str, Any]:
    """Fold ONE agent-clarify answer into the agent slot-state dict.

    ``answer`` is a chip ``id``, a chip ``label``, or free text. We resolve the
    chip id where possible (prefer it) and store BOTH the canonical value and
    the human label under ``slots["answers"][slot]`` so
    :func:`build_agent_intent` can phrase the enriched build intent. Mutates and
    returns ``slots``."""
    slot = str(question.get("slot") or "").strip()
    raw = (answer or "").strip()
    if not slot or not raw:
        return slots
    label = raw
    value = raw
    for opt in question.get("options") or []:
        oid = str(opt.get("id") or "")
        olabel = str(opt.get("label") or "")
        if oid and oid.lower() == raw.lower():
            value = oid
            label = olabel or oid
            break
        if olabel and olabel.lower() == raw.lower():
            value = oid or olabel
            label = olabel
            break
    answers = slots.setdefault("answers", {})
    answers[slot] = {"value": value, "label": label}
    return slots


# Map an action chip id → a CONCRETE, buildable enrichment phrase. These pin a
# sensible default trigger so the workflow planner always produces a valid draft
# (a vague "when a condition is met" fails validation — there's no level to
# build). The draft is register-not-execute and fully editable, so the user
# tunes the exact level/day on the card — exactly the system's silent-default
# pattern. We are NOT fabricating market data; we're proposing an editable
# starting point.
_ACTION_PHRASE = {
    "schedule": "Buy {sym} on a recurring schedule — every Friday at 9:30 IST.",
    "condition": "Buy {sym} when its 14-day RSI drops below 30 (oversold).",
    "sip": "Run a weekly SIP buying {sym} every Monday at 9:30 IST.",
    "alert": "Alert me (place no order) when {sym} moves more than 3% in a day.",
}


def build_agent_intent(request: str, slots: dict[str, Any]) -> str:
    """Assemble an enriched natural-language intent for ``propose_workflow`` from
    the original request + the answered agent slots.

    The builder (``propose_workflow_async``) plans + drafts from this string, so
    we phrase the answers as plain English the planner already understands —
    never fabricating a value the user didn't choose (a skipped slot just isn't
    mentioned, and the builder picks a sensible default the user edits).

    When the priors detected a theme (e.g. "rising oil") or a risk constraint
    ("risk neutral"), we INJECT an instrument-correctness hint and a risk
    constraint note into the intent string so the downstream planner does not
    silently ship a naive long-only structure under a hedge / theme framing.
    Those notes are the load-bearing fix for the "Build me an agent that
    profits from increasing oil prices but is risk neutral" failure mode."""
    symbol = (slots or {}).get("symbol")
    asset = (slots or {}).get("asset") or "equity"
    answers = (slots or {}).get("answers") or {}
    instrument_hint = (slots or {}).get("instrument_hint")
    theme_label = (slots or {}).get("theme_label")
    theme_key = (slots or {}).get("theme_key")
    risk_neutral = bool((slots or {}).get("risk_neutral"))
    view = (slots or {}).get("view") or "unspecified"

    sym = symbol or "the chosen instrument"
    if asset == "options":
        sym = f"{symbol} options" if symbol else "the chosen options structure"

    parts: list[str] = [(request or "").strip()]

    # Slot-driven enrichment. The deterministic action+size path emits
    # action/size; the intent-aware path emits exposure/structure/capital.
    # Both are folded as plain English.
    action = (answers.get(AGENT_SLOT_ACTION) or {}).get("value")
    if action in _ACTION_PHRASE:
        parts.append(_ACTION_PHRASE[action].format(sym=sym))

    exposure = (answers.get(AGENT_SLOT_EXPOSURE) or {}).get("label")
    if exposure:
        parts.append(f"Preferred exposure: {exposure}.")

    structure = (answers.get(AGENT_SLOT_STRUCTURE) or {}).get("label")
    if structure:
        parts.append(f"Preferred structure: {structure}.")

    capital = (answers.get(AGENT_SLOT_CAPITAL) or {}).get("label")
    if capital:
        parts.append(f"Capital budget per trade / position: {capital}.")

    size = answers.get(AGENT_SLOT_SIZE) or {}
    size_label = (size.get("label") or "").strip()
    if size_label and size_label.lower() not in {"skip", "something else"}:
        parts.append(f"Size each trade as {size_label}.")

    # Instrument-correctness hint — the producers-vs-refiners load-bearing
    # injection. Phrased bluntly because the planner reads English literally.
    if instrument_hint:
        prefix = f"Theme: {theme_label}. " if theme_label else ""
        parts.append(f"{prefix}{instrument_hint}")

    # Ground the hint in LIVE symbols from sector_universe (the single source of
    # truth for the producer-vs-refiner split) rather than a prose hint alone —
    # so the IOC-for-rising-oil failure is prevented by DATA, not just the
    # planner reading the prompt carefully. Best-effort: a miss just omits names.
    try:
        from backend.services import sector_universe as _su

        if theme_key in ("oil_up", "crude_up"):
            names = _su.crude_up_beneficiaries()
            if names:
                parts.append(
                    "Concrete beneficiaries of RISING crude — build the basket "
                    f"from these upstream producers, NOT refiners/OMCs: {', '.join(names)}."
                )
        elif theme_key in ("oil_down", "crude_down"):
            names = _su.crude_down_beneficiaries()
            if names:
                parts.append(
                    "Concrete beneficiaries of FALLING crude (refiners / heavy "
                    f"fuel consumers): {', '.join(names)}."
                )
    except Exception:  # noqa: BLE001 — never let the universe lookup break the build
        pass

    # Risk-constraint honesty. If the user asked for "risk neutral" / "hedged"
    # we make sure the downstream planner is told point-blank that a plain
    # long-only position does NOT satisfy that constraint and must either be
    # replaced with a hedged / defined-risk structure or shipped with an
    # explicit disclosure that the structure is directional.
    if risk_neutral:
        parts.append(
            "Risk constraint — the user explicitly asked for a RISK-NEUTRAL / "
            "hedged structure. A plain long-only equity position or a vanilla "
            "SIP is NOT risk-neutral. Propose a HEDGED or DEFINED-RISK "
            "structure (e.g. a long-leg + short-leg pair, a long call spread, "
            "a covered position, or a market-neutral pairs trade), OR if no "
            "hedged structure is feasible, clearly DISCLOSE in the agent "
            "rationale that the proposed structure is directional and not "
            "risk-neutral, and offer the nearest real hedged alternative."
        )
    elif view == "bullish" and theme_label:
        # Soft directional note when the user is bullish on a theme but did
        # NOT explicitly ask for hedging — keeps the planner from accidentally
        # proposing a bearish structure.
        parts.append(
            f"Directional view: bullish on {theme_label}; size and structure "
            "should reflect a long bias."
        )
    elif view == "bearish" and theme_label:
        parts.append(
            f"Directional view: bearish on {theme_label}; size and structure "
            "should reflect a short / put-side bias (register-not-execute)."
        )

    # Every agent draft is register-not-execute and must carry a genuinely
    # detailed rationale (not a one-liner). Tell the planner that explicitly
    # so it does not collapse the rationale to a single sentence.
    parts.append(
        "Output requirement: the agent draft MUST carry a DETAILED rationale "
        "(multi-sentence) covering instrument choice, why this structure "
        "matches the user's view + risk constraint, the trigger / sizing "
        "logic, and any honest caveats. Register-not-execute; this is "
        "analysis, not financial advice."
    )

    return " ".join(p for p in parts if p)


__all__ = [
    "AGENT_SLOT_ACTION",
    "AGENT_SLOT_SIZE",
    "AGENT_SLOT_EXPOSURE",
    "AGENT_SLOT_STRUCTURE",
    "AGENT_SLOT_CAPITAL",
    "extract_symbol",
    "detect_asset",
    "should_ask_agent",
    "generate_agent_clarify_card",
    "normalize_agent_answer_into_slots",
    "build_agent_intent",
]
