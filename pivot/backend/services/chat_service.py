"""ChatService — single-hop request handler with deterministic resume.

LLM-call audit (2026-05-04). Per turn the LLM is called AT MOST once on
this path. There used to be a multi-hop agentic loop here that re-fed
tool errors back to the model so it could self-correct ("validation
retry"); that loop was burning 2–6 seconds per turn for a problem the
schema already knew how to describe deterministically. Both fixes
shipped together:

    Change 1 — zero LLM retries on validation failure
    ─────────────────────────────────────────────────
    Tool returned `error` (not `needs_clarification`):
      OLD: append error to messages, loop back to LLM, hope it fixes
           the args. Up to MAX_TOOL_CALLS times.
      NEW: convert the error into a deterministic clarification
           question (`_format_recoverable_failure_question` /
           `_format_clarification_question`) and surface to the user.
           No second hop. The first call has to be right or it asks.

    Change 2 — deterministic resume after clarification
    ───────────────────────────────────────────────────
    User reply to a clarification (e.g. "1400" after we asked for the
    stop-loss price):
      OLD: another LLM hop with a "merge this into the JSON" system
           message; the model rebuilds the full tool call.
      NEW: ConversationStore persists `PendingToolCall(name, args,
           missing_field, field_type)` when the question is asked.
           On the next turn, if the reply parses cleanly as the
           missing field's value, splice and execute. ZERO LLM calls.
           Off-ramps for cancel / multi-clause / type mismatch fall
           back to the normal LLM path.

LLM call sites NOW:

    - `handle()`: 1× `client.complete(...)` per turn (single hop). Loop
      removed; on error → return question; on success → return result.
    - `handle_stream()`: same shape, streamed.
    - `propose.py`: 2× per `propose_workflow` tool call (planner +
      drafter split). Out of scope for this audit — that's a different
      LLM-call structure, not validation retries.
    - Fast path: 0 LLM calls. Greetings/help via regex.

What stays the same: the fast-path classifier, schema-driven
completeness check, structured cards, propose_workflow's macro
fallback (when the planner-drafter split itself fails), the
post-processor that strips placeholders and tool-call leakage.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Optional

from backend.llm import LLMClient, LLMMessage, ToolDef, get_llm_client
from backend.llm.base import ReasoningEffort
from backend.prompts import build_system_prompt
from backend.prompts.assembler import UserContext as PromptUserContext
from backend.services.chat_trace import TurnTrace, start_turn
from backend.services.conversation_store import (
    CONV_PROMPT_WINDOW_TURNS,
    ActiveDraft,
    ConversationStore,
    PendingToolCall,
    default_store,
)
from backend.services.fast_path import try_fast_path
from backend.services.tool_registry import get_tool_schema
from backend.services.workflow_skeleton import try_workflow_skeleton
from backend.services.tool_router import (
    cache_key_for,
    filter_registry_tools,
    select_tool_names,
)
from backend.services.validation_handler import (
    ASK_USER_TOOL_NAME,
    GuardedToolResult,
    ask_user_tool_def,
    execute_with_completeness,
)


logger = logging.getLogger(__name__)


_PLACEHOLDER_RE = re.compile(r"<[A-Z][A-Z0-9_]*>")
_TOOL_CALL_BLOCK_RE = re.compile(r"<TOOL_CALL>.*?(?:</TOOL_CALL>|$)", re.DOTALL | re.IGNORECASE)


# Internal-reasoning leak detector. WHY this exists: GPT-5 with
# reasoning_effort='minimal' or 'low' on long conversations sometimes
# writes its planning monologue into the visible output instead of the
# reasoning trace. Observed leakage: "This is a long and complex
# conversation. The user now says: '...'. We must answer succinctly.
# Earlier guidance: ... Let's craft final."
#
# A paragraph is treated as a leak if it contains TWO OR MORE of these
# meta-language signals in close proximity (within ~400 chars). One
# signal alone could be legitimate ("let me show you the chart"); two
# together is unmistakably internal monologue.
_REASONING_LEAK_TELLS = (
    r"\bthe\s+user\s+(?:now\s+)?(?:says|asks|wants|asked|is\s+asking|wants\s+to)\b",
    r"\bwe\s+(?:must|should|need\s+to|will|'ll)\b",
    r"\bi\s+(?:must|should|need\s+to|'ll|will)\s+(?:answer|reply|respond|provide|include|note|ensure|craft)\b",
    r"\blet'?s\s+(?:craft|finalise|finalize|provide|answer|respond)\b",
    r"\blet\s+me\s+(?:think|craft|provide|reason)\b",
    r"\bearlier\s+(?:guidance|said|noted|context)\b",
    r"\bmust\s+include\b",
    r"\bnot\s+sure\b.*\?",
    r"\bthis\s+is\s+a\s+long\s+and\s+complex\s+conversation\b",
    r"\bkeep\s+(?:concise|it\s+short)\b",
    r"\bneed\s+(?:safe|to\s+be\s+careful)\b",
    r"\bfinal(?:ly|\s+answer|\s+response|\s+output)\b",
    r"\bstep[- ]?by[- ]?step\s*:\s*$",
)
_REASONING_LEAK_RE = re.compile(
    "|".join(_REASONING_LEAK_TELLS),
    re.IGNORECASE,
)


def _strip_reasoning_leakage(text: str) -> str:
    """Remove paragraphs that look like the model's internal reasoning
    leaked into the user-facing output.

    Strategy: split on blank lines (paragraphs). For each paragraph,
    count meta-language tells. Drop the paragraph if the count is >=2.
    This preserves legitimate sentences that happen to mention "the
    user" or "we should" once, while reliably catching multi-sentence
    monologues.
    """
    if not text:
        return text
    paragraphs = re.split(r"\n\s*\n", text)
    kept: list[str] = []
    for para in paragraphs:
        if not para.strip():
            continue
        # Count distinct tells in the paragraph (cap at one per pattern
        # so a single repeated phrase doesn't trigger).
        seen = 0
        for pat in _REASONING_LEAK_TELLS:
            if re.search(pat, para, re.IGNORECASE):
                seen += 1
                if seen >= 2:
                    break
        if seen >= 2:
            continue
        kept.append(para)
    return "\n\n".join(kept).strip()
# F&O is WIRED as of P1 (chain / suggest / build / critique tools +
# the option_strategy_card). The pre-LLM decline that used to live here
# (_FO_STRATEGY_RE / _fo_strategy_decline) is gone — strategy verbs now
# route TO the options tools via tool_router + the _mentions_fno gate
# below. NOTE the old decline also shielded Azure's content filter from
# phrases like "naked put"; the options tools answer those turns with
# structured cards before free prose, and the regression tests in
# tests/test_chat_fno_routing.py pin each former decline phrase.


_GENERIC_FALLBACK = "Sorry, I had trouble with that — could you rephrase?"
_LLM_UNAVAILABLE = (
    "The AI backend is temporarily unavailable. You can still:\n"
    "• Run a backtest directly: `backtest pe_ratio < 15 from 2020-01-01 to 2024-12-31`\n"
    "• Screen the universe: `/screen roe > 18`\n"
    "• Type a stock ticker (e.g. `RELIANCE`) for a snapshot."
)
_LATENT_GREETING_RE = re.compile(
    r"execute\s+orders\s+on\s+zerodha\.\s+build\s+capital\s+protection",
    re.IGNORECASE,
)

# Defence-in-depth: the LLM sometimes mimics our internal fallback templates
# and produces user-facing text that names internal tools verbatim
# ("Done — `backtest_workflow` ran.", "I'll call get_live_price now"). The
# system prompt explicitly forbids this; this regex strips any whole sentence
# that contains a recognisable internal tool identifier.
_INTERNAL_TOOL_NAME = re.compile(
    # Matches the snake_case-shaped internal tool identifier pattern.
    # Restricted prefixes so we don't accidentally strip legitimate prose
    # that happens to contain underscores ("buy_back_program", etc).
    r"`?\b(?:[a-z]+_workflow"
    r"|propose_[a-z_]+|create_[a-z_]+|place_[a-z_]+"
    r"|squareoff_[a-z_]+|get_[a-z_]+|list_[a-z_]+|cancel_[a-z_]+)\b`?",
    re.IGNORECASE,
)


def _strip_internal_tool_leaks(text: str) -> str:
    """Drop any sentence that names an internal tool identifier.

    Sentence-level — preserves the rest of the reply if only one sentence
    leaks. Used by _post_process as defence-in-depth on top of the system-
    prompt rule that already forbids this phrasing.
    """
    if not text or not _INTERNAL_TOOL_NAME.search(text):
        return text
    # Split on sentence boundaries (. ? !) but keep paragraph breaks.
    out_parts: list[str] = []
    for para in re.split(r"(\n\s*\n)", text):
        if not para or para.isspace():
            out_parts.append(para)
            continue
        if para.startswith("\n"):
            out_parts.append(para)
            continue
        # Sentence-split the paragraph.
        sentences = re.split(r"(?<=[.!?])\s+", para)
        kept = [s for s in sentences if not _INTERNAL_TOOL_NAME.search(s)]
        out_parts.append(" ".join(kept))
    return "".join(out_parts).strip()


# GAN R2 R15: empty / apologetic "## News" section leak. The model
# sometimes prints a `## News` (or `## Recent news`) header whose body is
# the banned "I didn't pull any news" phrasing — a stub that fails the
# quality bar. Strip the whole section (header through to the next `##`
# header or end of text) when its body matches the no-news tell.
_EMPTY_NEWS_BODY_TELL = re.compile(
    r"did(?:n'?t|\s+not)\s+(?:pull|fetch|retrieve|have|get)\b"
    r"|not\s+using\s+any\s+(?:headline|news)"
    r"|no\s+(?:recent\s+)?(?:news|headlines?)\s+(?:were\s+)?"
    r"(?:pulled|fetched|available|retrieved)"
    r"|news\s+(?:was\s+)?not\s+(?:pulled|fetched|available)"
    r"|i\s+(?:do\s+not|don'?t)\s+have\s+(?:recent\s+)?news",
    re.IGNORECASE,
)
_NEWS_SECTION_RE = re.compile(
    r"(?:^|\n)#{1,4}\s*(?:recent\s+)?news\b[^\n]*\n"  # the ## News header
    r"(?P<body>.*?)"                                    # its body (lazy)
    r"(?=\n#{1,4}\s|\Z)",                              # up to next header/EOF
    re.IGNORECASE | re.DOTALL,
)


def _strip_empty_news_section(text: str) -> str:
    """Remove a `## News` section whose body says no news was fetched."""
    if not text or "news" not in text.lower():
        return text

    def _repl(m: re.Match) -> str:
        body = m.group("body") or ""
        if _EMPTY_NEWS_BODY_TELL.search(body):
            # Drop the whole section; keep a leading newline so adjacent
            # sections don't fuse.
            return "\n"
        return m.group(0)

    return _NEWS_SECTION_RE.sub(_repl, text)

# Circuit breaker — caps how many tool round-trips one user turn can
# trigger. The agentic loop is allowed to call several tools in a
# row but not run away.
_MAX_TOOL_CALLS = 8


# Compact-draft mode: after a macro draft tool succeeds, the FE
# already has the structured draft to render — the model's prose
# acknowledgment can be a single short line. The default 1500-token
# budget routinely produced 500-1000 token rationale prose that
# duplicated information on the card. Tightening the budget for the
# post-macro-draft hop cuts agent-turn output cost ~60-75% and shaves
# 6-10s of wall-clock on long agent drafts.
#
# Toggle: PIVOT_COMPACT_DRAFTS=0 disables (defaults ON). Honoured at
# import time so a deployment can flip it without code change.
import os as _os
_COMPACT_DRAFTS = _os.environ.get(
    "PIVOT_COMPACT_DRAFTS", "1",
).lower() not in ("0", "false", "off", "no")
_COMPACT_POST_MACRO_MAX_OUTPUT = 250


# ── Intent classification: automation vs agent vs other ─────────────
#
# Two distinct request shapes route to different tools:
#
#   AUTOMATION — single deterministic action with parameters supplied
#                by the user. We just execute the matching tool. No
#                fetch-then-decide. Examples:
#                  "buy 10 RELIANCE at market"       → place_market_order
#                  "GTT 5 TCS if drops to 3000"      → create_gtt_order
#                  "set 5% stop loss on my INFY"     → create_sl_order
#                  "SIP ₹5000 in NIFTYBEES Monday"   → create_sip
#                  "square off intraday"             → squareoff_all_intraday
#
#   AGENT — multi-step workflow. Requires runtime fetches, conditions,
#           or multiple actions per fire. Routes to propose_workflow.
#                  "every Monday if RSI<30 buy INFY" — schedule+fetch+cond+act
#                  "watch portfolio and alert if X"  — continuous+cond+notify
#                  "buy at open & sell at close ev day" — 2 scheduled actions
#                  "buy when it dips 5% from prior"  — runtime fetch + rel thresh
#
# Decision rule encoded below: does the request need a fetch step
# BEFORE the action? If yes → agent. If no → automation.
#
# Mutual exclusivity: agent wins ties (safer to draft a workflow than
# to misfire a single immediate tool).

_AGENT_INTENT_RE = re.compile(
    # Explicit "build/create an agent/strategy/workflow/automation"
    r"\b(?:build|create|set\s*up|setup|make|generate|design)\s+"
    r"(?:me\s+)?an?\s+(?:agent|strategy|workflow|automation|rule|bot)\b"
    # Indicator-anchored conditional — fetch step REQUIRED at runtime
    r"|\bwhen(?:ever)?\b[^\.]{0,80}\b(?:rsi|sma|ema|macd)\b"
    # Watch / monitor + and/then — continuous behaviour
    r"|\b(?:watch|monitor|track|alert\s+me|notify\s+me)\b[^\.]{0,80}"
    r"\b(?:and|then)\b"
    # Explicit "automatically execute" phrasing
    r"|\bautomatic(?:ally)?\s+execut"
    # Conditional rule with PERCENTAGE move (relative — needs runtime
    # fetch of a baseline). Absolute-price conditionals are GTTs,
    # which are automation.
    r"|\bif\b[^\.]{0,120}\b(?:dips?|drops?|falls?|rises?|crosses?)\b"
    r"\s*\d+\s*%"
    r"|\bwhen(?:ever)?\b[^\.]{0,80}\b(?:dips?|drops?|falls?|rises?|crosses?)\b"
    r"\s*\d+\s*%"
    # "X% dip" / "5% drop" + ANY action verb — same fetch-required shape
    r"|\b\d+\s*%\s*(?:dip|drop|fall|rise|crash|gain)\b"
    # Multi-action pattern in one fire WITHOUT explicit quantities —
    # "buy at open AND sell at close" / "buy then sell" — needs a
    # workflow with multiple actions. The two-action-with-qtys-NOW
    # case ("buy 7 RELIANCE and sell 2 ETERNAL") is intercepted
    # earlier in `_classify_intent` and routed to automation, so
    # it doesn't fall into this branch.
    r"|\b(?:buy|long)\b[^\.]{0,60}\b(?:and|then)\b[^\.]{0,60}\b(?:sell|exit|short)\b"
    # SIP that includes a condition is an agent (e.g. "SIP ₹5k every
    # Monday IF cash > ₹50k"). Plain SIP without 'if' is automation.
    r"|\bsip\b[^\.]{0,200}\bif\b"
    # Schedule + conditional: "every X ... if ..." always needs a runtime
    # fetch/condition step before the action → agent, not a single tool.
    # WHY this rule exists: "every weekday at 3:55 PM if buying power > 50k,
    # buy RELIANCE" was being matched by _AUTOMATION_INTENT_RE's
    # "every weekday ... buy" tail, classifying it as "automation" and
    # stripping propose_workflow from the tool surface. The LLM then told the
    # user "the multi-step workflow tool is not available in this chat" —
    # a tool-name leak AND the wrong answer. Adding "every X ... if" here
    # catches it as agent (checked first), keeping propose_workflow in scope.
    r"|\bevery\s+(?:weekday|monday|tuesday|wednesday|thursday|friday|day|week)\b"
    r"[^\.]{0,300}\bif\b"
    # News/event-conditional: "if <institution>" or "if <news verb>". The
    # whole prompt may span multiple sentences ("Buy 5 RELIANCE at open.
    # At 10 AM IST, if RBI cuts the repo rate, sell ...") so we
    # use re.DOTALL via `[\s\S]` rather than `[^\.]` for this branch.
    # Always needs `fetch.news` + `condition.boolean` → agent.
    r"|\bif\b[\s\S]{0,200}?\b(?:RBI|SEBI|MPC|FED|ECB|Moody'?s|S&P|Fitch|"
    r"OpenAI|Apple|Google|Microsoft|Amazon|government|ministry|FII|DII|"
    r"news|headlines?)\b"
    r"|\bif\b[\s\S]{0,200}?\b(?:announces?|announced|cuts?|cut|raises?|"
    r"hikes?|hiked|penali[sz]es?|penali[sz]ed|upgrades?|upgraded|"
    r"downgrades?|downgraded|files?|filed|confirms?|confirmed|launches?|"
    r"launched|approves?|approved|rejects?|rejected|imposes?|imposed|"
    r"signals?|signalled|signaled)\b"
    # Schedule-time + later condition: "at HH:MM ... if ..." — clearly
    # a multi-step workflow with a delayed gate. The single-line variant
    # already routed correctly; this catches the multi-sentence shape.
    r"|\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|ist)\b[\s\S]{0,300}?\bif\b",
    re.IGNORECASE,
)


# "Two-action basket NOW" — buy X and sell Y in the same turn, both
# carrying explicit quantities, with no scheduling / condition. The
# previous AGENT regex caught this as a workflow, but the user's
# intent (per PDF report) is two immediate market orders. We classify
# it here so the LLM sees place_market_order in scope rather than
# propose_workflow.
_TWO_ACTION_NOW_RE = re.compile(
    r"\b(?:buy|long)\b\s+\d+\s+\w+[^\.]{0,80}"
    r"\b(?:and|&)\b[^\.]{0,80}"
    r"\b(?:sell|exit|short)\b\s+\d+\s+\w+",
    re.IGNORECASE,
)
_HAS_SCHEDULE_OR_CONDITION_RE = re.compile(
    r"\bevery\s+(?:monday|tuesday|wednesday|thursday|friday|"
    r"weekday|day|week|month|hour|minute)\b"
    r"|\bif\b|\bwhen(?:ever)?\b|\buntil\b|\bafter\b"
    r"|\bat\s+\d{1,2}:?\d{0,2}\s*(?:am|pm|ist)?\b",
    re.IGNORECASE,
)

_RECURRING_SCHEDULE_RE = re.compile(
    r"\b(?:every|each)\s+(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|"
    r"thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|"
    r"weekday|day|week|month|morning|fortnight)\b"
    r"|\b(?:weekly|daily|monthly|fortnightly|every\s+week)\b",
    re.IGNORECASE,
)

# SESSION-ANCHOR detector — "at open"/"at close"/"at the open"/"market open"
# patterns that the DSL grammar can't express as conditions (they collapse to
# open==open tautologies). When a propose_dsl_workflow fails AND this matches
# the user message, redirect to propose_workflow with trigger.market_relative_time.
# WHY separate from _RECURRING_SCHEDULE_RE: recurring patterns ("every Friday")
# already work in _redirect_target_for_failure; session anchors ("at open")
# need the same redirect but were missing.
_SESSION_ANCHOR_RE = re.compile(
    r"\bat\s+(?:the\s+)?(?:open|close)\b"
    r"|\b(?:market\s+)?(?:open|close)\s+(?:today|tomorrow|every)?\b"
    r"|\bon\s+(?:the\s+)?(?:open|close)\b"
    r"|\b(?:buy|sell)\s+(?:at|on)\s+(?:the\s+)?(?:open|close)\b",
    re.IGNORECASE,
)
_ROUTE_HINT_RE = re.compile(
    r"use\s+(propose_workflow|propose_dsl_workflow|"
    r"propose_threshold_order|propose_scheduled_order|"
    r"propose_holding_action|propose_basket_allocation)\b",
    re.IGNORECASE,
)


def _redirect_target_for_failure(
    tool_name: str, error: str, user_message: str,
) -> Optional[str]:
    """Pick the tool to redirect to after ``tool_name`` failed, or None.

    Primary signal: an explicit "use <tool>" hint in the error string
    (tools emit these to steer the LLM to the right shape).

    Backstop: a ``propose_dsl_workflow`` failure on a RECURRING-SCHEDULE
    ask ("buy INFY every Friday and sell at 10% profit") → route to
    ``propose_workflow`` even when the error carries no hint. The DSL tool
    only builds price/indicator CONDITION triggers; a recurring schedule
    is an ENTRY it can't express. Its refusal error DOES name
    propose_workflow, but when the LLM crams the schedule into the
    condition slot the tool instead fails mid-translation (position-leaf-
    in-entry, self-comparison tautology) with a hint-less error — so the
    redirect must also fire on the schedule SHAPE of the user's message.

    Session-anchor backstop: "at open"/"at close" patterns also can't be
    expressed as DSL conditions (they collapse to open==open tautologies).
    When the error mentions "self-comparison" or "tautology" AND the user
    message contains a session-anchor phrase, redirect to propose_workflow
    which supports trigger.market_relative_time(anchor='open'/'close').
    """
    m = _ROUTE_HINT_RE.search(error or "")
    if m:
        return m.group(1)
    if tool_name == "propose_dsl_workflow":
        # Recurring schedule backstop
        if _RECURRING_SCHEDULE_RE.search(user_message or ""):
            return "propose_workflow"
        # Session-anchor backstop — "at open"/"at close" patterns
        if _SESSION_ANCHOR_RE.search(user_message or ""):
            return "propose_workflow"
        # Tautology / self-comparison error with any schedule-like shape
        err_lower = (error or "").lower()
        if ("self-comparison" in err_lower or "tautology" in err_lower
                or "same thing" in err_lower):
            return "propose_workflow"
    return None


_AUTOMATION_INTENT_RE = re.compile(
    # Imperative buy/sell with quantity (no condition keywords here)
    r"\b(?:buy|sell)\s+\d+\s+[A-Z][A-Z0-9\-_]{1,15}\b"
    # Imperative buy/sell at market / at open / now / today — one-time action.
    # WHY "at open" is here: "buy reliance at open. 10 shares" was classified
    # as "other" (no explicit qty+ticker pattern, no "every weekday"). The LLM
    # then saw propose_workflow in scope and interpreted "at open" as "9:15 AM
    # every day", creating a daily agent instead of a one-time order. Adding
    # "at open" here makes it "automation", stripping all workflow macros so
    # the LLM asks "one-time or recurring?" instead of drafting a daily agent.
    r"|\b(?:buy|sell)\b[^\.]{0,30}\b(?:at\s+(?:market|open)|right\s+now|today)\b"
    # Limit order with explicit price
    r"|\b(?:buy|sell)\b[^\.]{0,40}\b(?:at|@)\s*(?:rs\.?|₹|inr)?\s*\d{2,}\b"
    # GTT / SL / take-profit setup at an ABSOLUTE price ("at ₹1400")
    # or a percentage (handled by create_sl_order via stop_pct)
    r"|\b(?:set|place|put|create|add)\s+(?:a\s+|an\s+)?"
    r"(?:[\d.]+\s*%\s+)?"
    r"(?:stop[- ]?loss|sl|stoploss|trailing\s+stop|take[- ]?profit|tp|target|gtt)\b"
    # GTT / limit order with explicit price reference
    r"|\b(?:gtt|limit\s+order)\b[^\.]{0,80}\b(?:at|to)\s+(?:rs\.?|₹|inr)?\s*\d+"
    # Square-off
    r"|\bsquare\s*off\b"
    # SIP setup — recurring single action, NO condition (the agent regex
    # above catches "SIP … if …" before we get here)
    r"|\bsip\b[^\.]{0,80}\b(?:in|of|for|every)\b"
    # "create a sip" — explicit single-tool request
    r"|\b(?:create|set\s*up|setup|make)\s+(?:a\s+|an\s+)?sip\b"
    # "every Monday 9:15 buy <SYM>" with no condition — recurring SIP
    # automation. The agent regex catches the conditional variant first.
    r"|\bevery\s+(?:weekday|monday|tuesday|wednesday|thursday|friday|"
    r"day|week)\b[^\.]{0,80}\b(?:buy|sell|invest)\b\s+",
    re.IGNORECASE,
)


# Advisory question patterns — "should I reduce X?", "do you think Y?",
# "is it worth buying Z?". When these fire on "other" intent, we strip
# workflow macro tools so the LLM can't call propose_workflow to "helpfully"
# attach a rebalancing agent to an informational answer.
# WHY: "should I reduce that exposure?" after sector breakdown data was
# calling propose_workflow (system prompt rule "never attach a draft to an
# informational answer" is prose-only guidance the LLM ignores under
# some context pressures). Tool-surface removal enforces it structurally.
_ADVISORY_INTENT_RE = re.compile(
    r"\bshould\s+i\b"
    r"|\bwould\s+(?:it\s+be|you\s+recommend)\b"
    r"|\bis\s+it\s+(?:worth|wise|safe|good|bad)\b"
    r"|\bdo\s+you\s+(?:think|recommend|suggest)\b"
    r"|\bwhat\s+do\s+you\s+think\b",
    re.IGNORECASE,
)


# Under-specified agent-build detector. WHY this exists: prompts like
# "build an agent for it" / "make me an agent for ETERNAL" arrive
# carrying a build verb but ZERO action / trigger / quantity / threshold
# information. The model used to fill in fabricated defaults
# (quantity=10, generic schedule, place_order action) and emit a draft
# the user never asked for. With this detector matched, we relax
# `tool_choice` from "required" to "auto" so ASK_USER becomes the
# natural choice — and a system-prompt rule tells the model to ask
# one focused question rather than invent a workflow.
#
# Match shape:
#   has any agent-build verb ("build", "make", "create", "set up",
#   "agent", "workflow", "automation"), AND
#   does NOT have an action verb (buy/sell/short/exit/sip/order),
#   does NOT have a trigger keyword (when/every/if/at/rsi/sma/ema/
#                                       price/cron/schedule), and
#   does NOT have an explicit numeric (₹/quantity/%/digit).
def _is_underspecified_agent_build(message: str) -> bool:
    msg = (message or "").strip().lower()
    if not msg:
        return False
    has_build = bool(re.search(
        r"\b(?:build|make|create|set\s*up|design|spin\s+up)\b"
        r"|\b(?:an?\s+|some\s+)?(?:agent|workflow|automation|strategy|rule|bot)\b",
        msg,
    ))
    if not has_build:
        return False
    has_action = bool(re.search(
        r"\b(?:buy|sell|short|exit|sip|order|place|squareoff|"
        r"square\s+off|stop[-\s]?loss|sl|hold|alert|notify|allocate|"
        r"split|invest|trim|book)\b",
        msg,
    ))
    has_trigger = bool(re.search(
        r"\b(?:when|every|if|whenever|at\s+(?:open|close|\d)|"
        r"rsi|sma|ema|macd|cross(?:es|ed|ing)?|above|below|drops?|"
        r"rises?|crosses?|hits?|breaches?|touches?|reaches?|"
        r"weekday|monday|tuesday|wednesday|thursday|friday|"
        r"daily|weekly|monthly|hourly|cron|schedule)\b",
        msg,
    ))
    has_numeric = bool(re.search(r"\d", msg)) or "₹" in msg or "%" in msg
    return not (has_action or has_trigger or has_numeric)


# R4c: ban level-invention. When the user names a price level by ROLE
# (resistance, support, pivot, breakout, Fibonacci, etc.) but does NOT
# supply a numeric value or a computable definition, the LLM otherwise
# guesses from training memory — screenshot 6: "above resistance" →
# 1,643. Detect the bare-level shape and force underspec so the
# downstream pipeline strips macros and asks one focused question.
_LEVEL_ROLE_RE = re.compile(
    r"\b(?:"
    r"resistance|support|pivot|pivot\s+point|"
    r"breakout|breakdown|break(?:s|ing)?\s+out|break(?:s|ing)?\s+down|"
    r"key\s+level|swing\s+(?:high|low)|"
    r"fib(?:onacci)?(?:\s+(?:level|retracement))?|"
    r"trend\s*line|trendline|"
    r"bollinger\s+(?:upper|lower)|donchian\s+(?:upper|lower)"
    r")\b",
    re.IGNORECASE,
)

# A numeric anchor anywhere in the message — rupee number, percentage,
# rolling-window reference, or any decimal/integer that could be the
# level the user named. When present, the level role is grounded.
_LEVEL_ANCHOR_RE = re.compile(
    r"(?:"
    r"\b\d+\s*(?:-?\s*day|d|sessions?)\s+(?:high|low|rolling)\b|"
    r"₹\s*[\d,]+|"
    r"\b(?:rs\.?|inr)\s*[\d,]+\b|"
    r"\b\d+(?:\.\d+)?\s*%\b|"
    r"\b\d+\.\d+\b|"   # decimal value like 61.8 (Fibonacci anchor)
    r"\b\d{3,}\b"      # ≥3-digit raw number → likely a price like 1640
    r")",
    re.IGNORECASE,
)


def _is_ungrounded_level_prompt(message: str) -> bool:
    """True when the user names a price level by ROLE without supplying
    a numeric value or computable definition. The chat layer treats
    this like underspec and asks one focused question instead of
    letting the LLM invent a number from training memory."""
    msg = (message or "").strip()
    if not msg:
        return False
    if not _LEVEL_ROLE_RE.search(msg):
        return False
    if _LEVEL_ANCHOR_RE.search(msg):
        return False
    return True


# Filler-reply detector. WHY this exists: when the bot just asked a
# clarification ("What should the agent do — buy on a schedule, RSI
# trigger, alert?") and the user replies with "hmm" / "ok" / "you
# decide" / "whatever", the model interpreted it as "pick a default
# and emit". It then fabricated a propose_scheduled_order with
# weekday-09:15-1share — a workflow the user never specified.
#
# The right behaviour is: re-ask one focused question OR continue the
# conversation in prose. Treat filler-after-question as still-
# underspec at the routing layer (strip macros, tool_choice=auto).
_FILLER_REPLY_RE = re.compile(
    r"^\s*(?:"
    # Pure interjections — clearly non-committal.
    r"hmm+|huh+|uh+|um+|er+|hm+|aha+|"
    r"idk|i\s+don'?t\s+know|not\s+sure|no\s+idea|no\s+pref(?:erence)?|"
    # "you decide" family — explicitly handing off the choice.
    r"you\s+(?:decide|choose|pick)|your\s+(?:choice|call|pick)|"
    r"whatever|doesn'?t\s+matter|don'?t\s+care|either\s+(?:works|is\s+fine)|"
    r"any(?:thing)?\s+(?:works|is\s+fine|of\s+(?:them|those))|"
    r"(?:any|all)\s+of\s+(?:the\s+)?(?:above|those|them)|"
    # Genuinely uncertain — these don't commit. (Note: "sure",
    # "fine", "ok", "alright", "yes" are NOT in this list — they're
    # AFFIRMATIVES to a yes/no question and the user expects the
    # bot to PROCEED, not re-ask. Stripping macros on those would
    # block the model from emitting the draft the user just OK'd.)
    r"maybe|perhaps|"
    # Single reaction words — non-committal.
    r"lol|haha|wow|nice|cool"
    r")\s*[.!?]?\s*$",
    re.IGNORECASE,
)


# F&O / options / futures detection. WHY (P1 rewrite): options are now
# WIRED — when the user mentions options/strikes/expiry/F&O the gate
# (a) ADDS the options tool surface (chain / suggest / build / critique
# / portfolio greeks) so the model can actually serve the ask, and
# (b) still STRIPS the cash-equity order tools so it can't hallucinate
# a place_market_order on an options ticker. Futures execution remains
# unwired — the options tools answer the research side of futures asks
# and say so for execution.
_FNO_RE = re.compile(
    r"\bf\s*&?\s*o\b"  # f&o, fno, F&O
    r"|\boptions?\b|\bcalls?\b|\bputs?\b|\bfutures?\b"
    r"|\bstraddles?\b|\bstrangles?\b|\bspreads?\b"
    r"|\bcondor\b|\bbutterfly\b|\bcollars?\b"
    r"|\bstrike\s+price\b|\bexpiry\b|\bATM\b|\bITM\b|\bOTM\b"
    r"|\bcall\s+option\b|\bput\s+option\b"
    r"|\bweekly\s+(?:call|put|option)\b"
    r"|\b(?:nifty|banknifty|finnifty)\s+(?:call|put|option|future)\b",
    re.IGNORECASE,
)


def _mentions_fno(message: str) -> bool:
    return bool(_FNO_RE.search(message or ""))


# The options tool surface the _mentions_fno gate ADDS (mirrors
# TOOL_SUBSETS["OPTIONS_QUERY"] — kept literal here so the gate has no
# import-order dependency on agents.tools).
_OPTIONS_TOOLS: frozenset[str] = frozenset({
    "get_option_chain", "suggest_option_strategy",
    "build_option_strategy", "critique_option_strategy",
    "get_portfolio_greeks",
})


# ── R4: named-option-template BUILD detector ───────────────────────────
# GAN R2 regression: "build me an iron condor on NIFTY" bounced to
# ASK_USER even though the system prompt mandates a delta-defaulted build
# and `_build_option_strategy` supports a zero-strike build. Prose alone
# does not suppress the always-appended ASK_USER escape hatch. When the
# message names a known multi-leg TEMPLATE *with* an underlying, we
# deterministically narrow scope to build_option_strategy + force the
# tool so the model cannot escape to ASK_USER.
_OPTION_TEMPLATE_RE = re.compile(
    r"\b(?:"
    r"iron[\s_-]?condor|iron[\s_-]?butterfly|"
    r"straddle|strangle|"
    r"(?:bull|bear)\s+(?:call|put)\s+spread|"
    r"call\s+spread|put\s+spread|credit\s+spread|debit\s+spread|"
    r"vertical\s+spread|calendar\s+spread|diagonal\s+spread|"
    r"covered\s+call|protective\s+put|collar|"
    r"\bcondor\b|\bbutterfly\b|\bratio\s+spread\b"
    r")\b",
    re.IGNORECASE,
)
# Underlying must be named for a deterministic build (an index or a
# 3-15 char ticker). We accept the common F&O indices explicitly plus a
# generic uppercase-ish ticker token preceded by on/for/of/in.
_OPTION_UNDERLYING_RE = re.compile(
    r"\b(?:nifty|banknifty|bank\s*nifty|finnifty|midcpnifty|sensex|bankex)\b"
    r"|\b(?:on|for|of|in)\s+([A-Z][A-Z0-9&\-]{1,14})\b",
    re.IGNORECASE,
)
# Verbs that indicate an explicit BUILD (vs suggest/critique/chain read).
_OPTION_BUILD_VERB_RE = re.compile(
    r"\b(?:build|make|create|set\s*up|construct|give\s+me|"
    r"open|put\s+on|do)\b",
    re.IGNORECASE,
)


def _is_named_option_build(message: str) -> bool:
    """True when the user explicitly asks to BUILD a named multi-leg
    option template with an underlying — a documented canonical
    buildable that must NEVER bounce to ASK_USER."""
    msg = (message or "").strip()
    if not msg:
        return False
    if not _OPTION_TEMPLATE_RE.search(msg):
        return False
    if not _OPTION_UNDERLYING_RE.search(msg):
        return False
    # Default to True when a template + underlying are present; a build
    # verb strengthens it but "an iron condor on NIFTY this week" with no
    # verb is still unambiguously a build request.
    return True


# ── R5: unsupported-rail boundary detector ─────────────────────────────
# Sentiment / mood / tone polarity triggers are NOT a real rail (the only
# news rail is keyword/event). The model obeyed the imperative US-equity
# row but dropped the terse sentiment row, asking quantity and thereby
# AFFIRMING a fabricated capability on an auto-execute path. Detect the
# unsupported rail so we can force the boundary-first reply.
_UNSUPPORTED_RAIL_RE = re.compile(
    r"\b(?:"
    # Sentiment / mood / tone polarity on news or social
    r"sentiment|(?:news|headline|social|twitter|tweet)\s+(?:turns?|goes?|"
    r"gets?|becomes?)\s+(?:negative|positive|bearish|bullish|bad|sour)|"
    r"(?:turns?|goes?|gets?)\s+(?:negative|positive|bearish|bullish)\b|"
    r"mood\s+(?:turns?|sours?|shifts?)|bad\s+news|negative\s+news|"
    r"news\s+sentiment|"
    # IV rank / IV percentile (needs IV history — not wired)
    r"iv\s+rank|iv\s+percentile|"
    # UPI / macro-feed style triggers users sometimes ask for
    r"upi\s+(?:data|volume|transactions?)|gdp\s+(?:print|data)|"
    r"inflation\s+(?:print|data)\s+(?:above|below)"
    r")\b",
    re.IGNORECASE,
)


def _names_unsupported_rail(message: str) -> Optional[str]:
    """Return a short rail label when the message asks for an
    unsupported automation trigger rail (sentiment NLP, IV-rank,
    macro feed), else None. Used to force a boundary-first reply that
    names the nearest real alternative BEFORE any value question."""
    msg = (message or "")
    if not _UNSUPPORTED_RAIL_RE.search(msg):
        return None
    low = msg.lower()
    if "iv rank" in low or "iv percentile" in low:
        return "iv_rank"
    if "upi" in low or "gdp" in low or "inflation" in low:
        return "macro_feed"
    return "sentiment"


# ── R3: notify-only alert detector ─────────────────────────────────────
# "just alert me when AXISBANK crosses 1300, don't buy anything" fires
# two stacked hard gates yet the model still emitted a spurious
# "in-app only?" ASK_USER though in-app is the ONLY channel. Detect a
# fully-specified notify-only alert so we can force the notify_only DSL
# workflow and suppress the escape hatch.
_ALERT_VERB_RE = re.compile(
    r"\b(?:alert|notify|ping|let\s+me\s+know|tell\s+me|remind\s+me|"
    r"watch|flag|warn)\b",
    re.IGNORECASE,
)
_NO_TRADE_MARKER_RE = re.compile(
    r"\b(?:don'?t|do\s+not|no|without)\s+(?:buy|sell|trade|order|place|"
    r"executing?|placing)\b"
    r"|\b(?:just|only)\s+(?:alert|notify|ping|let\s+me\s+know|tell\s+me|"
    r"watch|warn)\b"
    r"|\bno\s+(?:order|trade|buy|sell)\b",
    re.IGNORECASE,
)
_PRICE_LEVEL_RE = re.compile(
    r"\b(?:cross(?:es|ed|ing)?|hits?|reach(?:es|ed)?|touch(?:es|ed)?|"
    r"break(?:s|ing)?|above|below|over|under|drops?\s+to|rises?\s+to|"
    r"goes?\s+(?:above|below|over|under))\b"
    r".{0,20}[₹$]?\s*\d[\d,]*(?:\.\d+)?"
    r"|[₹$]\s*\d[\d,]*",
    re.IGNORECASE,
)


def _is_notify_only_alert(message: str) -> bool:
    """True when the message is a fully-specified notify-only price
    alert (alert verb + a price level + a no-trade marker). Such a
    prompt must register a notify_only DSL workflow, never bounce to
    ASK_USER about a channel that doesn't vary."""
    msg = (message or "").strip()
    if not msg:
        return False
    if not _ALERT_VERB_RE.search(msg):
        return False
    if not _PRICE_LEVEL_RE.search(msg):
        return False
    return bool(_NO_TRADE_MARKER_RE.search(msg))


# ── R2: buy/sell-at-open|close detector ────────────────────────────────
# "buy 5 BAJAJ-AUTO at open, book +3% profit" must build a two-branch
# market_relative_time card — never a 09:30 cron downgrade and never
# ask_user. Detect an open/close anchor so we can pin propose_workflow
# / propose_dsl_workflow and forbid the 09:30 fallback.
_AT_OPEN_CLOSE_RE = re.compile(
    r"\b(?:at|on|in|after|before)\s+(?:the\s+)?(?:market\s+)?"
    r"(?:open|close|opening|closing)\b"
    r"|\bat\s+open\b|\bat\s+close\b|\bbuy\s+at\s+open\b|"
    r"\bsell\s+at\s+close\b|\bpre[- ]?open\b|\bopening\s+bell\b|"
    r"\bclosing\s+bell\b|\bmarket\s+open\b|\bmarket\s+close\b",
    re.IGNORECASE,
)


def _is_at_open_close_build(message: str) -> bool:
    """True when the message references an at-open/at-close anchor in a
    build/order context. Forces market_relative_time and bans the 09:30
    downgrade."""
    msg = (message or "").strip()
    if not msg:
        return False
    if not _AT_OPEN_CLOSE_RE.search(msg):
        return False
    # Must look like an order / agent build, not a conceptual question.
    return bool(re.search(
        r"\b(?:buy|sell|short|exit|book|enter|build|make|create|"
        r"set\s*up|agent|workflow|automation|place|order|sip)\b",
        msg, re.IGNORECASE,
    ))


# ── R6: confusion-after-menu detector ──────────────────────────────────
# When the prior assistant turn was an ASK_USER MENU and the user now
# says "I don't understand / which did you use / why that", the model
# re-dumps the identical menu. Force a TEACH reply instead.
_CONFUSION_META_RE = re.compile(
    r"\bi\s+don'?t\s+(?:understand|get\s+it|follow)\b"
    r"|\bwhat\s+do\s+you\s+mean\b|\bnot\s+sure\s+what\s+you\s+mean\b"
    r"|\bwhich\s+(?:one\s+)?(?:did\s+you|do\s+you)\s+(?:use|mean|pick)\b"
    r"|\bwhy\s+(?:that|those|this|these|did\s+you)\b"
    r"|\bcan\s+you\s+explain\b|\bhuh\??\s*$|\bconfused\b|\bnot\s+clear\b"
    r"|\bwhat'?s\s+the\s+difference\b",
    re.IGNORECASE,
)


def _prev_assistant_was_menu(history: list) -> bool:
    """True when the most recent assistant turn offered a multi-option
    menu (numbered list, 'A or B', or two '?'-bearing options). Used to
    distinguish the menu-confusion path from the answer-confusion path
    (the latter already teaches correctly)."""
    for h in reversed(history or []):
        if (h or {}).get("role") != "assistant":
            continue
        content = ((h or {}).get("content") or "").lower()
        if not content:
            return False
        has_q = "?" in content
        # Numbered/lettered options, or " or " between choices, or
        # multiple bullet markers — the classic 3-option menu shape.
        menu_shape = bool(
            re.search(r"(?:^|\n)\s*(?:[1-3][\.\)]|[a-c][\.\)]|[-*•])\s", content)
            or re.search(r"\b(?:option\s+[1-3a-c]|"
                         r"\(a\)|\(b\)|\(c\))\b", content)
            or (" or " in content and content.count("?") >= 1)
        )
        return has_q and menu_shape
    return False


def _is_confusion_after_menu(message: str, history: list) -> bool:
    """True when the user expresses confusion / asks a meta question
    immediately after we showed an ASK_USER menu."""
    if not _CONFUSION_META_RE.search(message or ""):
        return False
    return _prev_assistant_was_menu(history)


# Contradiction detector — "buy AND sell same symbol same time".
# Pivot can do paired buy/sell with different triggers (multi-branch
# workflow), but a literal "buy and sell at the same time" is
# self-cancelling — should ASK_USER, never silently pick one or
# draft both as a workflow.
# WHY tightened to specific time-words: the prior regex matched
# "at the same DAY'S close" which is a perfectly valid two-branch
# workflow (buy at open, sell at the same day's close). Require an
# explicit simultaneity word — "time"/"moment"/"instant"/"second" —
# so multi-branch workflows that share a day don't trip the gate.
_CONTRADICTION_RE = re.compile(
    r"\b(?:buy|sell)\b.{0,80}\b(?:and|while|plus|along)\b.{0,30}"
    r"\b(?:sell|buy)\b.{0,40}"
    r"\b(?:simultaneously|"
    r"at\s+the\s+same\s+(?:time|moment|instant|second)|"
    r"at\s+the\s+exact\s+same|"
    r"at\s+once|right\s+now"
    r")\b",
    re.IGNORECASE,
)


def _is_buy_sell_contradiction(message: str) -> bool:
    return bool(_CONTRADICTION_RE.search(message or ""))


def _is_filler_reply(message: str) -> bool:
    """True when the user's reply is filler / non-committal — should
    not trigger a fabricated default. Conservative: only matches when
    the WHOLE message is filler; "hmm, let me think about RSI" is
    not filler (the model can pick up "RSI")."""
    return bool(_FILLER_REPLY_RE.match(message or ""))


def _prev_assistant_was_question(history: list) -> bool:
    """True when the most recent assistant turn asked the user a
    question. Looks at the WHOLE response (not just the last char)
    because the model often appends a sentence after the question
    ("...or alert you? Also specify the amount.") — pure
    `endswith("?")` would miss those.

    Heuristic: a `?` anywhere in the LAST PARAGRAPH of the assistant
    message, OR the message contains a clarifying-question phrase
    that's a strong tell ("what should", "which", "how many", "do
    you want", "could you").
    """
    for h in reversed(history or []):
        if h.get("role") != "assistant":
            continue
        content = (h.get("content") or "").strip()
        if not content:
            return False
        # Question mark anywhere in the last paragraph (last 400 chars).
        tail = content[-400:].lower()
        if "?" in tail:
            return True
        # Phrase-level cues for clarifying questions even when the
        # punctuation is missing or mangled.
        if any(p in tail for p in (
            "what should", "what would you like",
            "which one", "which would",
            "how many", "how much",
            "do you want", "would you like",
            "could you", "can you confirm", "tell me",
        )):
            return True
        return False
    return False
# Bare-token typo / filler detector. WHY this exists: when an active
# order or workflow card is on screen and the user types a short
# alphabetic single-token message that isn't a recognized affirmative,
# negation, ticker or verb (the canonical bug: "nothung" — typo for
# "nothing"), the model would re-emit the prior card from conversation
# history. Stripping order + macro tools when this pattern matches AND
# a draft was sitting in cache prevents the spurious re-emit; the
# model is forced to either fetch (if it is a ticker) or prose-reply.
#
# Anchors: 3-12 chars, alphabetic only, optional trailing "?". Mixed
# case (lowercase user typing OR all-caps tickers like RELIANCE both
# match) — we differentiate by the stripping-only-when-active-draft
# guard rather than by case.
_BARE_TOKEN_RE = re.compile(r"^\s*[A-Za-z]{3,12}\s*\??\s*$")

# Tokens that ARE bare alphabetic strings but are recognized
# verbs / affirmatives / negations / fillers — never strip on these
# because the model already knows what to do with them.
_BARE_TOKEN_KNOWN_KEYWORDS: frozenset[str] = frozenset({
    "yes", "yep", "yup", "yeah", "yea", "y", "sure", "ok", "okay",
    "fine", "good", "great", "perfect",
    "no", "nope", "nah", "n",
    "cancel", "stop", "exit", "quit", "kill", "end", "done", "abort",
    "help", "wait", "hold", "pause", "back", "skip", "next", "more",
    "less", "redo", "undo",
    "confirm", "activate", "save", "register", "execute", "run",
    "go", "start", "begin", "now", "later", "soon",
    "thanks", "thank", "thx", "ty", "cheers", "great",
    "buy", "sell", "exit", "short", "long", "hold",
})

# Order + macro tool families to strip on the typo-guard path.
# Read-only / data tools stay in scope so the model can still answer
# the message naturally if the bare token IS a real ticker.
_ORDER_AND_MACRO_TOOLS: frozenset[str] = frozenset({
    "propose_workflow", "propose_scheduled_order",
    "propose_threshold_order", "propose_basket_allocation",
    "propose_holding_action",
    "place_market_order", "place_limit_order", "create_gtt_order",
    "create_sl_order", "create_oco_order", "create_dip_buy",
    "place_basket_order", "create_sip", "create_strategy",
    "squareoff_all_intraday", "squareoff_symbol",
})


def _is_bare_typo_continuation(message: str) -> bool:
    """True for short single-token alphabetic messages that aren't a
    known affirmative / verb / continuation keyword.

    Used together with `had_active_draft_at_entry` to decide whether
    to strip order + macro tools so the model can't re-emit the prior
    card on a typo'd follow-up.
    """
    msg = (message or "").strip()
    if not _BARE_TOKEN_RE.match(msg):
        return False
    base = re.sub(r"[?!.,;:\s]+$", "", msg).lower()
    return base not in _BARE_TOKEN_KNOWN_KEYWORDS


# Exception: even advisory language shouldn't strip macros when the user
# explicitly wants to build/set up an automation. If present, workflow
# tools stay in scope despite the advisory phrasing.
_ADVISORY_WORKFLOW_EXCEPTION_RE = re.compile(
    r"\bset\s+up\b|\bbuild\b|\bcreate\b"
    r"|\bstrategy\b|\bagent\b|\bautomation\b|\bworkflow\b|\bsip\b"
    r"|\bevery\s+(?:monday|tuesday|weekday|day|week)\b"
    r"|\bwhen\s+(?:rsi|sma|ema|price)\b",
    re.IGNORECASE,
)

# Tools whose successful call should have the draft stashed so the next
# turn gets an amendment hint. propose_workflow is the primary case;
# propose_threshold_order and propose_scheduled_order also produce
# editable draft cards the user may want to amend.
_STASH_DRAFT_TOOLS: frozenset[str] = frozenset({
    "propose_workflow",
    "propose_threshold_order",
    "propose_scheduled_order",
    "propose_basket_allocation",
    "propose_holding_action",
    # [C1] propose_dsl_workflow produces an editable workflow_draft_card
    # exactly like propose_workflow. Omitting it meant DSL agent drafts
    # were never stashed → the next turn had no active_draft → amendments
    # ("set an expiry for 30 days", "make it the 50-day high") got no
    # amendment hint and re-entered slot-filling (the "re-asks shares"
    # bug). It belongs in this set alongside every other draft tool.
    "propose_dsl_workflow",
    # backtest_workflow draft cards are amendable too — "try it with
    # 20/50 SMA instead", "add a trailing stop", "use 5 years not 3"
    # were producing wrong-tool dispatches because the prior backtest
    # draft never made it into the active-draft cache.
    "backtest_workflow",
})

# All macro tools that produce draft cards — superset of _STASH_DRAFT_TOOLS.
# Used to build the amendment hint for any active macro draft type.
_MACRO_AMENDMENT_TOOLS: frozenset[str] = frozenset({
    "propose_workflow", "propose_threshold_order", "propose_scheduled_order",
    "propose_basket_allocation", "propose_holding_action",
    # [C1] DSL agent drafts amend identically — the generic re-emit hint
    # ("Re-emit `propose_dsl_workflow` with ALL parameters from the
    # draft, only updating the changed field") is correct for them.
    "propose_dsl_workflow",
    # See _STASH_DRAFT_TOOLS comment — backtest amendments must re-emit
    # backtest_workflow, not propose_workflow or get_multiple_indicators.
    "backtest_workflow",
    # F&O P1: option strategy cards amend by re-emitting
    # build_option_strategy (suggest/critique results stash AS
    # build_option_strategy with a compact spec — see
    # _option_draft_spec). "use the 23400 strike", "make it 2 lots",
    # "switch to next expiry" are amendments, not new intents.
    "build_option_strategy",
})

# Card-producing option tools whose results stash a COMPACT re-emit
# spec (the full card payload blows the 1800-char draft-JSON budget in
# the amendment hint — a 61-point payoff array alone is ~2KB).
_OPTION_CARD_TOOLS: frozenset[str] = frozenset({
    "suggest_option_strategy", "build_option_strategy",
    "critique_option_strategy",
})


def _option_draft_spec(data: dict) -> dict:
    """Compact build_option_strategy arg-shape from a strategy card
    payload — what the amendment hint feeds back to the LLM."""
    editable = (data or {}).get("editable") or {}
    locked = (data or {}).get("locked") or {}
    return {
        "underlying": locked.get("underlying"),
        "template": editable.get("template"),
        "expiry": locked.get("expiry"),
        "qty_lots": editable.get("qty_lots", 1),
        "strikes": [
            l.get("strike") for l in (editable.get("legs") or [])
        ],
        "legs": [
            {"option_type": l.get("option_type"), "side": l.get("side"),
             "strike": l.get("strike")}
            for l in (editable.get("legs") or [])
        ],
    }

# Tools that return structured data the FE can render as a card
# directly. For these the model's prose can be a short ack because
# the user sees the card. Currently this set is **macro-draft tools
# only** — they emit a workflow_draft_card / logic_card the FE
# renders.
#
# WHY analytics tools were REMOVED from this set: get_indicator /
# compare_performance / get_correlation_matrix etc. don't have a
# dedicated FE widget yet — their results render as plain text.
# Compact-mode on those produced "Done — compare_performance ran"
# with NO visible values. Letting the model write 200-300 tokens
# of prose lets it quote the actual numbers (RSI 59.28, Sharpe
# −3.44, etc.) so the user sees the answer.
_COMPACT_PROSE_TOOLS: frozenset[str] = frozenset({
    "get_top_movers",  # already has rich prose patterns from earlier
})


def _classify_intent(message: str) -> str:
    """Return one of {'agent', 'automation', 'other'}.

    Agent wins ties — better to over-draft a workflow than misfire a
    single immediate tool. 'other' covers data lookups, conversation,
    explanations.

    EXCEPT: a two-action basket NOW ("buy 7 reliance and sell 2
    eternal") with no scheduling or conditional language is a pair
    of immediate market orders, not a workflow. The previous regex
    caught the buy+and+sell shape and routed it to propose_workflow,
    which then asked for permission and built a daily 15:25 agent
    around it (PDF report).
    """
    if not message:
        return "other"
    if (
        _TWO_ACTION_NOW_RE.search(message)
        and not _HAS_SCHEDULE_OR_CONDITION_RE.search(message)
    ):
        return "automation"
    if _AGENT_INTENT_RE.search(message):
        return "agent"
    if _AUTOMATION_INTENT_RE.search(message):
        return "automation"
    return "other"


# Short affirmative patterns — single words or garbled typos that mean "yes"
# when they appear as a reply to a prior assistant question. If matched, the
# caller should treat the response as context-confirming, not as a new ticker.
_SHORT_AFFIRMATIVE_RE = re.compile(
    r"^(?:yes|yep|yeah|yup|ya|yah|sure|ok|okay|k|yse|ues|ye|yer|sur|pls|please|fine|alright|y)\.?$",
    re.IGNORECASE,
)

# Order-intent keywords in recent history — signals the prior turns were about
# placing an immediate order, so a follow-up affirmative must stay "automation".
_ORDER_VERB_RE = re.compile(r"\b(buy|sell|order|place|purchase|short)\b", re.IGNORECASE)


def _is_post_order_clarification(message: str, history: list[dict]) -> bool:
    """Return True when the current message is a short affirmative replying
    to an ASK_USER turn that was triggered by an order intent.

    WHY this exists: "yes, SWIGGY on NSE" after the bot asked "which ticker
    for Swiggy?" used to be classified as 'other' (no order verb in the
    current message), putting propose_workflow back in scope. The LLM then
    upgraded a one-time buy to a recurring workflow. This helper detects that
    pattern and returns True → caller forces 'automation' intent.

    Guard: if the FIRST user message classifies as 'agent' (e.g. "build
    an agent — buy X if Y"), this override must NOT fire. Otherwise the
    clarification answer gets routed to automation tools (place_market_
    order) and the macro draft path is unreachable — observed on the
    "use 20-day rolling high" follow-up to "build an agent" prompts.
    """
    if not history or len(history) < 2:
        return False
    # If the original (first) user message was already agent intent,
    # the clarification answer continues the agent flow — don't override.
    first_user_msg = next(
        (m.get("content") or "" for m in history if m.get("role") == "user"),
        "",
    )
    if first_user_msg and _classify_intent(first_user_msg) == "agent":
        return False
    # Last two messages: penultimate user, last assistant
    last_assistant = next(
        (m["content"] for m in reversed(history) if m["role"] == "assistant"), ""
    )
    # Did the assistant just ask a clarifying question?
    if "?" not in last_assistant:
        return False
    # Is the current message a short affirmative OR contains a ticker name
    # after yes/no (e.g. "yes, SWIGGY")?
    msg_stripped = message.strip()
    if len(msg_stripped) > 40:
        return False  # too long to be a simple confirmation
    # Does the recent user history contain an order verb?
    recent_user_msgs = " ".join(
        m["content"] for m in history if m["role"] == "user"
    )
    return bool(_ORDER_VERB_RE.search(recent_user_msgs))


def _looks_like_agent_intent(message: str) -> bool:
    """Back-compat wrapper. Use _classify_intent for the three-way
    distinction; this stays for the few existing call sites that
    just need the agent / not-agent boolean."""
    return _classify_intent(message) == "agent"


# ── M1: unstructured clarification detector ────────────────────────
#
# Catches the failure shape "assistant writes a free-form question
# instead of calling ASK_USER". When this is detected at the final-
# text branch, the chat layer pushes a "USE ASK_USER" directive and
# retries the hop once. Keeps clarifications structured so the
# next-turn PendingResolution path resolves deterministically.
_CLARIFY_PROSE_RE = re.compile(
    r"\b(?:"
    r"do\s+you\s+(?:want|mean)|"
    r"did\s+you\s+mean|"
    r"would\s+you\s+like|"
    r"want\s+me\s+to|"
    r"should\s+(?:i|it)|"
    r"which\s+(?:one|of|symbol|stock|ticker)|"
    r"how\s+(?:many|much)|"
    r"what\s+(?:exact|specific|amount|quantity)|"
    r"or\s+do\s+you\s+have|"
    r"please\s+(?:confirm|share|specify|tell)|"
    r"could\s+you\s+(?:confirm|share|specify|tell)|"
    r"can\s+you\s+(?:confirm|share|specify|tell)"
    r")\b",
    re.IGNORECASE,
)

# "If you want, I'll proceed" / "I can run that as-is" / "I'll set
# that up" — non-question prose offering an action without doing
# anything. Often appears after a tool error the model didn't
# recover from. Treated as unstructured clarification so M1
# re-emits via the appropriate tool.
_OFFER_TO_ACT_RE = re.compile(
    r"\bif\s+you\s+want[,\.\s]+i('|')?(?:ll|\s*ll|\s*will|\s+can)\b"
    r"|\bi\s+can\s+(?:run\s+(?:it|that)|set|apply|proceed|go\s+ahead|"
    r"do\s+(?:it|that))[^.]*?\bas[\s-]*is\b"
    r"|\bi('|')?(?:ll|\s*ll)\s+(?:proceed|run|set|apply|go|do)\b[^.]*?"
    r"\bas[\s-]*is\b"
    r"|\bi('|')?(?:ll|\s*ll)\s+(?:treat|interpret)\s+(?:that|it|this)\s+as\b",
    re.IGNORECASE,
)


def _looks_like_unstructured_clarification(
    text: str, tools_called: list[str], raw_data: dict,
) -> bool:
    """True when the assistant text is a free-form clarification or
    over-confirmation offer-to-act AND no ASK_USER tool was called
    AND no card was emitted. Forces an M1 retry that re-emits via
    the appropriate tool."""
    if not text:
        return False
    # If ASK_USER was already called this turn, the question is
    # already structured.
    if "ASK_USER" in (tools_called or []):
        return False
    # If a card / draft / order is rendering, the text is a
    # caption alongside the card — not a clarification.
    if isinstance(raw_data, dict):
        render_hint = raw_data.get("_render_hint")
        if render_hint in {
            "workflow_draft_card", "logic_card",
            "indicator_backtest_chart", "multistep_card",
            "financial_backtest_chart",
            # Card-bearing turns are captions, not clarifications —
            # IPO + options cards included (F&O P1).
            "ipo_application_card", "ipo_list_card", "ipo_listed_card",
            "option_chain_card", "option_strategy_card",
        }:
            return False
    # Length-based skip: explainers and long analytical replies
    # legitimately end with "If you want, I can also..." — a polite
    # follow-up offer, NOT a clarification request. Only treat
    # short messages (≤ 320 chars after stripping markdown) as
    # potential clarification prose. This caught the L13 regression
    # where M1 wrongly forced ASK_USER on 350-token explainers.
    stripped = text.strip()
    if len(stripped) > 320:
        return False
    # Markdown structure: if the message contains a `##` heading
    # OR more than one bullet, it's a structured analytical reply,
    # not a clarification.
    if "## " in text or text.count("\n- ") >= 2:
        return False
    tail = text.rstrip()
    # Strong signal: ends with "?".
    if tail.endswith("?"):
        return True
    # Weaker signal: contains a clarification-shaped phrase
    # AND a "?" appears anywhere in the text (the model may
    # have written multiple sentences).
    if "?" in text and _CLARIFY_PROSE_RE.search(text):
        return True
    # Over-confirmation: "If you want, I'll proceed" / "I can run
    # that as-is" — model is offering to act without doing anything.
    # Often follows a tool error the model didn't recover from.
    if _OFFER_TO_ACT_RE.search(text):
        return True
    return False


# ── Reply-class classifier (R5) ─────────────────────────────────────
#
# `_classify_intent` decides whether to route the turn into a tool
# (agent / automation / backtest) or leave it as conversation
# ("other"). That bucket lumps explainers, capability questions, and
# small talk — three asks with very different ideal reply shapes. The
# blanket "≤120 words conversational" rule kills explainer answers
# (image 11: "Explain business model of Reliance" came back as three
# thin paragraphs with no structure). The reply_class sub-classifier
# below splits "other" so we can size the budget per-shape.

_EXPLAINER_INTENT_RE = re.compile(
    r"\b("
    r"explain|describe|tell\s+me\s+about|tell\s+about|what\s+(?:is|are)\b|"
    r"what\s+does\s+\w+\s+(?:do|mean|stand\s+for)|how\s+does\b|"
    r"how\s+(?:do|does|did)\s+\w+\s+work|why\s+(?:is|are|does|did)\b|"
    r"compare\b|pros\s+and\s+cons|"
    r"thesis|business\s+model|fundamentals|investment\s+case|"
    r"which\s+(?:is|has)\s+(?:better|stronger|cheaper|safer|cheaper)|"
    r"difference\s+between|breakdown\s+of|overview\s+of|key\s+metrics"
    r")\b",
    re.IGNORECASE,
)

# ANALYSIS-class detector — single-stock / comparative analysis asks that
# need structured, reasoned output (## Snapshot / ## Technicals /
# ## Fundamentals / ## News / ## View). These fire BEFORE analytical_short
# so the model gets a 250-450 word budget with explicit section guidance.
# WHY this is separate from EXPLAINER: "explain RSI" (concept) is explainer;
# "analyse HDFCBANK" (apply-the-data-and-reason) is analysis.
_ANALYSIS_INTENT_RE = re.compile(
    r"\b(?:"
    # Direct analysis verbs AND the analysis NOUN. GAN R2 R1: the verb
    # ("analyse") matched but "give me a proper analysis of HDFCBANK"
    # (noun) fell to analytical_short and got the thinnest reply of all.
    r"analy[sz]e|analy[sz](?:is|es)|deep\s+dive|breakdown\s+of|"
    r"full\s+(?:report|picture|rundown)\s+(?:on|of)|rundown\s+(?:on|of)|"
    # "what do you think of X" / "your view on X" / "your take on X"
    r"what\s+do\s+you\s+think\s+(?:of|about)|"
    r"your\s+(?:view|take|read|thoughts?|opinion)\s+(?:on|about)|"
    # Valuation asks: "is X expensive/cheap/overvalued/undervalued"
    r"(?:is|are)\s+\w+\s+(?:expensive|cheap|over\s*valued|under\s*valued|"
    r"fairly\s*valued|a\s+buy|a\s+sell|worth\s+buying)|"
    # Risk / quality asks: "how risky is X" / "is X risky"
    r"how\s+risky\s+(?:is|are)|(?:is|are)\s+\w+\s+(?:risky|safe|quality)|"
    # "X vs Y" comparison with 'vs' / 'versus' / explicit "comparison"
    r"\bvs\.?\b|\bversus\b|\bcomparison\b|"
    # Dividend play / income angle: "good dividend play" / "dividend stock"
    r"dividend\s+(?:play|stock|pick|yield)|"
    # "which one is better" / "which has better" pattern
    r"which\s+(?:one\s+)?(?:is|has)\s+(?:the\s+)?better|"
    # GAN R2 R1: INDEX-TREND reads ("is NIFTY in an uptrend", "moving
    # averages on BANKNIFTY", "trend on SENSEX") need the SMA %-distance
    # stack + structured budget, not a terse blurb.
    r"(?:up|down)trend|moving\s+averages?|"
    r"(?:is|are)\s+\w+\s+(?:trending|in\s+an?\s+(?:up|down)\s*trend)|"
    r"trend\b[^.]{0,30}\b(?:nifty|banknifty|bank\s*nifty|finnifty|sensex|"
    r"market)\b|"
    r"\b(?:nifty|banknifty|bank\s*nifty|finnifty|sensex)\b[^.]{0,20}"
    r"\b(?:trend|trending|uptrend|downtrend)\b|"
    # GAN R2 R1/R8: SCREEN / RANK asks ("screen me cheap high-ROE banks",
    # "rank these stocks by P/B", "cheapest on PE") inherit the ranked
    # markdown table budget.
    r"screen\s+(?:me\s+)?(?:for\s+)?|"
    r"rank\s+(?:these|the|them|by|me)|"
    r"(?:cheapest|cheap|best|top|highest|lowest)\s+\w*\s*"
    r"(?:on|by|with|in)\s+(?:pe|p/e|pb|p/b|roe|roce|valuation|"
    r"dividend|yield)|"
    r"cheap\s+high[- ]?roe|high[- ]?roe\s+(?:and\s+)?cheap|"
    r"(?:list|show)\s+(?:me\s+)?(?:the\s+)?(?:cheapest|best|top)\s+"
    r"\w+\s+(?:banks?|stocks?|companies)"
    r")\b",
    re.IGNORECASE,
)

# GAN R2 R1/R8: distinguishes SCREEN/RANK asks from single-name analysis
# so the analysis budget can append a screen-specific ranked-table hint.
_SCREEN_INTENT_RE = re.compile(
    r"\bscreen\s+(?:me\s+)?(?:for\s+)?\b"
    r"|\brank\s+(?:these|the|them|by|me)\b"
    r"|\b(?:cheapest|cheap|best|top|highest|lowest)\s+\w*\s*"
    r"(?:on|by|with|in)\s+(?:pe|p/e|pb|p/b|roe|roce|valuation|dividend|yield)\b"
    r"|\bcheap\s+high[- ]?roe\b|\bhigh[- ]?roe\s+(?:and\s+)?cheap\b"
    r"|\b(?:list|show)\s+(?:me\s+)?(?:the\s+)?(?:cheapest|best|top)\s+"
    r"\w+\s+(?:banks?|stocks?|companies)\b",
    re.IGNORECASE,
)

# GAN R2 R1: index-TREND reads route to analysis with an SMA %-distance hint.
_TREND_INTENT_RE = re.compile(
    r"\b(?:up|down)trend\b|\bmoving\s+averages?\b"
    r"|\b(?:is|are)\s+\w+\s+(?:trending|in\s+an?\s+(?:up|down)\s*trend)\b"
    r"|\btrend(?:ing)?\b[^.]{0,30}\b(?:nifty|banknifty|bank\s*nifty|"
    r"finnifty|sensex|market)\b"
    r"|\b(?:nifty|banknifty|bank\s*nifty|finnifty|sensex)\b[^.]{0,20}"
    r"\b(?:trend|trending|uptrend|downtrend)\b",
    re.IGNORECASE,
)

_CAPABILITY_INTENT_RE = re.compile(
    r"^\s*(?:"
    r"what\s+(?:all\s+)?can\s+you\s+do|"
    r"what\s+do\s+you\s+do|"
    r"who\s+are\s+you|"
    r"^help\b|"
    r"how\s+(?:do\s+I|can\s+I)\s+use\s+(?:this|you|pivot)|"
    r"capabilit(?:y|ies)"
    r")\b",
    re.IGNORECASE,
)

_SMALLTALK_INTENT_RE = re.compile(
    r"^\s*(?:"
    r"hi|hello|hey|namaste|"
    r"good\s+(?:morning|afternoon|evening|night)|"
    r"thanks?(?:\s+a\s+lot)?|thank\s+you|cheers|"
    r"bye|good\s*bye|see\s+you|gn|gm"
    r")[\s!.\?]*$",
    re.IGNORECASE,
)


def _classify_reply_class(message: str, intent_kind: str) -> str:
    """Return one of {'draft', 'automation', 'backtest', 'explainer',
    'capability', 'small_talk', 'analysis', 'analytical_short'}.

    The first three mirror intent_kind (with 'agent' renamed to 'draft'
    for clarity at the reply-budget layer); the rest sub-classify the
    'other' bucket so each shape gets a fitting length + format budget.
    """
    if intent_kind == "agent":
        return "draft"
    if intent_kind == "automation":
        return "automation"
    if intent_kind == "backtest":
        return "backtest"
    msg = (message or "").strip()
    if not msg:
        return "small_talk"
    if _SMALLTALK_INTENT_RE.match(msg):
        return "small_talk"
    if _CAPABILITY_INTENT_RE.match(msg):
        return "capability"
    # ANALYSIS class must fire BEFORE explainer — "analyse HDFC" is analysis
    # (apply-the-data-and-reason), not an explain-concept ask.
    if _ANALYSIS_INTENT_RE.search(msg):
        return "analysis"
    if _EXPLAINER_INTENT_RE.search(msg):
        return "explainer"
    return "analytical_short"


# Per-reply-class budget: (max_output_tokens, system hint).
# Draft / automation / backtest stay at the legacy 1500-token cap and
# emit no extra hint — those paths are tool-driven and the model's text
# is summary-of-tool-result, where the existing rules already work.
# The other four classes carry an explicit length + format directive
# that overrides the system.md "≤120 words conversational" default.
_REPLY_BUDGETS: dict[str, tuple[int, str]] = {
    "draft": (1500, ""),
    "automation": (1500, ""),
    "backtest": (1500, ""),
    "explainer": (2400, (
        "REPLY-CLASS: EXPLAINER. Aim for 250-500 words. Use `## Section` "
        "headings or bulleted highlights when the answer has multiple "
        "facets (segments, drivers, risks, comparisons). Depth and "
        "structure matter for this class — do NOT pad short. Do NOT "
        "append the current live price or LTP unless the user "
        "explicitly asked for a price; the portfolio block is for your "
        "awareness, not for recitation."
    )),
    "capability": (600, (
        "REPLY-CLASS: CAPABILITY. Reply in ≤120 words, plain prose, no "
        "headings. List the 3-5 most useful capabilities the user can "
        "act on right now (build an agent, place an order, view "
        "portfolio, run a backtest, ask for analysis)."
    )),
    "small_talk": (300, (
        "REPLY-CLASS: SMALL-TALK. Reply in 1-2 short sentences. No "
        "headings, no bullets, no live-price recital."
    )),
    # ANALYSIS — structured, reasoned stock/comparison analysis. The
    # model MUST do the analytical work (not just restate numbers) and
    # produce a defended view with what-would-change-my-mind.
    "analysis": (2400, (
        "REPLY-CLASS: ANALYSIS. Aim for 250-450 words, well-structured. "
        "Use `## Section` headings: Snapshot / Technicals / Fundamentals / "
        "News / What to watch / View. "
        "DO THE ANALYTICAL WORK — do not just restate numbers. Interpret "
        "the SMA stack (trend), RSI (momentum), PE vs history or sector, "
        "recent news impact, and synthesize a DEFENDED VIEW: bull case vs "
        "bear case, what would change your mind. Be honest about missing "
        "data (say 'PE unavailable' not silence). For comparisons, pick "
        "a winner with risk-adjusted reasoning and use a markdown table "
        "if 3+ metrics. End with standard disclaimer when actionable."
    )),
    "analytical_short": (1500, (
        "REPLY-CLASS: SHORT-ANALYTICAL. Reply in ≤120 words of plain "
        "prose. No `##` headings. Do NOT append unsolicited live "
        "prices — recite them only if the user asked."
    )),
}


# ── Independent-vs-dependent prompt detector ────────────────────────
#
# Drives whether to inject the cached active workflow draft into the
# follow-up hint. Without this gate the model treated EVERY follow-up
# turn as an amendment, mutating last hour's IREDA buy draft into a
# RELIANCE sell when the user said "sell it" while looking at an
# Eternal widget — and worse, suffixing a stale "Sell HDFCBANK at 10%
# profit" card under a "pros and cons of Reliance" answer.
#
# The decision is structural — we look at the SHAPE of the message:
#
#   DEPENDENT (keep active_draft, treat as amendment):
#     - Short imperatives that reference no new entity: "yes", "ok do
#       it", "no 5", "make it 3 instead", "weekday only".
#     - Pronoun-anchored: "change the qty", "set the trigger to 9:30",
#       "swap RELIANCE with INFY".
#     - Explicit amend verbs: "instead", "rename", "remove the email",
#       "add an SL".
#
#   INDEPENDENT (drop active_draft, fresh ask):
#     - New ticker query: "tell me about XYZ", "RELIANCE", "Eternal".
#     - Conceptual question: "pros and cons", "what's a SIP", "explain".
#     - New top-level intent verb that doesn't reference the draft:
#       "exit my positions", "show portfolio", "am I overexposed", "run
#       a backtest", "start a 2000 monthly sip".
#
# When in doubt: independent. The cost of a false-independent is one
# extra LLM hop where the model rebuilds the draft from chat history;
# the cost of a false-dependent is the user gets a workflow-card under
# their unrelated answer (the most-reported failure shape).

# Verbs / phrasings that indicate a fresh top-level intent. If ANY
# match, we drop the active draft. Each entry is documented inline so
# adding a new one is obvious.
_INDEPENDENT_INTENT_RE = re.compile(
    # Conceptual / informational asks
    r"\bpros?\s+and\s+cons?\b"
    r"|\bwhat\s+(?:is|are|does|do|can)\b"
    r"|\bhow\s+(?:does|do|is|are|much|many)\b"
    r"|\bwhy\s+(?:is|does|do|are|should)\b"
    r"|\bdefine\b|\bexplain\b|\bcompare\b|\boverview\b"
    # Portfolio / exposure introspection. The "over" / "under" branch
    # also matches their compound forms ("overexposed", "underweight",
    # "overweight") — \w* allows the suffix without breaking the leading
    # \b anchor.
    r"|\b(?:am\s+i|are\s+we)\b[^\.]{0,40}\b(?:over\w*|under\w*|too)\b"
    r"|\bover[- ]?expos(?:ed|ure)\b|\bexpos(?:ed|ure)\b"
    r"|\bshow\s+(?:my|me)\s+(?:portfolio|holdings|positions|p&?l)\b"
    # Square-off / exit at the top level (not amendment-style)
    r"|\bexit\s+(?:all\s+)?(?:my\s+)?positions?\b"
    r"|\bsquare\s*off\b"
    # Backtest top-level
    r"|\b(?:run|do|start)\s+(?:a\s+)?backtest\b"
    # SIP top-level — accept "start a 2000 monthly sip" with the
    # amount inline. Earlier rule required no number between "a" and
    # the cadence word and missed the most-typed shape.
    r"|\bstart\s+a\s+(?:[\d,]+\s+)?(?:monthly|weekly|daily)\b"
    r"|\bstart\s+a\s+sip\b"
    # Bare data lookups: "tell me about X", "X price", "what's X at"
    r"|\btell\s+me\s+(?:more\s+)?about\b"
    r"|\bsnapshot\s+of\b|\bquote\s+for\b|\bprice\s+of\b"
    # Price-asking variations: "what's the price", "what is X at",
    # "current price", "live price". Common drift patterns after a
    # draft that previously kept re-emitting the workflow.
    r"|\bwhat'?s?\s+(?:the\s+)?(?:current|live)?\s*price\b"
    r"|\bwhat'?s?\s+(?:it|\w+)\s+(?:trading\s+)?at\b"
    r"|\bcurrent\s+(?:live\s+)?price\b|\blive\s+price\b"
    # Price-history / chart-data fetches. Without these, "show me
    # last week's price" / "chart of X" / "what was the price on
    # Friday" after a draft were treated as amendments and the
    # model spawned new workflows. Treat as fresh data lookup.
    r"|\b(?:show\s+(?:me|us)?\s+)?(?:last|past|prior|previous)\s+"
    r"(?:week|month|quarter|year|day|\d+\s+(?:days?|weeks?|months?))"
    r"(?:'s)?\s+(?:price|close|high|low|open|chart|data)?\b"
    r"|\bshow\s+(?:me|us)?\s+(?:the\s+)?(?:chart|price[- ]?history|"
    r"history|graph|candles?)\b"
    r"|\bprice\s+history\b|\bchart\s+(?:of|for)\b|\bcandlestick\b"
    # Help / capabilities
    r"|\bwhat\s+can\s+you\s+do\b"
    # Indicator / metric data lookups — "RSI of X", "MACD on Y",
    # "Sharpe of Z", "drawdown for W". These are READ queries the
    # user is asking about an instrument, NOT amendments to the
    # draft on screen. Without this branch the model treated
    # "The RSI of Reliance" after a draft as "amend the draft to
    # trigger on RELIANCE RSI" — re-emitted the workflow with an
    # RSI rule the user never asked for. Treating as fresh intent
    # evicts the draft and routes to the analytics tool.
    #
    # Two phrase shapes covered:
    #   - "<metric> of/on/for <ticker>"   ("RSI of Reliance")
    #   - "<ticker>'s <metric>"            ("NIFTY's RSI")
    #   - "<ticker> <metric>"              ("TCS Sharpe")
    r"|\b(?:rsi|macd|sma|ema|adx|atr|cci|mfi|stoch(?:astic)?|"
    r"bollinger|supertrend|williams|aroon|ichimoku|"
    r"sharpe|sortino|drawdown|volatility|var|beta)\s+(?:of|on|for)\b"
    r"|\b\w+'s\s+(?:rsi|macd|sma|ema|adx|atr|cci|mfi|"
    r"sharpe|sortino|drawdown|volatility|beta)\b"
    r"|\b(?:overbought|oversold)\b"
    # Vague continuation prompts. WHY these are independent: a user
    # typing "what else" / "anything else" / "what now" after seeing
    # an order or workflow card is asking the bot to surface options,
    # NOT to amend the active draft. Without this branch the model
    # interpreted "what else" as an amendment cue, called
    # propose_workflow with placeholder values, validation rejected
    # it, and the canned "step shape isn't in Pivot v1's catalog"
    # message fired. Treating these as fresh intent evicts the draft
    # and lets the fast-path (or the model) handle them as a
    # conversational ask.
    r"|\bwhat\s+else\b|\banything\s+else\b"
    r"|\bwhat\s+(?:now|next)\b|\bnow\s+what\b"
    r"|^\s*(?:and\s+now|next)\??\s*$"
    # Fresh agent-build / workflow-build top-level intents. WHY: when
    # the user types "make me an agent that buys X at open and sells
    # at close…" while a stale draft for a DIFFERENT symbol is sitting
    # in active_draft from a prior turn, the amendment path was being
    # taken — so the model re-emitted the old draft instead of
    # building the new one. These phrases are unambiguously fresh
    # top-level intents; they should always evict the prior draft.
    r"|\b(?:build|make|create|set\s*up|design|spin\s+up)\s+"
    r"(?:me\s+)?(?:an?|some)\s+(?:agent|workflow|automation|strategy|rule|bot|sip)\b"
    r"|\bmake\s+(?:an?|some)\s+(?:agent|workflow|automation)\s+that\b"
    r"|\b(?:agent|workflow|automation)\s+that\s+(?:buys?|sells?|alerts?|notifies)\b",
    re.IGNORECASE,
)

# Verbs / phrasings that explicitly indicate the user IS amending the
# active draft. When ANY match, we KEEP active_draft even if an
# independent cue also matched (amend wins ties — explicit > inferred).
# Pure affirmatives — user is acknowledging the draft on screen, NOT
# proposing a change. Distinguished from amendments ("make it 5",
# "no 3 instead") which DO need re-emit. WHY this matters: the
# previous code lumped these into _DEPENDENT_INTENT_RE, which then
# forced tool_choice="required" on the next hop — re-emitting the
# same draft with identical args. Wasted token cost AND wasted
# latency for zero behavioural change. Treat pure affirmatives as a
# no-op → return a one-line acknowledgement, skip the LLM.
_PURE_AFFIRMATIVE_RE = re.compile(
    r"^\s*"
    # Optional LEADING ACKNOWLEDGEMENT CLAUSE + separator before the
    # action verb. "looks good, go ahead and register it" / "perfect —
    # activate it" / "sounds good, lock it in" used to fall through
    # because each ack phrase was anchored as a COMPLETE alternative;
    # a stacked compound (ack + comma + action) matched neither. This
    # prefix consumes the ack clause so the compound resolves to the
    # action-confirm branch.
    r"(?:(?:looks\s+good|sounds\s+good|got\s+it|perfect|great|cool|nice|"
    r"ok(?:ay)?|yes|yeah|yep|yup|sure|fine|alright)\s*[,;:.\-—]?\s+)?"
    r"(?:"
    # Bare affirmatives
    r"yes|y|yeah|yep|yup|"
    r"ok(?:ay)?|sure|fine|alright|"
    r"got\s+it|sounds\s+good|looks\s+good|"
    r"perfect|great|cool|nice|"
    r"please|ty|thanks?|"
    # Action-confirm phrases with optional fillers / pronouns. These
    # ALL acknowledge an existing draft without proposing a change:
    #   "do it", "go ahead", "let's go", "activate", "activate it",
    #   "activate that", "proceed with it", "run it", "run that",
    #   "make it so", "go for it", "yes activate", "ok do it",
    #   "save and activate", "proceed with it", "save it now".
    r"(?:yes\s+|ok\s+|okay\s+|sure\s+|alright\s+|fine\s+|now\s+)?"
    r"(?:do|go(?:\s+ahead)?|proceed|let'?s\s+go|"
    r"activate|confirm|run|launch|fire|"
    # "register it" / "set it up" / "lock it in" / "schedule it" /
    # "enable it" — these acknowledge an existing draft just like
    # "activate it"; chat can't flip it live, so the handler points the
    # user at the card's Save & activate button instead of re-building
    # (the prior bug: "register it" / "set it up exactly like that"
    # re-invoked propose_* and errored to nothing).
    r"register(?:\s+it|\s+that|\s+this)?|"
    r"set\s+(?:it\s+|this\s+|that\s+)?up|lock\s+(?:it\s+|this\s+)?in|"
    r"enable|schedule\s+it|turn\s+it\s+on|"
    r"make\s+it\s+so|go\s+for\s+it|save)"
    # Optional connector + verb (handles "save and activate",
    # "go ahead and proceed") and/or pronoun fillers.
    r"(?:\s+(?:and|then)\s+"
    r"(?:do|go|proceed|activate|confirm|run|launch|save|register))?"
    r"(?:\s+(?:with|on)\s+(?:it|that|this))?"
    # Trailing fillers incl. "exactly like that" / "just like this".
    r"(?:\s+(?:exactly|just|simply|like|it|that|this|now|please|ahead))*"
    r")\s*[.!?]?\s*$",
    re.IGNORECASE,
)


def _is_pure_affirmative(message: str) -> bool:
    """True for bare 'ok' / 'yes' / 'sure' / 'do it' that ACKNOWLEDGES
    a draft without proposing any change. The active draft on screen
    is already what the user wants — re-emitting it is waste.
    """
    return bool(_PURE_AFFIRMATIVE_RE.match(message or ""))


_DEPENDENT_INTENT_RE = re.compile(
    # Explicit amendment verbs
    r"\b(?:instead|rather|change|modify|update|edit|tweak|adjust"
    r"|rename|swap|replace|remove|drop|add|append|insert"
    # WHY "switch" / "convert" added: "switch to limit at 1450" /
    # "convert to a SIP" are amendment phrasings the prior regex
    # missed — the model then produced prose instead of re-emitting
    # the draft as a different order type.
    r"|switch|convert|turn\s+(?:it|that|this)"
    # WHY numeric-tweak verbs added (Phase 1 round-3 finding):
    # "Lower the ADX threshold to 20", "Raise it to 25", "Increase
    # period to 50", "Reduce SL to 3%" all fell through the original
    # regex even though they're clearly draft amendments. Each of
    # these verbs followed by a numeric tail strongly implies "edit
    # the active draft's number".
    r"|lower|raise|increase|decrease|reduce|bump|shift"
    # "try with 20/50", "try it with weekly", "use 5y instead"
    r"|try|use)\b"
    # Pronoun reference to the draft
    r"|\b(?:make\s+it|set\s+it|set\s+the|change\s+the|update\s+the)\b"
    # "the trigger / the action / the SL / the qty" — refers to a draft slot
    r"|\bthe\s+(?:trigger|action|condition|step|sl|stop[- ]?loss|"
    r"quantity|qty|symbol|schedule|notification|email)\b"
    # Common short amendment shapes that DO carry a change
    # ("no 5" = "no, make it 5"). Pure affirmatives (yes/ok/sure/etc.)
    # are handled separately via _PURE_AFFIRMATIVE_RE — keeping them
    # OUT of this regex stops tool_choice="required" from forcing a
    # wasted re-emit of the same draft.
    r"|^\s*no\s+\d+\s*[.!?]?\s*$"
    # Stepwise-emission patterns (T17/T18 fix). After accumulating
    # symbol → trigger → action across turns, the user's final piece
    # ("for 5 shares" / "valid for 30 days" / "at ₹420") used to fall
    # through both regexes and the model produced prose with NO
    # tool emit. These patterns mark the closing field as an
    # amendment so tool_choice="required" forces the macro to emit.
    r"|\bfor\s+\d+\s+(?:shares?|units?|lots?)\b"
    r"|^\s*\d+\s+(?:shares?|units?|lots?|qty)\s*[.!?]?\s*$"
    r"|\bvalid\s+(?:for|until|till|through)\b"
    r"|\bexpir(?:e|es|ing)\s+(?:in|on|after|by)\b"
    # [C1] expiry NOUN amendment shapes — "set an expiry for next 30
    # days", "add the expiration", "30-day expiry", "expiry of 30 days".
    # Scoped to set/add/give/put/apply + expiry (or N-day expiry / expiry
    # of|in|date) so it stays an amendment cue and never matches a fresh
    # "build an agent … with a 30-day expiry" top-level intent.
    r"|\b(?:set|add|give|put|apply)\b[^.]{0,20}\bexpir(?:y|ation)\b"
    r"|\bexpir(?:y|ation)\s+(?:of|in|for|date)\b"
    r"|\b\d+[- ]?day\s+expir(?:y|ation)\b"
    r"|\bgood\s+(?:for|till|until)\s+\d"
    r"|\b(?:until|till)\s+(?:end\s+of|next|this)\b"
    r"|\bat\s+[₹$]?\s*\d[\d,]*(?:\.\d+)?\s*[.!?]?\s*$"
    r"|^\s*₹\s*\d[\d,]*(?:\.\d+)?\s*[.!?]?\s*$"
    # GAN R2 R7: Hinglish amendment / resize cues. "nahi 12000 ka
    # kharido" / "12000 ka buy karo" / "bech do" were not caught, so the
    # rupee-notional resize never forced an amendment and the card stayed
    # at the old quantity. The "<NNNN> ka/ki/ke" cue is the canonical
    # rupee-notional resize ("12000 ka kharido" = "buy ₹12,000 worth").
    r"|\bnahi\b|\bnhi\b"
    r"|\bkharid(?:o|lo|na|ke)?\b|\bbech(?:\s*do|na|o)?\b"
    r"|\b\d[\d,]*\s*(?:ka|ki|ke)\b"
    r"|\b(?:karo|kardo|kar\s+do)\b",
    re.IGNORECASE,
)


# GAN R2 R7: rupee-notional resize detector. "12000 ka kharido" /
# "make it ₹12,000 worth" / "buy 12000 rupees of it" — the model must
# compute shares = round(amount / live_price) and re-emit the draft,
# never narrate "Updated" while leaving the quantity unchanged.
_RUPEE_NOTIONAL_RE = re.compile(
    r"\b\d[\d,]*\s*(?:ka|ki|ke)\b"                       # "12000 ka"
    r"|[₹$]\s*\d[\d,]*\s*(?:worth|of|ka)?"               # "₹12,000 worth"
    r"|\b\d[\d,]*\s*(?:rupees?|rs\.?|inr)\s*(?:worth|of)" # "12000 rupees of"
    r"|\bworth\s+[₹$]?\s*\d[\d,]*"                        # "worth 12000"
    r"|\bmake\s+it\s+[₹$]?\s*\d[\d,]*\s*(?:worth|rupees?|rs|inr)",
    re.IGNORECASE,
)


def _is_rupee_notional_resize(message: str) -> bool:
    """True when the message resizes a draft by a RUPEE notional (Hinglish
    or English) rather than a share count — requires a notional→shares
    conversion the model must compute, not punt."""
    return bool(_RUPEE_NOTIONAL_RE.search(message or ""))


def _is_independent_prompt(message: str) -> bool:
    """True when the user's message is a fresh top-level intent rather
    than an amendment to the active draft. Used to decide whether to
    drop the cached workflow draft from the follow-up hint.

    Returns False for empty / whitespace input — no signal, fall
    through to default behaviour (keep draft, model decides).
    """
    msg = (message or "").strip()
    if not msg:
        return False
    # Explicit "build another / also build" phrasing trumps any
    # amendment-shape match. Otherwise "Now also build a sell agent
    # for TCS at 4200" gets caught by the stepwise "at <number>"
    # pattern and treated as an amendment to the prior draft.
    if re.search(
        r"\b(?:also\s+build|another\s+(?:agent|workflow|automation)|"
        r"now\s+also\s+(?:build|set|make|create)|"
        r"build\s+(?:me\s+)?another|"
        r"new\s+(?:agent|workflow|automation)|"
        r"different\s+(?:agent|workflow|automation))\b",
        msg, re.IGNORECASE,
    ):
        return True
    # Explicit amend wins (after the multi-build override).
    if _DEPENDENT_INTENT_RE.search(msg):
        return False
    if _INDEPENDENT_INTENT_RE.search(msg):
        return True
    # Bare ticker (e.g. "RELIANCE", "ETERNAL", "Reliance") is a fresh
    # data-lookup intent — drop the draft. Length-bounded so it doesn't
    # match short workflow descriptions.
    if re.fullmatch(r"\$?[A-Za-z][A-Za-z0-9\-_]{1,15}\??", msg):
        return True
    return False


def _analysis_subhint(message: str) -> str:
    """GAN R2 R1/R8: extra structure directive appended to the ANALYSIS
    reply-class hint based on the SHAPE of the analytical ask (screen /
    rank vs index-trend vs single-name). Returns "" when no extra
    shaping is needed (plain single-name analysis already covered)."""
    if _SCREEN_INTENT_RE.search(message):
        return (
            " THIS IS A SCREEN / RANK ask — output is INVALID without a "
            "markdown TABLE. Render `Rank | Name | <primary> | <secondary> "
            "| <tertiary> | Flag` with one row per name. STATE the sort key "
            "in the lead sentence. For a BANK screen, RANK and column-order "
            "on P/B then ROE (render `Rank | Name | P/B | ROE | P/E | Flag`), "
            "never lead with P/E. Add a 'Cheap+Quality' flag column (✓ when "
            "the name is both below the group-median valuation AND above "
            "group-median ROE) and close with ONE defended single pick and "
            "why."
        )
    if _TREND_INTENT_RE.search(message):
        return (
            " THIS IS an INDEX / TREND read. Do NOT print raw SMA levels "
            "alone — for EACH moving average show the %-DISTANCE of price "
            "from it (e.g. 'price 2.1% above the 50-DMA') and read the SMA "
            "STACK (20>50>200 = uptrend). State the trend verdict in the "
            "first sentence with a number, then back it with the stack, "
            "RSI/momentum, and recent range. Use a small `Period | Level | "
            "Price vs MA` table for the 20/50/200-DMA."
        )
    return ""


def _build_deterministic_guards(message: str, history: list) -> list[str]:
    """GAN R2 R2–R6: deterministic directive blocks that suppress the
    over-eager ASK_USER escape hatch / 09:30 downgrade and force the
    documented canonical behaviour. Prose in system.md alone proved
    insufficient — these fire as additional hard system messages and the
    caller pairs them with scope-narrowing / tool_choice in the routing
    layer. Returns a list of directive strings (possibly empty)."""
    guards: list[str] = []

    # R6 — confusion AFTER an ASK_USER menu → TEACH, never re-dump.
    if _is_confusion_after_menu(message, history):
        guards.append(
            "## Confusion after a clarification menu — TEACH, do NOT "
            "re-ask\n"
            "The user is confused by the MENU you just offered. You MUST "
            "NOT re-emit the same ASK_USER menu and you MUST NOT call "
            "ASK_USER at all this turn. Reply in PLAIN PROSE that: (1) "
            "states honestly that NOTHING is set up yet (no agent/order "
            "exists — you only offered options), (2) explains ONE sensible "
            "option in one or two sentences with a concrete example (e.g. "
            "'RSI(14)<30 means the stock has fallen hard and may be "
            "oversold'), and (3) ends with ONE simple yes/no the user can "
            "answer ('Want to start with that?'). Never imply you already "
            "picked or built something."
        )

    # R5 — unsupported automation rail: boundary FIRST, then alternative.
    rail = _names_unsupported_rail(message)
    if rail is not None:
        if rail == "iv_rank":
            guards.append(
                "## Unsupported rail: IV rank / IV percentile\n"
                "Pivot does NOT yet have IV-rank / IV-percentile (needs "
                "option-chain IV history). Do NOT ask for any field that "
                "presupposes it (quantity, strike, threshold). FIRST state "
                "that boundary in one sentence, THEN offer the nearest real "
                "thing — an alert on ABSOLUTE IV level or on PCR — and ask "
                "which the user wants. Never affirm an IV-rank trigger as if "
                "buildable."
            )
        elif rail == "macro_feed":
            guards.append(
                "## Unsupported rail: macro / UPI / GDP feed trigger\n"
                "Pivot does NOT ingest UPI / GDP / inflation feeds as a "
                "trigger rail. Do NOT ask for any field that presupposes it. "
                "FIRST state that boundary plainly, THEN offer the nearest "
                "real trigger (a price/indicator level, a scheduled time, or "
                "a keyword-headline event) and ask which to use. Never affirm "
                "a macro-feed trigger as buildable."
            )
        else:  # sentiment
            guards.append(
                "## Unsupported rail: news / social SENTIMENT polarity\n"
                "Pivot does NOT run sentiment NLP — there is no 'when "
                "sentiment turns negative/positive' trigger. This is an "
                "auto-execute-shaped ask, so honesty is critical: do NOT ask "
                "'how many shares' or any field that AFFIRMS the fabricated "
                "capability. Your reply MUST, IN THIS ORDER: (1) state the "
                "boundary ('Pivot doesn't run news-sentiment analysis'); (2) "
                "name the nearest REAL thing — a keyword-HEADLINE trigger: 'I "
                "can watch <SYMBOL> headlines for terms you choose (SEBI, "
                "probe, downgrade, fraud…) and register a sell you confirm'; "
                "(3) only THEN ask the concrete fields (which keywords, how "
                "many shares). Never order quantity before stating the "
                "boundary and the alternative."
            )

    # R4 — named multi-leg option TEMPLATE build → build, never clarify.
    if _is_named_option_build(message):
        guards.append(
            "## Named option strategy build — BUILD, do NOT ASK_USER\n"
            "The user named a known multi-leg option template (iron condor / "
            "straddle / strangle / spread / butterfly / collar) WITH an "
            "underlying. This is a CANONICAL buildable. Call "
            "`build_option_strategy(underlying=<the index/ticker>, "
            "template=<the named template>, expiry=<nearest monthly unless "
            "the user named weekly/an expiry>)` IMMEDIATELY. Vague modifiers "
            "('around current levels', 'reasonable width', 'this week') are "
            "NOT missing inputs — the engine fills delta/ATM defaults "
            "(0.20Δ shorts, 0.10Δ wings, 1 lot). You MUST NOT call ASK_USER "
            "for a center strike, wing width, or quantity. After the card, "
            "say one line: the legs picked + 'say widen / next expiry to "
            "change' + credit/max-profit/max-loss/breakevens from the card."
        )

    # R3 — fully-specified notify-only alert → notify_only DSL, no ASK_USER.
    if _is_notify_only_alert(message):
        guards.append(
            "## Notify-only alert — register it, do NOT ASK_USER\n"
            "The user wants a price ALERT with an explicit 'no order' / "
            "'just alert' marker and a price level. Call "
            "`propose_dsl_workflow(action_kind='notify_only', "
            "primary_symbol=<symbol>, condition='price crosses "
            "above/below <level>')` IMMEDIATELY. Do NOT ask quantity. Do "
            "NOT ask whether the alert is in-app — IN-APP IS THE ONLY "
            "CHANNEL, so there is nothing to clarify; just disclose it in "
            "the read-back. NEVER call ASK_USER for this turn. Read-back: "
            "'Watching <SYMBOL> — I'll alert you the moment it crosses "
            "<above/below> ₹<level>. No order is placed (in-app alert).'"
        )

    # R2 — buy/sell at open|close → market_relative_time, never 09:30.
    if _is_at_open_close_build(message):
        guards.append(
            "## At-open / at-close order — two-branch card, NEVER 09:30\n"
            "The user wants an action at the market OPEN or CLOSE. This is "
            "fully supported via `trigger.market_relative_time(anchor='open'"
            "|'close', offset_minutes=0)`. Call `propose_dsl_workflow` (or "
            "`propose_workflow`) and build the card. It is a HARD ERROR to: "
            "(a) offer a '09:30 cron' / 'every morning at 09:30 I check the "
            "price' downgrade — that is capability theatre and is BANNED; "
            "(b) call ASK_USER — all required params are present. For 'buy N "
            "at open, book +X% profit' build TWO branches: ENTRY "
            "market_relative_time(anchor='open') → buy N; EXIT "
            "unrealised_pct>=X/100 → sell. Preserve the exact quantity given."
        )

    return guards


@dataclass
class ChatTurn:
    response: str
    tools_called: list[str] = field(default_factory=list)
    logiccard: dict | None = None
    latency_ms: int = 0
    sanitised: bool = False
    raw_data: dict = field(default_factory=dict)
    latency_breakdown: dict[str, int] = field(default_factory=dict)


@dataclass
class UserContext:
    user_id: int
    kite_token: str
    db: Any
    holdings: list[dict] = field(default_factory=list)


# ── ToolDef adapter ─────────────────────────────────────────────────


def _registry_tools_as_tooldefs(
    selected_names: Optional[set[str]] = None,
) -> list[ToolDef]:
    """Translate `agents/tools.py` ALL_TOOLS dicts → LLMClient ToolDefs.

    When `selected_names` is provided (from `tool_router.select_tool_names`),
    only matching tools are returned. The synthetic ASK_USER tool is always
    appended so the model has the clarification escape hatch regardless
    of routing.
    """
    raw = filter_registry_tools(get_tool_schema(), selected_names)
    out: list[ToolDef] = []
    for defn in raw:
        fn = defn.get("function") or {}
        out.append(ToolDef(
            name=fn.get("name", ""),
            description=fn.get("description", ""),
            parameters=fn.get("parameters") or {},
        ))
    out.append(ask_user_tool_def())
    return out


def _build_user_context(ctx: "UserContext") -> Optional[PromptUserContext]:
    """Assemble a compact prompt-ready context block from the chat
    UserContext.

    Pulls (with a target of < 50 ms p95 over a warm Postgres connection):
      - `full_name` from one `users` row.
      - Portfolio total, holdings count, top-5 holdings: in-memory over
        `ctx.holdings` (already pre-loaded by the chat router).
      - Active workflows: ONE query with `joinedload(Workflow.steps)`
        so step-0 type is read inline (no N+1). Capped at 10 rows.
        Replaces (does not augment) the prior `.count()` query.
      - `kite_connected`: derived from `ctx.kite_token` — no I/O.
      - Top-3 watchlist symbols: ONE query if any rows exist.

    Skipped on purpose:
      - `cash_buffer_inr`: today's only source is a live Kite
        `get_margins()` round-trip, which would blow the latency
        budget. Buying power stays available via `fetch.portfolio` /
        `get_portfolio_summary` when the user actually asks.

    Returns None when nothing useful populates — the prompt assembler
    skips rendering an empty block.
    """
    # ── full_name (1 row) ───────────────────────────────────────────
    full_name: Optional[str] = None
    try:
        from backend.models import User
        user_row = ctx.db.query(User.full_name).filter(
            User.id == ctx.user_id,
        ).first()
        if user_row is not None:
            # SQLAlchemy returns a Row; .full_name resolves through it.
            full_name = (user_row[0] or None) if user_row[0] else None
    except Exception:
        full_name = None

    # ── Portfolio totals + top holdings (in-memory) ────────────────
    portfolio_total: Optional[float] = None
    holdings_count: Optional[int] = None
    top_holdings: Optional[list[dict[str, Any]]] = None
    if ctx.holdings:
        try:
            portfolio_total = sum(
                float(h.get("last_price", 0) or 0) * float(h.get("quantity", 0) or 0)
                for h in ctx.holdings
            ) or None
        except (TypeError, ValueError):
            portfolio_total = None
        holdings_count = len(ctx.holdings) or None

        # Build top-5 by current INR value. Re-uses the already-loaded
        # `ctx.holdings` list — no extra I/O.
        try:
            scored: list[tuple[float, dict[str, Any]]] = []
            for h in ctx.holdings:
                qty = float(h.get("quantity", 0) or 0)
                lp = float(h.get("last_price", 0) or 0)
                val = qty * lp
                row = {
                    "symbol": h.get("tradingsymbol") or h.get("symbol"),
                    "qty": int(qty) if qty.is_integer() else qty,
                    "last_price": lp,
                    "value_inr": val,
                }
                day_pct = h.get("day_change_percentage")
                if isinstance(day_pct, (int, float)):
                    row["day_pct"] = float(day_pct)
                scored.append((val, row))
            scored.sort(key=lambda t: t[0], reverse=True)
            top_holdings = [r for _v, r in scored[:5]] or None
        except (TypeError, ValueError):
            top_holdings = None

    # ── Active workflows: ONE eager-loaded query (caps at 10) ──────
    active_workflows: Optional[list[dict[str, Any]]] = None
    active_workflows_count: Optional[int] = None
    try:
        # Lazy import — avoids a circular at module load.
        from sqlalchemy.orm import joinedload
        from backend.models import Workflow, WorkflowStatus
        wf_rows = (
            ctx.db.query(Workflow)
            .options(joinedload(Workflow.steps))
            .filter(
                Workflow.user_id == ctx.user_id,
                Workflow.status == WorkflowStatus.active,
            )
            .order_by(Workflow.next_run_at.asc().nullslast())
            .limit(10)
            .all()
        )
        if wf_rows:
            out: list[dict[str, Any]] = []
            for wf in wf_rows:
                step0_type: Optional[str] = None
                # `steps` is order_by step_index in the relationship; index 0
                # is the trigger.* (validator-enforced at activate time).
                if wf.steps:
                    step0_type = getattr(wf.steps[0], "step_type", None)
                out.append({
                    "id": wf.id,
                    "name": wf.name,
                    "last_run_at": (
                        wf.last_run_at.isoformat() if wf.last_run_at else None
                    ),
                    "next_run_at": (
                        wf.next_run_at.isoformat() if wf.next_run_at else None
                    ),
                    "step0_type": step0_type,
                })
            active_workflows = out
            active_workflows_count = len(out)
        else:
            active_workflows_count = 0
    except Exception:
        # If the workflows table or model is unavailable for any
        # reason, the chat shouldn't 500. Quiet degrade.
        active_workflows = None
        active_workflows_count = None

    # ── Kite session presence (no I/O) ─────────────────────────────
    # `'mock_token'` is the placeholder the router substitutes when no
    # real Kite session exists. Surfacing the distinction lets the
    # model steer away from broker-write tools for unconnected users.
    kite_connected: Optional[bool] = None
    if ctx.kite_token is not None:
        kite_connected = bool(ctx.kite_token) and ctx.kite_token != "mock_token"

    # ── Watchlist top-3 (one query) ────────────────────────────────
    watchlist_symbols: Optional[list[str]] = None
    try:
        from backend.models import WatchlistItem
        wl_rows = (
            ctx.db.query(WatchlistItem.symbol)
            .filter(WatchlistItem.user_id == ctx.user_id)
            .order_by(WatchlistItem.added_at.desc())
            .limit(3)
            .all()
        )
        if wl_rows:
            watchlist_symbols = [r[0] for r in wl_rows if r and r[0]]
    except Exception:
        watchlist_symbols = None

    # ── Bail if nothing useful populated ───────────────────────────
    if (
        full_name is None
        and portfolio_total is None
        and holdings_count is None
        and top_holdings is None
        and not active_workflows
        and not active_workflows_count
        and kite_connected is None
        and not watchlist_symbols
    ):
        return None

    return PromptUserContext(
        user_id=ctx.user_id,
        full_name=full_name,
        portfolio_total_inr=portfolio_total,
        holdings_count=holdings_count,
        active_workflows_count=active_workflows_count,
        top_holdings=top_holdings,
        active_workflows=active_workflows,
        kite_connected=kite_connected,
        cash_buffer_inr=None,  # see docstring — skipped on purpose.
        watchlist_symbols=watchlist_symbols,
    )


def _format_mode_pin(mode: Optional[str]) -> str:
    """Render the user's mode-pill choice as a hard system directive.

    The FE composer has three pills (Automation / Agent / Backtest);
    the active one is sent as `mode` on the chat request. When set,
    this directive overrides the keyword classifier AND tells the
    model which tool family to choose. Returns "" when no mode is
    pinned (the classifier decides).

    The strings are intentionally blunt — without them, the model
    occasionally still drafts a workflow on an Automation pill turn
    because a prior workflow draft was sitting in history. Pin the
    user's intent at the system level so it dominates.
    """
    if mode == "automation":
        return (
            "## Active mode: AUTOMATION\n"
            "The user clicked the AUTOMATION pill in the composer. "
            "They want a SINGLE deterministic action, executed now or "
            "as a one-off scheduled order. Call exactly ONE of the "
            "immediate-order tools (`place_market_order`, "
            "`place_limit_order`, `create_gtt_order`, `create_sl_order`, "
            "`create_oco_order`, `create_dip_buy`, `create_sip`, "
            "`squareoff_all_intraday`, `squareoff_symbol`, "
            "`place_basket_order`).\n"
            "Do NOT call `propose_workflow`. Do NOT amend a prior "
            "workflow draft. If a workflow draft was on screen from a "
            "previous turn, IGNORE it — the user has switched contexts. "
            "If the user's request is ambiguous (e.g. no quantity), "
            "call ASK_USER with one focused question."
        )
    if mode == "agent":
        # WHY this is broader than "call propose_workflow" only: the
        # user-stated rule is "first try the agent shape, fall back if
        # it really doesn't fit". Hard-pinning to propose_workflow
        # caused two known failures: (a) sector baskets ("basket of
        # steel stocks") got generic workflows instead of the
        # propose_basket_allocation macro, producing a worse FE card;
        # (b) holding-action shapes ("set 2% SL on my INFY") were
        # forced into propose_workflow when propose_holding_action
        # is the dedicated macro. The four macro tools below are all
        # AGENT-shaped — they emit workflow drafts with proper trigger
        # /fetch/action structure — so allowing them keeps the
        # multi-step intent without losing the right card type. We
        # still forbid plain single-shot order tools (place_market_order
        # etc.) because those would collapse a workflow ask into a
        # one-off order. ASK_USER stays available for genuine
        # ambiguity.
        return (
            "## Active mode: AGENT\n"
            "The user clicked the AGENT pill in the composer. They "
            "want a multi-step automated workflow (trigger + optional "
            "fetch/condition + action(s) + notify). Use one of: "
            "`propose_workflow` (general), `propose_basket_allocation` "
            "(sector basket), `propose_holding_action` (sell/SL on an "
            "existing holding), `propose_threshold_order` (price or "
            "indicator threshold), or `propose_scheduled_order` "
            "(time-based recurring buy/sell). Pick the most specific "
            "macro that fits the request — falling back to "
            "`propose_workflow` only when none of the macros do.\n"
            "Do NOT use single-shot order tools (`place_market_order`, "
            "`place_limit_order`, `create_gtt_order`, `create_sl_order`, "
            "`create_sip`, etc.) — even if the request looks simple, "
            "treat it as the action step of a workflow.\n"
            "If the request genuinely cannot be expressed as any "
            "agent shape (no trigger, no condition, single immediate "
            "action with all parameters supplied), call ASK_USER once "
            "to confirm the user wants an automation rather than a "
            "one-shot order."
        )
    if mode == "backtest":
        return (
            "## Active mode: BACKTEST\n"
            "The user clicked the BACKTEST pill in the composer. They "
            "want a historical simulation. Call `backtest_workflow` "
            "with the full `steps[]` shape (same schema as "
            "propose_workflow). Do NOT call `run_backtest` (legacy, "
            "single-indicator only) and do NOT call any live-order "
            "tool. Do NOT call `propose_workflow` (that registers an "
            "active strategy, not a backtest). If the user already "
            "answered every clarification, emit the workflow draft and "
            "run it — do NOT loop on ASK_USER once the entry / exit / "
            "window / capital are all stated."
        )
    return ""


def _format_user_context_block(ctx: PromptUserContext) -> str:
    """Render a PromptUserContext as the same '## User context' block
    the assembler used to inline. Returns "" when there's nothing
    useful to render (no fields populated).

    Kept thin and stable so adjacent system messages can be cached
    independently — small portfolio number changes only invalidate
    THIS message's cache region, not the static prefix.
    """
    from backend.prompts.assembler import _format_user_context as _f
    try:
        return _f(ctx)
    except Exception:
        return ""


def _history_to_llm_messages(history: list[dict[str, str]]) -> list[LLMMessage]:
    msgs: list[LLMMessage] = []
    for h in history or []:
        role = h.get("role")
        content = h.get("content") or ""
        if role in {"user", "assistant"}:
            msgs.append(LLMMessage(role=role, content=content))
    return msgs


# Phrases the assistant uses when it's asking the user something.
# We previously gated the follow-up hint on "last assistant content
# ends with `?`" — too narrow. Models often phrase clarifications as
# "Please share your portfolio size." or "Let me know which symbol."
# without a literal question mark. Detect both shapes.
_CLARIFICATION_CUES_RE = re.compile(
    r"\?"
    r"|\bplease\s+(?:share|specify|provide|confirm|clarify|tell|let\s+me\s+know)\b"
    r"|\blet\s+me\s+know\b"
    r"|\bcould\s+you\s+(?:share|specify|tell|confirm|clarify)\b"
    r"|\bcan\s+you\s+(?:share|specify|tell|confirm|clarify)\b"
    r"|\bwhich\s+(?:symbol|stock|ticker|amount|qty|quantity|threshold|period)\b"
    r"|\bhow\s+(?:much|many)\b",
    re.IGNORECASE,
)


def _looks_like_clarification_followup(history: list[dict]) -> bool:
    """True when the latest assistant turn was a clarification (so the
    user's current message is answering it). Used to inject a stronger
    follow-up system hint that carries the original ask forward.

    The earlier gate also required ``len(message) <= 50`` — too tight.
    A reply like "I want a 14-period RSI with threshold 30" is clearly
    a clarification answer but exceeds the cap. Drop the length gate;
    the cue match alone is the right signal."""
    last_assistant = next(
        (h for h in reversed(history)
         if isinstance(h, dict) and h.get("role") == "assistant"),
        None,
    )
    last_text = (last_assistant or {}).get("content") or ""
    if not last_text:
        return False
    # Look at the trailing portion (clarifications end with the ask).
    tail = last_text.rstrip()[-400:]
    return bool(_CLARIFICATION_CUES_RE.search(tail))


def _recent_user_text(history: Optional[list[dict]]) -> str:
    """Concatenate the user-side turns in the prompt window.

    Fed to the M2 suspicious-qty guard as `qty_context` so a quantity
    the user stated on an EARLIER turn ("10 shares") still counts as
    user-named when they later amend an unrelated field ("set an
    expiry for next 30 days") and the draft is re-emitted carrying that
    qty. Without this the guard sees only the current message, decides
    the qty looks defaulted, and re-asks "How many shares?". [C1/C2]
    """
    if not history:
        return ""
    return " ".join(
        (h.get("content") or "")
        for h in history
        if isinstance(h, dict) and h.get("role") == "user"
    )


# [C7] Backtest-intent keyword. Used on a clarification/confirmation
# follow-up turn to detect that the ORIGINAL request was a backtest, so
# backtest_workflow is kept in scope when the user confirms ("right",
# "yes, run it") — otherwise select_tool_names("right") drops it and the
# model loops back to ASK_USER instead of running the backtest.
_BACKTEST_INTENT_RE = re.compile(
    r"\bback[\s-]?test(?:s|ed|ing)?\b"
    r"|\blump[\s-]?sum\b"
    r"|\bhow\s+(?:would|much).{0,40}\b(?:performed?|returned?|done)\b",
    re.IGNORECASE,
)

# [C7+] Backtest TUNING follow-up — a short, verb-less continuation that tweaks a
# backtest the user already ran ("now try RSI<25", "and RSI<20", "add a 5% stop",
# "run that on RELIANCE instead", "the same but RSI<5"). These carry NO backtest
# verb, so the intent classifier doesn't tag them and the model mis-routes to
# get_indicator / propose_workflow. Gated at the call site by a prior backtest in
# the window, this re-forces the backtest tool surface so the tweak RE-RUNS the
# simulation (and the Deflated-Sharpe trial counter keeps deflating across turns).
_BACKTEST_TWEAK_RE = re.compile(
    r"^\s*(?:now|then|also|next|ok(?:ay)?|alright|and|but)?[\s,]*"
    r"(?:try|use|make|set|change|switch|swap|lower|raise|tighten|loosen|widen|"
    r"narrow|add|drop|remove|increase|decrease|bump|re-?run|rerun|redo|"
    r"run\s+(?:it|that|the\s+same)|do\s+(?:it|that|the\s+same))\b"
    r"|\binstead\b"
    r"|\bthe\s+same(?:\s+(?:but|strategy|setup|thing|one))?\b"
    r"|^[A-Za-z ,'/()-]{0,24}\b(?:rsi|sma|ema|wma|macd|adx|cci|mfi|stoch|atr|"
    r"bollinger|supertrend|aroon|donchian|keltner|roc|obv|vwap|williams|period|"
    r"threshold|stop[\s-]?loss|stop|target|window|lookback|trailing)\b"
    r"[^.]{0,30}?\d",
    re.IGNORECASE,
)


def _looks_like_backtest_tweak(message: str) -> bool:
    """A short continuation/tweak to a backtest the user already ran. The length
    cap keeps a full new strategy restatement (which has its own backtest verb)
    out of this path; the call site gates on a prior backtest in history so a
    fresh first-turn "RSI<30 on TCS" can't trip it."""
    msg = (message or "").strip()
    if not msg or len(msg.split()) > 20:
        return False
    return bool(_BACKTEST_TWEAK_RE.search(msg))

# [C7] Delegation replies — the user is handing the decision back to us
# ("you pick"), NOT answering with a specific value. On these we must
# choose a sensible default and DRAFT, never re-ask the same menu.
_DELEGATION_RE = re.compile(
    r"^\s*(?:"
    r"suggest\s+(?:something|one|any)?|"
    r"you\s+(?:decide|choose|pick)|"
    r"(?:whatever|anything)\s+(?:you\s+)?(?:think|recommend|want|prefer|suggest)?|"
    r"your\s+(?:call|choice|pick)|"
    r"(?:pick|choose)\s+(?:one|something|any|for\s+me)?|"
    r"recommend\s+(?:something|one)?|"
    r"up\s+to\s+you|i\s+don'?t\s+(?:know|mind)|either|any(?:thing)?"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def _is_delegation_reply(message: str) -> bool:
    return bool(_DELEGATION_RE.match((message or "").strip()))


# ── Deterministic resume after clarification (Change 2) ──────────────


class ValueCoercionError(ValueError):
    """Raised when a user reply can't be coerced into a pending field's
    type. The chat handler clears the pending state and falls through
    to the normal LLM path when this fires."""


# Replies that mean "abandon what we were doing." If the pending state
# is set and the user types one of these (alone or as the whole
# sentence), we clear pending and fall through to the LLM so the model
# can produce a graceful confirmation.
_RESUME_CANCEL_RE = re.compile(
    # Optional leading filler ("actually nevermind", "ok cancel",
    # "wait, stop") — without this, the resume path missed common
    # natural cancellations and the LLM defaulted to placing a
    # 1-share order. Keep the filler list short and unambiguous.
    r"^\s*(?:actually\s+|ok(?:ay)?[,.\s]+|alright[,.\s]+|"
    r"wait[,.\s]+|hmm[,.\s]+|hold on[,.\s]+)?"
    r"(?:cancel|never\s*mind|nevermind|forget(?:\s+it)?|stop|abort|"
    r"no(?:t)?(?:\s+anymore)?|drop\s+it|drop\s+that)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Broader cancel regex used by `_try_cancel_active_draft`. Allows
# trailing referents the user typically appends when there's a draft on
# screen — "cancel that one", "scrap it", "delete the agent / draft",
# "drop the workflow", "kill the basket". Deliberately more permissive
# than `_RESUME_CANCEL_RE` (which gates value-resume off-ramps and
# must not eat valid value replies).
_CANCEL_DRAFT_RE = re.compile(
    # Optional leading filler ("actually scrap that", "ok cancel",
    # "wait, drop the workflow"). Mirrors _RESUME_CANCEL_RE so cancel
    # behaviour is consistent whether or not a pending state is set.
    r"^\s*(?:actually\s+|ok(?:ay)?[,.\s]+|alright[,.\s]+|"
    r"wait[,.\s]+|hmm[,.\s]+|hold on[,.\s]+)?"
    r"(?:"
    r"cancel|scrap|kill|drop|delete|remove|abort|"
    r"never\s*mind|nevermind|forget(?:\s+(?:it|that|this))?|"
    r"throw\s+(?:it|that)\s+out|"
    # Bare negation as a yes/no answer to a "proceed?" question.
    # WHY: the user trace showed the bot asking "do you want X — reply
    # 'yes' to proceed" and a bare "no" was being interpreted as "no
    # don't change the symbol, ship the original draft" — exactly
    # the wrong outcome. Treat bare "no" / "nope" / "nah" / "no thanks"
    # as a cancel against any active draft so the user can opt out
    # cleanly. The pattern still anchors on string-end so longer
    # messages ("no buy 5 INFY instead") don't accidentally cancel.
    r"no(?:pe)?|nah|no\s+thanks?|no\s+thank\s+you|"
    r"don'?t|do\s+not"
    r")\b"
    r"(?:\s+(?:that|this|it|the|those|these))?"
    r"(?:\s+(?:one|agent|draft|workflow|automation|basket|order|sip|alert|rule))?"
    r"\s*[.!?]*\s*$",
    re.IGNORECASE,
)
# Conjunctions that signal "value PLUS modification" — fall through to
# the LLM so we don't strip context the user added on top.
_RESUME_MULTICLAUSE_RE = re.compile(
    r"\b(?:and|also|but|instead|except|however|plus|then)\b",
    re.IGNORECASE,
)


def _is_simple_value_reply(message: str, expected_kind: str) -> bool:
    """True when the user's message looks like a pure value for the
    pending field — short, single-clause, and shaped like the
    expected type. False for anything that warrants an LLM hop."""
    msg = (message or "").strip()
    if not msg:
        return False
    if _RESUME_CANCEL_RE.match(msg):
        return False
    if _RESUME_MULTICLAUSE_RE.search(msg):
        return False
    # Strip light decoration (₹, $, commas, leading "rs", trailing units)
    # before counting tokens — "₹1,400" and "1400 rs" are still 1-token
    # value replies.
    stripped = re.sub(r"[₹$,]", "", msg)
    if len(stripped.split()) > 6:
        return False
    if expected_kind in {"int", "float"}:
        return bool(re.match(
            r"^\s*-?\d+(?:[.,]\d+)?\s*(?:rs|inr|rupees?|%)?\s*$",
            stripped,
            re.IGNORECASE,
        ))
    if expected_kind == "bool":
        return msg.lower() in {
            "yes", "y", "true", "1", "no", "n", "false", "0",
        }
    if expected_kind == "date":
        return bool(re.match(r"^\s*\d{4}-\d{2}-\d{2}\s*$", msg))
    # str / enum / any → any short single-clause reply qualifies.
    return True


def _coerce_value(message: str, expected_kind: str, enum: Optional[list] = None) -> Any:
    """Convert a user reply into the pending field's type. Raises
    ValueCoercionError when the input doesn't fit."""
    msg = (message or "").strip()
    # Strip currency / unit decoration consistent with the recogniser.
    msg = re.sub(r"[₹$,]", "", msg)
    msg = re.sub(r"\s*(?:rs|inr|rupees?|%)\s*$", "", msg, flags=re.IGNORECASE)

    try:
        if expected_kind == "int":
            return int(float(msg))     # tolerate "10.0"
        if expected_kind == "float":
            return float(msg)
        if expected_kind == "bool":
            low = msg.lower()
            if low in {"yes", "y", "true", "1"}:
                return True
            if low in {"no", "n", "false", "0"}:
                return False
            raise ValueCoercionError(f"can't read {msg!r} as yes/no")
        if expected_kind == "date":
            # Already validated by the recogniser, just return as-is.
            return msg
        if expected_kind == "enum":
            if not enum:
                raise ValueCoercionError("enum field with empty enum")
            for opt in enum:
                if str(opt).lower() == msg.lower():
                    return opt
            raise ValueCoercionError(
                f"{msg!r} not one of {enum}"
            )
        # str / any
        return msg
    except (TypeError, ValueError) as e:
        raise ValueCoercionError(str(e)) from e


def _summarise_tool_result(g: GuardedToolResult) -> str:
    """Compact JSON the loop's next iteration consumes as the tool
    result. Errors get a structured prefix so the model treats them
    as a recovery hint rather than data.

    Tool-specific hints: ``propose_workflow`` validation errors are
    almost always recoverable (an unknown step_type, a numeric field
    given as a ref-string, a missing config key). Telling the model
    "or finish with text" on those errors gave it permission to bail
    out with a chatty apology rather than retry — observed in the
    agent-bucket trace test on 2026-05-04 where the model received
    an unknown-step-type error and then wrote "Sorry — I hit a
    validation error" instead of fixing the step. So the hint for
    propose_workflow says: just emit the corrected draft, no asking,
    no apology.
    """
    if not g.success:
        if g.name == "propose_workflow":
            hint = (
                "RE-EMIT propose_workflow with the SAME draft but "
                "with the specific issue above fixed. Do NOT call "
                "ASK_USER. Do NOT write a 'Sorry, validation error' "
                "message — the user only sees that as a failure. "
                "The fix is usually mechanical: pick a real step_type "
                "from the listed allowed set, fill the named missing "
                "config key, or change a string field to the right "
                "type. Most drafts succeed within 1-2 retries."
            )
        else:
            hint = (
                "Decide whether to call a different tool, call "
                "ASK_USER for clarification, or finish with a brief "
                "explanation. Do not retry the same call with the "
                "same arguments."
            )
        return json.dumps({
            "error": g.error or "tool failed",
            "hint": hint,
        })
    payload: dict[str, Any] = {}
    if g.data:
        payload["data"] = g.data
    if g.logiccard:
        payload["logiccard"] = g.logiccard
    return json.dumps(payload, default=str)[:6000]


# ── ChatService ─────────────────────────────────────────────────────


class ChatService:
    def __init__(
        self,
        store: ConversationStore | None = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.store = store or default_store()
        self._llm = llm_client

    def _client(self) -> LLMClient:
        return self._llm if self._llm is not None else get_llm_client()

    def _reset_session(self, conv_id: str) -> None:
        """Wipe per-conv state on a fresh-session signal.

        Defensive: stub stores in tests can implement only a subset of
        the ConversationStore surface (the relevant tests stub
        get_history / append / get_pending / set_pending). We swallow
        AttributeError so a partially-implemented store never breaks
        production code that legitimately relies on these calls.
        """
        for attr in (
            "clear_active_draft",
            "clear_pending",
            "clear_pending_resolution",
            "clear",
        ):
            fn = getattr(self.store, attr, None)
            if callable(fn):
                try:
                    fn(conv_id)
                except Exception:  # noqa: BLE001 — defensive, never blocks turn
                    logger.debug("session reset: %s failed", attr, exc_info=True)

    def _stash_workflow_draft(
        self, conv_id: str, draft: dict, caption: str = "",
        tool_name: str = "propose_workflow",
    ) -> None:
        """Cache the just-emitted workflow draft for the next turn's
        followup hint. Single source of truth — call from every place
        a draft becomes the user's pending agent (skeleton fast-path,
        agentic loop success, macro fallback).

        WHY tool_name param: propose_threshold_order and
        propose_scheduled_order also produce draft cards the user amends.
        We need the actual tool_name so the amendment hint on the next
        turn tells the LLM to re-emit the RIGHT tool, not propose_workflow.
        Previously hardcoded to "propose_workflow" — that caused "make it
        5 shares" after a threshold order to get no amendment hint (the
        stash was never set), so the LLM produced prose instead of calling
        propose_threshold_order again.
        """
        if not draft:
            return
        self.store.set_active_draft(conv_id, ActiveDraft(
            tool_name=tool_name,
            draft=draft,
            last_caption=caption[:400],
            created_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        ))

    def _maybe_set_pending(self, conv_id: str, guarded: GuardedToolResult) -> None:
        """Persist a PendingToolCall when a clarification fired with a
        single, coercible missing field. Multi-field misses and
        free-form ASK_USER calls clear pending — those need an LLM
        hop on the next turn."""
        mf = guarded.missing_field
        if mf is None or mf.type_kind in {"any", "object"}:
            self.store.clear_pending(conv_id)
            return
        self.store.set_pending(conv_id, PendingToolCall(
            tool_name=guarded.name,
            args=guarded.args,
            missing_field=mf.field_name,
            field_type=mf.type_kind,
            field_description=mf.description,
            enum=mf.enum,
            asked_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        ))

    def _maybe_set_pending_resolution(
        self, conv_id: str, original_intent: str,
        guarded: GuardedToolResult,
    ) -> None:
        """R2: persist a PendingResolution when ASK_USER carries
        default_on_yes or options. A pure-affirmative reply next turn
        resolves to default_on_yes without an LLM hop. Cleared on the
        next non-affirmative turn or by TTL."""
        from backend.services.conversation_store import PendingResolution
        if not (guarded.default_on_yes or guarded.options):
            # Clear stale resolution from a prior turn — only the most
            # recent clarification's options/default should be active.
            self.store.clear_pending_resolution(conv_id)
            return
        self.store.set_pending_resolution(conv_id, PendingResolution(
            question=(guarded.question or "").strip(),
            default_on_yes=guarded.default_on_yes,
            options=guarded.options or [],
            original_intent=original_intent[:280] if original_intent else None,
            asked_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        ))

    def _try_cancel_active_draft(
        self,
        *,
        message: str,
        conv_id: str,
        trace: TurnTrace,
        turn_started: float,
        breakdown: dict[str, int],
    ) -> Optional["ChatTurn"]:
        """Deterministic cancel for an unactivated draft.

        Fires when there's no pending tool-call but an active_draft IS
        cached AND the user typed a clean cancel intent. Clears the
        draft and returns a confirmation ChatTurn — zero LLM hops.

        Returns None when the cancel doesn't apply, so the caller falls
        through to the normal LLM path. We deliberately do not handle
        cancellation of *activated* agents here — that needs a tool
        call against the workflow service, which the LLM is better at.
        """
        if not _CANCEL_DRAFT_RE.match(message.strip()):
            return None
        active = self.store.get_active_draft(conv_id)
        if active is None:
            return None
        # An active pending state means the value-resume path will
        # handle the cancel (it already clears active_draft too).
        # Don't double-handle.
        if self.store.get_pending(conv_id) is not None:
            return None

        # ActiveDraft stores the raw tool-args JSON; the LLM usually
        # populates a "name" or "description" field. Pick the first
        # short, human-shaped string for the cancellation reply.
        draft_payload = active.draft if isinstance(active.draft, dict) else {}
        draft_name = ""
        for key in ("name", "title", "description"):
            val = draft_payload.get(key)
            if isinstance(val, str) and val.strip():
                draft_name = val.strip().split("\n", 1)[0][:80]
                break

        self.store.clear_active_draft(conv_id)
        # `name` is the positional event name on TurnTrace.event — don't
        # collide with it when passing draft metadata.
        trace.event("cancel.active_draft", tool=active.tool_name,
                    draft_name=draft_name or "(unnamed)")

        draft_label = f' "{draft_name}"' if draft_name else ""
        reply = (
            f"Cancelled the draft{draft_label}. "
            "Tell me if you want to start over or build something different."
        )
        self.store.append(conv_id, message, reply)
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        _log_timing("cancel_draft", message, total, breakdown,
                    tools=["cancel_draft"], note="deterministic-cancel")
        trace.event("turn.end", total_ms=total,
                    tools_called=["cancel_draft"], reason="cancel_draft")
        trace.end()
        return ChatTurn(
            response=reply,
            tools_called=["cancel_draft"],
            raw_data={"_render_hint": "logic_card"},
            latency_breakdown=breakdown,
            latency_ms=total,
        )

    async def _try_fast_resume(
        self,
        *,
        message: str,
        conv_id: str,
        ctx: "UserContext",
        trace: TurnTrace,
        turn_started: float,
        breakdown: dict[str, int],
    ) -> Optional["ChatTurn"]:
        """Deterministic resume after an ASK_USER clarification.

        Returns None when there's no pending state OR the user's reply
        doesn't fit the missing field — both cases fall through to the
        normal LLM path. Returns a ChatTurn (success or cascading
        clarification) when we resumed without an LLM hop.
        """
        pending = self.store.get_pending(conv_id)
        if pending is None:
            return None

        # Cancellation off-ramp: clear pending AND any active draft
        # (the user is abandoning the whole thing), then let the LLM
        # produce a graceful confirmation of the abandonment.
        if _RESUME_CANCEL_RE.match(message.strip()):
            self.store.clear_pending(conv_id)
            self.store.clear_active_draft(conv_id)
            trace.event("resume.cancelled", tool=pending.tool_name,
                        field=pending.missing_field)
            return None

        if not _is_simple_value_reply(message, pending.field_type):
            # Doesn't look like a clean value reply (multi-clause,
            # too long, wrong shape). Don't clear pending yet — the
            # LLM might still resolve it; if the next assistant turn
            # supersedes it the pending state expires on its own.
            trace.event("resume.shape_mismatch", tool=pending.tool_name,
                        field=pending.missing_field, kind=pending.field_type)
            return None

        try:
            value = _coerce_value(message, pending.field_type, pending.enum)
        except ValueCoercionError as e:
            trace.event("resume.coerce_failed", tool=pending.tool_name,
                        field=pending.missing_field, error=str(e)[:120])
            self.store.clear_pending(conv_id)
            return None

        # Splice the value into the saved args and execute. Any further
        # missing field cascades into a fresh pending state — still no
        # LLM hop on this turn.
        new_args = dict(pending.args)
        new_args[pending.missing_field] = value
        client = self._client()
        guarded = await execute_with_completeness(
            pending.tool_name, new_args,
            llm_client=client, user_message=message,
            kite_token=ctx.kite_token, db=ctx.db, user_id=ctx.user_id,
            # [C1/C2] include prior user turns so a qty named earlier
            # isn't re-flagged as a silent default during resume.
            qty_context=_recent_user_text(
                self.store.get_history(conv_id, limit=CONV_PROMPT_WINDOW_TURNS)
            ),
        )
        breakdown[f"tool_{guarded.name}"] = guarded.latency_ms
        trace.event("resume.tool", tool=guarded.name,
                    success=guarded.success,
                    needs_clarification=guarded.needs_clarification,
                    error=guarded.error)

        # Cascading clarification — set new pending and surface the
        # next question. Still 0 LLM calls on this turn.
        if guarded.needs_clarification and guarded.question:
            self.store.append(conv_id, message, guarded.question)
            if guarded.missing_field is not None:
                self.store.set_pending(conv_id, PendingToolCall(
                    tool_name=guarded.name,
                    args=guarded.args,
                    missing_field=guarded.missing_field.field_name,
                    field_type=guarded.missing_field.type_kind,
                    field_description=guarded.missing_field.description,
                    enum=guarded.missing_field.enum,
                    asked_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                ))
            else:
                self.store.clear_pending(conv_id)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["total"] = total
            _log_timing("resume", message, total, breakdown,
                        tools=[guarded.name], note="resume-cascade")
            trace.event("turn.end", total_ms=total, tools_called=[guarded.name],
                        reason="resume_cascade")
            trace.end()
            return ChatTurn(
                response=guarded.question,
                tools_called=[guarded.name],
                raw_data={"_render_hint": "ask_user"},
                latency_ms=total,
                latency_breakdown=breakdown,
            )

        # Tool failed for some other reason on resume — clear pending,
        # fall through to the LLM so the user gets a normal recovery
        # path rather than a stale pending loop.
        if not guarded.success:
            self.store.clear_pending(conv_id)
            trace.event("resume.tool_error", tool=guarded.name,
                        error=(guarded.error or "")[:120])
            return None

        # Success — render the card directly. ZERO LLM calls this turn.
        self.store.clear_pending(conv_id)
        raw_data: dict[str, Any] = {}
        logiccard: Optional[dict] = None
        if guarded.data:
            raw_data[guarded.name] = guarded.data
        if guarded.logiccard:
            logiccard = guarded.logiccard
        text = _tool_summary_line(guarded.name, logiccard)
        text = _ensure_widget_caption(
            text, tool_name=guarded.name,
            logiccard=logiccard, raw_data=raw_data,
        )
        self.store.append(conv_id, message, text)
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        _log_timing("resume", message, total, breakdown,
                    tools=[guarded.name], note="resume-success")
        trace.event("turn.end", total_ms=total, tools_called=[guarded.name],
                    reason="resume_success")
        trace.end()
        return ChatTurn(
            response=text,
            tools_called=[guarded.name],
            logiccard=logiccard,
            raw_data=raw_data,
            latency_ms=total,
            latency_breakdown=breakdown,
        )

    async def handle(
        self,
        message: str,
        conv_id: str,
        ctx: UserContext,
        *,
        history_override: list[dict] | None = None,
        mode_override: Optional[str] = None,
    ) -> ChatTurn:
        turn_started = time.monotonic()
        # Make the conversation id ambient so tool handlers that don't take it
        # (e.g. the backtest tools) can group DSR trials by conversation.
        from backend.services.turn_context import set_conversation_id
        set_conversation_id(conv_id)
        breakdown: dict[str, int] = {}
        trace = start_turn(conv_id, message)
        trace.event("turn.start", message_preview=message[:120])

        # ── Fast path ──────────────────────────────────────────────
        fast_response = try_fast_path(message)
        if fast_response is not None:
            trace.event("fast_path.matched")
            self.store.append(conv_id, message, fast_response)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["fast_path"] = total
            breakdown["total"] = total
            _log_timing("fast_path", message, total, breakdown, tools=[])
            trace.event("turn.end", total_ms=total, tools_called=[])
            trace.end()
            return ChatTurn(
                response=fast_response,
                latency_ms=total,
                latency_breakdown=breakdown,
            )

        # (F&O pre-LLM decline removed in P1 — options strategy verbs
        # now route to the suggest/build/critique tools via the router
        # and the _mentions_fno tool gate further down.)

        # ── Workflow skeleton fast-path ────────────────────────────
        # Canonical agent shapes (scheduled SIP, RSI threshold, price
        # threshold) skip the LLM hop entirely. Validates the draft
        # against the step registry before returning so a structurally
        # broken skeleton falls through to the LLM rather than going
        # out wrong.
        skeleton = try_workflow_skeleton(message)
        if skeleton is not None:
            try:
                from backend.workflows.propose import (
                    ProposalValidationError, validate_draft_against_registry,
                )
                validate_draft_against_registry(skeleton)
            except ProposalValidationError as e:
                trace.event("workflow_skeleton.invalid", error=str(e)[:120])
                skeleton = None
        if skeleton is not None:
            trace.event(
                "workflow_skeleton.matched",
                workflow_name=skeleton.get("name"),
                step_types=[s["step_type"] for s in skeleton.get("steps") or []],
            )
            response_text = _workflow_skeleton_caption(skeleton)
            self.store.append(conv_id, message, response_text)
            self._stash_workflow_draft(conv_id, skeleton, response_text)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["workflow_skeleton"] = total
            breakdown["total"] = total
            _log_timing("workflow_skeleton", message, total, breakdown,
                        tools=["propose_workflow"])
            trace.event("turn.end", total_ms=total,
                        tools_called=["propose_workflow"], reason="skeleton")
            trace.end()
            # Stash the draft under the tool name (matches the agentic
            # loop's convention for raw_data) AND let the router's
            # hoisting lift name/steps/rationale to top-level so the
            # FE's WorkflowDraftCard can read them directly. We
            # deliberately do NOT set top-level _render_hint here —
            # the hoister only fires when it's absent, and we want it
            # to fire so the draft fields get hoisted alongside the
            # render hint.
            return ChatTurn(
                response=response_text,
                tools_called=["propose_workflow"],
                latency_ms=total,
                raw_data={"propose_workflow": skeleton},
                latency_breakdown=breakdown,
            )

        # ── Deterministic resume (Change 2) ────────────────────────
        # If the previous turn ended with a clarification AND the user's
        # current message looks like a clean value reply, splice the
        # value into the partial args and execute the tool — no LLM.
        # Off-ramps (cancel / multi-clause / type mismatch) clear the
        # pending state and fall through to the LLM path.
        resumed = await self._try_fast_resume(
            message=message, conv_id=conv_id, ctx=ctx, trace=trace,
            turn_started=turn_started, breakdown=breakdown,
        )
        if resumed is not None:
            return resumed

        # ── Deterministic cancel for an unactivated draft ──────────
        # When the user types "cancel that one" / "scrap it" right after
        # a propose_workflow draft, we don't need an LLM hop to figure
        # out what to do. Clear the active draft and confirm.
        cancelled = self._try_cancel_active_draft(
            message=message, conv_id=conv_id, trace=trace,
            turn_started=turn_started, breakdown=breakdown,
        )
        if cancelled is not None:
            return cancelled

        # ── Pure-affirmative fast-path ────────────────────────────
        # When the user types "ok" / "yes" / "sure" / "got it":
        #
        # 1. If a PendingResolution (R2) carries a `default_on_yes`,
        #    we resolve deterministically — substitute the message
        #    with the option text and let the normal LLM path proceed
        #    with the answered clarification. Avoids the
        #    over-confirmation loop (screenshot 7) and the fabricated
        #    context bug (screenshots 9, 10).
        # 2. Else if an active draft exists, surface the short ack —
        #    re-emitting the macro tool wastes ~5-10s for zero
        #    behavioural change.
        # 3. Else fall through to the normal LLM path — but mark
        #    `_affirm_no_state` so a system hint below tells the
        #    model "no draft on screen; do NOT fabricate one." Kills
        #    the "the draft above is what you'll activate"
        #    fabrication when no draft exists.
        _affirm_no_state = False
        if _is_pure_affirmative(message):
            resolution = self.store.get_pending_resolution(conv_id)
            # When ASK_USER carried options but the model forgot to
            # set default_on_yes, treat the first option as the
            # implicit default. This is the convention "the option
            # I named first is the most likely pick." Without this,
            # "yes proceed" after "Did you mean MAHINDRA or M&MFIN?"
            # fell through to a re-ask loop.
            resolved_value = None
            if resolution is not None:
                if resolution.default_on_yes:
                    resolved_value = resolution.default_on_yes
                elif resolution.options:
                    resolved_value = resolution.options[0]
            if resolved_value:
                trace.event(
                    "pending_resolution.resolved",
                    resolved=resolved_value,
                    source=(
                        "default_on_yes"
                        if resolution.default_on_yes else "options[0]"
                    ),
                )
                self.store.clear_pending_resolution(conv_id)
                # Substitute the message with the resolved value so
                # downstream routing treats this as the user typing
                # the option. Original "yes" still appears in stored
                # history (appended at turn end).
                message = resolved_value
            else:
                affirm_active_draft = self.store.get_active_draft(conv_id)
                if affirm_active_draft is not None:
                    ack = (
                        "Got it — the draft above is what you'll activate. "
                        "Click **Save & activate** in the card when you're ready."
                    )
                    self.store.append(conv_id, message, ack)
                    total = int((time.monotonic() - turn_started) * 1000)
                    breakdown["affirm_ack"] = total
                    breakdown["total"] = total
                    _log_timing("affirm_ack", message, total, breakdown, tools=[])
                    trace.event("turn.end", total_ms=total, tools_called=[],
                                reason="pure_affirmative_ack")
                    trace.end()
                    return ChatTurn(
                        response=ack, tools_called=[],
                        logiccard=None, raw_data=None,
                        latency_ms=total, latency_breakdown=breakdown,
                    )
                # No draft, no pending resolution. Fall through with
                # the affirmation-no-state flag so a hint downstream
                # forbids draft fabrication.
                _affirm_no_state = True

        # ── R3 micro: structured-resolution hint for non-affirmative
        # replies while a PendingResolution is active. The LLM sees
        # the question + structured options directly instead of
        # re-parsing prose. Carries forward to the system message
        # block below.
        pending_resolution_hint_text: str = ""
        pending_resolution_active = False
        if not _is_pure_affirmative(message):
            _pr = self.store.get_pending_resolution(conv_id)
            if _pr is not None and (_pr.question or _pr.options):
                pending_resolution_active = True
                opts_block = (
                    "Options: " + " | ".join(_pr.options) + "."
                    if _pr.options else ""
                )
                default_block = (
                    f"Default if user says 'yes': {_pr.default_on_yes}."
                    if _pr.default_on_yes else ""
                )
                original_block = (
                    f" Original intent: \"{_pr.original_intent[:200]}\"."
                    if _pr.original_intent else ""
                )
                # [C7] If the user DELEGATES ("suggest something", "you
                # decide"), do NOT re-ask the same menu — pick a default.
                _deleg_clause = (
                    "The user is DELEGATING the choice to you — do NOT "
                    "re-ask the same menu. Choose the single most sensible "
                    "option yourself (favour the simplest actionable "
                    "strategy, e.g. a momentum / moving-average or "
                    "threshold buy) and emit the tool with it. "
                    if _is_delegation_reply(message) else ""
                )
                # [C7] If the original request was a backtest, the emit
                # tool is backtest_workflow — say so explicitly.
                _bt_clause = (
                    "The ORIGINAL request is a BACKTEST — you MUST call "
                    "backtest_workflow (NOT propose_workflow) and report "
                    "the winner and by how much. "
                    if (_pr.original_intent
                        and _BACKTEST_INTENT_RE.search(_pr.original_intent))
                    else ""
                )
                pending_resolution_hint_text = (
                    "## Pending clarification (structured)\n"
                    f"You asked: \"{_pr.question}\". "
                    f"{opts_block} {default_block}{original_block} "
                    "The user's CURRENT message is their answer. "
                    + _deleg_clause + _bt_clause +
                    "Map it to one of the options if possible, then "
                    "EMIT the workflow / order / backtest tool IMMEDIATELY "
                    "with the resolved value substituted into the original "
                    "request. Do NOT re-ask. Do NOT write prose like "
                    "'Drafted: ...' without actually calling the tool. "
                    "If no tool exists for the merged request, ASK_USER "
                    "for the next missing piece — never write a fake "
                    "draft description."
                ).strip()
                # Clear once consumed so a stale resolution doesn't
                # bleed across turns.
                self.store.clear_pending_resolution(conv_id)

        # ── Fresh-session eviction ─────────────────────────────────
        # When the FE explicitly hands us an EMPTY history list, the
        # user just opened a new chat. Any active draft / pending
        # clarification still sitting in Redis from the prior session
        # under this conv_id MUST go — it's another mechanism for the
        # "old context bleeds into new chat" failure shape.
        if history_override is not None and len(history_override) == 0:
            self._reset_session(conv_id)

        # ── Active-draft eviction (independent prompt, any turn) ───
        # Evict an active workflow draft when the current message is
        # a clearly independent top-level intent. Without this, even
        # turns that are NOT clarification-followups (so the followup
        # hint never fires) still let stale drafts leak — e.g. user
        # asks "What are the pros and cons of Reliance?" three turns
        # after building an HDFCBANK agent, and the model attaches
        # the stale HDFCBANK card under its prose answer.
        had_active_draft_at_entry = (
            self.store.get_active_draft(conv_id) is not None
        )
        if _is_independent_prompt(message):
            stale = self.store.get_active_draft(conv_id)
            if stale is not None:
                self.store.clear_active_draft(conv_id)
                trace.event("active_draft.evicted",
                            reason="independent_prompt_top",
                            tool=stale.tool_name)

        # ── Mode-override eviction (explicit user pill) ────────────
        # When the user clicked Automation or Backtest in the composer,
        # they EXPLICITLY want a non-workflow route. Drop any cached
        # workflow draft so the amendment-hint path further down can't
        # pull the LLM back into propose_workflow. (Agent mode keeps
        # the draft — that's the "iterate on a workflow" loop.)
        if mode_override in {"automation", "backtest"}:
            stale = self.store.get_active_draft(conv_id)
            if stale is not None:
                self.store.clear_active_draft(conv_id)
                trace.event("active_draft.evicted",
                            reason=f"mode_override:{mode_override}",
                            tool=stale.tool_name)

        # ── Agentic loop setup ─────────────────────────────────────
        # History window. We CAP this at CONV_PROMPT_WINDOW_TURNS so a
        # long conversation doesn't keep dragging stale tickers,
        # stale drafts, and stale clarifications back into context.
        # Storage stays at CONV_MAX_TURNS for transcript / debug.
        if history_override is not None:
            history = history_override[-(CONV_PROMPT_WINDOW_TURNS * 2):]
        else:
            history = self.store.get_history(
                conv_id, limit=CONV_PROMPT_WINDOW_TURNS,
            )

        client = self._client()
        # Per-hop tool router — narrows the visible tool surface from
        # ~48 down to ~8-12 based on keyword matches in the user's
        # current message. Halves input tokens on most turns. The
        # router is tolerant: if no rules match it returns the
        # always-include floor + fallback read tools, so we never
        # ship a turn with zero tools.
        selected_names = select_tool_names(message)
        intent_kind = _classify_intent(message)

        # WHY this strip exists: when an active draft was sitting in
        # cache at the start of this turn AND the user's message is
        # a short bare alphabetic token that isn't a recognized verb /
        # affirmative / negation (the canonical bug: "nothung" — typo
        # for "nothing"), the model would re-emit the prior card from
        # conversation history. Stripping order + macro tools forces
        # the model to either fetch (if the token IS a real ticker)
        # or prose-reply / ask for clarification.
        if (had_active_draft_at_entry
                and selected_names is not None
                and _is_bare_typo_continuation(message)
                and not _DEPENDENT_INTENT_RE.search(message)):
            selected_names = selected_names - _ORDER_AND_MACRO_TOOLS
            trace.event(
                "tools.stripped_typo_continuation",
                stripped=sorted(_ORDER_AND_MACRO_TOOLS),
                reason="bare_token_with_active_draft",
            )
        # Post-order-clarification override: if the current message is a
        # short affirmative (e.g. "yes, SWIGGY on NSE") replying to a bot
        # question that was triggered by an order intent, force "automation"
        # so workflow macros are stripped and place_market_order stays in scope.
        # WHY: without this, "yes, SWIGGY on NSE" is classified as "other",
        # propose_workflow stays in scope, and the LLM upgrades a one-time
        # buy into a recurring workflow draft.
        if intent_kind == "other" and _is_post_order_clarification(message, history):
            intent_kind = "automation"
            trace.event("intent.post_clarification_order_override")
        # User-supplied mode pill (Automation / Agent / Backtest from
        # the FE composer) overrides the keyword classifier
        # deterministically. Lets users force a route the regex would
        # have got wrong — and short-circuits the "is this an
        # automation or an agent?" guesswork the BE has to do otherwise.
        if mode_override in {"automation", "agent", "backtest"}:
            intent_kind = mode_override
            trace.event("mode_override.applied", mode=mode_override)
        is_agent_intent = intent_kind == "agent"
        is_automation_intent = intent_kind == "automation"
        is_backtest_intent = intent_kind == "backtest"
        # Tool-surface routing per intent class:
        #
        #   agent      → strip immediate-order tools (e.g. place_limit_order),
        #                force propose_workflow into the surface so the model
        #                drafts a workflow rather than misfiring a one-off.
        #   automation → KEEP the immediate-order tools (this IS automation —
        #                place_market_order / create_sl_order / create_sip /
        #                squareoff are exactly what we want), and DROP
        #                propose_workflow so the model can't reach for it.
        #                A single-action ask shouldn't become a workflow.
        #   backtest   → narrow to run_backtest + price-history reads.
        #   other      → leave the broad surface alone.
        _IMMEDIATE_ORDER_TOOLS = frozenset({
            "place_market_order", "place_limit_order",
            "create_gtt_order", "create_sl_order", "create_oco_order",
            "create_dip_buy", "place_basket_order",
            "create_sip", "squareoff_all_intraday", "squareoff_symbol",
        })
        if is_agent_intent and selected_names is not None:
            selected_names = (selected_names - _IMMEDIATE_ORDER_TOOLS) | {
                "propose_workflow",
            }
        elif is_automation_intent and selected_names is not None:
            # Automation = single immediate action. Remove ALL workflow/macro
            # tools — not just propose_workflow — so the LLM can't fall back
            # to a scheduled/threshold draft instead of an immediate tool call.
            # WHY all four macros: propose_scheduled_order is in _ALWAYS_INCLUDE
            # so it survives the "remove propose_workflow" pass. "buy reliance
            # at open" → the LLM then sees propose_scheduled_order and interprets
            # "at open" as "9:15 AM on schedule", creating a DAILY recurring
            # order instead of asking "one-time or recurring?". Removing all
            # macros here forces the LLM to use place_market_order or ASK_USER.
            _ALL_MACRO_TOOLS = frozenset({
                "propose_workflow", "propose_scheduled_order",
                "propose_threshold_order", "propose_basket_allocation",
                "propose_holding_action",
            })
            selected_names = (selected_names - _ALL_MACRO_TOOLS) | (
                _IMMEDIATE_ORDER_TOOLS
            )
        elif is_backtest_intent and selected_names is not None:
            # Backtest pill → narrow to backtest + read tools. Keep
            # propose_workflow excluded (no agent-build mid-backtest)
            # and orders excluded (no live trades from a backtest pill).
            # ALSO exclude run_backtest — the legacy single-indicator
            # tool drags the LLM into "what's the trigger_condition?"
            # clarification loops because trigger_condition is required
            # but the schema is too abstract for the LLM to fill from
            # free-form prose. backtest_workflow uses the propose-
            # workflow steps[] shape which the LLM already knows.
            selected_names = (
                (
                    selected_names
                    - _IMMEDIATE_ORDER_TOOLS
                    - {"propose_workflow", "run_backtest"}
                )
                | {"backtest_workflow", "get_price_history",
                   "get_live_price", "get_52wk_range"}
            )
        # Advisory questions in "other" intent: strip workflow macros.
        # WHY: "should I reduce that exposure?" after portfolio data was
        # calling propose_workflow. The system prompt "never attach a
        # workflow draft to an informational answer" is prose-only — LLM
        # ignores it. Remove the tools to enforce the rule structurally.
        # Exception: advisory phrasing + workflow-building keywords (e.g.
        # "should I set up an RSI strategy") keeps macros in scope.
        if (intent_kind == "other"
                and selected_names is not None
                and _ADVISORY_INTENT_RE.search(message)
                and not _ADVISORY_WORKFLOW_EXCEPTION_RE.search(message)):
            _ALL_MACRO_TOOLS = frozenset({
                "propose_workflow", "propose_scheduled_order",
                "propose_threshold_order", "propose_basket_allocation",
                "propose_holding_action",
            })
            selected_names = selected_names - _ALL_MACRO_TOOLS
        tooldefs = _registry_tools_as_tooldefs(selected_names)
        # Route-stable cache key — a fresh hash of the routed toolset
        # so each unique route caches its own system + tools prefix.
        # Without this, every route shift used to miss the cache for
        # one turn before warming.
        cache_key = cache_key_for(selected_names)

        # ── Agent-intent fast path tuning (A1 + B4) ────────────────
        # When the message signals "build me an agent" we know the
        # intended tool is propose_workflow. We then:
        #   A1. Set tool_choice="required" so the model MUST emit a
        #       tool call instead of think-aloud text. Removes 1–3
        #       wasted hops per turn.
        #   B4. Drop reasoning_effort to "minimal". Trace data showed
        #       gpt-5-mini hops with 800 reasoning tokens added ~10s
        #       of latency without changing the JSON output, since
        #       few-shot examples in the tool description already
        #       guide structure.
        # The skeleton fast-path (above) intercepts the easiest agent
        # shapes pre-LLM; this branch covers everything that fell
        # through to the model.
        agent_tool_choice: Literal["auto", "required"] = (
            "required" if is_agent_intent else "auto"
        )
        # When a PendingResolution is active (user is answering a prior
        # clarification), force tool_choice=required so the model emits
        # the workflow / ASK_USER tool instead of writing prose. The
        # observed L03_04 failure mode: "yes that one" after an M&M
        # disambiguation → no tool, just prose claiming a draft was made.
        if pending_resolution_active:
            agent_tool_choice = "required"
            # Ensure ALL relevant emit tools are in scope so the model
            # has a working path. Don't ADD if selected_names is None
            # (whitelist mode) — preserve existing semantics.
            if selected_names is not None:
                selected_names = selected_names | frozenset({
                    "ASK_USER", "propose_workflow",
                    "propose_threshold_order", "propose_scheduled_order",
                    "propose_dsl_workflow", "propose_holding_action",
                    "propose_basket_allocation",
                })
                tooldefs = _registry_tools_as_tooldefs(selected_names)
                cache_key = cache_key_for(selected_names)
        # [C7] Backtest-confirmation follow-up. When the user is answering
        # a clarification on a BACKTEST request — "right" / "yes, run it" /
        # "use 2022" / an amount — the emit tool is backtest_workflow. But
        # select_tool_names("right") doesn't surface it, and the
        # pending-resolution block above only force-adds the propose_*
        # macros — so the model has no backtest tool, can't emit, and
        # loops back to ASK_USER ("...sound right?" forever). Detect a
        # backtest original intent anywhere in the window and force
        # backtest_workflow into scope with tool_choice=required.
        _backtest_followup = False
        _prev_backtest_in_window = any(
            _BACKTEST_INTENT_RE.search((h or {}).get("content") or "")
            for h in (history or [])
            if (h or {}).get("role") == "user"
        )
        # A verb-less TUNING tweak ("now try RSI<25", "add a stop") of an
        # already-run backtest — distinct from answering a clarification.
        _is_backtest_tweak = (
            _prev_backtest_in_window and _looks_like_backtest_tweak(message)
        )
        if (selected_names is not None
                and _prev_backtest_in_window
                and (pending_resolution_active
                     or (history and _looks_like_clarification_followup(history))
                     or _is_backtest_tweak)):
            if _is_backtest_tweak:
                # NARROW to the backtest tools (+ ASK_USER) so the model re-runs
                # the simulation rather than fetching a live indicator or
                # drafting an agent for a verb-less tweak.
                selected_names = frozenset({
                    "backtest_workflow", "backtest_dsl_tree", "ASK_USER",
                })
            else:
                # Answering a clarification — keep scope, just ensure both
                # backtest emit tools are present.
                selected_names = selected_names | {
                    "backtest_workflow", "backtest_dsl_tree",
                }
            tooldefs = _registry_tools_as_tooldefs(selected_names)
            cache_key = cache_key_for(selected_names)
            agent_tool_choice = "required"
            _backtest_followup = True
            trace.event("backtest_followup.scope_forced", tweak=_is_backtest_tweak)
        # Underspec relaxation: "build me an agent for X" with no action /
        # trigger / quantity is genuinely ambiguous — we want ASK_USER,
        # not a fabricated draft. We do TWO things:
        #   (a) drop tool_choice to "auto" so ASK_USER is selectable,
        #   (b) strip macro tools from scope so the model literally
        #       cannot emit `propose_workflow` even if it wanted to.
        # Without (b) the model still picked propose_workflow with
        # fabricated defaults, citing "I have a symbol from history"
        # — overriding the system-prompt rule that says ASK first.
        # Removing macros structurally is the only reliable enforcement.
        is_underspec_agent = is_agent_intent and (
            _is_underspecified_agent_build(message)
            or _is_ungrounded_level_prompt(message)
        )
        # Filler reply after our own clarification question is the same
        # class of underspec: user gave us nothing new, we shouldn't
        # fabricate a default. Treat it identically to "build an agent
        # for X" with no other context.
        is_filler_after_q = (
            _is_filler_reply(message) and _prev_assistant_was_question(history)
        )
        # F&O gating (P1): when the message mentions options/strikes/
        # expiry, ADD the options tool surface and strip the cash-equity
        # order tools so the model serves the ask with the real options
        # tools instead of hallucinating an equity order.
        mentions_fno = _mentions_fno(message)
        # Contradiction gating: "buy AND sell same time" — strip
        # macros so the model can't draft both, force ASK_USER.
        is_contradiction = _is_buy_sell_contradiction(message)

        if is_underspec_agent or is_filler_after_q or mentions_fno or is_contradiction:
            agent_tool_choice = "auto"
            if selected_names is not None:
                _UNDERSPEC_STRIP = frozenset({
                    "propose_workflow", "propose_scheduled_order",
                    "propose_threshold_order", "propose_basket_allocation",
                    "propose_holding_action",
                })
                if mentions_fno:
                    # Strip immediate-order tools so the model can't
                    # hallucinate a place_market_order on an options ticker…
                    _UNDERSPEC_STRIP = _UNDERSPEC_STRIP | frozenset({
                        "place_market_order", "place_limit_order",
                        "create_gtt_order", "create_sl_order",
                        "create_oco_order", "create_dip_buy",
                        "place_basket_order", "create_sip",
                    })
                selected_names = selected_names - _UNDERSPEC_STRIP
                if mentions_fno:
                    # …and make sure the options surface is present even
                    # when the regex router missed (e.g. slangy phrasing).
                    selected_names = selected_names | _OPTIONS_TOOLS
                # selected_names was already converted to tooldefs above
                # before this strip. Rebuild the tooldefs to reflect the
                # narrower set actually sent to the LLM on the first hop.
                tooldefs = _registry_tools_as_tooldefs(selected_names)
                cache_key = cache_key_for(selected_names)

        # ── GAN R2 deterministic guards (R2–R6) ────────────────────────
        # Prose in system.md alone did not suppress the over-eager
        # ASK_USER / 09:30 downgrade. Pin scope + tool_choice here, then
        # pair with directive system messages built below.
        _deterministic_guards = _build_deterministic_guards(message, history)
        _named_option_build = _is_named_option_build(message)
        _notify_only = _is_notify_only_alert(message)
        _at_open_close = _is_at_open_close_build(message)
        _confusion_menu = _is_confusion_after_menu(message, history)
        _unsupported_rail = _names_unsupported_rail(message)
        # R4: named option template build → force build_option_strategy,
        # remove ASK_USER from scope so the model cannot escape to it.
        if _named_option_build and selected_names is not None:
            selected_names = (selected_names | _OPTIONS_TOOLS) - frozenset({
                "place_market_order", "place_limit_order",
                "create_gtt_order", "suggest_option_strategy",
                "critique_option_strategy",
            })
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            agent_tool_choice = "required"
        # R3: fully-specified notify-only alert → force propose_dsl_workflow,
        # drop ASK_USER so it can't ask about the single channel.
        elif _notify_only and selected_names is not None:
            selected_names = (selected_names | frozenset({
                "propose_dsl_workflow",
            })) - frozenset({
                "place_market_order", "place_limit_order",
                "create_gtt_order", "create_sl_order",
            })
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            agent_tool_choice = "required"
        # R2: at-open/at-close build → ensure the DSL/workflow tools are in
        # scope and force a tool so it can't downgrade to 09:30 / ASK_USER.
        elif _at_open_close and selected_names is not None:
            selected_names = selected_names | frozenset({
                "propose_dsl_workflow", "propose_workflow",
            })
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            agent_tool_choice = "required"
        # R5/R6: unsupported rail or confusion-after-menu → the reply is a
        # boundary/teach in PROSE; drop tool_choice to auto so the model is
        # free to answer without forcing a tool, and (R6) drop ASK_USER so
        # it cannot re-dump the menu.
        if _confusion_menu:
            agent_tool_choice = "auto"
            if selected_names is not None:
                tooldefs = [
                    t for t in _registry_tools_as_tooldefs(selected_names)
                    if t.name != ASK_USER_TOOL_NAME
                ]
        elif _unsupported_rail is not None:
            agent_tool_choice = "auto"

        # Reasoning-effort: "minimal" on every turn. The A/B against
        # "low" on Azure gpt-5.4-mini showed `minimal` (mapped to
        # `none` on the wire by LLMAzureOpenAI._translate_reasoning_effort)
        # strictly dominated `low` — higher hit-rate, ~30% lower p50,
        # half the output tokens (no reasoning trace billed). On
        # OpenAI's gpt-5 family `minimal` is the cheapest accepted
        # value and the same dominance holds in practice. Bumping to
        # "low"/"medium"/"high" universally is the wrong direction
        # — for Pivot's tool-heavy turns, extra reasoning tokens
        # mostly bias the model toward asking clarification questions
        # instead of just calling the right tool.
        effort: ReasoningEffort = "minimal"
        max_output: int = 1500
        # R5: per-reply-class budget. Explainer asks need 2400 tokens to
        # cover headed/bulleted depth; capability and small_talk get
        # tighter caps. The class also drives a system hint injected
        # below so the model knows the target shape, not just the size.
        reply_class = _classify_reply_class(message, intent_kind)
        _budget_tokens, reply_class_hint_text = _REPLY_BUDGETS.get(
            reply_class, _REPLY_BUDGETS["analytical_short"]
        )
        # GAN R2 R1/R8: append a screen/trend sub-hint to the analysis
        # directive so screens render ranked tables and index-trend reads
        # render SMA %-distance, not raw levels.
        if reply_class == "analysis":
            _sub = _analysis_subhint(message)
            if _sub:
                reply_class_hint_text = reply_class_hint_text + _sub
        max_output = _budget_tokens
        # Scoped retry budget for propose_workflow only — see the
        # documented escape hatch at the bottom of the Change-1 plan.
        # propose_workflow's failures are usually mechanical (unknown
        # step_type, "step 0 must be trigger.*") that the model fixes
        # on a single retry. All other tools stay single-shot.
        propose_workflow_attempts = 0
        _PROPOSE_WORKFLOW_MAX_ATTEMPTS = 2
        trace.event(
            "tool_router.select",
            n_selected=len(tooldefs),
            names=sorted([t.name for t in tooldefs])[:12],
            cache_key=cache_key,
            reasoning_effort=effort,
            max_output_tokens=max_output,
            tool_choice=agent_tool_choice,
            agent_intent=is_agent_intent,
            underspec_agent=is_underspec_agent,
            reply_class=reply_class,
        )

        prompt_ctx = _build_user_context(ctx)
        # Follow-up nudge: when the last assistant turn was a
        # clarification (ends with `?`), the user's current message is
        # answering it. Without this hint the model often re-plans the
        # whole turn from scratch — observed: a "2" reply taking 25s
        # because the model walks the full reasoning loop again. With
        # the hint, it merges the answer into the prior intent and
        # emits the tool directly. Cheap nudge, big latency win.
        #
        # Stronger v2: include the original user request inline (not
        # just "earlier intent") so the model can't lose the load-
        # bearing context. After the clarifying answer, the model has
        # everything it needs — failing to emit a tool here is the
        # single most-reported failure shape ("user already told me X
        # but now I'm asking again").
        followup_hint: Optional[LLMMessage] = None
        original_intent: Optional[str] = None

        # ── Active draft eviction (independent prompt) ────────────
        # Always check the cached draft against the new message —
        # independent of whether the prior turn was a clarification.
        # Without this, "pros and cons of Reliance" three turns after
        # building an HDFCBANK agent inherits the stale draft.
        active = self.store.get_active_draft(conv_id)
        if active is not None and _is_independent_prompt(message):
            self.store.clear_active_draft(conv_id)
            trace.event("active_draft.evicted",
                        reason="independent_prompt",
                        tool=active.tool_name)
            active = None

        # Build the workflow-hint payload once, reused below.
        # WHY extended to all macro tools: previously only "propose_workflow"
        # was handled here, so "make it 5 shares" after a propose_threshold_order
        # draft got no amendment hint → LLM produced prose instead of re-emitting
        # the tool. Now any active macro-draft type (threshold, scheduled, etc.)
        # triggers the hint, naming the CORRECT tool to re-emit.
        workflow_hint = ""
        if active is not None and active.tool_name in _MACRO_AMENDMENT_TOOLS:
            draft_json = json.dumps(active.draft, default=str)[:1800]
            tool_label = active.tool_name
            hint_verb = (
                "Re-emit propose_workflow with the SAME steps shape, only "
                "mutating the field(s) the user addressed. If the user is "
                "clearly proposing a wholly different agent, supersede."
                if tool_label == "propose_workflow" else
                # F&O P1: the options amendment needs the strongest verb —
                # the live eval showed the generic one still produced
                # "Should I re-emit it now?" ASK_USER confirmations.
                "Call build_option_strategy IMMEDIATELY with the draft's "
                "underlying/template/expiry, applying the user's change to "
                "`strikes` (array, leg order), `qty_lots` or `expiry`. "
                "NEVER ask to confirm an amendment — apply it; the card "
                "re-renders with fresh numbers and the user registers from "
                "the card."
                if tool_label == "build_option_strategy" else
                f"Re-emit `{tool_label}` with ALL parameters from the draft, "
                "only updating the field(s) the user changed. Do NOT switch to "
                "a different tool (e.g. do NOT call propose_workflow instead)."
            )
            # GAN R2 R7: rupee-notional resize ("12000 ka kharido", "make
            # it ₹12,000 worth"). The model must CONVERT notional→shares
            # using the live price, not punt to manual editing, and must
            # never narrate "Updated" if the quantity didn't change.
            _resize_clause = ""
            if _is_rupee_notional_resize(message):
                _resize_clause = (
                    " RUPEE-NOTIONAL RESIZE: the user gave a ₹ amount, not a "
                    "share count. FIRST call `get_live_price` for the draft's "
                    "symbol, compute quantity = round(amount / live_price), "
                    "then re-emit the draft with the NEW quantity. Do NOT ask "
                    "the user to edit the card manually. Do NOT say 'Updated' "
                    "unless the quantity actually changed. Lead your reply "
                    "with the arithmetic: '₹<amount> ÷ ~₹<price> = <qty> "
                    "shares.'"
                )
            workflow_hint = (
                f" ACTIVE {tool_label.upper().replace('_', ' ')} DRAFT from "
                f"a prior turn. Treat the user's reply as an AMENDMENT — "
                + hint_verb + _resize_clause +
                " Do NOT switch tools. Do NOT write prose. Do NOT call "
                "ASK_USER for non-essential fields (approval, defaults, "
                "stop-loss style) — the user can edit those on the card. "
                "The card is the confirmation surface. "
                f"DRAFT JSON: {draft_json}."
            )

        # For amendment turns with an active macro draft, force tool_choice
        # so the LLM MUST call the tool instead of describing the change.
        # WHY: "make it 5 shares" after propose_threshold_order was producing
        # prose ("I've updated the draft") with no actual tool call — the draft
        # card on the FE never changed. tool_choice="required" on hop 1
        # prevents that prose-only response.
        # GAN R2 R7: a Hinglish / rupee-notional resize is also an amendment
        # — force the tool even when the English amend-verb regex misses it.
        if (not is_agent_intent
                and active is not None
                and workflow_hint
                and (_DEPENDENT_INTENT_RE.search(message)
                     or _is_rupee_notional_resize(message))):
            agent_tool_choice = "required"
            # Resize needs the live price in scope to compute shares.
            if (_is_rupee_notional_resize(message)
                    and selected_names is not None
                    and "get_live_price" not in selected_names):
                selected_names = selected_names | {"get_live_price"}
                tooldefs = _registry_tools_as_tooldefs(selected_names)
                cache_key = cache_key_for(selected_names)

        # GAN R2 R6: a confusion-after-menu turn is NOT a clarification
        # answer — the user is asking us to explain, not picking an option.
        # Suppress the tool-forcing followup hint and pin tool_choice=auto
        # so the TEACH guard governs (no re-dumped menu, no forced emit).
        if _confusion_menu:
            agent_tool_choice = "auto"

        if (history and _looks_like_clarification_followup(history)
                and not _confusion_menu):
            # CLARIFICATION-FOLLOWUP path — the user is answering a
            # question we asked. Carry the original intent forward.
            last_assistant = next(
                (h for h in reversed(history)
                 if isinstance(h, dict) and h.get("role") == "assistant"),
                None,
            )
            last_text = (last_assistant or {}).get("content") or ""
            # First user message in history = the original ask.
            first_user = next(
                (h for h in history
                 if isinstance(h, dict) and h.get("role") == "user"),
                None,
            )
            original_intent = (first_user or {}).get("content") or ""
            followup_hint = LLMMessage(
                role="system",
                content=(
                    "FOLLOW-UP TURN. The user is answering your "
                    f"clarifying question. Their ORIGINAL request was: "
                    f'"{original_intent[:280]}". Their LAST clarification '
                    f'asked: "{last_text[-200:]}". Their CURRENT reply '
                    f'is: "{message}".'
                    + workflow_hint +
                    " Merge the reply into the original request and call "
                    "the matching tool (propose_workflow / "
                    "propose_dsl_workflow / backtest_workflow / "
                    "place_market_order / etc.) "
                    "IMMEDIATELY with the complete arguments.\n\n"
                    + (
                        "If the ORIGINAL request was a BACKTEST (compare "
                        "strategies, SIP vs lump sum, 'how would X have "
                        "performed'), you MUST call backtest_workflow — NOT "
                        "propose_workflow — and report the winner and by how "
                        "much.\n\n"
                        if _BACKTEST_INTENT_RE.search(original_intent) else ""
                    )
                    + (
                        "The user is DELEGATING the choice to you — do NOT "
                        "re-ask; pick the single most sensible option and "
                        "emit the tool.\n\n"
                        if _is_delegation_reply(message) else ""
                    )
                    + "CRITICAL — when the original request referenced a "
                    "placeholder (resistance / support / pivot / 'a level' "
                    "/ 'a threshold') AND the user's reply names what to "
                    "use, the tool's `condition` / `threshold` arg MUST "
                    "be the ORIGINAL phrasing with the placeholder "
                    "SUBSTITUTED by the reply value. Example: original "
                    "'buy HDFCBANK if it closes above resistance' + reply "
                    "'use 20-day rolling high' → "
                    "condition='close above the 20-day rolling high' "
                    "(NOT condition='stock closes above resistance' — "
                    "the abstract placeholder must be replaced).\n\n"
                    "For OPTIONAL tool fields the user did not mention "
                    "(exit_condition, sl_pct, valid_until, limit_price), "
                    "OMIT them entirely — do NOT pass null, 'none', "
                    "'n/a', or an empty string.\n\n"
                    "Do NOT restart from scratch. Do NOT ask another "
                    "question. Do NOT paraphrase back as 'Confirm: …'. "
                    "Do NOT ignore the original request. If the merged "
                    "request still has missing required fields, fill the "
                    "optional ones with sensible defaults (exchange=NSE, "
                    "order_type=market). If a share count or rupee budget was "
                    "never given, call ASK_USER for it — do NOT default the "
                    "quantity to 1."
                ),
            )
        elif active is not None and workflow_hint:
            # AMENDMENT path — the prior turn wasn't a clarification but
            # a macro draft is on screen and the user is mutating it.
            # WHY: LLM defaulted to text "do you want me to place…?"
            # instead of re-emitting the tool. The hint + required
            # tool_choice (set above) together fix this.
            tool_label = active.tool_name
            followup_hint = LLMMessage(
                role="system",
                content=(
                    f"AMENDMENT TURN. A `{tool_label}` draft is on screen. "
                    f"The user's CURRENT message is \"{message}\" — interpret "
                    "it as a mutation of THAT draft and re-emit "
                    f"`{tool_label}` with the same structure, only the "
                    "changed fields updated. "
                    + workflow_hint +
                    " Re-emit the tool IMMEDIATELY. Do NOT respond with "
                    "prose like 'Do you want me to…?' or 'Confirm: …' — "
                    "the freshly emitted card is the confirmation surface."
                ),
            )

        # Prompt-cache layout: static prefix FIRST (role rules +
        # calibration examples + domain primer — same bytes on every
        # turn for this route), per-user/per-turn payloads SECOND.
        # OpenAI's prompt cache is keyed on prefix bytes, so anything
        # that changes turn-to-turn (portfolio totals, the followup
        # hint with the user's reply, the rolling history) MUST come
        # after the cached static block. Previously user_context was
        # baked into the first system message — every portfolio
        # number change invalidated cache for that user.
        base_messages: list[LLMMessage] = [
            LLMMessage(
                role="system",
                content=build_system_prompt(role="chat", user_context=None),
            ),
        ]
        if prompt_ctx is not None:
            uc_block = _format_user_context_block(prompt_ctx)
            if uc_block:
                base_messages.append(LLMMessage(role="system", content=uc_block))
        # R5: per-class length+format directive. Empty string for draft/
        # automation/backtest (tool-driven turns) — append only when the
        # class actually carries a hint.
        if reply_class_hint_text:
            base_messages.append(
                LLMMessage(role="system", content=reply_class_hint_text)
            )
        # GAN R2 R2–R6: deterministic guard directives (named-option build,
        # notify-only alert, at-open/close, unsupported rail, confusion).
        for _g in _deterministic_guards:
            base_messages.append(LLMMessage(role="system", content=_g))
        # R1: affirmation with no draft + no pending resolution. Prevents
        # the model from fabricating "the draft above is what you'll
        # activate" when there is no draft on screen (screenshot 10).
        if _affirm_no_state:
            base_messages.append(LLMMessage(
                role="system",
                content=(
                    "## Affirmation with no active state\n"
                    "The user replied with a bare affirmative ('yes', "
                    "'sure', 'ok'). There is NO active workflow draft "
                    "on screen AND no structured pending resolution. "
                    "Do NOT pretend a draft exists. Do NOT say 'the "
                    "draft above is what you'll activate'. Do NOT "
                    "invent a previous failure or a prior plan to "
                    "retry. Interpret the affirmative as confirming "
                    "your last assistant message — if that was a "
                    "general suggestion, briefly act on it; if it was "
                    "small talk, reply briefly; if you're not sure "
                    "what they're agreeing to, ask one focused "
                    "follow-up question."
                ),
            ))
        # R3 micro: structured-resolution hint when a PendingResolution
        # exists and the user replied with something other than "yes".
        if pending_resolution_hint_text:
            base_messages.append(LLMMessage(
                role="system",
                content=pending_resolution_hint_text,
            ))
        # Mode pin — explicit user pill from the FE composer. This is
        # treated as a HARD route: the user clicked Automation, so the
        # LLM must place an order, not draft a workflow. Without this
        # message the BE-side tool narrowing alone wasn't enough — the
        # model could still answer in prose or pull a workflow draft
        # from the prior turn's history.
        mode_pin = _format_mode_pin(mode_override)
        if mode_pin:
            base_messages.append(LLMMessage(role="system", content=mode_pin))
        if followup_hint is not None:
            base_messages.append(followup_hint)

        # WHY this directive: when we stripped macro tools because the
        # request is underspec / filler, the model would fall back to
        # describing a "draft" in plain prose ("Name: ... Trigger: ...
        # Action: ..."). That's worse than fabricating a real card —
        # the user sees agent-shaped text but no Activate button, no
        # editable fields, and no commitment surface. This hard
        # directive tells the model: in this state, ASK_USER is the
        # ONLY correct action.
        if is_underspec_agent or is_filler_after_q:
            base_messages.append(LLMMessage(
                role="system",
                content=(
                    "## Underspec / filler reply — ASK_USER, do NOT "
                    "describe a draft\n"
                    "The user did not specify enough to draft a "
                    "workflow, AND macro draft tools have been "
                    "removed from your tool set for this turn. Do "
                    "NOT describe a draft in prose ('Name: ...', "
                    "'Trigger: ...', 'Action: ...'). Do NOT promise "
                    "a draft 'in the app' — there is no separate app "
                    "you're handing off to.\n\n"
                    "Call `ASK_USER` with ONE focused, specific "
                    "question. Examples:\n"
                    "- 'Want to start with a daily SIP of ₹1,000 in "
                    "ETERNAL?'\n"
                    "- 'Roughly what amount per trade — ₹500, "
                    "₹5,000, or larger?'\n"
                    "- 'Should it buy on a fixed schedule (e.g. every "
                    "Monday at 09:15) or wait for a price/RSI "
                    "trigger?'\n"
                    "Pick the simplest option as a suggestion the "
                    "user can confirm or change."
                ),
            ))
        # R4c: level-role prompt with no numeric anchor — never invent.
        if _is_ungrounded_level_prompt(message):
            base_messages.append(LLMMessage(
                role="system",
                content=(
                    "## Price-level role named without a number — "
                    "do NOT invent a value\n"
                    "The user named a price level by ROLE (resistance, "
                    "support, pivot, breakout, swing high/low, "
                    "Fibonacci, trendline) but supplied no numeric "
                    "value and no computable definition. NEVER guess "
                    "the level from training memory — those numbers "
                    "are stale and wrong.\n\n"
                    "Call `ASK_USER` with ONE focused question that "
                    "offers the user a concrete choice between:\n"
                    "  (a) a specific level the user names (e.g. "
                    "₹1,640),\n"
                    "  (b) a rolling N-day high/low (e.g. 'the 20-day "
                    "rolling high', backed by `fetch.rolling_high`),\n"
                    "  (c) a Donchian / Bollinger band component.\n"
                    "Phrase the question with a sensible default the "
                    "user can accept — e.g. 'Want me to use the 20-day "
                    "rolling high as the resistance level, or do you "
                    "have a specific ₹ value in mind?'"
                ),
            ))

        messages: list[LLMMessage] = [
            *base_messages,
            *_history_to_llm_messages(history),
            LLMMessage(role="user", content=message),
        ]

        tools_called: list[str] = []
        logiccard: Optional[dict] = None
        raw_data: dict = {}
        hop_index = 0
        # M1: When the LLM writes a free-form question (assistant text
        # ending with "?" / "do you want" / etc.) WITHOUT calling
        # ASK_USER, the chat layer pushes a "USE ASK_USER" directive
        # and forces one more hop. Flag prevents infinite recursion.
        ask_user_retry_used = False
        # Track whether the previous hop emitted a macro-draft tool —
        # used to shrink max_output on the post-draft prose hop in
        # compact mode (the FE already has the card; prose can be
        # one short line).
        last_was_macro_draft = False
        # Track the most recent tool error so the circuit-breaker
        # fallback can surface a specific reason instead of a generic
        # "I had trouble". The user's "internal step-format issue"
        # message was caused by the breaker swallowing this.
        last_tool_error: Optional[str] = None
        # ── find_tool lazy-load tracker ────────────────────────────
        # When the LLM calls `find_tool` on hop N, the handler returns
        # candidate tool names but does NOT execute them. Those names
        # may not be in the regex-routed `selected_names` set, so the
        # NEXT hop won't see their schemas unless we union them back in.
        # This set is per-turn (fresh `set()` per user message), never
        # persisted to Redis. Threaded into `selected_names` after
        # every find_tool success → tooldefs + cache_key rebuilt.
        loaded_extras: set[str] = set()
        # ── Loop (multi-tool only — no validation retry) ───────────
        # The loop is now exclusively for genuinely multi-tool turns:
        # tool A succeeds → model gets the next hop to chain tool B
        # or write the final reply. Tool errors return a deterministic
        # question and exit the function (no retry-against-the-model).
        while hop_index < _MAX_TOOL_CALLS:
            hop_index += 1
            # A1: only force tool_choice on the FIRST hop. Subsequent
            # hops carry tool results and must allow the model to emit
            # a final text response (otherwise the loop never exits).
            hop_tool_choice: Literal["auto", "required"] = (
                agent_tool_choice if hop_index == 1 else "auto"
            )
            # Compact-draft hop budget: when we just emitted a macro
            # draft tool, the next prose hop only needs ~50 words.
            hop_max_output = (
                _COMPACT_POST_MACRO_MAX_OUTPUT
                if (_COMPACT_DRAFTS and last_was_macro_draft)
                else max_output
            )
            trace.event("llm.call", hop=hop_index, reasoning_effort=effort,
                        tools_offered=len(tooldefs),
                        tool_choice=hop_tool_choice,
                        max_output_tokens=hop_max_output,
                        compact_post_macro=(_COMPACT_DRAFTS and last_was_macro_draft))
            try:
                response = await client.complete(
                    messages=messages,
                    tools=tooldefs,
                    tool_choice=hop_tool_choice,
                    max_output_tokens=hop_max_output,
                    reasoning_effort=effort,
                    temperature=0.2,
                    prompt_cache_key=cache_key,
                )
            except Exception as e:
                logger.warning(
                    "%s call failed at hop %d (%s); falling back",
                    client.provider_name, hop_index, type(e).__name__,
                )
                trace.event("llm.exception", hop=hop_index,
                            type=type(e).__name__)
                break
            breakdown[f"llm_hop_{hop_index}"] = response.latency_ms
            # Stash cache-hit token count alongside the hop latency so
            # _log_timing surfaces it without changing the log shape.
            if response.cached_tokens:
                breakdown[f"llm_hop_{hop_index}_cached"] = response.cached_tokens
            trace.event("llm.response", hop=hop_index,
                        finish_reason=response.finish_reason,
                        latency_ms=response.latency_ms,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        reasoning_tokens=response.reasoning_tokens,
                        cached_tokens=response.cached_tokens)

            if response.finish_reason == "error":
                logger.warning("LLM error finish at hop %d: %s",
                               hop_index, response.content)
                trace.event("turn.end", reason="llm_error")
                trace.end()
                return self._unavailable(turn_started, breakdown)

            if response.finish_reason != "tool_calls":
                # Final text — return it.
                text, sanitised = _post_process(response.content or "")
                if sanitised and text == _GENERIC_FALLBACK and tools_called:
                    text = _tool_summary_line(tools_called[-1], logiccard)
                    sanitised = False
                # M1: detect unstructured clarification prose. If the
                # text is question-shaped, ASK_USER was NOT called, and
                # no draft/order card was emitted, push a "USE ASK_USER"
                # directive and re-emit. Once-only retry.
                # GAN R2 R6: on a confusion-after-menu turn the reply is
                # INTENTIONALLY teaching prose ending in one yes/no — do
                # NOT coerce it back into an ASK_USER menu (that re-creates
                # the very menu the user was confused by).
                if (
                    not ask_user_retry_used
                    and not _confusion_menu
                    and _looks_like_unstructured_clarification(
                        text, tools_called, raw_data,
                    )
                ):
                    ask_user_retry_used = True
                    trace.event("ask_user.retry", text_preview=text[:120])
                    messages.append(LLMMessage(
                        role="assistant",
                        content=text,
                    ))
                    messages.append(LLMMessage(
                        role="system",
                        content=(
                            "## STRUCTURED ASK REQUIRED\n"
                            "Your previous reply was a clarification "
                            "question written as prose. The chat layer "
                            "requires ALL clarifications to go through "
                            "the `ASK_USER` tool so the next-turn "
                            "resolution path can fire deterministically. "
                            "Re-emit your question by calling ASK_USER "
                            "with: question (verbatim), options (if any "
                            "obvious choices), default_on_yes (the "
                            "single most likely answer). Do NOT write "
                            "the question as text again."
                        ),
                    ))
                    continue
                # Always ensure the assistant text describes any widget
                # that's about to render. Prevents a card-with-no-text
                # bubble that reads as a glitch in the chat.
                text = _ensure_widget_caption(
                    text,
                    tool_name=(tools_called[-1] if tools_called else ""),
                    logiccard=logiccard,
                    raw_data=raw_data,
                )
                self.store.append(conv_id, message, text)
                # Successful turn supersedes any stale pending state
                # (the prior clarification was abandoned or resolved
                # by the LLM path itself).
                self.store.clear_pending(conv_id)
                total = int((time.monotonic() - turn_started) * 1000)
                breakdown["total"] = total
                _log_timing(client.provider_name, message, total, breakdown,
                            tools=tools_called)
                trace.event("turn.end", total_ms=total,
                            tools_called=tools_called, reason="stop")
                trace.end()
                return ChatTurn(
                    response=text,
                    tools_called=tools_called,
                    logiccard=logiccard,
                    latency_ms=total,
                    sanitised=sanitised,
                    raw_data=raw_data,
                    latency_breakdown=breakdown,
                )

            # finish_reason == "tool_calls" — append assistant message
            # carrying the tool_calls, then run each.
            messages.append(LLMMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            ))

            for tc in response.tool_calls or []:
                trace.event("tool.invoke", tool=tc.get("name"),
                            args=tc.get("arguments"))
                guarded = await execute_with_completeness(
                    tc["name"],
                    tc.get("arguments") or {},
                    llm_client=client,
                    user_message=message,
                    kite_token=ctx.kite_token,
                    db=ctx.db,
                    user_id=ctx.user_id,
                    # [C1/C2] earlier user turns count toward "user named
                    # a qty" so the M2 guard doesn't re-ask on amendments.
                    qty_context=_recent_user_text(history),
                    # P1: pass the prior DSL draft so a non-structural
                    # amendment patches it in place (no notify-only collapse).
                    prior_dsl_draft=(
                        active.draft if (active is not None
                                         and active.tool_name == "propose_dsl_workflow")
                        else None
                    ),
                )
                breakdown[f"tool_{guarded.name}"] = (
                    breakdown.get(f"tool_{guarded.name}", 0) + guarded.latency_ms
                )
                trace.event("tool.result", tool=guarded.name,
                            success=guarded.success,
                            needs_clarification=guarded.needs_clarification,
                            error=guarded.error,
                            latency_ms=guarded.latency_ms)

                # Completeness or ASK_USER → surface immediately.
                # Persist the partial tool call so the user's next
                # reply can resume deterministically (Change 2).
                if guarded.needs_clarification and guarded.question:
                    self.store.append(conv_id, message, guarded.question)
                    self._maybe_set_pending(conv_id, guarded)
                    self._maybe_set_pending_resolution(
                        conv_id, message, guarded,
                    )
                    total = int((time.monotonic() - turn_started) * 1000)
                    breakdown["total"] = total
                    _log_timing(client.provider_name, message, total, breakdown,
                                tools=[guarded.name])
                    trace.event("turn.end", total_ms=total,
                                tools_called=[guarded.name],
                                reason="needs_clarification")
                    trace.end()
                    return ChatTurn(
                        response=guarded.question,
                        tools_called=[guarded.name],
                        raw_data={"_render_hint": "ask_user"},
                        latency_ms=total,
                        latency_breakdown=breakdown,
                    )

                # On success keep state and let the loop continue —
                # the model gets one more LLM hop to either chain
                # another tool (genuinely multi-tool turn) or write
                # the final reply. On error STOP: surface a
                # deterministic question and return. No retry hop
                # against the model — see Change 1 in this file's
                # docstring.
                if guarded.success:
                    tool_msg_content = _summarise_tool_result(guarded)
                    messages.append(LLMMessage(
                        role="tool",
                        tool_call_id=tc.get("id", f"call_{hop_index}"),
                        name=guarded.name,
                        content=tool_msg_content,
                    ))
                    if guarded.name not in tools_called:
                        tools_called.append(guarded.name)
                    if guarded.logiccard:
                        logiccard = guarded.logiccard
                    if guarded.data:
                        raw_data[guarded.name] = guarded.data
                    # Cache the active draft when any macro-draft tool
                    # succeeds so the next turn can amend it directly.
                    # WHY extended beyond propose_workflow: propose_threshold_order
                    # and propose_scheduled_order also produce editable draft
                    # cards. Without stashing them, "make it 5 shares" after a
                    # threshold order got no amendment hint — the LLM produced
                    # prose instead of calling propose_threshold_order again.
                    if guarded.name in _STASH_DRAFT_TOOLS and guarded.data:
                        self._stash_workflow_draft(
                            conv_id, guarded.data, tool_name=guarded.name,
                        )
                    # F&O P1: option strategy cards stash a COMPACT spec
                    # as build_option_strategy (full card payload blows
                    # the amendment hint's 1800-char draft budget).
                    elif guarded.name in _OPTION_CARD_TOOLS and guarded.data:
                        self._stash_workflow_draft(
                            conv_id, _option_draft_spec(guarded.data),
                            tool_name="build_option_strategy",
                        )
                    # Compact-mode tracker: any macro draft tool that
                    # succeeded means the FE will render the card; the
                    # NEXT hop's prose can be one short line.
                    # Extended to analytics tools — they return
                    # structured {value, interpretation} dicts the FE
                    # renders directly; restating them in 400 tokens
                    # of prose is pure waste.
                    if (guarded.name in _STASH_DRAFT_TOOLS
                            or guarded.name in _OPTION_CARD_TOOLS
                            or guarded.name in _COMPACT_PROSE_TOOLS):
                        last_was_macro_draft = True
                    # find_tool lazy-load: union the candidate tool
                    # names into `loaded_extras` so they show up on the
                    # next hop. Rebuild tooldefs + cache_key to reflect
                    # the widened surface (cache key MUST change — see
                    # cache_key_for docstring).
                    if guarded.name == "find_tool" and guarded.data:
                        new_extras: set[str] = set()
                        for m in (guarded.data.get("matches") or []):
                            n = (m or {}).get("name")
                            if isinstance(n, str) and n and n != "find_tool":
                                new_extras.add(n)
                        if new_extras - loaded_extras:
                            loaded_extras |= new_extras
                            if selected_names is None:
                                # No router narrowing was in effect — full
                                # catalog already visible. Nothing to widen.
                                pass
                            else:
                                selected_names = selected_names | loaded_extras
                                tooldefs = _registry_tools_as_tooldefs(
                                    selected_names,
                                )
                                cache_key = cache_key_for(selected_names)
                                trace.event(
                                    "find_tool.lazy_load",
                                    added=sorted(new_extras),
                                    total_extras=len(loaded_extras),
                                    new_tooldefs_count=len(tooldefs),
                                    cache_key=cache_key,
                                )
                    continue

                # Tool error path.
                #
                # propose_workflow: feed the error back ONCE so the
                # model can self-correct (mechanical fixes — unknown
                # step_type, step 0 isn't a trigger.*, etc.) — then
                # macro fallback, then deterministic question. All
                # other tools fail single-shot — no LLM retry.
                last_tool_error = f"{guarded.name}: {guarded.error}"
                # L12: when the tool error names a specific replacement
                # tool ("use propose_holding_action instead"), force
                # one retry hop with the named tool required. This
                # bridges the DSL early-bail → propose_holding_action
                # gap that the L08_21 / L10_01 trailing-SL probes
                # surfaced.
                target_tool = _redirect_target_for_failure(
                    guarded.name, guarded.error or "", message,
                )
                if target_tool and not last_was_macro_draft:
                    trace.event(
                        f"{guarded.name}.route_redirect",
                        target=target_tool,
                        error=(guarded.error or "")[:140],
                    )
                    messages.append(LLMMessage(
                        role="tool",
                        tool_call_id=tc.get("id", f"call_{hop_index}"),
                        name=guarded.name,
                        content=(
                            f"ERROR from {guarded.name}: "
                            f"{guarded.error or ''}\n\n"
                            f"You MUST call `{target_tool}` next "
                            "with arguments matching the user's "
                            "original request. Do NOT write prose. "
                            "Do NOT re-call the failed tool."
                        ),
                    ))
                    # Force the target tool into scope so the model
                    # can definitely emit it.
                    if selected_names is not None:
                        selected_names = selected_names | {target_tool}
                        tooldefs = _registry_tools_as_tooldefs(selected_names)
                        cache_key = cache_key_for(selected_names)
                    continue
                # backtest_workflow gets the same self-correction pass
                # as propose_workflow: the LLM sees the validation
                # error and tries once more before we fall to a user-
                # facing clarification. WHY: backtest_workflow uses the
                # same steps[] schema as propose_workflow, and the most
                # common failure mode (invalid step shape) is one the
                # LLM can self-fix in a single hop.
                if guarded.name in {"propose_workflow", "backtest_workflow"}:
                    propose_workflow_attempts += 1
                    if propose_workflow_attempts < _PROPOSE_WORKFLOW_MAX_ATTEMPTS:
                        # Append the error as a tool-result message and
                        # let the loop iterate — the model gets one
                        # more pass to fix the args.
                        tool_msg_content = _summarise_tool_result(guarded)
                        messages.append(LLMMessage(
                            role="tool",
                            tool_call_id=tc.get("id", f"call_{hop_index}"),
                            name=guarded.name,
                            content=tool_msg_content,
                        ))
                        trace.event(
                            f"{guarded.name}.retry",
                            attempt=propose_workflow_attempts,
                            error=(guarded.error or "")[:160],
                        )
                        continue
                if guarded.name == "propose_workflow":
                    # Out of retries — try macro fallback first.
                    fb_draft = _try_macro_fallback(message)
                    if fb_draft is not None:
                        fb_text = (
                            "I couldn't fit your full request into a "
                            "single workflow shape, so I've drafted a "
                            "simplified version you can edit. The "
                            "trigger has been set to manual — review the "
                            "steps and adjust the trigger before "
                            "activating."
                        )
                        self.store.append(conv_id, message, fb_text)
                        self.store.clear_pending(conv_id)
                        self._stash_workflow_draft(conv_id, fb_draft, fb_text)
                        total = int((time.monotonic() - turn_started) * 1000)
                        breakdown["total"] = total
                        _log_timing(
                            client.provider_name, message, total,
                            breakdown, tools=tools_called,
                            note="propose_workflow_macro_fallback",
                        )
                        trace.event(
                            "turn.end", total_ms=total,
                            tools_called=tools_called + ["propose_holding_action"],
                            reason="propose_workflow_macro_fallback",
                        )
                        trace.end()
                        return ChatTurn(
                            response=fb_text,
                            tools_called=tools_called + ["propose_holding_action"],
                            raw_data={
                                **fb_draft,
                                "propose_workflow": fb_draft,
                            },
                            latency_ms=total,
                            latency_breakdown=breakdown,
                        )

                # Build the user-facing question. For propose_workflow
                # we pass the user's original ask alongside the error
                # so the question can name the specific phrase that
                # didn't parse (NIFTY → NIFTYBEES, "buying power" →
                # supported via fetch.portfolio, etc.).
                question = _format_recoverable_failure_question(
                    tool_name=guarded.name,
                    error=guarded.error or "",
                    user_message=message,
                )
                # Generic fall-through → ask the LLM for a tailored,
                # prompt-aware clarification (vs. a hardcoded template).
                if question == _LLM_CLARIFY_SENTINEL:
                    question = await _llm_clarification(
                        client=client,
                        user_message=message,
                        tool_name=guarded.name,
                        error=guarded.error or "",
                        history=history,
                    )
                # WHY this varies the message: when the SAME generic
                # fallback would fire two turns in a row, repeating
                # the same canned question is dead UX.
                last_asst_text = next(
                    (
                        h.get("content", "")
                        for h in reversed(history)
                        if h.get("role") == "assistant"
                    ),
                    None,
                )
                if _is_repeat_fallback(question, last_asst_text):
                    question = _vary_repeat_fallback(message)
                self.store.append(conv_id, message, question)
                self.store.clear_pending(conv_id)
                total = int((time.monotonic() - turn_started) * 1000)
                breakdown["total"] = total
                _log_timing(
                    client.provider_name, message, total, breakdown,
                    tools=tools_called,
                    note=f"tool_error_no_retry:{guarded.name}",
                )
                trace.event(
                    "turn.end", total_ms=total, tools_called=tools_called,
                    reason="tool_error_no_retry", tool=guarded.name,
                    error=(guarded.error or "")[:120],
                )
                trace.end()
                return ChatTurn(
                    response=question,
                    tools_called=tools_called + [guarded.name],
                    raw_data={"_render_hint": "ask_user"},
                    latency_ms=total,
                    latency_breakdown=breakdown,
                )

            # back to top of loop — model now sees tool results

        # Circuit-breaker hit.
        logger.warning("agentic loop hit MAX_TOOL_CALLS=%d (last_err=%s)",
                       _MAX_TOOL_CALLS, last_tool_error)
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        if last_tool_error:
            msg = (
                "I couldn't finish that build — the workflow draft kept "
                f"failing validation. Last error: {last_tool_error[:240]}. "
                "Try rephrasing with the specific values you want."
            )
        else:
            msg = (
                "I needed to look up several things and got a bit lost. "
                "Could you ask again with more specifics?"
            )
        self.store.append(conv_id, message, msg)
        _log_timing(client.provider_name, message, total, breakdown,
                    tools=tools_called, note="hit_max_tool_calls")
        trace.event("turn.end", total_ms=total, tools_called=tools_called,
                    reason="circuit_breaker")
        trace.end()
        return ChatTurn(
            response=msg,
            tools_called=tools_called,
            raw_data={"_render_hint": "circuit_breaker"},
            latency_ms=total,
            latency_breakdown=breakdown,
        )

    # ── Streaming surface ─────────────────────────────────────────────
    #
    # `handle_stream` is the SSE-fronted twin of `handle`. It runs the
    # same agentic loop, but yields events as work progresses:
    #
    #   {"type": "start"}
    #   {"type": "tool_start", "name": "..."}              (per tool invoke)
    #   {"type": "tool_done",  "name": "...", "ok": bool}
    #   {"type": "delta",      "text": "..."}              (final-hop tokens)
    #   {"type": "done",       ...full ChatTurn payload}
    #   {"type": "error",      "message": "..."}
    #
    # Why `handle_stream` only streams the FINAL hop's text:
    #   - Intermediate hops carry tool_calls; their text content is
    #     usually empty. There's nothing for the user to read.
    #   - Tool execution is serial and 0–2s typically; the FE can show
    #     a "Running tool…" pill from `tool_start` until `tool_done`.
    #   - Streaming the final hop is where the perceived-latency win
    #     lives — first token within ~1s, full reply ~3-5s later.

    async def handle_stream(
        self,
        message: str,
        conv_id: str,
        ctx: UserContext,
        *,
        history_override: list[dict] | None = None,
        mode_override: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        from backend.llm.openai_client import LLMOpenAI, stream_openai
        from backend.services.turn_context import set_conversation_id

        turn_started = time.monotonic()
        set_conversation_id(conv_id)
        breakdown: dict[str, int] = {}
        trace = start_turn(conv_id, message)
        trace.event("turn.start.stream", message_preview=message[:120])

        yield {"type": "start"}

        # ── Fast path ──────────────────────────────────────────────
        fast_response = try_fast_path(message)
        if fast_response is not None:
            trace.event("fast_path.matched")
            self.store.append(conv_id, message, fast_response)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["fast_path"] = total
            breakdown["total"] = total
            yield {"type": "delta", "text": fast_response}
            yield {
                "type": "done",
                "response": fast_response,
                "tools_called": [],
                "logiccard": None,
                "raw_data": None,
                "latency_ms": total,
                "latency_breakdown": breakdown,
            }
            trace.end()
            return

        # ── Workflow skeleton fast-path ────────────────────────────
        skeleton = try_workflow_skeleton(message)
        if skeleton is not None:
            try:
                from backend.workflows.propose import (
                    ProposalValidationError, validate_draft_against_registry,
                )
                validate_draft_against_registry(skeleton)
            except ProposalValidationError:
                skeleton = None
        if skeleton is not None:
            trace.event(
                "workflow_skeleton.matched",
                workflow_name=skeleton.get("name"),
                step_types=[s["step_type"] for s in skeleton.get("steps") or []],
            )
            response_text = _workflow_skeleton_caption(skeleton)
            self.store.append(conv_id, message, response_text)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["workflow_skeleton"] = total
            breakdown["total"] = total
            yield {"type": "tool_start", "name": "propose_workflow"}
            yield {"type": "tool_done", "name": "propose_workflow", "ok": True}
            yield {"type": "delta", "text": response_text}
            # Streaming path: the chat router's `done` payload doesn't
            # go through the same hoist logic as the non-streaming
            # `/chat` POST — it ships raw_data verbatim. So here we
            # DO need to hoist the draft fields ourselves so the FE's
            # WorkflowDraftCard finds name/steps at the top level.
            stream_raw_data = {
                **skeleton,
                "propose_workflow": skeleton,
                "_render_hint": "workflow_draft_card",
            }
            yield {
                "type": "done",
                "response": response_text,
                "tools_called": ["propose_workflow"],
                "logiccard": None,
                "raw_data": stream_raw_data,
                "latency_ms": total,
                "latency_breakdown": breakdown,
            }
            _log_timing("workflow_skeleton", message, total, breakdown,
                        tools=["propose_workflow"], note="stream-skeleton")
            trace.event("turn.end", total_ms=total,
                        tools_called=["propose_workflow"], reason="skeleton")
            trace.end()
            return

        # ── Deterministic resume (Change 2 — streaming) ────────────
        # Same fast-resume gate as handle(); converts the resume's
        # ChatTurn output into the SSE event sequence the FE expects.
        resumed_turn = await self._try_fast_resume(
            message=message, conv_id=conv_id, ctx=ctx, trace=trace,
            turn_started=turn_started, breakdown=breakdown,
        )
        if resumed_turn is not None:
            yield {"type": "start"}
            for tname in resumed_turn.tools_called:
                yield {"type": "tool_start", "name": tname}
                yield {"type": "tool_done", "name": tname, "ok": True}
            if resumed_turn.response:
                yield {"type": "delta", "text": resumed_turn.response}
            yield {
                "type": "done",
                "response": resumed_turn.response,
                "tools_called": resumed_turn.tools_called,
                "logiccard": resumed_turn.logiccard,
                "raw_data": resumed_turn.raw_data or None,
                "latency_ms": resumed_turn.latency_ms,
                "latency_breakdown": resumed_turn.latency_breakdown,
            }
            return

        # Same cancel-active-draft gate as handle(). Streaming clients
        # see a quick start → done sequence with the cancel_draft tool
        # tag, no token deltas needed.
        cancelled_turn = self._try_cancel_active_draft(
            message=message, conv_id=conv_id, trace=trace,
            turn_started=turn_started, breakdown=breakdown,
        )
        if cancelled_turn is not None:
            yield {"type": "start"}
            yield {"type": "tool_start", "name": "cancel_draft"}
            yield {"type": "tool_done", "name": "cancel_draft", "ok": True}
            yield {"type": "delta", "text": cancelled_turn.response}
            yield {
                "type": "done",
                "response": cancelled_turn.response,
                "tools_called": cancelled_turn.tools_called,
                "logiccard": cancelled_turn.logiccard,
                "raw_data": cancelled_turn.raw_data or None,
                "latency_ms": cancelled_turn.latency_ms,
                "latency_breakdown": cancelled_turn.latency_breakdown,
            }
            return

        # ── Pure-affirmative fast-path (mirror of non-stream) ───────
        _affirm_no_state = False
        if _is_pure_affirmative(message):
            resolution = self.store.get_pending_resolution(conv_id)
            resolved_value = None
            if resolution is not None:
                if resolution.default_on_yes:
                    resolved_value = resolution.default_on_yes
                elif resolution.options:
                    resolved_value = resolution.options[0]
            if resolved_value:
                trace.event(
                    "pending_resolution.resolved",
                    resolved=resolved_value,
                    source=(
                        "default_on_yes"
                        if resolution.default_on_yes else "options[0]"
                    ),
                )
                self.store.clear_pending_resolution(conv_id)
                message = resolved_value
            else:
                existing = self.store.get_active_draft(conv_id)
                if existing is not None:
                    ack = (
                        "Got it — the draft above is what you'll activate. "
                        "Click **Save & activate** in the card when you're ready."
                    )
                    self.store.append(conv_id, message, ack)
                    total = int((time.monotonic() - turn_started) * 1000)
                    breakdown["affirm_ack"] = total
                    breakdown["total"] = total
                    _log_timing("affirm_ack", message, total, breakdown, tools=[])
                    trace.event("turn.end", total_ms=total, tools_called=[],
                                reason="pure_affirmative_ack")
                    trace.end()
                    yield {"type": "start"}
                    yield {"type": "delta", "text": ack}
                    yield {
                        "type": "done",
                        "response": ack,
                        "tools_called": [],
                        "logiccard": None,
                        "raw_data": None,
                        "latency_ms": total,
                        "latency_breakdown": breakdown,
                    }
                    return
                _affirm_no_state = True

        # ── R3 micro (streaming mirror): structured resolution hint ─
        pending_resolution_hint_text: str = ""
        pending_resolution_active = False
        if not _is_pure_affirmative(message):
            _pr = self.store.get_pending_resolution(conv_id)
            if _pr is not None and (_pr.question or _pr.options):
                pending_resolution_active = True
                opts_block = (
                    "Options: " + " | ".join(_pr.options) + "."
                    if _pr.options else ""
                )
                default_block = (
                    f"Default if user says 'yes': {_pr.default_on_yes}."
                    if _pr.default_on_yes else ""
                )
                original_block = (
                    f" Original intent: \"{_pr.original_intent[:200]}\"."
                    if _pr.original_intent else ""
                )
                # [C7] If the user DELEGATES ("suggest something", "you
                # decide"), do NOT re-ask the same menu — pick a default.
                _deleg_clause = (
                    "The user is DELEGATING the choice to you — do NOT "
                    "re-ask the same menu. Choose the single most sensible "
                    "option yourself (favour the simplest actionable "
                    "strategy, e.g. a momentum / moving-average or "
                    "threshold buy) and emit the tool with it. "
                    if _is_delegation_reply(message) else ""
                )
                # [C7] If the original request was a backtest, the emit
                # tool is backtest_workflow — say so explicitly.
                _bt_clause = (
                    "The ORIGINAL request is a BACKTEST — you MUST call "
                    "backtest_workflow (NOT propose_workflow) and report "
                    "the winner and by how much. "
                    if (_pr.original_intent
                        and _BACKTEST_INTENT_RE.search(_pr.original_intent))
                    else ""
                )
                pending_resolution_hint_text = (
                    "## Pending clarification (structured)\n"
                    f"You asked: \"{_pr.question}\". "
                    f"{opts_block} {default_block}{original_block} "
                    "The user's CURRENT message is their answer. "
                    + _deleg_clause + _bt_clause +
                    "Map it to one of the options if possible, then "
                    "EMIT the workflow / order / backtest tool IMMEDIATELY "
                    "with the resolved value substituted into the original "
                    "request. Do NOT re-ask. Do NOT write prose like "
                    "'Drafted: ...' without actually calling the tool. "
                    "If no tool exists for the merged request, ASK_USER "
                    "for the next missing piece — never write a fake "
                    "draft description."
                ).strip()
                self.store.clear_pending_resolution(conv_id)

        # ── Fresh-session eviction (mirror of non-streaming path) ──
        if history_override is not None and len(history_override) == 0:
            self._reset_session(conv_id)

        # ── Active-draft eviction (mirror of non-streaming path) ───
        had_active_draft_at_entry = (
            self.store.get_active_draft(conv_id) is not None
        )
        if _is_independent_prompt(message):
            stale = self.store.get_active_draft(conv_id)
            if stale is not None:
                self.store.clear_active_draft(conv_id)
                trace.event("active_draft.evicted",
                            reason="independent_prompt_top",
                            tool=stale.tool_name)

        # ── Mode-override eviction (mirror of non-streaming path) ──
        if mode_override in {"automation", "backtest"}:
            stale = self.store.get_active_draft(conv_id)
            if stale is not None:
                self.store.clear_active_draft(conv_id)
                trace.event("active_draft.evicted",
                            reason=f"mode_override:{mode_override}",
                            tool=stale.tool_name)

        # ── Agentic loop setup ─────────────────────────────────────
        # Window-cap mirror of handle().
        if history_override is not None:
            history = history_override[-(CONV_PROMPT_WINDOW_TURNS * 2):]
        else:
            history = self.store.get_history(
                conv_id, limit=CONV_PROMPT_WINDOW_TURNS,
            )

        client = self._client()
        # Streaming is gated on the LLMOpenAI / LLMAzureOpenAI client
        # surface; on any other client we degrade to the non-streaming
        # `handle()` and emit the result as one delta.
        can_stream = isinstance(client, LLMOpenAI)

        if not can_stream:
            turn = await self.handle(
                message, conv_id, ctx,
                history_override=history_override,
                mode_override=mode_override,
            )
            yield {"type": "delta", "text": turn.response}
            yield {
                "type": "done",
                "response": turn.response,
                "tools_called": turn.tools_called,
                "logiccard": turn.logiccard,
                "raw_data": turn.raw_data or None,
                "latency_ms": turn.latency_ms,
                "latency_breakdown": turn.latency_breakdown,
            }
            return

        selected_names = select_tool_names(message)
        intent_kind = _classify_intent(message)

        # Typo-continuation guard (mirror of non-streaming path).
        # See _is_bare_typo_continuation for full rationale.
        if (had_active_draft_at_entry
                and selected_names is not None
                and _is_bare_typo_continuation(message)
                and not _DEPENDENT_INTENT_RE.search(message)):
            selected_names = selected_names - _ORDER_AND_MACRO_TOOLS
            trace.event(
                "tools.stripped_typo_continuation",
                stripped=sorted(_ORDER_AND_MACRO_TOOLS),
                reason="bare_token_with_active_draft",
            )

        # Mirror of non-streaming post-order-clarification override.
        if intent_kind == "other" and _is_post_order_clarification(message, history):
            intent_kind = "automation"
            trace.event("intent.post_clarification_order_override")
        if mode_override in {"automation", "agent", "backtest"}:
            intent_kind = mode_override
            trace.event("mode_override.applied", mode=mode_override)
        is_agent_intent = intent_kind == "agent"
        is_automation_intent = intent_kind == "automation"
        is_backtest_intent = intent_kind == "backtest"
        # Streaming mirror of the non-streaming intent routing.
        # See handle() for the full rationale.
        _IMMEDIATE_ORDER_TOOLS = frozenset({
            "place_market_order", "place_limit_order",
            "create_gtt_order", "create_sl_order", "create_oco_order",
            "create_dip_buy", "place_basket_order",
            "create_sip", "squareoff_all_intraday", "squareoff_symbol",
        })
        if is_agent_intent and selected_names is not None:
            selected_names = (selected_names - _IMMEDIATE_ORDER_TOOLS) | {
                "propose_workflow",
            }
        elif is_automation_intent and selected_names is not None:
            # Mirror of non-streaming path. See comment there for WHY all
            # four macro tools are removed, not just propose_workflow.
            _ALL_MACRO_TOOLS = frozenset({
                "propose_workflow", "propose_scheduled_order",
                "propose_threshold_order", "propose_basket_allocation",
                "propose_holding_action",
            })
            selected_names = (selected_names - _ALL_MACRO_TOOLS) | (
                _IMMEDIATE_ORDER_TOOLS
            )
        elif is_backtest_intent and selected_names is not None:
            # Mirror of the non-streaming backtest-pill narrowing. See
            # handle() for WHY run_backtest is excluded.
            selected_names = (
                (
                    selected_names
                    - _IMMEDIATE_ORDER_TOOLS
                    - {"propose_workflow", "run_backtest"}
                )
                | {"backtest_workflow", "get_price_history",
                   "get_live_price", "get_52wk_range"}
            )
        # Mirror of non-streaming advisory-strip — see handle() for WHY.
        if (intent_kind == "other"
                and selected_names is not None
                and _ADVISORY_INTENT_RE.search(message)
                and not _ADVISORY_WORKFLOW_EXCEPTION_RE.search(message)):
            _ALL_MACRO_TOOLS = frozenset({
                "propose_workflow", "propose_scheduled_order",
                "propose_threshold_order", "propose_basket_allocation",
                "propose_holding_action",
            })
            selected_names = selected_names - _ALL_MACRO_TOOLS
        tooldefs = _registry_tools_as_tooldefs(selected_names)
        cache_key = cache_key_for(selected_names)
        # A1 + B4 (mirror of non-streaming path): when the message
        # signals "build me an agent", lock tool_choice to required
        # and drop reasoning_effort to minimal. See _looks_like_agent_intent.
        agent_tool_choice: Literal["auto", "required"] = (
            "required" if is_agent_intent else "auto"
        )
        # Streaming mirror: force tool emit when a PendingResolution
        # is active (see handle() for rationale).
        if pending_resolution_active:
            agent_tool_choice = "required"
            if selected_names is not None:
                selected_names = selected_names | frozenset({
                    "ASK_USER", "propose_workflow",
                    "propose_threshold_order", "propose_scheduled_order",
                    "propose_dsl_workflow", "propose_holding_action",
                    "propose_basket_allocation",
                })
                tooldefs = _registry_tools_as_tooldefs(selected_names)
                cache_key = cache_key_for(selected_names)
        # Underspec relaxation (mirror of non-streaming path).
        # Two-step enforcement: relax tool_choice + strip macros so
        # propose_workflow can't fabricate defaults from history.
        # Plus filler-reply-after-question, F&O detection, and
        # buy/sell contradiction (same class — strip + force ASK).
        is_underspec_agent = is_agent_intent and (
            _is_underspecified_agent_build(message)
            or _is_ungrounded_level_prompt(message)
        )
        is_filler_after_q = (
            _is_filler_reply(message) and _prev_assistant_was_question(history)
        )
        mentions_fno = _mentions_fno(message)
        is_contradiction = _is_buy_sell_contradiction(message)
        if is_underspec_agent or is_filler_after_q or mentions_fno or is_contradiction:
            agent_tool_choice = "auto"
            if selected_names is not None:
                _UNDERSPEC_STRIP = frozenset({
                    "propose_workflow", "propose_scheduled_order",
                    "propose_threshold_order", "propose_basket_allocation",
                    "propose_holding_action",
                })
                if mentions_fno:
                    _UNDERSPEC_STRIP = _UNDERSPEC_STRIP | frozenset({
                        "place_market_order", "place_limit_order",
                        "create_gtt_order", "create_sl_order",
                        "create_oco_order", "create_dip_buy",
                        "place_basket_order", "create_sip",
                    })
                selected_names = selected_names - _UNDERSPEC_STRIP
                if mentions_fno:
                    # P1: surface the options tools (mirror of handle()).
                    selected_names = selected_names | _OPTIONS_TOOLS
                tooldefs = _registry_tools_as_tooldefs(selected_names)
                cache_key = cache_key_for(selected_names)

        # ── GAN R2 deterministic guards (R2–R6) — mirror of handle() ────
        _deterministic_guards = _build_deterministic_guards(message, history)
        _named_option_build = _is_named_option_build(message)
        _notify_only = _is_notify_only_alert(message)
        _at_open_close = _is_at_open_close_build(message)
        _confusion_menu = _is_confusion_after_menu(message, history)
        _unsupported_rail = _names_unsupported_rail(message)
        if _named_option_build and selected_names is not None:
            selected_names = (selected_names | _OPTIONS_TOOLS) - frozenset({
                "place_market_order", "place_limit_order",
                "create_gtt_order", "suggest_option_strategy",
                "critique_option_strategy",
            })
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            agent_tool_choice = "required"
        elif _notify_only and selected_names is not None:
            selected_names = (selected_names | frozenset({
                "propose_dsl_workflow",
            })) - frozenset({
                "place_market_order", "place_limit_order",
                "create_gtt_order", "create_sl_order",
            })
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            agent_tool_choice = "required"
        elif _at_open_close and selected_names is not None:
            selected_names = selected_names | frozenset({
                "propose_dsl_workflow", "propose_workflow",
            })
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            agent_tool_choice = "required"
        if _confusion_menu:
            agent_tool_choice = "auto"
            if selected_names is not None:
                tooldefs = [
                    t for t in _registry_tools_as_tooldefs(selected_names)
                    if t.name != ASK_USER_TOOL_NAME
                ]
        elif _unsupported_rail is not None:
            agent_tool_choice = "auto"

        # Stream path matches the non-stream `handle()` decision —
        # "minimal" on every turn (see commentary there).
        effort: ReasoningEffort = "minimal"
        max_output: int = 1500
        # R5: mirror of non-streaming reply-class budget.
        reply_class = _classify_reply_class(message, intent_kind)
        _budget_tokens, reply_class_hint_text = _REPLY_BUDGETS.get(
            reply_class, _REPLY_BUDGETS["analytical_short"]
        )
        # GAN R2 R1/R8: screen/trend sub-hint on the analysis class.
        if reply_class == "analysis":
            _sub = _analysis_subhint(message)
            if _sub:
                reply_class_hint_text = reply_class_hint_text + _sub
        max_output = _budget_tokens
        # Same scoped retry budget as the non-streaming path.
        propose_workflow_attempts = 0
        _PROPOSE_WORKFLOW_MAX_ATTEMPTS = 2
        trace.event(
            "tool_router.select",
            n_selected=len(tooldefs),
            names=sorted([t.name for t in tooldefs])[:12],
            cache_key=cache_key,
            reasoning_effort=effort,
            tool_choice=agent_tool_choice,
            agent_intent=is_agent_intent,
            underspec_agent=is_underspec_agent,
            reply_class=reply_class,
        )

        prompt_ctx = _build_user_context(ctx)
        # Stronger follow-up nudge — mirror of the non-streaming path.
        # Carries the original user request inline so the model can't
        # treat the answer as a fresh prompt.
        followup_hint_msg: Optional[LLMMessage] = None

        # Same eviction + amend-vs-clarify split as the non-streaming
        # path — see handle() for full rationale.
        active = self.store.get_active_draft(conv_id)
        if active is not None and _is_independent_prompt(message):
            self.store.clear_active_draft(conv_id)
            trace.event("active_draft.evicted",
                        reason="independent_prompt",
                        tool=active.tool_name)
            active = None
        # Mirror of non-streaming workflow_hint — extended to all macro
        # draft types (propose_threshold_order, propose_scheduled_order, etc.).
        # See handle() for WHY.
        workflow_hint = ""
        if active is not None and active.tool_name in _MACRO_AMENDMENT_TOOLS:
            draft_json = json.dumps(active.draft, default=str)[:1800]
            tool_label = active.tool_name
            hint_verb = (
                "Re-emit propose_workflow with the SAME steps shape, only "
                "mutating the field(s) the user addressed. If the user is "
                "clearly proposing a wholly different agent, supersede."
                if tool_label == "propose_workflow" else
                # F&O P1 options amendment — strongest verb (see handle()).
                "Call build_option_strategy IMMEDIATELY with the draft's "
                "underlying/template/expiry, applying the user's change to "
                "`strikes` (array, leg order), `qty_lots` or `expiry`. "
                "NEVER ask to confirm an amendment — apply it; the card "
                "re-renders with fresh numbers and the user registers from "
                "the card."
                if tool_label == "build_option_strategy" else
                f"Re-emit `{tool_label}` with ALL parameters from the draft, "
                "only updating the field(s) the user changed. Do NOT switch to "
                "a different tool (e.g. do NOT call propose_workflow instead)."
            )
            # GAN R2 R7 (streaming mirror): rupee-notional resize.
            _resize_clause = ""
            if _is_rupee_notional_resize(message):
                _resize_clause = (
                    " RUPEE-NOTIONAL RESIZE: the user gave a ₹ amount, not a "
                    "share count. FIRST call `get_live_price` for the draft's "
                    "symbol, compute quantity = round(amount / live_price), "
                    "then re-emit the draft with the NEW quantity. Do NOT ask "
                    "the user to edit the card manually. Do NOT say 'Updated' "
                    "unless the quantity actually changed. Lead your reply "
                    "with the arithmetic: '₹<amount> ÷ ~₹<price> = <qty> "
                    "shares.'"
                )
            workflow_hint = (
                f" ACTIVE {tool_label.upper().replace('_', ' ')} DRAFT from "
                f"a prior turn. Treat the user's reply as an AMENDMENT — "
                + hint_verb + _resize_clause +
                " Do NOT switch tools. Do NOT write prose. Do NOT call "
                "ASK_USER for non-essential fields (approval, defaults, "
                "stop-loss style) — the user can edit those on the card. "
                "The card is the confirmation surface. "
                f"DRAFT JSON: {draft_json}."
            )

        # Force tool_choice="required" on amendment turns — see handle().
        # GAN R2 R7: a Hinglish / rupee-notional resize is also an amendment.
        if (not is_agent_intent
                and active is not None
                and workflow_hint
                and (_DEPENDENT_INTENT_RE.search(message)
                     or _is_rupee_notional_resize(message))):
            agent_tool_choice = "required"
            if (_is_rupee_notional_resize(message)
                    and selected_names is not None
                    and "get_live_price" not in selected_names):
                selected_names = selected_names | {"get_live_price"}
                tooldefs = _registry_tools_as_tooldefs(selected_names)
                cache_key = cache_key_for(selected_names)

        # GAN R2 R6 (streaming mirror): confusion-after-menu → TEACH, not
        # a forced clarification answer.
        if _confusion_menu:
            agent_tool_choice = "auto"

        if (history and _looks_like_clarification_followup(history)
                and not _confusion_menu):
            last_assistant = next(
                (h for h in reversed(history)
                 if isinstance(h, dict) and h.get("role") == "assistant"),
                None,
            )
            last_text = (last_assistant or {}).get("content") or ""
            first_user = next(
                (h for h in history
                 if isinstance(h, dict) and h.get("role") == "user"),
                None,
            )
            original_intent = (first_user or {}).get("content") or ""
            followup_hint_msg = LLMMessage(
                role="system",
                content=(
                    "FOLLOW-UP TURN. The user is answering your "
                    "clarifying question. Their ORIGINAL request was: "
                    f'"{original_intent[:280]}". Their LAST clarification '
                    f'asked: "{last_text[-200:]}". Their CURRENT reply '
                    f'is: "{message}".'
                    + workflow_hint +
                    " Merge the reply into the original request and call "
                    "the matching tool (propose_workflow / "
                    "propose_dsl_workflow / backtest_workflow / "
                    "place_market_order / etc.) IMMEDIATELY with the "
                    "complete arguments. "
                    + (
                        "If the ORIGINAL request was a BACKTEST, you MUST "
                        "call backtest_workflow (NOT propose_workflow) and "
                        "report the winner and by how much. "
                        if _BACKTEST_INTENT_RE.search(original_intent) else ""
                    )
                    + (
                        "The user is DELEGATING the choice — do NOT re-ask; "
                        "pick the most sensible option and emit the tool. "
                        if _is_delegation_reply(message) else ""
                    )
                    + "Do NOT restart from scratch. "
                    "If the merged request still has missing required "
                    "fields, fill the optional ones with sensible defaults "
                    "(exchange=NSE, order_type=market). If a share count or "
                    "rupee budget was never given, call ASK_USER for it — do "
                    "NOT default the quantity to 1."
                ),
            )
        elif active is not None and workflow_hint:
            tool_label = active.tool_name
            followup_hint_msg = LLMMessage(
                role="system",
                content=(
                    f"AMENDMENT TURN. A `{tool_label}` draft is on screen. "
                    f"The user's CURRENT message is \"{message}\" — interpret "
                    "it as a mutation of THAT draft and re-emit "
                    f"`{tool_label}` with the same structure, only the "
                    "changed fields updated. "
                    + workflow_hint +
                    " Re-emit the tool IMMEDIATELY. Do NOT respond with "
                    "prose like 'Do you want me to…?' or 'Confirm: …' — "
                    "the freshly emitted card is the confirmation surface."
                ),
            )

        # Same static-first layout as handle() — see comments there.
        base_msgs: list[LLMMessage] = [
            LLMMessage(
                role="system",
                content=build_system_prompt(role="chat", user_context=None),
            ),
        ]
        if prompt_ctx is not None:
            uc_block = _format_user_context_block(prompt_ctx)
            if uc_block:
                base_msgs.append(LLMMessage(role="system", content=uc_block))
        # R5: per-class length+format directive (streaming mirror).
        if reply_class_hint_text:
            base_msgs.append(
                LLMMessage(role="system", content=reply_class_hint_text)
            )
        # GAN R2 R2–R6: deterministic guard directives (streaming mirror).
        for _g in _deterministic_guards:
            base_msgs.append(LLMMessage(role="system", content=_g))
        # R1: affirmation-no-state hint (streaming mirror).
        if _affirm_no_state:
            base_msgs.append(LLMMessage(
                role="system",
                content=(
                    "## Affirmation with no active state\n"
                    "The user replied with a bare affirmative ('yes', "
                    "'sure', 'ok'). There is NO active workflow draft "
                    "on screen AND no structured pending resolution. "
                    "Do NOT pretend a draft exists. Do NOT say 'the "
                    "draft above is what you'll activate'. Do NOT "
                    "invent a previous failure or a prior plan to "
                    "retry. Interpret the affirmative as confirming "
                    "your last assistant message — if that was a "
                    "general suggestion, briefly act on it; if it was "
                    "small talk, reply briefly; if you're not sure "
                    "what they're agreeing to, ask one focused "
                    "follow-up question."
                ),
            ))
        # R3 micro (streaming mirror): structured resolution hint.
        if pending_resolution_hint_text:
            base_msgs.append(LLMMessage(
                role="system",
                content=pending_resolution_hint_text,
            ))
        mode_pin = _format_mode_pin(mode_override)
        if mode_pin:
            base_msgs.append(LLMMessage(role="system", content=mode_pin))
        if followup_hint_msg is not None:
            base_msgs.append(followup_hint_msg)
        # Mirror of non-streaming underspec/filler hint.
        if is_underspec_agent or is_filler_after_q:
            base_msgs.append(LLMMessage(
                role="system",
                content=(
                    "## Underspec / filler reply — ASK_USER, do NOT "
                    "describe a draft\n"
                    "Macro draft tools have been removed from your "
                    "tool set for this turn. Do NOT describe a draft "
                    "in prose. Do NOT promise a draft 'in the app'. "
                    "Call `ASK_USER` with ONE focused question, "
                    "naming the simplest option as a suggestion."
                ),
            ))
        messages: list[LLMMessage] = [
            *base_msgs,
            *_history_to_llm_messages(history),
            LLMMessage(role="user", content=message),
        ]

        tools_called: list[str] = []
        logiccard: Optional[dict] = None
        raw_data: dict = {}
        hop_index = 0
        accumulated_text = ""
        # Mirror of the non-streaming path's compact-draft tracker.
        last_was_macro_draft = False
        # Track the most recent tool error so the streaming
        # circuit-breaker can surface it to the user.
        last_tool_error: Optional[str] = None
        # Mirror of non-streaming `loaded_extras` — per-turn set of
        # tool names the LLM unlocked via `find_tool`. Threaded back
        # into `selected_names` after every find_tool success so the
        # next hop sees the schemas.
        loaded_extras: set[str] = set()

        while hop_index < _MAX_TOOL_CALLS:
            hop_index += 1
            hop_started = time.monotonic()
            # A1: only force tool_choice on hop 1; later hops MUST be
            # allowed to emit final text (otherwise the loop never ends).
            hop_tool_choice: Literal["auto", "required"] = (
                agent_tool_choice if hop_index == 1 else "auto"
            )
            hop_max_output = (
                _COMPACT_POST_MACRO_MAX_OUTPUT
                if (_COMPACT_DRAFTS and last_was_macro_draft)
                else max_output
            )
            trace.event(
                "llm.stream", hop=hop_index,
                reasoning_effort=effort, tools_offered=len(tooldefs),
                tool_choice=hop_tool_choice,
                max_output_tokens=hop_max_output,
                compact_post_macro=(_COMPACT_DRAFTS and last_was_macro_draft),
            )

            text_parts: list[str] = []
            # Function-call accumulator keyed by **item_id** (the
            # `fc_...` value Responses API uses on every delta event).
            # The downstream `call_id` (`call_...`) lives inside the
            # slot — that's what we send back as the tool_call_id when
            # we feed the result to the next hop.
            tc_acc: dict[str, dict[str, Any]] = {}
            cached_tokens = 0
            stream_error: Optional[str] = None

            async for ev in stream_openai(
                client,
                messages=messages,
                tools=tooldefs,
                tool_choice=hop_tool_choice,
                max_output_tokens=hop_max_output,
                reasoning_effort=effort,
                temperature=0.2,
                prompt_cache_key=cache_key,
            ):
                etype = ev.get("type")
                # Verbose stream-debug: emit every event type the first time
                # we see it on a hop to catch missed event paths.
                logger.debug("stream ev hop=%d type=%s keys=%s",
                             hop_index, etype, list(ev.keys()))
                if etype == "error":
                    stream_error = ev.get("message") or "stream error"
                    break

                if etype == "response.output_text.delta":
                    delta = ev.get("delta") or ""
                    if delta:
                        text_parts.append(delta)
                        # Stream user-visible text live.
                        yield {"type": "delta", "text": delta}
                    continue

                if etype == "response.output_item.added":
                    item = ev.get("item") or {}
                    if item.get("type") == "function_call":
                        # Key the accumulator by item_id (the value
                        # delta events reference). Stash call_id
                        # separately — it's what the next-hop
                        # function_call_output must echo back.
                        item_id = item.get("id") or ""
                        if item_id:
                            tc_acc.setdefault(item_id, {
                                "item_id": item_id,
                                "call_id": item.get("call_id") or "",
                                "name": item.get("name", "") or "",
                                "args_str": item.get("arguments", "") or "",
                            })
                    continue

                if etype == "response.function_call_arguments.delta":
                    item_id = ev.get("item_id") or ""
                    if item_id:
                        slot = tc_acc.setdefault(item_id, {
                            "item_id": item_id,
                            "call_id": "",
                            "name": "",
                            "args_str": "",
                        })
                        slot["args_str"] += ev.get("delta", "") or ""
                    continue

                if etype == "response.output_item.done":
                    item = ev.get("item") or {}
                    if item.get("type") == "function_call":
                        item_id = item.get("id") or ""
                        if item_id:
                            slot = tc_acc.setdefault(item_id, {
                                "item_id": item_id,
                                "call_id": "",
                                "name": "",
                                "args_str": "",
                            })
                            if item.get("call_id"):
                                slot["call_id"] = item["call_id"]
                            if item.get("name"):
                                slot["name"] = item["name"]
                            if item.get("arguments"):
                                slot["args_str"] = item["arguments"]
                    continue

                if etype == "response.completed":
                    resp_obj = ev.get("response") or {}
                    usage = resp_obj.get("usage") or {}
                    cached_tokens = int(
                        (usage.get("input_tokens_details") or {}).get(
                            "cached_tokens", 0
                        ) or 0
                    )
                    continue

                # Other events (response.created, response.in_progress,
                # reasoning deltas) are ignored.

            hop_ms = int((time.monotonic() - hop_started) * 1000)
            breakdown[f"llm_hop_{hop_index}"] = hop_ms
            if cached_tokens:
                breakdown[f"llm_hop_{hop_index}_cached"] = cached_tokens

            if stream_error:
                logger.warning("stream error at hop %d: %s", hop_index, stream_error)
                trace.event("turn.end", reason="llm_error")
                trace.end()
                yield {"type": "error", "message": _LLM_UNAVAILABLE}
                yield {
                    "type": "done",
                    "response": _LLM_UNAVAILABLE,
                    "tools_called": tools_called,
                    "logiccard": logiccard,
                    "raw_data": {"_llm_unavailable": True},
                    "latency_ms": int((time.monotonic() - turn_started) * 1000),
                    "latency_breakdown": breakdown,
                }
                return

            hop_text = "".join(text_parts)
            accumulated_text = hop_text  # final hop's text wins

            # No tool calls → final hop. Wrap up.
            if not tc_acc:
                text, sanitised = _post_process(hop_text)
                if sanitised and text == _GENERIC_FALLBACK and tools_called:
                    text = _tool_summary_line(tools_called[-1], logiccard)
                    sanitised = False
                # Caption-augment for widgets — ensures a workflow_draft_card
                # / logic_card / backtest_chart never renders without a
                # short conversational lead-in.
                augmented = _ensure_widget_caption(
                    text,
                    tool_name=(tools_called[-1] if tools_called else ""),
                    logiccard=logiccard,
                    raw_data=raw_data,
                )
                if augmented != text:
                    sanitised = True
                    text = augmented
                # If the post-processor rewrote the text, the user has
                # already seen the raw stream — emit a correction by
                # sending the cleaned text as a single replacement.
                if sanitised:
                    yield {"type": "replace", "text": text}
                self.store.append(conv_id, message, text)
                # Successful turn supersedes any pending state.
                self.store.clear_pending(conv_id)
                total = int((time.monotonic() - turn_started) * 1000)
                breakdown["total"] = total
                _log_timing(client.provider_name, message, total, breakdown,
                            tools=tools_called, note="stream")
                trace.event("turn.end", total_ms=total,
                            tools_called=tools_called, reason="stop")
                trace.end()
                yield {
                    "type": "done",
                    "response": text,
                    "tools_called": tools_called,
                    "logiccard": logiccard,
                    "raw_data": raw_data or None,
                    "latency_ms": total,
                    "latency_breakdown": breakdown,
                }
                return

            # Build tool_calls list with parsed args for the assistant
            # message + executor. The downstream `id` here is the
            # `call_id` — that's what function_call_output must echo;
            # `item_id` is internal to the streaming protocol and not
            # used past this point.
            tool_calls: list[dict[str, Any]] = []
            for slot in tc_acc.values():
                args_str = slot.get("args_str") or "{}"
                try:
                    args = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args = {"_raw_arguments": args_str, "_parse_error": True}
                tool_calls.append({
                    "id": slot.get("call_id") or slot.get("item_id", ""),
                    "name": slot.get("name", ""),
                    "arguments": args,
                })

            messages.append(LLMMessage(
                role="assistant",
                content=hop_text,
                tool_calls=tool_calls,
            ))

            for tc in tool_calls:
                yield {"type": "tool_start", "name": tc.get("name", "")}
                trace.event("tool.invoke", tool=tc.get("name"),
                            args=tc.get("arguments"))
                guarded = await execute_with_completeness(
                    tc["name"],
                    tc.get("arguments") or {},
                    llm_client=client,
                    user_message=message,
                    kite_token=ctx.kite_token,
                    db=ctx.db,
                    user_id=ctx.user_id,
                    # [C1/C2] earlier user turns count toward "user named
                    # a qty" so the M2 guard doesn't re-ask on amendments.
                    qty_context=_recent_user_text(history),
                    # P1: pass the prior DSL draft so a non-structural
                    # amendment patches it in place (no notify-only collapse).
                    prior_dsl_draft=(
                        active.draft if (active is not None
                                         and active.tool_name == "propose_dsl_workflow")
                        else None
                    ),
                )
                breakdown[f"tool_{guarded.name}"] = (
                    breakdown.get(f"tool_{guarded.name}", 0) + guarded.latency_ms
                )
                trace.event("tool.result", tool=guarded.name,
                            success=guarded.success,
                            needs_clarification=guarded.needs_clarification,
                            error=guarded.error,
                            latency_ms=guarded.latency_ms)
                yield {
                    "type": "tool_done",
                    "name": guarded.name,
                    "ok": guarded.success,
                    "error": guarded.error,
                }

                if guarded.needs_clarification and guarded.question:
                    self.store.append(conv_id, message, guarded.question)
                    self._maybe_set_pending(conv_id, guarded)
                    self._maybe_set_pending_resolution(
                        conv_id, message, guarded,
                    )
                    total = int((time.monotonic() - turn_started) * 1000)
                    breakdown["total"] = total
                    yield {"type": "delta", "text": guarded.question}
                    yield {
                        "type": "done",
                        "response": guarded.question,
                        "tools_called": [guarded.name],
                        "logiccard": None,
                        "raw_data": {"_render_hint": "ask_user"},
                        "latency_ms": total,
                        "latency_breakdown": breakdown,
                    }
                    _log_timing(client.provider_name, message, total, breakdown,
                                tools=[guarded.name], note="stream-ask")
                    trace.event("turn.end", total_ms=total,
                                tools_called=[guarded.name],
                                reason="needs_clarification")
                    trace.end()
                    return

                # Mirror handle()'s post-tool branching — see Change 1
                # in the file docstring. Success continues the loop;
                # error returns a deterministic question. No retry.
                if guarded.success:
                    tool_msg_content = _summarise_tool_result(guarded)
                    messages.append(LLMMessage(
                        role="tool",
                        tool_call_id=tc.get("id", f"call_{hop_index}"),
                        name=guarded.name,
                        content=tool_msg_content,
                    ))
                    if guarded.name not in tools_called:
                        tools_called.append(guarded.name)
                    if guarded.logiccard:
                        logiccard = guarded.logiccard
                    if guarded.data:
                        raw_data[guarded.name] = guarded.data
                    # Mirror of non-streaming path: stash any macro-draft tool
                    # so the next turn gets the right amendment hint.
                    if guarded.name in _STASH_DRAFT_TOOLS and guarded.data:
                        self._stash_workflow_draft(
                            conv_id, guarded.data, tool_name=guarded.name,
                        )
                    # F&O P1 mirror: option cards stash a compact spec
                    # as build_option_strategy (see handle()).
                    elif guarded.name in _OPTION_CARD_TOOLS and guarded.data:
                        self._stash_workflow_draft(
                            conv_id, _option_draft_spec(guarded.data),
                            tool_name="build_option_strategy",
                        )
                    if (guarded.name in _STASH_DRAFT_TOOLS
                            or guarded.name in _OPTION_CARD_TOOLS
                            or guarded.name in _COMPACT_PROSE_TOOLS):
                        last_was_macro_draft = True
                    # Mirror of handle(): lazy-load find_tool matches
                    # into `loaded_extras` so the next hop sees them.
                    if guarded.name == "find_tool" and guarded.data:
                        new_extras: set[str] = set()
                        for m in (guarded.data.get("matches") or []):
                            n = (m or {}).get("name")
                            if isinstance(n, str) and n and n != "find_tool":
                                new_extras.add(n)
                        if new_extras - loaded_extras:
                            loaded_extras |= new_extras
                            if selected_names is not None:
                                selected_names = selected_names | loaded_extras
                                tooldefs = _registry_tools_as_tooldefs(
                                    selected_names,
                                )
                                cache_key = cache_key_for(selected_names)
                                trace.event(
                                    "find_tool.lazy_load",
                                    added=sorted(new_extras),
                                    total_extras=len(loaded_extras),
                                    new_tooldefs_count=len(tooldefs),
                                    cache_key=cache_key,
                                )
                    continue

                last_tool_error = f"{guarded.name}: {guarded.error}"
                # L12 (streaming mirror): route-redirect on
                # "use <other_tool> instead" errors, plus the schedule-
                # shape backstop (see _redirect_target_for_failure).
                target_tool = _redirect_target_for_failure(
                    guarded.name, guarded.error or "", message,
                )
                if target_tool and not last_was_macro_draft:
                    trace.event(
                        f"{guarded.name}.route_redirect",
                        target=target_tool,
                        error=(guarded.error or "")[:140],
                    )
                    messages.append(LLMMessage(
                        role="tool",
                        tool_call_id=tc.get("id", f"call_{hop_index}"),
                        name=guarded.name,
                        content=(
                            f"ERROR from {guarded.name}: "
                            f"{guarded.error or ''}\n\n"
                            f"You MUST call `{target_tool}` next "
                            "with arguments matching the user's "
                            "original request. Do NOT write prose. "
                            "Do NOT re-call the failed tool."
                        ),
                    ))
                    if selected_names is not None:
                        selected_names = selected_names | {target_tool}
                        tooldefs = _registry_tools_as_tooldefs(selected_names)
                        cache_key = cache_key_for(selected_names)
                    continue
                # See non-streaming twin: backtest_workflow shares the
                # steps[] schema with propose_workflow, so it gets the
                # same single self-correction hop on validation errors.
                if guarded.name in {"propose_workflow", "backtest_workflow"}:
                    propose_workflow_attempts += 1
                    if propose_workflow_attempts < _PROPOSE_WORKFLOW_MAX_ATTEMPTS:
                        tool_msg_content = _summarise_tool_result(guarded)
                        messages.append(LLMMessage(
                            role="tool",
                            tool_call_id=tc.get("id", f"call_{hop_index}"),
                            name=guarded.name,
                            content=tool_msg_content,
                        ))
                        trace.event(
                            f"{guarded.name}.retry",
                            attempt=propose_workflow_attempts,
                            error=(guarded.error or "")[:160],
                        )
                        continue
                if guarded.name == "propose_workflow":
                    fb_draft = _try_macro_fallback(message)
                    if fb_draft is not None:
                        fb_text = (
                            "I couldn't fit your full request into a "
                            "single workflow shape, so I've drafted a "
                            "simplified version you can edit. The "
                            "trigger has been set to manual — review the "
                            "steps and adjust the trigger before "
                            "activating."
                        )
                        self.store.append(conv_id, message, fb_text)
                        self.store.clear_pending(conv_id)
                        self._stash_workflow_draft(conv_id, fb_draft, fb_text)
                        total = int((time.monotonic() - turn_started) * 1000)
                        breakdown["total"] = total
                        stream_raw_data = {
                            **fb_draft,
                            "propose_workflow": fb_draft,
                            "_render_hint": "workflow_draft_card",
                        }
                        yield {"type": "tool_start", "name": "propose_holding_action"}
                        yield {"type": "tool_done", "name": "propose_holding_action", "ok": True}
                        yield {"type": "delta", "text": fb_text}
                        yield {
                            "type": "done",
                            "response": fb_text,
                            "tools_called": tools_called + ["propose_holding_action"],
                            "logiccard": None,
                            "raw_data": stream_raw_data,
                            "latency_ms": total,
                            "latency_breakdown": breakdown,
                        }
                        _log_timing(
                            client.provider_name, message, total,
                            breakdown, tools=tools_called,
                            note="stream-propose_workflow_macro_fallback",
                        )
                        trace.event(
                            "turn.end", total_ms=total,
                            tools_called=tools_called + ["propose_holding_action"],
                            reason="propose_workflow_macro_fallback",
                        )
                        trace.end()
                        return

                question = _format_recoverable_failure_question(
                    tool_name=guarded.name,
                    error=guarded.error or "",
                    user_message=message,
                )
                # Stream-path mirror: route generic fall-through to the
                # LLM clarifier so the reply is tailored to the user's
                # actual prompt instead of a hardcoded template.
                if question == _LLM_CLARIFY_SENTINEL:
                    question = await _llm_clarification(
                        client=client,
                        user_message=message,
                        tool_name=guarded.name,
                        error=guarded.error or "",
                        history=history,
                    )
                # Stream-path mirror of the repeat-fallback variation.
                last_asst_text = next(
                    (
                        h.get("content", "")
                        for h in reversed(history)
                        if h.get("role") == "assistant"
                    ),
                    None,
                )
                if _is_repeat_fallback(question, last_asst_text):
                    question = _vary_repeat_fallback(message)
                self.store.append(conv_id, message, question)
                self.store.clear_pending(conv_id)
                total = int((time.monotonic() - turn_started) * 1000)
                breakdown["total"] = total
                yield {"type": "delta", "text": question}
                yield {
                    "type": "done",
                    "response": question,
                    "tools_called": tools_called + [guarded.name],
                    "logiccard": None,
                    "raw_data": {"_render_hint": "ask_user"},
                    "latency_ms": total,
                    "latency_breakdown": breakdown,
                }
                _log_timing(
                    client.provider_name, message, total, breakdown,
                    tools=tools_called,
                    note=f"stream-tool-error-no-retry:{guarded.name}",
                )
                trace.event(
                    "turn.end", total_ms=total, tools_called=tools_called,
                    reason="tool_error_no_retry", tool=guarded.name,
                )
                trace.end()
                return

            # next iteration of the loop will stream the next hop

        # Circuit-breaker hit during streaming.
        logger.warning("stream loop hit MAX_TOOL_CALLS=%d (last_err=%s)",
                       _MAX_TOOL_CALLS, last_tool_error)
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        if last_tool_error:
            msg = (
                "I couldn't finish that build — the workflow draft kept "
                f"failing validation. Last error: {last_tool_error[:240]}. "
                "Try rephrasing with the specific values you want."
            )
        else:
            msg = (
                "I needed to look up several things and got a bit lost. "
                "Could you ask again with more specifics?"
            )
        self.store.append(conv_id, message, msg)
        yield {"type": "delta", "text": msg}
        yield {
            "type": "done",
            "response": msg,
            "tools_called": tools_called,
            "logiccard": logiccard,
            "raw_data": {"_render_hint": "circuit_breaker"},
            "latency_ms": total,
            "latency_breakdown": breakdown,
        }

    def _unavailable(
        self, turn_started: float, breakdown: dict[str, int],
    ) -> ChatTurn:
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        return ChatTurn(
            response=_LLM_UNAVAILABLE,
            raw_data={"_llm_unavailable": True},
            sanitised=False,
            latency_ms=total,
            latency_breakdown=breakdown,
        )


# ── Helpers ────────────────────────────────────────────────────────


def _log_timing(
    provider: str,
    message: str,
    total_ms: int,
    breakdown: dict[str, int],
    *,
    tools: list[str] | None = None,
    note: str | None = None,
) -> None:
    """Emit a structured per-turn latency log line."""
    parts = [f"{k}={v}" for k, v in sorted(breakdown.items()) if k != "total"]
    tool_str = f"tools={tools}" if tools else "tools=[]"
    note_str = f" note={note!r}" if note else ""
    msg_preview = message.strip().replace("\n", " ")[:80]
    logger.info(
        "chat turn %dms [%s] %s %s (msg=%r)%s",
        total_ms, provider, tool_str, " ".join(parts), msg_preview, note_str,
    )


def _workflow_skeleton_caption(skeleton: dict) -> str:
    """Conversational message that accompanies the workflow_draft_card.

    The widget alone is silent — without a few words of human prose
    above it, the chat feels jumpy: a card appears with no acknowledgment.
    Here we describe the proposed agent in one short paragraph: trigger,
    action, and what to do next. Always under 240 chars so it doesn't
    crowd the card.
    """
    steps = skeleton.get("steps") or []
    name = (skeleton.get("name") or "Agent draft").rstrip(".")
    trigger_step = next((s for s in steps if s.get("step_type", "").startswith("trigger.")), None)
    action_step = next((s for s in steps if s.get("step_type", "").startswith("action.")), None)

    when_phrase = "on its trigger"
    if trigger_step:
        cfg = trigger_step.get("config") or {}
        if trigger_step["step_type"] == "trigger.schedule":
            cron = (cfg.get("cron") or "").strip()
            # Render a friendly time from "MM HH * * DOW"
            parts = cron.split()
            if len(parts) == 5:
                mm, hh, _, _, dow = parts
                dow_label = {
                    "1-5": "every weekday",
                    "*": "every day",
                    "1": "every Monday", "2": "every Tuesday",
                    "3": "every Wednesday", "4": "every Thursday",
                    "5": "every Friday",
                }.get(dow, f"on cron `{cron}`")
                try:
                    when_phrase = f"{dow_label} at {int(hh):02d}:{int(mm):02d} IST"
                except ValueError:
                    when_phrase = f"on `{cron}`"
        elif trigger_step["step_type"] == "trigger.indicator":
            ind = (cfg.get("indicator") or "").upper()
            period = cfg.get("period")
            op = cfg.get("operator", "")
            val = cfg.get("value")
            op_word = {
                "<": "drops below", ">": "rises above",
                "crosses_above": "crosses above",
                "crosses_below": "crosses below",
            }.get(op, op)
            when_phrase = f"when {ind}({period}) {op_word} {val}"
        elif trigger_step["step_type"] == "trigger.price":
            sym = cfg.get("symbol", "")
            op = cfg.get("operator", "")
            val = cfg.get("value")
            op_word = {
                "<": "drops below ₹", ">": "rises above ₹",
                "crosses_above": "crosses above ₹",
                "crosses_below": "crosses below ₹",
            }.get(op, f"{op} ₹")
            when_phrase = f"when {sym} {op_word}{val:g}".rstrip()

    do_phrase = "places the configured order"
    if action_step and action_step["step_type"] == "action.place_order":
        cfg = action_step.get("config") or {}
        do_phrase = (
            f"{cfg.get('side', 'buy')}s {cfg.get('quantity', '')} "
            f"{cfg.get('symbol', '')} at {cfg.get('order_type', 'market')}"
        ).strip()

    return (
        f"Here's a draft for **{name}** — it {do_phrase} {when_phrase}. "
        "Review the steps below and click Activate when you're happy "
        "with it."
    )


def _try_macro_fallback(message: str) -> Optional[dict]:
    """Last-ditch hydration when propose_workflow has hit its 3-attempt
    cap. Pattern-matches the user's message against the four macros
    and returns a draft dict on hit, or None to fall through to the
    normal escalation message.

    The match logic is intentionally generous — we'd rather emit a
    *partial* draft the user can edit than show "I couldn't do it".
    The user has already seen 30+ seconds of the model trying; giving
    them a workable starting point beats restating the request.

    Strategy:
      - SL phrasing → propose_holding_action with
        action_kind=set_stoploss, trigger_kind=manual (the user runs
        the workflow when the conditions are met). Drops the trigger
        condition since the model couldn't fit it; the user can edit
        the trigger in the editor before activating.
      - Indicator threshold + qty → propose_threshold_order
      - Schedule + qty → propose_scheduled_order
    """
    import re
    from backend.services.workflow_macros import hydrate_and_validate

    msg = message.strip()
    if not msg:
        return None

    # Pattern A: stop-loss phrasing → holding_action with manual trigger.
    sl_match = re.search(
        r"(?P<pct>\d+(?:\.\d+)?)\s*%\s*(?:stop[- ]?loss|stop|sl|loss)\b",
        msg, re.IGNORECASE,
    )

    # Tokens we never want as a symbol — days of week, common verbs,
    # indicators, etc. The case-insensitive symbol regex would happily
    # grab "MONDAY" otherwise.
    _SYMBOL_BLOCKLIST = {
        "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
        "SATURDAY", "SUNDAY",
        "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
        "TODAY", "YESTERDAY", "TOMORROW",
        "RSI", "SMA", "EMA", "MACD", "ADX", "ATR", "BB",
        "SL", "TP", "MP", "GTT", "OCO",
        "NSE", "BSE", "AT", "OF", "ON", "IF", "TO", "FROM",
        "IT", "OR", "AND", "ELSE", "WHEN", "THEN", "WHILE",
        "BUY", "SELL", "PLACE", "SET", "ADD", "STOP", "LOSS",
        "AGENT", "STRATEGY", "WORKFLOW", "AUTOMATION",
        "MARKET", "LIMIT", "OPEN", "CLOSE", "HIGH", "LOW",
        "PRICE", "QUANTITY",
        # "sell my ENTIRE RELIANCE holding" — ENTIRE is a modifier, not a
        # ticker. Same for FULL, WHOLE, ALL, COMPLETE, TOTAL, EVERY.
        "ENTIRE", "FULL", "WHOLE", "ALL", "COMPLETE", "TOTAL", "EVERY",
        "HOLDING", "POSITION", "SHARES",
    }

    def _pick_symbol(text: str) -> Optional[str]:
        # Try anchored extraction first: "on my SYM" / "stop loss on SYM" /
        # "SL on SYM" / "set ... on SYM". The user often spells out an
        # explicit anchor when describing an SL on an existing holding.
        for pat in (
            r"\b(?:on\s+my\s+|stop\s*-?\s*loss\s+on\s+my\s+|sl\s+on\s+my\s+|"
            r"trailing\s+stop\s+on\s+my\s+)([A-Za-z][A-Za-z0-9\-_]{2,15})\b",
            r"\b(?:on|for)\s+([A-Za-z][A-Za-z0-9\-_]{2,15})\b",
        ):
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                cand = m.group(1).upper()
                if cand not in _SYMBOL_BLOCKLIST:
                    return cand
        # Fallback: first ALL-CAPS token (real tickers are typed
        # uppercase by convention).
        for m in re.finditer(r"\b([A-Z][A-Z0-9\-_]{2,15})\b", text):
            cand = m.group(1)
            if cand not in _SYMBOL_BLOCKLIST:
                return cand
        # Last resort: any case-insensitive 3+ letter token.
        for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9\-_]{2,15})\b", text):
            cand = m.group(1).upper()
            if cand not in _SYMBOL_BLOCKLIST:
                return cand
        return None

    if sl_match:
        symbol = _pick_symbol(msg)
        if symbol is None:
            return None
        try:
            return hydrate_and_validate("holding_action", {
                "symbol": symbol,
                "action_kind": "set_stoploss",
                "trigger_kind": "manual",
                "sl_offset_pct": float(sl_match.group("pct")),
            })
        except (ValueError, TypeError):
            return None

    # Other fallbacks could go here — for now SL is the most-asked.
    return None


def _conversational_unsupported_reply(user_message_lower: str) -> str:
    """Build a tailored, plain-language reply when a workflow draft
    can't be expressed exactly as the user described.

    The previous canned response dumped the full step catalog with
    internal jargon ("cron", "fetch.relative_threshold",
    "condition.numeric", "trigger.market_relative_time"). That was
    correct internally but read like an error log. This helper picks
    cues from the user's message and offers a concrete, supported
    alternative the user can say yes to.

    Always returns a complete short reply — no bullet-point catalogs.
    """
    msg = (user_message_lower or "").strip()

    # ── Cue: weekly conditional sell ("sell if up by end of week").
    # Pivot can do scheduled sells AND profit-threshold sells, but
    # not both natively in one rule. Offer both as separate paths.
    has_weekly_anchor = bool(re.search(
        r"\bend\s+of\s+(?:the\s+)?week\b"
        r"|\b(?:by|on|every|next)\s+(?:the\s+)?friday\b"
        r"|\bweekly\b|\bweek[- ]?end\b",
        msg,
    ))
    has_conditional_verb = bool(re.search(
        r"\bif\s+(?:it|the|up|i|gain|profit|positive)\b"
        r"|\b(?:increased|increase|rises|rose|risen|gained|gain|"
        r"profit(?:able|s|ed)?|positive)\b",
        msg,
    ))
    weekly_conditional = has_weekly_anchor and has_conditional_verb

    has_basket = (
        "basket" in msg
        or "across" in msg
        or "top " in msg
        or any(s in msg for s in (" steel", " banking", " it stocks",
                                   " auto stocks", " pharma", " fmcg",
                                   " metals", " energy", " cement"))
    )
    if weekly_conditional and has_basket:
        return (
            "That one's two rules in one — Pivot can do each part on "
            "its own but not stitch them together yet. Pick how you "
            "want to start:\n\n"
            "1. **Just the daily basket** — I draft the buy side now "
            "(e.g. *₹1,000 of top steel stocks every weekday*). You "
            "manage the weekly exit yourself.\n"
            "2. **Profit-take rule on a specific stock** — I set up "
            "*sell when up X%* on a single ticker, not a whole basket.\n"
            "3. **Scheduled Friday review** — every Friday at close, "
            "I notify you with the basket's P&L so you can sell "
            "manually.\n\n"
            "Which one should I draft?"
        )

    # ── Cue: portfolio-relative or runtime-relative thresholds
    # ("if my P&L is up 5%", "5% below yesterday's close").
    runtime_relative = any(p in msg for p in (
        "yesterday", "yesterday's", "previous close", "prior close",
        "today's open", "the open", "5% below", "10% below",
        "below open", "above open",
    ))
    if runtime_relative:
        return (
            "Pivot's triggers fire on absolute prices or fixed indicator "
            "levels — they can't anchor to *yesterday's close* or *today's "
            "open* directly. The closest supported shape is a daily "
            "checkpoint: every morning at 09:30 I check the price and "
            "act if it's X% off the open. Want me to draft that, or "
            "switch to a fixed price level instead?"
        )

    # ── Cue: P&L-conditional / "if profitable" / "if up X%".
    pnl_conditional = any(p in msg for p in (
        "if up", "if profitable", "if i'm up", "if my position is up",
        "if it's up", "+X%", "profit-take", "take profit",
    ))
    if pnl_conditional:
        return (
            "I can do *sell when X is up Y%* on a single ticker or set "
            "a stop-loss with a percentage offset, but not a portfolio-"
            "wide *if any position is up* trigger yet. Tell me the "
            "ticker and the % gain you want to lock in and I'll wire "
            "it up."
        )

    # ── Cue: per-lot / fundamentals / multi-leg requests.
    # Fundamentals screening IS wired now (screen_fundamentals for
    # cross-sectional PE/ROE/etc. screens, fetch_fundamentals for one
    # stock). This fallback only fires when a workflow DRAFT failed to
    # validate — so steer the user to ask for the screen as a plain
    # query, which routes to the screen tool.
    if any(p in msg for p in (
        "pe ratio", "p/e ratio", "earnings", "eps", "roe", "fundamental",
        "screen by", "screen for stocks",
    )):
        return (
            "I can screen on fundamentals — ask it as a plain query like "
            "*\"pharma stocks with P/E under 25\"* or *\"stocks with ROE "
            "above 18\"* and I'll pull the list. For one company's "
            "P/E / ROE just ask *\"what's RELIANCE's PE and ROE\"*."
        )

    if any(p in msg for p in (
        "options", "calls", "puts", "futures", "f&o", "expiry",
        "straddle", "strangle", "call ", "put ",
    )):
        return (
            "I can work options directly now — ask for the chain "
            "(*\"NIFTY option chain\"*), a suggestion (*\"I'm bullish on "
            "NIFTY, suggest an options strategy\"*), or a specific "
            "structure (*\"build an iron condor on BANKNIFTY\"*) and "
            "you'll get an editable strategy card with payoff, max loss "
            "and probability of profit. Futures execution isn't wired "
            "yet — options register to paper or as live intents only."
        )

    if any(p in msg for p in (
        "tick", "every second", "every minute", "real-time",
        "real time", "live trigger", "intraday alert",
    )):
        return (
            "Pivot checks prices on a schedule, not on every tick — "
            "the most frequent meaningful trigger is once-per-minute. "
            "Tell me the price level (e.g. *if RELIANCE drops below "
            "₹2,800*) and I'll set up a check that runs every minute "
            "during market hours."
        )

    # ── Generic fall-through: route to the LLM clarification helper.
    # The sentinel is replaced by the async wrapper at the call site
    # with an LLM-generated, prompt-aware reply.
    return _LLM_CLARIFY_SENTINEL


# Sentinel returned by the (still-sync) deterministic dispatch when none
# of the specific user-side mistakes were detected and the right move is
# to ask the LLM for a custom clarification. The async wrapper around
# this function notices the sentinel and routes to _llm_clarification.
_LLM_CLARIFY_SENTINEL = "<<LLM_CLARIFY>>"


# Light NSE-ticker / window / side keyword extractor over recent user
# turns. Output is a small bulleted summary the clarification LLM can
# reuse so it never invents AAPL/2020-01-01 placeholders when the user
# already named specifics. Kept regex-only (no spaCy) — the cost is a
# few microseconds per turn and the win is preventing hallucinated
# substitutions in clarification replies.
_TICKER_RE = re.compile(r"\b([A-Z][A-Z0-9&]{1,14})\b")
_WINDOW_RES = [
    re.compile(r"\b(\d+\s*(?:trading\s*)?(?:day|days|month|months|year|years|y|yr|yrs|mo))\b", re.IGNORECASE),
    re.compile(r"\b(last\s+\d+\s*(?:day|days|month|months|year|years|trading\s*days?))\b", re.IGNORECASE),
    re.compile(r"\b(recent\s+\d+\s*(?:day|days|trading\s*days?))\b", re.IGNORECASE),
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
]
_SIDE_RE = re.compile(
    r"\b(buy(?:ing)?\s+at\s+(?:open|close)|sell(?:ing)?\s+at\s+(?:open|close)|"
    r"long|short|cover|squareoff|round\s*trip|reverse(?:\s+(?:the\s+)?strategy)?)\b",
    re.IGNORECASE,
)
_QTY_RE = re.compile(r"\b(\d+)\s*(?:share|shares|qty|lots?)\b", re.IGNORECASE)
_NOISE_WORDS = {
    "I", "A", "AN", "THE", "AND", "OR", "NOT", "WITH", "FOR", "TO", "ON",
    "IN", "AT", "BY", "OF", "IT", "ITS", "OK", "OKAY", "YES", "NO",
    "RUN", "PLOT", "RSI", "MACD", "SMA", "EMA", "VS", "BACKTEST", "BUY",
    "SELL", "OPEN", "CLOSE", "OHLC", "NSE", "BSE", "NIFTY", "USD", "INR",
}


def _extract_prior_specifics(history: list[dict] | None, latest: str) -> str:
    """Return a compact bullet list of user-stated specifics gathered
    across the conversation. Used in clarification prompts to anchor
    the LLM to what the user ALREADY said.

    Conservative on tickers — we only surface tokens that look like
    Indian equity tickers (2-15 uppercase chars) and that are NOT in
    the noise list. The clarification LLM uses these as 'do not
    substitute' anchors.
    """
    texts: list[str] = []
    for h in (history or []):
        if isinstance(h, dict) and h.get("role") == "user":
            c = (h.get("content") or "").strip()
            if c:
                texts.append(c)
    if latest:
        texts.append(latest)
    if not texts:
        return ""

    blob = "\n".join(texts[-10:])
    tickers: list[str] = []
    for m in _TICKER_RE.finditer(blob):
        tok = m.group(1)
        if tok in _NOISE_WORDS or len(tok) < 3:
            continue
        if tok not in tickers:
            tickers.append(tok)
    windows: list[str] = []
    for pat in _WINDOW_RES:
        for m in pat.finditer(blob):
            w = m.group(1).strip()
            if w and w not in windows:
                windows.append(w)
    sides = sorted({m.group(1).lower() for m in _SIDE_RE.finditer(blob)})
    qtys = sorted({m.group(1) for m in _QTY_RE.finditer(blob)})

    bullets: list[str] = []
    if tickers:
        bullets.append(f"  • Symbol(s) named: {', '.join(tickers[:5])}")
    if windows:
        bullets.append(f"  • Time window(s) named: {', '.join(windows[:3])}")
    if sides:
        bullets.append(f"  • Strategy / side cues: {', '.join(sides[:5])}")
    if qtys:
        bullets.append(f"  • Quantities named: {', '.join(qtys[:3])} share(s)")
    return "\n".join(bullets)


def _count_recent_clarifications(history: list[dict] | None) -> int:
    """Count consecutive assistant turns (from the end of history) that
    look like clarification questions. Used to detect when we're stuck
    in a 'ask, user replies, ask again' loop — at which point we
    switch tone and offer to run with defaults rather than asking yet
    another question."""
    count = 0
    for h in reversed(history or []):
        if not isinstance(h, dict):
            continue
        role = h.get("role")
        if role == "user":
            # A user reply between two assistant turns doesn't break
            # the consecutive count — it IS the reply to the previous
            # clarification. We only break when we see an assistant
            # turn that is NOT a clarification (i.e. it ran a tool /
            # gave a real answer).
            continue
        if role == "assistant":
            content = (h.get("content") or "").strip()
            if not content:
                continue
            tail = content[-400:]
            if _CLARIFICATION_CUES_RE.search(tail):
                count += 1
            else:
                break
        else:
            # system or tool turn — ignore
            continue
    return count


async def _llm_clarification(
    *,
    client: LLMClient,
    user_message: str,
    tool_name: str,
    error: str = "",
    history: list[dict] | None = None,
) -> str:
    """Ask the LLM to write a 1–3 sentence clarification for a failed
    chat request. Used INSTEAD of a hardcoded template when we can't
    name a specific user-side mistake.

    History-aware: the user's prior turns and our prior clarifications
    are sent as actual chat turns so the model sees the conversation
    arc, not just the latest message. Without this, replies like "yes
    run" or "i meant 252 trading days" lose all context and the model
    hallucinates ticker/date placeholders from training data.
    """
    capability = {
        "backtest_workflow": (
            "simulate a trading strategy on historical price data and "
            "show a chart with returns, trade signals, and buy-and-hold "
            "benchmark"
        ),
        "propose_workflow": (
            "build a saved automation that runs going forward — a "
            "recurring or trigger-based agent"
        ),
        "place_market_order": "place an immediate market order via the broker",
        "place_limit_order": "place a limit order with a target price",
        "create_sip": "set up a recurring investment on a schedule",
        "create_gtt_order": "place a long-lived limit / stop order",
        "run_backtest": (
            "simulate a trading strategy on historical price data"
        ),
    }.get(tool_name, "act on your request")

    # Compact transcript of the prior turns — the model sees the user's
    # *cumulative* ask (original prompt + their replies to our earlier
    # clarifications), not just the latest message. Cap at the last 8
    # turns so the prompt stays small.
    history_msgs: list[LLMMessage] = []
    for h in (history or [])[-8:]:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            history_msgs.append(LLMMessage(role=role, content=content[:600]))

    prior_specifics = _extract_prior_specifics(history, user_message)

    # Count how many of OUR last turns were clarifications. If we've
    # already asked 2+ in a row, the user is justifiably frustrated —
    # change the tone and offer to run with reasonable defaults rather
    # than asking another question.
    consecutive_clarifications = _count_recent_clarifications(history)

    system_prompt = (
        "You are Pivot's chat assistant. A user request just failed to "
        "build — NOTHING was created, saved, scheduled, or run. Write a "
        "SHORT reply (1–3 sentences, max 70 words).\n\n"
        "You are seeing the FULL conversation. Use it. The user has "
        "been progressively narrowing their ask across turns — your "
        "reply must reflect what they have ALREADY said, not restart "
        "from zero.\n\n"
        "CRITICAL HONESTY RULE: this is a FAILURE path. You did NOT "
        "produce anything. NEVER imply success or that it will run. Do "
        "NOT say 'I'll run it', 'running it now', 'I'll set it up', "
        "'I'll create that', 'run it as-is', 'done', 'it's live / "
        "scheduled / active', or 'consider it set'. There is no card "
        "and nothing will fire. Claiming otherwise is the single worst "
        "thing you can do here.\n\n"
        "RULES:\n"
        "  • Reuse specifics the user already gave (ticker, window, "
        "strategy, side). NEVER substitute a different ticker (no "
        "AAPL if they said TCS) or a different window (no '2020-01-01 "
        "to 2022-12-31' if they said 'last 252 trading days').\n"
        "  • If something is ambiguous, name ONE specific thing in "
        "plain English and ask the user to confirm a single concrete "
        "interpretation using THEIR specifics.\n"
        "  • If the request names everything yet still couldn't be "
        "built as one automation, say so honestly in plain words and "
        "offer the nearest thing that WOULD work (e.g. splitting it "
        "into two simpler rules) — framed as an offer to try, never as "
        "something already done.\n"
        "  • No code-flavoured words: 'tool', 'function', 'parameter', "
        "'field', 'required', 'missing argument', 'JSON', 'schema', "
        "'config', 'workflow draft'.\n"
        "  • Flowing prose, no bullet points, no headers.\n"
        "  • Vary the opening — never start with 'I couldn't complete "
        "that' or 'You want to'.\n"
        + (
            "\nIMPORTANT: you have already asked the user "
            f"{consecutive_clarifications} clarification(s) in a row. "
            "Do NOT ask another open-ended question. Instead, state the "
            "single most reasonable interpretation using their stated "
            "specifics (fill any genuinely missing detail with a "
            "reasonable default) and ask them to confirm so you can try "
            "to build it — e.g. 'Want me to set it up as X with Y?'. "
            "This is still a question, NOT a claim that it is running.\n"
            if consecutive_clarifications >= 2
            else ""
        )
    )

    user_prompt = (
        f"The user is trying to: {capability}.\n\n"
        f"Their latest message in this turn: \"{user_message}\"\n\n"
        f"What you already know from the conversation above:\n"
        f"{prior_specifics or '  (no specifics extracted)'}\n\n"
        f"Internal reason it didn't run (for your context only — do "
        f"NOT echo this back): {error or 'request was ambiguous.'}\n\n"
        "Write the reply now."
    )

    try:
        msgs: list[LLMMessage] = [LLMMessage(role="system", content=system_prompt)]
        msgs.extend(history_msgs)
        msgs.append(LLMMessage(role="user", content=user_prompt))
        resp = await client.complete(
            messages=msgs,
            tools=None,
            tool_choice="none",
            max_output_tokens=220,
            temperature=0.4,
            reasoning_effort="minimal",
            prompt_cache_key="clarify_failed_call_v2",
        )
        text = (resp.content or "").strip()
        if text:
            return text
    except Exception as e:
        logger.info("clarification LLM call failed: %s", e)

    # Final fallback — still nicer than the old hardcoded template
    # because it paraphrases the user's intent.
    return (
        f"I hit a snag working out the {capability} for that. Could you "
        "rephrase with one or two concrete details — the exact ticker, "
        "the trigger condition, and the time window?"
    )


# Hinglish + English filler words that must NEVER be mistaken for a
# ticker by the not-found symbol-extraction fallback. The prior blind
# regex surfaced "ACTUALLY" / "NAHI" as fake symbols.
_SYMBOL_STOPWORDS: frozenset[str] = frozenset({
    "ACTUALLY", "NAHI", "NAH", "KA", "KI", "KO", "KE", "TO", "AUR",
    "KHARIDO", "KHARID", "KHARIDLE", "BECH", "BECHO", "BECHDE", "GIR",
    "JAYE", "JAAYE", "UPAR", "NEECHE", "NICHE", "SHARE", "SHARES",
    "BUY", "SELL", "WHEN", "IF", "ONCE", "AT", "THE", "AND", "OR",
    "FOR", "WITH", "ALERT", "PING", "NOTIFY", "PRICE", "QUOTE", "LTP",
    "HAAN", "HAN", "YES", "NO", "OK", "OKAY", "CONFIRM", "KAR", "DE",
    "DO", "ME", "MY", "WORTH", "RUPEES", "RS", "INR", "LAKH", "CRORE",
})


def _extract_user_symbol(user_message: str) -> Optional[str]:
    """Best-effort: pull the NSE ticker the user actually referenced.

    Prefers a token that resolves against the curated symbol universe;
    falls back to the first non-stopword uppercase-able token of
    plausible ticker shape. Returns None when nothing credible is found
    so the caller can avoid naming a filler word as a fake symbol.
    """
    if not user_message:
        return None
    try:
        from backend.services.sector_universe import symbol_sector_map
        known = set(symbol_sector_map().keys())
    except Exception:
        known = set()
    # Candidate tokens: 2–15 char alnum runs from the raw message.
    raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9&\-_]{1,14}", user_message)
    upper_tokens = [t.upper() for t in raw_tokens]
    # 1) A token that resolves against the curated universe wins.
    for t in upper_tokens:
        if t in known:
            return t
    # 2) Otherwise, the first token that is BOTH all-uppercase in the
    #    original message (user typed it like a ticker) AND not a
    #    stopword — this respects "TATAMOTORS" while rejecting "actually".
    for raw, up in zip(raw_tokens, upper_tokens):
        if raw.isupper() and up not in _SYMBOL_STOPWORDS and len(up) >= 2:
            return up
    return None


def _format_recoverable_failure_question(
    *, tool_name: str, error: str, user_message: str = "",
) -> str:
    """User-facing question after a tool error.

    Maps the most common structural failures into a focused ask rather
    than dumping the raw schema error or a generic "didn't fit." When
    we can identify the offending phrase from the user's message
    (NIFTY index instead of NIFTYBEES, "buying power" condition that
    needs a fetch step, etc.), we name it.
    """
    err_lc = (error or "").lower()
    msg_lc = (user_message or "").lower()

    # Quick detector for offending phrases in the user's message that
    # commonly trip propose_workflow. Each entry is (regex pattern in
    # user message, tailored question).
    if tool_name == "propose_workflow" and msg_lc:
        # P2 (2026-05-29): only fire the "index isn't tradeable" nudge when
        # the index is the ACTION TARGET (buy/sell nifty) — NOT when it
        # merely appears in a WHEN/trigger clause ("buy A,B,C when nifty
        # rises 1%"). The old detector fired on any nifty mention and
        # dropped the named-equity basket with a misleading NIFTYBEES reply.
        _nifty_is_trigger = bool(re.search(
            r"\b(?:when|if|once|after|as\s+soon\s+as|whenever)\b[^.]*\bnifty\b",
            msg_lc))
        _nifty_is_action = bool(re.search(
            r"\b(?:buy|sell|short|purchase|trade)\b(?:\s+\d[\d,]*)?"
            r"(?:\s+(?:shares?|units?|lots?)\s+of)?\s+nifty\b", msg_lc))
        if (re.search(r"\bnifty\b(?!\s*bees|\s*50\b)", msg_lc)
                and _nifty_is_action and not _nifty_is_trigger):
            return (
                "I couldn't draft that — `NIFTY` is the index, not a "
                "tradeable instrument. To run a daily open→close round-"
                "trip you'd use the ETF that tracks it: `NIFTYBEES`. "
                "Want me to draft the same agent on NIFTYBEES instead?"
            )
        _bn_is_trigger = bool(re.search(
            r"\b(?:when|if|once|after|as\s+soon\s+as|whenever)\b[^.]*\bbank\s*nifty\b",
            msg_lc))
        _bn_is_action = bool(re.search(
            r"\b(?:buy|sell|short|purchase|trade)\b(?:\s+\d[\d,]*)?"
            r"(?:\s+(?:shares?|units?|lots?)\s+of)?\s+bank\s*nifty\b", msg_lc))
        if (re.search(r"\bbank\s*nifty\b(?!\s*bees)", msg_lc)
                and _bn_is_action and not _bn_is_trigger):
            return (
                "I couldn't draft that — `BANKNIFTY` is the index, not "
                "a tradeable instrument. The ETF that tracks it is "
                "`BANKBEES`. Should I draft the agent on BANKBEES instead?"
            )
        if re.search(
            r"\b(?:buying\s+power|cash\s+balance|available\s+balance|"
            r"free\s+cash|funds?\s+available)\b",
            msg_lc,
        ):
            return (
                "I couldn't fit the buying-power check — Pivot v1 doesn't "
                "support a 'cash > X' gate as a step. Two ways to express "
                "this: (a) drop the buying-power check and just run the "
                "buy on the schedule, or (b) run a portfolio fetch first "
                "and skip the day if cash is below your threshold (we'll "
                "wire the fetch + condition manually). Which would you "
                "like?"
            )
        if re.search(r"\bemail\b", msg_lc):
            return (
                "I couldn't wire the email step — Pivot v1's notify "
                "channel is in-app only (the agent run history surfaces "
                "the message). Want me to draft the same agent without "
                "email, with the notification visible in the run log?"
            )
        if re.search(r"\bnotify\b|\balert\s+me\b", msg_lc) and "notify" in err_lc:
            return (
                "I couldn't wire the notification step from that wording. "
                "Could you say what you want notified about — e.g. *notify "
                "me when the buy order fires* or *notify me at end of day "
                "with the P&L*?"
            )

    if tool_name == "propose_workflow":
        if "trigger_price" in err_lc and "required" in err_lc:
            return (
                "I tried to draft that agent but the stop-loss step needs "
                "either an absolute trigger price (e.g. ₹1,420) or a "
                "percentage below entry (e.g. 2%). Which would you like "
                "to use?"
            )
        if "quantity" in err_lc:
            return (
                "I started drafting that agent but I need a quantity for "
                "the order step — how many shares per fire?"
            )
        if "cron" in err_lc or "schedule" in err_lc:
            return (
                "I drafted the agent but the schedule didn't parse — "
                "could you tell me the day(s) and time? e.g. 'every "
                "weekday at 09:15 IST'."
            )
        # WHY this message was widened to mention market_relative_time:
        # the prior version listed only "absolute price" and "daily
        # checkpoint" alternatives, which made the user think
        # "1 hour after open" / "2 PM" were unsupported. Both ARE
        # supported via trigger.market_relative_time and trigger.schedule
        # respectively — the model just produced an invalid trigger
        # shape and the validator rejected it. The right user-facing
        # nudge is to name the actually-supported triggers, including
        # market-relative time, and let the user pick.
        # WHY this whole branch was rewritten: the previous responses
        # were architecture dumps — bullet lists naming "cron",
        # "trigger.market_relative_time", "fetch.relative_threshold +
        # condition.numeric", "step-catalog rejections". Users don't
        # want internals; they want a conversational reply that
        # acknowledges what they asked, names what part is hard in
        # plain English, and offers a concrete next step. The new
        # messages also try to extract specific cues from the user's
        # message ("basket", "if up by end of week", "stop loss") so
        # the alternative we offer is tailored, not generic.
        if any(
            tok in err_lc
            for tok in ("operator", "value", "trigger.price",
                        "trigger.indicator", "trigger.event")
        ) or any(
            tok in err_lc for tok in ("input should be", "extra inputs", "literal_error")
        ):
            return _conversational_unsupported_reply(msg_lc)

        # Short-message gate: vague follow-up that the model
        # mis-routed to propose_workflow. Stay friendly, not bullet-y.
        if msg_lc and len(msg_lc.strip()) <= 30:
            return (
                "I'm not sure what to draft from that. Tell me what you'd "
                "like — for example, *\"buy 10 RELIANCE every weekday at "
                "09:15\"* or *\"alert me when NIFTY drops 2%\"*."
            )

        return _conversational_unsupported_reply(msg_lc)
    if tool_name in {"place_market_order", "place_limit_order"}:
        return (
            "I couldn't place that order from what was given — could you "
            "confirm the symbol, quantity, and (for limit orders) the "
            "limit price?"
        )
    # WHY this branch exists: when get_live_price fails on an unknown
    # ticker (e.g. "XYZFAKE123"), the fallback used to be the generic
    # "I couldn't complete that — give me values". That hides the
    # actual problem — the ticker isn't in the data feed. Naming the
    # symbol gives the user something to act on.
    if tool_name in {"get_live_price", "get_ohlc", "get_52wk_range",
                     "get_index_level"}:
        # Extract the symbol the user mentioned for a specific message.
        # WHY this is careful: the prior blind first-uppercased-token
        # grab surfaced Hinglish filler ("ACTUALLY", "NAHI") as a
        # fake ticker and even reported a VALID, liquid NSE name (e.g.
        # TATAMOTORS) as "not found". We now (1) strip Hinglish/English
        # stopwords, (2) prefer a token that resolves against the
        # curated universe, and (3) only name a token the user actually
        # typed — never a filler word.
        sym = _extract_user_symbol(user_message)
        if sym is None:
            return (
                "I couldn't pull a live quote just now. Tell me the NSE "
                "ticker (e.g. TATAMOTORS, INFY) and I'll try again — "
                "Pivot covers NSE-listed equities and indices."
            )
        return (
            f"I couldn't find price data for `{sym}` on NSE. Double-"
            f"check the ticker spelling — Pivot covers NSE-listed "
            f"equities only."
        )
    return _LLM_CLARIFY_SENTINEL


# Phrases the user-facing fallback messages start with. Used to detect
# when the same canned response would fire two turns in a row — at
# which point we vary it so the user doesn't see the same text bounced
# back like a stuck record.
_GENERIC_FALLBACK_PREFIXES: tuple[str, ...] = (
    "i couldn't complete that",
    "i couldn't place that order",
    "i couldn't draft that",
    "that step shape isn't in pivot",
    "i'm not sure what to draft",
)


def _is_repeat_fallback(canned: str, last_assistant_msg: Optional[str]) -> bool:
    """True when `canned` would repeat the gist of the prior assistant
    turn. Compared on the leading prefix so paraphrase variants still
    register as duplicates.
    """
    if not canned or not last_assistant_msg:
        return False
    a = canned.strip().lower()[:60]
    b = last_assistant_msg.strip().lower()[:200]
    if not any(p in b for p in _GENERIC_FALLBACK_PREFIXES):
        return False
    if not any(p in a for p in _GENERIC_FALLBACK_PREFIXES):
        return False
    # Repeat if both messages start with the same fallback prefix.
    return any(a.startswith(p) and p in b for p in _GENERIC_FALLBACK_PREFIXES)


def _vary_repeat_fallback(user_msg: str) -> str:
    """Return a varied fallback when the canned message would otherwise
    repeat last turn. Doesn't try to answer the request — just pivots
    the conversation so the user isn't stuck on the same wall.
    """
    return (
        "Let's reset. Tell me concretely what you're after — for example, "
        "*\"buy 10 RELIANCE at market\"*, *\"build an agent that buys "
        "TCS every Monday at 9:15\"*, or *\"show me today's top "
        "gainers\"*. If you wanted something Pivot v1 doesn't support "
        "yet, say so and I'll suggest the closest fit."
    )


def _tool_summary_line(tool_name: str, logiccard: dict | None) -> str:
    """One-liner used when the post-processor stripped the LLM's
    narration but a tool actually produced a card.

    NEVER include the raw `tool_name` in the user-facing text — that
    leaks an internal identifier (e.g. "Done — `backtest_workflow` ran")
    which the system prompt explicitly forbids. Every branch below
    returns user-facing prose only.
    """
    if tool_name == "propose_workflow":
        return (
            "Here's a draft of that agent — the trigger, action(s), and "
            "any conditions are laid out below. Review and click Activate "
            "when you're happy."
        )
    if tool_name == "backtest_workflow":
        return (
            "Here's the backtest — equity curve, signals, and headline "
            "metrics are in the chart below."
        )
    if tool_name in {
        "propose_threshold_order", "propose_scheduled_order",
        "propose_basket_allocation", "propose_holding_action",
    }:
        return (
            "Drafted — the trigger and action are laid out in the card "
            "below. Review and click Activate when you're happy."
        )
    if logiccard:
        action = logiccard.get("action") or ""
        symbol = logiccard.get("symbol") or ""
        qty = logiccard.get("quantity") or logiccard.get("qty") or ""
        if action and symbol:
            qty_part = f" {qty}" if qty else ""
            return (
                f"Here's a {action}{qty_part} {symbol} order ready to go — "
                "the card below shows the full details. Click Confirm when "
                "ready."
            )
        return (
            "I've prepared the action for you — review the card below and "
            "click Confirm to send it through."
        )
    if tool_name.startswith("get_") or tool_name.startswith("list_"):
        return "Here's what I found — the details are in the card below."
    # Generic fallback — no tool name leak.
    return "Done — the result is shown below."


# Render hints whose widgets need an accompanying conversational caption
# in the assistant text. If the LLM produced no text (or only a placeholder
# we sanitised), we synthesise one rather than leaving the widget mute.
_WIDGET_RENDER_HINTS = frozenset({
    "workflow_draft_card",
    "logic_card",
    "indicator_backtest_chart",
    "financial_backtest_chart",
    "multistep_card",          # L14: compose_multistep timeline payload
})


def _ensure_widget_caption(
    text: str,
    *,
    tool_name: str,
    logiccard: dict | None,
    raw_data: dict,
) -> str:
    """Make sure assistant text accompanies any widget render.

    The chat pattern is `text + widget`, never `widget alone`. When the
    LLM:
      - emitted no text → synthesise one matching the widget kind.
      - emitted a single-word affirmation ("done", "okay") → upgrade
        to a descriptive line.
      - emitted a full sentence → leave it; the model already nailed it.

    Returns the (possibly-upgraded) text. Never empty.
    """
    # Inside chat_service, raw_data is keyed by tool_name and the
    # _render_hint lives nested. The router hoists it later. Look both
    # places so this helper is correct regardless of caller.
    rd = raw_data or {}
    render_hint = rd.get("_render_hint")
    if not render_hint:
        for v in rd.values():
            if isinstance(v, dict) and v.get("_render_hint"):
                render_hint = v["_render_hint"]
                break
    if render_hint not in _WIDGET_RENDER_HINTS and not logiccard:
        return text

    cleaned = (text or "").strip()
    too_terse = (
        not cleaned
        or len(cleaned) < 12
        or cleaned.lower() in {
            "done", "ok", "okay", "sure", "got it", "yes", "no",
            _GENERIC_FALLBACK.lower().rstrip("?."),
        }
    )
    if not too_terse:
        return cleaned

    # Synthesise per-widget caption.
    if render_hint == "workflow_draft_card":
        skeleton = rd.get("propose_workflow") or rd
        if isinstance(skeleton, dict) and skeleton.get("steps"):
            return _workflow_skeleton_caption(skeleton)
        return _tool_summary_line("propose_workflow", None)
    if render_hint == "indicator_backtest_chart":
        return (
            "Here's the backtest — equity curve, signals, and headline "
            "metrics are in the chart below."
        )
    if render_hint == "financial_backtest_chart":
        return (
            "Here's the fundamentals backtest — performance vs. NIFTY and "
            "the rebalance trades are below."
        )
    if logiccard or render_hint == "logic_card":
        return _tool_summary_line(tool_name or "", logiccard)
    return _tool_summary_line(tool_name or "", logiccard)


def _post_process(text: str) -> tuple[str, bool]:
    """Defence-in-depth: strip leaked tool-call blocks / placeholders /
    internal-reasoning monologue / tool-name leaks.
    Returns (cleaned, was_sanitised)."""
    if not text:
        return _GENERIC_FALLBACK, True
    original = text
    text = _TOOL_CALL_BLOCK_RE.sub("", text)
    text = _PLACEHOLDER_RE.sub("", text)
    # Strip sentences that name internal tools ("Done — backtest_workflow
    # ran.", "I'll call get_live_price next.") — system prompt forbids
    # mentioning internal tool names, but the model occasionally mimics
    # the canned fallback templates from history. Once these go, _ensure_
    # widget_caption will synthesise a user-facing caption.
    text = _strip_internal_tool_leaks(text)
    # GAN R2 R15: drop an empty / apologetic "## News" section stub.
    text = _strip_empty_news_section(text)
    # WHY this strip runs BEFORE the latent-greeting check: a leaked
    # reasoning paragraph can include greeting-shaped phrases ("Hi,
    # the user now says...") that would trip the latent-greeting
    # rule and replace the entire response with the generic fallback,
    # erasing legitimate text that came after the leak.
    text = _strip_reasoning_leakage(text)
    if _LATENT_GREETING_RE.search(text):
        text = _GENERIC_FALLBACK
    text = text.strip()
    if not text:
        text = _GENERIC_FALLBACK
    return text, text != original
