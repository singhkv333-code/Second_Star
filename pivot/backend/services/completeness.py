"""Schema-driven completeness checker.

The architectural principle: **the schema is the source of truth for
what's required, not the model's introspection.** Every Pivot tool
declares its inputs as a JSON Schema (in `agents/tools.py`); this
module walks that schema to find what's missing or set to a sentinel
value, and produces a structured `MissingField` list the chat can
turn into one focused user-facing question.

This is pure Python. No LLM. Microseconds to run. Reliable, debug-
gable, and not subject to model drift. It is the load-bearing piece
that lets the model do transcription at minimal reasoning effort
while the pipeline owns correctness.

How it interacts with the validate-and-retry loop (Prompt 1):
  - Completeness check runs FIRST. Catches missing required fields
    BEFORE we waste a tool execution or a Pydantic-validate call.
  - Type / enum / format violations are still caught by the
    `_validate_args_against_schema` path in validation_retry.py;
    the two layers are complementary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# Values that look like "I gave a value but it's a placeholder" — the
# completeness check treats them as missing. The model occasionally
# emits these when it can't extract a real value but doesn't want to
# leave the field empty.
_SENTINEL_STRINGS = frozenset({
    "", " ", "unknown", "tbd", "todo", "?", "n/a", "na",
    "<unknown>", "<none>", "<missing>",
    "your_value", "value_here", "fill_in",
})


@dataclass
class MissingField:
    """One required input the model didn't fill (or filled with a
    sentinel)."""
    field_name: str
    description: str             # from the JSON Schema's `description`
    type_hint: str               # human-readable: "integer > 0", "ISO date", etc.
    enum: Optional[list[Any]] = None      # if the schema constrains values
    why_required: str = "Required field — cannot proceed without it."


@dataclass
class CompletenessReport:
    tool_name: str
    missing: list[MissingField] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.missing

    def field_names(self) -> list[str]:
        return [m.field_name for m in self.missing]


# ── Type-hint formatting ───────────────────────────────────────────


def _human_type(prop: dict[str, Any]) -> str:
    """Render a JSON-Schema property as a phrase a user can read.

    Examples:
      {"type": "integer"}                                   → "integer"
      {"type": "integer", "minimum": 1}                     → "integer ≥ 1"
      {"type": "number", "minimum": 0, "maximum": 100}      → "number between 0 and 100"
      {"type": "string", "enum": ["BUY","SELL"]}            → "one of: BUY, SELL"
      {"type": "string", "format": "date"}                  → "ISO date (YYYY-MM-DD)"
      {"type": "array", "items": {"type": "string"}}        → "list of text"
    """
    if not isinstance(prop, dict):
        return "value"

    if "enum" in prop and prop["enum"]:
        opts = ", ".join(str(x) for x in prop["enum"])
        return f"one of: {opts}"

    t = prop.get("type")
    fmt = prop.get("format")

    if t == "string":
        if fmt == "date":
            return "ISO date (YYYY-MM-DD)"
        if fmt == "date-time":
            return "ISO timestamp"
        if "minLength" in prop:
            return f"text (≥ {prop['minLength']} characters)"
        return "text"

    if t == "integer":
        lo = prop.get("minimum")
        hi = prop.get("maximum")
        if lo is not None and hi is not None:
            return f"integer between {lo} and {hi}"
        if lo is not None:
            return f"integer ≥ {lo}"
        if hi is not None:
            return f"integer ≤ {hi}"
        return "integer"

    if t == "number":
        lo = prop.get("minimum")
        hi = prop.get("maximum")
        if lo is not None and hi is not None:
            return f"number between {lo} and {hi}"
        if lo is not None:
            return f"number ≥ {lo}"
        return "number"

    if t == "boolean":
        return "yes/no"

    if t == "array":
        items = prop.get("items") or {}
        return f"list of {_human_type(items)}"

    if t == "object":
        return "object"

    return str(t or "value")


# ── Sentinel detection ─────────────────────────────────────────────


def _is_sentinel(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _SENTINEL_STRINGS
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


# ── Public entry point ─────────────────────────────────────────────


def check_completeness(
    tool_name: str,
    schema: dict[str, Any],
    provided_args: dict[str, Any],
) -> CompletenessReport:
    """Walk the JSON Schema; return required-but-missing fields.

    Args:
      tool_name: for the report so the caller can phrase the question.
      schema: the tool's `parameters` JSON Schema (object-typed).
      provided_args: what the LLM emitted as the tool call's arguments.

    Returns:
      CompletenessReport with `is_complete=True` if all required
      fields are present and non-sentinel.
    """
    if not isinstance(schema, dict):
        return CompletenessReport(tool_name=tool_name)

    required = schema.get("required") or []
    properties = schema.get("properties") or {}

    report = CompletenessReport(tool_name=tool_name)
    for field_name in required:
        prop = properties.get(field_name) or {}
        provided = provided_args.get(field_name)
        if field_name not in provided_args or _is_sentinel(provided):
            report.missing.append(MissingField(
                field_name=field_name,
                description=prop.get("description", "") or "",
                type_hint=_human_type(prop),
                enum=prop.get("enum"),
            ))
    return report
