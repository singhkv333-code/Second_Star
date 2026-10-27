"""Unit tests for backend.workflows.refs.resolve_refs.

Covers ARCHITECTURE.md §6 invariants:
  - allowed namespaces: context.<index>.<path>, context.webhook_payload.<path>,
    now, workflow.<field>
  - any other namespace raises RefNotFoundError
  - ref-as-whole-string preserves typed value
  - ref-as-substring concatenates as a string
  - missing path -> RefNotFoundError with the original ref text
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.workflows.refs import RefNotFoundError, resolve_refs


WF_META = {"id": "wf-1", "name": "Test agent", "version": 3}
NOW = datetime(2026, 5, 7, 10, 15, 30, tzinfo=timezone.utc)


def test_whole_string_ref_returns_typed_value() -> None:
    """A whole-string ref must return the underlying typed value
    (int stays int) rather than its repr."""
    ctx = {"1": {"buying_power": 75000}}
    out = resolve_refs(
        "{{ context.1.buying_power }}",
        context=ctx,
        workflow_meta=WF_META,
        now=NOW,
    )
    assert out == 75000
    assert isinstance(out, int)


def test_embedded_ref_concatenates_as_string() -> None:
    ctx = {"1": {"qty": 10}}
    out = resolve_refs(
        "Buy {{ context.1.qty }} units",
        context=ctx,
        workflow_meta=WF_META,
        now=NOW,
    )
    assert out == "Buy 10 units"


def test_workflow_namespace() -> None:
    out = resolve_refs(
        "{{ workflow.name }} v{{ workflow.version }}",
        context={},
        workflow_meta=WF_META,
        now=NOW,
    )
    assert out == "Test agent v3"


def test_now_returns_iso8601_z() -> None:
    out = resolve_refs(
        "{{ now }}",
        context={},
        workflow_meta=WF_META,
        now=NOW,
    )
    assert isinstance(out, str)
    assert out == "2026-05-07T10:15:30Z"


def test_webhook_payload_namespace() -> None:
    """Per the Day-1 contract ruling: `webhook_payload` is a reserved
    literal key inside `context`, not a sibling namespace."""
    ctx = {"webhook_payload": {"symbol": "RELIANCE", "qty": 5}}
    out = resolve_refs(
        "{{ context.webhook_payload.symbol }}",
        context=ctx,
        workflow_meta=WF_META,
        now=NOW,
    )
    assert out == "RELIANCE"


def test_unknown_namespace_raises() -> None:
    with pytest.raises(RefNotFoundError) as exc:
        resolve_refs(
            "{{ secrets.kite }}",
            context={},
            workflow_meta=WF_META,
            now=NOW,
        )
    assert "secrets.kite" in str(exc.value)


def test_missing_context_step_raises_with_ref_text() -> None:
    with pytest.raises(RefNotFoundError) as exc:
        resolve_refs(
            "{{ context.5.foo }}",
            context={"1": {"foo": "bar"}},
            workflow_meta=WF_META,
            now=NOW,
        )
    assert "context.5.foo" in str(exc.value)


def test_missing_path_in_step_output() -> None:
    with pytest.raises(RefNotFoundError):
        resolve_refs(
            "{{ context.1.missing_field }}",
            context={"1": {"present": 1}},
            workflow_meta=WF_META,
            now=NOW,
        )


def test_dict_walk_resolves_nested() -> None:
    """Refs inside dict values must resolve recursively."""
    cfg = {
        "left": "{{ context.1.buying_power }}",
        "operator": ">",
        "right": 50000,
    }
    out = resolve_refs(
        cfg,
        context={"1": {"buying_power": 80000}},
        workflow_meta=WF_META,
        now=NOW,
    )
    assert out == {"left": 80000, "operator": ">", "right": 50000}


def test_list_walk_resolves_each_element() -> None:
    cfg = ["{{ context.1.x }}", 2, "lit"]
    out = resolve_refs(
        cfg,
        context={"1": {"x": "y"}},
        workflow_meta=WF_META,
        now=NOW,
    )
    assert out == ["y", 2, "lit"]


def test_non_ref_strings_passthrough() -> None:
    out = resolve_refs(
        "no refs here",
        context={},
        workflow_meta=WF_META,
        now=NOW,
    )
    assert out == "no refs here"


def test_empty_ref_body_raises() -> None:
    with pytest.raises(RefNotFoundError):
        resolve_refs(
            "{{ }}",
            context={},
            workflow_meta=WF_META,
            now=NOW,
        )


def test_workflow_field_missing_raises() -> None:
    with pytest.raises(RefNotFoundError):
        resolve_refs(
            "{{ workflow.unknown_field }}",
            context={},
            workflow_meta=WF_META,
            now=NOW,
        )


def test_context_step_index_must_be_int_or_webhook_payload() -> None:
    """Refs like `{{ context.foo.bar }}` are nonsense — only an integer
    step_index or the literal `webhook_payload` is valid for the first
    segment after `context.`."""
    with pytest.raises(RefNotFoundError) as exc:
        resolve_refs(
            "{{ context.notanindex.value }}",
            context={"notanindex": {"value": 1}},
            workflow_meta=WF_META,
            now=NOW,
        )
    assert "step_index" in str(exc.value) or "webhook_payload" in str(exc.value)
