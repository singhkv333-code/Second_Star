"""Pure unit tests for ``backend.workflows.compat``.

No DB, no network, no LLM. The compat engine is a pure function — these
tests feed it dict-shaped step lists and assert the exact diagnostic
severities + codes the FE editor will see.

Capability rules + NEEDS_* requirement labels come from the interactive
HTML spec at ``docs/plans/WORKFLOW_EDITOR_PLAN.html`` (the ``STEPS``,
``CAPS`` and ``NEEDS_*`` data objects). The output_schemas referenced
for ref-typing tests come from the in-memory registry — we never touch
the DB.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.workflows.compat import (
    AmbientState,
    Diagnostic,
    catalog_compat,
    lint_workflow,
)
from backend.workflows.registry import STEP_REGISTRY, get_catalog


# ---------------------------------------------------------------------------
# Small helpers — keep the tests legible.
# ---------------------------------------------------------------------------


def _step(step_type: str, **config: Any) -> dict[str, Any]:
    """Build a minimal step dict the linter accepts."""
    return {"step_type": step_type, "config": dict(config)}


def _errors(diags: list[Diagnostic]) -> list[Diagnostic]:
    return [d for d in diags if d.severity == "error"]


def _warnings(diags: list[Diagnostic]) -> list[Diagnostic]:
    return [d for d in diags if d.severity == "warning"]


def _codes(diags: list[Diagnostic]) -> list[str]:
    return [d.code for d in diags]


# ---------------------------------------------------------------------------
# Structural pass — trigger placement
# ---------------------------------------------------------------------------


def test_step0_not_a_trigger_raises_trigger_placement_error() -> None:
    """If step 0 isn't a trigger.* type, we emit `trigger_placement`
    severity=error at index 0 — the engine refuses to run such a
    workflow and the editor surfaces this so the user can fix it."""
    steps = [
        _step("fetch.quote", symbol="RELIANCE"),
        _step("action.place_order", symbol="RELIANCE", quantity=1, side="buy"),
    ]
    diags = lint_workflow(steps)
    placements = [
        d
        for d in diags
        if d.code == "trigger_placement" and d.step_index == 0
    ]
    assert len(placements) == 1, f"expected one trigger_placement at 0; got {diags!r}"
    assert placements[0].severity == "error"


# ---------------------------------------------------------------------------
# Capability pass — clean path
# ---------------------------------------------------------------------------


def test_happy_path_stoploss_after_place_order_has_no_errors() -> None:
    """trigger → fetch.portfolio → condition.position → action.place_order
    → action.set_stoploss must emit no error diagnostics. set_stoploss
    requires `position_open`, which place_order produces — the
    requirement is satisfied in-flow, no ambient needed."""
    steps = [
        _step("trigger.manual"),
        _step("fetch.portfolio"),
        _step(
            "condition.position",
            symbol="RELIANCE",
            quantity_relative="any",
        ),
        _step(
            "action.place_order",
            symbol="RELIANCE",
            side="buy",
            quantity=1,
        ),
        _step(
            "action.set_stoploss",
            symbol="RELIANCE",
            trigger_price=1000,
        ),
    ]
    diags = lint_workflow(steps)
    assert _errors(diags) == [], f"expected zero errors; got {diags!r}"
    # And specifically no needs_position warning on the stop-loss step.
    needs_pos_on_sl = [
        d
        for d in diags
        if d.step_index == 4 and d.code == "needs_position"
    ]
    assert not needs_pos_on_sl, f"unexpected needs_position on set_stoploss: {needs_pos_on_sl!r}"


# ---------------------------------------------------------------------------
# Capability pass — ambient fallback for `needs_position`
# ---------------------------------------------------------------------------


def test_squareoff_all_without_prior_open_warns_needs_position() -> None:
    """trigger → action.squareoff_all with no prior position-producing
    step and the default empty ambient → severity=warning,
    code=needs_position (NOT an error — capability gaps are
    user-correctable signals, not hard rejections)."""
    steps = [
        _step("trigger.manual"),
        _step("action.squareoff_all"),
    ]
    diags = lint_workflow(steps)
    needs_pos = [d for d in diags if d.code == "needs_position"]
    assert len(needs_pos) == 1, f"expected one needs_position warning; got {diags!r}"
    assert needs_pos[0].severity == "warning"
    assert needs_pos[0].step_index == 1
    # And it must NOT be promoted to an error.
    assert not any(
        d.code == "needs_position" and d.severity == "error" for d in diags
    )


def test_squareoff_all_with_ambient_position_is_clean() -> None:
    """Same workflow + AmbientState(held_symbols=["RELIANCE"]) → no
    needs_position diagnostic. The ambient slot satisfies the
    requirement before the in-flow `position_open` tag would."""
    steps = [
        _step("trigger.manual"),
        _step("action.squareoff_all"),
    ]
    ambient = AmbientState(held_symbols=["RELIANCE"])
    diags = lint_workflow(steps, ambient=ambient)
    assert not any(d.code == "needs_position" for d in diags), (
        f"unexpected needs_position with ambient held: {diags!r}"
    )


# ---------------------------------------------------------------------------
# Capability pass — `needs_pending_orders`
# ---------------------------------------------------------------------------


def test_cancel_orders_without_prior_pending_warns_needs_pending_orders() -> None:
    """trigger → action.cancel_orders with no in-flow pending order and
    default empty ambient → severity=warning, code=needs_pending_orders."""
    steps = [
        _step("trigger.manual"),
        _step("action.cancel_orders"),
    ]
    diags = lint_workflow(steps)
    needs_ord = [d for d in diags if d.code == "needs_pending_orders"]
    assert len(needs_ord) == 1, f"expected one needs_pending_orders; got {diags!r}"
    assert needs_ord[0].severity == "warning"
    assert needs_ord[0].step_index == 1


def test_cancel_orders_with_ambient_pending_is_clean() -> None:
    """AmbientState(has_pending_orders=True) → no needs_pending_orders
    diagnostic."""
    steps = [
        _step("trigger.manual"),
        _step("action.cancel_orders"),
    ]
    ambient = AmbientState(has_pending_orders=True)
    diags = lint_workflow(steps, ambient=ambient)
    assert not any(d.code == "needs_pending_orders" for d in diags), (
        f"unexpected needs_pending_orders with ambient: {diags!r}"
    )


# ---------------------------------------------------------------------------
# Ref pass — forward + bad-path
# ---------------------------------------------------------------------------


def test_ref_pointing_beyond_list_emits_ref_forward_error() -> None:
    """{{ context.5.value }} on a workflow with fewer than 6 steps →
    severity=error, code=ref_forward. The linter classifies forward
    indices as ref_forward (vs ref_bad_path) so the FE can offer
    "swap the steps" vs "fix the path"."""
    steps = [
        _step("trigger.manual"),
        _step("fetch.portfolio"),
        _step(
            "condition.numeric",
            left="{{ context.5.value }}",
            op="lt",
            right=100,
        ),
    ]
    diags = lint_workflow(steps)
    fwd = [d for d in diags if d.code == "ref_forward"]
    assert fwd, f"expected ref_forward; got {diags!r}"
    assert any(d.severity == "error" and d.step_index == 2 for d in fwd)


def test_ref_to_missing_field_emits_ref_bad_path_with_real_suggestion() -> None:
    """condition.numeric.left = {{ context.1.nonexistent_field }} where
    step 1 is fetch.portfolio (output_schema = holdings/buying_power/
    total_value) → severity=error, code=ref_bad_path, suggested_fix
    lists the REAL available fields."""
    # Sanity guard: the registry must actually expose the fields we
    # rely on for suggested_fix substring matching, otherwise the test
    # would silently pass against an empty hint.
    schema = STEP_REGISTRY["fetch.portfolio"].output_schema or {}
    available = set((schema.get("properties") or {}).keys())
    assert {"holdings", "buying_power", "total_value"} <= available, (
        f"fetch.portfolio output_schema changed: {schema!r}"
    )

    steps = [
        _step("trigger.manual"),
        _step("fetch.portfolio"),
        _step(
            "condition.numeric",
            left="{{ context.1.nonexistent_field }}",
            op="lt",
            right=100,
        ),
    ]
    diags = lint_workflow(steps)
    bad = [d for d in diags if d.code == "ref_bad_path"]
    assert bad, f"expected ref_bad_path; got {diags!r}"
    err = next(d for d in bad if d.severity == "error" and d.step_index == 2)
    assert err.suggested_fix is not None
    # The suggested_fix should enumerate the REAL keys from the schema.
    for field in ("holdings", "buying_power", "total_value"):
        assert field in err.suggested_fix, (
            f"suggested_fix missing real field {field!r}: {err.suggested_fix!r}"
        )


# ---------------------------------------------------------------------------
# Branch-reset behaviour — a second trigger restarts capability state.
# ---------------------------------------------------------------------------


def test_second_trigger_resets_state_and_stoploss_warns_again() -> None:
    """Two branches:
        0  trigger.manual
        1  action.place_order        → produces position_open
        2  trigger.manual            → STARTS a new branch, resets state
        3  action.set_stoploss       → requires position_open → WARNING

    The warning at index 3 proves the branch reset happened — if state
    leaked across the trigger boundary, set_stoploss would be satisfied
    by the earlier place_order."""
    steps = [
        _step("trigger.manual"),
        _step(
            "action.place_order",
            symbol="RELIANCE",
            side="buy",
            quantity=1,
        ),
        _step("trigger.manual"),
        _step(
            "action.set_stoploss",
            symbol="RELIANCE",
            trigger_price=1000,
        ),
    ]
    diags = lint_workflow(steps)
    needs_pos_at_3 = [
        d
        for d in diags
        if d.step_index == 3 and d.code == "needs_position"
    ]
    assert len(needs_pos_at_3) == 1, (
        f"expected one needs_position at step 3 (branch reset); got {diags!r}"
    )
    assert needs_pos_at_3[0].severity == "warning"
    # And step 1 (in the first branch) should NOT have any needs_position
    # — place_order requires nothing.
    assert not any(
        d.step_index == 1 and d.code == "needs_position" for d in diags
    )


# ---------------------------------------------------------------------------
# get_catalog — group + compat block contract
# ---------------------------------------------------------------------------


def test_get_catalog_every_step_has_group_and_compat_block() -> None:
    """The frontend picker requires:
      - n == 44 visible step types (the 9 collapsed legacy ids are
        deprecated + excluded; their 4 parameterized replacements are
        visible),
      - non-empty `group` on every entry,
      - a `compat` block with `produces`, `requires`, `consumes` keys
        (any may be an empty list, but all three keys must exist),
      - a non-empty `catalog_version` string (the FE uses it as a
        cache key).
    """
    catalog = get_catalog()
    assert isinstance(catalog["catalog_version"], str)
    assert catalog["catalog_version"], "catalog_version must be non-empty"

    step_types = catalog["step_types"]
    assert len(step_types) == 44, (
        f"expected n==44 visible step types; got {len(step_types)}"
    )

    missing_group: list[str] = []
    bad_compat: list[str] = []
    for entry in step_types:
        if not isinstance(entry.get("group"), str) or not entry["group"]:
            missing_group.append(entry["step_type"])
        compat = entry.get("compat")
        if not isinstance(compat, dict):
            bad_compat.append(entry["step_type"])
            continue
        for key in ("produces", "requires", "consumes"):
            if key not in compat:
                bad_compat.append(f"{entry['step_type']}::{key}")
                break
            if not isinstance(compat[key], list):
                bad_compat.append(f"{entry['step_type']}::{key}")
                break
    assert not missing_group, f"step types missing group: {missing_group}"
    assert not bad_compat, f"step types with bad compat block: {bad_compat}"


def test_catalog_version_is_stable_and_present() -> None:
    """Two back-to-back catalog reads return the same version — there's
    nothing time-dependent in the build. Frontend invalidates its 5min
    cache only when this changes, so a fluctuating value would thrash
    the picker."""
    v1 = get_catalog()["catalog_version"]
    v2 = get_catalog()["catalog_version"]
    assert v1 == v2
    assert v1  # non-empty


# ---------------------------------------------------------------------------
# catalog_compat helper — shape contract (independent of get_catalog)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "step_type",
    [
        "trigger.manual",
        "action.place_order",
        "action.set_stoploss",
        "action.cancel_orders",
        "fetch.portfolio",
        "condition.numeric",
        "notify.message",
        "wait.delay",
    ],
)
def test_catalog_compat_shape(step_type: str) -> None:
    """Every entry in the FE catalog must have the same three-key
    compat shape, regardless of whether the rule is empty or not. The
    `code` field is intentionally NOT exposed — it's a lint artefact."""
    block = catalog_compat(step_type)
    assert set(block.keys()) == {"produces", "requires", "consumes"}
    assert isinstance(block["produces"], list)
    assert isinstance(block["consumes"], list)
    assert isinstance(block["requires"], list)
    for req in block["requires"]:
        assert set(req.keys()) == {"any_of", "ambient", "label", "warn"}
        assert "code" not in req


# ---------------------------------------------------------------------------
# Regression: the HTTP response model must not strip group/compat.
#
# get_catalog() (in-process) carried group+compat all along, but the
# GET /api/step-types route serializes through schemas.StepTypeCatalogResponse
# — if that Pydantic model omits the fields, FastAPI silently drops them and
# the FE picker buckets/groups stop working against the real backend. This
# guards the response-model layer the in-process catalog check can't see.
# ---------------------------------------------------------------------------


def test_response_model_preserves_group_and_compat() -> None:
    from backend.schemas import StepTypeCatalogResponse

    catalog = get_catalog()
    dumped = StepTypeCatalogResponse.model_validate(catalog).model_dump()
    by = {s["step_type"]: s for s in dumped["step_types"]}

    # Every step survives the round-trip with a non-empty group + a compat block.
    assert all(s.get("group") for s in dumped["step_types"]), "a step lost its group"
    assert all(s.get("compat") is not None for s in dumped["step_types"]), "a step lost compat"

    # action.set_protective is the collapsed replacement for set_stoploss /
    # set_takeprofit (the legacy ids are deprecated + excluded from the catalog).
    sl = by["action.set_protective"]
    assert sl["group"] == "Exits & protection"
    assert "protective_order" in sl["compat"]["produces"]
    assert any(r["any_of"] == ["position_open"] for r in sl["compat"]["requires"])


# ---------------------------------------------------------------------------
# Humanised diagnostic messages — must NOT leak raw step_type ids or raw
# dotted template paths. The editor surfaces these inline; "trigger.exit_
# compound" and "holdings.RELIANCE.quantity" are internal identifiers.
# ---------------------------------------------------------------------------


def test_trigger_placement_message_is_human_and_no_raw_id() -> None:
    """The structural pass must report a friendly reference instead of
    the bare ``step_type`` id. ``Step 1 ("…")`` or registry fallback —
    never ``fetch.quote``."""
    steps = [
        _step("fetch.quote", symbol="RELIANCE"),
        _step("action.place_order", symbol="RELIANCE", quantity=1, side="buy"),
    ]
    diags = lint_workflow(steps)
    placement = next(
        d for d in diags
        if d.code == "trigger_placement" and d.step_index == 0
    )
    # Never leak the raw step_type id.
    assert "fetch.quote" not in placement.message, placement.message
    assert placement.message.startswith("the first step must be a trigger"), (
        placement.message
    )
    # The friendly reference shape lives in the message.
    assert "Step 1" in placement.message, placement.message


def test_trigger_placement_uses_user_supplied_label() -> None:
    """When the caller passes ``step_names``, the message uses that label
    as the friendly hint — proving the router can humanise diagnostics
    from saved/edited step labels."""
    steps = [
        _step("fetch.quote", symbol="RELIANCE"),
        _step("action.place_order", symbol="RELIANCE", quantity=1, side="buy"),
    ]
    diags = lint_workflow(steps, step_names={0: "Grab the quote"})
    placement = next(
        d for d in diags
        if d.code == "trigger_placement" and d.step_index == 0
    )
    assert "Grab the quote" in placement.message, placement.message
    assert "fetch.quote" not in placement.message, placement.message


def test_ref_bad_path_message_drops_raw_step_id_and_dotted_path() -> None:
    """The ref-missing diagnostic must reference the producer step with
    the friendly helper (``Step N ("…")``) — never ``fetch.portfolio`` —
    and never embed the raw dotted ``holdings.RELIANCE.quantity`` template
    path. The leaf field name is OK to surface, but the full template is
    an internal identifier."""
    steps = [
        _step("trigger.manual"),
        _step("fetch.portfolio"),
        _step(
            "condition.numeric",
            left="{{ context.1.nonexistent_field }}",
            op="lt",
            right=100,
        ),
    ]
    diags = lint_workflow(steps)
    bad = next(
        d for d in diags
        if d.code == "ref_bad_path" and d.step_index == 2
        and d.severity == "error"
    )
    assert "fetch.portfolio" not in bad.message, bad.message
    # The leaf field name is fine to surface (and helpful);
    # the FULL dotted path-with-parens shape from the old impl is not.
    assert "Step 2" in bad.message, bad.message
    assert "nonexistent_field" in bad.message, bad.message


def test_needs_position_message_drops_raw_step_id() -> None:
    """The capability warning must NOT prefix the raw step id
    (``trigger.exit_compound``, ``action.squareoff_all``) — that leaks
    an internal identifier. It uses the friendly reference instead."""
    steps = [
        _step("trigger.manual"),
        _step("action.squareoff_all"),
    ]
    diags = lint_workflow(steps)
    warn = next(d for d in diags if d.code == "needs_position")
    assert "action.squareoff_all" not in warn.message, warn.message
    assert "Step 2" in warn.message, warn.message
    # The Requirement-supplied "needs a position …" hint is preserved.
    assert "position" in warn.message.lower(), warn.message


def test_loose_ref_info_message_is_humanised() -> None:
    """The info-level loose-path diagnostic stops surfacing the raw
    dotted template path; it references the producer step instead."""
    steps = [
        _step("trigger.manual"),
        _step("fetch.portfolio"),
        # holdings is a loose-shape object on fetch.portfolio output —
        # walking deeper hits the "loose" ambiguity branch.
        _step(
            "condition.numeric",
            left="{{ context.1.holdings.RELIANCE.quantity }}",
            op="gt",
            right=0,
        ),
    ]
    diags = lint_workflow(steps)
    # The loose branch is informational; assert it doesn't leak the raw
    # dotted path when fired. If the producer schema's ``holdings`` is
    # already a leaf (not an object) the walk reports "missing" instead —
    # in that case the bad-path test above already covers the leak.
    infos = [
        d for d in diags
        if d.code == "ref_bad_path" and d.severity == "info"
        and d.step_index == 2
    ]
    for info in infos:
        assert "fetch.portfolio" not in info.message, info.message
        assert "holdings.RELIANCE.quantity" not in info.message, info.message
