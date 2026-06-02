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
CATALOG_VERSION = "2026-06-02T12:00:00Z"


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

    category_order = {c["id"]: i for i, c in enumerate(CATEGORIES)}
    sorted_defs = sorted(
        STEP_REGISTRY.values(),
        key=lambda d: (category_order.get(d.category, 999), d.step_type),
    )

    return {
        "catalog_version": CATALOG_VERSION,
        "categories": list(CATEGORIES),
        "step_types": [
            {
                "step_type": d.step_type,
                "category": d.category,
                "label": d.label,
                "description": d.description,
                "icon": d.icon,
                "max_retries": d.max_retries,
                "trigger_only": d.trigger_only,
                "config_schema": d.config_schema,
                "output_schema": d.output_schema,
            }
            for d in sorted_defs
        ],
    }
