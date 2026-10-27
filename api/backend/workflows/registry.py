"""Step-type registry.

Single source of truth for the workflow step catalog. Every step type is
declared here via @register_step, which captures:

  - step_type      "trigger.schedule"
  - category       "trigger" (drives UI grouping in /api/step-types)
  - label          short human label, e.g. "On schedule"
  - description    one-liner shown in the picker
  - icon           lucide-react name, e.g. "clock"
  - max_retries    per-step retry budget (ARCHITECTURE.md §7 invariant 3)
  - trigger_only   true for trigger.* — frontend uses this to gate
                   index-0 placement
  - config_model   Pydantic model whose JSON Schema (draft 2020-12) is
                   used for both API-side validation and UI form gen
  - output_schema  raw JSON-Schema dict describing this step's
                   `output` payload, or None for steps with no output
  - executor       async callable; raises NotImplementedError for stubs
                   on Day 1, real executors land Day 2-4

The registry is the closed list the LLM in propose_workflow MUST choose
from (ARCHITECTURE.md §10). API requests with an unknown step_type are
rejected with `validation_error` before they ever touch the DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel


# Bumped manually whenever a registered step type is added/changed.
# Frontend uses this to invalidate its 5-minute catalog cache. Format:
# ISO-8601 instant string (matches API_CONTRACT.md §8.1).
# 2026-06-04: F&O P3 — +trigger.expiry_day, +action.place_option_strategy,
# +option_metric/option_greek/dte DSL leaves in trigger.compound trees.
# 2026-06-17: +compat/group metadata — each step now emits a `group` label
# (set via register_step(group=...)) and a `compat` block sourced from
# backend.workflows.compat.catalog_compat (produces/requires/consumes).
# 2026-06-18: catalog regroup (17 group taxonomy) + 4 new collapsed steps
# (action.squareoff, action.set_protective, fetch.price_reference,
# fetch.rolling_extreme) + 6 legacy step types marked deprecated=True so
# they remain executable for persisted/active workflows but disappear from
# the picker.
CATALOG_VERSION = "2026-06-18T00:00:00Z"


# Categories listed in the order the picker should render them.
# Mirrors the `categories` array in API_CONTRACT.md §8.1.
CATEGORIES: list[dict[str, str]] = [
    {"id": "trigger", "label": "Triggers"},
    {"id": "fetch", "label": "Data fetches"},
    {"id": "condition", "label": "Conditions"},
    {"id": "action", "label": "Actions"},
    {"id": "notify", "label": "Communication"},
    {"id": "control", "label": "Control flow"},
]
_CATEGORY_IDS = {c["id"] for c in CATEGORIES}


# Type alias for an executor. Real executors will receive (run, step,
# context) and return an output dict (or None). For Day-1 stubs the
# signature is left flexible — engine.py will pin it down on Day 2.
StepExecutor = Callable[..., Awaitable[Optional[dict[str, Any]]]]


@dataclass(frozen=True)
class StepDefinition:
    step_type: str
    category: str
    label: str
    description: str
    icon: str
    max_retries: int
    trigger_only: bool
    config_model: type[BaseModel]
    output_schema: Optional[dict[str, Any]]
    executor: StepExecutor
    # Computed lazily and cached: the JSON Schema derived from
    # config_model. We compute on registration so any Pydantic schema
    # error blows up at import time, not at request time.
    config_schema: dict[str, Any] = field(default_factory=dict)
    # Optional sub-grouping label inside a category — used by the FE
    # picker to cluster e.g. "Price triggers" vs "Indicator triggers"
    # under the same "Triggers" category. Defaults to "" so existing
    # @register_step calls keep working before the catalog-relabel pass
    # populates groups.
    group: str = ""
    # Soft-retire a step type. Deprecated steps STAY in STEP_REGISTRY so
    # persisted/active workflows that still reference them validate +
    # execute, BUT they are excluded from `get_catalog()` so the FE
    # picker no longer offers them. Replacement step types (e.g.
    # `action.squareoff` for the three squareoff_* families) are
    # registered alongside; a `_normalize_deprecated_steps` pass in
    # `propose.py` rewrites freshly-proposed drafts to the new shape.
    deprecated: bool = False


STEP_REGISTRY: dict[str, StepDefinition] = {}


def _build_config_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic v2 model to JSON Schema (draft 2020-12).

    Pydantic v2's default schema emit is draft 2020-12 compatible. We
    strip the `title` keys Pydantic injects for every field — they leak
    Python class names into the contract and don't help the frontend.
    """
    schema = model.model_json_schema(mode="validation")
    schema.pop("title", None)
    props = schema.get("properties")
    if isinstance(props, dict):
        for prop in props.values():
            if isinstance(prop, dict):
                prop.pop("title", None)
    return schema


def register_step(
    *,
    step_type: str,
    category: str,
    label: str,
    description: str,
    icon: str,
    max_retries: int,
    trigger_only: bool,
    config_model: type[BaseModel],
    output_schema: Optional[dict[str, Any]] = None,
    group: str = "",
    deprecated: bool = False,
) -> Callable[[StepExecutor], StepExecutor]:
    """Decorator that registers a step type and returns the executor.

    Raises ValueError at import time if the step_type collides with one
    already registered, or if `category` isn't one of the locked
    CATEGORIES — drift from the catalog would silently break the UI.
    """
    if step_type in STEP_REGISTRY:
        raise ValueError(f"step_type {step_type!r} already registered")
    if category not in _CATEGORY_IDS:
        raise ValueError(
            f"unknown category {category!r}; "
            f"must be one of {sorted(_CATEGORY_IDS)}"
        )
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")

    config_schema = _build_config_schema(config_model)

    def _decorator(fn: StepExecutor) -> StepExecutor:
        STEP_REGISTRY[step_type] = StepDefinition(
            step_type=step_type,
            category=category,
            label=label,
            description=description,
            icon=icon,
            max_retries=max_retries,
            trigger_only=trigger_only,
            config_model=config_model,
            output_schema=output_schema,
            executor=fn,
            config_schema=config_schema,
            group=group,
            deprecated=deprecated,
        )
        return fn

    return _decorator


def get_step_definition(step_type: str) -> StepDefinition:
    """Look up a registered step. Raises KeyError on miss — callers
    should map that to a 422 `validation_error` with a clear message
    listing the known step types."""
    return STEP_REGISTRY[step_type]


def list_step_types() -> list[str]:
    """Sorted list of every registered step type — used by tests and by
    the propose_workflow tool to constrain LLM output."""
    return sorted(STEP_REGISTRY.keys())


def get_catalog() -> dict[str, Any]:
    """Build the catalog response per docs/API_CONTRACT.md §8.1.

    Steps are emitted in (category, step_type) order so the UI doesn't
    have to sort. The order across categories follows CATEGORIES.
    """
    # Force the workflow steps modules to import so the registry is
    # populated. `from backend.workflows import *` triggers __init__.py,
    # but we go through it explicitly here so unit tests that import
    # registry.py directly still see a populated registry.
    import backend.workflows  # noqa: F401

    # Lazy import — compat.lint_workflow lazy-imports this module, so a
    # top-level import would cycle. catalog_compat itself is pure.
    from backend.workflows.compat import catalog_compat

    category_order = {c["id"]: i for i, c in enumerate(CATEGORIES)}
    # Deprecated step types stay in STEP_REGISTRY (so persisted/active
    # workflows continue to validate + execute on the alias) but are
    # HIDDEN from the catalog — the FE picker no longer surfaces them,
    # and propose.py normalises freshly-built drafts onto the new
    # collapsed step_type before validation.
    sorted_defs = sorted(
        (d for d in STEP_REGISTRY.values() if not d.deprecated),
        key=lambda d: (category_order.get(d.category, 999), d.step_type),
    )

    return {
        "catalog_version": CATALOG_VERSION,
        "categories": list(CATEGORIES),
        "step_types": [
            {
                "step_type": d.step_type,
                "category": d.category,
                "group": d.group,
                "label": d.label,
                "description": d.description,
                "icon": d.icon,
                "max_retries": d.max_retries,
                "trigger_only": d.trigger_only,
                "config_schema": d.config_schema,
                "output_schema": d.output_schema,
                "compat": catalog_compat(d.step_type),
            }
            for d in sorted_defs
        ],
    }
