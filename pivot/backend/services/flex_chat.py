"""The flexible chat engine: tools, a little context, and the model's judgement.

WHY THIS EXISTS (2026-09-24)
----------------------------
The legacy path (`ChatService.handle_stream`) runs ~40 regex detectors over the
user's message BEFORE the model sees it — intent classifiers, affirmative
detectors, capital parsers, amendment detectors — and each one can pin tool
choice, inject a directive, or skip the model entirely. Measured on 27 of them
against 19 ordinary messages: 13 changed their answer merely because the
router prefixed the message with page context, so "yes" stopped being a yes,
"cancel" stopped cancelling a questionnaire, and a fresh question stopped
evicting a stale draft. Worse, the portfolio value in that context was read as
"the user stated ₹6,59,094", injected into three prompt guards as a fact, and
used to force-resize an open draft to the whole book.

Those detectors were built to steer a weaker model. They now steer a strong one
into wrong answers it would not have given on its own. So this engine does the
opposite: it gives the model every tool, a short brief, and the context the
screen already knows — and lets it decide.

WHAT IS DETERMINISTIC HERE, AND WHY THAT IS NOT A CONTRADICTION
---------------------------------------------------------------
Nothing reads the user's message. The code that remains decides only things the
model cannot: argument validation against each tool's JSON schema (a bad call
goes BACK TO THE MODEL as an error to fix, never to the user as a scripted
question), which tool calls may safely run in parallel (DB sessions are not
concurrency-safe), and a round cap so a loop cannot spin forever.

The platform's non-negotiables live where they belong: in the brief (never
fabricate, analysis not advice, simulate not execute) and in the TOOLS, which
already register rather than execute and already refuse out-of-scope asks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse

from backend.llm.base import LLMMessage, ToolDef

logger = logging.getLogger(__name__)

# ── Knobs ─────────────────────────────────────────────────────────────────────

# Tool rounds per turn. Tools are withdrawn on the last one so the model must
# answer from what it already fetched rather than stop mid-thought.
MAX_ROUNDS = 6
# Reasoning tokens count against this, so it is generous on purpose: a budget
# that only fits the prose truncates the answer whenever the model thinks hard.
MAX_OUTPUT_TOKENS = 8000
# One tool call cannot hold the whole turn hostage (yfinance can hang).
TOOL_TIMEOUT_S = 60.0
# History sent back to the model, in messages (user + assistant).
HISTORY_MESSAGES = 12
# Byte-stable cache key: the brief + tool schema are identical every turn, so
# the ~26k-token prefix is served from the provider's prompt cache.
CACHE_KEY = "pivot-flex-v1"

EFFORT = (os.environ.get("FLEX_CHAT_EFFORT") or "medium").strip().lower()

# Tools the flexible engine does not offer, and why.
_WITHHELD = {
    # Every tool is already on the wire every turn, so a search for more is a
    # wasted round.
    "find_tool",
    # A scripted question card. The model can simply ask, in its own words.
    "ASK_USER",
    # DuckDuckGo instant answers + Wikipedia, whose description forbade news
    # and recent figures. The hosted web search below replaces it.
    "web_search_brief",
}

# The provider's own web search, on every turn (a constant tools array keeps
# the prefix cached). The model searches, opens pages and cites them inline as
# markdown links; the India location ranks Indian sources for Indian asks.
from backend.config import settings as _settings  # noqa: E402

HOSTED_TOOLS: Optional[list[dict]] = (
    [{"type": "web_search", "user_location": {
        "type": "approximate", "country": "IN", "timezone": "Asia/Kolkata"}}]
    if _settings.web_search_enabled else None
)

# Parameters that only steer the legacy engine, and whose docs describe its
# behaviour, not ours. `presentation='table'` told the model "a deterministic
# table renders verbatim and you write nothing more": legacy pasted its own
# table and skipped the model, flex doesn't, so the user got one sentence
# pointing at a table that never came (2026-09-24, a 100-row screen).
_LEGACY_ONLY_PARAMS = {"screen_fundamentals": {"presentation"}}

# Tools whose output is a draft the user will want to revise in the next turn.
# Stashed so the model can see exactly what it built — history is text-only,
# and "make it 20 shares" is unanswerable if the model cannot see the card.
_DRAFT_PREFIXES = ("propose_", "build_", "register_")

IST = timezone(timedelta(hours=5, minutes=30))


# ── The brief ─────────────────────────────────────────────────────────────────

# Written as targets rather than prohibitions. The charto chat rewrite
# (2026-09-23) measured what a wall of "never" does to a model's prose: 11 em
# dashes, 36 bullets and 60 bold spans across six answers, down to 0/0/12 once
# the prompt described good writing instead of forbidding bad writing. The few
# hard rules that remain are the platform's identity, and each says what to do.
BRIEF = """\
You are Pivot, an AI analyst for Indian markets serving traders and investors.
You answer with real data from your tools, and you can build, backtest and
paper-trade strategies.

## How to work
Use your tools freely. Call several in one round when a question needs several
facts; they run in parallel. Read what they return before you answer, and if a
result is thin or failed, try another tool or say what is missing. You decide
which tools a question needs; nothing has been pre-selected for you.

Every number you state comes from a tool result, a web page you searched, or
the context below. Pivot's tools come first for prices, fundamentals, filings
and scans. For what they don't hold (monthly sales, commentary, events, news),
search the web, preferring the company's own releases, NSE and BSE filings and
regulators over press. Cite each web fact where you use it: ([site](url)). Say
something is unavailable only after both come up empty. Prices, levels, ratios and dates are never
written from memory.
Kite is the primary market feed; when a result's `source` is anything else
(yfinance, a cache, seed data), say so in a short clause so the reader knows
how fresh the figure is.

## What Pivot is
Pivot covers NSE and BSE equities, the major indices (NIFTY 50, BANKNIFTY,
SENSEX), NSE options, MCX commodities and crypto. For anything outside that,
say so in a sentence and name the nearest listed instrument (US tech via
MON100, gold via GOLDBEES).

Orders and strategies are simulated: they fill in the user's paper book and
nothing reaches a broker. When the user wants to act, call the tool that
drafts it; the draft appears as a card they review and confirm themselves.
You give analysis and frameworks, not personal buy or sell advice, so close
any answer about a specific trade or holding with "This is analysis, not
financial advice."

## Cards
Some tools render a card beneath your reply (their result carries a
`_render_hint`). The user sees that card, so write the reading of it: what
matters, what it implies, what to watch. Leave the full table to the card.

## Writing the answer
Open with the answer itself, one or two sentences with the figure that
carries it in bold, so a reader who stops there still has it.

Then shape the rest so it can be scanned. An answer with several parts gets a
short `###` heading per part, and the heading says what the part holds ("Where
the growth is", "What to check before trusting it"), never a bare label like
"Analysis". Under a heading, parallel items (names with their figures, signals,
risks, things to watch) go in bullets: one idea each, the name or figure in
bold first, then a clause on why it matters. Keep prose for an argument that
connects facts, in short paragraphs. Use a markdown table to compare the same
fields across several items, unless a card already shows them. An analytical
answer ends with what to watch or check next.

Be specific: name the level, the percentage, the date. Match length to the
question: a quick fact gets a sentence or two and no headings; a request for
analysis gets the full shape.

Punctuate with commas, colons, semicolons, full stops and parentheses. Where a
dash would interrupt a sentence, use a comma or colon, or start a new sentence;
a dash belongs only in a numeric range (1,289-1,299).

Use a calm, professional voice and write as a sharp analyst would to a smart
client."""


# ── Context ───────────────────────────────────────────────────────────────────


def _now_line() -> str:
    now = datetime.now(IST)
    status = (
        "open" if now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) < (15, 30)
        else "closed"
    )
    return (
        f"Now: {now:%A %d %B %Y, %H:%M} IST. NSE/BSE regular session is "
        f"09:15-15:30 IST on weekdays; it is currently {status} by the clock "
        f"(a holiday would also close it)."
    )


def _draft_line(tool_name: str, draft: dict, workflow_id: Optional[str]) -> str:
    body = json.dumps(draft, default=str, separators=(",", ":"))[:6000]
    anchor = (
        f" It is an edit of saved agent {workflow_id}; keep that id so saving "
        f"updates the agent in place." if workflow_id else ""
    )
    return (
        f"## Draft in progress\nThe most recent draft in this conversation came "
        f"from `{tool_name}` with these arguments:\n{body}\n"
        f"If the user asks to change it, call `{tool_name}` again with every "
        f"argument carried over and only the change applied.{anchor}"
    )


def build_context(
    *,
    page_lines: list[str],
    attachment_lines: list[str],
    quoted_text: Optional[str],
    mode: Optional[str],
    editor_draft: Optional[dict],
    active_draft: Any,
) -> str:
    """The per-turn facts the screen already knows, as ONE system block.

    Kept apart from the user's message on purpose. The legacy router prefixed
    this onto the message itself, which is what made every downstream regex
    read the portfolio value as something the user had said.
    """
    parts = [_now_line()]
    if page_lines:
        parts.append("## Where the user is\n" + "\n".join(page_lines))
    if attachment_lines:
        parts.append(
            "## Tagged by the user\nThe user attached these to their message; "
            "'it' or 'this' most likely means them. Use their exact symbols or "
            "ids with tools.\n" + "\n".join(attachment_lines)
        )
    quote = (quoted_text or "").strip()
    if quote:
        excerpt = quote[:2000]
        parts.append(
            "## Replying to\nThe user selected this passage of an earlier "
            "answer and is replying to it:\n> " + excerpt.replace("\n", "\n> ")
        )
    if mode:
        what = {
            "agent": "build an automated strategy (an agent)",
            "automation": "set up an order or automation",
            "backtest": "backtest an idea",
        }.get(mode, mode)
        parts.append(f"## Mode\nThe user chose the '{mode}' chip: they want to {what}.")
    if editor_draft:
        parts.append(_draft_line(
            "propose_workflow", editor_draft, editor_draft.get("workflow_id"),
        ).replace("## Draft in progress", "## Open in the strategy editor"))
    elif active_draft is not None:
        parts.append(_draft_line(
            active_draft.tool_name, active_draft.draft,
            getattr(active_draft, "workflow_id", None),
        ))
    return "\n\n".join(parts)


# ── Tools ─────────────────────────────────────────────────────────────────────


# Tools the model loads when it needs them (the provider's hosted tool search).
# It always sees each group's one-line description; the definitions arrive
# only when it opens the group, in the same response. Research, screening,
# charts and the strategy builders stay loaded: most turns use them, and
# every tool always on the wire made the choice a 64-way one.
NAMESPACES: dict[str, tuple[str, tuple[str, ...]]] = {
    "orders": ("Place paper-book orders: market/limit, stop-loss, GTT, OCO, "
               "basket, price-threshold and scheduled orders.",
               ("place_order", "create_sl_order", "create_gtt_order",
                "create_oco_order", "place_basket_order",
                "propose_threshold_order", "propose_scheduled_order")),
    "order_book": ("See and manage open paper orders: list pending and GTT "
                   "orders, cancel them, square off positions.",
                   ("list_pending_orders", "list_gtt_orders", "cancel_order",
                    "cancel_gtt", "squareoff_symbol", "squareoff_all_intraday")),
    "automations": ("Recurring and conditional automations: SIPs, dip-buys, "
                    "rule-triggered orders, acting on an existing holding "
                    "(exit, stop-loss, trailing stop), and managing or checking "
                    "running automations and schedules.",
                    ("create_sip", "create_dip_buy", "create_strategy",
                     "propose_holding_action", "manage_automation",
                     "get_workflow_status", "register_workflow",
                     "get_scheduler_status", "list_upcoming_jobs")),
    "options": ("NSE options: option chains, strategy suggestions and builds "
                "with payoff and greeks, critiques, rolling positions, "
                "portfolio greeks.",
                ("get_option_chain", "suggest_option_strategy",
                 "build_option_strategy", "critique_option_strategy",
                 "roll_option_position", "get_portfolio_greeks")),
    "quant_research": ("Pairs and portfolio research: pair scans, "
                       "cointegration, pair and portfolio backtests, regime "
                       "comparisons, correlation matrices, comparing "
                       "backtests, basket allocation.",
                       ("scan_pairs", "test_cointegration", "backtest_pairs",
                        "regime_compare_metrics", "get_correlation_matrix",
                        "compare_backtests", "backtest_portfolio",
                        "propose_basket_allocation")),
    "ipos": ("IPOs: upcoming and open issues, and one IPO's details.",
             ("get_ipo", "list_upcoming_ipos")),
    "fixed_income": ("Fixed income and yield products: recommendations and "
                     "yield comparisons.",
                     ("get_yield_recommendation", "compare_yields")),
}


def _tool_surface() -> tuple[list[ToolDef], list[dict], dict[str, dict]]:
    """(always-loaded tools, namespace + tool_search wire dicts, every schema)."""
    from backend.llm.openai_client import _tools_to_responses_format

    all_tools = _tooldefs()
    schemas = {t.name: t.parameters for t in all_tools}
    by_name = {t.name: t for t in all_tools}
    deferred = {n for _, names in NAMESPACES.values() for n in names}
    wire: list[dict] = []
    for ns, (desc, names) in NAMESPACES.items():
        fns = _tools_to_responses_format([by_name[n] for n in names if n in by_name])
        if fns:
            wire.append({"type": "namespace", "name": ns, "description": desc,
                         "tools": [{**f, "defer_loading": True} for f in fns]})
    if wire:
        wire.append({"type": "tool_search"})
    return [t for t in all_tools if t.name not in deferred], wire, schemas


def _tooldefs() -> list[ToolDef]:
    from backend.services.chat_service import _registry_tools_as_tooldefs

    out: list[ToolDef] = []
    for t in _registry_tools_as_tooldefs(None):
        if t.name in _WITHHELD:
            continue
        drop = _LEGACY_ONLY_PARAMS.get(t.name)
        if drop:
            params = dict(t.parameters or {})
            params["properties"] = {k: v for k, v in (params.get("properties") or {}).items()
                                    if k not in drop}
            if "required" in params:
                params["required"] = [r for r in params["required"] if r not in drop]
            t = t.model_copy(update={"parameters": params})
        out.append(t)
    return out


def _parallel_safe() -> frozenset:
    # One list, owned by the legacy loop, so the two engines cannot disagree
    # about which handlers are pure reads.
    from backend.services.chat_service import _PARALLEL_READ_TOOLS

    return _PARALLEL_READ_TOOLS


def _card_of(data: Any) -> Optional[dict]:
    """The renderable payload in a tool result, top-level or one level down."""
    if not isinstance(data, dict):
        return None
    if data.get("_render_hint"):
        return data
    for v in data.values():
        if isinstance(v, dict) and v.get("_render_hint"):
            return v
    return None


async def _run_tool(
    name: str,
    args: dict,
    schemas: dict[str, dict],
    *,
    ctx: Any,
    own_session: bool,
) -> Any:
    """Validate, execute, and return a ToolResult. Never raises.

    Validation failures come back as an ordinary failed result, so the model
    reads the error and corrects its own call. That is the whole difference
    from `execute_with_completeness`, which turned a missing field into a
    scripted question addressed to the user.
    """
    from backend.services.tool_registry import ToolResult, execute
    from backend.services.validation_handler import _validate_args_against_schema

    if name not in schemas:
        return ToolResult(name=name, args=args, success=False, data={},
                          error=f"unknown tool '{name}'")
    err = _validate_args_against_schema(args, schemas[name])
    if err:
        return ToolResult(name=name, args=args, success=False, data={},
                          error=f"invalid arguments: {err}")

    async def _call(db: Any) -> Any:
        return await asyncio.wait_for(
            execute(name, args, kite_token=ctx.kite_token, db=db,
                    user_id=ctx.user_id),
            timeout=TOOL_TIMEOUT_S,
        )

    try:
        if not own_session:
            return await _call(ctx.db)

        # Parallel reads: each in its own thread, event loop and DB session.
        # The handlers do blocking network I/O inside async defs, so gathering
        # them on one loop would still serialise; threads give real overlap.
        def _in_thread() -> Any:
            from backend.database import SessionLocal

            session = SessionLocal()
            try:
                return asyncio.run(_call(session))
            finally:
                session.close()

        return await asyncio.to_thread(_in_thread)
    except asyncio.TimeoutError:
        return ToolResult(name=name, args=args, success=False, data={},
                          error=f"timed out after {TOOL_TIMEOUT_S:.0f}s")
    except Exception as exc:  # noqa: BLE001 — a tool failure is the model's to handle
        logger.exception("flex tool %s failed", name)
        return ToolResult(name=name, args=args, success=False, data={},
                          error=f"{type(exc).__name__}: {exc}"[:300])


def _parse_args(raw: str) -> tuple[dict, Optional[str]]:
    if not raw or not raw.strip():
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"arguments were not valid JSON ({exc.msg})"
    if not isinstance(parsed, dict):
        return {}, "arguments must be a JSON object"
    return parsed, None


_SUBJECT_KEYS = ("symbol", "underlying", "primary_symbol", "trigger_symbol",
                 "index", "name_or_symbol", "sector", "query")


def _subject(args: dict) -> str:
    """What a tool call is about, for the loader line ("Checking NIFTY 50").

    Read off the model's own arguments, never inferred: a call with none of
    these keys gets no subject and the loader says only what kind of work it is.
    """
    syms = args.get("symbols")
    if isinstance(syms, list) and syms and all(isinstance(x, str) for x in syms):
        names = [x.strip() for x in syms if x.strip()]
        if len(names) > 2:
            return f"{names[0]}, {names[1]} and {len(names) - 2} more"
        return " and ".join(names)
    a, b = args.get("symbol_a"), args.get("symbol_b")
    if isinstance(a, str) and isinstance(b, str) and a and b:
        return f"{a} and {b}"
    for key in _SUBJECT_KEYS:
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            v = " ".join(v.split())
            return v if len(v) <= 40 else v[:39].rstrip() + "…"
    return ""


# ── Errors ────────────────────────────────────────────────────────────────────


def _is_permanent(error: str) -> bool:
    """A request the provider rejected will be rejected again.

    The legacy loop retried these twice with 1.5s sleeps — seven seconds spent
    re-sending a request that 400'd on an unsupported parameter.
    """
    e = (error or "").lower()
    return any(s in e for s in (
        "invalid_request_error", "unsupported parameter", "400", "401", "403",
        "404", "deploymentnotfound", "content_filter",
    ))


# The provider sometimes cites with a private-use token instead of a link,
# "\ue200cite\ue202turn1search0\ue201", and sends the URL as an annotation
# on the same span. Unconverted, the token reached the reader as
# "citeturn1search0".
_CITE_TOKEN = re.compile("\ue200cite\ue202([^\ue201]*)\ue201")


def _with_links(text: str, annotations: list[dict]) -> str:
    """Each citation token becomes ([host](url)) from its annotation."""
    if "\ue200" not in text:
        return text
    by_span = {(a.get("start_index"), a.get("end_index")): a for a in annotations}
    queue = list(annotations)

    def link(m: "re.Match[str]") -> str:
        inner = m.group(1).strip()
        if inner.startswith("http"):        # the model wrote the URL itself
            url = inner
        else:
            a = by_span.get((m.start(), m.end())) or (queue.pop(0) if queue else None)
            url = (a or {}).get("url") or ""
        if not url:
            return ""
        host = re.sub(r"^www\.", "", urlparse(url).hostname or "")
        pad = "" if m.start() == 0 or text[m.start() - 1].isspace() else " "
        return f"{pad}([{host}]({url}))"

    return _CITE_TOKEN.sub(link, text)


FAILURE_TEXT = (
    "I couldn't reach the model to answer that just now. Please try again in "
    "a moment."
)


# ── The loop ──────────────────────────────────────────────────────────────────


async def stream_turn(
    *,
    message: str,
    history: list[dict],
    ctx: Any,
    conv_id: str,
    store: Any,
    client: Any,
    page_lines: list[str],
    attachment_lines: list[str],
    quoted_text: Optional[str] = None,
    mode: Optional[str] = None,
    editor_draft: Optional[dict] = None,
) -> AsyncIterator[dict]:
    """One chat turn, streamed as the same SSE events the FE already renders.

    `message` is exactly what the user typed. Nothing in here parses it.
    """
    from backend.llm.openai_client import stream_openai
    from backend.services.conversation_store import ActiveDraft

    started = time.monotonic()
    breakdown: dict[str, int] = {}
    yield {"type": "start"}

    # A fresh chat (empty history) must not inherit an earlier draft: cleared,
    # not merely ignored, so it cannot resurface on the second turn.
    active = None
    try:
        if history:
            active = store.get_active_draft(conv_id)
        else:
            store.clear_active_draft(conv_id)
    except Exception:  # noqa: BLE001 — a store blip is not a failed turn
        active = None
    anchor_wf_id = (editor_draft or {}).get("workflow_id") or (
        getattr(active, "workflow_id", None) if active is not None else None
    )

    tools, namespaced, schemas = _tool_surface()
    hosted = (HOSTED_TOOLS or []) + namespaced
    parallel_safe = _parallel_safe()

    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=BRIEF),
        LLMMessage(role="system", content=build_context(
            page_lines=page_lines, attachment_lines=attachment_lines,
            quoted_text=quoted_text, mode=mode, editor_draft=editor_draft,
            active_draft=active,
        )),
    ]
    for m in history[-HISTORY_MESSAGES:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append(LLMMessage(role=m["role"], content=m["content"]))
    messages.append(LLMMessage(role="user", content=message))

    tools_called: list[str] = []
    raw_data: dict[str, Any] = {}
    logiccard: Optional[dict] = None
    final_text = ""
    # After round 1 each round continues the stored response, sending only the
    # new tool outputs (messages[sent_from:]). Rebuilt from `messages` alone,
    # a round lost the pages its web searches had read: the model searched
    # again, computed, lost them again, and gave up after 10 searches (77 s).
    prev_id: Optional[str] = None
    resp_id: Optional[str] = None
    sent_from = 0

    for round_no in range(1, MAX_ROUNDS + 1):
        last_round = round_no == MAX_ROUNDS
        text_parts: list[str] = []
        calls: dict[str, dict] = {}
        error: Optional[str] = None
        truncated = False
        round_started = time.monotonic()
        annotations: list[dict] = []

        for attempt in (1, 2):
            text_parts, calls, error, truncated = [], {}, None, False
            annotations = []
            async for ev in stream_openai(
                client,
                messages=messages[sent_from:] if prev_id else messages,
                previous_response_id=prev_id,
                tools=tools,
                tool_choice="none" if last_round else "auto",
                max_output_tokens=MAX_OUTPUT_TOKENS,
                reasoning_effort=EFFORT,
                temperature=None,
                prompt_cache_key=CACHE_KEY,
                hosted_tools=hosted or None,
            ):
                et = ev.get("type")
                if et == "error":
                    error = ev.get("message") or "stream error"
                    break
                if et == "response.output_text.delta":
                    d = ev.get("delta") or ""
                    if d:
                        text_parts.append(d)
                        yield {"type": "delta", "text": d}
                elif et in ("response.output_item.added", "response.output_item.done") \
                        and (ev.get("item") or {}).get("type") == "web_search_call":
                    # The provider runs these itself; surface them so the
                    # loader says what is being looked up.
                    item = ev["item"]
                    action = item.get("action") or {}
                    if et.endswith("added"):
                        yield {"type": "tool_start", "name": "web_search"}
                    else:
                        tools_called.append("web_search")
                        q = action.get("query") or ""
                        yield {"type": "tool_done", "name": "web_search", "ok": True,
                               "error": None, **({"hint": q[:60]} if q else {})}
                elif et in ("response.output_item.added", "response.output_item.done"):
                    item = ev.get("item") or {}
                    if item.get("type") == "function_call" and item.get("id"):
                        slot = calls.setdefault(item["id"], {
                            "call_id": "", "name": "", "args": ""})
                        slot["call_id"] = item.get("call_id") or slot["call_id"]
                        slot["name"] = item.get("name") or slot["name"]
                        if item.get("arguments"):
                            slot["args"] = item["arguments"]
                elif et == "response.function_call_arguments.delta":
                    iid = ev.get("item_id") or ""
                    if iid:
                        calls.setdefault(iid, {"call_id": "", "name": "", "args": ""})
                        calls[iid]["args"] += ev.get("delta") or ""
                elif et == "response.output_text.annotation.added":
                    ann = ev.get("annotation") or {}
                    if ann.get("type") == "url_citation":
                        annotations.append(ann)
                elif et in ("response.completed", "response.incomplete"):
                    resp = ev.get("response") or {}
                    resp_id = resp.get("id") or resp_id
                    if (resp.get("incomplete_details") or {}).get("reason") == "max_output_tokens":
                        truncated = True
            # A stored response can be gone (expired, another region): send
            # the whole conversation instead, once.
            if error and attempt == 1 and prev_id and not text_parts and not calls:
                logger.warning("flex round %d: continuing %s failed, resending in full: %s",
                               round_no, prev_id, error[:200])
                prev_id = None
                continue
            # Retry only a transient fault, and only while nothing is on screen.
            if error and attempt == 1 and not text_parts and not calls \
                    and not _is_permanent(error):
                logger.warning("flex round %d transient error, retrying: %s",
                               round_no, error[:200])
                await asyncio.sleep(1.0)
                continue
            break

        breakdown[f"llm_round_{round_no}"] = int((time.monotonic() - round_started) * 1000)

        if error:
            logger.error("flex round %d failed: %s", round_no, error[:500])
            text = "".join(text_parts) or FAILURE_TEXT
            yield {"type": "error", "message": text}
            yield _done(text, tools_called, logiccard,
                        {**raw_data, "_llm_unavailable": True}, started, breakdown)
            return
        if truncated:
            logger.warning("flex round %d hit the %d-token ceiling", round_no,
                           MAX_OUTPUT_TOKENS)

        round_text = _with_links("".join(text_parts), annotations)
        if not calls:
            final_text = round_text
            break

        # ── Execute this round's tool calls ────────────────────────────────
        ordered = [c for c in calls.values() if c["name"]]
        parsed = [_parse_args(c["args"]) for c in ordered]
        for c, (args, _) in zip(ordered, parsed):
            ev = {"type": "tool_start", "name": c["name"]}
            if hint := _subject(args):
                ev["hint"] = hint
            yield ev
        messages.append(LLMMessage(
            role="assistant", content=round_text,
            tool_calls=[{"id": c["call_id"], "name": c["name"], "arguments": c["args"] or "{}"}
                        for c in ordered],
        ))
        # The stored response already holds these calls; the next round sends
        # only their outputs, appended from here.
        prev_id, sent_from = resp_id, len(messages)

        results: list[Any] = [None] * len(ordered)
        reads = [i for i, c in enumerate(ordered)
                 if c["name"] in parallel_safe and not parsed[i][1]]
        # Every pure read runs in its own thread, even alone. The handlers do
        # blocking I/O inside async defs: on the event loop, one slow screen
        # (60s) froze the whole server, the SSE stream went silent, and the
        # Next.js proxy's 30s idle timeout cut it. The turn died without a word.
        run_parallel = len(reads) >= 1

        async def _one(i: int, own: bool) -> None:
            args, perr = parsed[i]
            if perr:
                from backend.services.tool_registry import ToolResult
                results[i] = ToolResult(name=ordered[i]["name"], args={},
                                        success=False, data={}, error=perr)
                return
            t0 = time.monotonic()
            results[i] = await _run_tool(ordered[i]["name"], args, schemas,
                                         ctx=ctx, own_session=own)
            key = f"tool_{ordered[i]['name']}"
            breakdown[key] = breakdown.get(key, 0) + int((time.monotonic() - t0) * 1000)

        if run_parallel:
            await asyncio.gather(*[_one(i, True) for i in reads])
        for i in range(len(ordered)):
            if results[i] is None:
                await _one(i, False)

        for c, r in zip(ordered, results):
            tools_called.append(c["name"])
            # What the model was actually shown. Without this a wrong answer
            # cannot be pinned on the model or the data: on 2026-09-24 a live
            # market pulse omitted five NIFTY names that the same tool returned
            # a minute later, and there was no record of which had failed.
            logger.info("flex tool %s ok=%s args=%s -> %s", c["name"], r.success,
                        c["args"][:200], r.to_llm_string()[:1500])
            done_ev = {"type": "tool_done", "name": c["name"], "ok": bool(r.success),
                       "error": r.error}
            # A chart leads the reply: sent now, it draws while the text is
            # still being written instead of arriving with `done`.
            chart = _card_of(r.data) if r.success else None
            if chart is not None and chart.get("_render_hint") == "price_chart_card":
                done_ev["card"] = chart
            yield done_ev
            if r.success:
                if r.logiccard:
                    logiccard = r.logiccard
                card = _card_of(r.data)
                if card is not None:
                    if anchor_wf_id and card.get("_render_hint") == "workflow_draft_card":
                        # Save & activate branches on the card's workflow_id:
                        # present → update the agent in place, absent → create
                        # a duplicate. An edit session must carry it.
                        card.setdefault("workflow_id", anchor_wf_id)
                    raw_data[c["name"]] = r.data
                    if c["name"].startswith(_DRAFT_PREFIXES):
                        _stash_draft(store, conv_id, c["name"], r.args or {},
                                     anchor_wf_id, ActiveDraft)
            messages.append(LLMMessage(
                role="tool", tool_call_id=c["call_id"], name=c["name"],
                content=r.to_llm_string(),
            ))

        # Text the model wrote before calling tools was a preamble; the next
        # round writes the answer. Start that answer on its own paragraph.
        if round_text:
            yield {"type": "delta", "text": "\n\n"}

    yield _done(final_text, tools_called, logiccard, raw_data, started, breakdown)


def _stash_draft(store: Any, conv_id: str, tool_name: str, args: dict,
                 workflow_id: Optional[str], ActiveDraft: Any) -> None:
    try:
        symbol = ""
        for key in ("symbol", "tradingsymbol", "underlying"):
            if isinstance(args.get(key), str):
                symbol = args[key].upper()
                break
        store.set_active_draft(conv_id, ActiveDraft(
            tool_name=tool_name,
            draft=args,
            created_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            symbol=symbol,
            workflow_id=workflow_id,
        ))
    except Exception:  # noqa: BLE001 — losing the stash costs an amendment, not the turn
        logger.debug("flex draft stash failed", exc_info=True)


def _done(text: str, tools_called: list[str], logiccard: Optional[dict],
          raw_data: dict, started: float, breakdown: dict) -> dict:
    total = int((time.monotonic() - started) * 1000)
    breakdown["total"] = total
    return {
        "type": "done",
        "response": text,
        "tools_called": tools_called,
        "logiccard": logiccard,
        "raw_data": raw_data or None,
        "latency_ms": total,
        "latency_breakdown": breakdown,
    }
