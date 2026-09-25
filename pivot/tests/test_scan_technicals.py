"""scan_technicals: charto's market-wide scan, shaped as the screen card.

The values are charto's; this layer only chooses columns and units, and
passes vocabulary misses back to the model instead of rendering an empty card.
"""
import asyncio

from backend.agents import tool_executor as te

SCAN = {
    "universe": 500, "matched": 31, "as_of": "22 Jul 2026",
    "criteria": "rsi14 > 60", "ranking": "by rsi14, high first",
    "_note": "present the rows as a markdown table",
    "filters_applied": [{"feature": "rsi14", "op": "gt", "value": 60}],
    "sorted_by": {"feature": "ret_1m", "desc": True},
    "symbols": [f"S{i}" for i in range(31)],
    "rows": [{"symbol": "S0", "name": "Zero", "industry": "banks",
              "close": 100.0, "rsi14": 71.0, "ret_1m": 4.2,
              "pattern": {"kind": "bull_flag", "bars_ago": 2}}],
}


def _run(monkeypatch, out, args=None):
    monkeypatch.setattr(te, "_run_charto_scan", lambda spec: out)
    monkeypatch.setattr(
        "backend.services.fundamentals_screen.with_verified_names", lambda d: d)
    return asyncio.run(te._scan_technicals(args or {}, None, None, None))


def test_scan_becomes_a_screen_card_with_units(monkeypatch):
    r = _run(monkeypatch, SCAN, {"title": "Strong RSI"})
    d = r["data"]
    assert d["_render_hint"] == "screen_results_card"
    assert list(d)[0] == "_render_hint"
    assert [(c["key"], c["unit"]) for c in d["columns"]] == [
        ("close", "inr"), ("rsi14", "num"), ("ret_1m", "pct_signed"),
        ("pattern", "text")]
    assert d["results"][0]["pattern"] == "bull flag, 2d ago"
    assert d["total_matched"] == 31 and len(d["symbols"]) == 31
    assert d["applied_filters"] == [{"field": "rsi14", "op": ">", "value": 60}]
    assert d["title"] == "Strong RSI" and d["as_of"] == "22 Jul 2026"
    # The chart surface's formatting instruction does not leak across.
    assert "_note" not in d


def test_a_vocabulary_miss_goes_back_to_the_model_without_a_card(monkeypatch):
    miss = {"error": "no industry named 'private banks'",
            "closest": ["banksprivatesector"]}
    r = _run(monkeypatch, miss, {"industry": "private banks"})
    assert r["success"] and "_render_hint" not in r["data"]
    assert r["data"]["closest"] == ["banksprivatesector"]


def test_charto_down_is_an_honest_error(monkeypatch):
    def boom(spec):
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(te, "_run_charto_scan", boom)
    r = asyncio.run(te._scan_technicals({}, None, None, None))
    assert not r["success"] and "technical scan unavailable" in r["error"]
