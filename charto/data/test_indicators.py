"""Focused regression tests for the indicator registry and edge cases."""

from __future__ import annotations

import pytest

import indicators


def _flat_rows(count: int = 20) -> list[tuple]:
    return [(i, 8.0, 10.0, 5.0, 8.0, 1_000 + i) for i in range(count)]


def test_aroon_uses_most_recent_occurrence_of_tied_extreme() -> None:
    result = indicators.compute("aroon", _flat_rows(), period=5)

    assert result["last"] == {
        "aroon_up": 100.0,
        "aroon_down": 100.0,
        "oscillator": 0.0,
    }


def test_williams_r_does_not_offer_volume_as_a_source() -> None:
    source = next(field for field in indicators.inputs("williams_r")
                  if field["key"] == "source")

    assert "volume" not in source["options"]


def test_williams_r_rejects_volume_when_called_directly() -> None:
    with pytest.raises(ValueError, match="volume is not a valid source"):
        indicators.compute("williams_r", _flat_rows(), period=5,
                           source="volume")
