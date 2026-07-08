"""Tests for ``backend.services.ipo_feed.parse_price_band``.

The structured price band drives:
  - the FE amount preview (min, max, is_fixed)
  - server-side amount-estimate math (uses max for cut-off)
  - the in-band bid_price validation (must be within [min, max])

So the parser has to handle every shape NSE produces and be honest about
garbage rather than fabricate a band. These tests cover the matrix.
"""
from __future__ import annotations

import pytest

from backend.services.ipo_feed import parse_price_band


class TestParsePriceBandHappyPaths:
    def test_basic_hyphen(self) -> None:
        assert parse_price_band("125-132") == {
            "min": 125.0, "max": 132.0, "is_fixed": False,
        }

    def test_spaces_around_hyphen(self) -> None:
        assert parse_price_band("125 - 132") == {
            "min": 125.0, "max": 132.0, "is_fixed": False,
        }

    def test_en_dash(self) -> None:
        assert parse_price_band("125 – 132") == {
            "min": 125.0, "max": 132.0, "is_fixed": False,
        }

    def test_em_dash(self) -> None:
        assert parse_price_band("125 — 132") == {
            "min": 125.0, "max": 132.0, "is_fixed": False,
        }

    def test_rupee_glyph(self) -> None:
        assert parse_price_band("₹125 – ₹132") == {
            "min": 125.0, "max": 132.0, "is_fixed": False,
        }

    def test_rs_prefix(self) -> None:
        assert parse_price_band("Rs. 125 to Rs. 132") == {
            "min": 125.0, "max": 132.0, "is_fixed": False,
        }

    def test_decimal_band(self) -> None:
        assert parse_price_band("125.50-132.25") == {
            "min": 125.5, "max": 132.25, "is_fixed": False,
        }

    def test_inverted_order_normalises(self) -> None:
        # If NSE ever returns "max-min" we still want low <= high.
        out = parse_price_band("132-125")
        assert out == {"min": 125.0, "max": 132.0, "is_fixed": False}


class TestParsePriceBandFixedPrice:
    def test_single_value_is_fixed(self) -> None:
        assert parse_price_band("120") == {
            "min": 120.0, "max": 120.0, "is_fixed": True,
        }

    def test_single_decimal_is_fixed(self) -> None:
        assert parse_price_band("120.50") == {
            "min": 120.5, "max": 120.5, "is_fixed": True,
        }

    def test_single_value_with_rupee(self) -> None:
        assert parse_price_band("₹120") == {
            "min": 120.0, "max": 120.0, "is_fixed": True,
        }

    def test_band_with_equal_endpoints_is_fixed(self) -> None:
        # Equal ends -> is_fixed:true, mirrors a single-value record.
        assert parse_price_band("120-120") == {
            "min": 120.0, "max": 120.0, "is_fixed": True,
        }


class TestParsePriceBandGarbageReturnsNone:
    def test_none(self) -> None:
        assert parse_price_band(None) is None

    def test_empty_string(self) -> None:
        assert parse_price_band("") is None

    def test_whitespace(self) -> None:
        assert parse_price_band("   ") is None

    def test_alpha_garbage(self) -> None:
        assert parse_price_band("TBD") is None

    def test_zero(self) -> None:
        # NSE sometimes returns "0" for not-yet-priced. Treat as no-band.
        assert parse_price_band("0") is None

    def test_zero_with_currency(self) -> None:
        assert parse_price_band("₹0") is None

    @pytest.mark.parametrize("raw", ["-", "--", "—", " – "])
    def test_pure_separator(self, raw: str) -> None:
        assert parse_price_band(raw) is None
