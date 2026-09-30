"""Charto's client onto Pivot's automation engine.

Charto does not own a second strategy builder. Pivot already has one that
has been evaluated, hardened and argued with for months: a step registry of
~50 typed steps, a DSL tree translator, five proposal tools with a routing
order encoded in their own descriptions, two backtesters, and a validator
that refuses a draft the engine could not actually fire. This module lends
that engine to the chart's side chat and adds nothing of its own to it.

What this module DOES own is the seam, and the seam has three jobs.

**One event loop, not one per call.** Pivot's tools are async and some of
them hold clients bound to the loop that created them. ``asyncio.run`` per
dispatch would close that loop underneath a cached client and the second
call of a session would fail with a closed-loop error that looks like a
network fault. So the bridge starts one daemon loop and hands work to it.

**Honest availability.** Pivot is a separate deployment. When its package
cannot be imported the bridge says so once, in a form the caller can show a
user, and every later question gets the same cached answer rather than a
stack trace per turn. A mode that cannot work must not be offered.

**Alerts stay Charto's.** Pivot's ``propose_dsl_workflow`` REFUSES a
notify-only draft — alerts were deliberately removed from that chat surface
(0ae2ded). Charto's own alert engine persists rules, catches up on boot and
fires on the live tick. So the alert verbs route to Charto's ``set_alert``
and never to a Pivot workflow, which is both the honest answer and the
better one. The execution prompt says this in as many words.
"""
from __future__ import annotations

import asyncio
import copy
import json
import contextvars
import logging
import re
import sys
import threading
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

PIVOT_ROOT = Path(__file__).resolve().parents[2] / "pivot"

# The Pivot tools Charto's execution mode offers.
#
# Pivot's chat also carries four "macro" proposal tools — scheduled_order,
# threshold_order, basket_allocation, holding_action — and they are left OFF
# this surface deliberately. They exist there as a decode-latency win (a
# handful of flat typed fields instead of a steps[] array, ~7s → ~0.2s), and
# that flat shape is exactly what this deployment mishandles: it populates
# every declared optional with the schema's own `minimum`. A weekly SIP
# arrived as `quantity: 5` AND `notional_inr: 1` (rejected as mutually
# exclusive) carrying `sl_pct: 0.1` — a stop-loss nobody asked for, on a
# product whose first hard rule is to stay literal. Three retries re-sent
# byte-identical arguments.
#
# `propose_dsl_workflow` and `propose_workflow` express every shape the macros
# express, and neither has a top-level optional scalar to auto-fill: the DSL
# tool takes prose, and the general one takes steps[] whose configs the model
# has to mean. Correctness outranks decode latency in a builder mode where a
# turn already costs ~20s. This narrows CHARTO's surface only; Pivot's chat
# keeps its macros.
PIVOT_TOOLS: tuple[str, ...] = (
    # Build a rule
    "propose_dsl_workflow",
    "propose_workflow",
    # Test it
    "backtest_dsl_tree",
    "backtest_workflow",
    # Construct a basket rather than a rule — the other half of "strategy".
    # A trigger→action automation cannot express "own these eight names at
    # these weights", and that is what most people mean first.
    "build_strategy",
    # NO OPTION TOOLS. They were here, and with them on the wire "strategy"
    # collapsed to "option strategy": asked for something protected from the
    # downside, the builder's first move was to offer a conservative options
    # income structure or a put hedge — for a user who had said nothing about
    # derivatives and was looking at an equity chart. Charto's subject is the
    # chart, and the chart is a stock. Downside control here is a regime
    # filter, an exit, or how the basket is composed.
    #
    # Removing them from THIS list does three things at once, which is why
    # the list is the right place to do it: the tools leave the wire, the
    # option calibration examples leave with them (`_calibration_block`
    # filters on this tuple), and `_DB_TOOLS` empties — so execution mode no
    # longer needs Pivot's Postgres at all.
    # Relationships between instruments, which single-symbol rules cannot say
    # anything about: a pair's spread, a basket's cross-section, whether two
    # series are actually cointegrated or merely correlated last quarter.
    "backtest_pairs",
    "scan_pairs",
    "test_cointegration",
    "backtest_portfolio",
)

# Tools that read Pivot's Postgres. Everything else runs with `db=None`.
# Empty since the option tools left — they were the only three that needed the
# instrument master and the option universe. Kept as a seam rather than
# deleted: the next DB-backed tool goes here, and `_session` already reads it.
_DB_TOOLS: frozenset[str] = frozenset()

# Prompt modules from Pivot that govern the tools above. Deliberately NOT the
# whole 83KB system_core.md — that carries the market-analysis contract, the
# option surface and the news rules, none of which apply to a builder mode
# and all of which would cost tokens on every turn to say nothing.
#
# THREE MODULES LEFT THIS LIST, and the reason is the same for all three: they
# teach routes that do not exist here. Measured by grepping each module for
# tool names against this surface's wire —
#
#   sips.md         create_sip, propose_scheduled_order, register_workflow
#   order_sizing.md create_dip_buy, get_market_data, create_sip, ASK_USER
#   stoploss.md     create_sl_order, propose_holding_action, get_portfolio
#
# — not one of those eleven names is callable from execution mode. A worked
# example of a tool you do not have is worse than no example: it is a route
# the model will try, fail at, and then narrate. Together they were ~3.5 KB of
# prompt per turn spent teaching mistakes, and order_sizing.md's three ASK_USER
# references were part of what made this surface open with a question instead
# of an answer.
#
# What each of them said that was TRUE and general — size it rather than ask,
# don't preflight a read the tool does itself — the adapter above now says once,
# without naming a tool that is not on the wire.
PROMPT_MODULES: tuple[str, ...] = ("workflows", "backtest")

# Rules the modules assume but do not carry, sliced by HEADING out of the files
# that own them rather than copied. A copy would read identically today and
# drift the first time someone edits the contract, and a forked behavioural
# rule is worse than a missing one: it is wrong in a way that still looks
# authoritative.
#
# `system_core.md`'s interval rule is here because without it the builder
# over-asks: the first live prompt answered "buy 10 INFY when RSI < 30" with
# "which timeframe?" — the lowest-priority gap in the ask, and asking it buries
# the real one.
#
# `stoploss.md`'s fresh-buy section is here because it is the one part of that
# module whose tools ARE on this wire: a buy plus an exit referenced to the
# position's own fill is exactly the shape `strategies.py` runs, and it is
# built with `propose_dsl_workflow`, not with the holding tools the rest of
# that file is about.
#
# WHAT IS DELIBERATELY NOT HERE is the clarify-discipline section. It reads
# "'Build an agent for X' with no trigger and no size → call ASK_USER with 2-4
# tappable options", and that instruction is the largest single cause of this
# surface answering a request for work with a menu — measured: "buy the top 10
# stocks" returned four bullet points and zero tool calls. There is no ASK_USER
# tool here, so the model rendered the menu in prose and did nothing. The
# adapter's "Act, then ask" replaces it, and the two cannot both be true.
PROMPT_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("system_core.md", (
        "## Technical-indicator timeframe — bar-interval is never a blocking question",
    )),
    ("modules/stoploss.md", (
        "## No holding yet (fresh buy-entry workflow) — a SUPPORTED shape, BUILD it",
    )),
)

_ADAPTER = """
# Execution mode — Charto

You research, decide, and register. The user brings an objective; you find the
instruments, read them, form a view, and leave behind something the runtime can
run without you. Nothing you register is executed by a model — it is a frozen
manifest a dumb loop fills. Your judgement is spent once, in this turn, and
recorded, so spend it well: think about what the user is actually asking for
before you decide how to express it.

## Act, then ask

Do not open with a question. The user asked for work, and every value you might
ask for is one you can choose. Choose it, do the work, show the result, and put
any question LAST, as an offer to amend something that already exists ("I sized
it at X — say the word and I'll re-cut it at Y"). Ask first only when no answer
would let you proceed.

A qualitative objective — "defensive", "momentum", "quality", "protect the
downside" — is yours to interpret. Say in one line what you took it to mean,
then build that.

## Choosing how to express an idea

When the user gives an objective rather than an exact rule, there are many ways
to express it, and the first one that comes to mind is rarely the best. Write
several genuinely different candidates — different mechanisms, not one rule with
nudged numbers — each with an entry and an exit, and run them together with
`evaluate_strategies`. They are tested in parallel on the same window; the
results come back to you and are not shown to the user.

Then judge them against what the user asked for, not against raw return. "Protect
the downside even if the upside is small" is won by the candidate that cuts the
worst drawdown most for the least return given up versus holding; "momentum" by
return and consistency; a mean-reversion idea by hit rate and trade quality.
Weigh the trust verdict and trade count: a winner on four trades is a guess.
Every candidate is a trial, and the deflated Sharpe already charges for how many
you tried.

### When nothing fits yet

A first round that finds nothing is the start of the search, not its answer.
Go again, and keep going for up to three rounds in the turn:

1. Fix what is broken. A candidate with `flags` measured a mistranslated rule —
   reword it (plainer, one condition per clause) and include it again.
2. Read why the others missed. Too many whipsaw trades → a longer lookback, a
   confirmation, or a slower exit. Gave up the rebound → an exit that lets the
   position recover, or re-entry sooner. Did not cut the drawdown → the signal
   was too slow for the falls that mattered.
3. Change one thing at a time on the nearest misses, and add mechanisms you
   have not tried. A rule has parts to vary: the signal that says "stand aside"
   (trend, momentum over a lookback, a volatility regime, strength against the
   index, depth of the fall), the lookback, a second filter, and the exit
   (signal reversal, trailing stop, fixed stop, time stop).

Every candidate is a trial and the deflated Sharpe charges for each one, so a
wider search is honest only because it raises its own bar — say how many you
tried.

Then deliver. Build the candidate with the best trade-off for the objective —
for protection, the one that cut the drawdown most for the return it gave up —
as a draft, and say plainly what it costs ("cuts the worst fall from 27% to 19%
and gives up 12 points of return"), with its verdict. Only when no candidate
did MATERIALLY better than holding on what the user asked for (for protection,
a drawdown cut of a point or two is noise, not protection) do you build nothing: then
name the rounds, what each showed, and the nearest thing that would work, with
its number.

Build with `propose_dsl_workflow`, passing the entry and exit text exactly as
tested, so the rule on the card is the rule you tested. In the reply, name what
you tried, say why the winner won, and quote its numbers beside buy & hold.
Never present a weak result as a strong one.

When the user states an exact rule, build it as stated; do not substitute a
better one without asking.

## Backtests are on request

A backtest is shown when the user asks for one ("backtest it", "how would it have
done", "test this") or presses Backtest on the card. Do not run
`backtest_dsl_tree` or `backtest_workflow` unasked, and do not attach a backtest
to a build. `evaluate_strategies` is your own homework, not a backtest shown to
the user.

## What registration means

- `register_plan` — a SELECTION. Several instruments, or one with no condition:
  "buy the top 10", "put 3 lakh to work". Legs carry a size and a reason each.
- `save_strategy` — a RULE. One instrument watched by a condition tree, built by
  `propose_dsl_workflow` or `propose_workflow` first. Call it on the draft this
  conversation already produced; never rebuild the draft to save it.

Both are register-not-execute: nothing fills until the user presses Activate,
and then only into the simulated paper book. Register in the turn you decided;
a described plan is gone when the turn ends. Never claim something is saved,
armed, bought or running without a tool call that returned an id.

## Research

An answer about a company needs the company in it. `screen_universe` ranks the
stored universe on price features; `read_symbol` and `compare_symbols` price
names against each other and a benchmark; `explain_move` and `search_news` say
what has been happening; `get_results` dates the last earnings; `get_peers`
places a name in its industry. Quote what you read, with its date. Research as
much as the decision needs and no more.

## Boundaries

The book is SIMULATED and long-only; no order reaches a broker from here. The
card says so — do not append "this is only a draft" or "review before arming".

Alerts are Charto's: "tell me when", "alert me" → `set_alert`.

Options are not on this surface. Downside control is a regime filter, an exit
(`unrealised_pct`, `peak_unrealised_pct`, `drawdown_from_peak_pct`, `bars_held`
are the stop, the trailing stop and the time stop), position sizing, or how a
basket is composed.

Scope is India: NSE/BSE equities, indices, MCX commodities, crypto. Out of scope,
name the nearest listed proxy with a number.

A reply that selects or recommends instruments ends with "…this is analysis, not
financial advice."

## The numbers

Every figure you state comes from a tool result or the instrument line. Never
invent a price, level, weight or date; if a tool returns nothing, say it is
unavailable.

Returns are the RULE's: fully invested whenever it holds a position, so they
compare directly with buy & hold over the same window, and a rupee figure is
capital × return. Do not do arithmetic a tool does for you: send `weight_pct`
and `capital_inr`, and send conditions in English to `propose_dsl_workflow`.
A size the user did not give is yours to choose — state it. Fill no other field
the ask does not imply: an unrequested stop-loss is a parameter you invented.

The instrument in the composer is the default subject and nothing more. A symbol
the user names wins, and any stored symbol is readable.

When a tool returns an error, read it — it names the missing field or the better
tool. Fix it and call again rather than narrating the failure.
""".strip()


# ── The lent loop ────────────────────────────────────────────────────
#
# Started on first use, never stopped. A daemon thread dies with the process,
# which is the right lifetime for something whose only job is to be available.

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        loop = asyncio.new_event_loop()
        threading.Thread(
            target=loop.run_forever, name="pivot-bridge-loop", daemon=True,
        ).start()
        _loop = loop
        return loop


# ── Import, once, with the answer cached either way ──────────────────

_import_lock = threading.Lock()
_state: dict[str, Any] = {"tried": False, "ok": False, "error": "", "mods": None}


def _ensure_pivot() -> dict[str, Any]:
    """Import Pivot's engine. Caches success AND failure — a mode that is
    unavailable stays unavailable until the process restarts, and asking
    again every turn would only repeat a 2-second import failure."""
    with _import_lock:
        if _state["tried"]:
            return _state
        _state["tried"] = True
        root = str(PIVOT_ROOT)
        try:
            if not PIVOT_ROOT.is_dir():
                raise ImportError(f"Pivot backend not found at {root}")
            if root not in sys.path:
                sys.path.insert(0, root)
            from backend.agents.tools import ALL_TOOLS
            from backend.prompts import assembler
            from backend.services import tool_registry
            from backend.workflows.registry import STEP_REGISTRY
            _state["mods"] = {
                "ALL_TOOLS": ALL_TOOLS, "assembler": assembler,
                "tool_registry": tool_registry, "STEP_REGISTRY": STEP_REGISTRY,
            }
            _state["ok"] = True
            # Additive, once, behind the same guard as the import: both touch
            # Pivot's modules and both are meaningless without them.
            _register_drawing_leaf()
            _patch_translator()
            _patch_fetcher()
        except Exception as exc:  # noqa: BLE001 — the reason is the payload
            _state["error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("execution mode unavailable: %s", _state["error"])
        return _state


def available() -> tuple[bool, str]:
    """(ready, reason). The reason is user-showable when ready is False."""
    st = _ensure_pivot()
    if st["ok"]:
        return True, ""
    return False, (
        "Execution mode needs Pivot's automation engine, which this server "
        f"could not load ({st['error']})."
    )


# ── The model-facing surface ─────────────────────────────────────────


# A clause in Pivot's own tool schema that instructs the opposite of this
# surface's contract, at the one place a model is most likely to obey it.
#
# `propose_dsl_workflow`'s `interval` parameter ends: "If user did NOT pin a
# timeframe, ASK — do not guess." That is right in Pivot's chat, which has
# `ask_user_dynamic` and a clarify card to ask WITH. Here it is wrong three
# times over: there is no clarify tool on this wire, the adapter's first rule
# is never to open with a question, and `_execution_context` has already told
# the model the composer's interval — so the value it would be asking for is
# sitting in its context.
#
# It is also winning the argument. Two prompt-level rules say never ask (the
# borrowed `system_core.md` slice, 411 tokens, and the context block's own
# line, 30 tokens), but both sit ~9k tokens from the decision while this one
# is attached to the argument being filled in. The measured failure is "buy 10
# INFY when RSI < 30" answered with "which timeframe?" — which costs a visible
# round plus, on the rebuild, two more hidden translation hops inside
# `propose_dsl_workflow` itself. Call it 15-30s to ask a question whose answer
# was already on screen.
#
# Corrected HERE rather than in Pivot's file for the same reason
# `_fix_borrowed_warnings` exists: Pivot's copy is right about Pivot, and the
# seam is the only place that knows which surface a description is bound for.
# The FACTS in the sentence (what the interval governs, that `period` counts
# bars of it) are untouched; only the verb changes.
_ASK_INTERVAL = "If user did NOT pin a timeframe, ASK — do not guess. "
_USE_INTERVAL = ("When the user pinned no timeframe, use the interval the "
                 "composer is on (it is named in your context) and say which "
                 "you used — never ask. ")


# A second borrowed instruction that is inert here, in a way that is invisible
# unless you check it against the wire.
#
# `build_strategy`'s description carries a self-sufficiency clause: "do NOT
# pre-call screen_fundamentals / fetch_fundamentals / compare_performance /
# compute". Every one of those four is a PIVOT tool, and not one of them is on
# Charto's surface — so the sentence forbids four things the model cannot do
# and is silent about the one screener it CAN reach, `screen_universe`.
#
# Measured: 4/4 named tools absent from `_tools_for_request()`, `screen_universe`
# present and unmentioned. So the clause cannot fire as written.
#
# What it costs is unproven and stated as such. I have seen one turn go
# `screen_universe` -> `build_strategy` (the screen's result unused, a whole
# round at 8-21s) and one go straight to `build_strategy` with no pre-call, and
# the profiling run never invoked `build_strategy` at all. So this is a latent
# defect fixed because it is provably inert and the fix is nearly free, NOT
# because a measured round is riding on it. If it turns out the model reliably
# pre-screens, this becomes a real saving; if not, it costs nothing.
_NO_PRESCREEN_OLD = ("do NOT pre-call screen_fundamentals / fetch_fundamentals "
                     "/ compare_performance / compute.")
_NO_PRESCREEN_NEW = ("do NOT pre-call a screener — on this surface that means "
                     "`screen_universe`, whose ranking this tool does not read. "
                     "It builds its own universe.")


def _retarget(defn: dict) -> dict:
    """Rewrite a borrowed tool description that contradicts this surface.

    Returns a copy; the registry's own dict is never mutated, because Pivot's
    chat reads the same object in-process.
    """
    fn = dict(defn)
    # The top-level description first: `build_strategy`'s self-sufficiency
    # clause lives there, not on a parameter.
    desc = fn.get("description")
    if isinstance(desc, str) and _NO_PRESCREEN_OLD in desc:
        fn["description"] = desc.replace(_NO_PRESCREEN_OLD, _NO_PRESCREEN_NEW)
    params = fn.get("parameters")
    if not isinstance(params, dict):
        return fn
    props = params.get("properties")
    if not isinstance(props, dict):
        return fn
    out = dict(props)
    changed = False
    for key, spec in props.items():
        desc = (spec or {}).get("description") if isinstance(spec, dict) else None
        if isinstance(desc, str) and _ASK_INTERVAL in desc:
            spec = dict(spec)
            spec["description"] = desc.replace(_ASK_INTERVAL, _USE_INTERVAL)
            out[key] = spec
            changed = True
    if changed:
        fn["parameters"] = {**params, "properties": out}
    return fn


# ── The clarify gate that is not on this wire ────────────────────────
#
# `_ADAPTER` opens with "NEVER open with a question", and the prompt modules
# were already trimmed of their clarify sections. The instruction survived
# anyway, in the one place nobody re-read: the borrowed tool DEFINITIONS.
#
#   propose_dsl_workflow .parameters.properties.quantity.description
#       "If user didn't state a size, call ASK_USER first — DO NOT emit this
#        tool until they answer."
#   propose_workflow .description
#       "'buy some X' → ASK_USER."
#
# A parameter doc sits closer to the call than any system prompt, so it wins
# the disagreement. Measured 2026-09-21: a fully specified rule followed by an
# explicit "arm it on paper" produced three prose questions and zero tool
# calls, and reproduced identically on retest. There is no ASK_USER on this
# wire, so the model renders the question as prose and emits nothing — the
# whole commit path stops, and the run saved zero strategies.
#
# Both sentences are correct about PIVOT, where ASK_USER exists and a draft
# places a real broker order ("silent defaults have produced wrong-size
# trades"). Neither is correct here: nothing fills until the user presses
# Activate on the card, and then only into the simulated paper book. A
# wrong-size paper trade costs nothing and is on screen to amend.
#
# So the sentence that ROUTES to the missing tool goes and the sentence that
# FORBIDS it stays — same rule `_calibration_block` already applies to the
# worked examples, and the same reason `_fix_borrowed_warnings` exists.
_ASK = "ASK_USER"


def _unask(text: str) -> str:
    """Drop sentences routing to ASK_USER; keep the ones prohibiting it."""
    if _ASK not in text:
        return text
    kept = [s for s in re.split(r"(?<=[.\n])\s+", text)
            if _ASK not in s or re.search(r"\bdo\s*not\b|\bnever\b", s, re.I)]
    return " ".join(kept).strip()


# The size is the MODEL's to choose, not this seam's. A fixed 10 used to be
# injected here whenever a buy arrived without one, which was a parameter
# nobody chose: 10 shares of a ₹40 stock and of a ₹40,000 one are not the same
# decision. Pivot's validator still refuses a buy with no quantity; its error
# now tells the model to choose one and say so, which costs a round only when
# the model skipped the instruction below.

_QTY_DOC = (
    "Shares to buy, for buy_market / buy_limit. If the user did not state a "
    "size, choose one that fits the idea and the instrument's price, pass it, "
    "and say in the reply what you chose — the user can amend it next turn. "
    "Never withhold the draft over it."
)


def _unblock_sizing(fn: dict) -> dict:
    """Rewrite the borrowed clarify routes out of one tool definition.

    COPIES before rewriting, for the reason `_retarget` gives: Pivot's chat
    reads the same registry objects in-process, and `_retarget` only copies the
    property dicts it actually changed — every other one is still the
    registry's own. Mutating `quantity` in place therefore edited
    `ALL_TOOLS`, which is inert only while Pivot's chat is a separate process
    and stops being inert at roadmap step 2.
    """
    fn["description"] = _unask(fn.get("description", ""))
    params = fn.get("parameters")
    props = (params or {}).get("properties")
    qty = (props or {}).get("quantity")
    if isinstance(qty, dict) and "description" in qty:
        fn["parameters"] = {**params, "properties": {
            **props, "quantity": {**qty, "description": _QTY_DOC}}}
    return fn


# ── The user's own drawings, as something a rule can be written against ──
#
# A trendline someone drew is a live object: it has a stable ref, it is
# persisted in the same `workspace_state` row the chart autosaves, and dragging
# it moves the level. `alerts.py` has been able to watch one since it was
# written (`draw:D7`, extrapolated past the second anchor). The STRATEGY
# runtime could not, because the DSL only speaks through the five accessor
# methods and none of them says "a line I drew".
#
# Three registrations make it addressable, all into dicts Pivot's own registry
# exposes for exactly this — `backtest_indicators` documents the first as the
# way "newly-registered indicators are accepted without code edits in the
# validator". Nothing in Pivot's source is forked; `charto` adds a key.
#
# `compute` refuses rather than returning a series. A backtest reaches the
# registry (live evaluation is intercepted in `ChartoDataAccessor` before it
# ever gets here), and there is no user, no symbol and no workspace behind a
# backtest call — so the honest answer is a named boundary, not a quietly
# empty series that would backtest as "never fired".
_DRAW_EDGES = ("bot", "mid", "top")


def _no_backtest(_bars, _n):
    raise ValueError(
        "A rule anchored to one of your drawings cannot be backtested yet: the "
        "backtester replays bars, and a drawing is geometry you placed on the "
        "chart rather than a series computed from them. The rule arms and "
        "fires live; say that plainly instead of testing something else.")


def _register_drawing_leaf() -> None:
    """Teach Pivot's indicator registry the `drawing` leaf. Idempotent."""
    try:
        from backend.services import backtest_indicators as bi
    except Exception as exc:                                # noqa: BLE001
        logger.warning("drawing leaf unavailable: %s", exc)
        return
    if "drawing" in bi._REGISTRY:
        return
    bi._register(bi.IndicatorSpec(
        key="drawing", label="Chart drawing", basis="price",
        default_period=1, compute=_no_backtest))
    # `component` carries WHICH price of a multi-price drawing — a rectangle
    # has three. A line takes no component, and the validator's own message
    # tells the model so.
    bi._INDICATOR_COMPONENT_PREFIX["drawing"] = {e: e for e in _DRAW_EDGES}
    # `ref` is the D-number. Refs are minted "D" + an integer and never
    # recycled, so the integer alone is the whole identity.
    bi._INDICATOR_SETTING_RULES["drawing"] = {"ref": (int, 1, 999999)}


# What the translator is told, and only when there is something to tell.
#
# `propose_dsl_workflow` takes NATURAL LANGUAGE and hands it to a second model
# to turn into a tree, so a leaf the translator has never heard of is a leaf it
# will not emit. Pivot already extends that prompt conditionally — a default
# symbol, an exit-grammar permission — and this follows it exactly: nothing is
# added when the user has no drawings on the symbol, and what IS added is the
# list of their actual refs, so the translator picks from real geometry instead
# of inventing a ref.
_TURN_DRAWINGS: contextvars.ContextVar[str] = contextvars.ContextVar(
    "charto_turn_drawings", default="")

_DRAW_GRAMMAR = """

DRAWINGS THE USER HAS ON THIS CHART — they may be referenced directly:
{rows}
Emit one as an indicator leaf: {{"type":"indicator","indicator":"drawing",
"symbol":"<ticker>","period":1,"settings":{{"ref":<the D number>}}}} — and for a
rectangle add "component":"top"|"bot"|"mid" to pick an edge. It resolves to that
drawing's price AT THE BAR BEING EVALUATED, so a sloped line's level moves with
time and moves again if the user drags it. A zone is TWO comparisons, one per
edge. Use one ONLY when the user's words point at a drawing ("my trendline",
"the line I drew", "that zone", "above D3"); never invent a ref that is not
listed above."""


def _drawing_hint(symbol: str, uid: int) -> str:
    """The grammar block for this user's drawings on this symbol, or ''."""
    if not uid or not symbol:
        return ""
    try:
        import alerts
        mine = alerts._drawings_of(symbol.upper(), int(uid))
    except Exception:                                       # noqa: BLE001
        return ""
    rows = []
    for ref, d in sorted(mine.items()):
        # `_drawings_of` keys by ref AND by id so either resolves, which means
        # one drawing can appear twice here. Advertising both would offer the
        # model a choice between two names for the same line, so only the ref
        # — the handle the chart itself shows — is listed.
        if ref != str(d.get("ref") or "").upper():
            continue
        num = "".join(ch for ch in ref if ch.isdigit())
        if not num:
            continue
        kind = str(d.get("type") or "drawing")
        edges = " (a zone: has top/bot/mid)" if kind in (
            "rect", "priceRange") else ""
        rows.append(f"  {ref} — a {kind}, ref {num}{edges}")
    return _DRAW_GRAMMAR.format(rows="\n".join(rows)) if rows else ""


_TRANSLATIONS: "OrderedDict[tuple, Any]" = OrderedDict()
_TRANSLATIONS_MAX = 512


def _patch_translator() -> None:
    """Append the drawing grammar to the NL→tree translator's prompt.

    Wrapped rather than edited: Pivot's prompt is right for Pivot, where there
    is no chart and no drawings. Idempotent.
    """
    try:
        from backend.workflows.dsl import llm_translate as lt
    except Exception as exc:                                # noqa: BLE001
        logger.warning("translator hint unavailable: %s", exc)
        return
    if getattr(lt, "_charto_wrapped", False):
        return
    inner = lt.translate_condition_to_tree

    base = lt.SYSTEM_PROMPT

    async def wrapped(condition, **kw):
        # THE SAME ENGLISH IS THE SAME TREE. The translator is a model call,
        # so the same sentence could come back as two different trees — and
        # the builder translates the entry that `evaluate_strategies` already
        # translated and tested. Without this, the rule on the card could be
        # a different parse of the text whose numbers the reply just quoted.
        # Keyed on everything that shapes the parse; `cache_key` only names a
        # provider-side prompt cache and does not.
        hint = _TURN_DRAWINGS.get("")
        key = (str(condition).strip(), hint, tuple(sorted(
            (k, repr(v)) for k, v in kw.items() if k != "cache_key")))
        hit = _TRANSLATIONS.get(key)
        if hit is not None:
            _TRANSLATIONS.move_to_end(key)
            return copy.deepcopy(hit)
        # `inner` reads the module-level SYSTEM_PROMPT synchronously, before
        # its first await, so no other turn can run between this assignment
        # and that read on a single-threaded loop. Restored either way so a
        # turn without drawings never inherits a previous turn's grammar.
        lt.SYSTEM_PROMPT = base + hint
        try:
            out = await inner(condition, **kw)
        finally:
            lt.SYSTEM_PROMPT = base
        _TRANSLATIONS[key] = copy.deepcopy(out)
        while len(_TRANSLATIONS) > _TRANSLATIONS_MAX:
            _TRANSLATIONS.popitem(last=False)
        return out

    lt.translate_condition_to_tree = wrapped
    lt._charto_wrapped = True


# ── One fetch per window, however many candidates read it ─────────────
#
# `evaluate_strategies` runs its candidates at once, and each one loads its
# own bars. Twelve simultaneous identical requests against Kite's historical
# endpoint (a few requests a second, stricter on daily) is how some of them
# end up rate-limited and falling through to yfinance — candidates judged
# against each other on DIFFERENT data, with nothing in the result saying so.
# So the fetcher is memoised by (symbol, window, interval) with a per-key
# lock: the first caller fetches, the rest wait for it and read the same
# frame. A short TTL keeps a live session from reading yesterday's bars.
_BARS: "OrderedDict[tuple, tuple[float, Any]]" = OrderedDict()
_BARS_LOCKS: dict[tuple, threading.Lock] = {}
_BARS_GUARD = threading.Lock()
_BARS_TTL_S = 300.0
_BARS_MAX = 64


def _patch_fetcher() -> None:
    """Memoise the DSL engine's OHLCV fetcher. Idempotent."""
    try:
        from backend.backtester import engine as be
    except Exception as exc:                                # noqa: BLE001
        logger.warning("bar memo unavailable: %s", exc)
        return
    if getattr(be, "_charto_memo", False):
        return
    inner = be._fetch_ohlcv

    def fetch(symbol, start, end, *, interval="1d"):
        import time
        key = (str(symbol).upper(), str(start), str(end), str(interval))
        with _BARS_GUARD:
            lock = _BARS_LOCKS.setdefault(key, threading.Lock())
        with lock:
            hit = _BARS.get(key)
            if hit is not None and time.monotonic() - hit[0] < _BARS_TTL_S:
                return hit[1].copy()
            df = inner(symbol, start, end, interval=interval)
            with _BARS_GUARD:
                _BARS[key] = (time.monotonic(), df)
                _BARS.move_to_end(key)
                while len(_BARS) > _BARS_MAX:
                    old, _ = _BARS.popitem(last=False)
                    _BARS_LOCKS.pop(old, None)
            return df.copy()

    be._fetch_ohlcv = fetch
    be._charto_memo = True


# ── Tools this surface owns ──────────────────────────────────────────
#
# `evaluate_strategies` is the builder's homework: several candidate ways of
# expressing one idea, tested side by side so the model can choose between
# them. The candidates are the model's — no menu of strategy families lives
# here, because a list of the constructions we thought of is a ceiling on the
# ones it can. It reuses `backtest_dsl_tree` whole (translation, engine, the
# trust battery) and adds only the parallelism, the shared trial count and a
# compact result. It renders nothing: its numbers are for the judgement, and
# the user sees the one that won, on the card the builder makes.
_MAX_CANDIDATES = 12

OWN_TOOLS: dict[str, dict] = {
    "evaluate_strategies": {
        "type": "function",
        "name": "evaluate_strategies",
        "description": (
            "Test several candidate rules for one instrument side by side, in "
            "parallel, on the same window — the numbers come back to YOU to "
            "judge and nothing is shown to the user. Use it when deciding HOW "
            "to express an objective: write the candidates yourself, as many "
            "genuinely different mechanisms as the idea deserves, each with an "
            "entry and an exit. Then choose the one that best serves what the "
            "user asked for and build it with propose_dsl_workflow using the "
            "SAME entry and exit text. Candidates differ by their rules only: "
            "each is measured fully invested while in a position — the way "
            "the paper runtime arms it — so returns compare directly with buy "
            "& hold over the same window. Every candidate counts as a trial "
            "in its deflated Sharpe, across calls. When nothing fits, call it "
            "again with a new round — broken candidates reworded, near misses "
            "varied, mechanisms not yet tried."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "primary_symbol": {
                    "type": "string",
                    "description": "The instrument every candidate trades."},
                "candidates": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": _MAX_CANDIDATES,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description":
                                     "A short label you will refer to it by."},
                            "entry": {"type": "string", "description":
                                      "Entry condition in plain English."},
                            "exit": {"type": "string", "description":
                                     "Exit condition in plain English — a "
                                     "reversal, a stop, a trailing stop, a "
                                     "time stop, or 'hold to the end'."},
                        },
                        "required": ["name", "entry", "exit"],
                    },
                },
                "interval": {
                    "type": "string",
                    "description": ("Bar interval every candidate runs on. "
                                    "The composer's interval unless the idea "
                                    "is about another timeframe.")},
                "start_date": {"type": "string", "description":
                               "Optional ISO date; default five years back."},
                "end_date": {"type": "string", "description":
                             "Optional ISO date; default today."},
                "starting_capital": {"type": "number", "description":
                                     "₹ the rupee figures are expressed in; "
                                     "the returns do not depend on it."},
            },
            "required": ["primary_symbol", "candidates", "interval"],
        },
    },
}


def _pct(v: Any, nd: int = 2) -> Optional[float]:
    return round(float(v), nd) if isinstance(v, (int, float)) else None


async def _evaluate_strategies(args: dict) -> dict:
    """Run every candidate through `backtest_dsl_tree` at once, then score
    each at the FINAL trial count and hand back one compact row per
    candidate."""
    from backend.services._dsl_chat_tools import backtest_dsl_tree
    from backend.services.backtest.validation import trust_verdict
    from backend.services.backtest.validation.trials import record_and_deflate

    cands = [c for c in (args.get("candidates") or []) if isinstance(c, dict)]
    if len(cands) < 2:
        return {"error": "evaluate_strategies needs at least two candidates — "
                         "for one rule, build it; the user can press Backtest."}
    cands = cands[:_MAX_CANDIDATES]
    base = {k: args[k] for k in ("primary_symbol", "interval", "start_date",
                                 "end_date", "starting_capital") if args.get(k)}

    async def one(c: dict):
        # Every candidate is measured the way the paper runtime ARMS a rule:
        # in or out of the instrument, nothing else. Sizing variants were on
        # offer here and a live run showed the cost — the model chose an
        # ATR-sized candidate, could only build it as a fixed share count,
        # and quoted the sized numbers for a rule that behaves differently.
        # A candidate that cannot be armed is not a candidate.
        a = {**base, "condition": str(c.get("entry") or ""),
             "exit_condition": str(c.get("exit") or "")}
        try:
            return c, await backtest_dsl_tree(a), None
        except Exception as exc:                            # noqa: BLE001
            return c, None, str(exc)[:400]

    ran = await asyncio.gather(*(one(c) for c in cands))

    # Candidates finished in whatever order they finished, so each was
    # deflated against however many had registered before it. The trial count
    # that is TRUE is the final one; re-scoring is idempotent per fingerprint.
    rows: list[dict] = []
    window = bench = None
    for c, out, err in ran:
        row: dict[str, Any] = {"name": c.get("name"), "entry": c.get("entry"),
                               "exit": c.get("exit")}
        if err or not isinstance(out, dict):
            row["error"] = err or "no result"
            rows.append(row)
            continue
        m = out.get("metrics") or {}
        fs = m.get("forward_stats")
        verdict = m.get("trust_verdict")
        group, fp = out.get("_trial_group"), out.get("_trial_fingerprint")
        if fs and group and fp:
            fs = record_and_deflate(fs, group, fp)
            verdict = trust_verdict(
                forward_stats=fs, monte_carlo=m.get("monte_carlo"),
                sub_periods=m.get("sub_periods"),
                total_return_pct=float(m.get("total_return_pct") or 0.0),
                n_trades=int(m.get("n_trades") or 0))
        window = window or out.get("period_label")
        bench = bench or {
            "return_pct": _pct(m.get("benchmark_return_pct")),
            "max_drawdown_pct": _pct(m.get("benchmark_max_drawdown_pct")),
        }
        # How long a position is typically held. An exit that fires the bar
        # after every entry shows up here as a one-day hold and hundreds of
        # trades, which is the tell of an exit written the wrong way round.
        holds = sorted(
            (date.fromisoformat(t["exit_date"])
             - date.fromisoformat(t["entry_date"])).days
            for t in (out.get("trades") or [])
            if t.get("exit_date") and t.get("entry_date"))
        row.update({
            "reads_as": out.get("tree_summary"),
            "median_hold_days": holds[len(holds) // 2] if holds else None,
            "return_pct": _pct(m.get("total_return_pct")),
            "cagr_pct": _pct(m.get("cagr_pct")),
            "max_drawdown_pct": _pct(m.get("max_drawdown_pct")),
            "pnl_inr": _pct(m.get("pnl_inr"), 0),
            "time_in_market_pct": _pct(m.get("capital_utilization_pct"), 0),
            "trades": m.get("n_trades"),
            "win_rate_pct": _pct(m.get("hit_rate_pct"), 0),
            "sharpe": _pct(m.get("sharpe")),
            "psr": _pct((fs or {}).get("psr")),
            "deflated_sharpe": _pct((fs or {}).get("deflated_sharpe")),
            "trials_counted": (fs or {}).get("num_trials"),
            "verdict": (verdict or {}).get("label"),
            "verdict_why": (verdict or {}).get("rationale"),
        })
        if out.get("assumptions"):
            row["assumptions"] = out["assumptions"]
        rows.append(row)

    # What each result says against holding, and which results are a broken
    # RULE rather than a bad IDEA. Computed here, read by the model: a
    # mistranslated candidate reported as evidence ("the Supertrend rule made
    # no trades") ends a search that should have fixed it and gone on.
    b_ret = (bench or {}).get("return_pct")
    b_dd = (bench or {}).get("max_drawdown_pct")
    for row in rows:
        if row.get("error"):
            continue
        row["flags"] = _broken_rule_flags(row)
        ret, dd, cagr = row.get("return_pct"), row.get("max_drawdown_pct"), row.get("cagr_pct")
        if isinstance(dd, (int, float)) and isinstance(b_dd, (int, float)):
            row["drawdown_cut_pts"] = _pct(b_dd - dd)
        if isinstance(ret, (int, float)) and isinstance(b_ret, (int, float)):
            row["return_given_up_pts"] = _pct(b_ret - ret)
        if isinstance(cagr, (int, float)) and isinstance(dd, (int, float)) and dd > 0:
            row["return_per_drawdown"] = _pct(cagr / dd)

    return {
        "symbol": base.get("primary_symbol"),
        "window": window,
        "interval": base.get("interval"),
        "capital_inr": float(base.get("starting_capital") or 100_000),
        "buy_and_hold": bench,
        "candidates": rows,
        "_note": (
            "For your judgement only — none of this was shown to the user. "
            "A candidate with `flags` is a broken rule, not evidence: rewrite "
            "it and run it again. Judge the rest against what the user asked "
            "for (downside protection: drawdown_cut_pts for the "
            "return_given_up_pts). If none serves the objective yet, search "
            "on — see 'When nothing fits yet' — rather than reporting."),
    }


def _broken_rule_flags(row: dict) -> list[str]:
    """The shapes a mistranslated rule leaves in its own numbers. Each is a
    reason to rewrite the candidate and run it again, not a finding."""
    flags = []
    trades = row.get("trades") or 0
    hold = row.get("median_hold_days")
    in_mkt = row.get("time_in_market_pct") or 0
    if trades == 0:
        flags.append("never_entered: the entry was never true — reword it "
                     "or it compares against the wrong kind of value")
    elif trades <= 1 and in_mkt >= 95:
        flags.append("never_exited: bought once and held to the end — the "
                     "exit was never true, so this measured buy & hold")
    if trades >= 50 and hold is not None and hold <= 1:
        flags.append("exit_fires_next_bar: exit true almost every bar — it "
                     "is written the wrong way round or always true")
    elif 0 < trades < 5:
        flags.append("too_few_trades: the verdict cannot mean much")
    return flags


async def _backtest_draft(args: dict) -> dict:
    """The draft card's Backtest button: the card's OWN trees, no
    re-translation, on the same engine and defaults the chat uses."""
    from backend.services._dsl_chat_tools import backtest_dsl_draft
    return await backtest_dsl_draft(args)


# `backtest_dsl_draft` is a handler without a tool definition: the button
# calls it, the model never does — the model's way to test a draft it built
# is to be asked, and then `backtest_dsl_tree`.
_OWN_HANDLERS = {"evaluate_strategies": _evaluate_strategies,
                 "backtest_dsl_draft": _backtest_draft}


def tools() -> list[dict]:
    """Pivot's tool definitions in the Responses-API shape Charto sends.

    Pivot stores them in the chat-completions shape (name/description/
    parameters nested under "function"); the Responses API wants those keys
    flat. The descriptions carry the step catalog and the routing order and
    are otherwise passed through — the ONE exception is `_unblock_sizing`,
    which removes the routes to an ASK_USER tool this wire does not have.
    """
    st = _ensure_pivot()
    if not st["ok"]:
        return []
    all_tools = st["mods"]["ALL_TOOLS"]
    out: list[dict] = []
    for name in PIVOT_TOOLS:
        defn = all_tools.get(name)
        if not defn:
            logger.warning("pivot tool %s missing from ALL_TOOLS", name)
            continue
        fn = _override(name, _unblock_sizing(_retarget(defn.get("function") or {})))
        out.append({
            "type": "function",
            "name": fn.get("name", name),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters")
            or {"type": "object", "properties": {}, "required": []},
        })
    return out + [dict(t) for t in OWN_TOOLS.values()]


def _override(name: str, fn: dict) -> dict:
    """This surface's own wording for a borrowed tool, where it has one.

    `execution_tool_docs.apply` replaces DESCRIPTION TEXT only — parameter
    names, types and `required` stay Pivot's, because Pivot's handler is what
    reads them. A missing module leaves the borrowed text as it is.
    """
    try:
        from execution_tool_docs import apply
    except Exception:                                       # noqa: BLE001
        return fn
    return apply({**fn, "name": fn.get("name") or name})


def _calibration_block() -> str:
    """Pivot's calibration examples, filtered to the tools Charto offers.

    The full set is calibrated against Pivot's ~90-tool chat. Handing an
    execution-mode model an ideal call to `get_index_level` teaches it to
    reach for a tool that is not on the wire — a worked example of a tool
    you do not have is worse than no example.
    """
    st = _ensure_pivot()
    if not st["ok"]:
        return ""
    path = PIVOT_ROOT / "backend" / "prompts" / "agentic_examples.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    all_tools = st["mods"]["ALL_TOOLS"]
    yield_rows: list[dict] = []
    keep = set(PIVOT_TOOLS)
    rows = [e for e in (data.get("examples") or []) if e.get("tool") in keep]
    if not rows:
        return ""
    # ASK_USER left `keep` with the modules that taught it. There is no
    # clarify tool on this wire — a worked example of one is a route the model
    # takes and then cannot finish, and this surface's whole instruction is to
    # act on a stated assumption instead of asking.
    #
    # A surviving example can still NAME a tool that is absent, in its `note`.
    # One does: the amendment example's note says "NEVER route to create_sip,
    # place_order, or another macro" — sound advice in Pivot's chat, and here
    # it is the only mention of `create_sip` on the whole wire, arriving as an
    # instruction about a tool the model would otherwise never think of. The
    # note goes and the worked call stays: the call is the part that teaches.
    #
    # Which names count as absent is read from Pivot's own registry rather
    # than listed, so a macro added or renamed over there needs no edit here.
    absent = {n for n in all_tools if n not in keep}
    for ex in rows:
        note = str(ex.get("note") or "")
        if any(n in note for n in absent):
            ex = dict(ex)
            ex["note"] = ""
        yield_rows.append(ex)
    rows = yield_rows
    lines = [
        "## Calibration examples",
        "",
        "The ideal first call for one prompt. `conf` is how sure the routing "
        "is.",
        "",
    ]
    for i, ex in enumerate(rows, start=1):
        conf = ex.get("confidence")
        tag = f" [conf={conf:.2f}]" if isinstance(conf, (int, float)) else ""
        try:
            args = json.dumps(ex.get("args") or {}, separators=(",", ":"))
        except (TypeError, ValueError):
            args = str(ex.get("args"))
        lines.append(f"Ex {i} — {ex.get('id') or i}{tag}")
        lines.append(f'user: "{(ex.get("prompt") or "").strip()}"')
        lines.append(f"→ {ex.get('tool')}({args})")
        note = (ex.get("note") or "").strip()
        if note:
            lines.append(f"why: {note}")
        lines.append("")
    return "\n".join(lines).strip()


def _sections(path, wanted: tuple[str, ...]) -> str:
    """Named markdown sections, sliced live out of the file that owns them.

    A section runs from its own heading to the next heading of the same or
    higher level. A heading that no longer exists is skipped with a warning
    rather than raising — a renamed section should cost the builder one rule,
    not every execution turn.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    out: list[str] = []
    for head in wanted:
        try:
            start = lines.index(head)
        except ValueError:
            logger.warning("prompt section missing in %s: %s", path.name, head)
            continue
        depth = len(head) - len(head.lstrip("#"))
        end = len(lines)
        for i in range(start + 1, len(lines)):
            line = lines[i]
            if line.startswith("#") and len(line) - len(line.lstrip("#")) <= depth:
                end = i
                break
        out.append("\n".join(lines[start:end]).strip())
    return "\n\n".join(out)


def _absent_tools_note(text: str, all_tools) -> str:
    """One line correcting the Pivot tools the modules name but this wire lacks.

    The modules are Pivot's prose and are included whole, so `workflows.md`
    still routes past four macros — "`propose_workflow` (and the macros
    `propose_threshold_order`, `propose_scheduled_order`, …)" — that this
    surface deliberately does not carry. Rewriting that sentence in Pivot's
    file would fork a contract two products read; deleting the section around
    it would cost the routing rule between the two builders that ARE here.
    So the mention stays and is corrected, in a line computed against Pivot's
    registry so a macro renamed over there needs no edit here.

    A NAME ONLY COUNTS WHEN IT IS BACKTICKED, and that is the whole of the
    matching rule. The first version tested `name in text`, an unanchored
    substring, which produced two false positives that were worse than the
    problem being solved:

      · `calculate` — a real entry in Pivot's 106-tool registry, and also an
        ordinary English word. Its single "occurrence" was the phrase "how
        the entry condition is calculated". The note was declaring a verb
        unavailable.
      · `place_order` — 10 occurrences, 9 of them `action.place_order`, which
        is not a tool at all but the DSL STEP every armable Charto strategy
        must contain (`strategies.py` matches that exact string). The note
        announced "NOT ON THIS SURFACE: place_order" in the same payload that
        instructs the model to append `action.place_order` to a draft.

    The packs reference tools in backticks and refer to step types dotted, so
    requiring the backticks separates the two exactly, and drops the English
    word for free.
    """
    absent = sorted(n for n in all_tools
                    if n not in PIVOT_TOOLS and f"`{n}`" in text)
    if not absent:
        return ""
    return (
        "NOT ON THIS SURFACE, though the pack above names them: "
        + ", ".join(f"`{n}`" for n in absent) + ". "
        # The redirect used to read "`propose_workflow` (a schedule or several
        # steps)", which sent every recurring-clock ask at the ONE draft shape
        # the runtime refuses: `strategies.parse_draft` raises Unbuildable for
        # `trigger.schedule`/`trigger.cron`, and `save_strategy`'s own
        # description on this same wire says so. The note was contradicting a
        # tool description and steering the model into a failure it could only
        # recover from with another round — at ~8-21s each, the expensive kind
        # of wrong. A schedule now gets its own honest answer instead of being
        # folded into the redirect.
        "A single condition on price or an indicator → `propose_dsl_workflow`; "
        "several steps → `propose_workflow`; a selection to own → "
        "`register_plan`. A recurring CLOCK (a SIP, \"every Monday\") cannot be "
        "armed here at all — the paper runtime evaluates conditions, not "
        "schedules — so either express the idea as a condition, or backtest it "
        "and say plainly that it cannot be armed."
    )


def _borrowed_sections() -> str:
    """Every `PROMPT_SECTIONS` entry, in order."""
    root = PIVOT_ROOT / "backend" / "prompts"
    parts = [_sections(root / rel, heads) for rel, heads in PROMPT_SECTIONS]
    return "\n\n".join(p for p in parts if p)


def system_prompt() -> str:
    """Charto's adapter + Pivot's automation modules + calibration.

    Raises nothing: an unavailable engine yields the adapter alone, so a
    misconfigured server degrades to a mode that explains itself rather than
    a chat turn that 500s.
    """
    parts = [_ADAPTER]
    st = _ensure_pivot()
    if st["ok"]:
        core = _borrowed_sections()
        if core:
            parts.append(core)
        try:
            modules = st["mods"]["assembler"].load_prompt_modules(
                list(PROMPT_MODULES))
            if modules:
                parts.append(modules)
                note = _absent_tools_note(modules, st["mods"]["ALL_TOOLS"])
                if note:
                    parts.append(note)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prompt modules unavailable: %s", exc)
        block = _calibration_block()
        if block:
            parts.append(block)
    return "\n\n".join(parts)


# ── Dispatch ─────────────────────────────────────────────────────────


# A warning Pivot stamps that is TRUE THERE AND FALSE HERE.
#
# `_dsl_chat_tools.py` appends "the ratchet is fully modeled in backtests.
# Live, this registers the initial stop — live re-ratcheting on new highs is
# coming, not wired yet" to any draft whose exit tree mentions
# `drawdown_from_peak_pct` or `peak_unrealised_pct`. That is an accurate
# statement about PIVOT's live executor.
#
# It is not true of the runtime this card will actually arm against.
# `strategies._update_peak` recomputes the high-water mark from the bar's own
# high and persists it BEFORE the exit tree is walked, on every bar — that is
# a live ratchet, and the ordering is deliberate enough to be commented in
# that file. So the borrowed warning tells a Charto user their trailing stop
# is less than it is, on a card that offers to arm it, and the model repeats
# the sentence in prose because the warning is on the payload it reads.
#
# A false limitation is a fabrication in the direction that looks responsible,
# which is the hardest kind to notice. It is corrected here rather than in
# Pivot because Pivot's copy is right about Pivot; the seam is the only place
# that knows which runtime the draft is headed for.
_RATCHET_WARNING = "live re-ratcheting"
_RATCHET_TRUTH = (
    "Trailing/peak exit: Charto re-computes the high-water mark from each "
    "bar's high before the exit is checked, so the stop ratchets live as well "
    "as in the backtest."
)


def _fix_borrowed_warnings(draft: dict) -> None:
    """Replace warnings that describe Pivot's runtime rather than Charto's."""
    warnings = draft.get("warnings")
    if not isinstance(warnings, list):
        return
    draft["warnings"] = [
        _RATCHET_TRUTH if isinstance(w, str) and _RATCHET_WARNING in w else w
        for w in warnings
    ]


def _humanize(draft: dict) -> None:
    """Make a draft readable on a card, in place.

    Two things the card cannot do for itself. **Labels**: the DSL proposal
    path returns steps without them, and a card that prints
    `trigger.compound` as a heading has leaked an engineering id into the
    product. Pivot's validator backfills these for the flat-steps path; the
    v2 handlers bypass it, so the seam does it for both.

    **Readback**: a compound trigger's config is a nested DSL tree. Rendering
    that as JSON on a card asks the user to audit a parse tree to find out
    what their own strategy says. Pivot already knows how to say it in
    English — `tree_to_english` is the same renderer its own cards use — so
    the tree travels with the sentence that describes it.
    """
    st = _ensure_pivot()
    if not st["ok"]:
        return
    _fix_borrowed_warnings(draft)
    registry = st["mods"]["STEP_REGISTRY"]
    try:
        from backend.workflows.dsl.readback import tree_symbols, tree_to_english
        from backend.workflows.dsl.schema import Tree
        from pydantic import TypeAdapter
        adapter = TypeAdapter(Tree)
    except Exception:  # noqa: BLE001 — labels still work without readback
        adapter = None
        tree_to_english = None
        tree_symbols = None

    # Whether the per-leaf "of SYMBOL" is worth printing is a fact about the
    # whole CARD, not about one tree: a draft that watches RELIANCE and buys
    # INFY needs it on both even though each tree alone looks single-symbol.
    # Only this loop can see every step, so the decision is made once, here,
    # and handed to the renderer.
    trees = []
    if adapter is not None:
        for _st in draft.get("steps") or []:
            if not isinstance(_st, dict):
                continue
            _t = (_st.get("config") or {}).get("entry")
            if not _t:
                continue
            try:
                trees.append(adapter.validate_python(_t))
            except Exception:  # noqa: BLE001
                pass
    _syms = set()
    for _t in trees:
        _syms |= tree_symbols(_t)
    _bare = len(_syms) == 1

    for step in draft.get("steps") or []:
        if not isinstance(step, dict):
            continue
        defn = registry.get(step.get("step_type"))
        if defn is not None:
            raw = (step.get("label") or "").strip()
            if not raw or raw == step.get("step_type") or raw in registry:
                step["label"] = defn.label
        tree = (step.get("config") or {}).get("entry")
        if tree and adapter is not None:
            try:
                step["readback"] = tree_to_english(
                    adapter.validate_python(tree), bare_symbol=_bare)
                # A tile's heading names the step's ROLE; its body says what
                # the step does. The registry's own label for a compound
                # trigger is "When multiple conditions are met", which as a
                # heading over "price crosses below EMA(50) OR price crosses
                # above EMA(50)" restates that conditions exist and nothing
                # else — the body already proves it. Once there is a sentence
                # to read, the heading gets out of its way.
                #
                # The role comes from the STEP TYPE, never from position or
                # from the config key: an exit condition is stored under
                # `entry` too (the tree slot is named for the schema, not for
                # what the step does), so labelling every readback-bearing
                # step "Entry" put that word over "unrealised P&L >= 0.08" —
                # the take-profit rule announced as an entry.
                _role = _STEP_ROLE.get(step.get("step_type"))
                if _role:
                    step["label"] = _role
            except Exception:  # noqa: BLE001 — a card without a sentence is fine
                pass


# Which steps get a one-word heading in place of the registry's generic
# sentence. Only the condition-bearing types: everything else already has a
# label that says what it does ("Place an order", "Your portfolio").
_STEP_ROLE = {
    "trigger.compound": "Entry",
    "trigger.exit_compound": "Exit",
}


def _drop_non_values(args: Optional[dict]) -> dict:
    """Remove arguments that carry no value the user expressed.

    This model fills every declared property rather than omitting the ones it
    has nothing for, so a scheduled order arrives with `run_at: ""` and
    `valid_until: ""` beside the fields that matter. An empty string is not a
    date; passing it on invites a downstream parse of nothing.

    Only unambiguous non-values go: None, empty strings, empty collections.
    A `0` stays — zero is a real number and deciding it is junk would need a
    guess about which field it is on. The invented NUMBERS (a ₹1 notional, a
    0.1% stop) are a contract question the prompt answers, not a shape
    question this function can settle.
    """
    out: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if value is None:
            continue
        if isinstance(value, (str, list, tuple, dict, set)) and len(value) == 0:
            continue
        out[key] = value
    return out


class _session:
    """A Pivot DB session for the tools that need one, and None for the rest.

    Opened per call and closed on the way out. Pivot's Postgres is in Azure and
    latency is RTT-bound, so a session held open across a chat turn is a
    connection held open across a chat turn — this surface's calls are short
    reads and do not deserve one.

    `_DB_TOOLS` is empty today: the option tools were the only ones here that
    needed Pivot's Postgres, and they are gone. The seam stays because the
    reasoning behind it has not changed — NOTHING on this surface writes, and
    `kite_token` stays empty everywhere, so no broker action is reachable even
    with a session in hand. Anything that reads a USER's positions is
    pointedly not on the tool list for the same reason activation is disabled:
    a Charto account is not a Pivot account.
    """

    def __init__(self, tool: str) -> None:
        self.tool, self.db = tool, None

    def __enter__(self):
        if self.tool not in _DB_TOOLS:
            return None
        try:
            from backend.database import SessionLocal
            self.db = SessionLocal()
        except Exception as exc:  # noqa: BLE001 — the tool reports it
            logger.warning("no DB session for %s: %s", self.tool, exc)
            self.db = None
        return self.db

    def __exit__(self, *exc) -> None:
        if self.db is not None:
            try:
                self.db.close()
            except Exception:  # noqa: BLE001
                pass


def dispatch(name: str, args: dict, *, timeout: float = 150.0) -> dict:
    """Run one Pivot tool and return a JSON-safe result for the model.

    `db` is None and `kite_token` empty by design: every tool on this surface
    proposes or simulates, and none of them reads a broker session or writes
    a row. If a tool added later needs either, it does not belong on a
    surface whose whole promise is that it cannot touch an account.

    **There is no user_id parameter, and that is the point.** Charto's
    accounts live in its own SQLite and Pivot's live in Postgres; the two
    numbering schemes have nothing to do with each other. This function used
    to take the caller's Charto id and hand it to Pivot's registry, so Charto
    user 3 arrived as Pivot user 3 — and three of these tools open a real
    Pivot session, which is a Charto account reading a stranger's rows.
    Nothing user-scoped was reachable through the current twelve, so it never
    fired; it was a trap set for whichever tool got added next.

    Anonymous is the honest identity here. A tool that cannot answer without
    knowing who is asking is a tool that has no business on a surface which
    cannot tell.
    """
    ready, reason = available()
    if not ready:
        return {"error": "execution_engine_unavailable", "detail": reason}
    tool_registry = _state["mods"]["tool_registry"]
    args = _drop_non_values(args)
    import dataserver as ds         # late: ds imports THIS module at load
    # WHICH CONVERSATION THIS TRIAL BELONGS TO. The Deflated Sharpe charges a
    # result for every variant tried in the same research session, and it
    # finds the session through Pivot's turn context — which nothing on this
    # surface ever set, so every Charto backtest was scored as the only one
    # ever run. Captured here on the request thread (`ds._req` is
    # thread-local) and set inside the coroutine, which is where the engine's
    # context is copied from.
    chat = str(getattr(ds._req, "chat_id", "") or "")
    who = getattr(ds._req, "user", None)
    trial_group = (f"charto:{chat}" if chat
                   else f"charto:u{who[0]}" if who else None)

    # Captured HERE, on the request thread, because `ds._req` is thread-local
    # and the tool runs on the engine's loop. Only the builders translate
    # natural language, so only they are told about drawings.
    hint = ""
    if name in ("propose_dsl_workflow", "backtest_dsl_tree",
                "evaluate_strategies"):
        hint = _drawing_hint(
            str(args.get("primary_symbol") or getattr(ds._req, "symbol", "")),
            who[0] if who else 0)

    async def _run(db):
        from backend.services.turn_context import set_conversation_id
        set_conversation_id(trial_group)
        token = _TURN_DRAWINGS.set(hint)
        try:
            if name in _OWN_HANDLERS:
                return await _OWN_HANDLERS[name](args)
            return await tool_registry.execute(
                name, args or {}, kite_token="", db=db, user_id=0,
            )
        finally:
            _TURN_DRAWINGS.reset(token)

    try:
        with _session(name) as db:
            future = asyncio.run_coroutine_threadsafe(_run(db), _get_loop())
            result = future.result(timeout=timeout)
    except TimeoutError:
        return {"error": "execution_tool_timeout",
                "detail": f"{name} did not finish within {timeout:.0f}s."}
    except Exception as exc:  # noqa: BLE001
        logger.exception("pivot tool %s failed", name)
        return {"error": "execution_tool_failed", "detail": str(exc)[:600]}

    if isinstance(result, dict):
        return result           # an own tool: already the model's payload
    if not result.success:
        # Pivot's errors are written FOR the model — they name the missing
        # field or the tool that should have been called. Pass them through
        # whole (including the redirect) instead of flattening to "failed".
        out = {"error": "tool_rejected", "detail": (result.error or "")[:900]}
        if getattr(result, "redirect_to", None):
            out["use_tool_instead"] = result.redirect_to
        return out

    data = dict(result.data or {})
    if data.get("_render_hint") == "workflow_draft_card":
        _humanize(data)
    return data
