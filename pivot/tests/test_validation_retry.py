"""Unit tests for validate-and-retry tool execution.

Covers:
  - Pydantic-style error formatter (terse, structured, one line per error)
  - JSON-Schema arg validator (required / type / enum)
  - ASK_USER synthetic tool def shape
  - Deterministic clarification-question template

The previous LLM-driven `execute_tool_with_retry` retry loop was deleted
on 2026-05-04; the live chat path uses `execute_with_completeness` which
falls back to a deterministic template instead of an LLM round-trip.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from backend.services import validation_retry as vr
from backend.services.completeness import MissingField


# ── Error formatter ─────────────────────────────────────────────────


class _DummyOrder(BaseModel):
    symbol: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)


def test_format_validation_errors_includes_loc_and_msg():
    try:
        _DummyOrder.model_validate({"quantity": -1})
    except ValidationError as e:
        out = vr.format_validation_errors_terse(e)
    assert "symbol" in out
    assert "quantity" in out
    assert "Field required" in out
    assert "-1" in out


def test_format_validation_errors_handles_root_loc():
    class Strict(BaseModel):
        x: int
    try:
        Strict.model_validate("not a dict")
    except ValidationError as e:
        out = vr.format_validation_errors_terse(e)
    assert out  # produces SOMETHING, not empty


# ── Arg validator ───────────────────────────────────────────────────


def test_validate_args_passes_when_valid():
    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}, "n": {"type": "integer"}},
        "required": ["x"],
    }
    assert vr._validate_args_against_schema({"x": "hi", "n": 1}, schema) is None


def test_validate_args_flags_missing_required():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    err = vr._validate_args_against_schema({}, schema)
    assert err and "x" in err and "required" in err.lower()


def test_validate_args_flags_type_mismatch():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": []}
    err = vr._validate_args_against_schema({"n": "not-an-int"}, schema)
    assert err and "integer" in err and "str" in err


def test_validate_args_flags_enum_membership():
    schema = {"type": "object", "properties": {
        "side": {"type": "string", "enum": ["BUY", "SELL"]},
    }, "required": []}
    err = vr._validate_args_against_schema({"side": "HODL"}, schema)
    assert err and "BUY" in err and "SELL" in err


def test_validate_args_returns_none_for_unknown_schema():
    assert vr._validate_args_against_schema({"x": 1}, None) is None  # type: ignore


# ── ASK_USER tool def + intercept ───────────────────────────────────


def test_ask_user_tool_def_shape():
    t = vr.ask_user_tool_def()
    assert t.name == vr.ASK_USER_TOOL_NAME
    assert "question" in t.parameters["properties"]
    assert "question" in t.parameters["required"]


# ── Deterministic clarification template ────────────────────────────


def test_clarification_one_field_uses_friendly_alias():
    """Well-known field names (symbol, quantity, …) get a curated
    user-friendly alias instead of the raw schema description, which
    is often schema-explainer prose."""
    out = vr._format_clarification_question([
        MissingField(field_name="symbol", description="the stock ticker", type_hint="text"),
    ])
    # "stock or ETF" is the curated alias for `symbol`.
    assert "stock or ETF" in out or "stock ticker" in out
    # We never leak the literal field name into the question.
    assert " symbol?" not in out and " symbol " not in out


def test_clarification_two_fields_uses_pair_phrasing():
    out = vr._format_clarification_question([
        MissingField(field_name="symbol", description="the stock ticker", type_hint="text"),
        MissingField(field_name="quantity", description="how many shares", type_hint="integer ≥ 1"),
    ])
    assert "two things" in out
    # "symbol" → "stock or ETF" via alias; "quantity" → "number of shares".
    assert ("stock or ETF" in out) or ("stock ticker" in out)
    assert "shares" in out


def test_clarification_three_fields_bullets():
    out = vr._format_clarification_question([
        MissingField(field_name="a", description="A", type_hint="text"),
        MissingField(field_name="b", description="B", type_hint="text"),
        MissingField(field_name="c", description="C", type_hint="text"),
    ])
    assert "•" in out
    assert "A" in out and "B" in out and "C" in out


def test_clarification_no_fields_falls_back():
    assert vr._format_clarification_question([]) == "Could you give me a bit more detail?"


# Stop here — the LLM-driven retry loop and its tests were deleted with
