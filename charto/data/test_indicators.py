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


NEW_STUDIES = {
    "ichimoku", "pivots", "vwma", "rma", "percent_b", "bandwidth", "tema",
    "awesome", "chaikin_osc", "vortex", "ultimate", "trix", "kst", "dpo",
    "force", "eom", "choppiness", "fisher", "rvi", "connors_rsi", "kama",
    "alma", "lsma",
}


def _rising_rows(count: int = 180) -> list[tuple]:
    return [(1_700_000_000 + i * 300, 99 + i, 101 + i, 98 + i,
             100 + i, 1_000 + i) for i in range(count)]


def test_tier_one_to_three_registry_is_complete() -> None:
    assert NEW_STUDIES <= indicators.SPECS.keys()
    assert len(NEW_STUDIES) == 23
    for name in NEW_STUDIES:
        result = indicators.compute(name, _rising_rows())
        assert result["lines"]
        assert all(len(line) == 180 for line in result["lines"].values())


def test_awesome_oscillator_uses_five_and_thirty_four_bar_hl2_means() -> None:
    rows = _rising_rows(60)
    line = indicators.compute("awesome", rows)["lines"]["awesome"]
    hl2 = [(r[2] + r[3]) / 2 for r in rows]
    expected = sum(hl2[-5:]) / 5 - sum(hl2[-34:]) / 34
    assert line[-1] == pytest.approx(expected)


def test_ichimoku_defaults_and_midpoints_are_conventional() -> None:
    rows = _rising_rows(80)
    result = indicators.compute("ichimoku", rows)
    assert {f["key"]: f["default"] for f in indicators.inputs("ichimoku")} == {
        "period": 9, "base_length": 26, "span_b_length": 52,
        "displacement": 26,
    }
    assert result["last"]["conversion"] == pytest.approx((rows[-1][2] + rows[-9][3]) / 2)
    assert result["last"]["base"] == pytest.approx((rows[-1][2] + rows[-26][3]) / 2)


def test_pivots_use_previous_session_and_cpr_orders_its_edges() -> None:
    day = 86_400
    rows = []
    for d, base in enumerate((100, 110, 120)):
        for j in range(6):
            rows.append((1_700_000_000 + d * day + j * 300,
                         base, base + 4 + j, base - 2, base + j, 1_000))
    line = indicators.compute("pivots", rows, timeframe="day")["lines"]
    p = ((110 + 9) + (110 - 2) + (110 + 5)) / 3
    assert line["pivot"][12] == pytest.approx(p)
    assert line["cpr_bottom"][12] <= line["cpr_top"][12]


def test_connors_rsi_remains_bounded() -> None:
    values = indicators.compute("connors_rsi", _rising_rows(180))["lines"]["connors_rsi"]
    assert all(0 <= x <= 100 for x in values if x is not None)
