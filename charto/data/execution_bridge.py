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
    # Options. These are the only tools here that need Pivot's DATABASE (the
    # instrument master and option universe live there); see `_session`.
    "get_option_chain",
    "suggest_option_strategy",
    "build_option_strategy",
    # Relationships between instruments, which single-symbol rules cannot say
    # anything about: a pair's spread, a basket's cross-section, whether two
    # series are actually cointegrated or merely correlated last quarter.
    "backtest_pairs",
    "scan_pairs",
    "test_cointegration",
    "backtest_portfolio",
)

# Tools that read Pivot's Postgres. Everything else runs with `db=None`, so a
# database that is down or unreachable costs exactly these four and leaves the
# proposal and backtest surface working.
_DB_TOOLS: frozenset[str] = frozenset({
    "get_option_chain", "suggest_option_strategy", "build_option_strategy",
})

# Prompt modules from Pivot that govern the tools above. Deliberately NOT the
# whole 83KB system_core.md — that carries the market-analysis contract, the
# option surface and the news rules, none of which apply to a builder mode
# and all of which would cost tokens on every turn to say nothing.
PROMPT_MODULES: tuple[str, ...] = (
    "workflows", "order_sizing", "stoploss", "sips", "backtest",
)

# Two rules the modules assume but do not carry, because in Pivot they live in
# the 83KB core that execution mode does not load. Without them the builder
# over-asks: the first live prompt answered "buy 10 INFY when RSI < 30" with
# "which timeframe?" — the exact question the interval section forbids, since
# the interval is the lowest-priority gap and asking it buries the real one.
#
# Sliced from `system_core.md` by heading at load time rather than copied.
# A copy would read identically today and drift the first time someone edits
# the contract, and a forked behavioural rule is worse than a missing one:
# it is wrong in a way that still looks authoritative.
CORE_SECTIONS: tuple[str, ...] = (
    "## Technical-indicator timeframe — bar-interval is never a blocking question",
    "## Clarify discipline — ask at most once, then EMIT",
)

_ADAPTER = """
# Strategy Builder — Charto execution mode

You are Pivot's automation builder, running inside Charto's chart. The rules
below are Charto's adaptation; everything after them is Pivot's own contract
and outranks nothing here but governs everything else.

THE CHART IS THE DEFAULT SUBJECT. When the user names no symbol, the strategy
is about the chart in context. When they name one, that wins. Never silently
build for a symbol nobody mentioned.

THE CHART IS CONTEXT, NOT AN INSTRUCTION. Read a visible level, indicator or
pattern only when the requested strategy needs it. A value being on screen is
not a rule the user asked for.

ALERTS ARE CHARTO'S, NOT A WORKFLOW. "Tell me when", "alert me", "ping me",
"let me know if" — call `set_alert`. Charto's alert engine persists the rule
and fires it on the live tick. Do NOT draft a notify workflow for these; the
Pivot proposal tools refuse them and will hand back an error. Only when the
user wants to ACT at that level does a proposal tool apply.

BUILD IT, DON'T DISCLAIM IT. The card already carries the approval state; the
user does not need to be told on every turn that nothing has been placed. Say
what the strategy DOES — the instrument, the trigger, the action — and stop.
Do not append "this is only a draft", "no order has been placed", "review
before arming" or any variant. Once per conversation is more than enough, and
usually zero is right because the card says it.

AN IMMEDIATE ORDER IS STILL A STRATEGY. "Buy 5 MARUTI at market" names no
trigger, and that is fine — build it as a manual-run draft (`trigger.manual`)
the user fires when they choose. Do not refuse it, and do not lecture about
contingency. If the user plainly wanted a rule and just left the trigger out,
ask what should trigger it — one question, not a boundary statement.

ONE DRAFT PER TURN. Emit the draft and stop. Do not ask the user to confirm
what the card already shows, and do not restate the card's fields in prose —
one or two plain sentences naming the instrument, the trigger and the action,
plus any assumption you had to make.

A STRATEGY IS NOT ALWAYS A RULE. "Build me a momentum strategy", "a basket of
IT names", "put 3 lakh to work" want HOLDINGS, not a trigger — call
`build_strategy`, which screens and weights real names. A trigger→action
workflow cannot express "own these eight at these weights"; reaching for
`propose_workflow` there produces an automation nobody asked for.

Do NOT ask for risk appetite or horizon before building a basket.
`build_strategy` fills them itself and returns what it assumed — the card
lists every assumption, and the user amends the one they disagree with. An
objective and a capital figure are enough to build on; asking first turns a
one-turn answer into an interview about fields the tool already defaults.

OPTIONS ARE REAL HERE. `get_option_chain` returns the live chain with OI, IV,
greeks, max pain and the expected move. `suggest_option_strategy` turns a view
("bullish on NIFTY into expiry") into structures; `build_option_strategy`
builds a NAMED template (`bull_call_spread`, `iron_condor`, `straddle`, …) and
returns its payoff, greeks, breakevens and a critique. Argument is `template`,
not `strategy`. Quote what comes back and never estimate a premium, a greek or
a margin yourself.

CHECK `data_status` BEFORE QUOTING AN OPTION NUMBER. Without a live broker
session these tools return MOCK strikes and premiums — structurally right,
financially fictional. When `data_status` is anything but live, say so in your
first line, before any figure. The card carries the same warning; a reply that
quotes the premiums as real while the card calls them mock is the two halves
of one answer contradicting each other.

PAIRS AND PORTFOLIOS. Two instruments in a relationship are their own
question: `test_cointegration` says whether a spread is statistically real
rather than a correlation that held recently, `backtest_pairs` trades one,
`scan_pairs` searches a list for candidates, and `backtest_portfolio` runs a
cross-sectional basket (it needs at least five distinct symbols).

TWO BUILDERS, ONE CHOICE. `propose_dsl_workflow` takes the entry (and exit)
condition as plain English and translates it — use it for anything driven by
price, an indicator, a comparison between two of them, or a position's own
state. `propose_workflow` takes explicit steps[] — use it for a SCHEDULE
("every Friday at 10am", "daily at 09:20"), for several actions, or for a
shape the condition tool cannot hold. A recurring buy is a schedule, not a
condition.

FILL ONLY WHAT THE USER SAID. Every tool here has optional fields. Omit the
ones you have no value for; do not fill a placeholder. A `0` limit price, a ₹1
notional, a 0.1% stop-loss or an empty-string date are not values the user
expressed — they are parameters you invented. Two are worse than invented:
`quantity` and `notional_inr` are mutually exclusive and sending both is
rejected outright, and an unrequested `sl_pct` bolts a stop-loss onto a
strategy that never asked for one. Stay literal: no unprompted stop, exit,
trim or notify branch.

BACKTEST IS A SIMULATION, NOT A DRAFT. "Test", "backtest", "how would this
have done", "what if I had" → the backtest tools. Never answer a backtest ask
with a workflow draft, and never present a draft as though it had been tested.

WHEN A PROPOSAL TOOL RETURNS AN ERROR, read it: it names the missing field or
the better tool. Fix and call again rather than narrating the failure. If the
error states a boundary ("not available"), state that boundary in one line and
name the nearest thing that IS wired.
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
        fn = defn.get("function") or {}
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
    keep = set(PIVOT_TOOLS) | {"ASK_USER"}
    rows = [e for e in (data.get("examples") or []) if e.get("tool") in keep]
    if not rows:
        return ""
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


def _core_sections() -> str:
    """The named `system_core.md` sections, sliced live from the file.

    A section runs from its own heading to the next heading of the same or
    higher level. A heading that no longer exists is skipped silently rather
    than raising — a renamed section should cost the builder one rule, not
    every execution turn.
    """
    path = PIVOT_ROOT / "backend" / "prompts" / "system_core.md"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    out: list[str] = []
    for wanted in CORE_SECTIONS:
        try:
            start = lines.index(wanted)
        except ValueError:
            logger.warning("system_core section missing: %s", wanted)
            continue
        depth = len(wanted) - len(wanted.lstrip("#"))
        end = len(lines)
        for i in range(start + 1, len(lines)):
            line = lines[i]
            if line.startswith("#"):
                if len(line) - len(line.lstrip("#")) <= depth:
                    end = i
                    break
        out.append("\n".join(lines[start:end]).strip())
    return "\n\n".join(out)


def system_prompt() -> str:
    """Charto's adapter + Pivot's automation modules + calibration.

    Raises nothing: an unavailable engine yields the adapter alone, so a
    misconfigured server degrades to a mode that explains itself rather than
    a chat turn that 500s.
    """
    parts = [_ADAPTER]
    st = _ensure_pivot()
    if st["ok"]:
        core = _core_sections()
        if core:
            parts.append(core)
        try:
            modules = st["mods"]["assembler"].load_prompt_modules(
                list(PROMPT_MODULES))
            if modules:
                parts.append(modules)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prompt modules unavailable: %s", exc)
        block = _calibration_block()
        if block:
            parts.append(block)
    return "\n\n".join(parts)


# ── Dispatch ─────────────────────────────────────────────────────────


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

    Scope is deliberate. The option tools read the instrument master and the
    option universe, which is what makes a live chain possible at all. NOTHING
    on this surface writes, and `kite_token` stays empty everywhere, so no
    broker action is reachable even with a session in hand. `get_portfolio_
    greeks` is pointedly NOT on the tool list for the same reason activation
    is disabled: it reads a USER's positions, and a Charto account is not a
    Pivot account.
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
