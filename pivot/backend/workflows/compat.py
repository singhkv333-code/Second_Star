"""Single source of truth for step compatibility.

This module is pure: no I/O, no LLM, no network, no DB. It mirrors the
conventions of ``backend.workflows.refs`` and
``backend.workflows.dsl.validators`` — Pydantic v2 models, strict typing,
``from __future__ import annotations``.

The capability rules are ported verbatim from the interactive HTML spec
at ``docs/plans/WORKFLOW_EDITOR_PLAN.html`` (the ``STEPS``, ``CAPS`` and
``NEEDS_*`` data objects). Steps not present in that spec default to a
permissive empty :class:`StepCompat` so unknown step types lint clean
(the unknown-type error is raised separately, by the structural pass).

The public surface is:

  - :class:`AmbientState`    user-book flags from the engine
  - :class:`Diagnostic`      one lint finding
  - :class:`Requirement`     "this step needs one of these tags"
  - :class:`StepCompat`      a step's produces/requires/consumes
  - :data:`CAPABILITY_RULES` dict[str, StepCompat] keyed by step_type
  - :func:`step_compat`      lookup with permissive default
  - :func:`catalog_compat`   FE-shaped dict for the catalog endpoint
  - :func:`lint_workflow`    the three-pass linter

CRITICAL: ``backend.workflows.registry`` is **lazily** imported inside
:func:`lint_workflow` only — importing it at module top would create a
circular dependency (registry imports step modules which may import this
module for self-checks).
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AmbientState(BaseModel):
    """User-book flags the engine knows about but the workflow itself
    didn't produce in-flow. Defaults are permissive-unknown — when no
    ambient is supplied, ``requires`` whose only path was ambient fall
    through to a warning (never an error)."""

    model_config = ConfigDict(extra="forbid")

    held_symbols: list[str] = Field(default_factory=list)
    has_pending_orders: bool = False


Severity = Literal["error", "warning", "info"]


# Closed set of diagnostic codes — frontend dispatches on these.
DiagnosticCode = Literal[
    "ref_forward",
    "ref_bad_path",
    "ref_type",
    "needs_position",
    "needs_pending_orders",
    "needs_symbols",
    "needs_boolean",
    "trigger_placement",
    "empty_branch",
    "unknown_step_type",
    "dead_branch",
]


class Diagnostic(BaseModel):
    """One lint finding, surfaced 1:1 to the FE workflow editor."""

    model_config = ConfigDict(extra="forbid")

    step_index: int
    severity: Severity
    code: DiagnosticCode
    message: str
    field: Optional[str] = None
    suggested_fix: Optional[str] = None


class Requirement(BaseModel):
    """A capability the step needs from accumulated produces OR from an
    ambient flag. ``any_of`` lists the capability tags that satisfy the
    requirement; if none are in the accumulated state, the linter falls
    back to checking the ``ambient`` slot (if set)."""

    model_config = ConfigDict(extra="forbid")

    any_of: list[str] = Field(default_factory=list)
    ambient: Optional[Literal["positions", "pending_orders"]] = None
    label: str
    warn: str
    code: DiagnosticCode


class StepCompat(BaseModel):
    """A step type's compatibility rule. Empty default = permissive."""

    model_config = ConfigDict(extra="forbid")

    produces: list[str] = Field(default_factory=list)
    requires: list[Requirement] = Field(default_factory=list)
    consumes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared requirements (from the HTML NEEDS_* objects, verbatim)
# ---------------------------------------------------------------------------


_NEEDS_POS = Requirement(
    any_of=["position_open"],
    ambient="positions",
    label="an open position",
    warn=(
        "needs a position — open one earlier, or it must already be in "
        "your portfolio"
    ),
    code="needs_position",
)

_NEEDS_ORD = Requirement(
    any_of=["pending_orders"],
    ambient="pending_orders",
    label="a pending order",
    warn=(
        "needs a pending order — place one earlier, or have one resting "
        "in your account"
    ),
    code="needs_pending_orders",
)

_NEEDS_SYMS = Requirement(
    any_of=["data:screen", "data:movers"],
    ambient=None,
    label="a symbols list",
    warn=(
        "give an inline symbol list, or add Screen stocks / Top movers "
        "first"
    ),
    code="needs_symbols",
)

_NEEDS_BOOL = Requirement(
    any_of=["data:news"],
    ambient=None,
    label="a yes/no value",
    warn="add a step that yields a true/false value first (e.g. Recent news)",
    code="needs_boolean",
)


# ---------------------------------------------------------------------------
# CAPABILITY_RULES — ported verbatim from the HTML STEPS object.
# ---------------------------------------------------------------------------


CAPABILITY_RULES: dict[str, StepCompat] = {
    # ── TRIGGERS ─────────────────────────────────────────────────────
    "trigger.schedule": StepCompat(),
    "trigger.market_relative_time": StepCompat(),
    "trigger.price": StepCompat(),
    "trigger.indicator": StepCompat(),
    "trigger.compound": StepCompat(),
    "trigger.exit_compound": StepCompat(requires=[_NEEDS_POS]),
    "trigger.expiry_day": StepCompat(),
    "trigger.event": StepCompat(produces=["data:news"]),
    "trigger.ipo_open": StepCompat(),
    "trigger.polymarket": StepCompat(produces=["webhook_payload"]),
    "trigger.manual": StepCompat(),
    "trigger.webhook": StepCompat(produces=["webhook_payload"]),
    # ── FETCHES ──────────────────────────────────────────────────────
    "fetch.quote": StepCompat(produces=["data:quote", "data:price_level"]),
    "fetch.day_open": StepCompat(produces=["data:price_level"]),
    "fetch.prior_close": StepCompat(produces=["data:price_level"]),
    "fetch.indicator": StepCompat(produces=["data:indicator"]),
    "fetch.rolling_high": StepCompat(produces=["data:price_level"]),
    "fetch.rolling_low": StepCompat(produces=["data:price_level"]),
    "fetch.relative_threshold": StepCompat(produces=["data:price_level"]),
    "fetch.spread_z_score": StepCompat(produces=["data:spread"]),
    "fetch.fundamental": StepCompat(produces=["data:fundamental"]),
    "fetch.portfolio": StepCompat(produces=["data:portfolio"]),
    "fetch.intraday_pnl": StepCompat(produces=["data:pnl"]),
    "fetch.news": StepCompat(produces=["data:news"]),
    "fetch.screener": StepCompat(produces=["data:screen"]),
    "fetch.top_movers": StepCompat(produces=["data:movers"]),
    # ── CONDITIONS ───────────────────────────────────────────────────
    "condition.numeric": StepCompat(),
    "condition.boolean": StepCompat(requires=[_NEEDS_BOOL]),
    "condition.market_status": StepCompat(),
    "condition.time_window": StepCompat(),
    "condition.position": StepCompat(),
    "condition.compound": StepCompat(),
    # ── ACTIONS ──────────────────────────────────────────────────────
    "action.place_order": StepCompat(
        produces=["position_open", "pending_orders"]
    ),
    "action.cancel_orders": StepCompat(
        requires=[_NEEDS_ORD],
        consumes=["pending_orders"],
    ),
    "action.set_stoploss": StepCompat(
        requires=[_NEEDS_POS],
        produces=["protective_order", "pending_orders"],
    ),
    "action.set_takeprofit": StepCompat(
        requires=[_NEEDS_POS],
        produces=["protective_order", "pending_orders"],
    ),
    "action.squareoff_symbol": StepCompat(
        requires=[_NEEDS_POS],
        consumes=["position_open"],
    ),
    "action.squareoff_all": StepCompat(
        requires=[_NEEDS_POS],
        consumes=["position_open"],
    ),
    "action.squareoff_all_intraday": StepCompat(
        requires=[_NEEDS_POS],
        consumes=["position_open"],
    ),
    "action.allocate_basket": StepCompat(
        produces=["position_open", "pending_orders"]
    ),
    "action.allocate_notional": StepCompat(
        requires=[_NEEDS_SYMS],
        produces=["position_open", "pending_orders"],
    ),
    "action.place_option_strategy": StepCompat(
        produces=["position_open", "pending_orders"]
    ),
    "action.arm_ipo_intent": StepCompat(),
    "action.update_watchlist": StepCompat(),
    # ── COMMUNICATION ────────────────────────────────────────────────
    "notify.message": StepCompat(),
    "notify.log": StepCompat(),
    "wait.approval": StepCompat(),
    # ── CONTROL FLOW ─────────────────────────────────────────────────
    "wait.delay": StepCompat(),
    "control.skip_if": StepCompat(),
}


# Numeric "slots" — config fields whose ref-resolved values are typed
# against the producing step's output_schema. If the ref points at a
# non-numeric JSON-Schema leaf (string/boolean/array/object/null) we emit
# ``ref_type``. Keyed by step_type, then field name. Defense in depth —
# Pydantic also rejects most of these at validation time, but the linter
# is what the FE editor surfaces while the user is still typing.
_NUMERIC_SLOTS: dict[str, frozenset[str]] = {
    "condition.numeric": frozenset({"left", "right"}),
    "action.set_stoploss": frozenset({"trigger_price", "limit_price"}),
    "action.set_takeprofit": frozenset({"trigger_price", "limit_price"}),
    "action.place_order": frozenset({"limit_price", "trigger_price"}),
    "action.allocate_notional": frozenset({"total_inr"}),
}

# JSON-Schema "type" values we consider numeric.
_NUMERIC_TYPES: frozenset[str] = frozenset({"number", "integer"})

# Severity rank for stable sorting (error < warning < info, per task spec).
_SEVERITY_RANK: dict[Severity, int] = {"error": 0, "warning": 1, "info": 2}

# Categories used by the structural pass. We do not import CATEGORIES
# from registry — derive the category from the step_type prefix, which
# matches registry's convention exactly ("trigger.*", "fetch.*", …).
_TRIGGER_PREFIX = "trigger."
_ACTION_PREFIX = "action."
_NOTIFY_PREFIX = "notify."
_CONTROL_PREFIX = "control."
# wait.approval is category=notify, wait.delay is category=control in the
# HTML spec. Treat both as terminating "the branch did something" so a
# branch made of triggers + waits doesn't get flagged dead.
_WAIT_NOTIFY = {"wait.approval"}
_WAIT_CONTROL = {"wait.delay"}


# ---------------------------------------------------------------------------
# Public lookup helpers
# ---------------------------------------------------------------------------


def step_compat(step_type: str) -> StepCompat:
    """Return the capability rule for ``step_type`` or a permissive
    empty :class:`StepCompat` if the type isn't in the static catalog.
    Never raises — the structural pass owns unknown-type reporting."""

    return CAPABILITY_RULES.get(step_type, StepCompat())


def catalog_compat(step_type: str) -> dict[str, Any]:
    """Serialise the rule for the FE catalog endpoint. The shape is
    stable: ``{produces, requires:[{any_of, ambient, label, warn}],
    consumes}``. The ``code`` field is intentionally **not** exposed —
    it's a backend lint artefact, not a UI affordance."""

    rule = step_compat(step_type)
    return {
        "produces": list(rule.produces),
        "requires": [
            {
                "any_of": list(req.any_of),
                "ambient": req.ambient,
                "label": req.label,
                "warn": req.warn,
            }
            for req in rule.requires
        ],
        "consumes": list(rule.consumes),
    }


# ---------------------------------------------------------------------------
# Ref extraction
# ---------------------------------------------------------------------------


# Reuse the same ref family as backend.workflows.refs (kept in sync by
# this comment + a unit test that asserts both regexes match the same
# strings). We don't import refs._REF_RE because it's a private member.
_REF_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _iter_refs(value: Any) -> Iterable[tuple[str, str]]:
    """Yield ``(field_path, ref_body)`` for every ``{{...}}`` found in
    ``value``. ``field_path`` is a dotted/bracketed path describing where
    in the config the ref lives (best-effort, for diagnostics)."""

    def _walk(v: Any, path: str) -> Iterable[tuple[str, str]]:
        if isinstance(v, Mapping):
            for k, vv in v.items():
                child = f"{path}.{k}" if path else str(k)
                yield from _walk(vv, child)
        elif isinstance(v, list):
            for i, vv in enumerate(v):
                child = f"{path}[{i}]" if path else f"[{i}]"
                yield from _walk(vv, child)
        elif isinstance(v, str):
            for m in _REF_RE.finditer(v):
                yield path, m.group(1).strip()
        # other primitives: nothing to do

    yield from _walk(value, "")


# ---------------------------------------------------------------------------
# Step normalisation
# ---------------------------------------------------------------------------


def _step_type_of(step: Any) -> str:
    """Defensively extract step_type from either a dict or an object."""

    if isinstance(step, Mapping):
        st = step.get("step_type")
    else:
        st = getattr(step, "step_type", None)
    return str(st) if st is not None else ""


def _config_of(step: Any) -> Mapping[str, Any]:
    """Defensively extract config from either a dict or an object.
    Returns an empty dict if absent — the linter never raises on
    malformed input; it reports."""

    if isinstance(step, Mapping):
        cfg = step.get("config", {})
    else:
        cfg = getattr(step, "config", {})
    if isinstance(cfg, Mapping):
        return cfg
    return {}


# ---------------------------------------------------------------------------
# Output-schema introspection (lazy — registry imported inside callers)
# ---------------------------------------------------------------------------


def _output_schema_for(
    step_type: str,
    registry_module: Any,
) -> Optional[Mapping[str, Any]]:
    """Look up a step's ``output_schema`` via the lazily-imported
    registry module. Returns None for unknown types or steps with no
    declared output."""

    step_registry = registry_module.STEP_REGISTRY
    definition = step_registry.get(step_type)
    if definition is None:
        return None
    schema = definition.output_schema
    if not isinstance(schema, Mapping):
        return None
    return schema


def _walk_output_path(
    schema: Mapping[str, Any],
    path: list[str],
) -> tuple[Optional[Mapping[str, Any]], Optional[str]]:
    """Walk ``path`` through ``schema``'s ``properties`` tree. Returns
    ``(leaf_schema, ambiguity)`` where ``ambiguity`` is a tag explaining
    why we stopped without a leaf:

      - "missing"   path explicitly absent in properties → ref_bad_path
      - "loose"     hit an array/loose-object subtree we can't resolve
                    → caller downgrades to info to avoid false positives
      - None        ``leaf_schema`` is the resolved leaf node
    """

    cur: Mapping[str, Any] = schema
    for segment in path:
        cur_type = cur.get("type")
        # Arrays / additional-properties objects: can't resolve further.
        if cur_type == "array":
            return None, "loose"
        if cur_type == "object" and "properties" not in cur:
            return None, "loose"
        if cur_type is not None and cur_type not in ("object",):
            # We're walking past a primitive — definitively bad path.
            return None, "missing"

        props = cur.get("properties")
        if not isinstance(props, Mapping):
            return None, "loose"
        if segment not in props:
            return None, "missing"
        nxt = props[segment]
        if not isinstance(nxt, Mapping):
            return None, "loose"
        cur = nxt
    return cur, None


def _is_numeric_slot(step_type: str, field_path: str) -> bool:
    """True if ``field_path`` lands on one of the numeric slots for
    ``step_type``. We match on the **first** path segment, which is the
    top-level config field — refs deep inside nested structures (the DSL
    tree, basket legs) are not type-checked here; the DSL has its own
    validator."""

    slots = _NUMERIC_SLOTS.get(step_type)
    if not slots:
        return False
    head = field_path.split(".", 1)[0].split("[", 1)[0]
    return head in slots


def _leaf_is_numeric(leaf: Optional[Mapping[str, Any]]) -> Optional[bool]:
    """Return True if leaf is numeric, False if definitively not,
    None if undeterminable (no type/oneOf/anyOf complexity)."""

    if leaf is None:
        return None
    t = leaf.get("type")
    if isinstance(t, str):
        return t in _NUMERIC_TYPES
    if isinstance(t, list):
        return any(x in _NUMERIC_TYPES for x in t if isinstance(x, str))
    # No explicit type — try anyOf/oneOf shallowly.
    for key in ("anyOf", "oneOf"):
        alts = leaf.get(key)
        if isinstance(alts, list):
            for alt in alts:
                if isinstance(alt, Mapping):
                    sub = _leaf_is_numeric(alt)
                    if sub is True:
                        return True
            return False
    return None


# ---------------------------------------------------------------------------
# Lint passes
# ---------------------------------------------------------------------------


def _is_trigger(step_type: str) -> bool:
    return step_type.startswith(_TRIGGER_PREFIX)


def _is_branch_useful(step_type: str) -> bool:
    """Does this step count as "the branch did something meaningful"?
    Used by the dead-branch check."""

    if (
        step_type.startswith(_ACTION_PREFIX)
        or step_type.startswith(_NOTIFY_PREFIX)
        or step_type.startswith(_CONTROL_PREFIX)
    ):
        return True
    if step_type in _WAIT_NOTIFY or step_type in _WAIT_CONTROL:
        return True
    return False


def _pass_structural(
    norm_steps: list[tuple[str, Mapping[str, Any]]],
) -> list[Diagnostic]:
    """Trigger placement, empty-branch and dead-branch checks."""

    out: list[Diagnostic] = []
    n = len(norm_steps)
    if n == 0:
        return out

    # Step 0 must be a trigger.
    first_type = norm_steps[0][0]
    if not _is_trigger(first_type):
        out.append(
            Diagnostic(
                step_index=0,
                severity="error",
                code="trigger_placement",
                message=(
                    "the first step must be a trigger "
                    f"(got {first_type!r})"
                ),
                suggested_fix=(
                    "insert a trigger.* step at position 0, or remove "
                    "this step"
                ),
            )
        )

    # Walk branches. A "branch" starts at index 0 or at any later
    # trigger. Empty-branch error: trigger immediately followed by
    # another trigger, or trigger at the last index.
    branch_starts: list[int] = []
    for i, (st, _cfg) in enumerate(norm_steps):
        if _is_trigger(st):
            branch_starts.append(i)
            # Trigger immediately followed by another trigger — empty.
            if i + 1 < n and _is_trigger(norm_steps[i + 1][0]):
                out.append(
                    Diagnostic(
                        step_index=i,
                        severity="error",
                        code="empty_branch",
                        message=(
                            "this trigger has no steps before the next "
                            "trigger starts a new branch"
                        ),
                    )
                )
            # Trigger as the final step — empty.
            if i == n - 1 and n > 1:
                out.append(
                    Diagnostic(
                        step_index=i,
                        severity="error",
                        code="empty_branch",
                        message="this trigger has no steps after it",
                    )
                )

    # Dead-branch (info): a branch with no action/notify/control step.
    # A workflow that's just a trigger (n==1) is allowed without warning
    # — that's a manual/webhook ping by itself.
    if n > 1:
        for bi, start in enumerate(branch_starts):
            end = (
                branch_starts[bi + 1]
                if bi + 1 < len(branch_starts)
                else n
            )
            body = norm_steps[start + 1 : end]
            if not body:
                # Already covered by empty_branch error above.
                continue
            if not any(_is_branch_useful(st) for st, _ in body):
                out.append(
                    Diagnostic(
                        step_index=start,
                        severity="info",
                        code="dead_branch",
                        message=(
                            "this branch has no action, notification "
                            "or control step — it will fire but do "
                            "nothing observable"
                        ),
                    )
                )

    return out


def _pass_refs(
    norm_steps: list[tuple[str, Mapping[str, Any]]],
    registry_module: Any,
) -> list[Diagnostic]:
    """Ref forward/bad-path/type checks."""

    out: list[Diagnostic] = []
    n = len(norm_steps)
    first_type = norm_steps[0][0] if n else ""

    for i, (step_type, cfg) in enumerate(norm_steps):
        for field_path, body in _iter_refs(cfg):
            # We only check `context.<head>.<tail>` refs here; other
            # namespaces (now, workflow.*) carry no cross-step typing.
            if not body.startswith("context."):
                continue
            rest = body[len("context.") :]
            if not rest:
                continue
            segments = rest.split(".")
            head, tail = segments[0], segments[1:]

            # webhook_payload — valid only if step 0 is trigger.webhook.
            if head == "webhook_payload":
                if first_type != "trigger.webhook":
                    out.append(
                        Diagnostic(
                            step_index=i,
                            severity="error",
                            code="ref_bad_path",
                            message=(
                                "{{ context.webhook_payload.* }} is "
                                "only valid when step 0 is "
                                "trigger.webhook"
                            ),
                            field=field_path or None,
                            suggested_fix=(
                                "change the trigger to trigger.webhook, "
                                "or use a different data source"
                            ),
                        )
                    )
                continue

            # Otherwise head must be a step index.
            try:
                ref_idx = int(head)
            except ValueError:
                out.append(
                    Diagnostic(
                        step_index=i,
                        severity="error",
                        code="ref_bad_path",
                        message=(
                            f"context.{head!r} is not a step index"
                        ),
                        field=field_path or None,
                    )
                )
                continue

            if ref_idx >= i or ref_idx < 0:
                out.append(
                    Diagnostic(
                        step_index=i,
                        severity="error",
                        code="ref_forward",
                        message=(
                            f"step {i} cannot reference step "
                            f"{ref_idx} — refs can only point at "
                            "earlier steps"
                        ),
                        field=field_path or None,
                    )
                )
                continue

            if ref_idx >= n:
                out.append(
                    Diagnostic(
                        step_index=i,
                        severity="error",
                        code="ref_forward",
                        message=(
                            f"step {ref_idx} does not exist (workflow "
                            f"has {n} steps)"
                        ),
                        field=field_path or None,
                    )
                )
                continue

            producer_type = norm_steps[ref_idx][0]
            schema = _output_schema_for(producer_type, registry_module)
            if schema is None:
                # Producer has no declared schema — can't type-check.
                # We don't error here: e.g. notify steps may legitimately
                # be referenced for their boolean "sent" result and the
                # registry just hasn't declared it yet. Stay silent.
                continue

            leaf, ambiguity = _walk_output_path(schema, tail)
            if ambiguity == "missing":
                props = schema.get("properties")
                avail = (
                    sorted(props.keys())
                    if isinstance(props, Mapping)
                    else []
                )
                fix = (
                    f"available fields on step {ref_idx}: "
                    f"{', '.join(avail)}"
                    if avail
                    else None
                )
                out.append(
                    Diagnostic(
                        step_index=i,
                        severity="error",
                        code="ref_bad_path",
                        message=(
                            f"step {ref_idx} ({producer_type}) has no "
                            f"output field {'.'.join(tail)!r}"
                        ),
                        field=field_path or None,
                        suggested_fix=fix,
                    )
                )
                continue
            if ambiguity == "loose":
                # Array/loose-object subtree — downgrade to info to
                # avoid false positives.
                out.append(
                    Diagnostic(
                        step_index=i,
                        severity="info",
                        code="ref_bad_path",
                        message=(
                            f"could not verify path {'.'.join(tail)!r} "
                            f"on step {ref_idx} ({producer_type}); "
                            "this is informational only"
                        ),
                        field=field_path or None,
                    )
                )
                continue

            # Leaf resolved — type-check if this slot is numeric.
            if _is_numeric_slot(step_type, field_path):
                numeric = _leaf_is_numeric(leaf)
                if numeric is False:
                    out.append(
                        Diagnostic(
                            step_index=i,
                            severity="error",
                            code="ref_type",
                            message=(
                                f"{step_type}.{field_path} expects a "
                                f"number but step {ref_idx} "
                                f"({producer_type}) returns a "
                                f"non-numeric value at "
                                f"{'.'.join(tail)!r}"
                            ),
                            field=field_path or None,
                        )
                    )

    return out


def _pass_capability(
    norm_steps: list[tuple[str, Mapping[str, Any]]],
    ambient: AmbientState,
    ref_indices: set[int],
) -> list[Diagnostic]:
    """Walk steps and emit capability warnings. Branch-resets on every
    trigger. Suppress at any step_index already flagged by the ref pass —
    that diagnostic is more precise."""

    out: list[Diagnostic] = []
    state: set[str] = set()

    has_positions_ambient = bool(ambient.held_symbols)
    has_orders_ambient = ambient.has_pending_orders

    for i, (step_type, _cfg) in enumerate(norm_steps):
        # Branch reset on triggers (including the first step).
        if _is_trigger(step_type):
            state = set()

        rule = step_compat(step_type)

        # Check requirements BEFORE we add this step's produces.
        if i not in ref_indices:
            for req in rule.requires:
                satisfied = any(tag in state for tag in req.any_of)
                if not satisfied and req.ambient is not None:
                    if (
                        req.ambient == "positions"
                        and has_positions_ambient
                    ):
                        satisfied = True
                    elif (
                        req.ambient == "pending_orders"
                        and has_orders_ambient
                    ):
                        satisfied = True
                if not satisfied:
                    out.append(
                        Diagnostic(
                            step_index=i,
                            severity="warning",
                            code=req.code,
                            message=(
                                f"{step_type} {req.warn} "
                                f"(needs {req.label})"
                            ),
                        )
                    )

        # Mutate state: remove consumes, add produces.
        for tag in rule.consumes:
            state.discard(tag)
        for tag in rule.produces:
            state.add(tag)

    return out


def _pass_unknown(
    norm_steps: list[tuple[str, Mapping[str, Any]]],
    registry_module: Any,
) -> list[Diagnostic]:
    """Flag step types not in the registry. We treat the *registry* as
    the closed list for "is this a real step type?" — :data:`CAPABILITY_RULES`
    may legitimately omit entries (which fall through to a permissive
    default), but an unknown registry entry is always an error."""

    out: list[Diagnostic] = []
    known = registry_module.STEP_REGISTRY
    for i, (step_type, _cfg) in enumerate(norm_steps):
        if not step_type:
            out.append(
                Diagnostic(
                    step_index=i,
                    severity="error",
                    code="unknown_step_type",
                    message="step has no step_type",
                )
            )
            continue
        if step_type not in known:
            out.append(
                Diagnostic(
                    step_index=i,
                    severity="error",
                    code="unknown_step_type",
                    message=f"unknown step_type {step_type!r}",
                )
            )
    return out


def _dedupe_and_sort(diags: list[Diagnostic]) -> list[Diagnostic]:
    """Dedupe on (step_index, code, message, field), then sort by
    (step_index, severity rank)."""

    seen: set[tuple[int, str, str, Optional[str]]] = set()
    unique: list[Diagnostic] = []
    for d in diags:
        key = (d.step_index, d.code, d.message, d.field)
        if key in seen:
            continue
        seen.add(key)
        unique.append(d)
    unique.sort(key=lambda d: (d.step_index, _SEVERITY_RANK[d.severity]))
    return unique


def lint_workflow(
    steps: Any,
    *,
    ambient: AmbientState | None = None,
) -> list[Diagnostic]:
    """Run all three lint passes over ``steps``.

    ``steps`` may be a list of dicts (``{step_type, config}``) **or**
    objects exposing ``.step_type`` / ``.config`` — both shapes are
    normalised defensively.

    Returns the deduped, sorted list of diagnostics. The function is
    pure and synchronous; it never raises on malformed input — anything
    unparseable shows up as a diagnostic.
    """

    if ambient is None:
        ambient = AmbientState()

    # Lazy import — keeps this module free of registry-side cycles.
    from backend.workflows import registry as registry_module  # noqa: WPS433

    if not isinstance(steps, list):
        # Single defensive guard: callers should pass a list; if they
        # don't, return an empty diag list rather than blowing up.
        return []

    norm_steps: list[tuple[str, Mapping[str, Any]]] = [
        (_step_type_of(s), _config_of(s)) for s in steps
    ]

    diags: list[Diagnostic] = []
    diags.extend(_pass_unknown(norm_steps, registry_module))
    diags.extend(_pass_structural(norm_steps))
    ref_diags = _pass_refs(norm_steps, registry_module)
    diags.extend(ref_diags)
    ref_indices = {d.step_index for d in ref_diags}
    diags.extend(_pass_capability(norm_steps, ambient, ref_indices))

    return _dedupe_and_sort(diags)


__all__ = [
    "AmbientState",
    "Diagnostic",
    "DiagnosticCode",
    "Severity",
    "Requirement",
    "StepCompat",
    "CAPABILITY_RULES",
    "step_compat",
    "catalog_compat",
    "lint_workflow",
]
