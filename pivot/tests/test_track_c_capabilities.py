"""Track C capability tests.

Covers the five Track C builds:
  1. register_workflow / get_workflow_status chat tools (armed-state
     introspection, register-not-execute).
  2. Addressable multi-draft store (per-symbol drafts, named back-ref,
     LRU cap).
  3. roll_option_position (close + reopen priced off the live chain).
  4. Weekly timeframe indicator evaluation (resample, watcher honoring,
     skeleton parse — no silent daily downgrade).
  5. Staged scale-out exits (multi-branch parse + draft + one-shot
     latch).
"""
import asyncio
import uuid

import pytest


# ── #4 Weekly timeframe ──────────────────────────────────────────────


def _daily_bars(n=400, start=100.0):
    """Synthetic daily bars, weekdays only, mild uptrend."""
    from datetime import date, timedelta
    bars = []
    d = date(2024, 1, 1)
    px = start
    i = 0
    while len(bars) < n:
        if d.weekday() < 5:
            px = px * (1.001 if i % 3 else 0.999)
            bars.append({
                "date": d.isoformat(),
                "open": round(px * 0.999, 2),
                "high": round(px * 1.01, 2),
                "low": round(px * 0.99, 2),
                "close": round(px, 2),
                "volume": 1000 + i,
            })
            i += 1
        d += timedelta(days=1)
    return bars


def test_weekly_resample_shapes_w_fri_bars():
    from backend.workflows.dsl.data_accessor import (
        resample_daily_bars_to_weekly,
    )
    bars = _daily_bars(60)
    wk = resample_daily_bars_to_weekly(bars)
    assert wk is not None
    # ~12 weeks out of 60 weekdays.
    assert 10 <= len(wk) <= 14
    assert {"date", "open", "high", "low", "close", "volume"} <= set(wk.columns)
    # Weekly high must be >= weekly close (aggregation really happened).
    assert (wk["high"] >= wk["close"] - 1e-9).all()


def test_compute_indicator_sync_weekly_differs_from_daily(monkeypatch):
    import backend.workflows.scheduler as sched
    import backend.kite.market_data as mkt

    bars = _daily_bars(400)
    monkeypatch.setattr(
        mkt, "get_historical_ohlcv", lambda *a, **k: bars,
    )
    daily = sched._compute_indicator_sync("TEST", "sma", 14, "daily")
    weekly = sched._compute_indicator_sync("TEST", "sma", 14, "weekly")
    assert daily is not None and weekly is not None
    # A 14-bar SMA over weekly closes covers ~70 trading days vs 14 —
    # the two must differ on a trending series.
    assert abs(daily - weekly) > 1e-6


def test_compute_indicator_sync_weekly_insufficient_history_is_none(monkeypatch):
    import backend.workflows.scheduler as sched
    import backend.kite.market_data as mkt

    # 40 daily bars = ~8 weekly bars < the min-history floor → honest None.
    monkeypatch.setattr(
        mkt, "get_historical_ohlcv", lambda *a, **k: _daily_bars(40),
    )
    assert sched._compute_indicator_sync("TEST", "rsi", 14, "weekly") is None


def test_dsl_indicator_node_accepts_timeframe():
    from pydantic import TypeAdapter
    from backend.workflows.dsl.schema import Tree

    node = TypeAdapter(Tree).validate_python({
        "type": "indicator", "indicator": "rsi", "symbol": "GRASIM",
        "period": 14, "timeframe": "weekly",
    })
    assert node.timeframe == "weekly"
    # Default stays daily for old persisted trees.
    node2 = TypeAdapter(Tree).validate_python({
        "type": "indicator", "indicator": "rsi", "symbol": "GRASIM",
        "period": 14,
    })
    assert node2.timeframe == "daily"


def test_evaluator_weekly_unknown_on_non_supporting_accessor():
    """An accessor without the timeframe kwarg must yield UNKNOWN for a
    weekly leaf — never a silently-daily value."""
    from pydantic import TypeAdapter
    from backend.workflows.dsl.evaluator import Ternary, evaluate
    from backend.workflows.dsl.schema import Tree

    class _DailyOnlyAccessor:
        def get_price(self, **kw):
            return 100.0

        def get_indicator(self, *, symbol, indicator, period,
                          exchange="NSE", component=None, offset=0):
            return 42.0  # would be the (wrong) daily value

        def get_volume(self, **kw):
            return 1.0

        def get_position_field(self, *, field, basis=None):
            return None

        def get_session_day(self):
            return None

    tree = TypeAdapter(Tree).validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "indicator", "indicator": "rsi",
                 "symbol": "GRASIM", "period": 14, "timeframe": "weekly"},
        "right": {"type": "constant", "value": 30},
    })
    result = evaluate(tree, accessor=_DailyOnlyAccessor(), prev_state={})
    assert result.value is Ternary.UNKNOWN


def test_skeleton_weekly_rsi_yields_honored_timeframe():
    from backend.services.workflow_skeleton import try_workflow_skeleton
    from backend.workflows.propose import validate_draft_against_registry

    sk = try_workflow_skeleton(
        "buy 10 GRASIM when its weekly RSI drops below 30",
    )
    assert sk is not None
    cfg = sk["steps"][0]["config"]
    assert cfg["timeframe"] == "weekly"
    assert "weekly" in sk["steps"][0]["label"].lower()
    # Survives registry validation (TriggerIndicatorConfig declares it).
    out = validate_draft_against_registry(sk).model_dump()
    assert out["steps"][0]["config"]["timeframe"] == "weekly"


def test_skeleton_monthly_indicator_still_bails():
    from backend.services.workflow_skeleton import try_workflow_skeleton
    assert try_workflow_skeleton(
        "buy 10 GRASIM when its monthly RSI drops below 30",
    ) is None


def test_watcher_indicator_trigger_passes_timeframe(monkeypatch):
    """_evaluate_indicator_trigger must forward cfg['timeframe'] to the
    compute — the card field is real, not decorative."""
    import backend.workflows.scheduler as sched

    seen = {}

    def _fake_compute(sym, indicator, period, timeframe="daily"):
        seen["args"] = (sym, indicator, period, timeframe)
        return None  # stop the evaluation right after the compute

    monkeypatch.setattr(sched, "_compute_indicator_sync", _fake_compute)
    asyncio.run(sched._evaluate_indicator_trigger(
        "wf-x", 0,
        {"symbol": "grasim", "indicator": "RSI", "period": 14,
         "operator": "<", "value": 30, "timeframe": "weekly"},
        __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc),
    ))
    assert seen["args"] == ("GRASIM", "rsi", 14, "weekly")


# ── #2 Addressable multi-draft store ─────────────────────────────────


def _store():
    from backend.services.conversation_store import ConversationStore
    return ConversationStore()


def _wf_draft(symbol, qty):
    return {
        "name": f"{symbol} agent",
        "steps": [
            {"step_type": "trigger.indicator",
             "config": {"symbol": symbol, "indicator": "rsi", "period": 14,
                        "operator": "<", "value": 30}},
            {"step_type": "action.place_order",
             "config": {"symbol": symbol, "side": "buy", "quantity": qty,
                        "order_type": "market"}},
        ],
    }


def test_multi_draft_store_parks_per_symbol():
    from backend.services.conversation_store import ActiveDraft

    store = _store()
    conv = f"t_{uuid.uuid4()}"
    store.set_active_draft(conv, ActiveDraft(
        tool_name="propose_workflow", draft=_wf_draft("INFY", 5),
        symbol="INFY",
    ))
    store.set_active_draft(conv, ActiveDraft(
        tool_name="propose_workflow", draft=_wf_draft("WIPRO", 5),
        symbol="WIPRO",
    ))
    # Most-recent slot = WIPRO; INFY stays parked and addressable.
    assert store.get_active_draft(conv).symbol == "WIPRO"
    infy = store.get_active_draft(conv, symbol="INFY")
    assert infy is not None and infy.symbol == "INFY"
    assert [d.symbol for d in store.list_active_drafts(conv)] == [
        "INFY", "WIPRO",
    ]
    # Amend INFY (re-stash) — WIPRO untouched.
    store.set_active_draft(conv, ActiveDraft(
        tool_name="propose_workflow", draft=_wf_draft("INFY", 8),
        symbol="INFY",
    ))
    infy2 = store.get_active_draft(conv, symbol="INFY")
    assert infy2.draft["steps"][1]["config"]["quantity"] == 8
    wipro = store.get_active_draft(conv, symbol="WIPRO")
    assert wipro.draft["steps"][1]["config"]["quantity"] == 5
    store.clear_active_draft(conv)
    assert store.get_active_draft(conv) is None
    assert store.list_active_drafts(conv) == []


def test_multi_draft_store_lru_caps_at_four():
    from backend.services.conversation_store import ActiveDraft

    store = _store()
    conv = f"t_{uuid.uuid4()}"
    evicted = []
    for i, sym in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
        out = store.set_active_draft(conv, ActiveDraft(
            tool_name="propose_workflow", draft=_wf_draft(sym, i + 1),
            symbol=sym,
        ))
        if out:
            evicted.append(out)
    assert evicted == ["AAA"]  # oldest dropped, honestly reported
    assert [d.symbol for d in store.list_active_drafts(conv)] == [
        "BBB", "CCC", "DDD", "EEE",
    ]
    store.clear_active_draft(conv)


def test_named_clear_repoints_slot():
    from backend.services.conversation_store import ActiveDraft

    store = _store()
    conv = f"t_{uuid.uuid4()}"
    store.set_active_draft(conv, ActiveDraft(
        tool_name="propose_workflow", draft=_wf_draft("INFY", 5),
        symbol="INFY",
    ))
    store.set_active_draft(conv, ActiveDraft(
        tool_name="propose_workflow", draft=_wf_draft("WIPRO", 5),
        symbol="WIPRO",
    ))
    store.clear_active_draft(conv, symbol="WIPRO")
    # Slot repointed to the remaining draft.
    assert store.get_active_draft(conv).symbol == "INFY"
    assert store.get_active_draft(conv, symbol="WIPRO") is None
    store.clear_active_draft(conv)


def test_draft_primary_symbol_helper():
    from backend.services.chat_service import _draft_primary_symbol
    assert _draft_primary_symbol(_wf_draft("INFY", 5)) == "INFY"
    assert _draft_primary_symbol({"underlying": "nifty"}) == "NIFTY"
    assert _draft_primary_symbol({}) == ""


# ── #5 Staged scale-out exits ────────────────────────────────────────


def test_staged_exit_parse_and_draft():
    from backend.services.chat_service import (
        _build_staged_exit_draft,
        _parse_staged_exit,
    )
    from backend.workflows.propose import validate_draft_against_registry

    msg = ("Buy 10 INFY at open. Sell 5 when up 3%, 5 more at 6%, "
           "and all out if it drops 2%.")
    p = _parse_staged_exit(msg)
    assert p == {
        "symbol": "INFY", "entry_qty": 10,
        "targets": [(5, 3.0), (5, 6.0)], "stop_pct": 2.0,
    }
    draft = _build_staged_exit_draft(p)
    types = [s["step_type"] for s in draft["steps"]]
    assert types.count("trigger.exit_compound") == 3
    assert types.count("action.place_order") == 4  # entry buy + 3 sells
    # Every exit branch is one-shot and symbol-scoped.
    for s in draft["steps"]:
        if s["step_type"] == "trigger.exit_compound":
            assert s["config"]["one_shot"] is True
            assert s["config"]["target_symbol"] == "INFY"
    # Stop branch uses the LOW basis, targets use HIGH.
    exit_cfgs = [s["config"] for s in draft["steps"]
                 if s["step_type"] == "trigger.exit_compound"]
    assert exit_cfgs[0]["entry"]["left"]["basis"] == "high"
    assert exit_cfgs[-1]["entry"]["left"]["basis"] == "low"
    assert exit_cfgs[-1]["entry"]["right"]["value"] == pytest.approx(-0.02)
    # Validates against the real step registry.
    validate_draft_against_registry(draft)


def test_staged_exit_no_entry_returns_offer_shape():
    from backend.services.chat_service import (
        _build_staged_exit_draft,
        _parse_staged_exit,
    )
    # Holding case — no parseable entry → parse keeps symbol empty,
    # builder declines (honest offer path in the guard).
    msg = ("I hold some shares. Sell 5 when up 3%, 5 more at 6%, "
           "all out if it drops 2%.")
    p = _parse_staged_exit(msg)
    assert p is not None and p["symbol"] == ""
    assert _build_staged_exit_draft(p) is None


def test_staged_exit_gate_rejects_plain_orders():
    from backend.services.chat_service import _parse_staged_exit
    assert _parse_staged_exit("buy 10 INFY at market") is None
    assert _parse_staged_exit(
        "sell 5 INFY when it is up 3%",
    ) is None  # single tranche, no stop → not a staged shape


def test_exit_compound_one_shot_latch_short_circuits():
    """A latched one-shot branch must return before any DB / position
    work — the guard is the very first check."""
    import backend.workflows.scheduler as sched
    from datetime import datetime, timezone

    called = {"resolve": 0}

    def _boom(*a, **k):
        called["resolve"] += 1
        raise AssertionError("position lookup must not run when latched")

    orig = sched._resolve_open_position
    sched._resolve_open_position = _boom
    try:
        asyncio.run(sched._evaluate_exit_compound_trigger(
            "wf-y", 2,
            {
                "entry": {"type": "comparison", "op": ">=",
                          "left": {"type": "position",
                                   "field": "unrealised_pct"},
                          "right": {"type": "constant", "value": 0.03}},
                "one_shot": True,
                sched._EXIT_FIRED_KEY: "2026-06-10T00:00:00+00:00",
            },
            datetime.now(timezone.utc),
        ))
    finally:
        sched._resolve_open_position = orig
    assert called["resolve"] == 0


# ── #3 roll_option_position ──────────────────────────────────────────


def _stub_chain(expiry, strikes, mids, *, atm, lot_size=75, forward=24000.0):
    rows = []
    for k in strikes:
        q = {
            "mid": mids.get(k, 100.0), "iv": 0.14, "delta": 0.4,
            "iv_status": "ok", "tradingsymbol": f"X{int(k)}",
            "instrument_token": int(k),
        }
        pe_q = {**q, "delta": -0.4}
        rows.append({"strike": float(k), "ce": dict(q), "pe": pe_q})
    return {
        "underlying": "NIFTY", "segment": "MOCK", "exchange": "NFO",
        "spot": forward, "forward": forward, "t_years": 0.05,
        "atm_strike": float(atm), "lot_size": lot_size,
        "expiry": expiry,
        "expiries": [
            {"expiry": "2026-06-11", "kind": "weekly"},
            {"expiry": "2026-06-18", "kind": "weekly"},
        ],
        "rows": rows,
        "expected_move": {"abs": 300.0},
        "research_only": False,
    }


def test_roll_option_position_prices_close_and_open(monkeypatch, db):
    import backend.market.option_chain as oc
    from backend.services.option_strategies import roll_option_position

    strikes = [23800, 23900, 24000, 24100, 24200, 24300]
    near = _stub_chain(
        "2026-06-11", strikes,
        {24000: 180.0}, atm=24000,
    )
    far = _stub_chain(
        "2026-06-18", strikes,
        {24100: 150.0, 24200: 120.0}, atm=24000,
    )

    def _fake_get_chain(db_, underlying, expiry=None, width=8):
        return far if expiry == "2026-06-18" else near

    monkeypatch.setattr(oc, "get_chain", _fake_get_chain)

    payload = roll_option_position(
        db, "NIFTY", strike=24000, option_type="CE", side="SELL",
        to_expiry="next",
    )
    roll = payload["roll"]
    assert roll["from_expiry"] == "2026-06-11"
    assert roll["to_expiry"] == "2026-06-18"
    assert roll["closes"] == {
        "strike": 24000.0, "option_type": "CE", "side": "BUY", "mid": 180.0,
    }
    # Default new strike = nearest liquid strike above ATM → 24100.
    assert roll["opens"]["strike"] == 24100.0
    assert roll["opens"]["side"] == "SELL"
    # Net = (open 150 − close 180) × 75 = −2250 (a debit roll).
    assert roll["net_premium"] == pytest.approx((150.0 - 180.0) * 75)
    assert roll["net_kind"] == "debit"
    # 2-leg card: close + open, each stamped with its expiry.
    legs = payload["editable"]["legs"]
    assert [l["action"] for l in legs] == ["close", "open"]
    assert legs[0]["expiry"] == "2026-06-11"
    assert legs[1]["expiry"] == "2026-06-18"
    # Go-forward econ from the engine (short call → max_loss unbounded).
    assert payload["computed"]["max_loss"] is None
    assert payload["computed"]["breakevens"]
    assert "register" in roll["note"].lower() or "confirm" in roll["note"].lower()


def test_roll_option_position_strike_offset(monkeypatch, db):
    import backend.market.option_chain as oc
    from backend.services.option_strategies import roll_option_position

    strikes = [23800, 23900, 24000, 24100, 24200, 24300]
    near = _stub_chain("2026-06-11", strikes, {24000: 180.0}, atm=24000)
    far = _stub_chain("2026-06-18", strikes, {24200: 120.0}, atm=24000)
    monkeypatch.setattr(
        oc, "get_chain",
        lambda db_, u, expiry=None, width=8: far if expiry == "2026-06-18" else near,
    )
    payload = roll_option_position(
        db, "NIFTY", strike=24000, option_type="CE", side="SELL",
        to_expiry="next", strike_offset=2,
    )
    assert payload["roll"]["opens"]["strike"] == 24200.0


def test_roll_option_position_honest_on_unquotable(monkeypatch, db):
    import backend.market.option_chain as oc
    from backend.services.option_strategies import (
        StrategyResolutionError, roll_option_position,
    )

    near = _stub_chain("2026-06-11", [24000], {24000: 180.0}, atm=24000)
    near["rows"][0]["ce"]["iv_status"] = "no_quote"
    near["rows"][0]["ce"]["mid"] = 0
    monkeypatch.setattr(oc, "get_chain",
                        lambda db_, u, expiry=None, width=8: near)
    with pytest.raises(StrategyResolutionError):
        roll_option_position(
            db, "NIFTY", strike=24000, option_type="CE", side="SELL",
        )


def test_roll_tool_registered():
    from backend.services.tool_registry import _REAL_TOOLS, get_tool_schema
    names = {t["function"]["name"] for t in get_tool_schema()}
    assert "roll_option_position" in _REAL_TOOLS
    assert "roll_option_position" in names
    assert "register_workflow" in names
    assert "get_workflow_status" in names


def test_router_surfaces_roll_and_status_tools():
    from backend.services.tool_router import select_tool_names

    sel = select_tool_names(
        "I sold the NIFTY 24000 call and it's against me — "
        "roll it to next expiry",
    )
    assert "roll_option_position" in sel

    sel2 = select_tool_names("is my agent actually live? when do you check?")
    assert "get_workflow_status" in sel2
    assert "register_workflow" in sel2


# ── #1 register_workflow / get_workflow_status ───────────────────────


@pytest.fixture
def _user(db):
    from backend.models import User
    u = User(
        email=f"trackc-{uuid.uuid4()}@test.dev",
        hashed_password="x" * 32,
    )
    db.add(u)
    db.flush()
    return u


def test_register_workflow_arms_and_status_reads_back(db, _user, monkeypatch):
    from backend.agents.tool_executor import execute_tool
    from backend.models import Workflow, WorkflowStatus
    import backend.workflows.scheduler as sched

    draft = _wf_draft("NESTLEIND", 10)
    out = asyncio.run(execute_tool(
        "register_workflow",
        {"name": draft["name"], "description": "test agent",
         "steps": draft["steps"]},
        "mock_token", db, _user.id,
    ))
    assert out["success"], out.get("error")
    data = out["data"]
    assert data["status"] == "active"
    assert data["registered"] is True
    wf_id = data["workflow_id"]
    wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
    assert wf is not None and wf.status == WorkflowStatus.active
    # Trigger summary names the REAL cadence + register-not-execute.
    assert any("every 60s" in t["summary"] for t in data["triggers"])
    assert "REGISTERS" in data["on_fire"]

    # Status readback — grounded, with a (stubbed) live indicator value.
    monkeypatch.setattr(
        sched, "_compute_indicator_sync",
        lambda sym, ind, period, timeframe="daily": 47.2,
    )
    st = asyncio.run(execute_tool(
        "get_workflow_status", {"workflow_id": wf_id},
        "mock_token", db, _user.id,
    ))
    assert st["success"]
    sd = st["data"]
    assert sd["armed"] is True and sd["armed_line"] == "Live."
    trig = sd["triggers"][0]
    assert trig["current_value"] == pytest.approx(47.2)
    assert trig["condition_met_now"] is False  # 47.2 < 30 is False
    assert "every 60s" in trig["summary"]
    assert "register-not-execute" in sd["on_fire"]


def test_register_workflow_rejects_garbage_draft(db, _user):
    from backend.agents.tool_executor import execute_tool

    out = asyncio.run(execute_tool(
        "register_workflow",
        {"name": "bad", "steps": [
            {"step_type": "action.place_order",
             "config": {"symbol": "X", "side": "buy", "quantity": 1,
                        "order_type": "market"}},
        ]},
        "mock_token", db, _user.id,
    ))
    # step 0 must be a trigger → honest validation failure, nothing armed.
    assert out["success"] is False
    assert "validation" in (out["error"] or "").lower() or "trigger" in (
        out["error"] or "").lower()


def test_get_workflow_status_no_workflows_is_honest(db, _user):
    from backend.agents.tool_executor import execute_tool

    st = asyncio.run(execute_tool(
        "get_workflow_status", {}, "mock_token", db, _user.id,
    ))
    assert st["success"]
    assert st["data"]["armed"] is False
    assert "not" in st["data"]["note"].lower() or "no workflow" in (
        st["data"]["note"].lower())


def test_register_guard_arms_then_status_reads_back(db, _user, monkeypatch):
    """End-to-end: stash a workflow draft, say 'register it' → the
    guard ARMS it (no LLM hop); 'is it actually live?' → grounded
    readback through the status guard."""
    import time as _time

    from backend.services.chat_service import ChatService, UserContext
    from backend.services.chat_trace import start_turn
    import backend.workflows.scheduler as sched

    svc = ChatService()
    conv = f"t_{uuid.uuid4()}"
    ctx = UserContext(user_id=_user.id, kite_token="mock_token", db=db)
    draft = _wf_draft("NESTLEIND", 10)
    svc._stash_workflow_draft(conv, draft, "draft on screen")

    trace = start_turn(conv, "register it")
    turn = asyncio.run(svc._try_register_active_draft(
        message="register it", conv_id=conv, ctx=ctx, trace=trace,
        turn_started=_time.monotonic(), breakdown={},
    ))
    assert turn is not None
    assert turn.tools_called == ["register_workflow"]
    assert "ARMED" in turn.response
    assert "not financial advice" in turn.response
    wf_id = turn.raw_data["register_workflow"]["workflow_id"]
    # Draft consumed; registered id recorded for the status guard.
    assert svc.store.get_active_draft(conv, symbol="NESTLEIND") is None
    assert svc.store.get_registered_workflow_id(conv) == wf_id

    monkeypatch.setattr(
        sched, "_compute_indicator_sync",
        lambda sym, ind, period, timeframe="daily": 47.2,
    )
    trace2 = start_turn(conv, "is it actually live? when do you check?")
    status = asyncio.run(svc._try_workflow_status(
        message="is it actually live? when do you check?",
        conv_id=conv, ctx=ctx, trace=trace2,
        turn_started=_time.monotonic(), breakdown={},
    ))
    assert status is not None
    assert status.tools_called == ["get_workflow_status"]
    assert "Live." in status.response
    assert "every 60s" in status.response
    assert "47.2" in status.response
    assert "register-not-execute" in status.response
    svc.store.clear_active_draft(conv)


def test_register_guard_ignores_non_workflow_drafts(db, _user):
    import time as _time

    from backend.services.chat_service import ChatService, UserContext
    from backend.services.chat_trace import start_turn

    svc = ChatService()
    conv = f"t_{uuid.uuid4()}"
    ctx = UserContext(user_id=_user.id, kite_token="mock_token", db=db)
    svc._stash_workflow_draft(
        conv, {"underlying": "NIFTY", "template": "covered_call"},
        "option card", tool_name="build_option_strategy",
    )
    trace = start_turn(conv, "register it")
    turn = asyncio.run(svc._try_register_active_draft(
        message="register it", conv_id=conv, ctx=ctx, trace=trace,
        turn_started=_time.monotonic(), breakdown={},
    ))
    # Option cards register through the card endpoint, not this guard.
    assert turn is None
    svc.store.clear_active_draft(conv)


def test_select_active_draft_named_backref(db):
    import time as _time  # noqa: F401

    from backend.services.chat_service import ChatService
    from backend.services.chat_trace import start_turn

    svc = ChatService()
    conv = f"t_{uuid.uuid4()}"
    svc._stash_workflow_draft(conv, _wf_draft("INFY", 5), "infy")
    svc._stash_workflow_draft(conv, _wf_draft("WIPRO", 5), "wipro")
    # Most recent is WIPRO; a named INFY back-reference promotes INFY.
    trace = start_turn(conv, "change the INFY one to 8 shares")
    active = svc._select_active_draft(
        conv, "change the INFY one to 8 shares", trace,
    )
    assert active is not None and active.symbol == "INFY"
    assert svc.store.get_active_draft(conv).symbol == "INFY"
    # WIPRO stays parked, and the amendment hint names it as untouched.
    clause = svc._parked_draft_clause(conv, active)
    assert "WIPRO" in clause and "UNTOUCHED" in clause
    svc.store.clear_active_draft(conv)


def test_register_intent_regexes():
    from backend.services.chat_service import (
        _REGISTER_DRAFT_RE, _WF_STATUS_RE,
    )
    for msg in ["register it", "go ahead", "ok, activate it",
                "arm it", "make it live", "save & activate",
                "yes, register it", "go ahead and register it"]:
        assert _REGISTER_DRAFT_RE.match(msg.strip()), msg
    for msg in ["register a complaint with SEBI",
                "what does register mean",
                "buy 5 INFY and register the order with my broker"]:
        assert not _REGISTER_DRAFT_RE.match(msg.strip()), msg
    for msg in ["is it actually live?", "when do you check?",
                "how often is it evaluated?",
                "what's the status of my agent?",
                "is the workflow running"]:
        assert _WF_STATUS_RE.search(msg), msg
    assert not _WF_STATUS_RE.search("show me the NIFTY option chain")
