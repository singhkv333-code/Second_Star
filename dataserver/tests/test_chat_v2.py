"""Chat v2: the ten-tool surface, its guards, and the drawing styles."""
from __future__ import annotations

import json

import pytest

import dataserver as server
import chat_v2
import mark


@pytest.fixture(autouse=True)
def _restore_request():
    yield
    server._req.chat_v2 = None      # later tests run on the default engine


def _v2(on: bool) -> None:
    server._req.chat_mode = "chat"
    server._req.engine = None
    server._req.chat_v2 = on


def test_v2_offers_ten_tools_without_tool_search() -> None:
    _v2(True)
    tools = server._tools_for_request()
    assert [t["name"] for t in tools] == ["read_chart", "draw", "workspace", "market_data", "search",
                                          "query", "screen", "alerts", "journal", "strategy"]
    wire = server._tools_wire(tools, server.LLM_DEPLOYMENT)
    assert not any(t.get("type") in ("namespace", "tool_search") for t in wire)
    # a fraction of the v1 surface on the wire
    _v2(False)
    v1 = server._tools_wire(server._tools_for_request(), server.LLM_DEPLOYMENT)
    _v2(True)
    assert len(json.dumps(wire)) < len(json.dumps(v1)) / 4


def test_v2_is_per_request_and_never_execution() -> None:
    _v2(False)
    assert not server._v2_on()
    _v2(True)
    assert server._v2_on()
    server._req.chat_mode = "execution"
    assert not server._v2_on()
    server._req.chat_mode = "chat"


def test_v2_prompt_leads_and_context_follows() -> None:
    block = chat_v2.system_block("## Chart the user is viewing\nX")
    assert block.startswith(chat_v2.PROMPT)
    assert block.endswith("X")
    assert "disclaimer" in chat_v2.PROMPT          # the app prints it; the model must not


def test_query_is_one_read_only_select() -> None:
    for bad in ("DELETE FROM bars", "SELECT 1; DROP TABLE bars", "PRAGMA table_info(bars)",
                "ATTACH DATABASE 'x' AS y", "UPDATE bars SET c = 0"):
        assert "error" in chat_v2.query(sql=bad), bad


def test_query_lists_tables_when_asked_nothing() -> None:
    got = chat_v2.query()
    assert "tables" in got and "bars_1d" in got["tables"]


def test_study_specs_parse_to_indicator_arguments() -> None:
    assert chat_v2._study("ema 200") == {"name": "ema", "period": 200}
    assert chat_v2._study("supertrend 10 3") == {"name": "supertrend", "period": 10, "mult": 3.0}
    assert chat_v2._study("rsi") == {"name": "rsi"}
    assert chat_v2._study("") == {}


def test_mark_styles_resolve_names_and_drop_unknowns() -> None:
    assert mark._style({"color": "red", "line": "dashed", "weight": "thick"}) == \
        {"color": mark.PALETTE["red"], "line": "dashed", "weight": "thick"}
    assert mark._style({"color": "chartreuse", "line": "wavy"}) == {}
    assert mark._style({"color": "#12ab34"}) == {"color": "#12ab34"}


def test_arrow_and_text_shapes_resolve() -> None:
    rows = [(1_700_000_000 + i * 86400, 100 + i, 101 + i, 99 + i, 100.5 + i, 1000) for i in range(30)]
    env = {"tz_off": 19800, "fmt_time": lambda t: str(t)}
    got = mark.build([{"shape": "arrow", "from": "-10 @ low", "to": "-1 @ high", "color": "blue"},
                      {"shape": "text", "at": "-5 @ high", "label": "Close above confirms"}], rows, env)
    assert not got["errors"], got["errors"]
    arrow, text = got["items"]
    assert arrow["kind"] == "segment" and arrow["arrow"] is True and arrow["color"] == mark.PALETTE["blue"]
    assert text["kind"] == "label" and text["text"] == "Close above confirms"


def test_sandbox_free_mode_returns_any_json() -> None:
    import indicator_sandbox
    code = "def compute(data, params):\n    c = data['c']\n    return {'n': len(c), 'mean': sum(c) / len(c)}"
    reply = indicator_sandbox.run(code, [{"id": "q", "free": True, "bars": {"c": [1.0, 2.0, 3.0]}, "params": {}}])
    assert reply["ok"] and reply["results"][0]["value"] == {"n": 3, "mean": 2.0}


def test_repeated_shapes_reach_every_session() -> None:
    box = {"shape": "box", "repeat": "session", "sessions": 5}
    assert chat_v2._reach([box], "15m") >= 5 * 26
    assert chat_v2._reach([box], "1m") == 1500
    assert chat_v2._reach([{"shape": "hline"}], "15m") == 0


def test_restating_panels_drop_and_answer_panels_stay() -> None:
    server._scene.cards = [{"kind": "screen"}]
    for k in ("trend", "indicators", "patterns", "option_chain"):
        server._card_add({"kind": k})
    out = chat_v2._panels({"a": {"_card_note": "A panel …"}, "_card_note": "x"}, 1, asked=False)
    assert [c["kind"] for c in server._card_take()] == ["screen", "option_chain"]
    assert "_card_note" not in json.dumps(out) and "option chain" in out["_panel"]


def test_an_asked_for_panel_shows_one_reading() -> None:
    server._scene.cards = []
    for k in ("timeframes", "patterns", "confirmation"):
        server._card_add({"kind": k})
    out = chat_v2._panels({}, 0, asked=True)
    assert [c["kind"] for c in server._card_take()] == ["timeframes"] and "timeframes" in out["_panel"]


def test_no_panel_note_when_nothing_is_shown() -> None:
    server._scene.cards = []
    server._card_add({"kind": "trend"})
    out = chat_v2._panels({"_card_note": "x"}, 0, asked=False)
    assert server._card_take() == [] and "_panel" not in out and "_card_note" not in out
