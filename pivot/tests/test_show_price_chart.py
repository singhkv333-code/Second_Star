"""show_price_chart: the model names one or two companies, the card draws them.

The tool carries no prices (the card loads its own bars), so nothing it
returns can be quoted as a number; an unknown symbol goes back to the model.
"""
import asyncio

from backend.agents import tool_executor as te


def _run(monkeypatch, args, have=("TCS", "INFY")):
    monkeypatch.setattr(te, "_charto_has_bars",
                        lambda s: {} if s in have else {"error": f"unknown symbol {s}"})
    monkeypatch.setattr(
        "backend.services.fundamentals_screen.with_verified_names",
        lambda d: {"results": [{**r, "name": r["symbol"].title()} for r in d["results"]]})
    return asyncio.run(te._show_price_chart(args, None, None, None))


def test_card_names_the_symbols_and_carries_no_prices(monkeypatch):
    r = _run(monkeypatch, {"symbols": ["tcs", "INFY", "TCS"], "range": "6M"})
    d = r["data"]
    assert r["success"] and d["_render_hint"] == "price_chart_card"
    assert [s["symbol"] for s in d["symbols"]] == ["TCS", "INFY"]
    assert d["range"] == "6M"
    assert not any(k in d for k in ("bars", "close", "price", "last"))


def test_unknown_symbol_goes_back_to_the_model(monkeypatch):
    r = _run(monkeypatch, {"symbols": ["TATAMOTOR"]})
    assert not r["success"] and "TATAMOTOR" in r["error"]
    assert "_render_hint" not in r["data"]


def test_a_chart_never_displaces_another_card():
    """The router hoists one card; the chart is appended by the FE instead."""
    import inspect
    from backend.routers import chat
    src = inspect.getsource(chat)
    assert '"price_chart_card"' in src
    raw = {"show_price_chart": {"_render_hint": "price_chart_card"},
           "propose_workflow": {"_render_hint": "workflow_draft_card"}}
    order = sorted(raw.items(), key=lambda kv: isinstance(kv[1], dict)
                   and kv[1].get("_render_hint") == "price_chart_card")
    assert order[0][0] == "propose_workflow"


def test_a_busy_charto_is_not_an_unknown_symbol(monkeypatch):
    """Mid-turn, other tools load charto too; a slow answer must not turn a
    real company into 'no price history'."""
    import urllib.request
    def slow(*a, **k):
        raise TimeoutError("timed out")
    monkeypatch.setattr(urllib.request, "urlopen", slow)
    assert te._charto_has_bars("BAJAJ-AUTO") == {}
