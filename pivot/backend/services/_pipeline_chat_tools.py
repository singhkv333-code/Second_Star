"""Chat-tool handler for ``propose_pipeline_workflow``.

One tool. Compositional workflow builder for shapes that don't fit the
rigid 5-step ``propose_dsl_workflow`` envelope:

  - Multi-branch (2+ independent triggers in one workflow)
  - Multi-tier exits (sell N at +X%, M at +Y%, rest on drawdown)
  - Mixed actions in one branch (compound gate + notify + conditional buy)
  - Branch fan-out (one entry, multiple exits with different DSL trees)

Pattern mirrors ``_dsl_chat_tools.propose_dsl_workflow``: the chat LLM
hands intent in natural language; a server-side translator
(``translate_intent_to_pipeline``) with the full step catalog + DSL
grammar + compositional fewshots in its system prompt builds the
``steps[]`` array. The handler validates each embedded tree, then runs
the registry validator. On validation failure, ONE retry with the
error context appended; second failure surfaces verbatim.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from backend.workflows.dsl.llm_translate import (
    TranslationError,
    translate_intent_to_pipeline,
)


logger = logging.getLogger(__name__)


_EMBEDDED_TREE_STEPS = {
    "trigger.compound",
    "trigger.exit_compound",
    "condition.compound",
}


def _validate_embedded_trees(steps: list[dict]) -> None:
    """Each compound/exit_compound/condition.compound step carries a
    DSL tree at ``config.entry``. Run them through ``semantic_validate``
    so failures come back as DSL-flavoured errors (lower bar than the
    Pydantic config models, but the error messages name the offending
    tree node)."""
    from pydantic import TypeAdapter, ValidationError
    from backend.workflows.dsl.schema import Tree
    from backend.workflows.dsl.validators import (
        DSLValidationError, semantic_validate,
    )

    for idx, step in enumerate(steps):
        st = step.get("step_type")
        if st not in _EMBEDDED_TREE_STEPS:
            continue
        cfg = step.get("config") or {}
        raw_tree = cfg.get("entry")
        if not isinstance(raw_tree, dict):
            raise ValueError(
                f"step {idx} ({st}): config.entry must be a DSL tree object"
            )
        try:
            parsed = TypeAdapter(Tree).validate_python(raw_tree)
            semantic_validate(
                parsed,
                allow_position=(st == "trigger.exit_compound"),
            )
        except (DSLValidationError, ValidationError) as exc:
            raise ValueError(
                f"step {idx} ({st}) tree invalid: {exc}"
            ) from None


def _derive_name(intent: str, fallback: str = "Pipeline workflow") -> str:
    """Short label when the LLM didn't name the workflow itself."""
    s = (intent or "").strip()
    if not s:
        return fallback
    # First 60 chars, trimmed at the last word boundary.
    if len(s) <= 60:
        return s
    cut = s[:60].rsplit(" ", 1)[0]
    return cut + "…"


async def propose_pipeline_workflow(args: dict) -> dict:
    """Build a compositional workflow draft from a full NL intent.

    Args:
      intent          — REQUIRED. The user's full intent verbatim (multi-
                        branch / multi-tier / mixed-action prompts).
      primary_symbol  — Optional default symbol to fill into step configs
                        that don't name one.
      name            — Optional short human label.

    Returns the same ``workflow_draft_card`` payload shape as
    ``propose_dsl_workflow`` (so the FE renders the existing card with
    no changes).
    """
    args = args or {}
    intent = (args.get("intent") or "").strip()
    primary = (args.get("primary_symbol") or "").strip().upper() or None
    name_hint = (args.get("name") or "").strip()
    if not intent:
        raise ValueError(
            "propose_pipeline_workflow needs 'intent' — the user's full "
            "multi-branch / multi-tier intent in natural language."
        )

    # ── Translate (attempt 1) ─────────────────────────────────────
    try:
        draft, tx_meta = await translate_intent_to_pipeline(
            intent, primary_symbol=primary, cache_key="dsl.pipeline.v1",
        )
    except TranslationError as exc:
        raise ValueError(
            f"could not translate intent into a pipeline workflow: {exc}"
        ) from None

    steps = draft.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise ValueError(
            "translator returned no steps — try a more specific intent"
        )

    # ── Validate embedded trees + full registry ───────────────────
    from backend.workflows.propose import (
        ProposalValidationError, validate_draft_against_registry,
    )

    async def _retry_with_error(err_msg: str) -> tuple[dict, dict[str, Any]]:
        logger.info("[pipeline.retry] err=%s", err_msg[:160])
        return await translate_intent_to_pipeline(
            intent,
            primary_symbol=primary,
            extra_instruction=(
                f"Previous attempt failed validation: {err_msg}. "
                "Re-emit a corrected JSON object."
            ),
            cache_key="dsl.pipeline.retry.v1",
        )

    retry_tx_meta: dict[str, Any] | None = None
    for attempt in range(2):
        try:
            _validate_embedded_trees(steps)
            shaped = {
                "name": name_hint or draft.get("name") or _derive_name(intent),
                "description": draft.get("description") or intent[:160],
                "steps": steps,
                "rationale": draft.get("rationale") or "",
            }
            validated = validate_draft_against_registry(shaped)
            break
        except (ValueError, ProposalValidationError) as exc:
            if attempt == 1:
                raise ValueError(
                    f"pipeline draft invalid after retry: {exc}"
                ) from None
            draft, retry_tx_meta = await _retry_with_error(str(exc))
            steps = draft.get("steps") or []
            if not isinstance(steps, list) or not steps:
                raise ValueError(
                    "retry returned no steps — could not build pipeline"
                ) from None
    else:
        # Loop fell through without breaking — already raised above.
        raise ValueError("pipeline draft did not converge")  # pragma: no cover

    # ── Build response payload (same shape as propose_dsl_workflow) ──
    steps_out = [
        {
            "step_type": s.step_type,
            "label": getattr(s, "label", None),
            "config": s.config,
        }
        for s in validated.steps
    ]

    return {
        "_render_hint": "workflow_draft_card",
        "draft_id": str(uuid.uuid4()),
        "name": validated.name,
        "description": validated.description,
        "steps": steps_out,
        "rationale": validated.rationale,
        "translation_meta": tx_meta,
        "retry_translation_meta": retry_tx_meta,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
