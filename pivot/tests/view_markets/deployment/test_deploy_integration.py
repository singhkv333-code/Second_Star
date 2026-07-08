"""Phase-4 deployment END-TO-END integration: ``suggest → backtest → compare → deploy``.

Unlike the focused ``test_backtest`` / ``test_compare`` / ``test_deploy`` unit tests
(which feed hand-built expressions to one seam at a time), this drives the WHOLE
Phase-4 pipeline from a curated ``MarketView`` through the REAL Phase-3
``dispatch.suggest_expressions`` builder and the REAL deployment-package seams,
mocking only the market data + backtest engines + the schedule-arming side effect
(no Kite, no broker, no network, no Azure). It pins the cross-module contract:

  * a curated view → ``suggest_expressions`` builds a real 3-tier ladder, then
    ``backtest_expression`` ROUTES each tier to its real engine and ATTACHES the
    Trust verdict + the Phase-2 Alignment dial + ``backtest_run_id`` onto the row;
  * ``compare_tiers`` backtests all three under ONE shared ``trial_group`` (the
    best-of-three DSR selection-bias guard) and ranks/recommends honestly;
  * ``deploy_expression`` turns the chosen expression into an ARMED workflow draft
    whose order/option steps are ALL ``requires_approval=True``, links a real
    ``workflow_id`` back onto the row, and PLACES NO ORDER (register-not-execute);
  * a COMMODITY view whose MCX price history is unavailable (the direct-MCX
    bullion CM4 pair, ``backtest_available=False``) degrades to an honest
    ``insufficient_data`` block — every metric ``None`` (no fabricated curve), the
    leverage note carried, and the backtest engine NEVER called for it;
  * trial-deflation is genuinely applied (``record_and_deflate`` is invoked per
    tier under the shared group — the battery is run, not stubbed).
"""
from __future__ import annotations

from typing import Any, Callable

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    ExpressionKind,
    MarketView,
    ViewConfidence,
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from backend.services.backtest.validation.trials import reset_group
from backend.view_markets.deployment import (
    backtest_expression,
    compare_tiers,
    deploy_expression,
)
from backend.view_markets.deployment.backtest import (
    TRUST_BLOCK_KEYS,
    TRUST_METRICS_KEYS,
    VERDICT_RANK,
)
from backend.view_markets import implied_move as _im
from backend.view_markets.expressions.builders import option_builder, pair_builder

_VALID_VERDICTS = set(VERDICT_RANK)
_ORDER_STEP_TYPES = {
    "action.allocate_basket",
    "action.place_order",
    "action.place_option_strategy",
}


# ── engine fakes (no network) ────────────────────────────────────────────────


def _fake_cointegration(a: str, b: str, **_kw: Any) -> dict[str, Any]:
    """BUILD-time pairs payload (only the keys ``pair_builder`` reads). Distinct
    from the DEPLOY-time battery payload below — the builder reads cointegration,
    the deployment engine reads ``metrics``."""
    return {
        "cointegration": {
            "alpha": 0.12, "beta": 0.85, "adf_tstat": -3.9,
            "half_life_days": 9.0, "cointegrated_at": "1%",
        },
        "metrics": {}, "series": {},
    }


def _battery_metrics() -> dict[str, Any]:
    """A realistic engine ``metrics`` block — the battery already ran inside the
    engine. With ``psr=0.91`` (< the 0.95 'strong' bar but ≥ the 0.60 'edge' bar)
    and a positive return the real ``trust_verdict`` deterministically returns
    ``unproven`` even after deflation (psr is untouched by ``record_and_deflate``),
    so the recommendation is stable across runs."""
    return {
        "total_return_pct": 12.5,
        "max_drawdown_pct": -8.0,
        "n_trades": 7,
        "benchmark_return_pct": None,
        "forward_stats": {
            "observed_sharpe": 1.2, "skew": 0.1, "kurtosis": 3.2,
            "n_obs": 300, "num_trials": 1, "psr": 0.91,
            "min_trl": 120.0, "deflated_sharpe": 0.85,
        },
        "monte_carlo": {"dd_p95_severity_pct": 15.0, "prob_loss": 0.2},
        "sub_periods": {"concentration": 0.3},
        "trust_verdict": {
            "verdict": "unproven", "label": "Unproven", "confidence": 70,
            "rationale": "Edge possible, not established.", "flags": [],
        },
    }


def _fake_pairs_battery(a: str, b: str, **_kw: Any) -> dict[str, Any]:
    """DEPLOY-time pairs-engine payload the deployment backtest normalizes."""
    return {"pair": {"a": a, "b": b}, "metrics": _battery_metrics()}


# ── view factories ───────────────────────────────────────────────────────────


def _fake_resolve_strategy(
    db: Any, underlying: str, template_name: str, *,
    expiry: Any = None, qty_lots: int = 1,
    explicit_legs: Any = None, chain: Any = None,
) -> dict[str, Any]:
    """A deterministic DEFINED-RISK option payload (bounded max loss)."""
    sides = ("BUY", "SELL", "SELL", "BUY") if explicit_legs else ("BUY", "SELL")
    legs = [
        {
            "option_type": "CE", "side": s, "strike": 50000.0 + 100 * i,
            "mid": 120.0, "iv": 0.16, "delta": 0.4, "iv_status": "ok",
            "tradingsymbol": f"{underlying}OPT{i}", "instrument_token": 1000 + i,
        }
        for i, s in enumerate(sides)
    ]
    return {
        "locked": {
            "underlying": underlying, "segment": "NFO-OPT", "exchange": "NFO",
            "lot_size": 25, "expiry": "2026-07-30",
        },
        "editable": {"template": template_name, "qty_lots": qty_lots, "legs": legs},
        "computed": {
            "net_premium": -2400.0, "max_loss": 5000.0, "max_profit": 8000.0,
            "pop": 0.55, "breakevens": [50250.0],
            "net_greeks": {"delta": 10.0, "gamma": 0.1, "theta": -50.0, "vega": 30.0},
            "capital_required": 2400.0, "margin_estimate": 2400.0,
        },
        "critique": {"verdict": "ok", "flags": [], "summary": "fine"},
        "validation": {"liquidity_flags": []},
    }


def _fake_implied_move(
    db: Any, underlying: str, *, expiry: Any = None,
    horizon_days: Any = None, width: int = 10,
) -> _im.ImpliedMove:
    return _im.ImpliedMove(
        underlying=underlying, expiry=expiry, forward=50000.0, atm_strike=50000.0,
        atm_iv=0.16, t_years=0.08, expected_move_abs=1200.0, expected_move_pct=2.4,
        low=48800.0, high=51200.0, straddle_price=1400.0, source="iv", asof=None,
    )


def _relative_view(make: Callable[..., MarketView]) -> MarketView:
    return make(
        view_type="relative",
        title="IT outperforms the Nifty over 6 months",
        thesis="USD strength + AI-services demand → IT beats the broad index.",
        category="relative_value",
        time_horizon="6m",
    )


def _gold_silver_view(make: Callable[..., MarketView]) -> MarketView:
    return make(
        view_type="relative",
        title="Gold outperforms silver as the gold/silver ratio mean-reverts",
        thesis=(
            "The gold/silver ratio is stretched; gold's bullion bid beats silver's "
            "industrial leg — a leveraged MCX long-gold/short-silver ratio."
        ),
        category="commodities",
        time_horizon="6m",
    )


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def patch_build_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake ONLY the BUILD-time pairs cointegration (the real ``honest_short`` rule
    still runs); the DEPLOY-time engine is patched per-test."""
    monkeypatch.setattr(pair_builder, "run_pairs_backtest", _fake_cointegration)


def _trip_wire_order_placement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trip-wire every order-placement seam: if deploy ever tries to actually
    execute, the test fails. register-not-execute arms a draft, it never places."""
    import backend.routers.orders as orders_router

    for name in dir(orders_router):
        if name.startswith(("place", "register")):
            obj = getattr(orders_router, name, None)
            if callable(obj):
                monkeypatch.setattr(
                    orders_router, name,
                    lambda *a, **k: pytest.fail("deploy must never place an order"),
                    raising=False,
                )


def _assert_block_shape(block: dict) -> None:
    assert set(block) == set(TRUST_BLOCK_KEYS)
    assert set(block["metrics"]) == set(TRUST_METRICS_KEYS)


# ════════════════════════════════════════════════════════════════════════════
# 1) Equity RELATIVE view — full pipeline, register-not-execute end-to-end
# ════════════════════════════════════════════════════════════════════════════


def test_relative_view_full_pipeline_arms_register_not_execute(
    db: Session,
    make_curated_view: Callable[..., MarketView],
    patch_build_pairs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DEPLOY-time pairs engine returns the battery; the portfolio/workflow engines
    # must never be touched for an all-pair relative view.
    monkeypatch.setattr(
        "backend.services.backtest.pairs.engine.run_pairs_backtest",
        _fake_pairs_battery,
    )
    monkeypatch.setattr(
        "backend.services.backtest.portfolio.engine.run_portfolio_backtest",
        lambda *a, **k: pytest.fail("portfolio engine wrongly called for a pair view"),
    )

    # Spy on the deflation primitive (still delegates to the real one) to prove the
    # battery's selection-bias guard is genuinely applied per tier, not stubbed.
    import backend.services.backtest.validation.trials as trials_mod

    real_deflate = trials_mod.record_and_deflate
    deflate_groups: list[str] = []

    def _spy_deflate(fs, group, fingerprint, **kw):  # type: ignore[no-untyped-def]
        deflate_groups.append(group)
        return real_deflate(fs, group, fingerprint, **kw)

    monkeypatch.setattr(trials_mod, "record_and_deflate", _spy_deflate)

    view = _relative_view(make_curated_view)

    # ── suggest: a real 3-tier ladder of pairs ──────────────────────────────
    rows = __import__(
        "backend.view_markets.expressions.dispatch", fromlist=["suggest_expressions"]
    ).suggest_expressions(db, view)
    assert len(rows) == 3
    assert {r.expression_kind for r in rows} == {ExpressionKind.pair}

    # ── backtest each tier: route to the pairs engine, attach Trust + Alignment ─
    group = "it-relative-e2e"
    reset_group(group)
    for row in rows:
        block = backtest_expression(db, row, trial_group=group)
        _assert_block_shape(block)
        assert block["engine"] == "pairs"
        assert block["degraded"] is False
        # Trust verdict attached + a real value (not None).
        assert block["verdict"] in _VALID_VERDICTS and block["verdict"] is not None
        assert block["verdict"] == "unproven"  # deterministic for psr=0.91
        # Phase-2 Alignment dial attached (unproven is NOT suppressed).
        assert isinstance(block["alignment"], dict)
        assert block["alignment"]["suppressed"] is False
        assert isinstance(block["alignment"]["score"], int)
        # Persisted onto the row (run id + config.scores.trust + confidence dial).
        assert row.backtest_run_id == block["backtest_run_id"]
        assert row.config["scores"]["trust"] == block
    # the deflation primitive ran once per tier, all under the SAME shared group.
    assert deflate_groups == [group, group, group]
    reset_group(group)

    # the expression confidence dimension was written (gated by the verdict).
    conf = (
        db.query(ViewConfidence)
        .filter(ViewConfidence.view_id == view.id)
        .all()
    )
    assert conf, "expected an expression confidence dial persisted"

    # ── compare: rank all three under ONE shared trial group, recommend honestly ─
    cmp = compare_tiers(db, view)
    assert cmp["view_id"] == view.id
    assert {t["tier"] for t in cmp["tiers"]} == {
        "conservative", "balanced", "aggressive",
    }
    assert set(cmp["ranking"]) == {"conservative", "balanced", "aggressive"}
    # every tier is unproven (≥ the recommend floor) → a tier IS recommended.
    assert cmp["recommended_tier"] in {"conservative", "balanced", "aggressive"}
    assert cmp["recommendation_rationale"]

    # ── deploy the recommended tier: an ARMED draft, no order placed ─────────
    _trip_wire_order_placement(monkeypatch)
    recommended_row = next(
        r for r in rows
        if str(r.tier.value) == cmp["recommended_tier"]
    )
    out = deploy_expression(db, recommended_row, user_id=4242)

    assert out["register_not_execute"] is True
    assert out["status"] == "draft"
    assert out["activated"] is False
    assert out["requires_approval"] is True

    # a pair deploys as long + honest short — both approval-gated order steps.
    orders = [s for s in out["steps"] if s["step_type"] == "action.place_order"]
    assert len(orders) == 2
    assert {o["config"]["side"] for o in orders} == {"buy", "short"}
    order_like = [s for s in out["steps"] if s["step_type"] in _ORDER_STEP_TYPES]
    assert order_like
    assert all(s["config"].get("requires_approval") is True for s in order_like)
    # the trigger is armed first (register-not-execute: arm trigger, never execute).
    assert out["steps"][0]["step_type"].startswith("trigger.")

    # the workflow draft is persisted + linked back onto the expression row.
    assert recommended_row.workflow_id == out["workflow_id"]
    wf = db.get(Workflow, out["workflow_id"])
    assert wf is not None
    assert wf.status == WorkflowStatus.draft
    assert wf.user_id == 4242
    persisted_steps = (
        db.query(WorkflowStep)
        .filter(WorkflowStep.workflow_id == wf.id)
        .order_by(WorkflowStep.step_index)
        .all()
    )
    assert persisted_steps[0].step_type.startswith("trigger.")
    assert any(s.step_type == "action.place_order" for s in persisted_steps)


# ════════════════════════════════════════════════════════════════════════════
# 2) Commodity view — honest insufficient_data when MCX history is missing
# ════════════════════════════════════════════════════════════════════════════


def test_commodity_view_missing_history_degrades_without_fabrication(
    db: Session,
    make_curated_view: Callable[..., MarketView],
    patch_build_pairs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Trip-wire/record the DEPLOY-time pairs engine: it must run for the equity
    # CM3 producer/importer legs but NEVER for the direct-MCX GOLD/SILVER pair
    # (no aligned OHLCV → honest construct-only degrade, no fabricated spread).
    engine_calls: list[tuple[str, str]] = []

    def _recording_engine(a: str, b: str, **kw: Any) -> dict[str, Any]:
        engine_calls.append((a, b))
        return _fake_pairs_battery(a, b, **kw)

    monkeypatch.setattr(
        "backend.services.backtest.pairs.engine.run_pairs_backtest",
        _recording_engine,
    )

    view = _gold_silver_view(make_curated_view)
    rows = __import__(
        "backend.view_markets.expressions.dispatch", fromlist=["suggest_expressions"]
    ).suggest_expressions(db, view)
    by_tier = {str(r.tier.value): r for r in rows}

    # The conservative tier is the direct-MCX CM4 bullion pair (backtest_available
    # False). It degrades honestly: insufficient_data, NO engine call, no curve.
    cons = by_tier["conservative"]
    assert cons.config["archetype"] == "CM4_gold_silver_ratio_pair"
    assert cons.config["structure"]["backtest_available"] is False

    block = backtest_expression(db, cons, trial_group="commodity-e2e")
    _assert_block_shape(block)
    assert block["verdict"] == "insufficient_data"
    assert block["degraded"] is True
    assert block["engine"] == "none"
    assert block["alignment"] is None
    # NO fabricated numbers — every headline metric + sub-block is None.
    assert all(block["metrics"][k] is None for k in TRUST_METRICS_KEYS)
    # the leverage note rides the honest-degrade reason (commodity = LEVERAGED).
    assert "LEVERAGED" in (block["data_note"] or "")
    # the engine was never invoked for the GOLD/SILVER legs.
    assert ("GOLD", "SILVER") not in engine_calls

    # The aggressive tier is the equity CM3 producer/importer pair → it DOES run
    # the engine and produces a real verdict (proving routing isn't all-degrade).
    aggr = by_tier["aggressive"]
    assert aggr.config["structure"]["backtest_available"] is True
    aggr_block = backtest_expression(db, aggr, trial_group="commodity-e2e")
    assert aggr_block["engine"] == "pairs"
    assert aggr_block["verdict"] == "unproven"
    assert engine_calls  # the equity legs were backtested
    assert all(call != ("GOLD", "SILVER") for call in engine_calls)
    reset_group("commodity-e2e")

    # ── compare: the degraded commodity tiers never out-rank / get recommended ─
    cmp = compare_tiers(db, view)
    by_out = {t["tier"]: t for t in cmp["tiers"]}
    # both direct-MCX CM4 tiers degrade honestly (no engine, no curve).
    for tier in ("conservative", "balanced"):
        assert by_out[tier]["trust"]["verdict"] == "insufficient_data"
        assert by_out[tier]["engine"] == "none"
    # the proven-enough equity tier ranks first + is the only recommendation;
    # both degraded commodity tiers rank below it and are never recommended.
    assert cmp["ranking"][0] == "aggressive"
    assert set(cmp["ranking"][1:]) == {"conservative", "balanced"}
    assert cmp["recommended_tier"] == "aggressive"

    # ── deploy the commodity (CM4) expression: armed + leverage note, no order ─
    _trip_wire_order_placement(monkeypatch)
    out = deploy_expression(db, cons, user_id=7)
    assert out["register_not_execute"] is True
    assert out["status"] == "draft"
    # commodity expression carries the leverage note (folded into the armed note).
    assert out["leverage_note"]
    assert out["leverage_note"] in out["note"]
    order_like = [s for s in out["steps"] if s["step_type"] in _ORDER_STEP_TYPES]
    assert order_like
    assert all(s["config"].get("requires_approval") is True for s in order_like)
    assert cons.workflow_id == out["workflow_id"]


# ════════════════════════════════════════════════════════════════════════════
# 3) Activate path — arms the schedule, still places no order
# ════════════════════════════════════════════════════════════════════════════


def test_real_dispatch_built_option_expression_deploys_to_place_option_strategy(
    db: Session,
    make_curated_view: Callable[..., MarketView],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for the cross-module glue fix: a REAL Phase-3 option
    expression (E1 rate debit spread) must arm ``action.place_option_strategy``.
    The Phase-3 ``option_builder`` now stamps ``config.structure.underlying`` (the
    engine-locked chain symbol) so Phase-4 deploy can target it without a fragile
    instrument-role scan — previously deploy raised ``ValueError`` here."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(pair_builder, "run_pairs_backtest", _fake_cointegration)
    monkeypatch.setattr(option_builder._opt, "resolve_strategy", _fake_resolve_strategy)
    monkeypatch.setattr(option_builder._im, "implied_move", _fake_implied_move)
    monkeypatch.setattr(
        "backend.services.trading_costs.option_leg_bps", lambda side, **k: 3.0
    )

    view = make_curated_view(
        view_type="event",
        title="RBI cuts the repo rate at the next MPC",
        thesis="Dovish guidance + softening CPI → a 25bp cut at the next MPC.",
        category="rates",
        time_horizon="1m",
        resolution_date=datetime.now(timezone.utc) + timedelta(days=21),
    )
    rows = __import__(
        "backend.view_markets.expressions.dispatch", fromlist=["suggest_expressions"]
    ).suggest_expressions(db, view)
    opt_row = next(
        r for r in rows if r.expression_kind == ExpressionKind.option_strategy
    )
    # the builder now stamps the deployable underlying onto the structure.
    assert opt_row.config["structure"]["underlying"]

    _trip_wire_order_placement(monkeypatch)
    out = deploy_expression(db, opt_row, user_id=11)

    opt_steps = [
        s for s in out["steps"]
        if s["step_type"] == "action.place_option_strategy"
    ]
    assert opt_steps, "a dispatch-built option must arm action.place_option_strategy"
    cfg = opt_steps[0]["config"]
    assert cfg["underlying"] == opt_row.config["structure"]["underlying"]
    assert cfg["book"] == "live"               # register-not-execute
    assert cfg["requires_approval"] is True
    assert out["status"] == "draft"
    assert opt_row.workflow_id == out["workflow_id"]


def test_deploy_activate_arms_schedule_without_placing_an_order(
    db: Session,
    make_curated_view: Callable[..., MarketView],
    patch_build_pairs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.services.backtest.pairs.engine.run_pairs_backtest",
        _fake_pairs_battery,
    )

    view = _relative_view(make_curated_view)
    rows = __import__(
        "backend.view_markets.expressions.dispatch", fromlist=["suggest_expressions"]
    ).suggest_expressions(db, view)
    row = rows[1]  # the balanced cointegrated pair

    # mock the schedule-arming side effect so no real APScheduler/cron runs.
    armed: dict[str, Any] = {}
    import backend.workflows.scheduler as scheduler

    monkeypatch.setattr(
        scheduler, "upsert_workflow_schedule",
        lambda _db, wf: armed.__setitem__("wf_id", wf.id),
    )
    _trip_wire_order_placement(monkeypatch)

    out = deploy_expression(db, row, user_id=99, activate=True)

    assert out["activated"] is True
    assert out["status"] == "active"
    assert armed.get("wf_id") == out["workflow_id"]  # the arming path was taken
    # even when ACTIVE, every order step stays approval-gated — nothing auto-fires.
    order_like = [s for s in out["steps"] if s["step_type"] in _ORDER_STEP_TYPES]
    assert order_like
    assert all(s["config"].get("requires_approval") is True for s in order_like)
    wf = db.get(Workflow, out["workflow_id"])
    assert wf is not None and wf.status == WorkflowStatus.active
