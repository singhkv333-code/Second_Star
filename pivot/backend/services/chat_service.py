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

import asyncio
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
from backend.prompts.assembler import load_prompt_modules
from backend.services.chat_trace import TurnTrace, start_turn
from backend.services.conversation_store import (
    CONV_PROMPT_WINDOW_TURNS,
    ActiveDraft,
    ClarifyState,
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
    select_prompt_modules,
    select_tool_names,
)
from backend.services.thematic_map import (
    ThematicScenario,
    basket_weights,
    detect_thematic_scenario,
    extract_capital_inr,
    is_scared_idle_cash,
    is_unrealistic_return,
    is_vague_onboarding,
    winners_losers_block,
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


def _unavailable_text(message: str = "") -> str:
    """GAN R4 F11: a degraded reply that ECHOES the user's intent so a
    transient backend failure doesn't make them re-type. When we can
    parse a clear ask from the last message, lead with 'I caught your
    request: <X> — the AI backend hiccuped, say "retry" and I'll run it.'
    so the referent/capital isn't lost. Falls back to the generic menu
    when nothing parses."""
    msg = (message or "").strip()
    if not msg:
        return _LLM_UNAVAILABLE
    cap = extract_capital_inr(msg)
    cap_bit = f" (₹{cap:,})" if cap else ""
    # Surface a recognised ticker if one is named explicitly.
    return (
        f"I caught your request — \"{msg[:110]}\"{cap_bit} — but the AI "
        "backend hiccuped for a moment. Say \"retry\" and I'll run it "
        "without you re-typing. (If it keeps failing you can also type a "
        "stock ticker for a snapshot, or `/screen roe > 18`.)"
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


def _release_db_conn(db: Any) -> None:
    """Hand the pooled DB connection back to the pool for the duration of
    the next LLM round-trip.

    A chat turn holds its ``Session`` (and thus one pooled connection) for
    the whole 8-15s turn, even though the DB is idle the entire time it is
    ``await``-ing the model. On a small pool (main engine: 10) that means
    ~10 in-flight turns can pin every connection while doing nothing but
    waiting on Azure. Calling this immediately before each LLM call closes
    the session — returning the connection to the pool so OTHER requests
    can use it during this turn's wait — and the session transparently
    re-acquires a fresh connection on its next query after the call.

    Safe because every tool commits its own writes (see tool_executor),
    so there is never uncommitted work to lose at an LLM-call boundary,
    and the loop carries only plain dicts/strings (never live ORM objects)
    across the await. Best-effort: never raises, so a release hiccup can
    never break a turn. No-op on the test stub store (no real Session)."""
    try:
        close = getattr(db, "close", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001 — releasing must never break a turn
        pass


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

# Provider-HOSTED tools offered on the main chat hop. When
# `web_search_enabled` is on, the LLM may invoke the Responses-API hosted
# web search (runs server-side, returns cited text + url_citations in one
# call — see llm/openai_client.py). None when off, so the tool never appears.
# Read at import (flag is env-driven); patch this constant in tests to toggle.
#
# VARIANT: the Azure gpt-5.4-mini deployment executes `web_search_preview`
# (verified 2026-07-13: 5 completed web_search_call items + a real
# url_citation to economictimes.indiatimes.com). The bare `web_search` type
# returns an EMPTY completion on this deployment — do NOT use it. Keep
# `web_search_preview` so the model actually browses (real headlines for
# market/company news, not generic reasoning).
from backend.config import settings as _settings
# search_context_size="low": the provider fetches a smaller context per
# search — measurably faster and cheaper; news/qualitative asks don't need
# the deep-research context tiers.
_HOSTED_TOOLS: "list[dict] | None" = (
    [{"type": "web_search_preview", "search_context_size": "low"}]
    if _settings.web_search_enabled else None
)


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
    # Macro-EVENT contingency phrasing detector — retained purely for
    # intent routing so a "when RBI cuts rates buy X" ask still lands in
    # the automation-builder path (not the small-talk path). The event-
    # trigger step types themselves have been removed, so the builder
    # will now propose a price/indicator/schedule trigger instead.
    r"|\b(?:when(?:ever)?|if|after|once|before)\b[^\.]{0,60}"
    r"\b(?:rbi|mpc|monetary\s+policy|repo\s+rate|"
    r"rate\s+(?:cut|hike|decision)|cpi|wpi|"
    r"inflation\s+(?:print|data|number)|fomc|"
    r"fed\s+(?:cuts?|hikes?|decision|meeting)|"
    r"union\s+budget|budget\s+(?:day|announcement)|election\s+results?)\b"
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


# ── CONSTRUCTION intent (Wave C) ───────────────────────────────────
# CONSTRUCTION = "what to own NOW": a basket / portfolio / strategy that
# expresses a view (theme, event-positioning, factor, sector, quality).
# Artifact: build_strategy → strategy_builder_card. It exists the moment
# it is built — there is NO contingent future action. This is the sibling
# of AGENT/AUTOMATION ("what to do LATER, contingently").
#
# The three gates (ALL must hold for a message to be construction):
#   1. it matches a construction shape (build-verb + strategy/basket/
#      portfolio/allocation noun, "basket/portfolio of", or a positioning
#      phrase "<strategy|basket|portfolio|stocks> that/to/which benefit/
#      profit/gain/play(s) from/on …"), AND
#   2. it has NO contingency (`_HAS_CONTINGENCY_RE`: every-<period>,
#      at-<time>, when/if-condition, alert/notify/remind/watch,
#      rebalance-<cadence>, "whenever"), AND
#   3. it names NO explicit agent noun (agent/automation/rule/bot/
#      workflow), AND
#   4. it does NOT mention F&O (options keep their existing path).
#
# Checked BEFORE the agent regex in `_classify_intent`, so "build me a
# strategy" no longer gets lumped into agent intent and stripped of the
# builder tools. The FE mode-pill override still wins downstream.
_CONSTRUCTION_INTENT_RE = re.compile(
    # (a) build-verb + construction noun (strategy / basket / portfolio /
    #     allocation / sleeve / book). Up to ~40 chars of filler between so
    #     "build me a long-term equity portfolio" still matches.
    # NOTE: "show me" is deliberately EXCLUDED — "show me my portfolio" is a
    # holdings LOOKUP, not a build. Only genuine construct verbs qualify.
    r"\b(?:build|create|make|set\s*up|setup|generate|design|construct|"
    r"assemble|put\s+together|come\s+up\s+with|give\s+me|"
    r"suggest)\b[^\.]{0,40}\b(?:strateg(?:y|ies)|basket|portfolio|"
    r"allocation|sleeve|book)\b"
    # (b) "basket/portfolio of <things>" — a direct construction ask even
    #     without a leading build verb ("a basket of monsoon winners").
    r"|\b(?:basket|portfolio)\s+of\b"
    # (c) positioning phrasing — "<noun> that/to/which benefit(s)/profit(s)/
    #     gain(s)/play(s)/capitalise(s)/win(s)/thrive(s) …". The noun anchors
    #     it to a construction artifact, not a bare data question.
    r"|\b(?:strateg(?:y|ies)|basket|portfolio|stocks?|names?|shares?)\s+"
    r"(?:that|to|which)\s+(?:benefits?|profits?|gains?|plays?|"
    r"capitali[sz]es?|wins?|thrives?|do\s+well)\b",
    re.IGNORECASE,
)

# Contingency = a stated FUTURE, conditional action. Any of these flips a
# would-be construction ask back to agent/automation (or leaves it 'other').
# Mirrors the doctrine's "contingency test".
_HAS_CONTINGENCY_RE = re.compile(
    # schedule / cadence
    r"\b(?:every|each)\s+(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|"
    r"thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|weekday|day|"
    r"week|month|quarter|year|hour|minute|morning|evening|fortnight)\b"
    r"|\b(?:daily|weekly|monthly|quarterly|fortnightly|hourly|annually)\b"
    # at-<time> / session anchors
    r"|\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|ist)\b"
    r"|\bat\s+(?:the\s+)?(?:open|close)\b"
    # runtime condition
    r"|\bwhen(?:ever)?\b|\bif\b|\bonce\b|\bas\s+soon\s+as\b"
    # alert / notify verbs (a watch, not a build)
    r"|\b(?:alert|notify|remind|watch)\b"
    # rebalance on a cadence (a hybrid workflow, not a one-shot build)
    r"|\bre-?balanc\w*\b|\brejig\w*\b",
    re.IGNORECASE,
)

# Explicit agent nouns — the user asked for an automation surface by name.
_EXPLICIT_AGENT_NOUN_RE = re.compile(
    r"\b(?:agent|automation|automate|workflow|bot)\b|\brules?\b",
    re.IGNORECASE,
)


def _is_construction_intent(message: str) -> bool:
    """True when the message is a CONSTRUCTION ask — build/own a basket,
    portfolio, or strategy expressing a view, with NO contingent future
    action. See `_CONSTRUCTION_INTENT_RE` for the doctrine.

    Shared by handle() and handle_stream() (via `_classify_intent`) so the
    two paths can never drift on this classification.
    """
    if not message:
        return False
    if not _CONSTRUCTION_INTENT_RE.search(message):
        return False
    # A stated contingent action → agent/automation, never construction.
    if _HAS_CONTINGENCY_RE.search(message):
        return False
    # An explicit agent/automation/rule/bot/workflow noun → agent.
    if _EXPLICIT_AGENT_NOUN_RE.search(message):
        return False
    # F&O mentions keep their existing options path (option "strategies").
    if _mentions_fno(message):
        return False
    return True


# Construction scope surgery — the structural enforcement that a
# construction ask CANNOT render a workflow card. Force IN the builder +
# its supporting read/vet tools; force OUT every workflow/macro/immediate-
# order tool. Applied IDENTICALLY in handle() and handle_stream() via
# `_apply_construction_scope` (the known drift trap → one function).
_CONSTRUCTION_FORCE_IN: frozenset[str] = frozenset({
    "build_strategy", "ask_user_dynamic",
    "screen_fundamentals", "fetch_fundamentals",
    "get_multiple_indicators", "get_performance_metrics",
    "compare_performance", "get_price_history", "get_live_price",
    # consolidated equivalents (chat-kernel Phase 1 + round 2)
    "get_indicators", "get_market_data", "query_financials", "calculate",
})
_CONSTRUCTION_FORCE_OUT: frozenset[str] = frozenset({
    # workflow / macro drafters — a construction ask is not a contingent
    # rule, so none of these may render. propose_basket_allocation is a
    # workflow drafter too (it emits a workflow_draft_card): a plain,
    # no-cadence sector basket must go through build_strategy →
    # strategy_builder_card, NOT a workflow card. Cadence-ask basket routes
    # (which are NOT construction — a contingency is present) still reach
    # propose_basket_allocation via the tool_router basket rule.
    "propose_basket_allocation",
    "propose_workflow", "propose_dsl_workflow",
    "propose_scheduled_order", "propose_threshold_order",
    "propose_holding_action",
    # immediate order tools — a build is register-not-execute via the card
    # actions, never a live order this turn.
    "place_market_order", "place_limit_order", "place_order",
    "create_gtt_order",
    "create_sl_order", "create_oco_order", "create_dip_buy",
    "place_basket_order", "create_sip", "squareoff_all_intraday",
    "squareoff_symbol",
})


def _apply_construction_scope(
    selected_names: Optional[frozenset],
) -> Optional[frozenset]:
    """Force the construction toolset: builder + read/vet tools IN,
    workflow/macro/immediate-order tools OUT. No-op in whitelist mode
    (selected_names is None) — the full registry already has every path
    and the guards + reply-class steer the model."""
    if selected_names is None:
        return selected_names
    return (frozenset(selected_names) | _CONSTRUCTION_FORCE_IN) - _CONSTRUCTION_FORCE_OUT


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

# "Buy NOW + a flat stop/target on that same buy" — e.g. "buy 10 INFY now
# and sell it if it falls 5%". _AGENT_INTENT_RE's percentage-conditional
# branch (below) treats any "if X falls N%" as needing a runtime fetch of
# a baseline price, which is right for a NEW conditional entry but wrong
# here: the buy fires this turn, so the % is just off the fill price —
# exactly what create_sl_order already does, no watcher needed.
#
# Misfire cost is HIGH (automation intent strips ALL workflow drafters
# from scope), so the match is deliberately strict:
#   - the segment between "buy N SYM" and "and" must contain NO condition
#     or indicator word — a conditional ENTRY ("buy 5 X on RSI below 35
#     and exit if …") must stay agent;
#   - the exit must be a flat % (peak-relative / trailing exits are
#     rejected by _POSITION_RELATIVE_EXIT_RE at the call site — those
#     need the position-aware watcher, create_sl_order can't trail).
# A conservative MISS here is fine — it falls through to _AGENT_INTENT_RE
# and over-drafts a workflow, the documented safe direction.
_IMMEDIATE_BUY_WITH_FLAT_STOP_RE = re.compile(
    r"\b(?:buy|long)\b\s+\d+\s+\w+"
    r"(?:(?!\b(?:if|when(?:ever)?|once|rsi|sma|ema|macd|crosses?|breaks?"
    r"|dips?|drops?|falls?|rises?|below|above)\b)[^\.]){0,80}?"
    r"\b(?:and|&)\b[^\.]{0,40}"
    r"\b(?:sell|exit)\b[^\.]{0,40}"
    r"\b(?:if|when(?:ever)?)\b[^\.]{0,30}"
    r"\b(?:dips?|drops?|falls?|declines?|rises?|gains?)\b\s*\d+\s*%",
    re.IGNORECASE,
)
# Position-relative / trailing exit markers — these need the workflow
# engine's position-aware watcher (drawdown_from_peak_pct etc.), so they
# disqualify the flat-stop shortcut above.
_POSITION_RELATIVE_EXIT_RE = re.compile(
    r"\bfrom\s+(?:its\s+|the\s+)?(?:peak|high|top|entry)\b|\btrail",
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


# ── Read-intent gates (51-sweep work order, 2026-07-10) ──────────────
# The sweep found the model FABRICATING agents on "what agents do I have
# running?" (tools_called=[]) and clarifying instead of reading on
# portfolio/series/analyse asks. These gates make the read STRUCTURAL:
# force the right tool into scope, force tool_choice=required, drop the
# bare ASK_USER escape, and pin a one-line directive.

_LIFECYCLE_READ_RE = re.compile(
    r"\b(?:what|which|list|show(?:\s+me)?)\b[^.?!]{0,50}"
    r"\b(?:agents?|automations?|workflows?|sips?|strategies)\b"
    r"|\b(?:agents?|automations?|workflows?|sips?|strategies)\b"
    r"[^.?!]{0,30}\b(?:running|active|do\s+i\s+have)\b"
    r"|\bmy\s+(?:running\s+|active\s+)?(?:agents?|automations?|workflows?)\b",
    re.IGNORECASE,
)
_PORTFOLIO_READ_RE = re.compile(
    r"\bhow(?:'s|\s+is)\s+my\s+portfolio\b"
    r"|\bmy\s+portfolio\s+(?:doing|performing|looking)\b"
    r"|\b(?:show|check)\s+my\s+(?:portfolio|holdings|positions)\b"
    r"|\bam\s+i\s+(?:in\s+)?(?:profit|loss|up\s+or\s+down)\b"
    r"|\bmy\s+(?:p&l|pnl|overall\s+returns?)\b",
    re.IGNORECASE,
)
_FIN_SERIES_DIRECT_RE = re.compile(
    r"\b(?:year\s+by\s+year|per\s+year|yearly|annually|"
    r"(?:over|for|across)\s+the\s+last\s+\d+\s+years?)\b",
    re.IGNORECASE,
)
_SINGLE_ANALYSE_RE = re.compile(
    r"\b(?:analy[sz]e|analysis\s+of|deep\s+dive\s+(?:on|into))\b",
    re.IGNORECASE,
)
_COMPARISON_MARKER_RE = re.compile(
    r"\b(?:vs\.?|versus|compare|compared|better\s+than|against)\b",
    re.IGNORECASE,
)
# Bare ticker-shaped tokens (all-caps, 2-15 letters) — a cheap proxy for
# "this message names multiple companies", used to decide whether a
# comparison marker is a genuine two-stock ask vs. a generic comparison
# ("SIP vs lump sum") that shouldn't force compare_performance.
_TICKER_TOKEN_RE = re.compile(r"\b[A-Z]{2,15}\b")
_TICKER_TOKEN_STOPWORDS = frozenset({
    "SIP", "ETF", "MF", "IPO", "PE", "PB", "ROE", "ROCE", "EPS", "NSE",
    "BSE", "CNC", "MIS", "VS", "GMP", "SL", "TP", "OI", "IV", "ATM", "ITM",
    "OTM", "RSI", "SMA", "EMA", "MACD", "PSU", "IT", "FMCG",
})
_OWNERSHIP_ASK_RE = re.compile(
    r"\bpromoters?\b.{0,15}\b(?:holding|stake|ownership|pledg\w*)\b"
    r"|\bpledg(?:e|ed|ing)\w*\b"
    r"|\bshareholding\s+pattern\b"
    r"|\binstitutional\s+holding\b",
    re.IGNORECASE,
)


def _named_symbol_count(message: str) -> int:
    """Cheap heuristic count of distinct ticker-shaped tokens in a
    message — NOT a real symbol resolver, just enough signal to tell a
    genuine two-stock comparison from a generic "X vs Y" phrasing that
    doesn't name companies."""
    tokens = set(_TICKER_TOKEN_RE.findall(message or ""))
    return len(tokens - _TICKER_TOKEN_STOPWORDS)


def _read_intent_gate(
    message: str, selected_names: Optional[set],
) -> Optional[tuple[set, str, str]]:
    """(new_selected_names, tool_choice, directive) when a read gate
    fires, else None. First match wins; gates are mutually exclusive in
    practice."""
    if selected_names is None:
        return None
    msg = message or ""
    if _LIFECYCLE_READ_RE.search(msg):
        return (
            selected_names | {"manage_automation"},
            "required",
            "## Lifecycle read — call the tool, never recall\n"
            "The user asks what automations/SIPs/agents they have. You "
            "MUST call manage_automation(action='list', ...) and report "
            "ONLY what it returns. If it returns none, say none are "
            "running. NEVER name agents from memory or conversation — "
            "that fabricates.",
        )
    if _PORTFOLIO_READ_RE.search(msg):
        return (
            selected_names | {"get_portfolio"},
            "required",
            "## Portfolio read — call the tool, never recall\n"
            "Call get_portfolio(view='summary') and report only its real "
            "numbers (paper-labeled). If empty, say the portfolio is "
            "empty. Do not ask clarifying questions first.",
        )
    if (_FIN_SERIES_DIRECT_RE.search(msg)
            and "query_financials" in selected_names):
        return (
            selected_names,
            "required",
            "## Series ask is fully specified — JUST DO IT\n"
            "The user named the metric and window. Call query_financials "
            "now. Do NOT offer alternative metrics or comparisons first.",
        )
    if (_SINGLE_ANALYSE_RE.search(msg)
            and not _COMPARISON_MARKER_RE.search(msg)):
        return (
            (selected_names
             | {"fetch_fundamentals", "get_market_data",
                "get_symbol_news", "get_indicators"})
            - {"compare_performance", "get_correlation_matrix"},
            "required",
            "## Single-stock analysis — fundamentals+price+news flow\n"
            "This is a ONE-stock analysis: start with get_market_data / "
            "fetch_fundamentals / get_symbol_news (comparison tools are "
            "out of scope this turn), then write the full sectioned "
            "ANALYSIS with a defended view.",
        )
    # Multi-stock comparison ("BAJFINANCE vs BAJAJFINSV", "compare X and
    # Y") — eval50 (2026-07-14) found this cited precise AUM/PAT/ROE/
    # GNPA/NNPA/technicals with ZERO fundamentals/indicator tool called,
    # a fabrication. `_COMPARISON_MARKER_RE` already existed but was only
    # ever used to CARVE comparisons OUT of the single-analyse gate above
    # — never as a positive gate of its own. Symbol-count-gated so a
    # generic "SIP vs lump sum" doesn't force compare_performance.
    if (_COMPARISON_MARKER_RE.search(msg)
            and _named_symbol_count(msg) >= 2
            and "compare_performance" in selected_names):
        return (
            selected_names | {"compare_performance"},
            "required",
            "## Multi-stock comparison — call the tool, never recall\n"
            "Call compare_performance with ALL named symbols and report "
            "only its real returned numbers. NEVER state one symbol's "
            "data from memory while only fetching the other — that "
            "fabricates.",
        )
    # Ownership/promoter/pledge ask — eval50 found a fabricated pledge %
    # for ZEEL with zero tools fired. Pivot's fundamentals data carries
    # promoter_holding_pct / institution_holding_pct but NOT a pledge
    # field; the directive both forces the real fetch and stops the
    # model from inventing the untracked pledge figure afterward.
    if (_OWNERSHIP_ASK_RE.search(msg)
            and "fetch_fundamentals" in selected_names):
        return (
            selected_names | {"fetch_fundamentals"},
            "required",
            "## Ownership read — call the tool, never recall\n"
            "Call fetch_fundamentals for the named symbol and report "
            "promoter_holding_pct / institution_holding_pct as the "
            "approximate-proxy ownership figures. Pivot does NOT track "
            "promoter PLEDGE percentage — if pledge specifically was "
            "asked, give the real holding % and say pledge isn't "
            "tracked; never invent a pledge number.",
        )
    return None


def _summary_bridge_block(conv_id: str, user_id: int,
                          history_override) -> str:
    """Chat-kernel A2 (2026-07-10): bridge the 6-turn context cliff.

    The ChatSummary rows have existed since migration 0022 but were
    WRITE-ONLY — generated for the UI, never injected into a turn (the
    2026-07-03 platform audit's #2 chat finding). When older turns were
    truncated away by CONV_PROMPT_WINDOW_TURNS, inject the stored
    summary READ-ONLY (one indexed PG lookup; generation stays off the
    hot path — the /chat router refreshes it in a background task).
    """
    overflow = (
        history_override is not None
        and len(history_override) > CONV_PROMPT_WINDOW_TURNS * 2
    )
    if not overflow:
        try:
            overflow = default_store().history_overflows(conv_id)
        except Exception:
            overflow = False
    if not overflow:
        return ""
    # ChatSummary rows key on the RAW client conversation id (the router
    # persists Conversation with it); handle() receives the u{uid}::
    # namespaced form — strip the namespace for the lookup.
    raw_id = conv_id.split("::", 1)[1] if "::" in conv_id else conv_id
    if not raw_id:
        return ""
    try:
        from backend.database import SessionLocal
        from backend.models import ChatSummary

        db = SessionLocal()
        try:
            row = (
                db.query(ChatSummary.summary)
                .filter(
                    ChatSummary.conversation_id == raw_id,
                    ChatSummary.user_id == user_id,
                )
                .first()
            )
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — the bridge must never break a turn
        logger.warning("summary bridge lookup failed: %s", e)
        return ""
    if not row or not row[0]:
        return ""
    return (
        "## Earlier in this conversation (summary of truncated turns)\n"
        + str(row[0])[:1200]
        + "\n(The turns shown below are the most recent ones; when this "
        "summary conflicts with them, the visible turns win.)"
    )


def _session_state_blocks(store, conv_id: str) -> list[str]:
    """System blocks that make per-conversation state VISIBLE to the model
    (container eval 2026-07-19). Pure context injection — no decisions:

    1. Artifact ledger — every card/draft THIS conversation produced, one
       line each, so a basket built 10 turns ago survives the history
       window/clamp and "that basket" resolves without re-asking.
    2. Pending clarify — when a clarify card is on screen, the model (not
       a regex) decides whether the new message answers it or is a new
       request. Free text falls through to the LLM with this block.
    """
    blocks: list[str] = []
    try:
        get_arts = getattr(store, "get_artifacts", None)
        arts = get_arts(conv_id) if callable(get_arts) else []
        if arts:
            blocks.append(
                "## Artifacts created in THIS conversation (most recent last)\n"
                + "\n".join(f"- {a}" for a in arts[-12:])
                + "\n(When the user says 'that basket/draft/backtest', it means "
                "one of these — do NOT ask them to re-list its contents.)"
            )
    except Exception:  # visibility must never break a turn
        pass
    try:
        get_clarify = getattr(store, "get_clarify", None)
        state = get_clarify(conv_id) if callable(get_clarify) else None
        if state is not None:
            qs = [q for q in (state.questions or []) if isinstance(q, dict)]
            idx = max(0, min(int(state.index or 0), max(len(qs) - 1, 0)))
            current_q = (qs[idx].get("prompt") or qs[idx].get("question") or "?") if qs else "?"
            slots_json = json.dumps(state.slot_state or {})[:600]
            build_tool = getattr(state, "build_tool", None) or (
                "propose_workflow" if getattr(state, "kind", "portfolio") == "agent"
                else "build_strategy")
            blocks.append(
                "## A clarify question is pending on screen\n"
                f"Original request: {str(getattr(state, 'request', ''))[:200]}\n"
                f"Current question: {current_q}\n"
                f"Slots so far: {slots_json}\n"
                f"- If the user's message ANSWERS the question, continue that "
                f"flow: call `{build_tool}` with the original request plus all "
                "known slot values including this answer (or ask the next "
                "genuinely-missing thing).\n"
                "- If it is a NEW, unrelated request, handle it normally — the "
                "clarify card stays available and must not swallow the new "
                "intent.\n"
                "- Never treat an order/automation/backtest instruction as a "
                "slot answer."
            )
    except Exception:
        pass
    return blocks


def _redirect_target_for_failure(
    tool_name: str, error: str, user_message: str,
    structured: Optional[str] = None,
) -> Optional[str]:
    """Pick the tool to redirect to after ``tool_name`` failed, or None.

    Primary signal (chat-kernel 2026-07-10): the tool's own typed
    ``redirect_to`` (raised as ToolRedirect, threaded through
    GuardedToolResult) — cannot be severed by truncation.

    Secondary signal: an explicit "use <tool>" hint in the error string
    (legacy raise-sites still emit these to steer the LLM).

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
    if structured:
        return structured
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

# H1: tools stripped on a hedge-construction turn. The screenshot bug
# was a schedule-BUY of the very symbols being hedged — structurally
# possible only because the order macros were in scope. On the hedge
# turn the model must explain + build the offsetting leg (options
# surface stays in scope); a follow-up turn that picks a cash-equity
# route ("ok, monthly GOLDBEES instead") no longer matches the hedge
# detector, so the macros return for the actual build.
_HEDGE_STRIP_TOOLS: frozenset[str] = frozenset({
    "propose_workflow", "propose_scheduled_order",
    "propose_threshold_order", "propose_basket_allocation",
    "propose_holding_action", "propose_dsl_workflow",
    "place_market_order", "place_limit_order", "place_order",
    "create_gtt_order", "create_sl_order", "create_oco_order",
    "create_dip_buy", "place_basket_order", "create_sip",
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


# ── R4b: VIEW-based option-strategy ask detector ────────────────────────
# "create me a bullish option strategy on nifty" names a VIEW (bullish),
# not a template (_is_named_option_build only fires for "iron condor" /
# "straddle" / etc.) — so it fell through to tool_choice="auto" and, under
# the prompt's heavy fabrication-avoidance framing, the model sometimes
# answered with a hedged "I can't provide live data" non-answer instead of
# calling suggest_option_strategy, even though the SAME phrasing succeeds
# in other sessions (pure tool-choice nondeterminism, not a real data
# outage — reported 2026-07-14). Forces the tool the same deterministic
# way R4 does for named templates.
_OPTION_VIEW_RE = re.compile(
    r"\b(?:bullish|bearish|neutral|range-?bound|non-?directional|"
    r"volatile|volatility|income)\b",
    re.IGNORECASE,
)
_OPTION_SUGGEST_VERB_RE = re.compile(
    r"\b(?:build|make|create|set\s*up|construct|give\s+me|suggest|"
    r"design|recommend|propose|what\s+should\s+i)\b",
    re.IGNORECASE,
)


def _is_option_view_ask(message: str) -> bool:
    """True when the user asks for a VIEW-based option strategy (a
    directional/volatility stance + an underlying + a build/suggest verb)
    rather than a named template. Deterministically routes to
    suggest_option_strategy instead of leaving tool_choice at "auto"."""
    msg = (message or "").strip()
    if not msg or not _mentions_fno(msg):
        return False
    if not _OPTION_VIEW_RE.search(msg):
        return False
    if not _OPTION_UNDERLYING_RE.search(msg):
        return False
    return bool(_OPTION_SUGGEST_VERB_RE.search(msg))


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
    # UPI round-ups / spare change / %-of-spend (no UPI rail)
    r"upi\s+(?:data|volume|transactions?|spend|round[\s-]?ups?)|"
    r"round[\s-]?ups?|spare\s+change|%\s*of\s+(?:my\s+)?(?:upi\s+)?spend|"
    r"percentage\s+of\s+(?:my\s+)?(?:upi\s+)?spend|"
    # Macro feed
    r"gdp\s+(?:print|data)|inflation\s+(?:print|data)\s+(?:above|below)|"
    # Broker auto-execute / fire-and-forget (register-not-execute boundary)
    r"auto[\s-]?execute|fire[\s-]?and[\s-]?forget|"
    r"(?:execute|place|buy|sell|trade)\s+(?:it\s+)?(?:directly|automatically|"
    r"auto)\s+(?:in|on|via|through)?\s*(?:zerodha|kite|dhan|upstox|groww|"
    r"my\s+broker)|"
    r"without\s+(?:my\s+)?confirmation|no\s+confirmation\s+needed|"
    r"don'?t\s+(?:ask|wait\s+for)\s+(?:me\s+)?(?:to\s+)?confirm"
    r")\b",
    re.IGNORECASE,
)


def _names_unsupported_rail(message: str) -> Optional[str]:
    """Return a short rail label when the message asks for an
    unsupported automation trigger rail (sentiment NLP, IV-rank,
    macro feed, UPI round-ups, broker auto-execute), else None. Used to
    force a boundary-first reply that names the nearest real alternative
    BEFORE any value question — never affirms a fabricated capability."""
    msg = (message or "")
    if not _UNSUPPORTED_RAIL_RE.search(msg):
        return None
    low = msg.lower()
    if (
        "auto-execute" in low or "auto execute" in low
        or "fire and forget" in low or "fire-and-forget" in low
        or "without confirmation" in low or "without my confirmation" in low
        or "no confirmation" in low
        or re.search(r"(?:execute|place|buy|sell|trade)\s+(?:it\s+)?"
                     r"(?:directly|automatically|auto)", low)
    ):
        return "auto_execute"
    if "iv rank" in low or "iv percentile" in low:
        return "iv_rank"
    if ("upi" in low or "round up" in low or "round-up" in low
            or "spare change" in low or "of spend" in low):
        return "upi_roundup"
    if "gdp" in low or "inflation" in low:
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


# A trade verb anywhere flips an "alert" into an order intent ("buy 5 X
# when it crosses 420", "sell when it drops to 1380"). Used to keep the
# relaxed alert gate from swallowing genuine order builds.
_TRADE_VERB_RE = re.compile(
    r"\b(?:buy|sell|purchase|short|long|enter|exit|book|square|"
    r"accumulate|add|trade|place\s+(?:an?\s+)?order|go\s+long|go\s+short)\b",
    re.IGNORECASE,
)
# A leading alert verb in the first ~4 words = the PRIMARY intent is to be
# notified. "alert me when COALINDIA crosses 420", "ping me if HCLTECH
# drops to 1380", "let me know when EICHERMOT hits 4500".
_LEADING_ALERT_RE = re.compile(
    r"^\W*(?:alert|ping|notify|tell|let|remind|heads?\s*up|just\s+watch|"
    r"watch|flag|warn)\b",
    re.IGNORECASE,
)


def _is_notify_only_alert(message: str) -> bool:
    """True when the message is a notify-only price alert that must
    register a notify_only DSL workflow rather than an order.

    Two ways to qualify:
      1. Explicit: an alert verb + a price level + a no-trade marker
         ("just alert me when X crosses 420, don't buy").
      2. Leading-intent: the message OPENS with an alert verb and carries
         a price level, with NO trade verb anywhere ("alert me when
         COALINDIA crosses 420", "ping me if HCLTECH drops to 1380").
         Alerts never trade, so a quantity question is wrong — these were
         misrouting to propose_threshold_order.
    """
    msg = (message or "").strip()
    if not msg:
        return False
    if not _ALERT_VERB_RE.search(msg):
        return False
    if not _PRICE_LEVEL_RE.search(msg):
        return False
    if _NO_TRADE_MARKER_RE.search(msg):
        return True
    # Relaxed leading-intent gate: opens with an alert verb, no trade verb.
    if _LEADING_ALERT_RE.search(msg) and not _TRADE_VERB_RE.search(msg):
        return True
    return False


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


# Hedge-construction detector — "make me a strategy to hedge my HDFC
# position". Screenshot regression: the model parsed "hedge against
# HDFCBANK + ICICIBANK" into a schedule-BUY of those very symbols —
# the literal opposite of a hedge. The directive built in
# _build_deterministic_guards explains what a hedge must do; the
# routing layer pairs it with scope narrowing (strip the order macros,
# add the options surface) so the broken draft is structurally
# impossible on the hedge turn. Excludes "sell/exit/close my hedge"
# (managing an EXISTING hedge, e.g. the event-resolution flow).
_HEDGE_REQUEST_RE = re.compile(
    r"\bhedg(?:e|ing|ed)\b", re.IGNORECASE,
)
_HEDGE_MANAGE_RE = re.compile(
    r"\b(?:sell|exit|close|unwind|remove|book)\b[^.?!]{0,30}\bhedge\b",
    re.IGNORECASE,
)


def _is_hedge_request(message: str) -> bool:
    """True when the user asks to CONSTRUCT a hedge (vs manage an
    existing one). Kept broad on purpose — the guard only shapes HOW
    a hedge is built, so a false positive costs one explanatory
    sentence, while a miss re-ships the buy-the-same-stock bug."""
    msg = message or ""
    if not _HEDGE_REQUEST_RE.search(msg):
        return False
    return not _HEDGE_MANAGE_RE.search(msg)


# Acceptance of the "say the word and I'll build the same for <other>"
# offer the H1 directive scripts. Without this, the follow-up ("yes add
# the same for the other one") carries no hedge word, the guard doesn't
# fire, and the generic draft machinery routes it to propose_workflow —
# a workflow card where the user expects the second option-strategy
# card (observed live).
_HEDGE_FOLLOWUP_MSG_RE = re.compile(
    r"\b(?:same|other|both|second)\b", re.IGNORECASE,
)
_HEDGE_OFFER_RE = re.compile(
    r"(?:say\s+the\s+word|build\s+the\s+same|same\s+(?:hedge\s+)?for)",
    re.IGNORECASE,
)


def _is_hedge_followup(message: str, history: list) -> bool:
    """True when a SHORT reply accepts the prior assistant turn's offer
    to build the same hedge for the other name."""
    msg = (message or "").strip()
    if not msg or len(msg.split()) > 14:
        return False
    if not _HEDGE_FOLLOWUP_MSG_RE.search(msg):
        return False
    for h in reversed(history or []):
        if h.get("role") != "assistant":
            continue
        prev = (h.get("content") or "").lower()
        return (
            ("hedge" in prev or "protective put" in prev)
            and bool(_HEDGE_OFFER_RE.search(prev))
        )
    return False


# Strategy-framed build detector — the user asked for a "strategy"
# (diversify / rebalance / hedge / allocate), not a mechanical order.
# Screenshot regression: "build me a strategy to diversify" → card
# shipped with a one-line "Drafted: …" handoff and zero explanation
# of the strategy. The POST-DRAFT FLOOR's 2-sentence cap is the wrong
# shape for these turns; the guard lifts it. Checks RECENT USER turns
# too because the draft usually lands on an affirmative follow-up
# ("yes make a concrete strategy") that carries none of the framing.
_STRATEGY_FRAMED_RE = re.compile(
    r"\b(?:strateg(?:y|ies)|diversif\w*|re-?balanc\w*|hedg(?:e|ing)|"
    r"allocat\w*|asset\s+mix|"
    # build_strategy / propose_basket_allocation framings: a "portfolio"
    # or a multi-name "basket" is a thoughtful strategy build, not a
    # mechanical order — it earns the strategy-explain guard + the
    # high-cap strategy reply budget.
    r"portfolio|basket)\b",
    re.IGNORECASE,
)


def _is_strategy_framed(message: str, history: list) -> bool:
    """True when this turn — or one of the last 3 user turns — frames
    the ask as a STRATEGY rather than a single mechanical order."""
    if _STRATEGY_FRAMED_RE.search(message or ""):
        return True
    seen = 0
    for h in reversed(history or []):
        if h.get("role") != "user":
            continue
        if _STRATEGY_FRAMED_RE.search(h.get("content") or ""):
            return True
        seen += 1
        if seen >= 3:
            break
    return False


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
    "place_market_order", "place_limit_order", "place_order",
    "create_gtt_order",
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

# R4/F15: workflow-shaped macro drafts the deterministic register path
# can arm with register_workflow. These all emit a workflow_draft_card
# carrying `steps`. Option/backtest drafts are EXCLUDED — they register
# through their own card endpoints, not register_workflow.
_REGISTERABLE_DRAFT_TOOLS: frozenset[str] = frozenset({
    "propose_workflow", "propose_dsl_workflow", "propose_threshold_order",
    "propose_scheduled_order", "propose_basket_allocation",
    "propose_holding_action",
})

# ── Track C #2: addressable multi-draft helpers ──────────────────────


def _draft_primary_symbol(draft: dict) -> str:
    """Best-effort primary symbol for a draft payload — the addressing
    key in the per-conversation draft map. Checks the top-level symbol
    / underlying first, then the first step config that names one."""
    if not isinstance(draft, dict):
        return ""
    for key in ("symbol", "underlying"):
        v = draft.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().upper()
    for step in draft.get("steps") or []:
        cfg = (step or {}).get("config") if isinstance(step, dict) else None
        if isinstance(cfg, dict):
            for key in ("symbol", "underlying"):
                v = cfg.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip().upper()
    return ""


# Named back-reference cues around a symbol token: "the INFY one",
# "INFY wala", "change the INFY draft", "for INFY". Resolution itself
# just checks whether a parked draft's symbol appears as a word in the
# message — these cues are documentation of the shapes we cover.
def _symbol_mentioned(message: str, symbol: str) -> bool:
    if not symbol:
        return False
    return bool(re.search(
        rf"\b{re.escape(symbol)}\b", message, re.IGNORECASE,
    ))


# R4/C2: change-verb vs keep-marker tokens, used to disambiguate WHICH of
# several named drafts an amendment targets. "change the INFY one to 8
# shares, WIPRO wala same rehne do" mentions BOTH symbols — the prior
# resolver bailed (len(named)==2) and amended the most-recent draft
# (WIPRO), then the prose lied ("WIPRO unchanged"). The fix binds the
# CHANGE verb to the symbol nearest it and treats a KEEP marker near
# the other symbol as an explicit "leave it alone".
_AMEND_CHANGE_TOKENS = (
    "change", "make", "set", "update", "edit", "tweak", "adjust", "raise",
    "lower", "increase", "decrease", "reduce", "bump", "switch", "kar do",
    "kardo", "karo", "badal",
)
_AMEND_KEEP_TOKENS = (
    "same rehne", "rehne do", "rakho", "untouched", "leave", "keep",
    "as is", "as-is", "stays the same", "stay the same", "don't change",
    "dont change", "unchanged", "wahi rehne", "same rakhna", "wala same",
    "same", "rehne",
)


def _resolve_amend_target_symbol(
    message: str, symbols: list[str],
) -> Optional[str]:
    """Given an amendment that names ≥2 parked-draft symbols, return the
    ONE symbol the change actually targets, or None if it can't be
    resolved cleanly.

    Heuristic: for each named symbol, find the nearest preceding/following
    change token and the nearest keep token; the symbol whose closest cue
    is a CHANGE (and not overridden by a closer KEEP) wins, provided
    exactly one symbol resolves that way. A symbol explicitly marked KEEP
    is never the target.
    """
    low = (message or "").lower()
    syms = [s for s in symbols if s]
    if len(syms) < 2:
        return None

    def _nearest(tokens: tuple[str, ...], sym_pos: int) -> int:
        best = 10**9
        for tok in tokens:
            start = 0
            while True:
                idx = low.find(tok, start)
                if idx == -1:
                    break
                best = min(best, abs(idx - sym_pos))
                start = idx + 1
        return best

    change_bound: list[str] = []
    keep_bound: set[str] = set()
    for sym in syms:
        m = re.search(rf"\b{re.escape(sym)}\b", low, re.IGNORECASE)
        if not m:
            continue
        pos = m.start()
        d_change = _nearest(_AMEND_CHANGE_TOKENS, pos)
        d_keep = _nearest(_AMEND_KEEP_TOKENS, pos)
        if d_keep <= d_change and d_keep < 10**9:
            keep_bound.add(sym.upper())
        elif d_change < d_keep:
            change_bound.append(sym)

    # Exactly one change-bound symbol that isn't also keep-marked → target.
    candidates = [s for s in change_bound if s.upper() not in keep_bound]
    if len(candidates) == 1:
        return candidates[0]
    # Fallback: exactly one symbol NOT keep-marked → it's the implied target.
    not_kept = [s for s in syms if s.upper() not in keep_bound]
    if keep_bound and len(not_kept) == 1:
        return not_kept[0]
    return None


# ── Track C #1: register / status intent cues ────────────────────────

# Deterministic ARM command on an active draft. Whole-message match —
# short imperative only, so longer prompts still go to the LLM.
# R4/F15: the leading-ack prefix used to allow only ok|yes|please|haan,
# so "looks good, go ahead and register it" / "perfect, activate it"
# (the canonical confirm phrasing) FAILED this match and fell through to
# the 0-token canned "click Save & activate" line instead of actually
# arming the draft. Widen the ack clause to the same vocabulary
# _PURE_AFFIRMATIVE_RE recognises (looks good / sounds good / perfect /
# great / cool / nice / sure / fine / alright) plus a comma|dash|colon
# separator, so the affirm-and-register compound reaches the
# deterministic register path.
_REGISTER_ACK_PREFIX = (
    r"(?:(?:ok(?:ay)?|yes|yeah|yep|yup|sure|fine|alright|please|haan|"
    r"looks?\s+good|sounds?\s+good|perfect|great|cool|nice|got\s+it|"
    r"awesome|sweet)\s*[,!;:.\-—]?\s+)*"
)
_REGISTER_DRAFT_RE = re.compile(
    r"^" + _REGISTER_ACK_PREFIX +
    r"(?:go\s+ahead(?:\s+and\s+(?:register|activate|arm|save)\s*(?:it|this|that)?)?"
    r"|register\s*(?:it|this|that|karo|kar\s*do|the\s+(?:agent|workflow|automation|draft|rule))?"
    r"|activate\s*(?:it|this|that|the\s+(?:agent|workflow|automation|draft|rule))?"
    r"|arm\s+(?:it|this|that)"
    r"|make\s+(?:it|this)\s+live"
    r"|set\s+(?:it|this)\s+live"
    r"|turn\s+(?:it|this)\s+on"
    r"|save\s*(?:&|and)\s*activate(?:\s+it)?"
    r")\s*[.!]*\s*$",
    re.IGNORECASE,
)

# Armed-state introspection: "is it actually live?", "when do you
# check?", "how often is it evaluated?", "status of my agent".
_WF_STATUS_RE = re.compile(
    r"\bis\s+(?:it|that|this|the\s+(?:agent|workflow|automation|rule)|"
    r"my\s+(?:agent|workflow|automation|rule))\s+"
    r"(?:actually\s+|really\s+)?(?:live|running|armed|active|on|working)\b"
    r"|\bwhen\s+do(?:es)?\s+(?:you|it|pivot)\s+(?:check|evaluate|poll|scan|look)\b"
    r"|\bhow\s+often\b[^?.]{0,60}\b(?:check|checked|evaluate|evaluated|"
    r"poll|polled|run|scan)"
    r"|\b(?:status|state)\s+of\s+(?:my|the|that)\s+"
    r"(?:agent|workflow|automation|rule|trigger)\b",
    re.IGNORECASE,
)


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
    """Return one of {'construction', 'agent', 'automation', 'other'}.

    Under `llm_owned_interpretation` always returns 'other': the model
    interprets the ask itself (see _LLM_OWNED_DIRECTIONS) and no
    intent-keyed tool-surface surgery runs. The FE mode pill still
    overrides downstream — an explicit user pick is not interpretation.

    CONSTRUCTION wins over agent — "build me a strategy/basket/portfolio"
    with no contingent action is a basket-build (build_strategy →
    strategy_builder_card), NOT a workflow draft. It is checked BEFORE the
    agent regex so the word "strategy" no longer routes to propose_workflow
    by default.

    Otherwise agent wins ties — better to over-draft a workflow than
    misfire a single immediate tool. 'other' covers data lookups,
    conversation, explanations.

    EXCEPT: a two-action basket NOW ("buy 7 reliance and sell 2
    eternal") with no scheduling or conditional language is a pair
    of immediate market orders, not a workflow. The previous regex
    caught the buy+and+sell shape and routed it to propose_workflow,
    which then asked for permission and built a daily 15:25 agent
    around it (PDF report).
    """
    if not message or _settings.llm_owned_interpretation:
        return "other"
    if (
        _TWO_ACTION_NOW_RE.search(message)
        and not _HAS_SCHEDULE_OR_CONDITION_RE.search(message)
    ):
        return "automation"
    if (
        _IMMEDIATE_BUY_WITH_FLAT_STOP_RE.search(message)
        and not _RECURRING_SCHEDULE_RE.search(message)
        and not _POSITION_RELATIVE_EXIT_RE.search(message)
    ):
        return "automation"
    # CONSTRUCTION is checked BEFORE the agent regex: a build/basket/
    # portfolio/positioning ask with no contingency, no explicit agent
    # noun, and no F&O mention is "what to own now", not a workflow.
    if _is_construction_intent(message):
        return "construction"
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
    # "X vs Y" comparison — the VERB too: "compare it with ONGC" fell to
    # analytical_short (≤120w) and produced the thinnest reply of all
    # (user-reported 2026-07-10) because only the noun forms matched.
    r"\bcompare\b|\bvs\.?\b|\bversus\b|\bcomparison\b|"
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

# Ranked-list DATA reads — "top gainers/losers/movers", "most active",
# "biggest gainers today", "who's moving". The tool returns a ranked list that
# reads best as a compact table (rank · symbol · LTP · change%); the default
# analytical_short class (≤120 words, prose-only, no headings) forces the model
# to cram two tables into a 120-word budget and it truncates mid-table. Route
# these to the table-friendly 'list_read' budget instead. (Fundamental screens —
# "cheapest banks by P/E" — are handled separately via the analysis subhint.)
_LIST_READ_RE = re.compile(
    r"\b(?:top|biggest|best|worst|leading)\s+(?:\d+\s+)?"
    r"(?:gainers?|losers?|movers?|advancers?|decliners?)\b"
    r"|\bgainers?\s+(?:and|&|/|,)\s*losers?\b"
    r"|\bmost\s+active\b"
    r"|\bwho'?s\s+moving\b",
    re.IGNORECASE,
)


def _classify_reply_class(message: str, intent_kind: str) -> str:
    """Return one of {'draft', 'automation', 'backtest', 'explainer',
    'capability', 'small_talk', 'analysis', 'analytical_short'}.

    NOTE: the high-cap 'strategy' class is NOT returned here — a
    strategy/basket build classifies as intent_kind='agent' → 'draft'.
    The caller upgrades it to 'strategy' via `_is_strategy_framed`
    AFTER calling this, so the builder path gets the 3800-token budget
    (see the STRATEGY budget override in handle() / the stream path).

    The first three mirror intent_kind (with 'agent' renamed to 'draft'
    for clarity at the reply-budget layer); the rest sub-classify the
    'other' bucket so each shape gets a fitting length + format budget.

    Under `llm_owned_interpretation` returns 'model_owned' — one generous
    budget + a sizing DIRECTION; the model decides length and shape.
    """
    if _settings.llm_owned_interpretation:
        return "model_owned"
    if intent_kind == "agent":
        return "draft"
    if intent_kind == "construction":
        # A basket/portfolio/strategy build earns the high-cap 'strategy'
        # budget (connection + rationale + winners/losers table + card
        # readback). _is_strategy_framed catches most of these too, but a
        # construction ask is strategy-class by definition — pin it here.
        return "strategy"
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
    # Ranked-list reads (movers / gainers-losers / most-active) need a table,
    # not the ≤120-word prose cap — check BEFORE the analytical_short fallback.
    if _LIST_READ_RE.search(msg):
        return "list_read"
    if _EXPLAINER_INTENT_RE.search(msg):
        return "explainer"
    return "analytical_short"


# Per-reply-class budget: (max_output_tokens, system hint).
# 2026-06-18 BUDGET RAISE: previous caps were strangling rationale-heavy
# replies (a "risk neutral oil agent" build that needed to disclose
# producers-vs-refiners + hedge-honesty got truncated mid-sentence under
# the old 1500-token draft cap). New caps below; small_talk + capability
# stay tight so quick replies remain snappy. The companion change in
# openai_client.py raises `complete()` / `stream_openai()` default
# max_output_tokens from 1500 → 4000 so any caller that doesn't pass an
# explicit budget also gets the headroom.
_REPLY_BUDGETS: dict[str, tuple[int, str]] = {
    "model_owned": (3800, (
        "REPLY SIZING — you decide. Size and structure the reply to the "
        "ask itself: a quick fact gets 1-3 direct sentences; a comparison "
        "or analysis gets 250-450 words with ## sections and markdown "
        "tables for any multi-name numbers; a concept explainer up to 500 "
        "words with headers/bullets; when a CARD renders below your text, "
        "one plain-English summary sentence and let the card speak. Never "
        "pad a simple answer, never compress a data-rich answer into a "
        "blurb, and never restate a card's full field list in prose."
    )),
    "draft": (3500, (
        "REPLY-CLASS: DRAFT. A workflow/agent CARD is being rendered "
        "below your text — DO NOT restate the full trigger/action list. "
        "Lead with ONE plain-English sentence summarising the trigger "
        "and action, then let the card speak. If you must add notes, "
        "use short labelled lines (e.g. `Sizing: ...`, `Risk: ...`) — "
        "never a wall of prose."
    )),
    "automation": (3500, (
        "REPLY-CLASS: AUTOMATION. A registered order / SIP / GTT CARD "
        "is being rendered — keep prose tight. ONE plain-English "
        "sentence on what got registered + the register-not-execute "
        "note. Use short labelled lines for any caveats; never a wall "
        "of text."
    )),
    "backtest": (3000, (
        "REPLY-CLASS: BACKTEST. A backtest CARD with equity curve + "
        "metrics is being rendered. ONE summary sentence (window + "
        "headline result) and then let the card speak. If you call out "
        "a metric in prose, prefer short labelled lines over paragraphs."
    )),
    "explainer": (4000, (
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
    "analysis": (4000, (
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
    "analytical_short": (3000, (
        "REPLY-CLASS: SHORT-ANALYTICAL. Reply in ≤120 words of plain "
        "prose. No `##` headings. Do NOT append unsolicited live "
        "prices — recite them only if the user asked."
    )),
    # LIST — a ranked-list read (top movers, gainers/losers, most active). The
    # tool returned a ranked list; render it as a compact markdown TABLE, not
    # terse prose. This class exists so the two-table layout isn't crushed into
    # the ≤120-word analytical_short budget (which truncated the table mid-row).
    "list_read": (3500, (
        "REPLY-CLASS: LIST. The tool returned a ranked list. Render it as a "
        "compact markdown TABLE — columns rank · symbol · LTP · change%. If the "
        "user asked for both gainers AND losers, give ONE table per group. "
        "Include EVERY row the tool returned and FINISH every row — never stop "
        "a table mid-line. Add at most one short sentence of read (what's "
        "leading). Do NOT append prices for unrelated names."
    )),
    # STRATEGY — the text that accompanies a build_strategy /
    # propose_basket_allocation card. The previous 3800-token cap still
    # truncated a hedge-honest "this isn't risk neutral, here's the
    # nearest real F&O hedge" reply mid-table; raised to 6000 so the
    # model can ship the full rationale + alternatives + sizing table
    # + the honesty disclosure without being cut off.
    "strategy": (6000, (
        "REPLY-CLASS: STRATEGY. This text accompanies a strategy/basket "
        "CARD the user can see — do NOT restate every leg mechanically. "
        "Aim for 250-450 words, well-structured with `## Section` "
        "headings. CONNECT the build to the user's ask: (1) why this "
        "structure fits their stated/assumed view, risk, horizon and "
        "capital; (2) the rationale for the constituents and the "
        "weighting (explain the LOGIC in plain English — 'tilted toward "
        "lower-volatility names so a drawdown hurts less' — do NOT name "
        "internal scheme/gate enums like risk-parity, min-variance, "
        "black-litterman, f-score, magic-formula); (3) 1-2 real "
        "ALTERNATIVES with the trade-off (e.g. 'fewer names = more "
        "conviction but more single-stock risk'); (4) a markdown table "
        "of the holdings with weights when 3+ names. Flag any "
        "(assumed …) slot honestly. End with the register-not-execute "
        "note and the not-advice disclaimer."
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

# Fresh top-level build/create intents — checked BEFORE `_DEPENDENT_INTENT_RE`
# in `_is_independent_prompt`, not folded into `_INDEPENDENT_INTENT_RE` below
# (which is only consulted AFTER the dependent-verb branch, so it never gets
# a say when a bare amendment verb like "make it" also appears later in the
# same message — see the call site for the live repro this fixes).
_FRESH_BUILD_INTENT_RE = re.compile(
    r"\b(?:also\s+build|another\s+(?:agent|workflow|automation)|"
    r"now\s+also\s+(?:build|set|make|create)|"
    r"build\s+(?:me\s+)?another|"
    r"new\s+(?:agent|workflow|automation)|"
    r"different\s+(?:agent|workflow|automation))\b"
    # Fresh agent-build / workflow-build / strategy-build top-level
    # intents. WHY: when the user types "make me an agent that buys X at
    # open and sells at close…" while a stale draft for a DIFFERENT
    # symbol is sitting in active_draft from a prior turn, the amendment
    # path was being taken — so the model re-emitted the old draft
    # instead of building the new one. These phrases are unambiguously
    # fresh top-level intents; they should always evict the prior draft.
    r"|\b(?:build|make|create|set\s*up|design|spin\s+up)\s+"
    r"(?:me\s+)?(?:an?|some)\s+(?:agent|workflow|automation|strategy|rule|bot|sip)\b"
    r"|\bmake\s+(?:an?|some)\s+(?:agent|workflow|automation)\s+that\b"
    r"|\b(?:agent|workflow|automation)\s+that\s+(?:buys?|sells?|alerts?|notifies)\b",
    re.IGNORECASE,
)

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
    # Analysis verbs are a fresh READ intent even while a draft is active
    # (live repro 2026-07-10: "Analyze reliance." right after an agent
    # draft re-emitted the draft instead of running the analysis flow).
    r"|\banaly[sz]e\b|\bdeep\s+dive\b|\banalysis\s+(?:of|on)\b"
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
    # Backtest top-level. WHY the second branch (GAN R4 F3): the user
    # often says "backtest that / this / it / the strategy" referring to
    # the active draft — the engine must RUN, not re-draft. The original
    # regex only matched "(run|do|start) backtest", so "backtest that"
    # fell through to the amendment path (propose_dsl_workflow re-draft,
    # zero engine calls). This verb-first branch evicts the draft and
    # forces the backtest surface so the simulation actually runs.
    r"|\b(?:run|do|start)\s+(?:a\s+)?backtest\b"
    r"|\bbacktest(?:\s+(?:that|this|it|the\s+(?:strategy|draft|rule|idea)))?\b"
    r"|\bsimulate\s+(?:that|this|it|the\s+(?:strategy|draft|rule))\b"
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
    r"|^\s*(?:and\s+now|next)\??\s*$",
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
    r"|lower|raise|increase|decrease|reduce|bump|shift|widen|narrow"
    # Basket RE-WEIGHT amendments (fix: "rebuild it heavier in X",
    # "re-weight", "reallocate", "tilt to the leaders", "overweight KSB"
    # were treated as fresh builds → the model reproduced the same weights
    # and only reframed the prose. These are amendments of the active basket.
    r"|rebuild|re-?weight|reweight|re-?allocate|reallocate|re-?balance|rebalance"
    r"|tilt|overweight|underweight|weight\s+(?:it|more|less)"
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

# Structural anchor to the draft — a pronoun referencing it, a named
# slot/field, or a concrete NEW value (number / % / currency). Used to
# resolve the one case _DEPENDENT_INTENT_RE is genuinely ambiguous about:
# a QUESTION-SHAPED message that merely contains one of its bare verbs
# (reduce/add/use/try/change/increase/…) with nothing tying it to the
# active draft. "reduce SL to 3%" and "can you make it 5 lots instead"
# are anchored (a slot-noun / pronoun + a number) and stay amendments;
# "how can I reduce risk in general investing" is NOT anchored — it's a
# free-standing question that happens to contain "reduce" — and must NOT
# be treated as an edit to whatever draft happens to be on screen.
_AMENDMENT_ANCHOR_RE = re.compile(
    r"\b(?:it|that|this|the\s+draft)\b"
    r"|\d"
    r"|₹|%|\bpercent\b"
    r"|\b(?:trigger|action|condition|step|sl|stop[- ]?loss|quantity|qty|"
    r"symbol|schedule|notification|email|lot|lots|leg|legs|strike|strikes|"
    r"expiry|premium|weight|allocation|basket)\b",
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


def _is_genuine_dependent_amendment(message: str) -> bool:
    """True when a `_DEPENDENT_INTENT_RE` match is a real signal that the
    message amends the active draft, not a free-standing question that
    merely contains one of the amendment-verb regex's common English words
    (see `_AMENDMENT_ANCHOR_RE`'s docstring for the canonical case this
    excludes: "how can I reduce risk in general investing").

    Every call site that gates tool-selection or `tool_choice="required"`
    behavior on an active draft + `_DEPENDENT_INTENT_RE` MUST call this
    helper, not the raw regex. The raw pattern was independently
    duplicated at several call sites (F&O amendment-scope tool filtering,
    forced tool_choice for workflow-macro amendments, the meta-question
    escape check — each present in both the streaming and non-streaming
    code paths); when the unanchored-question false positive was first
    found and fixed, it was fixed at only one of those call sites
    (`_is_independent_prompt`), so the same stale-draft-refires-on-an-
    unrelated-question bug still reproduced at every other site — the
    literal root cause was "the same classification logic copy-pasted N
    times, only one copy patched." Routing every site through this single
    function is the actual fix for that class of bug, not another
    one-off patch.
    """
    msg = (message or "").strip()
    if not _DEPENDENT_INTENT_RE.search(msg):
        return False
    return not (_is_question_shaped(msg) and not _AMENDMENT_ANCHOR_RE.search(msg))


# Reported live 2026-07-14: "compare me both the baskets we built and tell
# me on the basis of latest news whihc one is better? modify of needed to"
# tripped `_is_genuine_dependent_amendment` (via the bare verb "modify")
# and force-re-emitted the SAME backtest tool with an identical card,
# ignoring the comparison ask entirely. No narrowing of the question-
# detector regex closes this class of bug — a compound "compare X, modify
# if needed" message will always share vocabulary with a genuine amendment,
# so a message can be BOTH _is_genuine_dependent_amendment()==True and
# clearly asking for something the active draft's tool can't give it.
# Rather than trying to perfectly classify the whole message (whack-a-mole
# against every future phrasing), detect the COMPETING analysis/comparison
# signal and use it to relax the hard "re-emit this exact tool, do NOT
# switch" lock at its call sites — a wrong call here just leaves the model
# free to choose (today's already-safe default), never a guaranteed
# wrong-widget refire.
_COMPETING_ANALYSIS_RE = re.compile(
    r"\bcompar(?:e|ing|ison)\b|\bcontrast(?:ing)?\b|\brank(?:ed|ing)?\b|"
    r"\bversus\b|\bvs\.?\b|\bdifference\s+between\b|"
    r"\bwhich\s+(?:one|is)\b[^.!?]{0,25}\bbetter\b",
    re.IGNORECASE,
)


def _requests_comparison_over_amendment(message: str) -> bool:
    """True when the message asks to compare/rank/contrast existing
    results rather than mutate the single active draft in place, even if
    it also contains an amendment-verb word as a secondary clause. Call
    sites that force `tool_choice="required"` + "do NOT switch tools" on
    `_is_genuine_dependent_amendment()` should also require this to be
    False, so a compound analysis-plus-maybe-amendment message doesn't
    get hard-locked into re-emitting a tool that can't do the analysis."""
    return bool(_COMPETING_ANALYSIS_RE.search((message or "").strip()))


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
    # An unambiguous top-level build/create phrasing trumps any
    # amendment-shape match found ANYWHERE ELSE in the same message.
    # Two live repros forced this to run BEFORE `_DEPENDENT_INTENT_RE`:
    #   - "Now also build a sell agent for TCS at 4200" was caught by the
    #     stepwise "at <number>" amendment pattern.
    #   - "build me a strategy that has high correlation with gold... make
    #     it aggressive and concentrated" (2026-07-15): the trailing "make
    #     it <adjective>" tripped `\bmake\s+it\b` in `_DEPENDENT_INTENT_RE`,
    #     which returned early (msg isn't question-shaped, so
    #     `_is_genuine_dependent_amendment` said "genuine") and the turn
    #     was classified DEPENDENT — the active draft (a prior, unrelated
    #     top-gainer-rotation workflow) was kept and "amended" instead of
    #     building the new strategy fresh, so the resulting card was a
    #     reskin of the old draft's schedule/steps. `_INDEPENDENT_INTENT_RE`
    #     already had a matching "build me a strategy" branch, but it was
    #     only ever reached AFTER `_DEPENDENT_INTENT_RE`'s unconditional
    #     early return, so it never got a chance to win. Fresh top-level
    #     build intents belong in this same priority tier, not after it.
    if _FRESH_BUILD_INTENT_RE.search(msg):
        return True
    # Explicit amend wins (after the multi-build override) — UNLESS the
    # match is only the bare-verb branch of _DEPENDENT_INTENT_RE (reduce/
    # add/use/try/change/increase/…) inside a free-standing QUESTION with
    # no anchor tying it to the draft on screen. Those verbs are common
    # English words ("how can I reduce risk in general investing") that
    # otherwise unconditionally beat the independent check below — the
    # root cause of a stale draft's tool re-firing on a topic switch. A
    # real amendment either (a) isn't phrased as a question ("reduce SL
    # to 3%", "tilt to the leaders") or (b) is a question but still
    # anchored ("can you make it 5 lots instead" — has "it" + a number).
    # Only the unanchored-question case gets reclassified as independent
    # here; every non-question or anchored match is unaffected.
    if _DEPENDENT_INTENT_RE.search(msg):
        return not _is_genuine_dependent_amendment(msg)
    if _INDEPENDENT_INTENT_RE.search(msg):
        return True
    # Bare ticker (e.g. "RELIANCE", "ETERNAL", "Reliance") is a fresh
    # data-lookup intent — drop the draft. Length-bounded so it doesn't
    # match short workflow descriptions.
    if re.fullmatch(r"\$?[A-Za-z][A-Za-z0-9\-_]{1,15}\??", msg):
        return True
    return False


# ── Meta-turn classification (2026-07-10 follow-up rework) ─────────────
# The draft/clarify funnels used to be capture-by-default: any turn that
# didn't match the narrow "independent prompt" allowlist was forced into
# "AMENDMENT TURN — re-emit IMMEDIATELY", and any turn during a clarify
# flow was folded as an answer. Live repro of the user-reported failures:
#   * "What time interval are you taking to calculate the RSI?"  → the
#     same draft was re-emitted instead of an answer.
#   * "You didn't ask me the number of shares. You just created the
#     agent." → the clarify state machine consumed it as an answer and
#     emitted its next scripted question (0 LLM hops).
# These regexes give questions and meta-feedback their own lane: keep the
# draft/clarify state, hand the turn to the LLM with the draft as CONTEXT
# (not as a mandate), tool_choice=auto.

_META_FEEDBACK_RE = re.compile(
    # "you didn't / never / should have (asked) …", "without asking me"
    r"\byou\s+(?:didn'?t|did\s+not|never|should\s+(?:have|not)|shouldn'?t"
    r"|just\s+(?:created|built|made|placed|went))\b"
    r"|\bwithout\s+asking(?:\s+me)?\b"
    r"|\bwhy\s+did(?:n'?t)?\s+you\b"
    r"|\bi\s+(?:didn'?t|did\s+not|never)\s+(?:say|ask|tell|approve|confirm"
    r"|want)\b",
    re.IGNORECASE,
)

_META_QUESTION_RE = re.compile(
    # Trailing "?" is NOT required — real chat input routinely drops it
    # ("will this place a live order automatically, or just register
    # something I confirm"). Requiring it meant a genuine question about
    # an active draft fell through this classifier entirely (followup_
    # turn_kind → None), skipping the register-not-execute engine-fact
    # grounding in `_meta_turn_hint` and letting the model answer from an
    # unguided guess — root cause of a live false claim that an
    # automation "will place a live order automatically" (it registers
    # for confirmation). Bounded on `.!` instead of requiring `?`, so a
    # genuine multi-sentence message still doesn't match past its first
    # sentence terminator.
    # Contracted negations ("isn't IGL a gas company, not pharma?") are as
    # much a leading-question shape as their uncontracted form, but were
    # missing from the alternation — a live eval found the SAME question
    # asked plainly ("is IGL a pharma company") got answered directly,
    # while the contracted phrasing fell through this classifier and got
    # a non-response re-running the prior tool instead (reported
    # 2026-07-14).
    r"^\s*(?:what|which|how|why|when|where|who|whose|does|do|is|are|am"
    r"|can|could|will|would|should"
    r"|isn'?t|aren'?t|wasn'?t|weren'?t|doesn'?t|don'?t|didn'?t"
    r"|can'?t|won'?t|wouldn'?t|shouldn'?t|couldn'?t)\b[^.!]{0,180}\??\s*$",
    re.IGNORECASE,
)


def _followup_turn_kind(message: str) -> Optional[str]:
    """'meta_feedback' | 'question' | None.

    Fires ONLY for turns that must escape the amendment/clarify capture:
    a complaint about how the assistant acted, or an interrogative that
    carries no amendment verb ("can you make it 20 shares?" still matches
    _DEPENDENT_INTENT_RE and stays an amendment)."""
    msg = (message or "").strip()
    if not msg:
        return None
    if _META_FEEDBACK_RE.search(msg):
        return "meta_feedback"
    if _META_QUESTION_RE.match(msg) and not _is_genuine_dependent_amendment(msg):
        return "question"
    return None


def _safe_draft_json(draft: object, budget: int = 1800) -> str:
    """Valid-JSON dump of a draft under `budget` chars.

    The old ``json.dumps(draft)[:1800]`` cut MID-JSON; the model re-parsed
    the fragment and regenerated corrupted params (live repro: a MACD
    signal drifting 12,2 → 12,1 across re-emits). Drop bulky non-identity
    fields, then the largest remaining values, until the dump fits.

    IDENTITY-CRITICAL keys are NEVER dropped: ``steps`` is the workflow's
    actual conditions/actions — trimming it on an amendment ("change qty to
    20") hands the model a draft with the conditions stripped, so it rebuilds
    them from chat history and can silently drop an AND-leg or a stop-loss
    (the amendment-drops-conditions regression). Better to slightly exceed the
    char budget than to amputate the workflow's structure."""
    _PROTECTED = ("steps", "type", "id", "kind")
    try:
        if not isinstance(draft, dict):
            s = json.dumps(draft, default=str)
            return s if len(s) <= budget else "{}"
        d = {k: v for k, v in draft.items()
             if k not in ("payoff", "chart", "chart_data", "history",
                          "preview", "equity_curve")}
        while True:
            s = json.dumps(d, default=str)
            if len(s) <= budget or not d:
                return s
            # Only pop droppable keys; never the identity-critical ones.
            droppable = [k for k in d if k not in _PROTECTED]
            if not droppable:
                return s  # only protected keys remain — keep them whole
            biggest = max(
                droppable, key=lambda k: len(json.dumps(d[k], default=str))
            )
            d.pop(biggest)
    except Exception:  # noqa: BLE001 — a hint must never break the turn
        return "{}"


_ENGINE_FACTS = (
    "Engine facts you may state plainly: indicators (RSI / SMA / EMA / "
    "MACD) are computed on DAILY bars — RSI(14) means 14 trading DAYS — "
    "unless the draft names another interval; price/indicator triggers "
    "are evaluated about once a minute during market hours (09:15–15:30 "
    "IST); scheduled triggers fire on their cron schedule; event triggers "
    "are checked every few minutes. Orders are REGISTERED for the user's "
    "confirmation (paper mode fills a simulated book) — nothing executes "
    "against a live broker account on its own.\n\n"
    "Never state an execution price, limit-price offset, slippage "
    "estimate, or fill-probability detail for THIS draft unless that "
    "literal field appears in DRAFT JSON above — different tools have "
    "different fields (e.g. create_gtt_order carries a limit_price, "
    "propose_holding_action/propose_workflow SL steps do not), and "
    "borrowing a mechanic from a DIFFERENT tool's schema onto this draft "
    "is fabrication (reported 2026-07-14: invented \"execution price "
    "slightly below ₹X to improve fill probability\" on a draft with no "
    "such field)."
)


def _meta_turn_hint(kind: str, active, message: str) -> LLMMessage:
    """System hint for a question / meta-feedback turn while a draft or
    clarify flow is on screen: answer conversationally, keep the draft."""
    if active is not None:
        ctx = (
            f"A `{active.tool_name}` draft is on screen — it STAYS on "
            "screen; do not touch it unless the user's message fully "
            f"specifies a change. DRAFT JSON: "
            f"{_safe_draft_json(active.draft)}. "
        )
    else:
        ctx = (
            "You are mid-questionnaire (your last turn asked the user a "
            "question). "
        )
    if kind == "meta_feedback":
        body = (
            "The user is giving META-FEEDBACK about how you handled the "
            "last step (e.g. you built something without collecting a "
            "parameter). Do NOT re-emit the unchanged draft. Do NOT treat "
            "their message as a questionnaire answer. Respond in plain "
            "prose: briefly acknowledge (one clause, no over-apologising), "
            "state what the draft currently assumes for the parameter they "
            "raised, and ask ONE short question for the missing value. If "
            "their message itself fully specifies the correction, apply it "
            "by re-emitting the tool with that one field changed."
        )
    else:
        body = (
            "The user is asking a QUESTION — about the draft's parameters, "
            "what it will do, or how the system works. ANSWER it directly "
            "in plain prose, grounded in the draft JSON and the engine "
            "facts below. Do NOT re-emit the draft. Do NOT call a build "
            "tool. Do NOT echo their question back at them. Do NOT answer "
            "with a card. After answering, you may add one short line "
            "inviting them to tweak or activate the draft. "
            + _ENGINE_FACTS
        )
    return LLMMessage(
        role="system",
        content=f"META TURN — NOT an amendment, NOT a clarify answer. {ctx}{body}",
    )


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
    # 51-sweep: sector-OUTLOOK asks were answered with a bare ROE table
    # and no view — the one thing an outlook ask is FOR.
    if re.search(r"\b(?:outlook|prospects?|view)\b[^.?!]{0,60}\bsector\b"
                 r"|\bsector\b[^.?!]{0,40}\b(?:outlook|prospects?|view)\b",
                 message, re.IGNORECASE):
        return (
            " THIS IS a SECTOR OUTLOOK ask — a data table alone is an "
            "INVALID answer. You MUST end with a `## View` section: your "
            "defended 6-month stance for the sector (constructive / "
            "neutral / cautious) with the 2-3 numbers that justify it and "
            "what would change your mind, then the not-advice line."
        )
    # 2026-07-10: "compare X with Y" was landing in analytical_short and
    # producing a 4-row metric table with a two-line takeaway — far below
    # the analysis bar. A comparison is a FULL analysis of two names.
    if re.search(r"\bcompare\b|\bvs\.?\b|\bversus\b|\bcomparison\b",
                 message, re.IGNORECASE):
        return (
            " THIS IS a HEAD-TO-HEAD COMPARISON — a single metric table is "
            "an INVALID answer. Structure it as: `## Head-to-head` "
            "(performance table: return windows, volatility, Sharpe, max "
            "drawdown, with a Winner column), `## Fundamentals` (P/E, ROE, "
            "ROCE, D/E, dividend yield side-by-side — call fetch_fundamentals "
            "for BOTH names if not already in context), `## What separates "
            "them` (2-3 sentences on business/valuation drivers behind the "
            "numbers), and `## Verdict` — ONE defended pick for a stated "
            "investor profile with what would flip it, then the not-advice "
            "line."
        )
    return ""


_QUESTION_SHAPED_RE = re.compile(
    r"^\s*(what|which|whats|what's|how|is|are|do|does|can|could|would|should|why|"
    r"who|when should|help me|any idea|thoughts|thinking about|considering)\b"
    r"|\bshould i\b|\bworth (building|doing|it)\b|\bmake sense\b|\bhelp me decide\b"
    r"|\bnot sure (if|whether|which|what)\b|\bwhat'?s a good\b|\bany suggestions?\b",
    re.IGNORECASE,
)


def _is_question_shaped(message: str) -> bool:
    """True when an agent-flavoured message is really a QUESTION or a
    deliberation ('what options should I trade?', 'should I build a dip-buy?',
    'is 30 a good RSI threshold?') rather than a command to build.

    Used to relax hop-1 tool_choice from 'required' to 'auto' so the model
    can answer in prose or ask the one blocking question (e.g. the options
    view) instead of being forced to emit a card. Commands ('buy 10 INFY
    when RSI<30', 'alert me when…', 'build an agent that…') are NOT
    question-shaped and keep the commit-surface forcing. Conservative on
    purpose: a leading 'when'/'if' (a condition, not a question) does not
    match, and any specific required-override below still wins."""
    if not message:
        return False
    m = message.strip()
    # An explicit build/order command anywhere near the front dominates —
    # never treat a real command as a question even if it also asks something.
    if re.match(r"^\s*(buy|sell|short|exit|square|place|set up an? (alert|order|sip)|"
                r"build|create|make|set up|arm|schedule|alert|ping|notify|remind)\b",
                m, re.IGNORECASE):
        return False
    return bool(_QUESTION_SHAPED_RE.search(m))


def _history_tail_text(history: list, limit: int = 800) -> str:
    """Best-effort string of the last few turns, for intent-pack selection
    so a follow-up ("make it 2 lots") still pulls the right module. Tolerates
    dict- or object-shaped history entries; never raises."""
    parts: list[str] = []
    try:
        for m in (history or [])[-4:]:
            c = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
            if isinstance(c, str) and c:
                parts.append(c)
    except Exception:
        return ""
    return " ".join(parts)[-limit:]


def _prompt_module_block(message: str, history: list) -> str:
    """The per-turn intent-pack system-message content (empty when none
    applies). system_core.md is always loaded; these packs are additive."""
    names = select_prompt_modules(message, _history_tail_text(history))
    # When the hosted web_search tool is offered on THIS turn (scoped to
    # news / qualitative-company / earnings-date asks), load its usage
    # contract so the model knows WHEN to reach for it and — critically —
    # that prices/fundamentals still come from Kite tools, not the web.
    if _HOSTED_TOOLS and _web_search_scope(message):
        names = [*names, "web_search"]
    return load_prompt_modules(names) if names else ""


# A NEWS / "latest developments / what will move" ask. When this fires AND
# web browsing is enabled, we hard-direct the model to actually call the
# hosted web_search tool instead of reasoning from memory or hedging that it
# "has no live feed" (the failure the user flagged: a market-overview turn
# pulled index/movers, then gave a generic "what usually moves the open"
# answer without ever browsing). Company-profile asks ("what does X do",
# "who is the CEO") are deliberately NOT matched — those are stable-knowledge,
# not news.
_NEWS_BROWSE_RE = re.compile(
    r"\bnews\b|\bheadlines?\b"
    r"|\blatest\b[^.?!]{0,30}\b(?:on|around|about|for|in)\b"
    r"|\bwhat(?:'?s|\s+is|\s+are)\s+(?:happening|going\s+on)\b"
    r"|\bwhat\s+(?:will|could|would|might)\s+(?:move|impact|drive|affect)\b"
    r"|\bimpact\s+(?:the\s+)?(?:price|open|market|nifty|sensex)\b"
    r"|\bwhy\s+(?:is|did|are|has|have)\s+[\w.&'-]+\s+"
    r"(?:up|down|fall|fell|fall(?:en|ing)|drop|dropp\w*|ris\w*|jump\w*|"
    r"surg\w*|crash\w*|tank\w*|rally\w*|mov\w*|gain\w*|los\w*|slid\w*)\b",
    re.IGNORECASE,
)


def _is_news_browse_ask(message: str) -> bool:
    return bool(_NEWS_BROWSE_RE.search(message or ""))


# ── Web-search SCOPE (2026-07-19) ────────────────────────────────────
# The hosted web_search tool is attached per-turn ONLY for the three ask
# shapes it exists for: news, qualitative company context (operations,
# management, plans, deals …), and earnings/results dates. Everything
# else (prices, technicals, fundamentals, screens, orders, backtests)
# has a local tool and must never burn a browse hop. This is tool-SURFACE
# narrowing — the same lane the tool_router already uses — not an
# interpretation layer: the model still owns what to do with the turn;
# out-of-scope turns simply don't carry the (slow) browse tool.
_WEB_QUALITATIVE_RE = re.compile(
    r"\b(?:management|promoters?|ceo|cfo|founder|chairman|board\b"
    r"|operations?|business\s+model|segments?|subsidiar|products?\s+and\b"
    r"|what\s+does\s+[\w.&'-]+\s+do\b|about\s+the\s+company"
    r"|expansion|capex\s+plans?|acquisitions?|merger|demerger|deal\b"
    r"|order\s+(?:win|book)|contract\s+(?:win|award)|partnership"
    r"|guidance|outlook|commentary|concall|conference\s+call"
    r"|analyst\s+(?:day|meet)|credit\s+rating|downgrade|upgrade\b"
    r"|litigation|investigation|probe\b|resign|appoint)",
    re.IGNORECASE,
)
_WEB_EARNINGS_DATE_RE = re.compile(
    r"\b(?:earnings?|results?|q[1-4]\s*(?:fy)?\d*)\b"
    r"[^.?!]{0,60}\b(?:date|when|calendar|schedule|announc|declar|report)"
    r"|\b(?:when|what\s+date)\b[^.?!]{0,60}\b(?:earnings?|results?)\b"
    r"|\bboard\s+meeting\b|\brecord\s+date\b|\bex[- ]date\b|\bagm\b"
    r"|\bdividend\s+(?:date|announc)",
    re.IGNORECASE,
)


def _web_search_scope(message: str) -> bool:
    """True when this turn's ask is in the web-search lane (news /
    qualitative company context / earnings-results dates)."""
    msg = message or ""
    return bool(
        _NEWS_BROWSE_RE.search(msg)
        or _WEB_QUALITATIVE_RE.search(msg)
        or _WEB_EARNINGS_DATE_RE.search(msg)
    )


def _hosted_tools_for(message: str) -> "list[dict] | None":
    """The per-turn hosted-tool surface: the browse tool only in scope."""
    if _HOSTED_TOOLS is None:
        return None
    return _HOSTED_TOOLS if _web_search_scope(message) else None


# ── LLM-owned interpretation (experiment): one static direction block ──
# Replaces the regex-triggered steering guards + intent surgery when
# `llm_owned_interpretation` is on. Byte-stable so it caches; describes
# HOW to interpret and construct, never forces a tool.
_LLM_OWNED_DIRECTIONS = """## Interpreting the ask — you own this decision
Decide from the message itself what the user wants and pick the tool that
matches. The shapes to distinguish:
- QUESTION / data read → answer it (call read tools); never block a read
  on a clarifying question.
- IMMEDIATE ORDER ("buy 10 INFY") → the order tool, one-time. Never
  upgrade a one-time buy into a recurring workflow.
- RECURRING / CONDITIONAL ("every Friday…", "when RSI<30…") → the
  workflow/automation tools. Never silently drop a stated condition.
- STRATEGY / BASKET / PORTFOLIO build with no trigger language →
  build_strategy (a construction, NOT a workflow draft).
- BACKTEST → backtest_workflow; a verb-less tweak right after a backtest
  ("now try RSI<25") re-runs it with that one change.
- CONTRADICTION: an ask to buy AND sell the same instrument at the same
  time is contradictory — ask which action was meant; never draft both.
- "crosses N" with no direction means crosses ABOVE (from below). Build that
  and note the assumption; don't spend the turn asking which way.
- A staged exit ("sell a third at +5%, a third at +10%, all out at −3%") must
  keep the STOP ARMED AT EVERY STAGE: express each stage as a compound exit
  (its take-profit OR the stop), never a linear chain where the stop sits
  behind the profit legs — in a chain the stop cannot fire until the targets
  do, which silently disarms it on the exact path it exists for.
- A card caption summarises EVERY leg (entry and each exit), not just the
  entry — a caption that describes only the buy hides the mechanics.

## Clarify discipline
Call ASK_USER (structured, tappable) only when a REQUIRED field is
genuinely missing and no sensible default exists. Named option template +
underlying (straddle/condor/spread on NIFTY…) is buildable NOW — the
engine fills strikes/width/qty defaults; vague modifiers are not missing
fields. Indicator rules (RSI/SMA/…) default to DAILY bars — never ask
daily-vs-intraday; build daily unless the user names an interval. If the user is confused by a menu you offered, teach one option
in plain prose and end with one yes/no — never re-dump the menu.

## Quoting numbers you were handed
- If the user NAMES a metric the tool didn't return (XIRR, alpha, Sortino,
  beta), open by saying it isn't computed and give the nearest one that IS,
  naming how they differ. Silently answering a different question is the
  failure — they asked for a number, not a table.
- Percentages carry a BASIS (`metric_legend` spells it out). Use ONE basis
  across every leg of a comparison, and never re-sign a value that is already
  negative (drawdown).
- Sanity-check before you write: if a monthly SIP ran three years, the buy
  count should look like ~36 (`n_buys`, `total_contributed_inr` are there) —
  when the payload contradicts itself, say so rather than narrating it.
- F&O sizing anchored on "my N shares": FIRST line reconciles lot maths —
  "lot = L, this writes K lots = K×L shares vs your N: over/under by X" — then
  the greeks. A covered call written over fewer shares than the lot covers is
  NAKED on the difference; never label it covered without that math.

## Construction honesty
- A hedge must OFFSET exposure (canonical: protective put via
  build_option_strategy) — never buy more of the hedged name. One card
  per turn; offer the second name as a follow-up.
- "At the open / at the close" = trigger.market_relative_time
  (anchor='open'|'close'). NEVER approximate a price, percent, or
  open/close condition with a time-of-day cron — a 09:30 daily check is
  not the same thing and is a correctness failure.
- THEMATIC asks ("profits from a weak rupee / monsoon / crude spike"):
  reason out who ACTUALLY benefits — real NSE tickers, not sector
  clichés (weak rupee → IT/pharma exporters, NOT importers; rising crude
  → upstream ONGC/OIL, NOT refiners IOC/BPCL/HPCL). Reply with a short
  thesis, a winners/losers markdown table with one-line WHYs, what would
  confirm or invalidate the view, then offer a basket card
  (propose_basket_allocation) sized to the user's capital if stated.
  Never a generic staples basket, never a bare clarify punt.
- For a strategy-framed draft, explain WHAT it does and WHY it fits
  (with the real fetched numbers) before the card readback."""


def _build_deterministic_guards(message: str, history: list) -> list[str]:
    """GAN R2 R2–R6: deterministic directive blocks that suppress the
    over-eager ASK_USER escape hatch / 09:30 downgrade and force the
    documented canonical behaviour. Prose in system.md alone proved
    insufficient — these fire as additional hard system messages and the
    caller pairs them with scope-narrowing / tool_choice in the routing
    layer. Returns a list of directive strings (possibly empty).

    Under `llm_owned_interpretation`, the STEERING guards (named-option,
    hedge choreography, at-open-close, confusion-teach, strategy-framed,
    thematic template, vague-onboarding) are replaced by the single
    static _LLM_OWNED_DIRECTIONS block; the BOUNDARY/HONESTY guards
    (news grounding, unsupported rails, alert boundary, unrealistic
    return, scared idle cash) fire in both arms."""
    guards: list[str] = []
    _llm_owned = _settings.llm_owned_interpretation

    # NEWS ask → hard-direct the model to BROWSE (only when web search is on).
    # Without this, a "latest news around NIFTY" ask gets anchored on the
    # market-overview tools (index/movers) and the model answers from memory
    # with a "I don't have a live news feed" hedge — the exact failure the
    # user flagged. The hosted web_search tool IS available; force its use.
    if _HOSTED_TOOLS and _is_news_browse_ask(message):
        guards.append(
            "## NEWS ASK — you MUST browse the web before answering\n"
            "The user is asking for NEWS / the latest developments / what "
            "will move a stock or the market. You HAVE a live web search "
            "tool (hosted `web_search`). You MUST call it to fetch the REAL "
            "current headlines BEFORE you answer. Do NOT answer from training "
            "memory, and NEVER say you 'don't have a live news feed' or tell "
            "the user to 'go check the news' — you check it. Steps: (1) call "
            "`web_search` for the actual current headlines on the subject "
            "(the named stock, NIFTY/SENSEX, or the macro event) from "
            "credible Indian-market sources (Economic Times, Moneycontrol, "
            "Mint, Business Standard, Reuters); (2) you MAY also pull "
            "`get_index_level` / `get_top_movers` for the live tape; (3) "
            "synthesize the FETCHED headlines (each with its source/outlet) "
            "PLUS the tape into a specific, useful answer, and cite the "
            "sources you browsed. NEVER invent a headline, source, number, "
            "or URL — quote ONLY what `web_search` actually returned. If the "
            "search genuinely returns nothing usable, say so plainly rather "
            "than falling back to generic 'what usually moves markets' prose.\n"
            "STRUCTURE (required): open with a one-line take, then a "
            "**bulleted list** of the headlines — one bullet each, the driver "
            "in **bold** followed by its source link — and close with a "
            "one-line 'what to watch'. Never answer as one unbroken block of "
            "prose; every news answer must have a lead line + bullets."
        )

    # R6 — confusion AFTER an ASK_USER menu → TEACH, never re-dump.
    if not _llm_owned and _is_confusion_after_menu(message, history):
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
        elif rail == "auto_execute":
            guards.append(
                "## Unsupported rail: broker AUTO-EXECUTE / fire-and-forget\n"
                "The user asked Pivot to place/execute orders AUTOMATICALLY "
                "in their broker (Zerodha/Kite/Dhan/etc.) without "
                "confirmation. Pivot is REGISTER-NOT-EXECUTE under the SEBI "
                "Feb 2025 retail-algo framework — it NEVER auto-executes in a "
                "broker. This is capability theatre if affirmed. Do NOT draft "
                "an order with requires_approval=false; do NOT imply it will "
                "fire by itself. Your reply MUST, IN THIS ORDER: (1) state the "
                "boundary plainly ('Pivot can't auto-execute in your broker — "
                "it registers the order and you tap-to-confirm in the broker "
                "app; that's the SEBI register-not-execute posture'); (2) "
                "offer the nearest real thing — register the trigger/order so "
                "it's one tap away when the condition fires; (3) only then "
                "ask any concrete field. Never narrate auto-execution."
            )
        elif rail == "upi_roundup":
            guards.append(
                "## Unsupported rail: UPI round-ups / spare change / %-of-spend\n"
                "Pivot CANNOT see UPI transactions, bank balances, or spend — "
                "true round-ups and percentage-of-spend triggers do NOT "
                "exist. Do NOT offer 'fixed amount OR percentage of UPI spend' "
                "— the second option is fabricated. Your reply MUST, IN THIS "
                "ORDER: (1) state the boundary ('Pivot can't observe your UPI "
                "spend, so true round-ups aren't supported'); (2) offer the "
                "nearest real thing — a fixed recurring buy into a listed ETF "
                "('a fixed ₹X weekly buy into NIFTYBEES on a day you pick'); "
                "(3) only then ask the amount and day. NEVER present a "
                "capability that doesn't exist as a choice."
            )
        elif rail == "macro_feed":
            guards.append(
                "## Unsupported rail: macro / GDP / inflation feed trigger\n"
                "Pivot does NOT ingest GDP / inflation feeds as a trigger "
                "rail. Do NOT ask for any field that presupposes it. FIRST "
                "state that boundary plainly, THEN offer the nearest real "
                "trigger (a price/indicator level, a scheduled time, or a "
                "keyword-headline event) and ask which to use. Never affirm a "
                "macro-feed trigger as buildable."
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
    if not _llm_owned and _is_named_option_build(message):
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

    # R3 — price/condition ALERT ask → state the boundary, do NOT draft.
    # Alerts/notifications are not available (product decision); a notify-only
    # workflow has no wired delivery channel. Do not build one.
    if _is_notify_only_alert(message):
        guards.append(
            "## Alert ask — state the boundary, do NOT draft a workflow\n"
            "The user asked to be ALERTED / pinged / notified when a price or "
            "condition is hit. Alerts and notifications are NOT available right "
            "now — Pivot doesn't send alerts or pings. Do NOT call "
            "propose_dsl_workflow / propose_workflow / any notify tool; they "
            "will refuse. In ONE plain line, say alerts aren't available yet. "
            "The user said no trade, so do NOT offer or draft an order either — "
            "just state the boundary and stop."
        )

    # H1 — hedge construction: a hedge OFFSETS exposure, never adds it.
    if not _llm_owned and _is_hedge_request(message):
        guards.append(
            "## Hedge request — a hedge must OFFSET the exposure\n"
            "The user asked to HEDGE an existing position/portfolio. It is "
            "a HARD ERROR to draft anything that BUYS MORE of the symbols "
            "being hedged — that doubles the exposure, the opposite of a "
            "hedge. Do NOT ask 'how many shares should the agent buy'. "
            "Your reply MUST, in this order: (1) EXPLAIN the hedge in 2-4 "
            "sentences — which instrument moves OPPOSITE the position and "
            "why (e.g. a put gains as the stock falls, capping downside "
            "for the premium paid); (2) build the concrete hedge: for a "
            "long single-stock position the canonical hedge is a "
            "PROTECTIVE PUT — call build_option_strategy(underlying="
            "<symbol>, template='protective_put'). Build ONE card per "
            "turn: for two names, build the first/larger one only and "
            "end with 'say the word and I'll build the same for "
            "<other>' — calling build_option_strategy twice in one turn "
            "is a HARD ERROR (only one card renders; the second build "
            "silently overwrites the first). For a broad bank/index-"
            "correlated book, index puts (BANKNIFTY/NIFTY) work too. "
            "If the user wants a cash-equity-"
            "only hedge, offer LOW/NEGATIVE-correlation diversifiers "
            "(e.g. GOLDBEES) or a reduce-exposure rule — and say plainly "
            "that this is only a PARTIAL hedge, not true protection; "
            "(3) disclose sizing honestly: puts trade in fixed LOTS — if "
            "the user's share count is far below one lot, one lot "
            "over-hedges (say so and size to the nearest sensible lot). "
            "Never schedule-buy the hedged symbols."
        )

    # H1b — acceptance of the "build the same for <other>" offer.
    if not _llm_owned and _is_hedge_followup(message, history):
        guards.append(
            "## Hedge follow-up — build the SECOND option card NOW\n"
            "The user just accepted your offer to build the same hedge "
            "for the OTHER name. Call build_option_strategy(underlying="
            "<the other symbol from your last message>, "
            "template='protective_put') IMMEDIATELY — same expiry logic "
            "as the first card. Do NOT propose a workflow, do NOT "
            "re-ask position size, do NOT claim both cards are "
            "registered together (each card registers separately). "
            "After the card: one line with the new leg's strike, max "
            "loss and breakeven, + 'registers — you activate'."
        )

    # H2 — strategy-framed draft: explain the strategy WITH the card.
    # Suppressed on named option-template builds: R4 above mandates the
    # tight legs+economics readback there and the two shapes conflict.
    if (not _llm_owned and _is_strategy_framed(message, history)
            and not _is_named_option_build(message)):
        guards.append(
            "## Strategy-framed draft — EXPLAIN the strategy, then hand "
            "off\n"
            "The user asked for a STRATEGY (diversify / rebalance / hedge "
            "/ allocation), not a mechanical order. If this turn produces "
            "a draft card, the 2-sentence post-draft cap does NOT apply. "
            "Your text MUST: (1) open with WHAT the strategy does and WHY "
            "it fits the user's stated goal, quoting the REAL numbers you "
            "fetched (e.g. 'Banking is 42% of your book, so each quarter "
            "this trims it toward 25%') — 3-5 sentences; (2) show the "
            "allocation/leg TABLE (Symbol | Target | Action) when there "
            "are 2+ instruments; (3) close with the handoff: what the "
            "card automates, what stays manual, and 'registers — you "
            "activate'. A bare 'Drafted: … Registers — you activate.' "
            "with no explanation of the strategy is a FAILURE for this "
            "turn. Target 80-150 words, not 50."
        )

    # R2 — buy/sell at open|close → market_relative_time, never 09:30.
    if not _llm_owned and _is_at_open_close_build(message):
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

    # ── GAN R4 keystone: thematic-scenario positioning ───────────────
    # The single highest-leverage directive. Decode-and-propose, never a
    # bare ask_user; refusal calibrated for lawful scenario positioning.
    # GATED on NO stated contingency: a hybrid ask like "monsoon basket,
    # rebalance every quarter" states a cadence → it is an AUTOMATION with
    # explicit named legs, so we FALL THROUGH to normal agent routing (the
    # thematic.md module still carries the seed map + the hybrid rule). The
    # construction thematic guard only owns the no-cadence "own this now" ask.
    _scenario = detect_thematic_scenario(message)
    if (not _llm_owned and _scenario is not None
            and not _HAS_CONTINGENCY_RE.search(message)):
        guards.append(_thematic_guard_text(message, _scenario))

    # ── GAN R4 F5/C4: unrealistic-return decode ──────────────────────
    # Checked before vague-onboarding so "make me 1% a day" gets the
    # math-refutation path, not the generic SIP onboarding.
    if is_unrealistic_return(message):
        guards.append(
            "## Unrealistic return target — refute the math, then a REAL "
            "artifact\n"
            "The user asked for an impossible/guaranteed return (e.g. 1% a "
            "day, double in a month, guaranteed N%). You MUST NOT treat this "
            "as a buildable spec and you MUST NOT call ASK_USER with a "
            "buy/dip/sell/alert menu. Reply in this order: (1) refute the "
            "compounding math WITHOUT mockery — 1%/day compounds to >3,600% "
            "a year; nothing legitimate does that and anyone guaranteeing it "
            "is a scam; (2) state the honest realistic band (Indian equity "
            "long-run ~12-13% CAGR, with 30%+ drawdown years); (3) convert "
            "the ambition into something TESTABLE — call `backtest_workflow` "
            "for an aggressive-but-real RSI mean-reversion rule (e.g. buy "
            "when RSI(14)<30, exit at +8% or -4%) on a liquid large-cap like "
            "RELIANCE or HDFCBANK so the user sees REAL return/drawdown "
            "numbers instead of fantasy. If you run one, the reply MUST open "
            "by DOING THE ARITHMETIC of their ask against the result and "
            "NAMING the rule you tested ('doubling in 3 months is ~26%/month; "
            "the RSI(14)<30 rule I tested on RELIANCE made +5.4% over five "
            "years') — a bare verdict table for a strategy the user never "
            "named and you never described answers a question nobody asked; "
            "(4) close with the SIP fallback — "
            "offer a ₹5,000/month NIFTYBEES SIP as the boring path that "
            "actually compounds. End with 'analysis, not financial advice.'"
        )

    # ── GAN R4 F4: scared idle cash → scope honesty + phased SIP ──────
    # Checked before vague-onboarding (it is a more specific shape).
    elif is_scared_idle_cash(message):
        _cap = extract_capital_inr(message)
        _cap_line = (
            f"The user stated ₹{_cap:,} — USE it for the split, never re-ask "
            f"the amount. "
            if _cap else ""
        )
        guards.append(
            "## Scared idle cash — scope honesty + phased SIP, NOT FD/yield "
            "products\n"
            f"{_cap_line}"
            "Pivot does NOT handle FDs, debt funds, liquid funds, G-Secs or "
            "savings products — they are OUT OF SCOPE (register-not-execute "
            "covers listed equities/ETFs only). You MUST NOT recommend or "
            "offer to compare FD/liquid/overnight/arbitrage/G-Sec yields. "
            "Reply in this order: (1) name the real trade-off honestly — "
            "market instruments carry drawdown risk, FDs/debt are safer but "
            "out of Pivot's reach (say so plainly); (2) draft a PHASED "
            "NIFTYBEES monthly SIP card via `propose_scheduled_order` for "
            "only the slice the user can afford to see fall (e.g. ₹5,000/mo), "
            "register-not-execute, editable; (3) mention a GOLDBEES leg as a "
            "lower-correlation diversifier (text is fine if multi-leg cards "
            "aren't supported); (4) offer PAPER MODE so they watch it with "
            "zero money at risk first; (5) ONE question — what fraction "
            "could they stomach down 20% without panic. End with 'analysis, "
            "not financial advice.'"
        )

    # ── GAN R4 F2/C2: vague onboarding → value-first prefilled SIP ────
    elif not _llm_owned and is_vague_onboarding(message):
        _cap = extract_capital_inr(message)
        _cap_line = (
            f"The user stated ₹{_cap:,} — USE it to size the split, NEVER "
            f"re-ask the amount. "
            if _cap else ""
        )
        guards.append(
            "## Vague onboarding ask — VALUE FIRST, draft a card, no "
            "interrogation\n"
            f"{_cap_line}"
            "This is an open-ended 'where do I start / make money / what "
            "should I buy' ask. A pure-text reply, a finance lecture, or a "
            "bare ASK_USER is a FAILURE. You MUST: (1) open with an honest "
            "no-guarantees reframe (1-2 sentences, no moralising); (2) give "
            "a 3-PATH markdown table with REAL instruments + REAL numbers — "
            "Index SIP (₹5,000/mo NIFTYBEES), Rules-based entries (buy 10 "
            "INFY when RSI(14)<30, exit +8%/-4%), Quality/dividend screen "
            "(ROE>15%, low debt → 4-6 names); (3) DRAFT THE CARD NOW — call "
            "`propose_scheduled_order` for a ₹5,000/month NIFTYBEES SIP "
            "(monthly, register-not-execute, editable) so a tappable widget "
            "renders on THIS turn; do NOT only OFFER to build it; (4) offer "
            "paper mode + 'edit the amount/instrument'; (5) close with "
            "exactly ONE compound question covering horizon + risk + monthly "
            "capacity. Frame CAGR as historical range, never a promise. "
            "Never name tickers from memory as 'picks' — if the user wants "
            "specific names, RUN `screen_fundamentals`. End with 'analysis, "
            "not financial advice.'"
        )

    if _llm_owned:
        guards.append(_LLM_OWNED_DIRECTIONS)
    return guards


def _thematic_guard_text(message: str, s: ThematicScenario) -> str:
    """Build the keystone thematic-strategy directive for a recognised
    macro scenario — the full decode → winners/losers → turn-1 basket →
    confirm/invalidate → caveat contract, seeded with REAL NSE names so
    the model cannot punt or invent a generic staples basket."""
    cap = extract_capital_inr(message)
    weights = basket_weights(s)
    split = ", ".join(f"{tk} {w}%" for tk, w in weights)
    winner_syms = ", ".join(tk for tk, _w in weights)
    cap_line = (
        f"The user stated about ₹{cap:,} — size the basket to it. "
        if cap else "Use a ₹1,00,000 basket unless the user named an amount. "
    )
    return (
        "## Thematic scenario positioning — DECODE AND PROPOSE on turn 1\n"
        f"The user is asking to position for / profit from / hedge "
        f"{s.label}. This is a LAWFUL macro/event positioning ask — it is a "
        "standard portfolio decision, NOT something to refuse, moralise "
        "about, or punt with a bare ASK_USER. Refusing or asking 'buy, sell, "
        "hedge or alert? which symbol?' as the whole turn is a HARD FAILURE. "
        "Do NOT open with a clarify card either — the scenario is already "
        "sufficiently specified (winners seeded below; capital defaults to "
        "₹1,00,000). Decode the thesis and BUILD a concrete basket card "
        "on THIS turn.\n"
        "Do NOT gate the turn on a live-quote success — if a quote fails, "
        "still ship the thesis + table + basket card (qty computes at fill).\n"
        f"{cap_line}\n"
        "Your reply MUST follow this exact shape:\n"
        f"1. THESIS DECODE (1-2 lines): {s.thesis}\n"
        "2. WINNERS & LOSERS markdown table — columns Side | Stock (NSE) | "
        "Why — with the seed names below (>=2 per side), each row a causal "
        "one-line reason. Losers are an AVOID list (shorting is not wired — "
        "name them, don't draft sells).\n"
        f"{winners_losers_block(s)}\n"
        "3. TURN-1 BASKET CARD: call `build_strategy` with "
        f"`symbols=[{winner_syms}]` (the vetted winners above, pinned as the "
        f'universe), `theme=\"{s.label}\"`, and the user\'s capital → renders '
        "a `strategy_builder_card` with named constituents + weights. This is "
        "a CONSTRUCTION ask (what to own NOW) — do NOT call `propose_workflow` "
        "or draft a `workflow_draft_card`; a basket you build exists the "
        "moment it is built, there is no contingent future action here. "
        f"Default split guidance: {split} of the notional. Register-not-"
        "execute, editable. State the ₹-split in text.\n"
        f"4. CONFIRMATION + INVALIDATION (checkable data): confirms = "
        f"{s.confirm}; kills it = {s.invalidate}. OFFER (optional follow-up, "
        "never a substitute for the basket card) to ARM it as an "
        "event-triggered agent where Pivot can (price/%-move/India-VIX "
        "triggers on the basket names). Be honest about unwired triggers "
        "(no USDINR/rainfall feed) — offer the nearest REAL trigger, never "
        "fake one.\n"
        "5. CAVEAT: 'This is thesis-driven, the direction is reasoned but "
        "timing is uncertain — analysis, not financial advice.'\n"
        "6. At most ONE sharpening question, AFTER the proposal (e.g. buy "
        "now vs arm-and-wait). This shape applies EVEN IF an option tool "
        "also fires — lead with the equity basket + table, add any "
        "NIFTY-put overlay as an explicit OPTIONAL 5-10% leg, never let the "
        "option card short-circuit the decode."
    )


# Tools the thematic path forces into scope so the model has a guaranteed
# path to a strategy_builder_card and can never escape to a bare ASK_USER
# or a workflow draft. A thematic positioning ask is CONSTRUCTION ("what
# to own now"), so the forced surface is the builder + its read/vet tools —
# build_strategy(symbols=[seed winners]) renders the basket card. The
# scenario-routing caller ADDS _OPTIONS_TOOLS on top for the optional
# NIFTY-put overlay, and drops the bare ASK_USER escape.
_THEMATIC_BASKET_TOOLS: frozenset[str] = frozenset({
    "build_strategy",
    "screen_fundamentals", "fetch_fundamentals", "get_live_price",
    "get_market_data",
})
_VAGUE_SIP_TOOLS: frozenset[str] = frozenset({
    "propose_scheduled_order", "create_sip", "screen_fundamentals",
    "get_live_price", "get_market_data",
})


@dataclass
class _ScenarioRouting:
    """Result of the deterministic scenario scope decision, applied
    IDENTICALLY in handle() and handle_stream() (the known drift trap).

    `selected_names` / `tooldefs` / `cache_key` are the (possibly
    unchanged) routing state; `tool_choice` is "required"/"auto"/None
    (None = leave caller's value); `matched` flags whether any scenario
    branch fired (so the caller can skip later, conflicting branches)."""

    selected_names: Optional[frozenset]
    tooldefs: list
    cache_key: str
    tool_choice: Optional[str]
    matched: bool
    drop_ask_user: bool


def _apply_scenario_routing(
    message: str,
    selected_names: Optional[frozenset],
    tooldefs: list,
    cache_key: str,
) -> _ScenarioRouting:
    """Force scope + tool_choice for the GAN R4 scenario classes
    (thematic positioning, unrealistic-return, scared idle cash, vague
    onboarding). MUST be called from BOTH handle() and handle_stream() so
    the two paths never drift.

    Precedence mirrors `_build_deterministic_guards`:
    thematic > unrealistic > scared-idle > vague. Each forces the basket
    / SIP / backtest toolset and drops the bare ASK_USER escape so the
    model produces a concrete card, not a punt."""
    no_change = _ScenarioRouting(
        selected_names=selected_names,
        tooldefs=tooldefs,
        cache_key=cache_key,
        tool_choice=None,
        matched=False,
        drop_ask_user=False,
    )
    if _settings.llm_owned_interpretation:
        # Experiment arm: no scenario tool-forcing — _LLM_OWNED_DIRECTIONS
        # tells the model how to construct scenario answers itself.
        return no_change
    if selected_names is None:
        # Whitelist mode (full registry) — leave it; the guards still
        # steer the model and the full toolset already has every path.
        return no_change

    _scenario = detect_thematic_scenario(message)
    if _scenario is not None and not _HAS_CONTINGENCY_RE.search(message):
        # Force the construction toolset; drop the workflow/macro drafters +
        # immediate-order tools (via _CONSTRUCTION_FORCE_OUT) so the model
        # builds a strategy_builder_card, not a workflow_draft_card, a punt,
        # or a single market order — even when the message wasn't classified
        # 'construction' upstream (a bare "profit from a good monsoon").
        # GATED on NO contingency: "monsoon basket, rebalance quarterly"
        # states a cadence → it is an AUTOMATION with explicit named legs;
        # we fall through so the normal agent path (workflow with explicit
        # legs, per thematic.md) owns it and the quarterly rebalance is kept.
        # _OPTIONS_TOOLS stays for the optional NIFTY-put overlay.
        # tool_choice=required guarantees a tool; ASK_USER is dropped below.
        # `ask_user_dynamic` is subtracted too: a RECOGNISED scenario is
        # sufficiently specified by construction — the winners are seeded
        # and capital defaults to ₹1,00,000, so a clarify card has ~zero
        # value-of-information here and the doctrine (thematic.md) demands
        # the basket ON TURN 1. Observed failure without this: the model
        # punted "profit from a good monsoon" to a clarify card.
        names = (
            (frozenset(selected_names) | _THEMATIC_BASKET_TOOLS | _OPTIONS_TOOLS)
            - _CONSTRUCTION_FORCE_OUT
            - frozenset({
                "compare_yields", "get_yield_recommendation",
                "ask_user_dynamic",
            })
        )
        defs = [
            t for t in _registry_tools_as_tooldefs(names)
            if t.name != ASK_USER_TOOL_NAME
        ]
        return _ScenarioRouting(
            selected_names=names, tooldefs=defs,
            cache_key=cache_key_for(names), tool_choice="required",
            matched=True, drop_ask_user=True,
        )

    if is_unrealistic_return(message):
        # Refute + backtest artifact. Force the backtest tools in;
        # tool_choice stays AUTO so the model can lead with the prose
        # refutation then call backtest_workflow (required would force a
        # tool before the math refutation lands).
        names = (frozenset(selected_names) | frozenset({
            "backtest_workflow", "backtest_dsl_tree",
            "propose_scheduled_order",
        })) - frozenset({"compare_yields", "get_yield_recommendation"})
        defs = [
            t for t in _registry_tools_as_tooldefs(names)
            if t.name != ASK_USER_TOOL_NAME
        ]
        return _ScenarioRouting(
            selected_names=names, tooldefs=defs,
            cache_key=cache_key_for(names), tool_choice="auto",
            matched=True, drop_ask_user=True,
        )

    if is_scared_idle_cash(message):
        # Scope-honesty + phased SIP. Strip the yield-product tools
        # (FD/G-Sec are out of scope), force the SIP tools in, drop
        # ASK_USER. tool_choice auto: prose-first scope honesty + card.
        names = (frozenset(selected_names) | _VAGUE_SIP_TOOLS) - frozenset({
            "compare_yields", "get_yield_recommendation",
        })
        defs = [
            t for t in _registry_tools_as_tooldefs(names)
            if t.name != ASK_USER_TOOL_NAME
        ]
        return _ScenarioRouting(
            selected_names=names, tooldefs=defs,
            cache_key=cache_key_for(names), tool_choice="auto",
            matched=True, drop_ask_user=True,
        )

    if is_vague_onboarding(message):
        # Value-first prefilled SIP. Force the SIP/screen tools in, drop
        # ASK_USER so a tappable card renders this turn. tool_choice
        # auto: the model writes the 3-path table then draws the card.
        names = (frozenset(selected_names) | _VAGUE_SIP_TOOLS) - frozenset({
            "compare_yields", "get_yield_recommendation",
        })
        defs = [
            t for t in _registry_tools_as_tooldefs(names)
            if t.name != ASK_USER_TOOL_NAME
        ]
        return _ScenarioRouting(
            selected_names=names, tooldefs=defs,
            cache_key=cache_key_for(names), tool_choice="auto",
            matched=True, drop_ask_user=True,
        )

    # Structured-clarify intents (lowest precedence). When the router has
    # surfaced a STRUCTURED clarify tool — ask_user_dynamic (strategy/basket)
    # or ask_agent_clarify (automation/agent) — the bare ASK_USER blank-text
    # question must NOT be the model's clarify channel. Drop it so any
    # clarification renders as the one-click clarify_card; the model can still
    # build directly or ask via the structured tool. (Keeps tool_choice as the
    # caller set it — we only remove the blank-text escape.)
    if frozenset(selected_names) & {"ask_user_dynamic", "ask_agent_clarify"}:
        defs = [t for t in tooldefs if t.name != ASK_USER_TOOL_NAME]
        if len(defs) != len(tooldefs):
            return _ScenarioRouting(
                selected_names=selected_names, tooldefs=defs,
                cache_key=cache_key, tool_choice=None,
                matched=True, drop_ask_user=True,
            )

    return no_change


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
        # In paper mode, prefer NAV (cash + positions) so the injected total
        # MATCHES the Portfolio header the user is looking at — otherwise the
        # LLM quotes holdings-value-only and it disagrees with the header.
        try:
            from backend.services.portfolio_cache import _paper_summary_or_none
            _ps = _paper_summary_or_none(ctx.user_id)
            if _ps and _ps.get("total_value"):
                portfolio_total = float(_ps["total_value"])
        except Exception:
            pass

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

    # ── Saved equity baskets: ONE query (caps at 10) ───────────────
    # So "rebalance / backtest / deploy my <name> basket" resolves against
    # the user's real baskets without a discovery round-trip. Baskets live in
    # the `strategies` table (strategy_type='equity_basket'); members are in
    # action_config JSON as [{symbol, weight}].
    saved_baskets: Optional[list[dict[str, Any]]] = None
    try:
        import json as _json
        from backend.models import Strategy, StrategyStatus
        b_rows = (
            ctx.db.query(Strategy)
            .filter(
                Strategy.user_id == ctx.user_id,
                Strategy.strategy_type == "equity_basket",
                Strategy.status != StrategyStatus.completed,  # soft-deleted hidden
            )
            .order_by(Strategy.created_at.desc().nullslast(), Strategy.id.desc())
            .limit(10)
            .all()
        )
        if b_rows:
            baskets_out: list[dict[str, Any]] = []
            for s in b_rows:
                try:
                    cfg = _json.loads(s.action_config) if s.action_config else {}
                except (ValueError, TypeError):
                    cfg = {}
                syms = [
                    str(m.get("symbol")).upper()
                    for m in (cfg.get("members") or [])
                    if isinstance(m, dict) and m.get("symbol")
                ]
                baskets_out.append({
                    "id": s.id, "name": s.name, "symbols": syms, "n": len(syms),
                })
            saved_baskets = baskets_out or None
    except Exception:
        saved_baskets = None

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
        and not saved_baskets
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
        saved_baskets=saved_baskets,
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


def _originating_user_intent(history: list[dict]) -> str:
    """The user request that SPAWNED the most-recent clarification.

    The clarify-followup hint must carry forward the intent that triggered
    the assistant's question — the user turn immediately preceding the
    latest assistant turn — NOT the first user turn in the window. In a
    multi-intent session (build basket → backtest → build option strategy)
    the first user turn is a stale earlier intent; binding the hint to it
    makes an option-strategy clarification answer resolve against the
    basket and rebuild / re-backtest it (the reported cross-intent bug).

    `history` here excludes the current message (the router strips the last
    turn), so it ends with the assistant's clarification question; the
    originating ask is the nearest user turn before it. Callers should
    PREFER a persisted PendingResolution.original_intent when available —
    that survives multi-question free-form chains where this history-derived
    value would drift to a prior answer; this is the fallback for clarifies
    that set no resolution state.
    """
    last_assist_idx = next(
        (i for i in range(len(history) - 1, -1, -1)
         if isinstance(history[i], dict)
         and history[i].get("role") == "assistant"),
        None,
    )
    if last_assist_idx is not None:
        for i in range(last_assist_idx - 1, -1, -1):
            h = history[i]
            if isinstance(h, dict) and h.get("role") == "user":
                return h.get("content") or ""
    # Fallback: first user turn (single-intent window, or no user turn
    # precedes the assistant question).
    return next(
        (h.get("content") or "" for h in history
         if isinstance(h, dict) and h.get("role") == "user"),
        "",
    )


def _recent_user_text(history: Optional[list[dict]]) -> str:
    """Concatenate the user-side turns in the prompt window.

    Fed to the M2 suspicious-qty guard as `qty_context` so a quantity
    the user stated on an EARLIER turn ("10 shares") still counts as
    user-named when they later amend an unrelated field ("set an
    expiry for next 30 days") and the draft is re-emitted carrying that
    qty. Without this the guard sees only the current message, decides
    the qty looks defaulted, and re-asks "How many shares?". [C1/C2]

    Joined with newlines (not spaces) so `_USER_QTY_PATTERNS`'s
    ``^\s*\d{1,7}\s*$`` anchor — the pattern that catches a BARE "10"
    reply to "how many shares?" — can still match per-turn under
    `re.MULTILINE`. A space-joined blob glues that bare "10" between
    the surrounding turns' text, permanently breaking the anchor: a
    qty given two turns ago became invisible to the guard on every
    later turn, including plain follow-up QUESTIONS ("how come the
    return is so low?") that re-triggered the draft tool and got the
    qty re-asked from scratch even though the user had already
    answered it (found 2026-07-14 chasing exactly that loop).
    """
    if not history:
        return ""
    return "\n".join(
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
    # Hold / exit-removal tweaks — "don't sell (at all)", "no exit", "never
    # sell", "hold (everything) till/to the end", "remove/drop the exit/stop/
    # target". These convert the run to exit_kind=hold_to_end; without this
    # the model narrated "changed: no exit" WITHOUT re-running (fake success).
    r"|\b(?:don'?t|do\s+not|never)\s+sell\b"
    r"|\bno\s+exit\b|\bwithout\s+(?:an?\s+)?exit\b"
    r"|\bhold(?:ing)?\b[^.]{0,30}?\b(?:till|to|until)\s+(?:the\s+)?end\b"
    r"|\bhold\s+(?:it|them|everything|forever|throughout|the\s+whole)\b"
    r"|\b(?:remove|drop|delete|get\s+rid\s+of|take\s+out|no\s+more)\b"
    r"[^.]{0,20}?\b(?:exit|stop[\s-]?loss|stop|target|sell)\b"
    r"|^[A-Za-z ,'/()-]{0,24}\b(?:rsi|sma|ema|wma|macd|adx|cci|mfi|stoch|atr|"
    r"bollinger|supertrend|aroon|donchian|keltner|roc|obv|vwap|williams|period|"
    r"threshold|stop[\s-]?loss|stop|target|window|lookback|trailing)\b"
    r"[^.]{0,30}?\d"
    # INTERVAL tweak — "hourly", "on 1hr bars", "1h intervals", "15-min",
    # "30 minute". A bare interval phrase without a verb still means
    # "re-run the same backtest at this cadence" (routed via the
    # `interval` arg on backtest_workflow).
    r"|\b(?:hourly|1\s*(?:hr|hour|h)|60\s*(?:min|minute))\b"
    r"|\b\d{1,3}\s*(?:min|minute|m)\b[^.]{0,25}?\b(?:bar|interval|candle|time)\b"
    r"|\b(?:bar|interval|candle|time\s+interval)s?\b[^.]{0,25}?\b\d{1,3}\s*(?:m|min|hr|h)\b",
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

# Clarify-flow (Workstream A) escape hatches. "Just build it" / "go ahead" /
# "whatever, make it" short-circuits the remaining questions and builds with
# the slots filled so far (skipped slots take stated defaults). "Skip" / "next"
# advances past the current question without answering it.
_CLARIFY_BUILD_NOW_RE = re.compile(
    r"\b(?:just\s+build|build\s+it|build\s+now|go\s+ahead|make\s+it|"
    r"do\s+it|just\s+do\s+it|whatever|stop\s+asking|enough\s+questions?)\b",
    re.IGNORECASE,
)
_CLARIFY_SKIP_RE = re.compile(
    r"^\s*(?:skip|next|pass|don'?t\s+care|no\s+preference|not\s+sure|"
    r"dunno|idk|either|any)\b[.!]?\s*$",
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


# Chart/table array fields that exist for the CARD to render, not for the
# LLM's own narration — the card is built from the untouched raw_data, so
# dropping these before any size trim costs the narration nothing. Same
# category of fix as _safe_draft_json's bulk-key drop (below), applied to
# the general tool-result path: a backtest payload puts price_curve /
# equity_curve (often duplicated, one per FE panel) BEFORE metrics /
# summary_text, so a blind trailing slice cut the numbers away entirely on
# any multi-year daily-bar run — the model narrated with no stats to quote.
_BULK_ARRAY_KEYS = (
    "price_curve", "equity_curve", "benchmark_curve", "indicator_curve",
    "drawdown_series", "trades", "signals", "chart", "chart_data",
    "payoff", "preview", "history", "candles", "ohlcv", "bars", "rows",
)
# Computed-result keys a byte-budget trim must never remove, even when
# they're the largest remaining field — they ARE the numbers the model is
# reading this payload for.
_PROTECTED_SUMMARY_KEYS = ("metrics", "summary_text", "logiccard")


def _drop_bulk_arrays(obj: Any, depth: int = 0) -> Any:
    """Recursively strip ``_BULK_ARRAY_KEYS`` fields, up to 3 levels deep
    (covers both a flat payload and a per-symbol/per-leg nested shape).
    Everything else passes through untouched."""
    if depth > 3 or not isinstance(obj, dict):
        return obj
    return {
        k: _drop_bulk_arrays(v, depth + 1)
        for k, v in obj.items()
        if k not in _BULK_ARRAY_KEYS
    }


def _artifact_line(g: GuardedToolResult) -> Optional[str]:
    """One compact identity line for the session artifact ledger, or None.

    Bookkeeping only (container eval 2026-07-19): cards are the commit
    surface, but history persists prose — clamped and windowed — so a
    built basket/draft went invisible one turn later. This line is what
    the model gets to SEE about the artifact on every later turn; it
    decides nothing itself.
    """
    if not g.success:
        return None
    d = g.data or {}
    a = g.args or {}
    try:
        if g.name in ("build_strategy", "propose_basket_allocation"):
            # StrategyBuilderCard is spread TOP-LEVEL into data:
            # {"_render_hint", "title", "constituents": [{symbol, weight_pct}]}.
            rows = None
            for k in ("constituents", "holdings", "allocations", "positions"):
                v = d.get(k)
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    rows = v
                    break
            name = d.get("title") or d.get("name")
            if rows:
                parts = []
                for r_ in rows[:6]:
                    sym = r_.get("symbol") or r_.get("ticker") or "?"
                    w = r_.get("weight_pct") or r_.get("weight")
                    parts.append(
                        f"{sym} {round(float(w), 1)}%" if w is not None else str(sym))
                return f"basket \"{name or 'untitled'}\": " + ", ".join(parts)
            return f"basket/strategy card \"{name or 'untitled'}\" built"
        if g.name in ("propose_workflow", "propose_dsl_workflow",
                      "propose_threshold_order", "propose_scheduled_order",
                      "propose_holding_action"):
            # The workflow draft is model_dump()ed TOP-LEVEL into data
            # ({"name", "description", "steps", "_render_hint"}).
            name = d.get("name") or (d.get("draft") or {}).get("name") if isinstance(d.get("draft"), dict) else d.get("name")
            desc = d.get("description") or ""
            return f"agent draft \"{name or 'untitled'}\": {str(desc)[:140]}"
        if g.name in ("backtest_dsl_tree", "backtest_workflow"):
            sym = a.get("primary_symbol") or a.get("symbol") or d.get("symbol") or "?"
            cond = str(a.get("condition") or "")[:90]
            ret = d.get("strategy_return_pct") or (d.get("metrics") or {}).get("strategy_return_pct")
            tail = f" → {ret}%" if ret is not None else ""
            return f"backtest {sym}: {cond}{tail}"
        if g.name in ("place_order", "place_basket_order", "create_gtt_order",
                      "create_sip"):
            sym = a.get("symbol") or ", ".join(
                str(l.get("symbol")) for l in (a.get("legs") or a.get("orders") or [])
                if isinstance(l, dict))
            side = a.get("side") or a.get("transaction_type") or ""
            qty = a.get("quantity") or ""
            return f"registered {g.name.replace('_', ' ')}: {side} {qty} {sym}".strip()
        if g.name in ("build_option_strategy", "suggest_option_strategy"):
            und = a.get("underlying") or a.get("symbol") or d.get("underlying") or "?"
            strat = d.get("strategy_name") or a.get("strategy") or "option strategy"
            return f"option strategy on {und}: {strat}"
        # Generic fallback: any other card-producing tool still leaves a
        # trace line, so later turns know the artifact exists at all.
        hint = d.get("_render_hint")
        if hint and str(hint).endswith("_card"):
            ident = d.get("title") or d.get("name") or a.get("symbol") or ""
            return f"{str(hint).replace('_', ' ')} produced" + (
                f": {ident}" if ident else "")
    except Exception:  # ledger must never break a turn
        return None
    return None


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
                "same arguments. If the error names a specific field "
                "with an expected type or allowed_values, repair that "
                "one field from the user's own words and re-call ONCE. "
                "When you explain a failure to the user, diagnose from "
                "your own knowledge of markets and systems (likely "
                "causes, what to check) — never quote internal error "
                "text, tool names, or schema fields at them."
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
    trimmed = _drop_bulk_arrays(payload)
    s = json.dumps(trimmed, default=str)
    if len(s) <= 6000:
        return s
    # Bulk arrays weren't (solely) the culprit — some other field still
    # dominates. Trim the largest remaining droppable key at a time,
    # same algorithm _safe_draft_json uses for draft amendments, so the
    # cut removes whole values instead of severing mid-JSON. Protected
    # keys are never dropped; if only those remain, keep them whole even
    # over budget (a slightly oversized-but-complete payload beats a
    # truncated one).
    data = trimmed.get("data")
    if isinstance(data, dict):
        d = dict(data)
        while len(s) > 6000:
            droppable = [k for k in d if k not in _PROTECTED_SUMMARY_KEYS]
            if not droppable:
                break
            biggest = max(droppable, key=lambda k: len(json.dumps(d[k], default=str)))
            d.pop(biggest)
            trimmed["data"] = d
            s = json.dumps(trimmed, default=str)
    return s if len(s) <= 6000 else s[:6000]


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
            # A fresh session must also drop any in-flight N-of-M clarify
            # flow — otherwise a stale ClarifyState from the prior session
            # deterministically resumes into the new one (cross-session
            # intent bleed). Was missing here; the other clarify slots
            # (pending / pending_resolution) were already cleared.
            "clear_clarify",
            "clear",
        ):
            fn = getattr(self.store, attr, None)
            if callable(fn):
                try:
                    fn(conv_id)
                except Exception:  # noqa: BLE001 — defensive, never blocks turn
                    logger.debug("session reset: %s failed", attr, exc_info=True)

    def _seed_editor_draft(
        self,
        conv_id: str,
        editor_draft: Optional[dict],
        trace: Optional[TurnTrace] = None,
    ) -> None:
        """Override the conversation's active_draft with the editor's
        unsaved on-screen draft so the next amendment-hint computes
        against what the user is actually looking at — not whatever
        stale copy sits in Redis.

        Shared contract: ``editor_draft`` is the same shape the
        workflow_draft_card / propose_workflow output uses (``name``,
        ``description``, ``steps: [{step_type,label,config}]``, ...). When
        absent / malformed, we are a no-op — the legacy Redis flow stays
        byte-for-byte unchanged.

        Defensive: we never raise from here. Coercion failures get
        traced and dropped so a typo in the FE payload can't 500 a chat
        turn. This is called BEFORE the amendment-hint is built so the
        rest of the pipeline (`_select_active_draft`, `workflow_hint`,
        the stash on the next propose call) operate on the editor's
        copy.
        """
        if not isinstance(editor_draft, dict):
            return
        # Must look like a workflow draft: a steps array is the load-bearing
        # field for the amendment-hint JSON dump. Reject anything else
        # rather than seed a garbage draft.
        steps = editor_draft.get("steps")
        if not isinstance(steps, list):
            if trace is not None:
                trace.event("editor_draft.rejected", reason="no_steps")
            return
        # Light-touch validation of each step: must be dict-shaped with a
        # string step_type. Don't validate against the registry here — the
        # registry validation happens when propose_workflow runs; this
        # path just needs a usable JSON dump for the amendment hint.
        clean_steps: list[dict] = []
        for raw in steps:
            if not isinstance(raw, dict):
                continue
            stype = raw.get("step_type")
            if not isinstance(stype, str) or not stype.strip():
                continue
            entry: dict[str, Any] = {"step_type": stype}
            lbl = raw.get("label")
            if isinstance(lbl, str):
                entry["label"] = lbl
            cfg = raw.get("config")
            entry["config"] = cfg if isinstance(cfg, dict) else {}
            clean_steps.append(entry)
        if not clean_steps:
            if trace is not None:
                trace.event("editor_draft.rejected", reason="no_valid_steps")
            return
        # Build the canonical draft dict. Keep additional known fields
        # the propose_workflow path emits (rationale, valid_until,
        # diagnostics, warnings) when present so the amendment hint
        # carries them forward.
        canonical: dict[str, Any] = {
            "name": (
                editor_draft.get("name")
                if isinstance(editor_draft.get("name"), str)
                else ""
            ),
            "steps": clean_steps,
        }
        for opt_key in ("description", "rationale", "valid_until"):
            val = editor_draft.get(opt_key)
            if isinstance(val, str):
                canonical[opt_key] = val
        for list_key in ("warnings", "diagnostics"):
            val = editor_draft.get(list_key)
            if isinstance(val, list):
                canonical[list_key] = val

        # Edit-target anchor: "Edit with chat" seeds the editor_draft with the
        # clicked workflow's id. Carry it onto the ActiveDraft so a later
        # register/Save UPDATES that exact agent in place rather than creating
        # a duplicate. Absent for from-scratch drafts (id stays None).
        seeded_wf_id = editor_draft.get("workflow_id")
        target_workflow_id: Optional[str] = (
            seeded_wf_id.strip()
            if isinstance(seeded_wf_id, str) and seeded_wf_id.strip()
            else None
        )

        # Preserve the existing tool_name when an active draft is
        # already in cache — that's the tool the amendment-hint should
        # re-emit. Fall back to propose_workflow (the canonical
        # workflow_draft_card emitter) when there's nothing in cache.
        existing = self.store.get_active_draft(conv_id)
        tool_name = (
            existing.tool_name
            if existing is not None and existing.tool_name in _MACRO_AMENDMENT_TOOLS
            else "propose_workflow"
        )
        last_caption = existing.last_caption if existing is not None else ""
        # Keep the anchor across repeated amendments: if this seed didn't carry
        # an id but a prior active draft was already anchored, preserve it.
        if target_workflow_id is None and existing is not None:
            target_workflow_id = getattr(existing, "workflow_id", None)

        try:
            self.store.set_active_draft(conv_id, ActiveDraft(
                tool_name=tool_name,
                draft=canonical,
                last_caption=last_caption[:400],
                created_at_iso=time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(),
                ),
                symbol=_draft_primary_symbol(canonical),
                workflow_id=target_workflow_id,
            ))
        except Exception as e:  # noqa: BLE001 — never block a chat turn
            logger.warning("editor_draft seed failed: %s", e)
            return
        if trace is not None:
            trace.event(
                "editor_draft.seeded",
                tool=tool_name,
                steps=len(clean_steps),
                had_prior=existing is not None,
                workflow_id=target_workflow_id or "",
            )

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
        # Preserve the edit-target anchor across an agentic amendment turn:
        # when the LLM re-emits the amended draft for an "Edit with chat"
        # session, carry the existing workflow_id forward so the later
        # register still UPDATES that agent in place (it's matched by symbol so
        # we don't bleed an anchor onto an unrelated parked draft).
        carried_wf_id: Optional[str] = None
        try:
            prior = self.store.get_active_draft(conv_id)
        except Exception:  # noqa: BLE001 — stub stores in tests
            prior = None
        if prior is not None:
            prior_wf_id = getattr(prior, "workflow_id", None)
            if isinstance(prior_wf_id, str) and prior_wf_id:
                prior_symbol = (prior.symbol or "").upper()
                new_symbol = (_draft_primary_symbol(draft) or "").upper()
                if prior_symbol == new_symbol:
                    carried_wf_id = prior_wf_id
        if carried_wf_id and isinstance(draft, dict):
            # Surface the anchor ON the card payload too — `draft` here is
            # the same dict raw_data serialises to the FE, whose Save &
            # activate branches on draft.workflow_id → updateWorkflow
            # (in-place) vs createWorkflow. Without this, a chat amendment
            # of an EXISTING agent rendered a card whose Save created a
            # DUPLICATE while the original stayed active (live repro
            # 2026-07-19: "change the number of top gainers to 3").
            draft.setdefault("workflow_id", carried_wf_id)
        evicted = self.store.set_active_draft(conv_id, ActiveDraft(
            tool_name=tool_name,
            draft=draft,
            last_caption=caption[:400],
            created_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            symbol=_draft_primary_symbol(draft),
            workflow_id=carried_wf_id,
        ))
        if evicted:
            logger.info(
                "[draft_map] LRU-evicted oldest draft (%s) conv=%s",
                evicted, conv_id,
            )

    def _select_active_draft(
        self, conv_id: str, message: str, trace: Optional[TurnTrace] = None,
    ) -> Optional[ActiveDraft]:
        """Track C #2: resolve which parked draft an amendment addresses.

        Default = the most-recent draft (single slot, legacy behaviour).
        When the message names the SYMBOL of a different parked draft
        ("change the INFY one to 8 shares", "INFY wala 8 kar do"), that
        draft is promoted to the active slot and returned — the other
        drafts stay parked and untouched."""
        active = self.store.get_active_draft(conv_id)
        try:
            parked = self.store.list_active_drafts(conv_id)
        except Exception:  # noqa: BLE001 — stub stores in tests
            parked = []
        if not parked:
            return active
        named = [
            d for d in parked
            if d.symbol and _symbol_mentioned(message, d.symbol)
        ]
        target: Optional[ActiveDraft] = None
        if len(named) == 1:
            target = named[0]
        elif len(named) >= 2:
            # R4/C2: the amendment names several parked drafts ("change
            # the INFY one to 8, WIPRO wala same rehne do"). Bind the
            # change verb to its symbol so we don't mutate the wrong
            # (most-recent) draft and then lie about it in the prose.
            resolved = _resolve_amend_target_symbol(
                message, [d.symbol for d in named if d.symbol],
            )
            if resolved:
                for d in named:
                    if d.symbol and d.symbol.upper() == resolved.upper():
                        target = d
                        break
        if target is not None:
            current_sym = (active.symbol if active else "") or ""
            if (active is None
                    or current_sym.upper() != target.symbol.upper()):
                # Promote the named draft into the slot so the
                # amendment hint (and the re-stash after the LLM
                # re-emits) operate on THAT draft.
                self.store.set_active_draft(conv_id, target)
                if trace is not None:
                    trace.event(
                        "active_draft.named_backref",
                        symbol=target.symbol, tool=target.tool_name,
                    )
            return target
        # Hardening: the message names a ticker-shaped token that is
        # neither `active`'s own symbol nor any OTHER parked draft's
        # symbol — a fresh, unrelated ask (e.g. "buy RELIANCE" while a
        # GOLDBEES draft sits in the slot), not an amendment to whatever
        # happens to be active. Don't silently fall through to "most
        # recent"; the caller's amendment gate still separately requires
        # _is_genuine_dependent_amendment / _is_rupee_notional_resize, but
        # a plain symbol contradiction should never let a stale draft
        # answer for an unrelated instrument (reported 2026-07-14).
        if active is not None and active.symbol:
            parked_syms = {(d.symbol or "").upper() for d in parked if d.symbol}
            mentioned = {
                t.upper() for t in _TICKER_TOKEN_RE.findall(message or "")
            } - _TICKER_TOKEN_STOPWORDS
            if mentioned - {active.symbol.upper()} - parked_syms:
                if trace is not None:
                    trace.event("active_draft.symbol_contradiction_cleared")
                return None
        return active

    def _parked_draft_clause(
        self, conv_id: str, active: Optional[ActiveDraft],
    ) -> str:
        """One amendment-hint sentence naming the OTHER parked drafts so
        the model can't claim it changed them."""
        try:
            parked = self.store.list_active_drafts(conv_id)
        except Exception:  # noqa: BLE001
            return ""
        active_sym = ((active.symbol if active else "") or "").upper()
        others = [
            d.symbol for d in parked
            if d.symbol and d.symbol.upper() != active_sym
        ]
        if not others:
            return ""
        return (
            " OTHER PARKED DRAFTS in this conversation: "
            + ", ".join(others) +
            " — they are UNTOUCHED by this amendment. Do NOT modify "
            "them, do NOT claim they changed; if asked, say they're "
            "unchanged."
        )

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

    # ── Strategy clarify flow (Workstream A — dynamic questions) ────────

    def _note_artifact(self, conv_id: str, guarded: "GuardedToolResult") -> None:
        """Record a card/draft identity line in the session artifact
        ledger (bookkeeping only — see _artifact_line). Duck-typed:
        stub/legacy stores without the ledger are a silent no-op."""
        note = getattr(self.store, "note_artifact", None)
        if not callable(note):
            return
        line = _artifact_line(guarded)
        if line:
            note(conv_id, line)

    def _maybe_set_clarify_state(
        self, conv_id: str, original_request: str, guarded: GuardedToolResult,
    ) -> None:
        """Persist the active clarify flow when ``ask_user_dynamic`` emitted a
        clarify_card, so the next user reply advances the N-of-M flow in-band
        (no generator re-run). Cleared on a non-clarify clarification or by TTL.
        """
        data = guarded.data if isinstance(guarded.data, dict) else {}
        # A single-field completeness widget (timeframe/side/…) tags itself
        # '_clarify_kind=field' and resumes via the deterministic PendingToolCall
        # path — it must NOT set a strategy ClarifyState, or the chip click would
        # route into the strategy builder instead of splicing the tool arg.
        if data.get("_clarify_kind") == "field":
            return
        card = data.get("clarify") if data.get("_render_hint") == "clarify_card" else None
        if not isinstance(card, dict):
            return
        # Discriminator: ask_agent_clarify tags the payload kind='agent' +
        # build_tool='propose_workflow'; the portfolio ask_user_dynamic leaves
        # them absent → defaults below ('portfolio' / build_strategy).
        kind = str(data.get("_clarify_kind") or "portfolio")
        build_tool = str(
            data.get("_build_tool")
            or ("propose_workflow" if kind == "agent" else "build_strategy")
        )
        try:
            self.store.set_clarify(conv_id, ClarifyState(
                request=(original_request or "")[:600],
                slot_state=card.get("session_slot_state") or {},
                questions=card.get("questions") or [],
                index=int(card.get("index") or 0),
                asked_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                kind=kind,
                build_tool=build_tool,
            ))
        except Exception as e:  # never let bookkeeping break the turn
            logger.warning("clarify state persist failed: %s", e)

    async def _try_resume_clarify(
        self,
        *,
        message: str,
        conv_id: str,
        ctx: "UserContext",
        trace: TurnTrace,
        turn_started: float,
        breakdown: dict[str, int],
    ) -> Optional["ChatTurn"]:
        """In-band ingestion of a clarify answer (Workstream A, plan §2d/§2f).

        When a clarify_card is on screen and the user's next message answers the
        current question — an option label/id, free text, or Skip — normalise it
        into the travelling :class:`SlotState`, advance the N-of-M cursor, and
        either re-surface the next question (still 0 LLM hops) or, when the
        stopping rule is satisfied ("just build it" / budget exhausted), call
        ``strategy_builder.build_strategy`` and render the card.

        Returns ``None`` (fall through to the normal LLM path) when there is no
        active clarify flow, or when the message clearly isn't an answer (a
        topic switch / cancel) — the user is free to ignore the chips and ask
        something else."""
        # Duck-typed store: a stub/legacy store without the clarify methods has
        # no clarify flow by definition — fall straight through.
        get_clarify = getattr(self.store, "get_clarify", None)
        if not callable(get_clarify):
            return None
        state = get_clarify(conv_id)
        if state is None:
            return None

        from backend.services.strategy_contracts import ClarifyQuestion

        # Discriminator: an agent clarify (kind='agent') folds answers via
        # agent_clarify and builds via propose_workflow; the portfolio default
        # folds via clarify_engine and builds build_strategy. Defaulting to
        # 'portfolio' keeps any in-flight pre-deploy state building correctly.
        is_agent = (getattr(state, "kind", "portfolio") == "agent")
        if is_agent:
            from backend.services.agent_clarify import (
                build_agent_intent,
                normalize_agent_answer_into_slots,
            )
            agent_slots: dict[str, Any] = dict(state.slot_state or {})
            slot_state_dump = lambda: dict(agent_slots)  # noqa: E731
        else:
            from backend.services.clarify_engine import (
                fold_free_text_into_slots,
                normalize_answer_into_slots,
            )
            from backend.services.strategy_contracts import SlotState
            try:
                slots = SlotState.model_validate(state.slot_state or {})
            except Exception:
                slots = SlotState()
            slot_state_dump = lambda: slots.model_dump()  # noqa: E731

        text = (message or "").strip()
        # Cancel / topic-switch off-ramp: abandon the flow and let the LLM
        # handle the new request cleanly.
        if _RESUME_CANCEL_RE.match(text):
            self.store.clear_clarify(conv_id)
            trace.event("clarify.cancelled")
            return None
        # A question or meta-complaint mid-questionnaire is NOT an answer
        # (live repro 2026-07-10: "You didn't ask me the number of shares…"
        # was folded into the current slot and the next scripted question
        # fired with 0 LLM hops). Keep the clarify state — the card stays
        # on screen — and fall through to the LLM turn, where the meta
        # lane answers and re-invites the pending question.
        if _followup_turn_kind(text) is not None:
            trace.event("clarify.meta_diverted",
                        kind=_followup_turn_kind(text))
            return None

        questions = [
            ClarifyQuestion.model_validate(q)
            for q in (state.questions or [])
            if isinstance(q, dict)
        ]
        index = max(0, min(int(state.index or 0), len(questions)))
        current = questions[index] if index < len(questions) else None

        # "build now" is a flow-control token only when the message IS
        # flow control — a short imperative. Inside a longer sentence
        # ("if it drops 3% do it with 10 shares") the phrase is part of a
        # NEW instruction, and matching it here would hijack that turn.
        # Length bound = abstain rule, not interpretation: long messages
        # go to the model.
        build_now = len(text) <= 48 and bool(_CLARIFY_BUILD_NOW_RE.search(text))
        is_skip = bool(_CLARIFY_SKIP_RE.match(text))

        # Batched local-paging answers: the FE pages all questions client-side
        # and, on completion, submits every answer at once as a single silent
        # turn -> {"_clarify_answers": [{slot, value, label}, ...]}. Fold them
        # all here and go straight to the build, instead of one cursor advance
        # per question. (Agent flow only; the portfolio clarify stays one-at-a-time.)
        # NOTE: handled for BOTH the agent flow AND the portfolio/strategy
        # flow. The FE pages every question client-side and submits them in
        # one silent `_clarify_answers` batch on completion — for strategy
        # builds too (build_tool=build_strategy). This used to be gated on
        # `is_agent`, so a batched strategy clarify fell through to the
        # one-at-a-time path: the JSON blob got mis-folded into the first
        # slot, the cursor advanced, and the turn returned a "next question"
        # (clarify_advance, tools=[]) that the FE — already showing "All set,
        # building…" — never rendered. Result: the user answered everything
        # and NO card was built. Folding the whole batch here for both flows
        # sets build_now and goes straight to the builder.
        if text.startswith("{") and "_clarify_answers" in text:
            try:
                _payload = json.loads(text)
                _batch = _payload.get("_clarify_answers")
            except Exception:
                _batch = None
            if isinstance(_batch, list):
                _q_by_slot = {q.slot: q for q in questions}
                for _a in _batch:
                    if not isinstance(_a, dict):
                        continue
                    _aq = _q_by_slot.get(str(_a.get("slot") or ""))
                    _aval = str(_a.get("value") or _a.get("label") or "")
                    if _aq is None or not _aval:
                        continue
                    try:
                        if is_agent:
                            agent_slots = normalize_agent_answer_into_slots(
                                _aq.model_dump(), _aval, agent_slots,
                            )
                        else:
                            slots = normalize_answer_into_slots(_aq, _aval, slots)
                    except Exception as e:
                        logger.warning("clarify batch fold failed: %s", e)
                # Every answer is folded — skip the single-answer path and build.
                build_now = True
                current = None

        # Deterministic folding is for STRUCTURED answers only: a chip
        # click (text == an option id/label of the current question) or
        # the FE's batched JSON above. Any other free text is LANGUAGE —
        # whether it answers the question or starts a new request is the
        # model's call, not a regex's (container eval 2026-07-19: one
        # pending basket clarify consumed three unrelated new intents at
        # 0 LLM hops). Fall through — the LLM turn receives the pending
        # clarify as context via _session_state_blocks and can continue
        # the flow or handle the new intent. Capability is never reduced:
        # this bound only ever hands MORE turns to the model.
        if current is not None and not build_now and not is_skip:
            _opts = {str(o.id).strip().lower() for o in (current.options or [])}
            _opts |= {str(o.label).strip().lower() for o in (current.options or [])}
            if text.lower() not in _opts:
                trace.event("clarify.free_text_to_llm",
                            chars=len(text))
                return None

        if current is not None and not build_now and not is_skip:
            # Normalise the answer (option id/label or free text) into the slot.
            # The engine owns the parse so the slot vocabulary stays in one
            # place; an unrecognisable answer leaves the slot at its default.
            try:
                if is_agent:
                    agent_slots = normalize_agent_answer_into_slots(
                        current.model_dump(), text, agent_slots,
                    )
                else:
                    slots = normalize_answer_into_slots(current, text, slots)
                    # A free-text answer often pins MORE than the asked slot
                    # ("Around 3 lakh, 5 plus years, equities only." answers
                    # capital + horizon + assets in one line). Fold every slot
                    # the text mentions so queued questions for already-answered
                    # slots get skipped below — never re-ask what was answered.
                    slots = fold_free_text_into_slots(text, slots)
            except Exception as e:
                logger.warning("clarify answer normalise failed: %s", e)

        # Advance the cursor past the question we just handled (answered or
        # skipped). A skipped slot keeps its default + stays flagged assumed.
        next_index = index + 1 if current is not None else index

        # Skip any queued questions whose slot the free-text answer already
        # filled (portfolio flow only — agent slots fold differently). A slot
        # is "filled" when it is no longer flagged assumed (capital also needs
        # a concrete value). Without this, the multi-slot fold above would
        # populate the slot but the cursor would still stop to re-ask it.
        if not is_agent and not build_now:
            def _slot_filled(slot_name: str) -> bool:
                if slot_name == "capital_inr":
                    return (slots.capital_inr is not None
                            and not getattr(slots.assumed, "capital_inr", True))
                if slot_name in ("view", "risk", "horizon", "asset_prefs",
                                 "theme"):
                    return not getattr(slots.assumed, slot_name, True)
                return False
            while (next_index < len(questions)
                   and _slot_filled((questions[next_index].slot or "").strip())):
                trace.event("clarify.slot_prefilled",
                            slot=(questions[next_index].slot or ""))
                next_index += 1

        # Stopping rule: build when the user said so, or we've run the budget.
        if build_now or next_index >= len(questions) or not questions:
            self.store.clear_clarify(conv_id)
            if is_agent:
                # Assemble the answered slots into an enriched intent and build
                # the workflow draft directly (the legacy user_intent path of
                # the propose_workflow executor — bypasses the steps[] schema
                # gate; runs the inner planner, NOT 0-hop, which is fine for a
                # build turn). Honest-fallthrough to the LLM on failure.
                from backend.agents.tool_executor import _propose_workflow
                intent = build_agent_intent(state.request, agent_slots)
                t0 = time.monotonic()
                try:
                    result = await _propose_workflow(
                        {"user_intent": intent},
                        ctx.kite_token, ctx.db, ctx.user_id,
                    )
                except Exception as e:
                    logger.info("clarify agent build failed: %s", e)
                    return None
                breakdown["tool_propose_workflow"] = int(
                    (time.monotonic() - t0) * 1000
                )
                if not result.get("success"):
                    # Build failed (e.g. the chosen action couldn't be planned
                    # into a valid trigger). Do NOT fall through to the LLM —
                    # it would only see the bare chip id ("lot_2") with no
                    # context and reply confusingly. Reuse the same honest,
                    # error-aware clarification path as the main tool-failure
                    # fallback (_llm_clarification) instead of a canned
                    # template that says the same generic thing regardless
                    # of what actually went wrong.
                    error_text = str(result.get("error") or "")
                    trace.event("clarify.build", success=False,
                                error=error_text[:120])
                    if _is_internal_shape_error(error_text):
                        msg = _INTERNAL_SHAPE_ERROR_REPLY
                    else:
                        msg = await _llm_clarification(
                            client=self._client(),
                            user_message=message,
                            tool_name="propose_workflow",
                            error=error_text,
                            history=self.store.get_history(
                                conv_id, limit=CONV_PROMPT_WINDOW_TURNS,
                            ),
                        )
                    self.store.append(conv_id, message, msg)
                    total = int((time.monotonic() - turn_started) * 1000)
                    breakdown["total"] = total
                    _log_timing("clarify_build_agent_failed", message, total,
                                breakdown, tools=[])
                    trace.event("turn.end", total_ms=total, tools_called=[],
                                reason="clarify_build_agent_failed")
                    trace.end()
                    return ChatTurn(
                        response=msg,
                        tools_called=[],
                        raw_data={"_render_hint": "ask_user"},
                        latency_ms=total,
                        latency_breakdown=breakdown,
                    )
                payload = result.get("data") or {}
                raw_data: dict[str, Any] = {"propose_workflow": payload}
                # Stash so a follow-up amendment ("make it 2 lots") patches it.
                try:
                    self._stash_workflow_draft(
                        conv_id, payload, tool_name="propose_workflow",
                    )
                except Exception:
                    pass
                text_out = _ensure_widget_caption(
                    "", tool_name="propose_workflow",
                    logiccard=None, raw_data=raw_data,
                    user_message=message,
                )
                self.store.append(conv_id, message, text_out)
                total = int((time.monotonic() - turn_started) * 1000)
                breakdown["total"] = total
                _log_timing("clarify_build_agent", message, total, breakdown,
                            tools=["propose_workflow"])
                trace.event("turn.end", total_ms=total,
                            tools_called=["propose_workflow"],
                            reason="clarify_build_agent")
                trace.end()
                return ChatTurn(
                    response=text_out,
                    tools_called=["propose_workflow"],
                    raw_data=raw_data,
                    latency_ms=total,
                    latency_breakdown=breakdown,
                )

            guarded = await execute_with_completeness(
                "build_strategy",
                {"request": state.request, **slots.model_dump()},
                llm_client=self._client(),
                user_message=message,
                kite_token=ctx.kite_token, db=ctx.db, user_id=ctx.user_id,
            )
            breakdown[f"tool_{guarded.name}"] = guarded.latency_ms
            trace.event("clarify.build", success=guarded.success,
                        error=(guarded.error or "")[:120])
            if not guarded.success:
                return None  # honest fallthrough to the LLM recovery path
            self._note_artifact(conv_id, guarded)
            raw_data = {}
            if guarded.data:
                raw_data[guarded.name] = guarded.data
            text_out = _ensure_widget_caption(
                _tool_summary_line(guarded.name, None),
                tool_name=guarded.name, logiccard=None, raw_data=raw_data,
                user_message=message,
            )
            self.store.append(conv_id, message, text_out)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["total"] = total
            _log_timing("clarify_build", message, total, breakdown,
                        tools=[guarded.name])
            trace.event("turn.end", total_ms=total, tools_called=[guarded.name],
                        reason="clarify_build")
            trace.end()
            return ChatTurn(
                response=text_out,
                tools_called=[guarded.name],
                raw_data=raw_data,
                latency_ms=total,
                latency_breakdown=breakdown,
            )

        # More questions remain — re-surface the next one as a fresh
        # single-question clarify_card carrying the updated slot-state. Preserve
        # the kind/build_tool discriminator so the next answer still routes to
        # the right builder.
        next_q = questions[next_index]
        next_slot_state = slot_state_dump()
        self.store.set_clarify(conv_id, ClarifyState(
            request=state.request,
            slot_state=next_slot_state,
            questions=[q.model_dump() for q in questions],
            index=next_index,
            asked_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            kind=getattr(state, "kind", "portfolio"),
            build_tool=getattr(state, "build_tool", "build_strategy"),
        ))
        clarify_payload = {
            "_render_hint": "clarify_card",
            "clarify": {
                "session_slot_state": next_slot_state,
                "total": len(questions),
                "index": next_index,
                "questions": [q.model_dump() for q in questions],
            },
        }
        self.store.append(conv_id, message, next_q.prompt)
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        _log_timing("clarify_advance", message, total, breakdown, tools=[])
        trace.event("turn.end", total_ms=total, tools_called=[],
                    reason="clarify_advance", index=next_index)
        trace.end()
        return ChatTurn(
            response=next_q.prompt,
            tools_called=[],
            raw_data=clarify_payload,
            latency_ms=total,
            latency_breakdown=breakdown,
        )

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

    async def _try_register_active_draft(
        self,
        *,
        message: str,
        conv_id: str,
        ctx: "UserContext",
        trace: TurnTrace,
        turn_started: float,
        breakdown: dict[str, int],
    ) -> Optional["ChatTurn"]:
        """Track C #1: deterministic ARM of the active workflow draft.

        Fires when the user types a clean register command ("register
        it", "go ahead", "activate it", "arm it") and a workflow-shaped
        draft is cached. Drives the SAME persist+activate path the FE
        'Save & activate' button hits (via the register_workflow tool),
        then answers with a grounded armed-state readback. Zero LLM
        hops. Register-not-execute throughout."""
        if len(message) > 80 or not _REGISTER_DRAFT_RE.match(message.strip()):
            return None
        active = self.store.get_active_draft(conv_id)
        if active is None:
            return None
        # Only workflow-shaped drafts can be armed here; option/backtest
        # cards register through their own card endpoints.
        # R4/F15: the macro draft tools (propose_threshold_order,
        # propose_scheduled_order, propose_basket_allocation,
        # propose_holding_action) ALL emit a workflow_draft_card with
        # `steps` that register_workflow can persist+arm — but the prior
        # allowlist named only the two generic tools, so "register it" on
        # a NESTLEIND RSI threshold draft (the canonical confirm turn)
        # fell through to the 0-token "click Save & activate" line instead
        # of actually arming. Accept any workflow-shaped macro draft; the
        # `steps` presence check below is the real precondition.
        if active.tool_name not in _REGISTERABLE_DRAFT_TOOLS:
            return None
        draft = active.draft if isinstance(active.draft, dict) else {}
        if not isinstance(draft.get("steps"), list) or not draft["steps"]:
            return None

        from backend.services.tool_registry import execute as _registry_execute
        # When the draft is anchored to an existing agent ("Edit with chat"),
        # pass its id so register_workflow UPDATES that workflow in place
        # (replace steps/name/description, bump version) instead of creating a
        # duplicate. None anchor → unchanged create-new behaviour.
        register_args: dict[str, Any] = {
            "name": draft.get("name"),
            "description": draft.get("description"),
            "steps": draft.get("steps"),
            "expires_at": draft.get("expires_at") or draft.get("valid_until"),
        }
        target_wf_id = getattr(active, "workflow_id", None)
        if isinstance(target_wf_id, str) and target_wf_id.strip():
            register_args["workflow_id"] = target_wf_id.strip()
        result = await _registry_execute(
            "register_workflow",
            register_args,
            kite_token=ctx.kite_token, db=ctx.db, user_id=ctx.user_id,
        )
        if not result.success:
            # Defence-in-depth: never surface a raw DB/driver exception in the
            # reply even if some future error path forgets to generalise it.
            raw_err = (result.error or "unknown error")
            _low = raw_err.lower()
            if any(marker in _low for marker in (
                "psycopg2", "sqlalchemy", "integrityerror", "traceback",
                "foreignkeyviolation", "[sql:", "constraint",
            )):
                safe_err = "a temporary issue saving it on our end"
            else:
                safe_err = raw_err[:200]
            reply = (
                "I couldn't register that draft: "
                f"{safe_err} — fix the "
                "draft (or rebuild it) and tell me to register again."
            )
            self.store.append(conv_id, message, reply)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["total"] = total
            trace.event("register_draft.failed",
                        error=(result.error or "")[:120])
            trace.event("turn.end", total_ms=total,
                        tools_called=["register_workflow"],
                        reason="register_draft_failed")
            trace.end()
            return ChatTurn(
                response=reply,
                tools_called=["register_workflow"],
                latency_ms=total,
                latency_breakdown=breakdown,
            )

        data = result.data or {}
        wf_id = str(data.get("workflow_id") or "")
        if wf_id:
            try:
                self.store.set_registered_workflow_id(conv_id, wf_id)
            except Exception:  # noqa: BLE001 — stub stores in tests
                pass
        # The draft is no longer a draft — drop it (keep other parked
        # symbols' drafts intact via the named clear).
        try:
            if active.symbol:
                self.store.clear_active_draft(conv_id, symbol=active.symbol)
            else:
                self.store.clear_active_draft(conv_id)
        except TypeError:  # stub store without the symbol kwarg
            self.store.clear_active_draft(conv_id)

        trig_lines = "; ".join(
            t.get("summary", "") for t in data.get("triggers") or [] if t
        ) or "trigger armed"
        reply = (
            f"Registered and ARMED — \"{data.get('name', 'agent')}\" is "
            f"live (workflow {wf_id[:8]}…). {trig_lines}. "
            f"{data.get('on_fire', '')} "
            "This is automation of your instructions, not financial advice."
        ).strip()
        self.store.append(conv_id, message, reply)
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        _log_timing("register_draft", message, total, breakdown,
                    tools=["register_workflow"], note="deterministic-register")
        trace.event("register_draft.armed", workflow_id=wf_id)
        trace.event("turn.end", total_ms=total,
                    tools_called=["register_workflow"],
                    reason="register_draft")
        trace.end()
        return ChatTurn(
            response=reply,
            tools_called=["register_workflow"],
            raw_data={"register_workflow": data, **data},
            latency_ms=total,
            latency_breakdown=breakdown,
        )

    async def _try_workflow_status(
        self,
        *,
        message: str,
        conv_id: str,
        ctx: "UserContext",
        trace: TurnTrace,
        turn_started: float,
        breakdown: dict[str, int],
    ) -> Optional["ChatTurn"]:
        """Track C #1: grounded armed-state readback ("is it actually
        live? when do you check?") via the get_workflow_status tool —
        persisted status + real watcher cadence + current indicator
        value. Zero LLM hops on a clean match."""
        if len(message) > 160 or not _WF_STATUS_RE.search(message):
            return None

        wf_id = None
        try:
            wf_id = self.store.get_registered_workflow_id(conv_id)
        except Exception:  # noqa: BLE001 — stub stores in tests
            wf_id = None
        # Without a conversation-registered workflow, only proceed when
        # the user actually has workflows — otherwise let the LLM
        # answer (it may be about a SIP / strategy instead).
        from backend.services.tool_registry import execute as _registry_execute
        result = await _registry_execute(
            "get_workflow_status",
            ({"workflow_id": wf_id} if wf_id else {}),
            kite_token=ctx.kite_token, db=ctx.db, user_id=ctx.user_id,
        )
        if not result.success:
            return None
        data = result.data or {}
        if not data.get("workflow_id") and not wf_id:
            # No workflow at all — keep the honest "nothing armed" reply
            # only if the user seems to be asking about an agent; the
            # regex gate already established that.
            if not data.get("note"):
                return None

        if data.get("workflow_id"):
            parts = [data.get("armed_line", "")]
            for t in data.get("triggers") or []:
                line = t.get("summary", "")
                cur = t.get("current_value")
                if cur is not None:
                    met = t.get("condition_met_now")
                    line += (
                        f"; current value {cur:g} — "
                        + ("condition MET this tick" if met else "waiting")
                    )
                parts.append(line)
            if data.get("next_run_at"):
                parts.append(f"Next scheduled fire: {data['next_run_at']}.")
            parts.append(data.get("on_fire", ""))
            reply = " ".join(p for p in parts if p).strip()
        else:
            reply = str(data.get("note", "Nothing is armed yet."))

        self.store.append(conv_id, message, reply)
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        _log_timing("workflow_status", message, total, breakdown,
                    tools=["get_workflow_status"], note="deterministic-status")
        trace.event("workflow_status.readback",
                    workflow_id=str(data.get("workflow_id") or ""))
        trace.event("turn.end", total_ms=total,
                    tools_called=["get_workflow_status"],
                    reason="workflow_status")
        trace.end()
        return ChatTurn(
            response=reply,
            tools_called=["get_workflow_status"],
            raw_data={"get_workflow_status": data},
            latency_ms=total,
            latency_breakdown=breakdown,
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
        self._note_artifact(conv_id, guarded)

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
                raw_data=_clarify_raw_data(guarded),
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
            user_message=message,
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
        editor_draft: Optional[dict] = None,
    ) -> ChatTurn:
        turn_started = time.monotonic()
        # Make the conversation id ambient so tool handlers that don't take it
        # (e.g. the backtest tools) can group DSR trials by conversation.
        from backend.services.turn_context import set_conversation_id
        set_conversation_id(conv_id)
        breakdown: dict[str, int] = {}
        trace = start_turn(conv_id, message)
        trace.event("turn.start", message_preview=message[:120])

        # ── Editor-draft seed (shared contract) ────────────────────
        # When the FE has an unsaved workflow draft open in the editor,
        # it attaches the on-screen copy here. Seed it into the
        # conversation's active_draft slot so the amendment-hint path
        # computes against what the user sees — not a stale Redis copy.
        # When absent / malformed, this is a no-op and the existing
        # Redis flow runs byte-for-byte unchanged.
        if editor_draft is not None:
            self._seed_editor_draft(conv_id, editor_draft, trace)

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

        # ── Alert-ask boundary (deterministic, pre-LLM) ────────────
        # Price/condition ALERTS are not available (product decision). A
        # detected alert ask returns the boundary DIRECTLY — zero LLM hops, no
        # tool — so no notify workflow is ever built AND the model can't convert
        # the alert into an order the user didn't ask for. `_is_notify_only_alert`
        # requires a leading alert verb + a price level and NO trade verb (or an
        # explicit no-trade marker), so genuine "buy when X" automations, which
        # carry a trade verb, are unaffected.
        if _is_notify_only_alert(message):
            boundary = (
                "Price alerts aren't available yet — Pivot doesn't send alerts, "
                "pings, or “tell me when” notifications right now, so I "
                "can't watch that level for you. No order or workflow was "
                "created. If you'd want to *act* at that level instead, I can "
                "register a broker-held order (GTT) there — just say so and the "
                "quantity."
            )
            self.store.append(conv_id, message, boundary)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["alert_boundary"] = total
            breakdown["total"] = total
            _log_timing("alert_boundary", message, total, breakdown, tools=[])
            trace.event("turn.end", total_ms=total, tools_called=[])
            trace.end()
            return ChatTurn(
                response=boundary,
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

        # ── In-band clarify-answer ingestion (Workstream A) ────────
        # When a dynamic clarify_card is on screen and the user answers the
        # current question (option / free text / skip / "just build it"),
        # normalise it into the slot-state, advance the N-of-M flow, and build
        # when the stopping rule fires — all without an LLM hop.
        clarified = await self._try_resume_clarify(
            message=message, conv_id=conv_id, ctx=ctx, trace=trace,
            turn_started=turn_started, breakdown=breakdown,
        )
        if clarified is not None:
            return clarified

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

        # ── Track C #1: deterministic register / status guards ─────
        # "register it / go ahead" on an active workflow draft actually
        # ARMS it (same persist+activate path as Save & activate);
        # "is it actually live? when do you check?" gets a grounded
        # readback from the persisted workflow + scheduler facts.
        registered = await self._try_register_active_draft(
            message=message, conv_id=conv_id, ctx=ctx, trace=trace,
            turn_started=turn_started, breakdown=breakdown,
        )
        if registered is not None:
            return registered
        status_turn = await self._try_workflow_status(
            message=message, conv_id=conv_id, ctx=ctx, trace=trace,
            turn_started=turn_started, breakdown=breakdown,
        )
        if status_turn is not None:
            return status_turn

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
        # The intent that spawned the pending clarification, captured BEFORE
        # the resolution is cleared. Authoritative source for the
        # clarify-followup hint's "original request" — beats re-deriving it
        # from history (which mis-picks the first turn in a multi-intent
        # session). Empty when no resolution is pending.
        _pending_original_intent: str = ""
        if not _is_pure_affirmative(message):
            _pr = self.store.get_pending_resolution(conv_id)
            if _pr is not None and (_pr.question or _pr.options):
                pending_resolution_active = True
                _pending_original_intent = _pr.original_intent or ""
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
        # Chat-kernel round 3: a bare amendment turn ("make it 10 years")
        # matches no router rule, so the tool that served the PRIOR turn
        # falls out of scope and the model burns a find_tool hop to
        # recover it (measured on revenue_cagr_compare/1). Union the
        # prior turn's READ tools back in — mutating/drafting tools are
        # deliberately excluded (they have their own draft-followup
        # machinery, and widening order scope from a stale turn would be
        # a safety regression).
        _prior_read_tools = [
            t for t in self.store.get_last_tools(conv_id)
            if t.startswith(("get_", "query_", "compare_", "screen_"))
            or t == "calculate"
            # A just-run backtest is the actual subject of a pushback
            # turn ("-12.7% seems off, break down per-stock") — without
            # it surviving into scope, the analyse-rule's keyword match
            # ("break down") swapped the toolset for single-stock tools,
            # dropped the backtest tools entirely, and the model dead-
            # ended into an unrelated live-price lookup with no ticker
            # to resolve (reported 2026-07-14). Read-only like the
            # tools above — no draft/mutation risk from carrying it over.
            or t in ("backtest_workflow", "backtest_dsl_tree")
        ]
        if selected_names is not None and _prior_read_tools:
            selected_names = selected_names | set(_prior_read_tools)
        intent_kind = _classify_intent(message)

        # F&O amendment scope: when the active draft is an OPTION strategy
        # card and the user is AMENDING it ("increase max profit", "make it
        # safer", "switch to a call spread"), keep the turn on the options
        # surface and DROP the equity-basket builders. WHY: build_strategy
        # (equity+gold basket) is in _ALWAYS_INCLUDE and its name is
        # confusingly close to build_option_strategy, so on an option
        # amendment the planner frequently fired build_strategy and emitted a
        # stray "Diversified Equity Basket" under an options answer (~4/5 of
        # the time on gpt-5.4-mini). The amendment HINT alone (re-emit
        # build_option_strategy) didn't stop it — the wrong tool has to leave
        # scope. Mirrors the hedge-turn strip (_HEDGE_STRIP_TOOLS). Gated on a
        # DEPENDENT amendment that isn't a fresh independent intent, so a
        # genuine new "build me a portfolio" ask is unaffected.
        if selected_names is not None:
            _active_opt = self.store.get_active_draft(conv_id)
            if (_active_opt is not None
                    and _active_opt.tool_name == "build_option_strategy"
                    and _is_genuine_dependent_amendment(message)
                    and not _FRESH_BUILD_INTENT_RE.search(message)
                    and not _INDEPENDENT_INTENT_RE.search(message)):
                selected_names = (selected_names | _OPTIONS_TOOLS) - frozenset({
                    "build_strategy", "propose_basket_allocation",
                })
                trace.event(
                    "tools.option_amendment_scope",
                    dropped=["build_strategy", "propose_basket_allocation"],
                )

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
                and not _is_genuine_dependent_amendment(message)):
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
        is_construction_intent = intent_kind == "construction"
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
            "place_market_order", "place_limit_order", "place_order",
            "create_gtt_order", "create_sl_order", "create_oco_order",
            "create_dip_buy", "place_basket_order",
            "create_sip", "squareoff_all_intraday", "squareoff_symbol",
        })
        if is_construction_intent and selected_names is not None:
            # Construction scope surgery (shared helper — no drift): builder
            # + read/vet tools IN, workflow/macro/immediate-order tools OUT.
            # A construction ask structurally CANNOT render a workflow card;
            # it renders a strategy_builder_card (or ask_user_dynamic clarify).
            selected_names = _apply_construction_scope(selected_names)
        elif is_agent_intent and selected_names is not None:
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
                   "get_live_price", "get_52wk_range", "get_market_data"}
            )
        # Advisory questions in "other" intent: strip workflow macros.
        # WHY: "should I reduce that exposure?" after portfolio data was
        # calling propose_workflow. The system prompt "never attach a
        # workflow draft to an informational answer" is prose-only — LLM
        # ignores it. Remove the tools to enforce the rule structurally.
        # Exception: advisory phrasing + workflow-building keywords (e.g.
        # "should I set up an RSI strategy") keeps macros in scope.
        if (intent_kind == "other"
                and not _settings.llm_owned_interpretation
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
            "required" if (is_agent_intent or is_construction_intent) else "auto"
        )
        # A tool widget is NOT needed on every agent-flavoured turn. When the
        # message is really a QUESTION / deliberation ("what options should I
        # trade?", "should I build a dip-buy?"), relax hop-1 forcing to 'auto'
        # so the model can answer in prose or ask the one blocking question
        # (e.g. the options view) instead of being forced to emit a card.
        # Real commands stay 'required'; any specific override below wins.
        if agent_tool_choice == "required" and _is_question_shaped(message):
            agent_tool_choice = "auto"
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
        # loops back to ASK_USER ("...sound right?" forever). Force
        # backtest_workflow into scope with tool_choice=required — but ONLY
        # when the clarification IN FLIGHT is itself a backtest (see below),
        # never merely because some earlier turn ran one.
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
        # Is the clarification the user is answering RIGHT NOW a BACKTEST
        # clarification? Key off the intent that SPAWNED the question (the
        # pending resolution's original_intent, else the user turn before
        # our question) — NOT any stale backtest earlier in the window.
        # Without this scoping, answering an OPTION-strategy clarify in a
        # session that ALSO ran a backtest earlier got force-routed to
        # backtest_workflow (tool_choice=required) and re-backtested the
        # wrong thing instead of building the option strategy.
        _clarify_orig_intent = (
            _pending_original_intent or _originating_user_intent(history or [])
        )
        _is_backtest_clarify_followup = (
            (pending_resolution_active
             or (history and _looks_like_clarification_followup(history)))
            and bool(_clarify_orig_intent)
            and bool(_BACKTEST_INTENT_RE.search(_clarify_orig_intent))
        )
        if (selected_names is not None
                and not _settings.llm_owned_interpretation
                and (_is_backtest_tweak or _is_backtest_clarify_followup)):
            if _is_backtest_tweak:
                # NARROW to the backtest tools (+ ASK_USER) so the model re-runs
                # the simulation rather than fetching a live indicator or
                # drafting an agent for a verb-less tweak.
                selected_names = frozenset({
                    "backtest_workflow", "backtest_dsl_tree", "ASK_USER",
                })
            else:
                # Answering a backtest clarification — keep scope, just ensure
                # both backtest emit tools are present.
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

        if (not _settings.llm_owned_interpretation
                and (is_underspec_agent or is_filler_after_q
                     or mentions_fno or is_contradiction)):
            # Genuine clarification cases (an underspecified agent build, or a
            # buy/sell contradiction) must surface a STRUCTURED ASK_USER with
            # tappable options — NOT a free-form prose question. With the build
            # macros stripped (below), forcing tool_choice="required" leaves
            # ASK_USER as the emit path, so the question renders as a tappable
            # card AND the next-turn resolution path fires deterministically.
            # This was the "only asks on the first message" bug: tool_choice
            # was "auto" here, so the model usually wrote the question as prose
            # (no card) instead of calling ASK_USER. A FILLER reply after our
            # own question ("whatever", "you decide") stays prose-friendly so we
            # don't loop the same menu; pure F&O surfacing (mentions_fno without
            # an underspec/contradiction) also stays "auto" so the model can
            # lead with the option chain when that's the better answer.
            agent_tool_choice = (
                "required"
                if (is_underspec_agent or is_contradiction) and not is_filler_after_q
                else "auto"
            )
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
                        "place_order",
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
        _option_view_ask = _is_option_view_ask(message)
        _notify_only = _is_notify_only_alert(message)
        _at_open_close = _is_at_open_close_build(message)
        _confusion_menu = _is_confusion_after_menu(message, history)
        _unsupported_rail = _names_unsupported_rail(message)
        _hedge_followup = _is_hedge_followup(message, history)
        _hedge_request = _is_hedge_request(message) or _hedge_followup
        # R4: named option template build → force build_option_strategy,
        # remove ASK_USER from scope so the model cannot escape to it.
        if (not _settings.llm_owned_interpretation
                and _named_option_build and selected_names is not None):
            selected_names = (selected_names | _OPTIONS_TOOLS) - frozenset({
                "place_market_order", "place_limit_order", "place_order",
                "create_gtt_order", "suggest_option_strategy",
                "critique_option_strategy",
            })
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            agent_tool_choice = "required"
        # R4b: VIEW-based option ask ("bullish option strategy on NIFTY")
        # → force suggest_option_strategy the same way, remove ASK_USER so
        # the model can't escape to a hedged non-answer (reported
        # 2026-07-14: identical phrasing intermittently skipped the tool
        # call entirely under tool_choice="auto").
        elif (not _settings.llm_owned_interpretation
                and _option_view_ask and selected_names is not None):
            selected_names = (selected_names | _OPTIONS_TOOLS) - frozenset({
                "place_market_order", "place_limit_order", "place_order",
                "create_gtt_order",
            })
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            agent_tool_choice = "required"
        # H1: hedge construction → options surface in, order macros OUT so
        # a buy-the-hedged-symbols draft is structurally impossible this
        # turn. tool_choice stays auto: the directive wants prose-first
        # (explain the hedge) and the model may need to ask position size.
        elif (not _settings.llm_owned_interpretation
                and _hedge_request and selected_names is not None):
            selected_names = (
                selected_names | _OPTIONS_TOOLS
            ) - _HEDGE_STRIP_TOOLS
            if _hedge_followup:
                # Acceptance of the offered second card: force the build,
                # drop ASK_USER so it cannot re-ask position size.
                tooldefs = [
                    t for t in _registry_tools_as_tooldefs(selected_names)
                    if t.name != ASK_USER_TOOL_NAME
                ]
                agent_tool_choice = "required"
            else:
                tooldefs = _registry_tools_as_tooldefs(selected_names)
                agent_tool_choice = "auto"
            cache_key = cache_key_for(selected_names)
            trace.event("hedge_guard.scope_forced", followup=_hedge_followup)
        # R3: price/condition ALERT ask → NOT forced. Alerts aren't available
        # (the notify tools refuse); the boundary guard tells the model to state
        # the boundary in prose. No tool forcing, so tool_choice stays auto and
        # the model answers with the boundary line instead of a refused draft.
        # R2: at-open/at-close build → ensure the DSL/workflow tools are in
        # scope and force a tool so it can't downgrade to 09:30 / ASK_USER.
        elif (not _settings.llm_owned_interpretation
                and _at_open_close and selected_names is not None):
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
        if _confusion_menu and not _settings.llm_owned_interpretation:
            agent_tool_choice = "auto"
            if selected_names is not None:
                tooldefs = [
                    t for t in _registry_tools_as_tooldefs(selected_names)
                    if t.name != ASK_USER_TOOL_NAME
                ]
        elif _unsupported_rail is not None:
            agent_tool_choice = "auto"

        # 51-sweep read gates: lifecycle/portfolio/series/analyse reads
        # become STRUCTURAL — right tool in scope, tool forced, ASK_USER
        # dropped, directive pinned. After the specific guards so hedge/
        # notify/option flows keep precedence.
        _read_gate = _read_intent_gate(message, selected_names)
        if _read_gate is not None:
            selected_names, agent_tool_choice, _read_gate_directive = \
                _read_gate
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            trace.event("read_gate.scope_forced")
        else:
            _read_gate_directive = None

        # ── GAN R4 scenario routing (thematic / vague / idle / unreal) ──
        # MIRROR of handle_stream(); keep both in sync. Only fires when no
        # higher-priority specific guard already claimed the turn so it
        # never overrides a hedge / named-option / notify build.
        _scenario_routed = False
        if not (_named_option_build or _hedge_request or _notify_only
                or _at_open_close or _confusion_menu
                or _unsupported_rail is not None):
            _scn = _apply_scenario_routing(
                message, selected_names, tooldefs, cache_key
            )
            if _scn.matched:
                selected_names = _scn.selected_names
                tooldefs = _scn.tooldefs
                cache_key = _scn.cache_key
                _scenario_routed = True
                if _scn.tool_choice is not None:
                    agent_tool_choice = _scn.tool_choice
                trace.event(
                    "scenario_routing.applied",
                    thematic=detect_thematic_scenario(message) is not None,
                    tool_choice=agent_tool_choice,
                )

        # Reasoning-effort: "medium" on every turn, permanently raised from
        # "low" (2026-07-07). History: "minimal" (mapped to `none` on the
        # wire by LLMAzureOpenAI._translate_reasoning_effort) once beat "low"
        # on hit-rate/p50/tokens, but the ambiguity eval showed 0 reasoning
        # tokens on every turn and a class of clarify-priority / over-build
        # misses a bit more budget should help; "low" was the first step
        # back up. This is the next step. WATCH: p50 latency, output tokens,
        # and the JUST-DO-IT-for-reads bar — more reasoning budget biases the
        # model toward asking clarification questions rather than just
        # calling the tool.
        effort: ReasoningEffort = "medium"
        max_output: int = 1500
        # R5: per-reply-class budget. Explainer asks need 2400 tokens to
        # cover headed/bulleted depth; capability and small_talk get
        # tighter caps. The class also drives a system hint injected
        # below so the model knows the target shape, not just the size.
        reply_class = _classify_reply_class(message, intent_kind)
        # GAN R4: thematic / vague / idle / unrealistic need the full
        # structured-reply budget (table + thesis + card readback ≈
        # 300-500 words). Force the analysis class so they don't get the
        # 120-word analytical_short cap that produced the 22-89-word
        # baseline blurbs.
        if (not _settings.llm_owned_interpretation
                and (detect_thematic_scenario(message) is not None
                     or is_vague_onboarding(message)
                     or is_scared_idle_cash(message)
                     or is_unrealistic_return(message))):
            reply_class = "analysis"
        # STRATEGY budget override: a strategy/basket/portfolio build
        # (build_strategy / propose_basket_allocation) classifies as
        # intent_kind='agent' → 'draft' (1500-token cap), which strangled
        # the connection+rationale+alternatives+table reply. Route it to
        # the high-cap 'strategy' class instead. _is_strategy_framed also
        # catches the affirmative-follow-up turn ("yes, build it") that
        # carries the framing only in recent history.
        if (_is_strategy_framed(message, history)
                and not _settings.llm_owned_interpretation):
            reply_class = "strategy"
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
        # A ranked-list read just formats the tool's rows into a table — no deep
        # reasoning is needed, and on a reasoning model 'medium' effort spends
        # most of max_output on reasoning, starving (and truncating) the visible
        # table. Drop to 'minimal' — formatting rows needs no reasoning, and any
        # reasoning here just eats the budget and truncates the table.
        if reply_class == "list_read":
            effort = "minimal"
        # Same starvation on the LIGHT reply classes: a short factual /
        # capability / small-talk answer needs little planning, but on
        # gpt-5.4-mini 'medium' effort burns the whole output budget on
        # hidden reasoning — hop-probe (2026-07-13) saw a simple "who is
        # the CEO" ask emit 0 visible tokens at medium/500 while 'low'
        # produced a full answer. It also compresses good multi-fact
        # answers into terse one-liners. Drop to 'low' so the visible
        # text gets the budget. (Agent / analysis / automation classes
        # KEEP medium — their clarify-priority / build decisions need it.)
        elif reply_class in ("analytical_short", "capability", "small_talk"):
            effort = "low"
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
        # Track C #2: a NAMED back-reference ("change the INFY one")
        # promotes the matching parked draft instead of defaulting to
        # whatever was most recent.
        active = self._select_active_draft(conv_id, message, trace)
        # A fired READ gate is a fresh top-level read intent by definition
        # ("Analyze reliance." while an agent draft is active) — it must
        # evict the draft exactly like an independent prompt, or the
        # amendment hint below overrides the gate and re-emits the draft.
        if active is not None and (_is_independent_prompt(message)
                                   or _read_gate is not None):
            self.store.clear_active_draft(conv_id)
            trace.event("active_draft.evicted",
                        reason=("read_gate" if _read_gate is not None
                                else "independent_prompt"),
                        tool=active.tool_name)
            active = None

        # Question / meta-feedback turns get their own lane (2026-07-10):
        # the draft stays on screen as CONTEXT, the LLM answers in prose.
        _meta_kind = _followup_turn_kind(message) if (
            active is not None
            or (history and _looks_like_clarification_followup(history))
            # A challenge to a plain read/screen result ("isn't IGL a
            # gas company, not pharma?") has no draft and no clarify-
            # question tail to anchor on — `active`/clarification-
            # followup alone never engaged this lane for it, so the
            # message fell through to fresh tool-selection and re-ran
            # the SAME screen with zero acknowledgment (reported
            # 2026-07-14). Any turn following a tool call at all is a
            # candidate for "answer from what's already known" — the
            # regex inside `_followup_turn_kind` still has to actually
            # match for this to do anything.
            or bool(self.store.get_last_tools(conv_id))
        ) else None

        # Build the workflow-hint payload once, reused below.
        # WHY extended to all macro tools: previously only "propose_workflow"
        # was handled here, so "make it 5 shares" after a propose_threshold_order
        # draft got no amendment hint → LLM produced prose instead of re-emitting
        # the tool. Now any active macro-draft type (threshold, scheduled, etc.)
        # triggers the hint, naming the CORRECT tool to re-emit.
        workflow_hint = ""
        if (active is not None and _meta_kind is None
                and active.tool_name in _MACRO_AMENDMENT_TOOLS):
            draft_json = _safe_draft_json(active.draft)
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
                + self._parked_draft_clause(conv_id, active)
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
                and (_is_genuine_dependent_amendment(message)
                     or _is_rupee_notional_resize(message)
                     # A message that explicitly NAMES the active draft's
                     # own symbol ("activate that goldbees agent from
                     # earlier") is a stronger signal than any amendment
                     # verb — `_select_active_draft` already promoted
                     # THIS draft into the slot on that exact basis
                     # (named_backref). Without this, such a message fell
                     # through with no followup_hint/forced tool_choice,
                     # so the model classified the turn fresh off raw
                     # history and picked a different, wrong tool
                     # (reported 2026-07-14: recalling a 20-turn-old
                     # draft by name silently activated the most-recent
                     # draft instead).
                     or (active.symbol
                         and _symbol_mentioned(message, active.symbol)))
                and not _requests_comparison_over_amendment(message)):
            agent_tool_choice = "required"
            # Resize needs the live price in scope to compute shares.
            if (_is_rupee_notional_resize(message)
                    and selected_names is not None
                    and "get_live_price" not in selected_names):
                # get_market_data is the visible consolidated equivalent;
                # get_live_price stays for the hidden direct dispatch path.
                selected_names = selected_names | {"get_live_price",
                                                   "get_market_data"}
                tooldefs = _registry_tools_as_tooldefs(selected_names)
                cache_key = cache_key_for(selected_names)

        # GAN R2 R6: a confusion-after-menu turn is NOT a clarification
        # answer — the user is asking us to explain, not picking an option.
        # Suppress the tool-forcing followup hint and pin tool_choice=auto
        # so the TEACH guard governs (no re-dumped menu, no forced emit).
        if _confusion_menu:
            agent_tool_choice = "auto"

        if _meta_kind is not None:
            # META lane — a question about the draft/system or feedback
            # about how we acted. Answer conversationally; never re-emit.
            followup_hint = _meta_turn_hint(_meta_kind, active, message)
            agent_tool_choice = "auto"
            if _meta_kind == "question":
                # ASK_USER out of scope: with it available the model
                # parroted the user's own question back via ask_user
                # (live repro). Feedback turns KEEP it — asking for the
                # missing parameter is the right move there.
                tooldefs = [t for t in tooldefs
                            if t.name != ASK_USER_TOOL_NAME]
            trace.event("followup.meta_lane", kind=_meta_kind,
                        has_draft=active is not None)
        elif (history and _looks_like_clarification_followup(history)
                and not _confusion_menu):
            # CLARIFICATION-FOLLOWUP path — the user is answering a
            # question we asked. Carry the original intent forward.
            last_assistant = next(
                (h for h in reversed(history)
                 if isinstance(h, dict) and h.get("role") == "assistant"),
                None,
            )
            last_text = (last_assistant or {}).get("content") or ""
            # Original ask = the intent that SPAWNED this clarification, NOT
            # the first user turn in the window. Prefer the persisted
            # PendingResolution.original_intent; fall back to the user turn
            # just before the assistant's question. Binding to first-in-window
            # cross-contaminated multi-intent sessions (answering an
            # option-strategy clarify rebuilt/backtested an earlier basket).
            original_intent = (
                _pending_original_intent
                or _originating_user_intent(history)
            )
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
        elif (active is not None and workflow_hint
                and (_is_genuine_dependent_amendment(message)
                     or _is_rupee_notional_resize(message)
                     # A message that explicitly NAMES the active draft's
                     # own symbol ("activate that goldbees agent from
                     # earlier") is a stronger signal than any amendment
                     # verb — `_select_active_draft` already promoted
                     # THIS draft into the slot on that exact basis
                     # (named_backref). Without this, such a message fell
                     # through with no followup_hint/forced tool_choice,
                     # so the model classified the turn fresh off raw
                     # history and picked a different, wrong tool
                     # (reported 2026-07-14: recalling a 20-turn-old
                     # draft by name silently activated the most-recent
                     # draft instead).
                     or (active.symbol
                         and _symbol_mentioned(message, active.symbol)))
                and not _requests_comparison_over_amendment(message)):
            # AMENDMENT path — the prior turn wasn't a clarification but
            # a macro draft is on screen and the user is mutating it.
            # WHY: LLM defaulted to text "do you want me to place…?"
            # instead of re-emitting the tool. The hint + required
            # tool_choice (set above) together fix this.
            # Gated on the SAME confidence check as the tool_choice force
            # above (not just "not meta/question") — see
            # `_is_genuine_dependent_amendment`'s docstring: this hint
            # unconditionally telling the model "treat as AMENDMENT, do
            # NOT write prose" for any non-meta-classified turn was the
            # root cause of stale-draft re-firing on generic/ambiguous
            # follow-ups (e.g. a finance-education question with no "?"
            # got answered with a verbatim re-emit of an unrelated
            # automation draft). A turn that fails this bar falls through
            # with no followup_hint — the draft stays as ambient context,
            # tool_choice stays whatever it already was, and the model
            # classifies the turn fresh instead of being told the answer.
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
        # Intent packs: system_core.md is always loaded; the router injects
        # the domain mechanics (options/backtest/baskets/…) only on turns
        # that need them. Placed right after the cached core so mechanics
        # sit high, before per-turn user/reply-class context.
        _summary_block = _summary_bridge_block(
            conv_id, getattr(ctx, "user_id", 0), history_override,
        )
        if _summary_block:
            base_messages_summary = LLMMessage(
                role="system", content=_summary_block,
            )
        else:
            base_messages_summary = None
        for _st_block in _session_state_blocks(self.store, conv_id):
            base_messages.append(LLMMessage(role="system", content=_st_block))
        _mod_block = _prompt_module_block(message, history)
        if _mod_block:
            base_messages.append(LLMMessage(role="system", content=_mod_block))
        if base_messages_summary is not None:
            base_messages.append(base_messages_summary)
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
        if _read_gate_directive:
            base_messages.append(
                LLMMessage(role="system", content=_read_gate_directive))
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
        # Chat-kernel Phase A3 (2026-07-10): bare amendment of a READ
        # answer — "make it 10 years" after a revenue-CAGR reply. There
        # is NO active draft, so the draft-amendment machinery must not
        # engage; without a hint the model either burned a find_tool hop
        # or asked "which draft/workflow?" (both live-observed). Tell it
        # plainly: re-run the prior read with the changed parameter.
        elif (active is None
                and _prior_read_tools
                and _DEPENDENT_INTENT_RE.search(message)
                and not _is_question_shaped(message)):
            base_messages.append(LLMMessage(
                role="system",
                content=(
                    "## Amendment of the previous ANSWER (no draft exists)\n"
                    f"The previous turn answered using "
                    f"{', '.join(_prior_read_tools[:3])}. The user's "
                    "current message changes ONE parameter of that same "
                    "question (a period, a symbol, a threshold). Re-call "
                    "the SAME tool with the amended parameter and answer "
                    "directly. There is NO workflow or draft to amend — "
                    "do NOT ask which one."
                ),
            ))
            agent_tool_choice = "required"

        # WHY this directive: when we stripped macro tools because the
        # request is underspec / filler, the model would fall back to
        # describing a "draft" in plain prose ("Name: ... Trigger: ...
        # Action: ..."). That's worse than fabricating a real card —
        # the user sees agent-shaped text but no Activate button, no
        # editable fields, and no commitment surface. This hard
        # directive tells the model: in this state, ASK_USER is the
        # ONLY correct action.
        if ((is_underspec_agent or is_filler_after_q) and not _scenario_routed
                and not _settings.llm_owned_interpretation):
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
        # Turn-level screen-call counter: the deterministic table reply is
        # only valid when ONE screen was the whole ask — multiple screens
        # mean the model is gathering inputs for a synthesis it must write.
        screen_calls_this_turn = 0
        # presentation='analysis' (model-chosen on screen_fundamentals): the
        # model owns the WHOLE reply — tables included (instructed to quote
        # tool values verbatim); the deterministic render never fires.
        screen_analysis_mode = False
        # M1: When the LLM writes a free-form question (assistant text
        # ending with "?" / "do you want" / etc.) WITHOUT calling
        # ASK_USER, the chat layer pushes a "USE ASK_USER" directive
        # and forces one more hop. Flag prevents infinite recursion.
        ask_user_retry_used = False
        # When a read tool ran but the model returned EMPTY prose (it
        # deferred to a non-existent card, or reasoning ate the budget),
        # re-prompt ONCE with tools OFF to force a real answer instead of
        # shipping a canned "see the card below" stub. Flag prevents loops.
        empty_narration_reprompt_used = False
        _force_no_tools = False
        # A CONSTRUCTION ask must actually build the basket — a read tool
        # left in scope for grounding (screen_fundamentals) also satisfies
        # hop-1's tool_choice=required, so the model can call it and stop
        # (live repro 2026-07-15, see the mirrored gate in `handle_stream`).
        # Force exactly ONE more hop, scoped to build_strategy/
        # ask_user_dynamic, before accepting prose as final.
        construction_retry_used = False
        _force_construction_tools = False
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
            hop_tool_choice: Literal["auto", "required", "none"] = (
                "none" if _force_no_tools
                else "required" if _force_construction_tools
                else (agent_tool_choice if hop_index == 1 else "auto")
            )
            _force_no_tools = False
            _force_construction_tools = False
            # Compact-draft hop budget: when we just emitted a macro
            # draft tool, the next prose hop only needs ~50 words.
            hop_max_output = (
                _COMPACT_POST_MACRO_MAX_OUTPUT
                if (_COMPACT_DRAFTS and last_was_macro_draft)
                else max_output
            )
            # On the forced-no-tools reprompt (empty-narration recovery),
            # the model has one job: WRITE prose. Reasoning here just eats
            # the budget and re-produces the empty output we're recovering
            # from — so drop effort to 'minimal' and guarantee headroom.
            hop_effort: ReasoningEffort = effort
            if hop_tool_choice == "none":
                hop_effort = "minimal"
                hop_max_output = max(hop_max_output, 1500)
            trace.event("llm.call", hop=hop_index, reasoning_effort=hop_effort,
                        tools_offered=len(tooldefs),
                        tool_choice=hop_tool_choice,
                        max_output_tokens=hop_max_output,
                        compact_post_macro=(_COMPACT_DRAFTS and last_was_macro_draft))
            try:
                # Release the pooled DB connection for the LLM wait — see
                # _release_db_conn. The session re-acquires on its next query.
                _release_db_conn(ctx.db)
                response = await client.complete(
                    messages=messages,
                    tools=tooldefs,
                    tool_choice=hop_tool_choice,
                    max_output_tokens=hop_max_output,
                    reasoning_effort=hop_effort,
                    temperature=0.2,
                    prompt_cache_key=cache_key,
                    hosted_tools=_hosted_tools_for(message),
                )
            except Exception as e:
                # GAN R4 F11: ONE short-backoff retry on a transient
                # first-hop failure before degrading — a single 50s
                # timeout was wiping context turns. Only retry the FIRST
                # hop (later hops carry tool state that's costly to redo).
                if hop_index == 1:
                    logger.warning(
                        "%s call failed at hop %d (%s); retrying once",
                        client.provider_name, hop_index, type(e).__name__,
                    )
                    trace.event("llm.retry", hop=hop_index,
                                type=type(e).__name__)
                    try:
                        await asyncio.sleep(0.5)
                        _release_db_conn(ctx.db)
                        response = await client.complete(
                            messages=messages,
                            tools=tooldefs,
                            tool_choice=hop_tool_choice,
                            max_output_tokens=hop_max_output,
                            reasoning_effort=effort,
                            temperature=0.2,
                            prompt_cache_key=cache_key,
                        )
                    except Exception as e2:  # noqa: BLE001
                        logger.warning(
                            "%s retry failed at hop %d (%s); falling back",
                            client.provider_name, hop_index, type(e2).__name__,
                        )
                        trace.event("llm.exception", hop=hop_index,
                                    type=type(e2).__name__)
                        break
                else:
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
                return self._unavailable(turn_started, breakdown, message)

            if response.finish_reason != "tool_calls":
                # Final text — return it.
                text, sanitised = _post_process(response.content or "")
                # CONSTRUCTION ask that finalised without ever building the
                # basket — see the `construction_retry_used` init comment
                # above for the WHY. A read tool alone (screen_fundamentals,
                # query_financials, etc.) is grounding input, never the
                # answer, to a build/create-a-strategy ask.
                if (
                    is_construction_intent and not construction_retry_used
                    and not any(
                        t in ("build_strategy", "ask_user_dynamic")
                        for t in tools_called
                    )
                ):
                    construction_retry_used = True
                    _force_construction_tools = True
                    tooldefs = _registry_tools_as_tooldefs(
                        frozenset({"build_strategy", "ask_user_dynamic"})
                    )
                    trace.event("construction.retry_forced",
                                tools_so_far=tools_called)
                    messages.append(LLMMessage(role="assistant", content=text))
                    messages.append(LLMMessage(
                        role="system",
                        content=(
                            "## FINISH THE STRATEGY BUILD\n"
                            "This is a CONSTRUCTION ask (build/own a basket "
                            "now). Any read tool you just called (e.g. a "
                            "sector screen) is an INPUT to the basket, not "
                            "the answer — the turn is not done. Call "
                            "`build_strategy` now, using the real names from "
                            "what you just fetched, or `ask_user_dynamic` "
                            "if a genuinely blocking detail is missing. Do "
                            "NOT present the screen/table itself as the "
                            "final answer."
                        ),
                    ))
                    continue
                # Empty prose after a read tool → the model deferred to a
                # (non-existent) card or reasoning ate the budget. Re-prompt
                # ONCE with tools OFF to force a real answer — a canned "see
                # the card below" line is a lie (news/movers render no card)
                # and reads as broken.
                _empty_prose = (
                    not (response.content or "").strip()
                    or (sanitised and text == _GENERIC_FALLBACK)
                )
                _emitted_card = any(
                    isinstance(v, dict) and v.get("_render_hint")
                    for v in (raw_data or {}).values()
                ) or bool(raw_data.get("_render_hint"))
                if (
                    _empty_prose and tools_called and not _emitted_card
                    and not empty_narration_reprompt_used
                ):
                    empty_narration_reprompt_used = True
                    _force_no_tools = True
                    trace.event("empty_narration.reprompt",
                                tools=tools_called)
                    messages.append(LLMMessage(
                        role="system",
                        content=(
                            "## WRITE THE ANSWER NOW\n"
                            "You called tools and their results are in the "
                            "conversation above, but you returned an empty "
                            "message. Write the user-facing answer in prose "
                            "NOW, using those results plus your own "
                            "knowledge. Do NOT call any more tools. Do NOT "
                            "defer to a card — there is no card for this "
                            "answer. Give a substantive, useful, data-rich "
                            "reply."
                        ),
                    ))
                    continue
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
                    user_message=message,
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

            # Hop-scoped flags. When a hop drafts a card with no error and no
            # find_tool lazy-load, the FE already has everything to render —
            # the narration hop (a full ~33k-token re-prefill just to restate
            # the card) is pure waste and we finalize deterministically below.
            hop_drafted_card = False
            hop_error = False
            hop_find_tool = False
            # Screen turns: the ranked rows ARE the reply — rendered
            # deterministically below (render_screen_markdown), skipping the
            # narration hop (measured ~7s warm / the whole cold-cache tail).
            hop_screen_data: Optional[dict] = None

            for tc in response.tool_calls or []:
                trace.event("tool.invoke", tool=tc.get("name"),
                            args=tc.get("arguments"))
                if tc.get("name") == "screen_fundamentals":
                    screen_calls_this_turn += 1
                    if (tc.get("arguments") or {}).get("presentation") == "analysis":
                        screen_analysis_mode = True
                # H1: only ONE strategy card renders per turn — a second
                # build_option_strategy would silently overwrite the first
                # card (observed live on two-name hedge asks). Reject it
                # as a tool error so the model offers the second build as
                # a follow-up instead of shipping an incoherent turn.
                if (
                    tc.get("name") == "build_option_strategy"
                    and "build_option_strategy" in tools_called
                ):
                    messages.append(LLMMessage(
                        role="tool",
                        tool_call_id=tc.get("id", f"call_{hop_index}"),
                        name="build_option_strategy",
                        content=(
                            "REJECTED: only one strategy card renders per "
                            "turn and the first build is already on "
                            "screen. Describe the card that was built and "
                            "OFFER to build this one next turn ('say the "
                            "word and I'll build the same for <name>')."
                        ),
                    ))
                    trace.event("tool.rejected_duplicate",
                                tool="build_option_strategy")
                    continue
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
                # Session artifact ledger — hooked HERE (right after
                # execution) so TERMINAL card tools are recorded too;
                # the tool_msg path below only runs when the loop takes
                # another LLM hop, which card turns never do.
                self._note_artifact(conv_id, guarded)

                # Completeness or ASK_USER → surface immediately.
                # Persist the partial tool call so the user's next
                # reply can resume deterministically (Change 2).
                if guarded.needs_clarification and guarded.question:
                    self.store.append(conv_id, message, guarded.question)
                    self._maybe_set_pending(conv_id, guarded)
                    self._maybe_set_pending_resolution(
                        conv_id, message, guarded,
                    )
                    # Workstream A: when this is a dynamic clarify_card, persist
                    # the in-band slot-state + question list so the next answer
                    # advances the N-of-M flow without re-running the generator.
                    self._maybe_set_clarify_state(conv_id, message, guarded)
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
                        raw_data=_clarify_raw_data(guarded),
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
                    if (guarded.name == "screen_fundamentals"
                            and screen_analysis_mode
                            and guarded.data and guarded.data.get("results")):
                        tool_msg_content += (
                            "\n\n[presentation=analysis: NO table is "
                            "auto-rendered — your reply must include the "
                            "ranked results as a markdown table, quoting "
                            "these tool values VERBATIM (never round, "
                            "reorder, or invent), followed by your "
                            "analysis in YOUR OWN structured form: "
                            "open with one '## <specific title>' heading "
                            "that names THIS answer, then "
                            "markdown ## section headings (e.g. what "
                            "stands out / caveats / view — pick headings "
                            "that fit THIS answer), bold key numbers, "
                            "bullets where they help. Never a wall of "
                            "plain paragraphs. Include the FULL ranked "
                            "table ONLY when the user asked for a screen/"
                            "list ('screen me…', 'show me companies with "
                            "X'). For an analyze/research/suggest ask, do "
                            "NOT dump the whole screen — table only the "
                            "shortlisted names your analysis actually "
                            "discusses; the screen is your working "
                            "material, not the deliverable. If the user named "
                            "a constraint you could NOT express as a "
                            "filter (e.g. stability/consistency over "
                            "time), say so explicitly and verify it "
                            "yourself for the shortlisted names (e.g. "
                            "query_financials history) before ranking "
                            "them.]"
                        )
                    messages.append(LLMMessage(
                        role="tool",
                        tool_call_id=tc.get("id", f"call_{hop_index}"),
                        name=guarded.name,
                        content=tool_msg_content,
                    ))
                    if guarded.name not in tools_called:
                        tools_called.append(guarded.name)
                        # Round 3: persist for the next turn's scope
                        # union (bare-amendment recovery, both paths).
                        self.store.set_last_tools(conv_id, tools_called)
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
                    # This tool rendered its OWN card (a GTT/SL/OCO/SIP/
                    # squareoff order) and never touches the active_draft
                    # slot on success — so a PRIOR unrelated draft (e.g. a
                    # propose_workflow card from earlier in the same
                    # conversation) is left stale in the slot. The next
                    # follow-up's generic amendment classifier ("change
                    # the number of shares to 7") has no symbol-anchor
                    # requirement, so it matches the stale draft and
                    # re-fires the WRONG tool (reported 2026-07-14: a GTT
                    # edit also re-firing propose_workflow). Evict it here,
                    # keyed on which tool just actually ran — not on
                    # message wording, so this isn't another keyword gate.
                    elif guarded.name in _ORDER_AND_MACRO_TOOLS:
                        self.store.clear_active_draft(conv_id)
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
                    # A workflow/order DRAFT card this hop → the FE renders it
                    # in full from raw_data; no narration hop needed. (Option
                    # cards and analytics stay on the prose hop — their tables
                    # / defended view / interpretation add real value.)
                    if guarded.name in _STASH_DRAFT_TOOLS:
                        hop_drafted_card = True
                    # A successful screen with rows → deterministic table
                    # reply below (same values verbatim), no narration hop.
                    # EXCEPT sector-OUTLOOK asks (51-sweep): the user asked
                    # for a VIEW — a bare ranked table is an invalid answer,
                    # so those turns keep the LLM narration hop (the ANALYSIS
                    # directive mandates the ## View section).
                    if (guarded.name == "screen_fundamentals"
                            and guarded.data and guarded.data.get("results")
                            and not screen_analysis_mode):
                        hop_screen_data = guarded.data
                    if guarded.name == "find_tool":
                        hop_find_tool = True
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
                hop_error = True
                last_tool_error = f"{guarded.name}: {guarded.error}"
                # L12: when the tool error names a specific replacement
                # tool ("use propose_holding_action instead"), force
                # one retry hop with the named tool required. This
                # bridges the DSL early-bail → propose_holding_action
                # gap that the L08_21 / L10_01 trailing-SL probes
                # surfaced.
                target_tool = _redirect_target_for_failure(
                    guarded.name, guarded.error or "", message,
                    structured=getattr(guarded, "redirect_to", None),
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
                # prompt-aware clarification (vs. a hardcoded template) —
                # EXCEPT an internal shape bug, which gets an honest
                # deterministic reply instead of a fabricated ambiguity.
                if question == _LLM_CLARIFY_SENTINEL:
                    if _is_internal_shape_error(guarded.error or ""):
                        question = _INTERNAL_SHAPE_ERROR_REPLY
                    else:
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

            # A card was fully drafted this hop with no error / no lazy-load —
            # skip the narration hop entirely. The card carries every value;
            # a deterministic, data-rich caption (synthesised from the steps)
            # replaces the ~33k-token narration round-trip. Biggest single
            # latency win on an agent-build turn.
            if hop_drafted_card and not hop_error and not hop_find_tool:
                primary = next(
                    (t for t in reversed(tools_called) if t in _STASH_DRAFT_TOOLS),
                    "propose_workflow",
                )
                text_out = _ensure_widget_caption(
                    "", tool_name=primary, logiccard=logiccard, raw_data=raw_data,
                    user_message=message,
                )
                self.store.append(conv_id, message, text_out)
                self.store.clear_pending(conv_id)
                total = int((time.monotonic() - turn_started) * 1000)
                breakdown["total"] = total
                breakdown["narration_hop_skipped"] = 1
                _log_timing(client.provider_name, message, total, breakdown,
                            tools=tools_called, note="draft_card_no_narration")
                trace.event("turn.end", total_ms=total, tools_called=tools_called,
                            reason="draft_card_no_narration")
                trace.end()
                return ChatTurn(
                    response=text_out,
                    tools_called=tools_called,
                    logiccard=logiccard,
                    latency_ms=total,
                    raw_data=raw_data,
                    latency_breakdown=breakdown,
                )

            # Screen turn finalized deterministically — the ranked rows are
            # the reply, rendered verbatim (render_screen_markdown), so the
            # narration hop (whose only job was restating them as a table)
            # is skipped. Gated to single-tool turns so a multi-tool turn's
            # extra context is never silently dropped. NEVER on a
            # CONSTRUCTION ask — a screen is grounding input to the basket,
            # not the answer, and this shortcut returns before the model
            # ever gets a hop to continue to `build_strategy` (live repro
            # 2026-07-15: this exact branch was the actual mechanism behind
            # "build a strategy that gets affected positively by big oil
            # moves" terminating on a bare screener table — the
            # `construction_retry_used` gate below never even runs because
            # this shortcut returns first).
            if (hop_screen_data is not None and not hop_error
                    and not hop_find_tool
                    and not is_construction_intent
                    and tools_called == ["screen_fundamentals"]
                    # tools_called is DEDUPED — three parallel screens still
                    # read as one entry. Multiple screens = ingredients for a
                    # synthesis (e.g. "who wins if the monsoon fails"); the
                    # model keeps its narration hop (live repro 2026-07-17:
                    # this branch swallowed a 3-screen thematic ask and the
                    # user got one bare FMCG table instead of an answer).
                    and screen_calls_this_turn == 1):
                from backend.services.fundamentals_screen import (
                    render_screen_markdown,
                )
                text_out = render_screen_markdown(hop_screen_data) or ""
                if text_out:
                    self.store.append(conv_id, message, text_out)
                    self.store.clear_pending(conv_id)
                    total = int((time.monotonic() - turn_started) * 1000)
                    breakdown["total"] = total
                    breakdown["narration_hop_skipped"] = 1
                    _log_timing(client.provider_name, message, total,
                                breakdown, tools=tools_called,
                                note="screen_table_no_narration")
                    trace.event("turn.end", total_ms=total,
                                tools_called=tools_called,
                                reason="screen_table_no_narration")
                    trace.end()
                    return ChatTurn(
                        response=text_out,
                        tools_called=tools_called,
                        logiccard=logiccard,
                        latency_ms=total,
                        raw_data=raw_data,
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
        editor_draft: Optional[dict] = None,
    ) -> AsyncIterator[dict]:
        from backend.llm.openai_client import LLMOpenAI, stream_openai
        from backend.services.turn_context import set_conversation_id

        turn_started = time.monotonic()
        set_conversation_id(conv_id)
        breakdown: dict[str, int] = {}
        trace = start_turn(conv_id, message)
        trace.event("turn.start.stream", message_preview=message[:120])

        # ── Editor-draft seed (shared contract) ────────────────────
        # Mirror of handle() — see _seed_editor_draft for the rationale.
        # When the FE has an unsaved workflow draft open and attaches
        # it here, base any amendment against the on-screen copy. When
        # absent / malformed, this is a no-op and the existing Redis
        # active_draft flow stays byte-for-byte unchanged.
        if editor_draft is not None:
            self._seed_editor_draft(conv_id, editor_draft, trace)

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

        # ── Alert-ask boundary (deterministic, pre-LLM) ────────────
        # Mirror of the handle() short-circuit: price/condition alerts aren't
        # available, so a detected alert ask streams the boundary directly.
        if _is_notify_only_alert(message):
            boundary = (
                "Price alerts aren't available yet — Pivot doesn't send alerts, "
                "pings, or “tell me when” notifications right now, so I "
                "can't watch that level for you. No order or workflow was "
                "created. If you'd want to *act* at that level instead, I can "
                "register a broker-held order (GTT) there — just say so and the "
                "quantity."
            )
            self.store.append(conv_id, message, boundary)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["alert_boundary"] = total
            breakdown["total"] = total
            yield {"type": "delta", "text": boundary}
            yield {
                "type": "done",
                "response": boundary,
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

        # ── Clarify-card resume (streaming) ────────────────────────
        # CRITICAL: handle() resumes a clarify answer via _try_resume_clarify,
        # but the streaming path historically did NOT — so on the SSE surface
        # the FE actually uses, a clarify answer fell through to the full LLM
        # loop (the deterministic 0-hop advance / build never ran). Wire it
        # here, mirroring the fast-resume conversion. The streaming `done`
        # ships raw_data verbatim (no router hoist), and the FE reads
        # _render_hint at the TOP level — so hoist a nested widget payload
        # (the agent build's {propose_workflow: draft}) to the top, while a
        # clarify-advance card (already top-level _render_hint) ships as-is.
        clarified_turn = await self._try_resume_clarify(
            message=message, conv_id=conv_id, ctx=ctx, trace=trace,
            turn_started=turn_started, breakdown=breakdown,
        )
        if clarified_turn is not None:
            rd = clarified_turn.raw_data or {}
            if rd and not rd.get("_render_hint"):
                for _v in rd.values():
                    if isinstance(_v, dict) and _v.get("_render_hint"):
                        rd = {**rd, **_v}
                        break
            yield {"type": "start"}
            for tname in clarified_turn.tools_called:
                yield {"type": "tool_start", "name": tname}
                yield {"type": "tool_done", "name": tname, "ok": True}
            if clarified_turn.response:
                yield {"type": "delta", "text": clarified_turn.response}
            yield {
                "type": "done",
                "response": clarified_turn.response,
                "tools_called": clarified_turn.tools_called,
                "logiccard": clarified_turn.logiccard,
                "raw_data": rd or None,
                "latency_ms": clarified_turn.latency_ms,
                "latency_breakdown": clarified_turn.latency_breakdown,
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

        # ── Track C guards (streaming mirror of handle()) ───────────
        # register-it / is-it-live — deterministic turns converted to
        # the SSE event sequence the FE expects.
        _guard_turn: Optional[ChatTurn] = await self._try_register_active_draft(
            message=message, conv_id=conv_id, ctx=ctx, trace=trace,
            turn_started=turn_started, breakdown=breakdown,
        )
        if _guard_turn is None:
            _guard_turn = await self._try_workflow_status(
                message=message, conv_id=conv_id, ctx=ctx, trace=trace,
                turn_started=turn_started, breakdown=breakdown,
            )
        if _guard_turn is not None:
            yield {"type": "start"}
            for tname in _guard_turn.tools_called:
                yield {"type": "tool_start", "name": tname}
                yield {"type": "tool_done", "name": tname, "ok": True}
            if _guard_turn.response:
                yield {"type": "delta", "text": _guard_turn.response}
            # Streaming path ships raw_data verbatim — hoist draft /
            # status fields top-level the same way the skeleton path does.
            _stream_raw = None
            if _guard_turn.raw_data:
                _stream_raw = dict(_guard_turn.raw_data)
                inner = _stream_raw.get("propose_workflow")
                if isinstance(inner, dict):
                    _stream_raw = {
                        **inner, **_stream_raw,
                        "_render_hint": "workflow_draft_card",
                    }
            yield {
                "type": "done",
                "response": _guard_turn.response,
                "tools_called": _guard_turn.tools_called,
                "logiccard": _guard_turn.logiccard,
                "raw_data": _stream_raw,
                "latency_ms": _guard_turn.latency_ms,
                "latency_breakdown": _guard_turn.latency_breakdown,
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
        # Streaming mirror: capture the pending clarification's originating
        # intent BEFORE it's cleared (line below), for the clarify-followup
        # hint. See handle() for the multi-intent rationale.
        _pending_original_intent: str = ""
        if not _is_pure_affirmative(message):
            _pr = self.store.get_pending_resolution(conv_id)
            if _pr is not None and (_pr.question or _pr.options):
                pending_resolution_active = True
                _pending_original_intent = _pr.original_intent or ""
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
            # editor_draft has already been seeded above; don't re-seed
            # in handle() — pass None so the inner call is a no-op on
            # the seed path. The active_draft slot already holds the
            # editor copy.
            turn = await self.handle(
                message, conv_id, ctx,
                history_override=history_override,
                mode_override=mode_override,
                editor_draft=None,
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
        # Chat-kernel round 3: a bare amendment turn ("make it 10 years")
        # matches no router rule, so the tool that served the PRIOR turn
        # falls out of scope and the model burns a find_tool hop to
        # recover it (measured on revenue_cagr_compare/1). Union the
        # prior turn's READ tools back in — mutating/drafting tools are
        # deliberately excluded (they have their own draft-followup
        # machinery, and widening order scope from a stale turn would be
        # a safety regression).
        _prior_read_tools = [
            t for t in self.store.get_last_tools(conv_id)
            if t.startswith(("get_", "query_", "compare_", "screen_"))
            or t == "calculate"
            # A just-run backtest is the actual subject of a pushback
            # turn ("-12.7% seems off, break down per-stock") — without
            # it surviving into scope, the analyse-rule's keyword match
            # ("break down") swapped the toolset for single-stock tools,
            # dropped the backtest tools entirely, and the model dead-
            # ended into an unrelated live-price lookup with no ticker
            # to resolve (reported 2026-07-14). Read-only like the
            # tools above — no draft/mutation risk from carrying it over.
            or t in ("backtest_workflow", "backtest_dsl_tree")
        ]
        if selected_names is not None and _prior_read_tools:
            selected_names = selected_names | set(_prior_read_tools)
        intent_kind = _classify_intent(message)

        # F&O amendment scope: when the active draft is an OPTION strategy
        # card and the user is AMENDING it ("increase max profit", "make it
        # safer", "switch to a call spread"), keep the turn on the options
        # surface and DROP the equity-basket builders. WHY: build_strategy
        # (equity+gold basket) is in _ALWAYS_INCLUDE and its name is
        # confusingly close to build_option_strategy, so on an option
        # amendment the planner frequently fired build_strategy and emitted a
        # stray "Diversified Equity Basket" under an options answer (~4/5 of
        # the time on gpt-5.4-mini). The amendment HINT alone (re-emit
        # build_option_strategy) didn't stop it — the wrong tool has to leave
        # scope. Mirrors the hedge-turn strip (_HEDGE_STRIP_TOOLS). Gated on a
        # DEPENDENT amendment that isn't a fresh independent intent, so a
        # genuine new "build me a portfolio" ask is unaffected.
        if selected_names is not None:
            _active_opt = self.store.get_active_draft(conv_id)
            if (_active_opt is not None
                    and _active_opt.tool_name == "build_option_strategy"
                    and _is_genuine_dependent_amendment(message)
                    and not _FRESH_BUILD_INTENT_RE.search(message)
                    and not _INDEPENDENT_INTENT_RE.search(message)):
                selected_names = (selected_names | _OPTIONS_TOOLS) - frozenset({
                    "build_strategy", "propose_basket_allocation",
                })
                trace.event(
                    "tools.option_amendment_scope",
                    dropped=["build_strategy", "propose_basket_allocation"],
                )

        # Typo-continuation guard (mirror of non-streaming path).
        # See _is_bare_typo_continuation for full rationale.
        if (had_active_draft_at_entry
                and selected_names is not None
                and _is_bare_typo_continuation(message)
                and not _is_genuine_dependent_amendment(message)):
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
        is_construction_intent = intent_kind == "construction"
        # Streaming mirror of the non-streaming intent routing.
        # See handle() for the full rationale.
        _IMMEDIATE_ORDER_TOOLS = frozenset({
            "place_market_order", "place_limit_order", "place_order",
            "create_gtt_order", "create_sl_order", "create_oco_order",
            "create_dip_buy", "place_basket_order",
            "create_sip", "squareoff_all_intraday", "squareoff_symbol",
        })
        if is_construction_intent and selected_names is not None:
            # Construction scope surgery (shared helper — no drift): builder
            # + read/vet tools IN, workflow/macro/immediate-order tools OUT.
            # A construction ask structurally CANNOT render a workflow card;
            # it renders a strategy_builder_card (or ask_user_dynamic clarify).
            selected_names = _apply_construction_scope(selected_names)
        elif is_agent_intent and selected_names is not None:
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
                   "get_live_price", "get_52wk_range", "get_market_data"}
            )
        # Mirror of non-streaming advisory-strip — see handle() for WHY.
        if (intent_kind == "other"
                and not _settings.llm_owned_interpretation
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
            "required" if (is_agent_intent or is_construction_intent) else "auto"
        )
        # Streaming mirror: relax forcing for question/deliberation-shaped
        # agent turns so the model can discuss/ask instead of force-building
        # a card (see handle() for rationale).
        if agent_tool_choice == "required" and _is_question_shaped(message):
            agent_tool_choice = "auto"
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
        if (not _settings.llm_owned_interpretation
                and (is_underspec_agent or is_filler_after_q
                     or mentions_fno or is_contradiction)):
            # Genuine clarification cases (an underspecified agent build, or a
            # buy/sell contradiction) must surface a STRUCTURED ASK_USER with
            # tappable options — NOT a free-form prose question. With the build
            # macros stripped (below), forcing tool_choice="required" leaves
            # ASK_USER as the emit path, so the question renders as a tappable
            # card AND the next-turn resolution path fires deterministically.
            # This was the "only asks on the first message" bug: tool_choice
            # was "auto" here, so the model usually wrote the question as prose
            # (no card) instead of calling ASK_USER. A FILLER reply after our
            # own question ("whatever", "you decide") stays prose-friendly so we
            # don't loop the same menu; pure F&O surfacing (mentions_fno without
            # an underspec/contradiction) also stays "auto" so the model can
            # lead with the option chain when that's the better answer.
            agent_tool_choice = (
                "required"
                if (is_underspec_agent or is_contradiction) and not is_filler_after_q
                else "auto"
            )
            if selected_names is not None:
                _UNDERSPEC_STRIP = frozenset({
                    "propose_workflow", "propose_scheduled_order",
                    "propose_threshold_order", "propose_basket_allocation",
                    "propose_holding_action",
                })
                if mentions_fno:
                    _UNDERSPEC_STRIP = _UNDERSPEC_STRIP | frozenset({
                        "place_market_order", "place_limit_order",
                        "place_order",
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
        _option_view_ask = _is_option_view_ask(message)
        _notify_only = _is_notify_only_alert(message)
        _at_open_close = _is_at_open_close_build(message)
        _confusion_menu = _is_confusion_after_menu(message, history)
        _unsupported_rail = _names_unsupported_rail(message)
        _hedge_followup = _is_hedge_followup(message, history)
        _hedge_request = _is_hedge_request(message) or _hedge_followup
        if (not _settings.llm_owned_interpretation
                and _named_option_build and selected_names is not None):
            selected_names = (selected_names | _OPTIONS_TOOLS) - frozenset({
                "place_market_order", "place_limit_order", "place_order",
                "create_gtt_order", "suggest_option_strategy",
                "critique_option_strategy",
            })
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            agent_tool_choice = "required"
        # R4b (stream mirror): see the non-streaming R4b comment above.
        elif (not _settings.llm_owned_interpretation
                and _option_view_ask and selected_names is not None):
            selected_names = (selected_names | _OPTIONS_TOOLS) - frozenset({
                "place_market_order", "place_limit_order", "place_order",
                "create_gtt_order",
            })
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            agent_tool_choice = "required"
        # H1 (stream mirror): hedge construction → options surface in,
        # order macros OUT; tool_choice auto for the explain-first reply.
        elif (not _settings.llm_owned_interpretation
                and _hedge_request and selected_names is not None):
            selected_names = (
                selected_names | _OPTIONS_TOOLS
            ) - _HEDGE_STRIP_TOOLS
            if _hedge_followup:
                # Acceptance of the offered second card: force the build,
                # drop ASK_USER so it cannot re-ask position size.
                tooldefs = [
                    t for t in _registry_tools_as_tooldefs(selected_names)
                    if t.name != ASK_USER_TOOL_NAME
                ]
                agent_tool_choice = "required"
            else:
                tooldefs = _registry_tools_as_tooldefs(selected_names)
                agent_tool_choice = "auto"
            cache_key = cache_key_for(selected_names)
            trace.event("hedge_guard.scope_forced", followup=_hedge_followup)
        # R3: price/condition ALERT ask → NOT forced (alerts aren't available;
        # the notify tools refuse and the boundary guard states it in prose).
        elif (not _settings.llm_owned_interpretation
                and _at_open_close and selected_names is not None):
            selected_names = selected_names | frozenset({
                "propose_dsl_workflow", "propose_workflow",
            })
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            agent_tool_choice = "required"
        if _confusion_menu and not _settings.llm_owned_interpretation:
            agent_tool_choice = "auto"
            if selected_names is not None:
                tooldefs = [
                    t for t in _registry_tools_as_tooldefs(selected_names)
                    if t.name != ASK_USER_TOOL_NAME
                ]
        elif _unsupported_rail is not None:
            agent_tool_choice = "auto"

        # 51-sweep read gates (streaming mirror of handle()).
        _read_gate = _read_intent_gate(message, selected_names)
        if _read_gate is not None:
            selected_names, agent_tool_choice, _read_gate_directive = \
                _read_gate
            tooldefs = [
                t for t in _registry_tools_as_tooldefs(selected_names)
                if t.name != ASK_USER_TOOL_NAME
            ]
            cache_key = cache_key_for(selected_names)
            trace.event("read_gate.scope_forced")
        else:
            _read_gate_directive = None

        # ── GAN R4 scenario routing (thematic / vague / idle / unreal) ──
        # MIRROR of handle(); keep both in sync. Only fires when no
        # higher-priority specific guard already claimed the turn.
        _scenario_routed = False
        if not (_named_option_build or _hedge_request or _notify_only
                or _at_open_close or _confusion_menu
                or _unsupported_rail is not None):
            _scn = _apply_scenario_routing(
                message, selected_names, tooldefs, cache_key
            )
            if _scn.matched:
                selected_names = _scn.selected_names
                tooldefs = _scn.tooldefs
                cache_key = _scn.cache_key
                _scenario_routed = True
                if _scn.tool_choice is not None:
                    agent_tool_choice = _scn.tool_choice
                trace.event(
                    "scenario_routing.applied",
                    thematic=detect_thematic_scenario(message) is not None,
                    tool_choice=agent_tool_choice,
                )

        # Stream path matches the non-stream `handle()` decision —
        # "medium" on every turn (see the full rationale + tradeoff
        # commentary there).
        effort: ReasoningEffort = "medium"
        max_output: int = 1500
        # R5: mirror of non-streaming reply-class budget.
        reply_class = _classify_reply_class(message, intent_kind)
        # GAN R4: force the structured analysis budget on the scenario
        # classes (mirror of handle()).
        if (not _settings.llm_owned_interpretation
                and (detect_thematic_scenario(message) is not None
                     or is_vague_onboarding(message)
                     or is_scared_idle_cash(message)
                     or is_unrealistic_return(message))):
            reply_class = "analysis"
        # STRATEGY budget override (mirror of handle()): route a
        # strategy/basket build to the high-cap 'strategy' class so the
        # connection + rationale + alternatives + table reply isn't
        # truncated at the 1500-token draft cap.
        if (_is_strategy_framed(message, history)
                and not _settings.llm_owned_interpretation):
            reply_class = "strategy"
        _budget_tokens, reply_class_hint_text = _REPLY_BUDGETS.get(
            reply_class, _REPLY_BUDGETS["analytical_short"]
        )
        # GAN R2 R1/R8: screen/trend sub-hint on the analysis class.
        if reply_class == "analysis":
            _sub = _analysis_subhint(message)
            if _sub:
                reply_class_hint_text = reply_class_hint_text + _sub
        max_output = _budget_tokens
        # List reads only format a table — drop reasoning effort to 'low' so
        # reasoning tokens don't eat the output budget and truncate the table
        # (mirror of the non-streaming path).
        if reply_class == "list_read":
            effort = "minimal"
        # Light classes starve on 'medium' too — drop to 'low' so the
        # visible answer gets the budget (mirror of the non-streaming path;
        # see the hop-probe note there).
        elif reply_class in ("analytical_short", "capability", "small_talk"):
            effort = "low"
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
        # path — see handle() for full rationale. Track C #2: named
        # back-references promote the matching parked draft.
        active = self._select_active_draft(conv_id, message, trace)
        # Read-gate eviction — see handle() for WHY.
        if active is not None and (_is_independent_prompt(message)
                                   or _read_gate is not None):
            self.store.clear_active_draft(conv_id)
            trace.event("active_draft.evicted",
                        reason=("read_gate" if _read_gate is not None
                                else "independent_prompt"),
                        tool=active.tool_name)
            active = None
        # Meta lane (question / feedback while draft or clarify active) —
        # see handle() for WHY.
        _meta_kind = _followup_turn_kind(message) if (
            active is not None
            or (history and _looks_like_clarification_followup(history))
            # A challenge to a plain read/screen result ("isn't IGL a
            # gas company, not pharma?") has no draft and no clarify-
            # question tail to anchor on — `active`/clarification-
            # followup alone never engaged this lane for it, so the
            # message fell through to fresh tool-selection and re-ran
            # the SAME screen with zero acknowledgment (reported
            # 2026-07-14). Any turn following a tool call at all is a
            # candidate for "answer from what's already known" — the
            # regex inside `_followup_turn_kind` still has to actually
            # match for this to do anything.
            or bool(self.store.get_last_tools(conv_id))
        ) else None
        # Mirror of non-streaming workflow_hint — extended to all macro
        # draft types (propose_threshold_order, propose_scheduled_order, etc.).
        # See handle() for WHY.
        workflow_hint = ""
        if (active is not None and _meta_kind is None
                and active.tool_name in _MACRO_AMENDMENT_TOOLS):
            draft_json = _safe_draft_json(active.draft)
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
                + self._parked_draft_clause(conv_id, active)
            )

        # Force tool_choice="required" on amendment turns — see handle().
        # GAN R2 R7: a Hinglish / rupee-notional resize is also an amendment.
        if (not is_agent_intent
                and active is not None
                and workflow_hint
                and (_is_genuine_dependent_amendment(message)
                     or _is_rupee_notional_resize(message)
                     # A message that explicitly NAMES the active draft's
                     # own symbol ("activate that goldbees agent from
                     # earlier") is a stronger signal than any amendment
                     # verb — `_select_active_draft` already promoted
                     # THIS draft into the slot on that exact basis
                     # (named_backref). Without this, such a message fell
                     # through with no followup_hint/forced tool_choice,
                     # so the model classified the turn fresh off raw
                     # history and picked a different, wrong tool
                     # (reported 2026-07-14: recalling a 20-turn-old
                     # draft by name silently activated the most-recent
                     # draft instead).
                     or (active.symbol
                         and _symbol_mentioned(message, active.symbol)))
                and not _requests_comparison_over_amendment(message)):
            agent_tool_choice = "required"
            if (_is_rupee_notional_resize(message)
                    and selected_names is not None
                    and "get_live_price" not in selected_names):
                # get_market_data is the visible consolidated equivalent;
                # get_live_price stays for the hidden direct dispatch path.
                selected_names = selected_names | {"get_live_price",
                                                   "get_market_data"}
                tooldefs = _registry_tools_as_tooldefs(selected_names)
                cache_key = cache_key_for(selected_names)

        # GAN R2 R6 (streaming mirror): confusion-after-menu → TEACH, not
        # a forced clarification answer.
        if _confusion_menu:
            agent_tool_choice = "auto"

        if _meta_kind is not None:
            # META lane — mirror of handle(). Answer, never re-emit.
            followup_hint_msg = _meta_turn_hint(_meta_kind, active, message)
            agent_tool_choice = "auto"
            if _meta_kind == "question":
                # See handle(): ASK_USER available → question parroted back.
                tooldefs = [t for t in tooldefs
                            if t.name != ASK_USER_TOOL_NAME]
            trace.event("followup.meta_lane", kind=_meta_kind,
                        has_draft=active is not None)
        elif (history and _looks_like_clarification_followup(history)
                and not _confusion_menu):
            last_assistant = next(
                (h for h in reversed(history)
                 if isinstance(h, dict) and h.get("role") == "assistant"),
                None,
            )
            last_text = (last_assistant or {}).get("content") or ""
            # Original ask = the intent that spawned THIS clarification, not
            # the first user turn in the window (streaming mirror of handle();
            # see _originating_user_intent for the multi-intent rationale).
            original_intent = (
                _pending_original_intent
                or _originating_user_intent(history)
            )
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
        elif (active is not None and workflow_hint
                and (_is_genuine_dependent_amendment(message)
                     or _is_rupee_notional_resize(message)
                     # A message that explicitly NAMES the active draft's
                     # own symbol ("activate that goldbees agent from
                     # earlier") is a stronger signal than any amendment
                     # verb — `_select_active_draft` already promoted
                     # THIS draft into the slot on that exact basis
                     # (named_backref). Without this, such a message fell
                     # through with no followup_hint/forced tool_choice,
                     # so the model classified the turn fresh off raw
                     # history and picked a different, wrong tool
                     # (reported 2026-07-14: recalling a 20-turn-old
                     # draft by name silently activated the most-recent
                     # draft instead).
                     or (active.symbol
                         and _symbol_mentioned(message, active.symbol)))
                and not _requests_comparison_over_amendment(message)):
            # Mirror of the non-streaming AMENDMENT gate — see handle()
            # for WHY this must share the same confidence check as the
            # tool_choice force above rather than firing on any turn
            # that merely isn't meta/question-classified.
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
        # Intent packs (streaming mirror of handle()): core always loaded,
        # domain mechanics injected only when the turn needs them.
        _summary_block = _summary_bridge_block(
            conv_id, getattr(ctx, "user_id", 0), history_override,
        )
        if _summary_block:
            base_messages_summary = LLMMessage(
                role="system", content=_summary_block,
            )
        else:
            base_messages_summary = None
        for _st_block in _session_state_blocks(self.store, conv_id):
            base_msgs.append(LLMMessage(role="system", content=_st_block))
        _mod_block = _prompt_module_block(message, history)
        if _mod_block:
            base_msgs.append(LLMMessage(role="system", content=_mod_block))
        if base_messages_summary is not None:
            base_msgs.append(base_messages_summary)
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
        if _read_gate_directive:
            base_msgs.append(
                LLMMessage(role="system", content=_read_gate_directive))
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
        # Mirror of non-streaming A3 read-amendment hint (chat-kernel
        # 2026-07-10): bare amendment of a READ answer, no active draft.
        elif (active is None
                and _prior_read_tools
                and _DEPENDENT_INTENT_RE.search(message)
                and not _is_question_shaped(message)):
            base_msgs.append(LLMMessage(
                role="system",
                content=(
                    "## Amendment of the previous ANSWER (no draft exists)\n"
                    f"The previous turn answered using "
                    f"{', '.join(_prior_read_tools[:3])}. The user's "
                    "current message changes ONE parameter of that same "
                    "question (a period, a symbol, a threshold). Re-call "
                    "the SAME tool with the amended parameter and answer "
                    "directly. There is NO workflow or draft to amend — "
                    "do NOT ask which one."
                ),
            ))
            agent_tool_choice = "required"
        # Mirror of non-streaming underspec/filler hint.
        if ((is_underspec_agent or is_filler_after_q) and not _scenario_routed
                and not _settings.llm_owned_interpretation):
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
        # Turn-level screen-call counter: the deterministic table reply is
        # only valid when ONE screen was the whole ask — multiple screens
        # mean the model is gathering inputs for a synthesis it must write.
        screen_calls_this_turn = 0
        # presentation='analysis' (model-chosen on screen_fundamentals): the
        # model owns the WHOLE reply — tables included (instructed to quote
        # tool values verbatim); the deterministic render never fires.
        screen_analysis_mode = False
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
        # Stream mirror of handle(): re-prompt ONCE with tools OFF when a
        # read tool ran but the model streamed empty prose (deferred to a
        # non-existent card / reasoning ate the budget).
        empty_narration_reprompt_used = False
        _force_no_tools = False
        # A NEWS ask must actually BROWSE. Track whether the model invoked the
        # hosted web_search this turn; if it finalised WITHOUT browsing (it
        # got anchored on the index/movers tools instead), re-prompt ONCE to
        # force the search. Only armed for genuine news asks + web enabled.
        _news_ask = bool(_HOSTED_TOOLS) and _is_news_browse_ask(message)
        news_browse_reprompt_used = False
        # A CONSTRUCTION ask must actually build the basket. Prompt wording
        # alone (system_core.md's Construction contract) isn't reliable here:
        # `is_construction_intent` already forces hop-1 tool_choice=required
        # and widens `build_strategy` into scope, but a read tool that's
        # legitimately still in scope for grounding (screen_fundamentals)
        # ALSO satisfies "required" — the model can call it, then treat the
        # screen as a sufficient final answer and stop (live repro
        # 2026-07-15: "build a strategy that gets affected positively by big
        # oil moves" streamed a bare "Energy — ranked by Market Cap" table
        # and never built anything, ~2/5 live-tested). Track whether
        # build_strategy/ask_user_dynamic has fired; if a construction turn
        # finalises without either, force exactly ONE more hop scoped to
        # just those two tools before accepting prose as final.
        construction_retry_used = False
        _force_construction_tools = False
        # When the construction-retry fires, the model already streamed a
        # stale (screener-only) answer. Suppress that hop's live deltas and
        # emit the corrected basket answer as a single 'replace' — mirrors
        # the news browse-reprompt below.
        _suppress_stream_deltas = False

        while hop_index < _MAX_TOOL_CALLS:
            hop_index += 1
            hop_started = time.monotonic()
            # A1: only force tool_choice on hop 1; later hops MUST be
            # allowed to emit final text (otherwise the loop never ends).
            hop_tool_choice: Literal["auto", "required", "none"] = (
                "none" if _force_no_tools
                else "required" if _force_construction_tools
                else (agent_tool_choice if hop_index == 1 else "auto")
            )
            _force_no_tools = False
            _force_construction_tools = False
            hop_max_output = (
                _COMPACT_POST_MACRO_MAX_OUTPUT
                if (_COMPACT_DRAFTS and last_was_macro_draft)
                else max_output
            )
            # Forced-no-tools reprompt: drop effort to 'minimal' + guarantee
            # headroom so the model writes prose instead of re-emitting empty.
            hop_effort: ReasoningEffort = effort
            if hop_tool_choice == "none":
                hop_effort = "minimal"
                hop_max_output = max(hop_max_output, 1500)
            trace.event(
                "llm.stream", hop=hop_index,
                reasoning_effort=hop_effort, tools_offered=len(tooldefs),
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

            # Release the pooled DB connection for the streaming LLM wait —
            # see _release_db_conn. Re-acquired on the session's next query.
            _release_db_conn(ctx.db)
            async for ev in stream_openai(
                client,
                messages=messages,
                tools=tooldefs,
                tool_choice=hop_tool_choice,
                max_output_tokens=hop_max_output,
                reasoning_effort=hop_effort,
                temperature=0.2,
                prompt_cache_key=cache_key,
                hosted_tools=_hosted_tools_for(message),
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
                        # Stream user-visible text live (unless we're on a
                        # browse-reprompt hop, where the stale answer already
                        # streamed and we'll swap the final text via 'replace').
                        if not _suppress_stream_deltas:
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
                _degraded = _unavailable_text(message)
                yield {"type": "error", "message": _degraded}
                yield {
                    "type": "done",
                    "response": _degraded,
                    "tools_called": tools_called,
                    "logiccard": logiccard,
                    "raw_data": {"_llm_unavailable": True},
                    "latency_ms": int((time.monotonic() - turn_started) * 1000),
                    "latency_breakdown": breakdown,
                }
                return

            hop_text = "".join(text_parts)

            # No tool calls → final hop. Wrap up.
            if not tc_acc:
                text, sanitised = _post_process(hop_text)
                # CONSTRUCTION ask that finalised without ever building the
                # basket (see `construction_retry_used` above for the WHY).
                # A read tool alone (screen_fundamentals, query_financials,
                # etc.) is grounding input, never the answer, to a
                # build/create-a-strategy ask.
                if (
                    is_construction_intent and not construction_retry_used
                    and not any(
                        t in ("build_strategy", "ask_user_dynamic")
                        for t in tools_called
                    )
                ):
                    construction_retry_used = True
                    _force_construction_tools = True
                    _suppress_stream_deltas = True
                    tooldefs = _registry_tools_as_tooldefs(
                        frozenset({"build_strategy", "ask_user_dynamic"})
                    )
                    trace.event("construction.retry_forced",
                                tools_so_far=tools_called)
                    messages.append(LLMMessage(role="assistant", content=text))
                    messages.append(LLMMessage(
                        role="system",
                        content=(
                            "## FINISH THE STRATEGY BUILD\n"
                            "This is a CONSTRUCTION ask (build/own a basket "
                            "now). Any read tool you just called (e.g. a "
                            "sector screen) is an INPUT to the basket, not "
                            "the answer — the turn is not done. Call "
                            "`build_strategy` now, using the real names from "
                            "what you just fetched, or `ask_user_dynamic` "
                            "if a genuinely blocking detail is missing. Do "
                            "NOT present the screen/table itself as the "
                            "final answer."
                        ),
                    ))
                    continue
                # NEWS ask that finalised WITHOUT a cited source → force a real
                # browse ONCE. The model either skipped web_search (anchored on
                # the index/movers tools) OR called it but wrote a generic,
                # un-cited answer that didn't surface the headlines. Either way,
                # a news answer with NO source URL is the failure the user
                # flagged. Keep tools ON so web_search can fire; suppress this
                # hop's deltas (stale answer already streamed) and swap the
                # browsed, cited answer in via 'replace'. `http` in the text =
                # the model inlined a real url_citation → good, don't reprompt.
                _has_citation = "http" in (text or "").lower()
                if (
                    _news_ask and not _has_citation
                    and not news_browse_reprompt_used
                ):
                    news_browse_reprompt_used = True
                    _suppress_stream_deltas = True
                    trace.event("news_browse.reprompt", tools=tools_called)
                    messages.append(LLMMessage(
                        role="system",
                        content=(
                            "## BROWSE AND CITE — YOUR ANSWER HAD NO SOURCES\n"
                            "This is a NEWS ask and your answer cited NO real "
                            "headlines. Call the `web_search` tool NOW (again "
                            "if needed) to fetch the actual current headlines "
                            "from credible Indian-market sources (Economic "
                            "Times, Moneycontrol, Mint, Business Standard, "
                            "Reuters), then REWRITE the answer around those "
                            "FETCHED headlines — lead with the specific stories "
                            "and include each source as an inline link — "
                            "combined with any tape data already gathered. Do "
                            "NOT answer with generic 'what usually moves the "
                            "market' prose. Quote ONLY what the search returns; "
                            "never invent a headline, source, number, or URL."
                        ),
                    ))
                    continue
                # Empty prose after a read tool → re-prompt ONCE (tools off)
                # to force a real answer. Nothing was shown to the user yet
                # (empty stream), so the reprompt's text streams in cleanly.
                _empty_prose = (
                    not hop_text.strip()
                    or (sanitised and text == _GENERIC_FALLBACK)
                )
                _emitted_card = any(
                    isinstance(v, dict) and v.get("_render_hint")
                    for v in (raw_data or {}).values()
                ) or bool(raw_data.get("_render_hint"))
                if (
                    _empty_prose and tools_called and not _emitted_card
                    and not empty_narration_reprompt_used
                ):
                    empty_narration_reprompt_used = True
                    _force_no_tools = True
                    trace.event("empty_narration.reprompt",
                                tools=tools_called)
                    messages.append(LLMMessage(
                        role="system",
                        content=(
                            "## WRITE THE ANSWER NOW\n"
                            "You called tools and their results are in the "
                            "conversation above, but you returned an empty "
                            "message. Write the user-facing answer in prose "
                            "NOW, using those results plus your own "
                            "knowledge. Do NOT call any more tools. Do NOT "
                            "defer to a card — there is no card for this "
                            "answer. Give a substantive, useful, data-rich "
                            "reply."
                        ),
                    ))
                    continue
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
                    user_message=message,
                )
                if augmented != text:
                    sanitised = True
                    text = augmented
                # If the post-processor rewrote the text, OR we ran a
                # browse-reprompt / construction-reprompt (whose deltas were
                # suppressed), the user's on-screen text is stale — send the
                # final text as a single replacement so the FE swaps it in.
                if sanitised or news_browse_reprompt_used or construction_retry_used:
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

            # Hop-scoped flags (stream mirror of handle()): a fully-drafted
            # card with no error / no find_tool lazy-load needs no narration
            # hop — finalize deterministically below.
            hop_drafted_card = False
            hop_error = False
            hop_find_tool = False
            # Stream mirror: screen rows render deterministically (no
            # narration hop) — see handle() for the rationale.
            hop_screen_data: Optional[dict] = None

            for tc in tool_calls:
                yield {"type": "tool_start", "name": tc.get("name", "")}
                trace.event("tool.invoke", tool=tc.get("name"),
                            args=tc.get("arguments"))
                if tc.get("name") == "screen_fundamentals":
                    screen_calls_this_turn += 1
                    if (tc.get("arguments") or {}).get("presentation") == "analysis":
                        screen_analysis_mode = True
                # H1 (stream mirror): one strategy card per turn — reject
                # a duplicate build_option_strategy so it can't overwrite
                # the card already built this turn.
                if (
                    tc.get("name") == "build_option_strategy"
                    and "build_option_strategy" in tools_called
                ):
                    messages.append(LLMMessage(
                        role="tool",
                        tool_call_id=tc.get("id", f"call_{hop_index}"),
                        name="build_option_strategy",
                        content=(
                            "REJECTED: only one strategy card renders per "
                            "turn and the first build is already on "
                            "screen. Describe the card that was built and "
                            "OFFER to build this one next turn ('say the "
                            "word and I'll build the same for <name>')."
                        ),
                    ))
                    trace.event("tool.rejected_duplicate",
                                tool="build_option_strategy")
                    continue
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
                # Session artifact ledger — hooked HERE (right after
                # execution) so TERMINAL card tools are recorded too;
                # the tool_msg path below only runs when the loop takes
                # another LLM hop, which card turns never do.
                self._note_artifact(conv_id, guarded)
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
                    # Workstream A: persist the dynamic clarify flow so the next
                    # answer advances the N-of-M flow in-band (streaming mirror).
                    self._maybe_set_clarify_state(conv_id, message, guarded)
                    total = int((time.monotonic() - turn_started) * 1000)
                    breakdown["total"] = total
                    yield {"type": "delta", "text": guarded.question}
                    yield {
                        "type": "done",
                        "response": guarded.question,
                        "tools_called": [guarded.name],
                        "logiccard": None,
                        "raw_data": _clarify_raw_data(guarded),
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
                    if (guarded.name == "screen_fundamentals"
                            and screen_analysis_mode
                            and guarded.data and guarded.data.get("results")):
                        tool_msg_content += (
                            "\n\n[presentation=analysis: NO table is "
                            "auto-rendered — your reply must include the "
                            "ranked results as a markdown table, quoting "
                            "these tool values VERBATIM (never round, "
                            "reorder, or invent), followed by your "
                            "analysis in YOUR OWN structured form: "
                            "open with one '## <specific title>' heading "
                            "that names THIS answer, then "
                            "markdown ## section headings (e.g. what "
                            "stands out / caveats / view — pick headings "
                            "that fit THIS answer), bold key numbers, "
                            "bullets where they help. Never a wall of "
                            "plain paragraphs. Include the FULL ranked "
                            "table ONLY when the user asked for a screen/"
                            "list ('screen me…', 'show me companies with "
                            "X'). For an analyze/research/suggest ask, do "
                            "NOT dump the whole screen — table only the "
                            "shortlisted names your analysis actually "
                            "discusses; the screen is your working "
                            "material, not the deliverable. If the user named "
                            "a constraint you could NOT express as a "
                            "filter (e.g. stability/consistency over "
                            "time), say so explicitly and verify it "
                            "yourself for the shortlisted names (e.g. "
                            "query_financials history) before ranking "
                            "them.]"
                        )
                    messages.append(LLMMessage(
                        role="tool",
                        tool_call_id=tc.get("id", f"call_{hop_index}"),
                        name=guarded.name,
                        content=tool_msg_content,
                    ))
                    if guarded.name not in tools_called:
                        tools_called.append(guarded.name)
                        # Round 3: persist for the next turn's scope
                        # union (bare-amendment recovery, both paths).
                        self.store.set_last_tools(conv_id, tools_called)
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
                    # Mirror of handle(): a non-stashing order/macro tool
                    # (GTT/SL/OCO/SIP/squareoff) just rendered its own card
                    # — evict any stale prior draft so it can't leak into
                    # the next turn's amendment routing. See handle()'s
                    # comment for the full rationale.
                    elif guarded.name in _ORDER_AND_MACRO_TOOLS:
                        self.store.clear_active_draft(conv_id)
                    if (guarded.name in _STASH_DRAFT_TOOLS
                            or guarded.name in _OPTION_CARD_TOOLS
                            or guarded.name in _COMPACT_PROSE_TOOLS):
                        last_was_macro_draft = True
                    # Workflow/order draft card → no narration hop (mirror).
                    if guarded.name in _STASH_DRAFT_TOOLS:
                        hop_drafted_card = True
                    # Screen rows → deterministic table reply (mirror).
                    # EXCEPT sector-OUTLOOK asks — see handle().
                    if (guarded.name == "screen_fundamentals"
                            and guarded.data and guarded.data.get("results")
                            and not screen_analysis_mode):
                        hop_screen_data = guarded.data
                    if guarded.name == "find_tool":
                        hop_find_tool = True
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

                hop_error = True
                last_tool_error = f"{guarded.name}: {guarded.error}"
                # L12 (streaming mirror): route-redirect on
                # "use <other_tool> instead" errors, plus the schedule-
                # shape backstop (see _redirect_target_for_failure).
                target_tool = _redirect_target_for_failure(
                    guarded.name, guarded.error or "", message,
                    structured=getattr(guarded, "redirect_to", None),
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
                # Stream-path mirror of handle() — internal shape bug
                # gets an honest reply, not a fabricated LLM question.
                if question == _LLM_CLARIFY_SENTINEL:
                    if _is_internal_shape_error(guarded.error or ""):
                        question = _INTERNAL_SHAPE_ERROR_REPLY
                    else:
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

            # A card was fully drafted this hop with no error / no lazy-load —
            # skip the narration hop. Stream a deterministic, data-rich caption
            # (synthesised from the steps) and finish. Removes a full ~33k-token
            # narration round-trip on every agent-build turn.
            if hop_drafted_card and not hop_error and not hop_find_tool:
                primary = next(
                    (t for t in reversed(tools_called) if t in _STASH_DRAFT_TOOLS),
                    "propose_workflow",
                )
                text_out = _ensure_widget_caption(
                    "", tool_name=primary, logiccard=logiccard, raw_data=raw_data,
                    user_message=message,
                )
                self.store.append(conv_id, message, text_out)
                self.store.clear_pending(conv_id)
                total = int((time.monotonic() - turn_started) * 1000)
                breakdown["total"] = total
                breakdown["narration_hop_skipped"] = 1
                yield {"type": "delta", "text": text_out}
                _log_timing(client.provider_name, message, total, breakdown,
                            tools=tools_called, note="stream-draft_card_no_narration")
                trace.event("turn.end", total_ms=total, tools_called=tools_called,
                            reason="draft_card_no_narration")
                trace.end()
                yield {
                    "type": "done",
                    "response": text_out,
                    "tools_called": tools_called,
                    "logiccard": logiccard,
                    "raw_data": raw_data or None,
                    "latency_ms": total,
                    "latency_breakdown": breakdown,
                }
                return

            # Screen turn finalized deterministically (stream mirror of
            # handle()) — the ranked rows render verbatim; no narration hop.
            # NEVER on a CONSTRUCTION ask — see the twin guard in handle()
            # for the WHY.
            if (hop_screen_data is not None and not hop_error
                    and not hop_find_tool
                    and not is_construction_intent
                    and tools_called == ["screen_fundamentals"]
                    # tools_called is DEDUPED — three parallel screens still
                    # read as one entry. Multiple screens = ingredients for a
                    # synthesis (e.g. "who wins if the monsoon fails"); the
                    # model keeps its narration hop (live repro 2026-07-17:
                    # this branch swallowed a 3-screen thematic ask and the
                    # user got one bare FMCG table instead of an answer).
                    and screen_calls_this_turn == 1):
                from backend.services.fundamentals_screen import (
                    render_screen_markdown,
                )
                text_out = render_screen_markdown(hop_screen_data) or ""
                if text_out:
                    self.store.append(conv_id, message, text_out)
                    self.store.clear_pending(conv_id)
                    total = int((time.monotonic() - turn_started) * 1000)
                    breakdown["total"] = total
                    breakdown["narration_hop_skipped"] = 1
                    yield {"type": "delta", "text": text_out}
                    _log_timing(client.provider_name, message, total,
                                breakdown, tools=tools_called,
                                note="stream-screen_table_no_narration")
                    trace.event("turn.end", total_ms=total,
                                tools_called=tools_called,
                                reason="screen_table_no_narration")
                    trace.end()
                    yield {
                        "type": "done",
                        "response": text_out,
                        "tools_called": tools_called,
                        "logiccard": logiccard,
                        "raw_data": raw_data or None,
                        "latency_ms": total,
                        "latency_breakdown": breakdown,
                    }
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
        message: str = "",
    ) -> ChatTurn:
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        return ChatTurn(
            response=_unavailable_text(message),
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
    # Model-authored summary (propose_dsl_workflow `summary` arg): the model
    # already wrote the how-it-works prose — lead with it instead of the
    # code-assembled phrase.
    _summary = str(skeleton.get("summary") or "").strip()
    if _summary:
        return (
            f"**{name}** — {_summary} "
            "Review the steps below and click Activate when you're happy "
            "with it."
        )
    trigger_step = next((s for s in steps if s.get("step_type", "").startswith("trigger.")), None)
    action_step = next(
        (
            s for s in steps
            if s.get("step_type", "").startswith(("action.", "notify."))
        ),
        None,
    )

    when_phrase = "on its trigger"
    if trigger_step:
        cfg = trigger_step.get("config") or {}
        if trigger_step["step_type"] == "trigger.schedule":
            cron = (cfg.get("cron") or "").strip()
            run_at = str(cfg.get("run_at") or "").strip()
            # Render a friendly time from "MM HH DOM MON DOW". The DOM/MON
            # fields matter: "0 9 1 */3 *" is QUARTERLY (1st of every 3rd
            # month), and describing it as "every day" mis-states the draft
            # (observed live on the quarterly-rebalance basket caption).
            parts = cron.split()
            if run_at:
                when_phrase = f"once, on {run_at[:16].replace('T', ' at ')} IST"
            elif len(parts) == 5:
                mm, hh, dom, mon, dow = parts
                if dom != "*":
                    mon_label = {
                        "*": "every month",
                        "*/3": "every 3rd month",
                        "*/6": "every 6th month",
                        "1": "January every year",
                    }.get(mon, f"months `{mon}`")
                    dom_label = (
                        f"on the {dom}st of {mon_label}" if dom == "1"
                        else f"on day {dom} of {mon_label}"
                    )
                else:
                    dom_label = {
                        "1-5": "every weekday",
                        "*": "every day",
                        "1": "every Monday", "2": "every Tuesday",
                        "3": "every Wednesday", "4": "every Thursday",
                        "5": "every Friday",
                    }.get(dow, f"on cron `{cron}`")
                try:
                    when_phrase = f"{dom_label} at {int(hh):02d}:{int(mm):02d} IST"
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

    # Default assumes an action step exists but didn't match a named
    # branch below — NOT a safe default for "no action step matched at
    # all" (e.g. notify.message falling through here would silently
    # claim an order gets placed for a pure alert, violating the
    # alert-verb hard gate). Only reached when action_step really is an
    # action.* step of an unhandled sub-type.
    do_phrase = "runs the configured action"
    is_notify_only = bool(action_step and action_step["step_type"] == "notify.message")
    if is_notify_only:
        do_phrase = "sends you a notification"
    elif action_step and action_step["step_type"] == "action.place_order":
        cfg = action_step.get("config") or {}
        side = cfg.get("side", "buy")
        qty = cfg.get("quantity", "")
        sym = cfg.get("symbol", "")
        order_type = cfg.get("order_type", "market")
        # Humanise Mustache refs so the caption never leaks a raw
        # "{{ context.N.symbol }}" — a top-movers-driven symbol becomes
        # "the day's top gainer/loser"; any other ref-symbol becomes
        # "the selected stock". (The LLM narration used to phrase this; the
        # deterministic caption now owns it on the no-narration fast path.)
        humanized = isinstance(sym, str) and "{{" in sym
        if humanized:
            mover = next(
                (s for s in steps if s.get("step_type") == "fetch.top_movers"),
                None,
            )
            if mover is not None:
                direction = (mover.get("config") or {}).get("direction") or "gainers"
                sym = ("the day's top gainer" if "gain" in str(direction)
                       else "the day's top loser")
            else:
                sym = "the selected stock"
        if isinstance(qty, str) and "{{" in qty:
            qty = ""  # a ref qty ("sell the whole position") → drop the number
        if humanized:
            qty_word = f"{qty} shares of " if qty != "" else ""
            do_phrase = f"{side}s {qty_word}{sym} at {order_type}".strip()
        else:
            qty_part = f"{qty} " if qty != "" else ""
            do_phrase = f"{side}s {qty_part}{sym} at {order_type}".strip()

    no_order_note = " No order is placed — this only alerts you." if is_notify_only else ""
    return (
        f"Here's a draft for **{name}** — it {do_phrase} {when_phrase}."
        f"{no_order_note} "
        "Review the steps below and click Activate when you're happy "
        "with it."
    )


# Honesty guard: the user asked for a hedge / market-neutral / delta-neutral /
# non-directional structure, but the agent loop just built a plain long-only
# draft (SIP, single-side buy, allocate-notional basket). Long-only ≠ neutral;
# silently shipping the draft as "your risk-neutral oil agent" is the exact
# failure mode the 2026-06-17 incident exposed (user asked for "profits from
# rising oil but risk neutral" → got a weekly long-only IOC SIP, which is
# *neither* neutral nor an oil-producer exposure). Append a non-blocking
# disclosure that names the mismatch and offers the nearest real thing.
_RISK_NEUTRAL_CUE_RE = re.compile(
    r"\b("
    r"risk[\s-]?neutral|"
    r"market[\s-]?neutral|"
    r"delta[\s-]?neutral|"
    r"non[\s-]?directional|"
    r"hedg(?:e|ed|ing)|"
    r"defined[\s-]?risk|"
    r"capped[\s-]?risk"
    r")\b",
    re.IGNORECASE,
)

# Action step_types that we count as a "long-only directional" leg. Anything
# outside this set (action.short_*, any action.option_*, action.write_*,
# action.sell_to_open, etc.) counts as offsetting — we DON'T fire the
# disclosure when the build includes a real hedge leg.
_LONG_ONLY_ACTION_TYPES = frozenset({
    "action.place_order",
    "action.allocate_notional",
    "action.allocate_basket",
    "action.scheduled_order",
    "action.sip",
})


def _is_long_only_draft(skeleton: dict) -> bool:
    """True iff every action.* step in the workflow draft is a plain
    long buy / allocate / SIP — no offsetting short or option leg.

    A draft with zero action steps (notify-only, fetch-only) returns
    False — there's nothing directional to disclose. A draft with even
    one non-long-only action (a short, a written option, a put leg)
    returns False — the build IS hedged in spirit.
    """
    steps = skeleton.get("steps") or []
    action_steps = [
        s for s in steps
        if isinstance(s, dict)
        and isinstance(s.get("step_type"), str)
        and s["step_type"].startswith("action.")
    ]
    if not action_steps:
        return False
    for s in action_steps:
        step_type = s["step_type"]
        if step_type not in _LONG_ONLY_ACTION_TYPES:
            return False
        cfg = s.get("config") or {}
        # An options leg (long put/call/spread) is a hedge in spirit even when
        # "bought" — don't fire the long-only disclosure for it. Pivot routes
        # options via action.place_option_strategy (already excluded above), but
        # guard a place_order on an option symbol / explicit option fields too.
        if (
            cfg.get("option_type")
            or cfg.get("instrument_type")
            or cfg.get("template")
            or str(cfg.get("symbol") or "").upper().endswith(("CE", "PE"))
        ):
            return False
        # action.place_order can be a short — if side != "buy", treat as
        # offsetting. Default missing side to "buy" (the schema default).
        side = str(cfg.get("side") or "buy").lower()
        if side not in {"buy", "long"}:
            return False
    return True


def _risk_neutral_disclosure(
    user_message: str, raw_data: dict | None,
) -> Optional[str]:
    """Return a one-paragraph honesty disclosure to APPEND to the reply
    when the user asked for a neutral/hedged structure but the produced
    workflow draft is plain long-only.

    Returns None when no disclosure is warranted (no cue in the user
    message, no workflow_draft in raw_data, or the draft already carries
    an offsetting leg). Never raises — defensive against malformed
    raw_data shapes the caller might pass.
    """
    if not user_message or not _RISK_NEUTRAL_CUE_RE.search(user_message):
        return None
    rd = raw_data or {}
    skeleton: dict | None = None
    # raw_data is keyed by tool name in chat_service-internal shape;
    # accept both {"propose_workflow": {steps...}} and the hoisted
    # {steps: [...]} shape (handle_stream hoists before this runs in
    # some paths). Check both.
    candidate = rd.get("propose_workflow")
    if isinstance(candidate, dict) and candidate.get("steps"):
        skeleton = candidate
    elif isinstance(rd.get("steps"), list):
        skeleton = rd  # type: ignore[assignment]
    else:
        # Other macro-draft tools (propose_threshold_order,
        # propose_scheduled_order, propose_basket_allocation) — they
        # also surface their steps[] under their tool key.
        for k, v in rd.items():
            if (k.startswith("propose_") and isinstance(v, dict)
                    and isinstance(v.get("steps"), list)):
                skeleton = v
                break
    if skeleton is None:
        return None
    if not _is_long_only_draft(skeleton):
        return None
    return (
        "\n\n**Heads up — this draft is not actually risk-neutral.** You "
        "asked for a hedged / market-neutral structure, but the agent "
        "above is a plain long-only buy. A true neutral or hedged "
        "expression for this view usually needs an offsetting leg — for "
        "rising-oil exposure that typically means a long upstream "
        "producer (ONGC, Oil India) paired with a short refiner/marketer "
        "(BPCL, IOC, HPCL — whose margins compress when crude rises), or "
        "a defined-risk **options** structure (a call spread on an "
        "energy ETF / producer, or a bull-call-spread sized to your "
        "loss budget). Want me to build the options version, or the "
        "long-producer / short-refiner pair instead?"
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

    # ── R4/F7: at-OPEN / at-CLOSE order ("buy at the open, book +3%").
    # This is FULLY supported via trigger.market_relative_time(anchor=
    # 'open'|'close'). The runtime_relative cue below contains "the open",
    # so an at-open BUILD that hit a validation failure (the LLM emitted a
    # malformed trigger config) used to fall into the BANNED 09:30-cron
    # downgrade text — capability theatre that contradicts the at-open
    # path the engine actually runs. Catch the at-open/close build FIRST
    # and offer the real rebuild, never the 09:30 downgrade.
    if _is_at_open_close_build(msg):
        anchor = "close" if re.search(r"\b(?:at|on|the)\s+close|closing", msg) else "open"
        return (
            f"I can do that — an at-{anchor} order is a real trigger "
            f"(market_relative_time, anchor='{anchor}'), not a 09:30 "
            "approximation. My last attempt produced an invalid step "
            "config, so the card didn't render. Say *'try again'* (or "
            f"restate it, e.g. *'buy 5 BAJAJ-AUTO at the {anchor} and sell "
            "at +3%'*) and I'll build the two-branch agent: entry at the "
            f"{anchor}, exit on the profit target. Registers for your "
            "confirmation — never auto-executed."
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


# Tools that CREATE/REGISTER/SCHEDULE/PLACE something. A failure here
# genuinely means "nothing was built" — the ambiguity-clarification
# framing (never claim success, ask the user to pick one interpretation)
# is correct. Every other tool that can land in this generic fallback
# (compare_performance, fetch_fundamentals, get_price_history, backtest_*
# read paths, suggest/build/critique_option_strategy, etc.) is a
# DATA/ANALYSIS call — nothing was ever going to be "created, saved,
# scheduled" for those, so that framing is a category error, and its
# "do NOT echo this back" instruction actively suppresses the one thing
# the user needs to hear (e.g. "TATAMOTORS data unavailable"). Treating
# every generic tool failure as user-side ambiguity is what produced
# fabricated "your request was ambiguous" replies for genuine backend/
# data failures — see [[project fixes philosophy: honest tool-error
# surfacing]] context. Keep this list to true action/persistence tools
# only; anything unlisted defaults to the honest data-failure framing.
_ACTION_TOOL_NAMES = {
    "propose_workflow", "backtest_workflow", "propose_dsl_workflow",
    "propose_scheduled_order", "propose_threshold_order",
    "propose_basket_allocation", "propose_holding_action",
    "register_workflow", "place_market_order", "place_limit_order",
    "place_order",
    "create_sip", "create_gtt_order", "cancel_order", "modify_order",
    "build_strategy",
}


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
    is_action = tool_name in _ACTION_TOOL_NAMES
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
        "place_order": (
            "register a buy/sell order (market, or limit when a price "
            "is given) for you to confirm in your broker app"
        ),
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
        (
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
        if is_action else
        "You are Pivot's chat assistant. The user asked for data/"
        "analysis (a lookup, comparison, or computation) and the "
        "underlying call FAILED — this is not about ambiguity, it is "
        "a real data/feed problem. Write a SHORT reply (1–3 sentences, "
        "max 70 words).\n\n"
        "You are seeing the FULL conversation and the actual internal "
        "failure reason below. Use it.\n\n"
        "CRITICAL HONESTY RULE: state the REAL reason in plain English "
        "— name the specific symbol/metric/window that failed if the "
        "internal reason names one (e.g. 'TATAMOTORS' price data isn't "
        "available from our feed right now'). NEVER invent an unrelated "
        "excuse like 'your request could mean a few things' or 'I need "
        "a time window' when the real reason is a data/feed gap — that "
        "is fabrication. If part of the request DID have working data "
        "(e.g. one of two symbols), offer to show that part now.\n\n"
        "RULES:\n"
        "  • Do not claim you produced a table/chart/answer — you "
        "didn't.\n"
        "  • No code-flavoured words: 'tool', 'function', 'parameter', "
        "'field', 'schema', 'exception', 'null'.\n"
        "  • Flowing prose, one to three sentences, no bullet points, "
        "no headers.\n"
        )
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
        + (
            f"Internal reason it didn't run (for your context only — do "
            f"NOT echo this back verbatim — use it to pick ONE ambiguity "
            f"to ask about, in plain English): "
            f"{error or 'request was ambiguous.'}\n\n"
            if is_action else
            f"The real reason this data call failed — PARAPHRASE this "
            f"honestly in plain English, naming the specific symbol/"
            f"metric/window it mentions: "
            f"{error or 'data unavailable.'}\n\n"
        )
        + "Write the reply now."
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


_MARKET_NOUNS = {
    "market", "markets", "nifty", "sensex", "banknifty", "index", "indices",
    "stocks", "economy", "sector", "sectors", "today", "the",
}


def _looks_like_named_company(user_message: str) -> Optional[str]:
    """Return a multi-word Proper-Noun company name the user typed, else None.

    Used to tell a genuine "named a company that didn't resolve" case apart
    from a broad market ask on the live-price failure path, so we can give an
    honest message instead of a misleading "feed momentarily unavailable".
    """
    if not user_message:
        return None
    cands = re.findall(
        r"\b([A-Z][A-Za-z&.\-]+(?:\s+[A-Z][A-Za-z&.\-]+){1,3})", user_message)
    for c in cands:
        if any(w.lower() in _MARKET_NOUNS for w in c.split()):
            continue
        return c.strip()
    return None


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


# Some tool failures are OUR bug (a DSL/step tree shape the grammar
# rejects), not a real gap in what the user told us — routing those to
# the LLM clarifier ("name ONE ambiguity") fabricates a fake question.
_INTERNAL_SHAPE_ERROR_RE = re.compile(
    r"tagged-union|validation\s+error\s+for|does not match any of the "
    r"expected tags|tree invalid|self-comparison|vacuous comparison|"
    r"unknown indicator\(s\)|tree depth \d+ exceeds|contradictory entry "
    r"condition|position['\"]?\s+leaf is only valid|input tag .{0,4} "
    r"found using|extra inputs are not permitted",
    re.IGNORECASE,
)


def _is_internal_shape_error(error: str) -> bool:
    """True when a tool's error string names an internal DSL/step-
    schema shape bug rather than a genuine gap in the user's request."""
    return bool(_INTERNAL_SHAPE_ERROR_RE.search(error or ""))


_INTERNAL_SHAPE_ERROR_REPLY = (
    "That didn't go through — not because anything in what you said was "
    "unclear, but because I hit an internal snag putting the automation "
    "together. Could you try again? If it still doesn't work, try "
    "breaking it into one condition at a time (e.g. just the profit "
    "target first) and I'll build that piece."
)


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
                        "trigger.indicator")
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
    if tool_name in {"place_market_order", "place_limit_order", "place_order"}:
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
                     "get_index_level", "get_market_data"}:
        # Extract the symbol the user mentioned for a specific message.
        # WHY this is careful: the prior blind first-uppercased-token
        # grab surfaced Hinglish filler ("ACTUALLY", "NAHI") as a
        # fake ticker and even reported a VALID, liquid NSE name (e.g.
        # TATAMOTORS) as "not found". We now (1) strip Hinglish/English
        # stopwords, (2) prefer a token that resolves against the
        # curated universe, and (3) only name a token the user actually
        # typed — never a filler word.
        sym = _extract_user_symbol(user_message)
        # A market-overview / index ask ("tell me about the market today",
        # "how's the market") has NO single ticker to name. The old reply
        # here — "give me an NSE ticker, Pivot covers NSE-listed equities
        # only" — was wrong on both counts: it treated a broad-market ask
        # as a failed single-stock quote, AND it claimed an NSE-only scope
        # that isn't true (Kite spans NSE + BSE + F&O; the yfinance
        # fallback still covers the indices). Treat a no-symbol failure as
        # a transient feed issue, not a user error.
        if sym is None:
            # Two very different cases land here: (a) a broad market ask with
            # no ticker ("how's the market"), and (b) the user named a company
            # by NAME that didn't resolve to a fetchable symbol ("tell me about
            # Snehaa Organics"). Claiming a transient "feed momentarily
            # unavailable, try again" is dishonest for (b) — the name simply
            # isn't a listed symbol we can pull. Give an honest message that
            # covers both without promising a retry will fix it.
            named = _looks_like_named_company(user_message)
            if named:
                return (
                    f"I couldn't find live market data for **{named}** — it may "
                    "be unlisted, delisted, a very small/SME name, or the name "
                    "may be slightly off. If it's listed, send its exact NSE "
                    "ticker (e.g. RELIANCE) and I'll pull the details."
                )
            return (
                "I couldn't pull a live market level just now. If you meant a "
                "specific stock or index, send its exact NSE ticker or name "
                "(e.g. NIFTY, RELIANCE) and I'll fetch it."
            )
        return (
            f"I couldn't pull price data for `{sym}` just now — double-"
            f"check the ticker, or try again in a moment if the quote "
            f"feed is momentarily unavailable."
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


def _backtest_headline(rd: dict) -> str | None:
    """Data-rich one-liner for a backtest widget: prefer the engine's own
    ``summary_text``, else assemble the headline metrics (return / trades /
    CAGR / max DD / Sharpe) from the result. ``None`` when nothing is present
    so the caller keeps its generic line. Stops the chat punting EVERY number
    to the chart."""
    if not isinstance(rd, dict):
        return None
    # The engine already writes a rich sentence — use it verbatim.
    for v in rd.values():
        if isinstance(v, dict):
            st = v.get("summary_text")
            if isinstance(st, str) and len(st.strip()) > 30:
                return st.strip()
    # Otherwise build from the metrics dict (top-level or nested).
    m = None
    for v in [rd, *rd.values()]:
        if isinstance(v, dict):
            cand = v if "total_return_pct" in v else v.get("metrics")
            if isinstance(cand, dict) and "total_return_pct" in cand:
                m = cand
                break
    if not m:
        return None
    parts = [f"{m['total_return_pct']:+.1f}% total return"]
    if m.get("n_trades") is not None:
        parts.append(f"{m['n_trades']} trade(s)")
    if m.get("cagr_pct") is not None:
        parts.append(f"CAGR {m['cagr_pct']:+.1f}%")
    if m.get("max_drawdown_pct") is not None:
        parts.append(f"max drawdown {m['max_drawdown_pct']:.1f}%")
    if m.get("sharpe") is not None:
        try:
            parts.append(f"Sharpe {float(m['sharpe']):.2f}")
        except (TypeError, ValueError):
            pass
    return (
        "Backtest result: " + ", ".join(parts)
        + ". Equity curve, signals and the full metric set are charted below."
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
    # Read tools (news / movers / index / quotes / fundamentals) render NO
    # card — there is no news card in the product. If the model's prose came
    # back empty here, a canned "see the card below" line is a LIE and reads
    # as broken. Return empty so the caller re-prompts the model for a real
    # answer (see the empty-narration reprompt) rather than shipping a stub.
    if "news" in tool_name:
        return ""
    if tool_name.startswith("get_") or tool_name.startswith("list_"):
        return ""
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
    # Workstream B: the DB-driven equity+gold basket card. Editable,
    # register-not-execute; ends with the not-advice disclaimer.
    "strategy_builder_card",
})


def _clarify_raw_data(guarded: "GuardedToolResult") -> dict:
    """raw_data block for a ``needs_clarification`` turn.

    When the clarification came from ``ask_user_dynamic`` (Workstream A), the
    executor stashed a ``clarify_card`` payload in ``guarded.data`` — surface it
    verbatim so the FE renders the paginated 'N of M' ClarifyCard (with its
    in-band ``session_slot_state``). Every other clarification keeps the legacy
    thin ``ask_user`` hint. The chat router hoists the nested ``_render_hint`` to
    the top level for the FE either way."""
    data = guarded.data if isinstance(guarded.data, dict) else {}
    if data.get("_render_hint") == "clarify_card":
        return dict(data)
    return {"_render_hint": "ask_user"}


def _fmt_inr(v) -> str:
    """₹ in Indian grouping; '—' on None."""
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    neg = n < 0
    s = f"{abs(n):,.0f}"
    # Convert thousands grouping to the Indian lakh/crore grouping.
    if "," in s:
        whole = s.replace(",", "")
        if len(whole) > 3:
            head, tail = whole[:-3], whole[-3:]
            import re as _re
            head = _re.sub(r"(?<=\d)(?=(\d\d)+$)", ",", head)
            s = f"{head},{tail}"
    return ("-₹" if neg else "₹") + s


def _find_option_payload(raw_data: dict, hint: str) -> dict | None:
    """Pull the option chain / strategy payload out of raw_data regardless
    of nesting (chat keys by tool name; the router hoists later)."""
    rd = raw_data or {}
    if rd.get("_render_hint") == hint:
        return rd
    for v in rd.values():
        if isinstance(v, dict):
            if v.get("_render_hint") == hint:
                return v
            inner = v.get("data") if isinstance(v.get("data"), dict) else None
            if inner and inner.get("_render_hint") == hint:
                return inner
    return None


def _fno_mandated_tables(raw_data: dict) -> str:
    """Engine-anchored markdown tables that MUST accompany every F&O card.

    Prompt pressure alone repeatedly failed to make the model render these,
    so we synthesise them deterministically from the digest the engine
    already produced — the numbers are quoted verbatim, never re-derived.

      * chain   → metrics table + OI-walls table (≥4 named strikes) + source
      * suggest → ≥2-row candidate comparison table
      * build/critique → per-leg type/side/strike/premium table; critique
        also gets the 2-row current-vs-alternative table + POP.
    """
    chunks: list[str] = []

    # ── Option chain ──────────────────────────────────────────────────
    chain = _find_option_payload(raw_data, "option_chain_card")
    if chain:
        und = chain.get("underlying", "")
        spot = chain.get("spot") or chain.get("forward")
        em = chain.get("expected_move") or {}
        rows = [
            ("Underlying", str(und)),
            ("Spot/forward", _fmt_inr(spot)),
            ("ATM strike", _fmt_inr(chain.get("atm_strike"))),
            ("Max pain", _fmt_inr(chain.get("max_pain"))),
            ("PCR (OI)", str(chain.get("pcr_oi") if chain.get("pcr_oi") is not None else "—")),
            ("PCR (volume)", str(chain.get("pcr_volume") if chain.get("pcr_volume") is not None else "—")),
        ]
        if em.get("low") is not None and em.get("high") is not None:
            # Quote the band VERBATIM from the digest — never re-derive.
            rows.append((
                "Expected move (1σ)",
                f"{_fmt_inr(em['low'])} – {_fmt_inr(em['high'])} (±{em.get('pct', '')}%)",
            ))
        tbl = ["| Metric | Value |", "| --- | --- |"]
        tbl += [f"| {k} | {v} |" for k, v in rows]
        chunks.append("\n".join(tbl))

        # OI walls — name ≥4 real strikes (top-3 call + top-3 put).
        tcall = chain.get("top_call_oi") or []
        tput = chain.get("top_put_oi") or []
        if tcall or tput:
            walls = ["| Side | Strike | Open interest |",
                     "| --- | --- | --- |"]
            for w in tcall:
                walls.append(f"| Call (resistance) | {_fmt_inr(w.get('strike'))} | {int(w.get('oi') or 0):,} |")
            for w in tput:
                walls.append(f"| Put (support) | {_fmt_inr(w.get('strike'))} | {int(w.get('oi') or 0):,} |")
            chunks.append("\n".join(walls))

        src = chain.get("source") or "—"
        asof = chain.get("asof") or ""
        chunks.append(f"Source: {src}; as of {asof}.")
        return "\n\n".join(chunks)

    # ── Strategy card (suggest / build / critique) ────────────────────
    strat = _find_option_payload(raw_data, "option_strategy_card")
    if strat:
        summ = strat.get("summary") or {}
        computed = strat.get("computed") or {}
        crit = strat.get("critique") or {}
        candidates = strat.get("candidates") or []

        # Suggest flow → candidate comparison table (≥2 rows).
        if candidates:
            from backend.services.option_strategies import humanize_strategy_key
            primary = {
                "label": humanize_strategy_key(summ.get("template")) or "Suggested",
                "pop": computed.get("pop"),
                "max_loss": computed.get("max_loss"),
                "max_profit": computed.get("max_profit"),
                "net_premium": computed.get("net_premium"),
            }
            rows_in = [primary] + candidates
            cmp_tbl = ["| Strategy | Max loss | Max profit | POP | Net premium |",
                       "| --- | --- | --- | --- | --- |"]
            for c in rows_in:
                ml = c.get("max_loss")
                mp = c.get("max_profit")
                pop = c.get("pop")
                cmp_tbl.append(
                    f"| {c.get('label') or humanize_strategy_key(c.get('template', ''))} "
                    f"| {'uncapped' if ml is None else _fmt_inr(ml)} "
                    f"| {'uncapped' if mp is None else _fmt_inr(mp)} "
                    f"| {f'{pop:.0%}' if pop is not None else '—'} "
                    f"| {_fmt_inr(c.get('net_premium'))} |"
                )
            chunks.append("\n".join(cmp_tbl))

        # Per-leg table (named build / critique).
        legs = (strat.get("editable") or {}).get("legs") or summ.get("legs") or []
        if legs:
            leg_tbl = ["| Leg | Type | Side | Strike | Premium |",
                       "| --- | --- | --- | --- | --- |"]
            for i, l in enumerate(legs, 1):
                leg_tbl.append(
                    f"| {i} | {l.get('option_type', '')} | {l.get('side', '')} "
                    f"| {_fmt_inr(l.get('strike'))} | {_fmt_inr(l.get('mid'))} |"
                )
            chunks.append("\n".join(leg_tbl))

        # Critique: 2-row current-vs-alternative + POP.
        comparison = crit.get("comparison") or []
        if comparison:
            ctbl = ["| Structure | Max loss | Max profit | POP |",
                    "| --- | --- | --- | --- |"]
            for c in comparison:
                ctbl.append(
                    f"| {c.get('structure', '')} | {c.get('max_loss', '')} "
                    f"| {c.get('max_profit', '')} | {c.get('pop', '')} |"
                )
            chunks.append("\n".join(ctbl))

        pop = computed.get("pop")
        if pop is not None:
            chunks.append(f"Probability of profit (market-implied): {pop:.1%}.")
        return "\n\n".join(chunks)

    return ""


def _ensure_widget_caption(
    text: str,
    *,
    tool_name: str,
    logiccard: dict | None,
    raw_data: dict,
    user_message: str = "",
) -> str:
    """Make sure assistant text accompanies any widget render.

    The chat pattern is `text + widget`, never `widget alone`. When the
    LLM:
      - emitted no text → synthesise one matching the widget kind.
      - emitted a single-word affirmation ("done", "okay") → upgrade
        to a descriptive line.
      - emitted a full sentence → leave it; the model already nailed it.

    Returns the (possibly-upgraded) text. Never empty.

    `user_message` (optional) is the original user prompt that triggered
    this turn. When supplied, this helper also runs the risk-neutral
    honesty guard: if the user asked for a hedged / neutral structure
    but the produced workflow_draft is plain long-only, a non-blocking
    disclosure is appended naming the mismatch and offering the nearest
    real thing (producers-vs-refiners pair, or an options structure).
    Default empty string is back-compat for callers that don't carry
    the user message in scope.
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

    # ── Unsupported-asset-class honesty for basket builds ──────────────
    # strategy_builder.build_strategy() computes real unsupported-asset
    # notes (crypto/US/commodity/bond asks it can't construct) into the
    # card's `assumptions`, but the deterministic zero-hop clarify-terminal
    # caller (a bare "Done — the result is shown below.") is long enough
    # to pass the too_terse check below unchanged, so those notes never
    # reached the visible reply — the user saw an equities-only basket
    # with no explanation. Force them into the text here, same pattern as
    # the F&O mandated-tables branch above, so it holds regardless of
    # which caller produced the caption. Skip if the text already covers
    # it (the LLM-narrated direct-build path already does this itself).
    if render_hint == "strategy_builder_card":
        cleaned = (text or "").strip() or _tool_summary_line(tool_name or "", None)
        card = rd.get(tool_name) if isinstance(rd.get(tool_name), dict) else next(
            (v for v in rd.values()
             if isinstance(v, dict) and v.get("_render_hint") == render_hint),
            {},
        )
        gaps = [
            a for a in (card.get("assumptions") or [])
            if isinstance(a, str) and "was NOT included in this basket" in a
        ]
        if gaps and "not included in this basket" not in cleaned.lower():
            asked_for = " and ".join(
                g.split(" too —", 1)[0].replace("you asked for ", "").strip()
                for g in gaps
            )
            note = (
                f"Note: {asked_for} — this builder only constructs NSE "
                "equities + gold, so that exposure was **not** included; "
                "register it separately or ask for a listed proxy."
            )
            cleaned = f"{cleaned}\n\n{note}"
        return _maybe_append_risk_neutral_disclosure(
            cleaned, user_message, rd, render_hint,
        )

    # ── F&O mandated tables ───────────────────────────────────────────
    # Option chain / strategy cards MUST ship engine-anchored markdown
    # tables (metrics + OI walls / candidate comparison / per-leg / the
    # critique current-vs-alternative). Prompt pressure failed twice, so
    # synthesise them from the digest and append when absent. Fires in
    # BOTH handle() and handle_stream() (both call this helper).
    if render_hint in {"option_chain_card", "option_strategy_card"}:
        cleaned = (text or "").strip()
        tables = _fno_mandated_tables(rd)
        if tables and "| --- |" not in cleaned:
            base = cleaned or _tool_summary_line(tool_name or "", None)
            result = f"{base}\n\n{tables}"
        else:
            result = cleaned or _tool_summary_line(tool_name or "", None)
        return _maybe_append_risk_neutral_disclosure(
            result, user_message, rd, render_hint,
        )

    if render_hint not in _WIDGET_RENDER_HINTS and not logiccard:
        return _maybe_append_risk_neutral_disclosure(
            text, user_message, rd, render_hint,
        )

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
        return _maybe_append_risk_neutral_disclosure(
            cleaned, user_message, rd, render_hint,
        )

    # Synthesise per-widget caption.
    if render_hint == "workflow_draft_card":
        skeleton = rd.get("propose_workflow")
        if not (isinstance(skeleton, dict) and skeleton.get("steps")):
            # threshold/dsl/scheduled/basket/holding drafts are keyed by their
            # OWN tool name, not 'propose_workflow' — find the draft skeleton
            # under any key so they get the SAME param-rich caption ("when
            # RSI(14) drops below 30, buy 10 INFY") instead of a generic line.
            skeleton = next(
                (v for v in rd.values()
                 if isinstance(v, dict) and v.get("steps")),
                rd,
            )
        if isinstance(skeleton, dict) and skeleton.get("steps"):
            result = _workflow_skeleton_caption(skeleton)
        else:
            result = _tool_summary_line("propose_workflow", None)
    elif render_hint == "indicator_backtest_chart":
        result = _backtest_headline(rd) or (
            "Here's the backtest — equity curve, signals, and headline "
            "metrics are in the chart below."
        )
    elif render_hint == "financial_backtest_chart":
        result = _backtest_headline(rd) or (
            "Here's the fundamentals backtest — performance vs. NIFTY and "
            "the rebalance trades are below."
        )
    elif logiccard or render_hint == "logic_card":
        result = _tool_summary_line(tool_name or "", logiccard)
    else:
        result = _tool_summary_line(tool_name or "", logiccard)
    return _maybe_append_risk_neutral_disclosure(
        result, user_message, rd, render_hint,
    )


def _maybe_append_risk_neutral_disclosure(
    text: str,
    user_message: str,
    raw_data: dict,
    render_hint: object,
) -> str:
    """Wrap `_risk_neutral_disclosure` with the render-hint gate so the
    honesty guard only fires when the user is about to see a workflow
    draft card (other widgets — option_chain, option_strategy, backtest
    charts — are NOT plain long-only directional asks and the disclosure
    would be noise). Non-blocking: appends to text, never replaces.
    """
    if not user_message:
        return text
    # The disclosure is specifically about the workflow_draft_card case.
    # Other card types either already express a hedged structure
    # (option_strategy_card) or don't carry a directional bet
    # (logic_card / backtest_chart).
    if render_hint != "workflow_draft_card":
        return text
    extra = _risk_neutral_disclosure(user_message, raw_data)
    if not extra:
        return text
    return text + extra


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
