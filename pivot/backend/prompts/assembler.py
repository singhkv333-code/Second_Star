"""Single source of truth for system-prompt assembly.

Every LLM call across the codebase goes through
`build_system_prompt(role, user_context, extra_context)`. Auditing
prompt content is now a single-file job.

Four layers, in order:
  1. Identity + role-specific instructions (from role-keyed templates
     below or from system.md for the generic 'chat' role).
  2. Calibration examples (chat role only). Loaded from
     agentic_examples.json. Compact `prompt → tool → args [conf]`
     mappings that demonstrate the right first-call decision plus
     low-confidence cases that should ASK rather than guess. Caching
     these in the system prompt prefix means OpenAI's prompt cache
     keeps them warm — zero per-turn input-token cost after the first
     call. They sit BEFORE per-turn / per-user blocks so they don't
     break cache when downstream content shifts.
  3. Domain primer (always included). Loaded from domain_primer.md.
  4. User context (portfolio summary, active workflows) when supplied.

Adding a new role: add an entry to ROLE_INSTRUCTIONS and call
`build_system_prompt(role="my_new_role", ...)`. Prefer prose
instructions over enumerated rules — the model handles them better.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional


PROMPTS_DIR = Path(__file__).resolve().parent


PromptRole = Literal[
    "chat",
    "propose_workflow",
    "narrate_tool_result",
    "correlation_decompose",   # placeholder — wires up in Prompt 2
]


@dataclass
class UserContext:
    """Subset of user state relevant to LLM prompting.

    Optional everywhere — the assembler degrades gracefully when None.

    The block is rendered AFTER the cached static prefix (system.md +
    examples + domain primer), so widening it does NOT invalidate the
    prompt cache for the static head. Per-turn variability stays local
    to this one system message.

    Field intent (read by `_format_user_context` below):
      - `top_holdings`: at most 5 dicts, each
        `{symbol: str, qty: int|float, last_price: float,
          value_inr: float, day_pct?: float}`. Sorted desc by `value_inr`.
        Lets the model skip a `get_holdings` call when the user asks
        "what about my INFY position".
      - `active_workflows`: at most 10 dicts, each
        `{id: str, name: str, last_run_at: str|None,
          next_run_at: str|None, step0_type: str}`. ISO datetimes
        ('Z' suffix). Lets the model skip `list_strategies` when the
        user says "pause that NIFTYBEES one".
      - `kite_connected`: True when the user has a live Kite session,
        False when they're on the mock_token fallback. Steers the
        model away from broker-write tools when no real account is
        wired.
      - `cash_buffer_inr`: reserved for a future cheap accessor —
        today's only path is a live Kite margins call, which costs a
        broker round-trip. Always `None` in the current build.
      - `watchlist_symbols`: at most 3 symbol strings, newest first.
      - `saved_baskets`: at most 10 dicts, each
        `{id, name, symbols: [str], n: int}` — the user's saved equity
        baskets. Lets the model answer "rebalance my oil basket" /
        "backtest my defensive basket" without a discovery round-trip.
    """
    user_id: int
    full_name: Optional[str] = None
    portfolio_total_inr: Optional[float] = None
    holdings_count: Optional[int] = None
    active_workflows_count: Optional[int] = None
    top_holdings: Optional[list[dict[str, Any]]] = None
    active_workflows: Optional[list[dict[str, Any]]] = None
    kite_connected: Optional[bool] = None
    cash_buffer_inr: Optional[float] = None
    watchlist_symbols: Optional[list[str]] = None
    saved_baskets: Optional[list[dict[str, Any]]] = None


# ── Role-specific instructions ──────────────────────────────────────


_CHAT_FALLBACK = """You are the assistant for Pivot.

Reply naturally and briefly. When the user asks for an action that maps to a
tool (placing an order, building an agent, fetching data), call the tool.
When you don't have a tool that fits or critical info is missing, say so
plainly — never fabricate values to fill required fields.
"""


_PROPOSE_WORKFLOW = """You translate the user's natural-language strategy
into a Pivot workflow draft.

A workflow is a LINEAR ordered list of steps. Step 0 MUST be a `trigger.*`
type. No branching, no loops, no sub-workflows. You may ONLY use step types
from the catalog you'll be shown in this prompt — inventing step types
fails validation hard.

When critical fields aren't in the user's request (dip threshold, quantity,
stop-loss level, target symbol when ambiguous), DO NOT GUESS. Either:
  (a) Output a draft that omits the optional field if the registry
      allows it, OR
  (b) Call the `ASK_USER` tool with one focused question.

Never fabricate parameter values to satisfy a required field. The user
will see the draft and act on it; a wrong dip threshold loses money.
"""


_NARRATE_TOOL_RESULT = """You just executed a tool. Write the user-facing
reply that summarises the result. Keep it to 1–3 sentences. Reference the
data the tool returned — don't invent figures, don't say "the data isn't
available" when it clearly is in the result. If the tool failed, say
plainly what went wrong and (if obvious) what the user could try next.
"""


ROLE_INSTRUCTIONS: dict[PromptRole, str] = {
    "chat": _CHAT_FALLBACK,
    "propose_workflow": _PROPOSE_WORKFLOW,
    "narrate_tool_result": _NARRATE_TOOL_RESULT,
    "correlation_decompose": "(placeholder — wires up in Prompt 2)",
}


# ── File loaders ────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _load_domain_primer() -> str:
    return (PROMPTS_DIR / "domain_primer.md").read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def _load_chat_system_md() -> str:
    """The always-on core instructions for the chat role.

    Since 2026-07-03 the monolithic system.md was split into a lean
    ``system_core.md`` (identity + routing doctrine + decision hierarchy,
    always loaded) plus per-intent packs in ``modules/*.md`` that are
    injected only on the relevant turn (see ``load_prompt_modules``). We
    prefer system_core.md; fall back to the old monolith, then the inline
    fallback, so the prompt still builds in any environment."""
    for name in ("system_core.md", "system.md"):
        p = PROMPTS_DIR / name
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    return _CHAT_FALLBACK


@lru_cache(maxsize=None)
def _load_prompt_module(name: str) -> str:
    """Load one intent pack from ``prompts/modules/<name>.md`` (cached
    per-name). Returns "" if the file is missing so a missing pack never
    breaks a turn — the core rules + tool schema still carry it."""
    p = PROMPTS_DIR / "modules" / f"{name}.md"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


def load_prompt_modules(names: "list[str]") -> str:
    """Concatenate the named intent packs into one system-message block,
    de-duplicated and order-stable. Returns "" when no pack applies, so the
    caller can skip appending an empty message."""
    seen: set[str] = set()
    blocks: list[str] = []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        text = _load_prompt_module(n)
        if text:
            blocks.append(text)
    return "\n\n---\n\n".join(blocks)


@lru_cache(maxsize=1)
def _load_agentic_examples() -> str:
    """Render `agentic_examples.json` to a compact text block for the
    system prompt.

    Format per example (one block, separated by blank lines):

        Ex N — <id> [conf=0.97]
        user: "<prompt>"
        → <tool>(<args>)
        why: <note>

    JSON is a verbose carrier — rendering as labelled lines saves
    ~40% of tokens vs dumping the JSON wholesale. Confidence is
    explicit so the model treats <0.6 cases as ASK_USER cues rather
    than forcing a tool call. Returns "" if the file is missing so the
    prompt still builds in dev environments without it.
    """
    p = PROMPTS_DIR / "agentic_examples.json"
    if not p.exists():
        return ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    examples = data.get("examples") or []
    if not examples:
        return ""

    lines: list[str] = [
        "## Calibration examples",
        "",
        "Each block shows the IDEAL first-call decision for one user "
        "prompt. `conf` is the system's confidence the named tool is "
        "the right one — when conf < 0.6, call ASK_USER instead of "
        "the listed tool. The `_DETERMINISTIC_*` markers indicate the "
        "runtime intercepts before the LLM hop; you should not see "
        "those prompts at the LLM. `_compose_hint` shows the step "
        "chain a propose_workflow draft should produce.",
        "",
    ]
    for i, ex in enumerate(examples, start=1):
        prompt = (ex.get("prompt") or "").strip()
        tool = ex.get("tool") or "?"
        args = ex.get("args") or {}
        conf = ex.get("confidence")
        note = (ex.get("note") or "").strip()
        ex_id = ex.get("id") or f"ex_{i}"
        try:
            args_str = json.dumps(args, separators=(",", ":"))
        except (TypeError, ValueError):
            args_str = str(args)
        conf_tag = f" [conf={conf:.2f}]" if isinstance(conf, (int, float)) else ""
        lines.append(f"Ex {i} — {ex_id}{conf_tag}")
        lines.append(f'user: "{prompt}"')
        lines.append(f"→ {tool}({args_str})")
        if note:
            lines.append(f"why: {note}")
        lines.append("")
    return "\n".join(lines).strip()


def reload_prompts() -> None:
    """Tests and live edits clear caches without a process restart."""
    _load_domain_primer.cache_clear()
    _load_chat_system_md.cache_clear()
    _load_prompt_module.cache_clear()
    _load_agentic_examples.cache_clear()


# ── Public entry point ──────────────────────────────────────────────


def _format_user_context(ctx: UserContext) -> str:
    """Compact user-context block.

    Budget: typically ~150-400 tokens; ~1500 tokens worst case with a
    full top-5 holdings list + 10 active workflows + watchlist. The
    chat router caps `active_workflows` at 10 and `top_holdings` at 5
    upstream, so the worst case is bounded.

    Returns an empty string when the only content would be the
    `## User context` header — the caller relies on that to omit the
    block entirely.
    """
    bits: list[str] = ["## User context"]

    # ── Identity + session line ─────────────────────────────────────
    ident_parts: list[str] = []
    if ctx.full_name:
        ident_parts.append(f"name={ctx.full_name}")
    if ctx.kite_connected is not None:
        ident_parts.append(
            f"kite={'connected' if ctx.kite_connected else 'not-connected'}"
        )
    if ident_parts:
        bits.append("- " + ", ".join(ident_parts))

    # ── Portfolio totals ────────────────────────────────────────────
    totals_parts: list[str] = []
    if ctx.portfolio_total_inr is not None:
        totals_parts.append(f"total=₹{ctx.portfolio_total_inr:,.0f}")
    if ctx.holdings_count is not None:
        totals_parts.append(f"holdings={ctx.holdings_count} symbols")
    if ctx.cash_buffer_inr is not None:
        totals_parts.append(f"cash=₹{ctx.cash_buffer_inr:,.0f}")
    if totals_parts:
        bits.append("- Portfolio: " + ", ".join(totals_parts))

    # ── Top holdings ────────────────────────────────────────────────
    # Compact one-line-per-row format so the model can scan it but it
    # never balloons into a JSON dump. ~30 tokens per row, 5 rows max.
    if ctx.top_holdings:
        bits.append("- Top holdings (desc by value):")
        for h in ctx.top_holdings[:5]:
            sym = h.get("symbol", "?")
            qty = h.get("qty")
            lp = h.get("last_price")
            val = h.get("value_inr")
            day = h.get("day_pct")
            row = f"  • {sym}"
            row_parts: list[str] = []
            if qty is not None:
                row_parts.append(f"qty={qty}")
            if lp is not None:
                row_parts.append(f"ltp=₹{lp:,.2f}")
            if val is not None:
                row_parts.append(f"value=₹{val:,.0f}")
            if isinstance(day, (int, float)):
                sign = "+" if day >= 0 else ""
                row_parts.append(f"day={sign}{day:.2f}%")
            if row_parts:
                row += " " + " ".join(row_parts)
            bits.append(row)

    # ── Active automations ──────────────────────────────────────────
    # The model previously had to call `list_strategies` to learn names
    # / next-run times. Surfacing the same data here saves that hop on
    # ~every turn where the user references "that agent" / "pause it".
    if ctx.active_workflows:
        n = len(ctx.active_workflows)
        bits.append(f"- Active automations ({n}):")
        for wf in ctx.active_workflows[:10]:
            name = wf.get("name") or "(unnamed)"
            wid = wf.get("id") or "?"
            step0 = wf.get("step0_type") or "?"
            nxt = wf.get("next_run_at") or "—"
            last = wf.get("last_run_at") or "—"
            bits.append(
                f'  • "{name}" id={wid} step0={step0} '
                f"next={nxt} last={last}"
            )
    elif ctx.active_workflows_count is not None:
        # Backward compat — when only the count was passed (e.g. an
        # older caller), render that rather than nothing.
        bits.append(f"- Active automations: {ctx.active_workflows_count}")

    # ── Saved equity baskets ────────────────────────────────────────
    # So "rebalance / backtest / deploy my <name> basket" resolves without
    # a discovery round-trip. Compact: name + constituent symbols.
    if ctx.saved_baskets:
        n = len(ctx.saved_baskets)
        bits.append(f"- Saved baskets ({n}):")
        for b in ctx.saved_baskets[:10]:
            name = b.get("name") or "(unnamed)"
            bid = b.get("id") or "?"
            syms = b.get("symbols") or []
            nsym = b.get("n") or len(syms)
            preview = ", ".join(str(s) for s in syms[:6])
            more = f" +{nsym - 6} more" if nsym > 6 else ""
            bits.append(f'  • "{name}" id={bid} ({nsym}): {preview}{more}')

    # ── Watchlist (compact one-liner) ───────────────────────────────
    if ctx.watchlist_symbols:
        wl = ", ".join(ctx.watchlist_symbols[:3])
        bits.append(f"- Watchlist (newest 3): {wl}")

    return "\n".join(bits) if len(bits) > 1 else ""


def _current_date_line() -> str:
    """A real, always-fresh "today" fact — computed per call, never
    cached (unlike the file loaders above, which cache static content).

    Without this, the ONLY date-shaped text anywhere in the assembled
    prompt was a worked-example table in system_core.md illustrating
    `valid_until` resolution, headed "assume today is 2026-05-28" — the
    model had nothing else to anchor "today" to, so it read that
    illustrative placeholder as fact (reproduced live 2026-07-14: asked
    directly, it answered "May 28, 2026"). Fixing it here, dynamically,
    means it can't go stale again the way a hardcoded prompt string does.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    return (
        "## Current date\n"
        f"Today is {now.strftime('%Y-%m-%d')} ({now.strftime('%A')}), "
        "Asia/Kolkata. Use this — not any date in an illustrative example "
        "elsewhere in this prompt — for every relative-date resolution "
        "(valid_until, \"this week\", \"tomorrow\", expiries, schedules)."
    )


def build_system_prompt(
    role: PromptRole,
    user_context: Optional[UserContext] = None,
    extra_context: Optional[dict[str, Any]] = None,
) -> str:
    """Build the system prompt for a given role.

    Layers:
      1. Role identity + instructions (from system.md for 'chat',
         else from ROLE_INSTRUCTIONS).
      2. Domain primer (always included).
      3. Current date (always included, computed fresh every call) —
         placed AFTER the large stable blocks above so the prompt-cache
         prefix (role instructions + calibration examples + domain
         primer, thousands of tokens, rarely changes) survives the
         once-a-day rollover; only this line and whatever follows it
         needs revalidating at midnight IST.
      4. User context (only when provided).
      5. Extra context (only when provided) — caller-injected text,
         e.g. catalog summary for propose_workflow.

    Returns a single newline-joined string ready to send as the system
    message content.
    """
    parts: list[str] = []

    if role == "chat":
        parts.append(_load_chat_system_md())
        # Calibration examples live RIGHT AFTER the role instructions so
        # they sit in the cached prefix. Not loaded for non-chat roles
        # (propose_workflow has its own catalog block; narrate_tool_result
        # doesn't make tool-call decisions).
        examples_block = _load_agentic_examples()
        if examples_block:
            parts.append(examples_block)
    else:
        parts.append(ROLE_INSTRUCTIONS.get(role, _CHAT_FALLBACK).strip())

    parts.append(_load_domain_primer())
    parts.append(_current_date_line())

    if user_context:
        block = _format_user_context(user_context)
        if block:
            parts.append(block)

    if extra_context:
        # Caller controls the title; we just stringify each value.
        ec_lines = ["## Additional context"]
        for k, v in extra_context.items():
            ec_lines.append(f"### {k}\n{v}")
        parts.append("\n".join(ec_lines))

    return "\n\n".join(parts)
