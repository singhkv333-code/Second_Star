"""F&O P3 — DSL option nodes, option metrics, expiry-day trigger latch,
and the action.place_option_strategy executor."""
from datetime import date

import pytest
from pydantic import TypeAdapter

from backend.market.instrument_master import refresh_instrument_master


@pytest.fixture(autouse=True)
def _master_and_cache(db):
    from backend.cache import redis_client

    if hasattr(redis_client, "_store"):
        redis_client._store.clear()
        redis_client._expires_at.clear()
    elif hasattr(redis_client, "scan_iter"):
        for key in list(redis_client.scan_iter("optchain:*")):
            redis_client.delete(key)
    refresh_instrument_master(db)
    yield


# ── DSL schema + evaluator ───────────────────────────────────────────


def _tree(d):
    from backend.workflows.dsl.schema import Tree

    return TypeAdapter(Tree).validate_python(d)


def test_option_nodes_parse_in_compound_tree():
    tree = _tree({
        "type": "logic", "op": "and",
        "operands": [
            {"type": "comparison", "op": ">",
             "left": {"type": "option_metric", "underlying": "nifty",
                      "metric": "iv_atm"},
             "right": {"type": "constant", "value": 0.2}},
            {"type": "comparison", "op": ">=",
             "left": {"type": "dte", "underlying": "NIFTY"},
             "right": {"type": "constant", "value": 5}},
        ],
    })
    # underlying normalizes to uppercase at parse time.
    assert tree.operands[0].left.underlying == "NIFTY"


def test_unknown_metric_rejected_at_parse():
    with pytest.raises(Exception):
        _tree({"type": "option_metric", "underlying": "NIFTY",
               "metric": "ivp"})  # needs IV history — not offered in v1


class _StubAccessor:
    """Accessor WITHOUT option methods — the backtest shape."""

    def get_price(self, **k):  # pragma: no cover - unused
        return None


def test_accessor_without_option_methods_yields_unknown(db):
    from backend.workflows.dsl.evaluator import Ternary, evaluate

    tree = _tree({
        "type": "comparison", "op": ">",
        "left": {"type": "option_metric", "underlying": "NIFTY",
                 "metric": "iv_atm"},
        "right": {"type": "constant", "value": 0.1},
    })
    result = evaluate(tree, accessor=_StubAccessor())
    assert result.value is Ternary.UNKNOWN  # honest absence, no crash


def test_live_accessor_evaluates_option_tree(db, monkeypatch):
    """LiveDataAccessor opens its own SessionLocal — point it at the
    test session factory so it sees the fixture's instrument master."""
    import backend.workflows.dsl.data_accessor as da
    from backend.workflows.dsl.evaluator import Ternary, evaluate

    accessor = da.LiveDataAccessor()
    monkeypatch.setattr(
        accessor, "_with_db", lambda fn: fn(db),
    )
    tree = _tree({
        "type": "logic", "op": "and",
        "operands": [
            {"type": "comparison", "op": ">",
             "left": {"type": "option_metric", "underlying": "NIFTY",
                      "metric": "iv_atm"},
             "right": {"type": "constant", "value": 0.05}},
            {"type": "comparison", "op": ">",
             "left": {"type": "dte", "underlying": "NIFTY"},
             "right": {"type": "constant", "value": 0}},
            {"type": "comparison", "op": "<",
             "left": {"type": "option_greek", "underlying": "NIFTY",
                      "greek": "delta", "option_type": "PE"},
             "right": {"type": "constant", "value": 0}},
        ],
    })
    result = evaluate(tree, accessor=accessor)
    assert result.value is Ternary.TRUE


# ── Option metrics ───────────────────────────────────────────────────


def test_option_metrics_against_mock_chain(db):
    from backend.market.option_metrics import (
        compute_dte,
        compute_option_greek,
        compute_option_metric,
    )

    iv = compute_option_metric(db, "NIFTY", "iv_atm")
    assert iv and 0.05 < iv < 0.5
    pcr = compute_option_metric(db, "NIFTY", "pcr_oi")
    assert pcr and pcr > 0
    mp = compute_option_metric(db, "NIFTY", "max_pain")
    assert mp and mp % 50 == 0          # a strike on the ladder
    em = compute_option_metric(db, "NIFTY", "expected_move_pct")
    assert em and 0 < em < 10
    straddle = compute_option_metric(db, "NIFTY", "straddle_price")
    assert straddle and straddle > 0
    rr = compute_option_metric(db, "NIFTY", "rr_25d")
    assert rr is not None                # mock smile has put tilt
    slope = compute_option_metric(db, "NIFTY", "term_slope")
    assert slope is not None
    dte = compute_dte(db, "NIFTY")
    assert dte and dte > 0
    delta = compute_option_greek(db, "NIFTY", "delta", option_type="CE")
    assert delta and 0.3 < delta < 0.7   # ATM call
    # Unknown things resolve to None, never crash / fabricate.
    assert compute_option_metric(db, "NIFTY", "nonsense") is None
    assert compute_option_metric(db, "NOTREAL", "iv_atm") is None


# ── Registry / catalog ───────────────────────────────────────────────


def test_new_steps_registered_in_catalog():
    from backend.workflows.registry import (
        CATALOG_VERSION,
        STEP_REGISTRY,
        get_catalog,
    )

    catalog = get_catalog()
    types = {s["step_type"] for s in catalog["step_types"]}
    assert "trigger.expiry_day" in types
    assert "action.place_option_strategy" in types
    assert STEP_REGISTRY["trigger.expiry_day"].trigger_only is True
    assert STEP_REGISTRY["action.place_option_strategy"].max_retries == 1
    # Track the constant rather than hard-pinning a date string — the
    # catalog version legitimately bumps whenever any registered step
    # type changes (e.g. the 2026-06-17 +group/+compat metadata pass).
    assert catalog["catalog_version"] == CATALOG_VERSION


# ── action.place_option_strategy executor ────────────────────────────


class _Ctx:
    """Minimal executor ctx (mirrors engine's shape)."""

    def __init__(self, db, workflow, run, step, config):
        self.db = db
        self.workflow = workflow
        self.run = run
        self.step = step
        self.config = config


@pytest.fixture()
def wf_ctx(db):
    """A workflow + run + user, enough for the action executor."""
    from backend.models import User, Workflow, WorkflowRun, WorkflowStep

    user = User(email="p3@test.com", hashed_password="h")
    db.add(user)
    db.flush()
    wf = Workflow(user_id=user.id, name="opt wf", status="active")
    db.add(wf)
    db.flush()
    run = WorkflowRun(
        workflow_id=wf.id, workflow_version=1,
        triggered_by="manual", status="running", context={},
    )
    db.add(run)
    db.flush()
    step = WorkflowStep(
        workflow_id=wf.id, step_index=1,
        step_type="action.place_option_strategy", config={},
    )
    db.add(step)
    db.flush()
    return db, wf, run, step


@pytest.mark.asyncio
async def test_action_paper_book_executes(wf_ctx, monkeypatch):
    db, wf, run, step = wf_ctx
    from backend.config import settings as _settings

    monkeypatch.setattr(_settings, "paper_trading_enabled", True, raising=False)
    from backend.workflows.steps.actions import (
        execute_action_place_option_strategy,
    )

    ctx = _Ctx(db, wf, run, step, {
        "underlying": "NIFTY", "template": "short_strangle",
        "expiry_rule": "nearest", "qty_lots": 1, "book": "paper",
        "requires_approval": False,
    })
    out = await execute_action_place_option_strategy(ctx)
    assert out["executed"] is True
    assert out["status"] == "active"
    assert len(out["fills"]) == 2
    assert out["margin_estimate"] and out["margin_estimate"] > 0

    # Engine retry (same run) is idempotent — no second strategy/fills.
    from backend.models import OptionStrategy, PaperFill

    out2 = await execute_action_place_option_strategy(ctx)
    assert out2["strategy_id"] == out["strategy_id"]
    assert db.query(OptionStrategy).count() == 1
    assert db.query(PaperFill).count() == 2


@pytest.mark.asyncio
async def test_action_live_book_registers_never_places(wf_ctx):
    db, wf, run, step = wf_ctx
    from backend.workflows.steps.actions import (
        execute_action_place_option_strategy,
    )

    ctx = _Ctx(db, wf, run, step, {
        "underlying": "NIFTY", "template": "bull_put_spread",
        "book": "live", "qty_lots": 1, "requires_approval": False,
    })
    out = await execute_action_place_option_strategy(ctx)
    assert out["executed"] is False
    assert out["status"] == "registered"      # register-not-execute
    from backend.models import PaperFill, PaperOrder

    assert db.query(PaperFill).count() == 0
    assert db.query(PaperOrder).count() == 0


@pytest.mark.asyncio
async def test_action_mcx_allowed(wf_ctx):
    # Commodities (MCX) are tradeable via register-not-execute — the workflow
    # option action no longer hard-rejects MCX; it paper-fills like any segment.
    db, wf, run, step = wf_ctx
    from backend.workflows.steps.actions import (
        execute_action_place_option_strategy,
    )

    ctx = _Ctx(db, wf, run, step, {
        "underlying": "CRUDEOIL", "template": "long_straddle",
        "book": "paper", "qty_lots": 1, "requires_approval": False,
    })
    out = await execute_action_place_option_strategy(ctx)
    assert out and out.get("book") == "paper"


@pytest.mark.asyncio
async def test_action_approval_pause(wf_ctx):
    db, wf, run, step = wf_ctx
    from backend.workflows.engine import _AwaitingApproval
    from backend.workflows.steps.actions import (
        execute_action_place_option_strategy,
    )

    ctx = _Ctx(db, wf, run, step, {
        "underlying": "NIFTY", "template": "iron_condor",
        "book": "paper", "qty_lots": 1, "requires_approval": True,
    })
    with pytest.raises(_AwaitingApproval):
        await execute_action_place_option_strategy(ctx)
    from backend.models import OptionStrategy

    assert db.query(OptionStrategy).count() == 0  # nothing before approval


# ── Expiry-day trigger semantics (latch math, no scheduler loop) ─────


def test_expiry_day_dte_window(db):
    """On the mock master, the NEAREST NIFTY expiry is days away — DTE
    > 1 → no fire. Asserting the >1 case proves the watcher's window
    check would hold its fire today; the <=1 path is covered by the
    chain-math test (year_fraction floors at expiry)."""
    from backend.market.option_metrics import compute_dte

    dte = compute_dte(db, "NIFTY", expiry_rule="nearest")
    assert dte is not None
    fired_window = dte <= 1.0
    from datetime import date as _date
    from backend.market.instrument_master import resolve_expiry

    nearest = resolve_expiry(db, "NIFTY")
    assert fired_window == (nearest == _date.today())
