"""End-to-end ChatService tests using a stub LLM client.

Covers the full flow without hitting any real provider:
  - Plain text reply (no tool call)
  - Tool call → execute → narrate (two-hop)
  - ASK_USER bubble surfaces as the assistant message
  - Validation failure surfaces as a structured error

The stub implements LLMClient.complete and returns a programmable
response queue. ChatService picks it up via set_llm_client_for_tests.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from backend.llm import LLMClient, LLMMessage, LLMResponse, ToolDef
from backend.llm.factory import set_llm_client_for_tests
from backend.services.chat_service import (
    ChatService,
    UserContext,
)
from backend.services import validation_handler as vr
from backend.services.tool_registry import ToolResult


class _StubClient(LLMClient):
    provider_name = "stub"
    model = "stub-model"

    def __init__(self, queue: list[LLMResponse]) -> None:
        self.queue = list(queue)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if not self.queue:
            return LLMResponse(content="(empty queue)", finish_reason="stop")
        return self.queue.pop(0)


class _StubStore:
    """In-memory replacement for ConversationStore so we don't need
    a Redis connection during tests."""
    def __init__(self) -> None:
        self.appended: list[tuple[str, str, str]] = []
        self.pending: dict[str, object] = {}
        self.active_drafts: dict[str, object] = {}

    def get_history(self, conv_id: str, limit: int = 20):
        return []

    def append(self, conv_id: str, user: str, assistant: str) -> None:
        self.appended.append((conv_id, user, assistant))

    def get_pending(self, conv_id: str):
        return self.pending.get(conv_id)

    def set_pending(self, conv_id: str, pending) -> None:
        self.pending[conv_id] = pending

    def clear_pending(self, conv_id: str) -> None:
        self.pending.pop(conv_id, None)

    def get_active_draft(self, conv_id: str):
        return self.active_drafts.get(conv_id)

    def set_active_draft(self, conv_id: str, draft) -> None:
        self.active_drafts[conv_id] = draft

    def clear_active_draft(self, conv_id: str) -> None:
        self.active_drafts.pop(conv_id, None)


@pytest.fixture
def stub_ctx():
    return UserContext(user_id=1, kite_token="x", db=None, holdings=[])


@pytest.fixture(autouse=True)
def _clear_stub():
    set_llm_client_for_tests(None)
    yield
    set_llm_client_for_tests(None)


@pytest.mark.asyncio
async def test_plain_text_reply_passes_through(stub_ctx):
    """Non-fast-path question → goes to LLM → plain text reply."""
    stub = _StubClient(queue=[
        LLMResponse(
            content="That's an interesting question about strategy choice.",
            finish_reason="stop",
        ),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    # "should I diversify?" is conversational but doesn't fast-path
    turn = await svc.handle("should I diversify?", "u1", stub_ctx, history_override=[])
    assert turn.response == "That's an interesting question about strategy choice."
    assert turn.tools_called == []


@pytest.mark.asyncio
async def test_fast_path_bypasses_llm(stub_ctx):
    """Greetings/help/thanks must NOT hit the LLM — fast path."""
    stub = _StubClient(queue=[])  # any LLM call would crash with empty queue
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle("hi", "u1", stub_ctx, history_override=[])
    assert "tell me what" in turn.response.lower()
    assert turn.tools_called == []
    assert len(stub.calls) == 0
    assert turn.latency_breakdown.get("fast_path") is not None


@pytest.mark.asyncio
async def test_tool_call_two_hop_narration(stub_ctx, monkeypatch):
    """Model picks a tool → wrapper executes → second hop narrates."""
    async def fake_execute(name, args, **kw):
        return ToolResult(
            name=name, args=args, success=True,
            data={"price": 2487.50}, logiccard=None, error=None,
        )
    monkeypatch.setattr(vr, "execute", fake_execute)
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object",
        "properties": {"symbol": {"type": "string"}},
        "required": ["symbol"],
    })

    stub = _StubClient(queue=[
        LLMResponse(
            content=None,
            tool_calls=[{
                "id": "call_1", "name": "get_live_price",
                "arguments": {"symbol": "RELIANCE"},
            }],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="RELIANCE is trading at ₹2,487.50.", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle("price of reliance", "u1", stub_ctx, history_override=[])

    assert turn.tools_called == ["get_live_price"]
    assert "2,487" in turn.response or "2487" in turn.response
    # Second hop got the tool result message
    second_call = stub.calls[1]
    msgs = second_call["messages"]
    tool_msg = next(m for m in msgs if m.role == "tool")
    assert "2487.5" in tool_msg.content


@pytest.mark.asyncio
async def test_ask_user_surfaces_as_assistant_question(stub_ctx, monkeypatch):
    """When the model picks ASK_USER, the wrapper short-circuits and
    surfaces the question. No second LLM hop, no card."""
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: None)

    stub = _StubClient(queue=[
        LLMResponse(
            content=None,
            tool_calls=[{
                "id": "askcall", "name": vr.ASK_USER_TOOL_NAME,
                "arguments": {"question": "What dip threshold should trigger the buy?"},
            }],
            finish_reason="tool_calls",
        ),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle("buy on a dip", "u1", stub_ctx, history_override=[])

    assert turn.response == "What dip threshold should trigger the buy?"
    assert turn.tools_called == [vr.ASK_USER_TOOL_NAME]
    assert turn.raw_data == {"_render_hint": "ask_user"}
    # Only one LLM call (no second hop after ASK_USER)
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_completeness_gate_fires_clarification_question(stub_ctx, monkeypatch):
    """Model emits a tool call missing required fields → completeness
    check fires → minimal-reasoning LLM call writes a question →
    user gets it without ever invoking the executor."""
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object",
        "properties": {"symbol": {"type": "string", "description": "NSE ticker"},
                       "quantity": {"type": "integer",
                                    "description": "Number of shares",
                                    "minimum": 1}},
        "required": ["symbol", "quantity"],
    })
    monkeypatch.setattr(vr, "_description_for_tool", lambda n: "Place a market order")

    # Stub the executor to crash if it's reached — proves the gate
    # short-circuited.
    async def must_not_run(*a, **kw):
        raise AssertionError("executor called despite missing fields")
    monkeypatch.setattr(vr, "execute", must_not_run)

    stub = _StubClient(queue=[
        # Hop 1: model emits buy with NO symbol or quantity. The
        # completeness gate now generates the clarification question
        # deterministically (no follow-up LLM call), so a single hop
        # is enough.
        LLMResponse(
            content=None,
            tool_calls=[{
                "id": "c1", "name": "place_market_order", "arguments": {},
            }],
            finish_reason="tool_calls",
        ),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle("buy some shares", "u1", stub_ctx, history_override=[])

    # The deterministic template surfaces the missing fields by their
    # schema descriptions. We assert structure (a question was shown,
    # no executor ran) rather than exact wording.
    assert turn.response  # non-empty question
    assert turn.raw_data == {"_render_hint": "ask_user"}


@pytest.mark.asyncio
async def test_llm_error_returns_unavailable_fallback(stub_ctx):
    """A finish_reason='error' on the first hop → graceful fallback,
    no exception bubbles to the user."""
    stub = _StubClient(queue=[
        LLMResponse(content="HTTP 502 bad gateway", finish_reason="error"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    # Use a non-fast-path message so we hit the LLM
    turn = await svc.handle("show me INFY's PE", "u1", stub_ctx, history_override=[])
    assert "temporarily unavailable" in turn.response.lower()
    assert turn.raw_data == {"_llm_unavailable": True}


@pytest.mark.asyncio
async def test_tool_error_returns_question_no_llm_retry(stub_ctx, monkeypatch):
    """Non-`propose_workflow` tools fail single-shot — error becomes a
    deterministic question, no LLM retry. (`propose_workflow` keeps a
    one-retry escape hatch; covered by a different test.)

    Test contract:
      - LLM is called exactly once.
      - Tool runs once, returns error.
      - The chat turn returns a deterministic question, not the
        model's next-iteration output.
    """
    from backend.services.tool_registry import ToolResult

    call_count = {"n": 0}
    async def fake_execute(name, args, **kw):
        call_count["n"] += 1
        return ToolResult(
            name=name, args=args, success=False, data={},
            error="symbol: unrecognised ticker 'NFTY'",
        )
    monkeypatch.setattr(vr, "execute", fake_execute)
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "transaction_type": {"type": "string", "enum": ["BUY", "SELL"]},
            "quantity": {"type": "integer"},
        },
        "required": ["symbol", "transaction_type", "quantity"],
    })

    stub = _StubClient(queue=[
        LLMResponse(
            content=None,
            tool_calls=[{
                "id": "p1", "name": "place_market_order",
                "arguments": {
                    "symbol": "NFTY", "transaction_type": "BUY",
                    "quantity": 10,
                },
            }],
            finish_reason="tool_calls",
        ),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle(
        "buy 10 NFTY", "u1", stub_ctx, history_override=[],
    )

    assert call_count["n"] == 1
    assert len(stub.calls) == 1, "no LLM retry expected for non-propose tools"
    assert turn.response, "expected a non-empty deterministic question"
    assert turn.raw_data and turn.raw_data.get("_render_hint") == "ask_user"


@pytest.mark.asyncio
async def test_multi_tool_chain_in_one_turn(stub_ctx, monkeypatch):
    """Agentic loop key property: model can call N tools sequentially
    in one user turn, with each tool's result informing the next.
    Exit-gate item #3 — 'compare RELIANCE PE to TCS and INFY' should
    do three lookups then synthesise."""
    from backend.services.tool_registry import ToolResult

    fake_data = {
        "RELIANCE": {"pe": 25.4, "symbol": "RELIANCE"},
        "TCS": {"pe": 28.7, "symbol": "TCS"},
        "INFY": {"pe": 22.1, "symbol": "INFY"},
    }
    async def fake_execute(name, args, **kw):
        return ToolResult(
            name=name, args=args, success=True,
            data=fake_data.get(args.get("symbol", ""), {}),
        )
    monkeypatch.setattr(vr, "execute", fake_execute)
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object",
        "properties": {"symbol": {"type": "string"}},
        "required": ["symbol"],
    })

    stub = _StubClient(queue=[
        # Hop 1: fetch RELIANCE
        LLMResponse(
            content=None,
            tool_calls=[{"id": "c1", "name": "get_holding_detail",
                         "arguments": {"symbol": "RELIANCE"}}],
            finish_reason="tool_calls",
        ),
        # Hop 2: fetch TCS
        LLMResponse(
            content=None,
            tool_calls=[{"id": "c2", "name": "get_holding_detail",
                         "arguments": {"symbol": "TCS"}}],
            finish_reason="tool_calls",
        ),
        # Hop 3: fetch INFY
        LLMResponse(
            content=None,
            tool_calls=[{"id": "c3", "name": "get_holding_detail",
                         "arguments": {"symbol": "INFY"}}],
            finish_reason="tool_calls",
        ),
        # Hop 4: synthesise
        LLMResponse(
            content="RELIANCE PE 25.4, TCS 28.7, INFY 22.1.",
            finish_reason="stop",
        ),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle(
        "What's RELIANCE PE vs TCS and INFY?", "u1", stub_ctx, history_override=[],
    )

    # All three tools were called; synthesis ran on the 4th hop.
    assert "RELIANCE" in turn.response
    assert "TCS" in turn.response and "INFY" in turn.response
    assert "25.4" in turn.response and "28.7" in turn.response
    assert len(stub.calls) == 4
    # Each call's messages list grew with prior tool results
    final_call_msgs = stub.calls[-1]["messages"]
    tool_msgs = [m for m in final_call_msgs if m.role == "tool"]
    assert len(tool_msgs) == 3


@pytest.mark.asyncio
async def test_circuit_breaker_after_max_tool_calls(stub_ctx, monkeypatch):
    """If the model keeps calling tools forever, the loop bails at
    MAX_TOOL_CALLS=8 with a 'got a bit lost' message."""
    from backend.services.tool_registry import ToolResult

    async def fake_execute(name, args, **kw):
        return ToolResult(name=name, args=args, success=True, data={"ok": True})
    monkeypatch.setattr(vr, "execute", fake_execute)
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: None)

    # Always return a tool call, never finish.
    looping_response = LLMResponse(
        content=None,
        tool_calls=[{
            "id": "x", "name": "get_live_price",
            "arguments": {"symbol": "INFY"},
        }],
        finish_reason="tool_calls",
    )
    stub = _StubClient(queue=[looping_response] * 20)
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle("loop forever", "u1", stub_ctx, history_override=[])

    assert turn.raw_data.get("_render_hint") == "circuit_breaker"
    assert "got a bit lost" in turn.response.lower() or "more specifics" in turn.response.lower()


@pytest.mark.asyncio
async def test_chat_turn_records_latency_breakdown(stub_ctx):
    """Every turn now ships a per-hop latency breakdown for
    observability. The agentic loop names hops `llm_hop_N`."""
    stub = _StubClient(queue=[
        LLMResponse(content="A diversified portfolio is...", finish_reason="stop", latency_ms=42),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    # Use a non-fast-path message so we actually hit the LLM
    turn = await svc.handle("should I diversify?", "u1", stub_ctx, history_override=[])
    assert "llm_hop_1" in turn.latency_breakdown
    assert "total" in turn.latency_breakdown
    assert turn.latency_breakdown["llm_hop_1"] == 42
    assert turn.latency_ms == turn.latency_breakdown["total"]


@pytest.mark.asyncio
async def test_fast_path_records_fast_path_in_breakdown(stub_ctx):
    """Fast-path turns log under `fast_path` key, never hit `llm_hop_*`."""
    stub = _StubClient(queue=[])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle("thanks", "u1", stub_ctx, history_override=[])
    assert "fast_path" in turn.latency_breakdown
    assert "llm_hop_1" not in turn.latency_breakdown


@pytest.mark.asyncio
async def test_fast_resume_executes_tool_with_zero_llm_hops(stub_ctx, monkeypatch):
    """Change 2 — when the previous turn left a PendingToolCall and the
    user replies with a clean value, splice and execute. ZERO LLM hops
    on the resume turn.

    Test contract:
      - Pending state: tool=create_gtt_order, missing_field=trigger_price
      - User reply: "1400"
      - Expected: tool runs once with trigger_price=1400, no LLM call.
    """
    from backend.services.conversation_store import PendingToolCall
    from backend.services.tool_registry import ToolResult

    captured = {"args": None, "name": None, "n": 0}
    async def fake_execute(name, args, **kw):
        captured["n"] += 1
        captured["args"] = dict(args)
        captured["name"] = name
        return ToolResult(
            name=name, args=args, success=True,
            data={"trigger": args.get("trigger_price")},
            logiccard={"action": "BUY", "symbol": "INFY", "quantity": 10},
        )
    monkeypatch.setattr(vr, "execute", fake_execute)
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "trigger_price": {"type": "number"},
            "limit_price": {"type": "number"},
            "quantity": {"type": "integer"},
        },
        "required": ["symbol", "trigger_price", "limit_price", "quantity"],
    })

    store = _StubStore()
    store.set_pending("u1", PendingToolCall(
        tool_name="create_gtt_order",
        args={
            "symbol": "INFY", "limit_price": 1410, "quantity": 10,
        },
        missing_field="trigger_price",
        field_type="float",
        field_description="trigger price (INR)",
    ))
    # Empty queue — if the chat layer asks for an LLM call this fails.
    stub = _StubClient(queue=[])
    set_llm_client_for_tests(stub)

    svc = ChatService(store=store)
    turn = await svc.handle("1400", "u1", stub_ctx, history_override=[])

    assert captured["n"] == 1, "tool should run exactly once"
    assert captured["name"] == "create_gtt_order"
    assert captured["args"]["trigger_price"] == 1400.0
    assert captured["args"]["symbol"] == "INFY"   # original args preserved
    assert len(stub.calls) == 0, "ZERO LLM hops expected on resume"
    assert turn.tools_called == ["create_gtt_order"]
    assert "u1" not in store.pending, "pending state should be cleared"


@pytest.mark.asyncio
async def test_fast_resume_cancellation_clears_pending(stub_ctx, monkeypatch):
    """User reply 'cancel' → pending cleared, fall through to LLM path."""
    from backend.services.conversation_store import PendingToolCall

    store = _StubStore()
    store.set_pending("u1", PendingToolCall(
        tool_name="place_market_order",
        args={"symbol": "INFY", "transaction_type": "BUY"},
        missing_field="quantity",
        field_type="int",
        field_description="number of shares",
    ))
    stub = _StubClient(queue=[
        LLMResponse(content="Got it — order cancelled.", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)

    svc = ChatService(store=store)
    turn = await svc.handle("never mind", "u1", stub_ctx, history_override=[])

    assert "u1" not in store.pending
    assert "cancel" in turn.response.lower() or len(stub.calls) == 1


@pytest.mark.asyncio
async def test_fast_resume_multiclause_falls_through_to_llm(stub_ctx, monkeypatch):
    """Reply with a value AND modification ('1400, and use CNC') falls
    through to the LLM — not a clean value reply."""
    from backend.services.conversation_store import PendingToolCall

    store = _StubStore()
    store.set_pending("u1", PendingToolCall(
        tool_name="create_gtt_order",
        args={"symbol": "INFY"},
        missing_field="trigger_price",
        field_type="float",
        field_description="trigger price",
    ))
    stub = _StubClient(queue=[
        LLMResponse(content="OK, taking that into account.", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)

    svc = ChatService(store=store)
    await svc.handle("1400 and use CNC instead", "u1", stub_ctx,
                     history_override=[])
    # LLM was hit because the reply wasn't a pure-value shape.
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_active_draft_cached_when_propose_workflow_succeeds(
    stub_ctx, monkeypatch,
):
    """When propose_workflow returns success, the active draft is
    persisted in conversation state so the next turn can amend it
    directly via the followup_hint (no history regex scan)."""
    from backend.services.tool_registry import ToolResult

    draft = {
        "name": "Daily NIFTYBEES open buy",
        "steps": [
            {"step_type": "trigger.cron",
             "config": {"cron": "0 15 9 * * 1-5", "tz": "Asia/Kolkata"}},
            {"step_type": "action.place_market_order",
             "config": {"symbol": "NIFTYBEES", "side": "BUY", "quantity": 1}},
        ],
        "rationale": "Buy NIFTYBEES at open every weekday.",
    }

    async def fake_execute(name, args, **kw):
        return ToolResult(
            name=name, args=args, success=True, data=draft,
            logiccard=None,
        )
    monkeypatch.setattr(vr, "execute", fake_execute)
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object",
        "properties": {"user_intent": {"type": "string"}},
        "required": ["user_intent"],
    })

    stub = _StubClient(queue=[
        # Hop 1 — emit propose_workflow.
        LLMResponse(
            content=None,
            tool_calls=[{
                "id": "p1", "name": "propose_workflow",
                "arguments": {"user_intent": "buy NIFTYBEES at open daily"},
            }],
            finish_reason="tool_calls",
        ),
        # Hop 2 — final text.
        LLMResponse(content="Done — drafted.", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)
    store = _StubStore()
    svc = ChatService(store=store)
    await svc.handle("buy NIFTYBEES at open daily", "u1", stub_ctx,
                     history_override=[])

    # Active draft was cached.
    cached = store.get_active_draft("u1")
    assert cached is not None
    assert cached.tool_name == "propose_workflow"
    assert cached.draft["name"] == "Daily NIFTYBEES open buy"
    assert len(cached.draft["steps"]) == 2


@pytest.mark.asyncio
async def test_followup_hint_includes_active_draft_when_present(
    stub_ctx, monkeypatch,
):
    """When an active draft exists and the user replies to a
    clarification, the system message we send to the LLM contains the
    actual draft JSON — not a regex-scraped paraphrase."""
    from backend.services.conversation_store import ActiveDraft
    from backend.services.tool_registry import ToolResult

    store = _StubStore()
    store.set_active_draft("u1", ActiveDraft(
        tool_name="propose_workflow",
        draft={"name": "X", "steps": [{"step_type": "trigger.cron"}]},
        last_caption="prior draft caption",
    ))

    # Stub: any LLM call returns final text; we just want to inspect
    # what messages it received.
    stub = _StubClient(queue=[
        LLMResponse(content="ok", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=store)
    # History with a prior assistant question to trigger followup hint.
    await svc.handle(
        "1 lot",
        "u1",
        stub_ctx,
        history_override=[
            {"role": "user", "content": "build me an agent"},
            {"role": "assistant", "content": "What quantity?"},
        ],
    )

    assert len(stub.calls) == 1
    sent_messages = stub.calls[0]["messages"]
    system_blobs = " ".join(
        (m.content or "") for m in sent_messages if m.role == "system"
    )
    # The hint label now interpolates the active draft's tool name:
    # the format is "ACTIVE <TOOL> DRAFT" — e.g. "ACTIVE PROPOSE
    # WORKFLOW DRAFT" for a propose_workflow draft. Assert on the
    # durable markers rather than the exact phrase so a future
    # rewording doesn't trip the test.
    assert "ACTIVE" in system_blobs and "WORKFLOW DRAFT" in system_blobs
    assert "trigger.cron" in system_blobs   # actual draft JSON injected


@pytest.mark.asyncio
async def test_active_draft_cleared_on_explicit_cancel(stub_ctx, monkeypatch):
    """Cancellation off-ramp also clears the active draft (not just
    pending). The user is abandoning the whole thing."""
    from backend.services.conversation_store import ActiveDraft, PendingToolCall

    store = _StubStore()
    store.set_active_draft("u1", ActiveDraft(
        tool_name="propose_workflow",
        draft={"name": "X", "steps": []},
    ))
    store.set_pending("u1", PendingToolCall(
        tool_name="propose_workflow", args={},
        missing_field="quantity", field_type="int",
        field_description="qty",
    ))
    stub = _StubClient(queue=[
        LLMResponse(content="Cancelled.", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=store)
    await svc.handle("never mind", "u1", stub_ctx, history_override=[])

    assert "u1" not in store.pending
    assert "u1" not in store.active_drafts


# ── Intent classification (automation vs agent vs other) ────────────


@pytest.mark.parametrize("message,expected", [
    # Automation — single deterministic action
    ("buy 10 RELIANCE at market",         "automation"),
    ("sell 5 INFY",                       "automation"),
    ("buy 100 TCS at 3500",               "automation"),
    ("set a 5% stop loss on my INFY",     "automation"),
    ("place an SL at ₹1400 on RELIANCE",  "automation"),
    ("GTT buy 5 TCS at 3000",             "automation"),
    ("create a SIP for ₹5000 in NIFTYBEES every Monday", "automation"),
    ("SIP ₹5000 in NIFTYBEES every Monday at 09:15",     "automation"),
    ("square off all intraday",           "automation"),
    ("square off my RELIANCE position",   "automation"),
    # Agent — multi-step workflow
    ("build me an agent that buys NIFTYBEES every Monday", "agent"),
    ("create a strategy where I buy when RSI < 30",        "agent"),
    ("set up an automation to rebalance monthly",          "agent"),
    ("watch my portfolio and alert me if any holding exceeds 30%", "agent"),
    ("buy NIFTYBEES whenever RSI drops below 30",          "agent"),
    ("buy when RSI < 30 over the next year",               "agent"),
    ("if RELIANCE dips 5% then buy 10 shares",             "agent"),
    ("when RELIANCE drops 5% buy 10 shares",               "agent"),
    ("buy at open and sell at close every weekday",        "agent"),
    ("SIP ₹5000 every Monday IF cash > ₹50,000",           "agent"),
    # Other — chat / data lookup
    ("what is the price of RELIANCE",     "other"),
    ("what's PE of INFY",                 "other"),
    ("explain RSI",                       "other"),
    ("show me my portfolio",              "other"),
    ("hi",                                "other"),
])
def test_classify_intent(message, expected):
    from backend.services.chat_service import _classify_intent
    assert _classify_intent(message) == expected, (
        f"misclassified: {message!r}"
    )
