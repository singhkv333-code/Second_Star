"""The chat's hands on the workspace, and the tool surface it is offered.

`workspace` verifies every op against the catalog the page sends before
anything reaches the page; tool loading puts the chart-shaped core on the
wire and the purpose groups behind hosted tool search. These tests pin the
seams — no model calls.

Run: ../../pivot/.venv/bin/python -m pytest test_workspace_tool.py -q
"""
from __future__ import annotations

import json

import dataserver as server

CATALOG = [
    {"type": "screener", "title": "Screener", "single": False, "linkable": True,
     "settings": [{"key": "rows", "kind": "seg", "options": [25, 50]},
                  {"key": "names", "kind": "toggle"}],
     "writes": {"filters": "…"}},
    {"type": "notes", "title": "Notes", "single": False, "linkable": True,
     "settings": [{"key": "textSize", "kind": "seg", "options": ["s", "m", "l"]}],
     "writes": {"text": "…"}},
    {"type": "news", "title": "News", "single": False, "linkable": False,
     "settings": [{"key": "sources", "kind": "chips", "options": ["et", "mint"]}], "writes": None},
]


def _desk(widgets=()):
    server._req.chat_mode = "chat"
    server._req.widget_catalog = CATALOG
    server._req.workspace = {"widgets": list(widgets), "ack": []}
    server._req.ws_opened = {}
    server._view_take()
    server._card_take()


def _ops():
    return [op for v in server._view_take() if v.get("kind") == "workspace" for op in v["ops"]]


def test_open_mints_an_id_and_streams_one_op_group():
    _desk()
    out = server.tool_workspace([{"op": "open", "type": "notes", "where": "right",
                                  "settings": {"textSize": "l"}, "content": {"text": "Watch 720"}}])
    r = out["results"][0]
    assert "error" not in r and r["id"].startswith("notes:")
    ops = _ops()
    assert len(ops) == 1 and ops[0]["id"] == r["id"] and ops[0]["content"]["text"] == "Watch 720"


def test_bad_settings_and_unknown_ids_never_reach_the_page():
    _desk()
    out = server.tool_workspace([
        {"op": "open", "type": "notes", "settings": {"colour": "red"}},
        {"op": "open", "type": "screener", "settings": {"rows": 30}},
        {"op": "configure", "id": "notes:nothere", "settings": {"textSize": "s"}},
        {"op": "open", "type": "rocket"},
        {"op": "open", "type": "news", "content": {"text": "x"}},
    ])
    errs = [r.get("error", "") for r in out["results"]]
    assert "not a notes setting" in errs[0]
    assert "must be one of" in errs[1]
    assert "no 'notes:nothere' on the desk — open it first" in errs[2]
    assert "no widget kind 'rocket'" in errs[3]
    assert "no content to write" in errs[4]
    assert _ops() == []


def test_a_later_op_can_use_an_id_opened_earlier_in_the_same_call():
    _desk()
    out = server.tool_workspace([{"op": "open", "type": "notes"}])
    nid = out["results"][0]["id"]
    out = server.tool_workspace([{"op": "write", "id": nid, "content": {"text": "second line"}},
                                 {"op": "move", "id": nid, "where": f"split:{nid}:bottom"}])
    assert all("error" not in r for r in out["results"])


def test_describe_and_read_answer_from_the_page_without_touching_it():
    _desk([{"id": "notes:ab12", "type": "notes", "title": "Notes", "symbol": "SBIN",
            "content": "Support at 790 held twice."}])
    out = server.tool_workspace([{"op": "describe", "type": "screener"},
                                 {"op": "read", "id": "notes:ab12"}])
    assert out["results"][0]["describe"]["type"] == "screener"
    assert "790" in out["results"][1]["content"]
    assert _ops() == []


def test_workspace_block_names_ids_and_failed_ops():
    _desk([{"id": "screener:k2", "type": "screener", "title": "Screener", "link": "1",
            "symbol": "HDFCBANK", "visible": True, "model": "Banks · 26 of 500"}])
    server._req.workspace["ack"] = [{"op": "write", "id": "screener:k2", "ok": False, "error": "boom"}]
    line = server._workspace_line()
    assert "screener:k2" in line and "link 1 (HDFCBANK)" in line and "boom" in line
    server._req.chat_mode = "execution"
    assert server._workspace_line() == ""


def test_a_screen_written_into_the_screener_runs_on_the_engine():
    _desk([{"id": "screener:k2", "type": "screener", "title": "Screener"}])
    out = server.tool_workspace([{"op": "write", "id": "screener:k2", "content": {
        "name": "Oversold", "filters": [{"feature": "rsi14", "op": "lt", "value": 35}], "sort": "rsi14"}}])
    r = out["results"][0]
    if "error" in r:                       # an empty daily universe on this machine
        assert "universe" in r["error"] or "bars_1d" in r["error"]
        return
    assert r["result"]["matched"] is not None and "top" in r["result"]
    assert not [c for c in server._card_take() if c.get("kind") == "screen"]   # no stray chat panel
    bad = server.tool_workspace([{"op": "write", "id": "screener:k2", "content": {
        "filters": [{"feature": "rsi_fourteen", "op": "lt", "value": 35}]}}])
    assert "unknown feature" in bad["results"][0]["error"]


# ── tool loading ───────────────────────────────────────────────────────────

def test_core_stays_loaded_and_groups_go_behind_tool_search():
    server._req.chat_mode = "chat"
    flat = server._tools_for_request()
    wire = server._tools_wire(flat, "gpt-6-luna")
    names = {t.get("name") for t in wire if t.get("type") == "function"}
    assert {"get_patterns", "mark", "workspace", "screen_universe"} <= names
    assert "set_alert" not in names and "custom_indicator" not in names
    spaces = {t["name"]: t for t in wire if t.get("type") == "namespace"}
    assert "alerts" in spaces and all(f["defer_loading"] for f in spaces["alerts"]["tools"])
    # loaded tools default to strict schemas (every parameter required) unless told otherwise
    assert all(f["strict"] is False for n in spaces.values() for f in n["tools"])
    assert wire[-1] == {"type": "tool_search"}
    deferred = {f["name"] for n in spaces.values() for f in n["tools"]}
    assert names | deferred == {t["name"] for t in flat}       # nothing lost


def test_no_tool_search_where_it_is_not_supported():
    server._req.chat_mode = "chat"
    flat = server._tools_for_request()
    assert server._tools_wire(flat, "gpt-5.2") == flat
    server._req.chat_mode = "execution"
    flat = server._tools_for_request()
    assert server._tools_wire(flat, "gpt-6-luna") == flat
    server._req.chat_mode = "chat"


def test_search_items_are_replayed_and_stripped_for_an_older_arm():
    out = [{"type": "reasoning", "id": "r1"},
           {"type": "tool_search_call", "execution": "server", "call_id": None, "status": "completed",
            "arguments": {"paths": ["alerts"]}, "id": "ts1"},
           {"type": "tool_search_output", "execution": "server", "call_id": None, "status": "completed",
            "tools": [{"type": "namespace", "name": "alerts"}], "id": "ts2"},
           {"type": "function_call", "name": "set_alert", "namespace": "alerts", "call_id": "c1"}]
    items = server._search_items(out)
    assert [i["type"] for i in items] == ["tool_search_call", "tool_search_output"]
    assert all("id" not in i for i in items)
    wire = items + [{"type": "function_call", "name": "set_alert", "namespace": "alerts",
                     "call_id": "c1", "arguments": "{}"}]
    old = server._wire_for("gpt-5.2", wire)
    assert [i["type"] for i in old] == ["function_call"] and "namespace" not in old[0]
    assert server._wire_for("gpt-6-luna", wire) == wire
    json.dumps(items)
