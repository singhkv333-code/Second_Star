"""The page assistant: page data up front, few tools, formal failures."""
import asyncio
import json
import types

import pytest

from backend.services import assist_chat as a
from backend.services import flex_chat
from backend.services.tool_registry import ToolResult


def _ctx():
    return types.SimpleNamespace(kite_token=None, user_id=7, db=None, holdings=[])


def _run(gen):
    async def collect():
        return [ev async for ev in gen]
    return asyncio.run(collect())


def _fake_stream(rounds):
    """stream_openai stand-in: each call plays the next round's events."""
    calls = []

    async def stream(client, **kw):
        calls.append(kw)
        for ev in rounds[len(calls) - 1]:
            yield ev
    return stream, calls


def _text(t):
    return [{"type": "response.output_text.delta", "delta": t},
            {"type": "response.completed", "response": {"id": "r2", "usage": {
                "input_tokens": 100, "output_tokens": 10}}}]


def _call(name, args):
    item = {"type": "function_call", "id": "i1", "call_id": "c1", "name": name,
            "arguments": json.dumps(args)}
    return [{"type": "response.output_item.done", "item": item},
            {"type": "response.completed", "response": {"id": "r1", "usage": {}}}]


@pytest.fixture(autouse=True)
def _no_snapshot(monkeypatch):
    async def snap(page, symbol, ctx):
        return "### Quote\n{\"ltp\":994.1}"
    monkeypatch.setattr(a, "page_snapshot", snap)
    a._tools.cache_clear()


def test_a_page_question_is_answered_from_the_block_without_tools(monkeypatch):
    stream, calls = _fake_stream([_text("**₹994.10** today.")])
    monkeypatch.setattr("backend.llm.openai_client.stream_openai", stream)
    evs = _run(a.stream_turn(message="price?", history=[], ctx=_ctx(), client=None,
                             page="stock", symbol="INFY"))
    assert [e["type"] for e in evs] == ["start", "delta", "done"]
    assert evs[-1]["response"] == "**₹994.10** today."
    sys_msgs = [m.content for m in calls[0]["messages"] if m.role == "system"]
    assert sys_msgs[0].startswith(a.BRIEF) and "## App map" in sys_msgs[0]
    assert "On this page: Company page for INFY" in sys_msgs[1] and "994.1" in sys_msgs[1]
    assert calls[0]["reasoning_summary"] == "auto"
    assert not calls[0].get("hosted_tools")


def test_tools_run_in_parallel_then_the_model_answers(monkeypatch):
    stream, calls = _fake_stream([_call("get_symbol_news", {"symbol": "INFY"}),
                                  _text("Two headlines.")])
    monkeypatch.setattr("backend.llm.openai_client.stream_openai", stream)
    seen = []

    async def run_tool(name, args, schemas, *, ctx, own_session):
        seen.append((name, args, own_session))
        return ToolResult(name=name, args=args, success=True,
                          data={"items": [{"title": "x", "link": "http://y"}] * 30,
                                "_render_hint": "news"})
    monkeypatch.setattr(flex_chat, "_run_tool", run_tool)
    evs = _run(a.stream_turn(message="news?", history=[], ctx=_ctx(), client=None,
                             page="stock", symbol="INFY"))
    types_ = [e["type"] for e in evs]
    assert types_ == ["start", "tool_start", "tool_done", "delta", "done"]
    assert evs[1]["label"] == "Reading the news · INFY"
    assert seen == [("get_symbol_news", {"symbol": "INFY"}, True)]
    # round 2 continues the stored response with only the compacted output
    assert calls[1]["previous_response_id"] == "r1"
    out = [m for m in calls[1]["messages"] if m.role == "tool"][0].content
    assert "_render_hint" not in out and "link" not in out and out.count("title") == 12


def test_a_model_outage_reaches_the_user_as_one_formal_sentence(monkeypatch):
    stream, _ = _fake_stream([[{"type": "error",
                                "message": "HTTP 500 upstream: DeploymentNotFound trace..."}]])
    monkeypatch.setattr("backend.llm.openai_client.stream_openai", stream)
    evs = _run(a.stream_turn(message="hi", history=[], ctx=_ctx(), client=None, page="home"))
    assert evs[-1] == {"type": "error", "message": a.FAILURE["llm"]}
    assert "500" not in json.dumps(evs) and "Deployment" not in json.dumps(evs)


def test_a_tool_outside_the_pages_set_is_refused_to_the_model(monkeypatch):
    stream, calls = _fake_stream([_call("place_order", {"symbol": "INFY"}), _text("I can't.")])
    monkeypatch.setattr("backend.llm.openai_client.stream_openai", stream)

    async def run_tool(*a_, **k):
        raise AssertionError("must not execute")
    monkeypatch.setattr(flex_chat, "_run_tool", run_tool)
    evs = _run(a.stream_turn(message="buy", history=[], ctx=_ctx(), client=None, page="home"))
    assert evs[-1]["type"] == "done"
    out = [m for m in calls[1]["messages"] if m.role == "tool"][0].content
    assert "not available" in out


def test_each_page_carries_a_small_read_only_tool_set():
    for page, names in a.PAGE_TOOLS.items():
        tools, _ = a._tools(page)
        assert [t.name for t in tools] == list(names)
        size = sum(len(t.description) + len(json.dumps(t.parameters)) for t in tools)
        assert size < (20000 if page == "screener" else 6000), (page, size)
    assert not {"place_order", "manage_automation", "build_strategy"} & a.TOOLS
    stock = {t.name: t for t in a._tools("stock")[0]}
    assert set(stock["screen_fundamentals"].parameters["properties"]) == {
        "symbols", "metrics", "sort_by", "limit", "title"}


def test_the_snapshot_is_fetched_in_parallel_cached_and_never_waits_on_a_slow_source(
        monkeypatch):
    monkeypatch.undo()                       # the real page_snapshot
    a._cache.clear()
    monkeypatch.setattr(a, "SNAPSHOT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(a, "_schemas", dict)
    calls = []

    async def run_tool(name, args, schemas, *, ctx, own_session):
        calls.append(name)
        if args.get("view") == "holdings":
            await asyncio.sleep(1)
        return ToolResult(name=name, args=args, success=True, data={"v": 1.23456})
    monkeypatch.setattr(flex_chat, "_run_tool", run_tool)

    async def twice():
        t0 = asyncio.get_running_loop().time()
        one = await a.page_snapshot("portfolio", None, _ctx())
        took = asyncio.get_running_loop().time() - t0
        two = await a.page_snapshot("portfolio", None, _ctx())
        return one, two, took
    one, two, took = asyncio.run(twice())
    assert took < 0.6 and one == two and len(calls) == 3
    assert "### Holdings\nunavailable" in one and '"v":1.23' in one
