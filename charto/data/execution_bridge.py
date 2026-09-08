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
import json
import logging
import sys
import threading
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
manifest a dumb loop fills. That is why you may reason freely here: your
judgement is spent once, in this turn, and recorded.

## Act, then ask

NEVER open with a question. A question first is a wasted turn: the user asked
for work, and every value you might ask for is one you can choose better than
they can guess. Choose it, do the work, show the result, and put the question
LAST — as an amendment to something that already exists.

- Missing amount, count, horizon, universe, sector, lookback, threshold: pick
  one, use it, list it under `assumptions`, and name it in one line of prose.
- Present the amendment as an offer, not a gate: "I sized it at X — say the
  word and I'll re-cut it at Y."
- Ask a real question only when NO answer would let you proceed, and even then
  ask it after everything that does not depend on it is already built.
- A vague objective is a specification, not a gap. "Momentum", "defensive",
  "quality", "AI exposure", "something that will run next month" each name a
  construction you already know. Build the standard one and say which you chose.
  Which strategy to build is never a question you put to the user.

## What registration means

Two verbs, and the difference is the shape of the thing, not its size.

- `register_plan` — a SELECTION. Several instruments, or one with no condition:
  "buy the top 10", "the best AI name for next month", "put 3 lakh to work".
  Legs carry a size and a reason each; the plan carries the reasoning.
- `save_strategy` — a RULE. One instrument watched by a condition tree, built
  by `propose_dsl_workflow` or `propose_workflow` first. Call it directly on
  the draft this conversation already produced; never rebuild the draft.

Both are register-not-execute. Nothing fills until the user presses Activate on
the card. So register in the same turn you decided — do not ask permission to
register, and do not describe a plan you did not register. A described plan is
gone when the turn ends; a registered one is on screen with a button.

Never claim something is saved, armed, bought or running without a tool call
that returned an id.

## Research is part of the job

An answer about a company needs the company in it. Before you select names,
read them: `screen_universe` ranks the whole stored universe on price features,
`read_symbol` and `compare_symbols` price them against each other and against a
benchmark, `explain_move` and `search_news` say what has been happening and
why, `get_results` dates the last earnings, `get_peers` places a name among its
own industry. Quote what you read, with its date. A selection with no readings
behind it is a guess wearing a card.

Then test what you can. `backtest_dsl_tree` and `backtest_workflow` run a rule;
`backtest_portfolio` runs a cross-section (five names minimum); `scan_pairs`,
`test_cointegration` and `backtest_pairs` handle relationships between two
instruments. A rule you can test and did not is a rule you are guessing about.
`build_strategy` screens and weights a basket for you and returns what it
assumed — use it when you want its construction, and register the result.

Research → decide → test → register is ONE turn. Do not stop after the research
to report what you found and wait; finish.

## Boundaries, stated once and not repeated

The book is SIMULATED and long-only. No real order reaches a broker from here.
The card says so, so you do not have to — never append "this is only a draft",
"no order has been placed" or "review before arming". Say what the plan DOES.

Alerts are Charto's, not a workflow. "Tell me when", "alert me", "ping me" →
`set_alert`. The proposal tools refuse notify-only drafts.

Options are not on this surface. Downside control here is a regime filter, an
exit (`unrealised_pct`, `peak_unrealised_pct`, `drawdown_from_peak_pct`,
`bars_held` are the stop, the trailing stop and the time stop), or how a basket
is composed — never a hedge this surface cannot build.

Scope is India: NSE/BSE equities, indices, MCX commodities, crypto. Out of
scope, name the nearest listed proxy with a number rather than refusing flat.

## The numbers

Every figure you state comes from a tool result or from the chart line above.
Never invent a price, a level, a weight or a date. If a tool returns nothing,
say it is unavailable — silence and a guess are both worse.

Do not do arithmetic that a tool will do for you. Send `weight_pct` and
`capital_inr`; the share counts are computed against the live mark when the
user presses. Send the condition in English to `propose_dsl_workflow`; it
translates. Fill only fields the user's ask actually implies — an unrequested
stop-loss, a ₹1 notional or an empty date are parameters you invented, and
`quantity` with `notional_inr` on one leg is rejected outright.

The instrument in the composer is the DEFAULT subject and nothing more. A
symbol the user names wins, any stored symbol is readable whether or not it is
on screen, and what is open on the workspace constrains nothing.

When a tool returns an error, read it — it names the missing field or the
better tool. Fix it and call again rather than narrating the failure.
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


def tools() -> list[dict]:
    """Pivot's tool definitions in the Responses-API shape Charto sends.

    Pivot stores them in the chat-completions shape (name/description/
    parameters nested under "function"); the Responses API wants those keys
    flat. Nothing else is rewritten — the descriptions carry the step catalog
    and the routing order, and editing them here would fork the contract.
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
        fn = _retarget(defn.get("function") or {})
        out.append({
            "type": "function",
            "name": fn.get("name", name),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters")
            or {"type": "object", "properties": {}, "required": []},
        })
    return out


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
        "is; below 0.6, ask one question instead of guessing.",
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

    async def _run(db):
        return await tool_registry.execute(
            name, args or {}, kite_token="", db=db, user_id=0,
        )

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
