"""Mustache-style ref resolver for inter-step data passing.

Per docs/ARCHITECTURE.md §6, step configs may contain refs of the form
`{{ context.<step_index>.<dotted.path> }}`, `{{ context.webhook_payload.<path> }}`,
`{{ now }}`, or `{{ workflow.<field> }}`. The resolver walks every value in
a step config dict, replacing refs with values from the run-scoped data
sources before the executor sees it.

Allowed namespaces (and ONLY these — anything else raises
RefNotFoundError):

  - context.<step_index>.<dotted.path>   prior step output (str step_index)
  - context.webhook_payload.<dotted.path> inbound webhook body
  - now                                  ISO 8601 timestamp at resolve time
  - workflow.<field>                     workflow metadata: id|name|version

The resolver returns a fully-resolved deep copy. Refs that are the
entire string get replaced by the typed value (preserving int/float/dict);
refs embedded in larger strings get stringified and concatenated.

All resolution is synchronous and pure — no DB or network access. The
caller is responsible for assembling the `context`, `workflow_meta`, and
`now` arguments.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


# Whole-string ref:        "{{ context.1.buying_power }}"
# Embedded refs match too: "Buy {{context.1.qty}} of {{workflow.name}}"
_REF_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_WHOLE_REF_RE = re.compile(r"^\s*\{\{\s*([^{}]+?)\s*\}\}\s*$")


class RefNotFoundError(ValueError):
    """Raised when a ref points at a path that doesn't exist or names a
    namespace outside the allowed set. Carries the original ref string so
    callers can produce the API_CONTRACT.md §2 error message verbatim."""

    def __init__(self, ref: str, reason: str) -> None:
        self.ref = ref
        self.reason = reason
        super().__init__(f"Reference {{{{{ref}}}}} not found — {reason}")


def _walk(path: list[str], obj: Any) -> Any:
    """Walk a dotted path through nested mappings. Lists are not
    traversable in v1 (no `[0]` indexing) — keep the namespace flat and
    predictable."""
    cur = obj
    for segment in path:
        if not isinstance(cur, Mapping):
            raise KeyError(segment)
        if segment not in cur:
            raise KeyError(segment)
        cur = cur[segment]
    return cur


def _resolve_one(
    ref: str,
    *,
    context: Mapping[str, Any],
    workflow_meta: Mapping[str, Any],
    now: datetime,
) -> Any:
    """Resolve a single ref body (the bit between `{{` and `}}`).

    Raises RefNotFoundError on unknown namespace or missing path.
    """
    body = ref.strip()
    if not body:
        raise RefNotFoundError(ref, "empty reference")

    # `now` is bare — no dotted suffix.
    if body == "now":
        return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    # workflow.<field>
    if body.startswith("workflow."):
        rest = body[len("workflow."):]
        path = rest.split(".") if rest else []
        if not path:
            raise RefNotFoundError(ref, "workflow.<field> requires a field")
        try:
            return _walk(path, workflow_meta)
        except KeyError as e:
            raise RefNotFoundError(
                ref, f"workflow has no field {e.args[0]!r}"
            ) from None

    # context.<step_index>.<path>  OR  context.webhook_payload.<path>
    if body.startswith("context."):
        rest = body[len("context."):]
        if not rest:
            raise RefNotFoundError(ref, "context.<key> requires a key")
        # First segment is either a stringified int step_index OR the
        # literal "webhook_payload" reserved key. Both live in the same
        # context bag — the difference is purely cosmetic.
        path = rest.split(".")
        head, tail = path[0], path[1:]

        if head != "webhook_payload":
            # Must be a non-negative integer step_index. We accept either
            # the stringified int ("1") or — defensively — anything that
            # casts to int. The DB stores keys as strings.
            try:
                int(head)
            except ValueError:
                raise RefNotFoundError(
                    ref,
                    f"context key {head!r} is not a step_index or "
                    f"'webhook_payload'",
                ) from None

        if head not in context:
            raise RefNotFoundError(
                ref,
                f"context has no entry for {head!r} — step has not run "
                f"yet or did not produce output",
            )
        try:
            return _walk(tail, context[head])
        except KeyError as e:
            raise RefNotFoundError(
                ref,
                f"context.{head} has no field {e.args[0]!r}",
            ) from None

    raise RefNotFoundError(
        ref,
        "unknown namespace; allowed: context.*, now, workflow.*",
    )


def resolve_refs(
    value: Any,
    *,
    context: Mapping[str, Any],
    workflow_meta: Mapping[str, Any],
    now: Optional[datetime] = None,
) -> Any:
    """Recursively resolve refs in any JSON-serialisable value.

    - dict: resolve every value (keys are not refs)
    - list: resolve every element
    - str:  if the entire string is one ref, return the typed value;
            otherwise interpolate every ref textually and return a string
    - other primitives: returned unchanged

    Raises RefNotFoundError on any unresolved ref. We fail closed — a
    typo in a config should surface as a step error, not a silent miss.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if isinstance(value, Mapping):
        return {
            k: resolve_refs(
                v,
                context=context,
                workflow_meta=workflow_meta,
                now=now,
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            resolve_refs(
                item,
                context=context,
                workflow_meta=workflow_meta,
                now=now,
            )
            for item in value
        ]
    if isinstance(value, str):
        whole = _WHOLE_REF_RE.match(value)
        if whole:
            return _resolve_one(
                whole.group(1),
                context=context,
                workflow_meta=workflow_meta,
                now=now,
            )

        def _sub(match: re.Match[str]) -> str:
            resolved = _resolve_one(
                match.group(1),
                context=context,
                workflow_meta=workflow_meta,
                now=now,
            )
            return str(resolved)

        return _REF_RE.sub(_sub, value)

    return value
