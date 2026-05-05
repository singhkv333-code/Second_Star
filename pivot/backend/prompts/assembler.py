"""Single source of truth for system-prompt assembly.

Every Sarvam / OpenAI call across the codebase goes through
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
    """
    user_id: int
    full_name: Optional[str] = None
    portfolio_total_inr: Optional[float] = None
    holdings_count: Optional[int] = None
    active_workflows_count: Optional[int] = None


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
    """Existing system.md is the chat role's full instructions; we load it
    rather than the inline _CHAT_FALLBACK when the file is present."""
    p = PROMPTS_DIR / "system.md"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return _CHAT_FALLBACK


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
    _load_agentic_examples.cache_clear()


# ── Public entry point ──────────────────────────────────────────────


def _format_user_context(ctx: UserContext) -> str:
    """Compact user-context block. ~80 tokens."""
    bits: list[str] = ["## User context"]
    if ctx.full_name:
        bits.append(f"- Name: {ctx.full_name}")
    if ctx.portfolio_total_inr is not None:
        bits.append(f"- Portfolio total: ₹{ctx.portfolio_total_inr:,.0f}")
    if ctx.holdings_count is not None:
        bits.append(f"- Holdings: {ctx.holdings_count} symbols")
    if ctx.active_workflows_count is not None:
        bits.append(f"- Active agents: {ctx.active_workflows_count}")
    return "\n".join(bits) if len(bits) > 1 else ""


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
      3. User context (only when provided).
      4. Extra context (only when provided) — caller-injected text,
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
