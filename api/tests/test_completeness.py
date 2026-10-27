"""Tests for the schema-driven completeness checker.

The promise: walking a JSON Schema returns the same answer 1000 times
out of 1000. This is the load-bearing infrastructure that lets us run
the model at minimal reasoning effort. If completeness ever becomes
flaky the agentic loop loses its determinism guarantee.
"""
from __future__ import annotations

from backend.services.completeness import (
    CompletenessReport,
    MissingField,
    _human_type,
    _is_sentinel,
    check_completeness,
)


# ── Sentinel detection ────────────────────────────────────────────


class TestSentinelDetection:
    def test_none_is_sentinel(self):
        assert _is_sentinel(None) is True

    def test_empty_string_is_sentinel(self):
        assert _is_sentinel("") is True
        assert _is_sentinel("   ") is True

    def test_placeholder_strings_are_sentinels(self):
        for v in ["unknown", "TBD", "?", "N/A", "<missing>", "your_value"]:
            assert _is_sentinel(v) is True, v

    def test_real_values_are_not_sentinels(self):
        for v in ["RELIANCE", "BUY", 10, 1.5, True, False, [1, 2], {"a": 1}]:
            assert _is_sentinel(v) is False, v

    def test_empty_list_is_sentinel(self):
        assert _is_sentinel([]) is True

    def test_zero_and_false_are_not_sentinels(self):
        # Important: 0 and False are valid values for integer/boolean
        # fields. Treating them as missing would block legitimate
        # tool calls (e.g. quantity=0 may be invalid but it's a
        # *typed* problem, not a *missing* problem — let
        # validation_handler handle it).
        assert _is_sentinel(0) is False
        assert _is_sentinel(False) is False


# ── Type-hint rendering ────────────────────────────────────────────


class TestHumanType:
    def test_string(self):
        assert _human_type({"type": "string"}) == "text"

    def test_string_with_minlength(self):
        assert _human_type({"type": "string", "minLength": 5}) == "text (≥ 5 characters)"

    def test_string_iso_date(self):
        assert "YYYY-MM-DD" in _human_type({"type": "string", "format": "date"})

    def test_integer_with_minimum(self):
        assert _human_type({"type": "integer", "minimum": 1}) == "integer ≥ 1"

    def test_integer_range(self):
        assert _human_type({"type": "integer", "minimum": 1, "maximum": 100}) == \
            "integer between 1 and 100"

    def test_enum_overrides_type(self):
        assert _human_type({
            "type": "string", "enum": ["BUY", "SELL"],
        }) == "one of: BUY, SELL"

    def test_array_of_strings(self):
        assert _human_type({"type": "array", "items": {"type": "string"}}) == "list of text"

    def test_boolean(self):
        assert _human_type({"type": "boolean"}) == "yes/no"


# ── Completeness report ────────────────────────────────────────────


_PLACE_ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "symbol": {
            "type": "string",
            "description": "NSE ticker, e.g. RELIANCE",
        },
        "quantity": {
            "type": "integer",
            "minimum": 1,
            "description": "Number of shares to trade",
        },
        "transaction_type": {
            "type": "string",
            "enum": ["BUY", "SELL"],
            "description": "Side of the order",
        },
        "exchange": {
            "type": "string",
            "enum": ["NSE", "BSE"],
            "default": "NSE",
        },
    },
    "required": ["symbol", "quantity", "transaction_type"],
}


class TestCheckCompleteness:
    def test_all_fields_present_returns_complete(self):
        rpt = check_completeness(
            "place_market_order", _PLACE_ORDER_SCHEMA,
            {"symbol": "RELIANCE", "quantity": 10, "transaction_type": "BUY"},
        )
        assert rpt.is_complete
        assert rpt.missing == []

    def test_missing_required_field_flagged(self):
        rpt = check_completeness(
            "place_market_order", _PLACE_ORDER_SCHEMA,
            {"transaction_type": "BUY"},
        )
        assert not rpt.is_complete
        names = rpt.field_names()
        assert "symbol" in names
        assert "quantity" in names
        assert "transaction_type" not in names

    def test_optional_field_not_flagged_when_missing(self):
        rpt = check_completeness(
            "place_market_order", _PLACE_ORDER_SCHEMA,
            {"symbol": "INFY", "quantity": 5, "transaction_type": "SELL"},
        )
        assert rpt.is_complete  # exchange is optional with default

    def test_sentinel_value_treated_as_missing(self):
        rpt = check_completeness(
            "place_market_order", _PLACE_ORDER_SCHEMA,
            {"symbol": "unknown", "quantity": 10, "transaction_type": "BUY"},
        )
        assert not rpt.is_complete
        assert rpt.missing[0].field_name == "symbol"

    def test_missing_field_carries_description_and_type_hint(self):
        rpt = check_completeness(
            "place_market_order", _PLACE_ORDER_SCHEMA,
            {"transaction_type": "BUY"},
        )
        sym = next(m for m in rpt.missing if m.field_name == "symbol")
        assert "NSE ticker" in sym.description
        assert sym.type_hint == "text"

        qty = next(m for m in rpt.missing if m.field_name == "quantity")
        assert qty.type_hint == "integer ≥ 1"

    def test_missing_enum_field_carries_options(self):
        rpt = check_completeness(
            "place_market_order", _PLACE_ORDER_SCHEMA,
            {"symbol": "INFY", "quantity": 5},
        )
        txn = next(m for m in rpt.missing if m.field_name == "transaction_type")
        assert txn.enum == ["BUY", "SELL"]
        assert "BUY" in txn.type_hint and "SELL" in txn.type_hint

    def test_unknown_schema_returns_complete(self):
        # Tool with no schema (or a non-object schema) should not
        # block — return complete and let validation_handler handle it.
        rpt = check_completeness("weird_tool", {}, {})
        assert rpt.is_complete

    def test_no_required_array_means_everything_optional(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
        }
        rpt = check_completeness("opt_tool", schema, {})
        assert rpt.is_complete

    def test_zero_quantity_is_present_not_missing(self):
        # quantity=0 violates minimum=1 but is NOT missing — that's
        # validation_handler's job, not completeness's.
        rpt = check_completeness(
            "place_market_order", _PLACE_ORDER_SCHEMA,
            {"symbol": "INFY", "quantity": 0, "transaction_type": "BUY"},
        )
        assert rpt.is_complete
