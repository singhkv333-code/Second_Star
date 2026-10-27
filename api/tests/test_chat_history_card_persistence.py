"""Regression tests for `_bounded_card` — the conversation-history card
persistence gate in `backend/routers/chat.py`.

Reported 2026-07-14: a large basket-backtest card (multi-year equity
curve + benchmark curve) exceeded `_PERSIST_CARD_MAX_CHARS` (60k), so the
old all-or-nothing gate dropped the WHOLE card and persisted only
`{"_render_hint": ...}`. On history resume, the FE's card components
destructure required chart-series arrays with no defaults and crashed on
`undefined`, surfaced by `CardErrorBoundary` as "This card couldn't be
shown". Fix: down-sample large chart-series arrays first so the card
(metrics intact, chart slightly coarser) still fits under budget instead
of vanishing outright.
"""
from __future__ import annotations

from backend.routers.chat import (
    _bounded_card,
    _downsample_large_arrays,
    _downsample_series,
    _PERSIST_CARD_MAX_CHARS,
)


def _big_curve(n: int) -> list[dict]:
    return [{"t": f"2020-01-{i:02d}", "v": float(i)} for i in range(n)]


def test_downsample_series_caps_length_keeps_last_point():
    curve = _big_curve(1000)
    out = _downsample_series(curve, cap=200)
    assert len(out) == 200
    assert out[-1] == curve[-1]


def test_downsample_series_noop_under_cap():
    curve = _big_curve(50)
    assert _downsample_series(curve, cap=200) == curve


def test_downsample_large_arrays_recurses_into_nested_dicts():
    payload = {
        "_render_hint": "financial_backtest_chart",
        "equity_curve": _big_curve(1000),
        "nested": {"benchmark_curve": _big_curve(1000)},
        "metrics": {"cagr_pct": 12.3},
    }
    out = _downsample_large_arrays(payload)
    assert len(out["equity_curve"]) == 200
    assert len(out["nested"]["benchmark_curve"]) == 200
    assert out["metrics"] == {"cagr_pct": 12.3}  # untouched


def test_bounded_card_no_longer_drops_oversized_basket_backtest():
    """The exact regression: a card that would have exceeded the size
    budget with its full-resolution curves must now be persisted
    (down-sampled), not dropped to a bare hint."""
    huge_card = {
        "_render_hint": "financial_backtest_chart",
        "expression": "pe_ratio < 15",
        "metrics": {"cagr_pct": 8.1, "total_return_pct": 24.7},
        "equity_curve": _big_curve(5000),
        "benchmark_curve": _big_curve(5000),
        "rebalances": [
            {"date": f"2020-{m:02d}-01", "entered": [{"symbol": "AAA", "weight": 0.1}],
             "exited": []}
            for m in range(1, 13)
        ],
        "n_trades": 12,
        "warnings": [],
    }
    # Sanity: the full-resolution card genuinely exceeds the budget, so
    # this test actually exercises the down-sample path.
    import json
    assert len(json.dumps(huge_card, default=str)) > _PERSIST_CARD_MAX_CHARS

    bounded = _bounded_card(huge_card)
    assert bounded is not None, "card must survive, down-sampled — not be dropped"
    assert "equity_curve" in bounded and "benchmark_curve" in bounded
    assert len(bounded["equity_curve"]) <= 200
    assert len(bounded["benchmark_curve"]) <= 200
    # The metrics that matter are untouched.
    assert bounded["metrics"]["total_return_pct"] == 24.7


def test_bounded_card_returns_none_for_non_render_hinted_payload():
    assert _bounded_card({"foo": "bar"}) is None
    assert _bounded_card(None) is None


def test_bounded_card_passes_small_card_through_unchanged():
    small = {"_render_hint": "logic_card", "type": "price", "value": 100}
    assert _bounded_card(small) == small
