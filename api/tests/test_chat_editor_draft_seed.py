"""Shared-contract tests: ``editor_draft`` on the chat request.

When the FE has an unsaved workflow draft open in the editor, it
attaches the on-screen copy as ``editor_draft`` on the next chat
request. The backend seeds it into the conversation's active_draft
slot BEFORE the amendment-hint is built so chat amendments are
computed against what the user SEES — not whatever stale copy sits
in Redis.

These tests assert the contract end-to-end against a stub LLM:

  1. With ``editor_draft`` set, the amendment-hint workflow JSON
     blob the LLM receives carries the EDITOR's draft, not the
     pre-existing Redis ``active_draft``.
  2. Without ``editor_draft`` (the legacy case), the behaviour is
     byte-for-byte unchanged: the Redis ``active_draft`` is used.
  3. Malformed ``editor_draft`` payloads are ignored (no 500) and
     the legacy Redis flow takes over.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.llm import LLMClient, LLMResponse
from backend.llm.factory import set_llm_client_for_tests
from backend.services.chat_service import ChatService, UserContext
from backend.services.conversation_store import ActiveDraft


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
    """In-memory replacement for ConversationStore. Covers the full
    method surface the current ChatService touches (pending tool calls,
    pending resolution, clarify state, active drafts, registered
    workflow id) so chat turns don't crash on AttributeError."""

    def __init__(self) -> None:
        self.appended: list[tuple[str, str, str]] = []
        self.pending: dict[str, Any] = {}
        self.pending_resolution: dict[str, Any] = {}
        self.clarify: dict[str, Any] = {}
        self.active_drafts: dict[str, ActiveDraft] = {}
        self.registered_workflow: dict[str, Any] = {}

    def get_history(self, conv_id: str, limit: int = 20):
        return []

    def append(self, conv_id: str, user: str, assistant: str) -> None:
        self.appended.append((conv_id, user, assistant))

    # pending tool call
    def get_pending(self, conv_id: str):
        return self.pending.get(conv_id)

    def set_pending(self, conv_id: str, pending) -> None:
        self.pending[conv_id] = pending

    def clear_pending(self, conv_id: str) -> None:
        self.pending.pop(conv_id, None)

    # pending resolution ("yes/no" deterministic resolve)
    def get_pending_resolution(self, conv_id: str):
        return self.pending_resolution.get(conv_id)

    def set_pending_resolution(self, conv_id: str, pr) -> None:
        self.pending_resolution[conv_id] = pr

    def clear_pending_resolution(self, conv_id: str) -> None:
        self.pending_resolution.pop(conv_id, None)

    # clarify state (Workstream A)
    def get_clarify(self, conv_id: str):
        return self.clarify.get(conv_id)

    def set_clarify(self, conv_id: str, state) -> None:
        self.clarify[conv_id] = state

    def clear_clarify(self, conv_id: str) -> None:
        self.clarify.pop(conv_id, None)

    # active draft (the slot the amendment-hint reads)
    def get_active_draft(self, conv_id: str, symbol=None):
        return self.active_drafts.get(conv_id)

    def set_active_draft(self, conv_id: str, draft):
        self.active_drafts[conv_id] = draft
        return None

    def list_active_drafts(self, conv_id: str):
        d = self.active_drafts.get(conv_id)
        return [d] if d is not None else []

    def clear_active_draft(self, conv_id: str, symbol=None) -> None:
        self.active_drafts.pop(conv_id, None)

    # registered workflow id (post-register link)
    def get_registered_workflow_id(self, conv_id: str):
        return self.registered_workflow.get(conv_id)

    def set_registered_workflow_id(self, conv_id: str, wf_id) -> None:
        self.registered_workflow[conv_id] = wf_id


@pytest.fixture
def stub_ctx():
    return UserContext(user_id=1, kite_token="x", db=None, holdings=[])


@pytest.fixture(autouse=True)
def _clear_stub():
    set_llm_client_for_tests(None)
    yield
    set_llm_client_for_tests(None)


def _system_blob(stub: _StubClient) -> str:
    """Concatenated system content from the first (and only) LLM call."""
    assert stub.calls, "expected at least one LLM call"
    msgs = stub.calls[0]["messages"]
    return " ".join((m.content or "") for m in msgs if m.role == "system")


def _amendment_hint_blob(stub: _StubClient) -> str:
    """Just the workflow amendment-hint slice of the system prompt —
    the segment that starts with "ACTIVE <TOOL> DRAFT from" and
    carries the "DRAFT JSON: ..." dump. We isolate this so generic
    mentions of a ticker in the base system prompt (RELIANCE shows
    up in examples) don't false-positive our assertions.

    The marker comes from chat_service:
        f" ACTIVE {tool_label.upper().replace('_', ' ')} DRAFT from "
    so e.g. "ACTIVE PROPOSE WORKFLOW DRAFT from ".
    """
    blob = _system_blob(stub)
    import re as _re
    m = _re.search(r"ACTIVE [A-Z ]+ DRAFT from ", blob)
    if not m:
        return ""
    start = m.start()
    # The hint runs until the parked-draft clause ends (we keep
    # generous slack — 4000 chars covers the 1800-char DRAFT JSON
    # cap + the parked-draft sentence).
    return blob[start:start + 4000]


# ── 1. editor_draft overrides a stale Redis active_draft ──────────────


@pytest.mark.asyncio
async def test_editor_draft_overrides_stale_redis_active_draft(stub_ctx):
    """User has an unsaved draft open in the editor. The Redis
    ``active_draft`` carries an OLDER copy (different ticker / step
    config). On an amendment turn ("make it 5 shares"), the
    amendment-hint must reflect what the EDITOR shows, not Redis."""
    store = _StubStore()

    # Stale Redis copy — what would have been used pre-contract.
    store.set_active_draft("u1", ActiveDraft(
        tool_name="propose_workflow",
        draft={
            "name": "Stale RELIANCE order",
            "steps": [
                {"step_type": "trigger.cron",
                 "config": {"cron": "0 15 9 * * 1-5", "tz": "Asia/Kolkata"}},
                {"step_type": "action.place_market_order",
                 "config": {"symbol": "RELIANCE", "side": "BUY",
                            "quantity": 1}},
            ],
        },
        last_caption="(stale)",
    ))

    # Editor's on-screen copy — different symbol + qty.
    editor_draft = {
        "name": "INFY threshold buy (editor copy)",
        "description": "edited in the panel",
        "steps": [
            {"step_type": "trigger.cron",
             "label": "Daily 09:15",
             "config": {"cron": "0 15 9 * * 1-5", "tz": "Asia/Kolkata"}},
            {"step_type": "action.place_market_order",
             "label": "Buy 3 INFY",
             "config": {"symbol": "INFY", "side": "BUY", "quantity": 3}},
        ],
    }

    stub = _StubClient(queue=[
        # Tool-free reply is fine — we only inspect what the LLM SAW.
        LLMResponse(content="ok", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=store)

    await svc.handle(
        # "make it 5 shares" is a clear amendment-shaped phrase.
        "make it 5 shares",
        "u1",
        stub_ctx,
        history_override=[
            {"role": "user", "content": "build an INFY buy agent"},
            {"role": "assistant", "content": "Here's a draft."},
        ],
        editor_draft=editor_draft,
    )

    hint = _amendment_hint_blob(stub)
    # The amendment-hint always carries the DRAFT JSON inline. The
    # editor's INFY copy must be the one that landed; the stale
    # RELIANCE copy must NOT show up in the hint slice (generic
    # system-prompt mentions of RELIANCE elsewhere are fine).
    assert hint, "expected an ACTIVE ... DRAFT hint in the system prompt"
    assert "INFY" in hint
    assert "RELIANCE" not in hint
    assert "DRAFT" in hint

    # And the seed-side effect: the editor copy is now what's in the
    # store, so the NEXT turn's amendment also bases off the editor.
    cached = store.get_active_draft("u1")
    assert cached is not None
    assert cached.draft["name"] == "INFY threshold buy (editor copy)"
    steps = cached.draft["steps"]
    order_cfg = steps[1]["config"]
    assert order_cfg["symbol"] == "INFY"
    assert order_cfg["quantity"] == 3


# ── 2. Without editor_draft, behaviour is byte-for-byte unchanged ─────


@pytest.mark.asyncio
async def test_amendment_without_editor_draft_uses_redis_unchanged(stub_ctx):
    """When ``editor_draft`` is absent (legacy / closed-editor case),
    the amendment-hint reads the Redis ``active_draft`` exactly as
    before — no seeding, no override."""
    store = _StubStore()
    store.set_active_draft("u1", ActiveDraft(
        tool_name="propose_workflow",
        draft={
            "name": "Redis RELIANCE order",
            "steps": [
                {"step_type": "trigger.cron",
                 "config": {"cron": "0 15 9 * * 1-5", "tz": "Asia/Kolkata"}},
                {"step_type": "action.place_market_order",
                 "config": {"symbol": "RELIANCE", "side": "BUY",
                            "quantity": 1}},
            ],
        },
        last_caption="(redis)",
    ))

    stub = _StubClient(queue=[
        LLMResponse(content="ok", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=store)

    await svc.handle(
        "make it 5 shares",
        "u1",
        stub_ctx,
        history_override=[
            {"role": "user", "content": "build a RELIANCE buy agent"},
            {"role": "assistant", "content": "Here's a draft."},
        ],
        # editor_draft INTENTIONALLY omitted — legacy path.
    )

    hint = _amendment_hint_blob(stub)
    # The Redis-cached RELIANCE draft must still be the one injected.
    assert hint, "expected an ACTIVE ... DRAFT hint in the system prompt"
    assert "RELIANCE" in hint
    assert "INFY" not in hint

    # No seed-side effect: the cached draft is still the Redis one.
    cached = store.get_active_draft("u1")
    assert cached is not None
    assert cached.draft["name"] == "Redis RELIANCE order"


# ── 3. Malformed editor_draft is ignored, never 500 ───────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_draft", [
    {"name": "no steps array"},                # missing steps
    {"name": "wrong type", "steps": "not-a-list"},  # steps not list
    {"steps": []},                             # empty after coercion
    {"steps": [{"label": "no step_type"}]},    # no step_type strings
    {"steps": [{"step_type": ""}]},            # empty step_type string
    {"steps": [42, "garbage"]},                # non-dict entries
])
async def test_malformed_editor_draft_falls_back_to_redis(stub_ctx, bad_draft):
    """Defensive coercion: garbage in ``editor_draft`` must NOT 500
    the turn and must NOT clobber the Redis ``active_draft``."""
    store = _StubStore()
    store.set_active_draft("u1", ActiveDraft(
        tool_name="propose_workflow",
        draft={
            "name": "Redis RELIANCE order",
            "steps": [
                {"step_type": "action.place_market_order",
                 "config": {"symbol": "RELIANCE", "side": "BUY",
                            "quantity": 1}},
            ],
        },
        last_caption="(redis)",
    ))

    stub = _StubClient(queue=[
        LLMResponse(content="ok", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=store)

    # Must not raise.
    turn = await svc.handle(
        "make it 5 shares",
        "u1",
        stub_ctx,
        history_override=[
            {"role": "user", "content": "build a RELIANCE buy agent"},
            {"role": "assistant", "content": "Here's a draft."},
        ],
        editor_draft=bad_draft,
    )
    assert turn.response  # got a response, not a crash

    # Cached draft is still the Redis original — not the garbage.
    cached = store.get_active_draft("u1")
    assert cached is not None
    assert cached.draft["name"] == "Redis RELIANCE order"


# ── 4. editor_draft seeds when Redis is EMPTY (closed-editor → open) ──


@pytest.mark.asyncio
async def test_editor_draft_seeds_when_redis_is_empty(stub_ctx):
    """A user can open the editor for the first time on a fresh chat
    session — Redis has no ``active_draft``. The editor copy seeds
    cleanly and the amendment-hint fires against it."""
    store = _StubStore()
    assert store.get_active_draft("u1") is None  # truly empty

    editor_draft = {
        "name": "Editor-only INFY draft",
        "steps": [
            {"step_type": "action.place_market_order",
             "config": {"symbol": "INFY", "side": "BUY", "quantity": 2}},
        ],
    }

    stub = _StubClient(queue=[
        LLMResponse(content="ok", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=store)

    await svc.handle(
        "make it 5 shares",
        "u1",
        stub_ctx,
        history_override=[
            {"role": "user", "content": "build an INFY agent"},
            {"role": "assistant", "content": "Here's a draft."},
        ],
        editor_draft=editor_draft,
    )

    hint = _amendment_hint_blob(stub)
    assert hint, "expected an ACTIVE ... DRAFT hint in the system prompt"
    assert "INFY" in hint

    cached = store.get_active_draft("u1")
    assert cached is not None
    assert cached.tool_name == "propose_workflow"
    assert cached.draft["name"] == "Editor-only INFY draft"
