"""Regression coverage for the 2026-07-14 live report (img #13,
"same persisting problem"): "buy GOLDBEES every 5 minutes if its price
is lower than it was the previous 5 minute check" built a draft whose
`price of GOLDBEES < price of GOLDBEES (1 bar ago)` entry had NO way to
mean "5 minutes ago" — `PriceNode` had no `timeframe` field (unlike
`IndicatorNode`), so `offset=1` always resolved against the accessor's
default DAILY bars: "yesterday's close", not "5 minutes ago". The chat
tool also never required an interval for a pure price-lookback
condition (only for indicator conditions), so the LLM never got asked.

Fixed: `PriceNode.timeframe` (schema.py), evaluator + both accessors
honour it (data_accessor.py / backtest/data_accessor.py), and
`_dsl_chat_tools._tree_has_indicator` / `_apply_interval_to_indicators`
now treat a price node with offset > 0 as timeframe-sensitive too.
"""
from __future__ import annotations

from typing import Optional

from pydantic import TypeAdapter

from backend.services._dsl_chat_tools import (
    _apply_interval_to_indicators,
    _tree_has_indicator,
)
from backend.workflows.dsl.evaluator import evaluate
from backend.workflows.dsl.schema import PriceNode, Tree


def _price_lookback_tree(timeframe: Optional[str] = None) -> dict:
    right: dict = {
        "type": "price", "symbol": "GOLDBEES", "offset": 1,
    }
    if timeframe is not None:
        right["timeframe"] = timeframe
    return {
        "type": "comparison", "op": "<",
        "left": {"type": "price", "symbol": "GOLDBEES", "offset": 0},
        "right": right,
    }


def test_price_node_defaults_timeframe_to_daily():
    node = PriceNode(symbol="GOLDBEES", offset=1)
    assert node.timeframe == "1d"


def test_price_node_accepts_intraday_timeframe():
    node = PriceNode(symbol="GOLDBEES", offset=1, timeframe="5m")
    assert node.timeframe == "5m"


def test_tree_has_indicator_true_for_price_offset_lookback():
    """A pure price-vs-price(offset=1) tree is just as timeframe-
    sensitive as an indicator — must trip the "ask for interval" gate."""
    assert _tree_has_indicator(_price_lookback_tree()) is True


def test_tree_has_indicator_false_for_current_bar_only():
    tree = {
        "type": "comparison", "op": "<",
        "left": {"type": "price", "symbol": "GOLDBEES", "offset": 0},
        "right": {"type": "constant", "value": 100.0},
    }
    assert _tree_has_indicator(tree) is False


def test_apply_interval_stamps_price_offset_node():
    tree = _price_lookback_tree()
    _apply_interval_to_indicators(tree, "5m")
    assert tree["right"]["timeframe"] == "5m"
    # offset=0 (current bar) leaf is untouched — timeframe is
    # meaningless for it.
    assert "timeframe" not in tree["left"]


def test_apply_interval_does_not_override_explicit_timeframe():
    tree = _price_lookback_tree(timeframe="15m")
    _apply_interval_to_indicators(tree, "5m")
    assert tree["right"]["timeframe"] == "15m"


class _TimeframeAwareAccessor:
    """Mirrors LiveDataAccessor's signature — records the timeframe
    each get_price call was made with."""

    def __init__(self, values: dict[tuple, float]):
        self._values = values
        self.calls: list[tuple] = []

    def get_price(self, *, symbol, exchange="NSE", basis="close",
                  offset=0, timeframe="daily"):
        self.calls.append((symbol, basis, offset, timeframe))
        return self._values.get((symbol, offset, timeframe))

    def get_indicator(self, **kwargs):
        return None

    def get_volume(self, **kwargs):
        return None

    def get_position_field(self, **kwargs):
        return None

    def get_session_day(self):
        return None


def test_evaluator_passes_5m_timeframe_through_to_accessor():
    tree = TypeAdapter(Tree).validate_python(_price_lookback_tree("5m"))
    accessor = _TimeframeAwareAccessor({
        ("GOLDBEES", 0, "1d"): 100.0,   # current price, no lag → "1d" default
        ("GOLDBEES", 1, "5m"): 101.0,   # 1 bar ago on 5m bars
    })
    result = evaluate(tree, accessor=accessor)
    # 100 < 101 → True, and crucially the lookback leg was fetched at 5m,
    # not silently against a daily bar.
    assert ("GOLDBEES", "close", 1, "5m") in accessor.calls
    assert result.value.name == "TRUE"


def test_evaluator_falls_back_for_legacy_accessor_without_timeframe_kwarg():
    """An accessor that doesn't accept `timeframe` (old stub / any code
    not yet updated) must still work — daily-default behaviour
    unchanged, no crash."""

    class _LegacyAccessor:
        def get_price(self, *, symbol, exchange="NSE", basis="close", offset=0):
            return 42.0 if offset == 1 else 40.0

        def get_indicator(self, **kwargs):
            return None

        def get_volume(self, **kwargs):
            return None

        def get_position_field(self, **kwargs):
            return None

        def get_session_day(self):
            return None

    tree = TypeAdapter(Tree).validate_python(_price_lookback_tree("5m"))
    result = evaluate(tree, accessor=_LegacyAccessor())
    assert result.value.name == "TRUE"  # 40 < 42
