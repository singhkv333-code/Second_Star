"""Phase-4 ``backtest_expression`` behaviour tests.

Unit tests with the real engines MOCKED (no network, no bars) — they assert the
load-bearing contract:

  * kind → engine ROUTING (basket/multi_asset → portfolio, pair → pairs,
    option_strategy/hedge → honest degrade),
  * the FROZEN trust-block shape is fully populated (every key present, the three
    raw battery sub-blocks carried through, the gated expression dial attached),
  * HONEST DEGRADE: a pair flagged ``backtest_available=False`` never calls the
    engine; an engine that can't serve data (the MCX-commodity case) degrades to
    ``insufficient_data`` with NO fabricated numbers + the leverage note,
  * REGISTER-NOT-EXECUTE: backtest only evaluates — it places no order and arms
    no workflow (the option/hedge route degrades without touching any engine),
  * trial-group DSR deflation is threaded for the post-hoc engines,
  * attachment: ``backtest_run_id`` + ``config.scores.trust`` + the EXPRESSION
    ``view_confidence`` dial are written on ``persist`` and skipped otherwise.
"""
from __future__ import annotations

from typing import Callable

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    ConfidenceDimension,
    ExpressionKind,
    ExpressionTier,
    MarketView,
    ViewConfidence,
    ViewExpression,
)
from backend.services.backtest.portfolio.engine import PortfolioError
from backend.services.backtest.validation.trials import reset_group
from backend.view_markets.deployment import backtest as bt
from backend.view_markets.deployment.backtest import (
    TRUST_BLOCK_KEYS,
    TRUST_METRICS_KEYS,
    backtest_expression,
)


# --------------------------------------------------------------------------- #
# Fakes / factories
# --------------------------------------------------------------------------- #
def _fake_metrics(verdict: str = "promising") -> dict:
    """A realistic engine ``metrics`` block (the battery already ran inside the
    engine). ``forward_stats`` carries the fields the expression dial reads."""
    return {
        "total_return_pct": 12.5,
        "max_drawdown_pct": -8.0,
        "n_trades": 7,
        "benchmark_return_pct": 9.0,
        "forward_stats": {
            "observed_sharpe": 1.2,
            "skew": 0.1,
            "kurtosis": 3.2,
            "n_obs": 300,
            "num_trials": 1,
            "psr": 0.91,
            "min_trl": 120.0,
            "deflated_sharpe": 0.85,
        },
        "monte_carlo": {"dd_p95_severity_pct": 15.0, "prob_loss": 0.2},
        "sub_periods": {"concentration": 0.3},
        "trust_verdict": {
            "verdict": verdict,
            "label": verdict.replace("_", " ").title(),
            "confidence": 78,
            "rationale": "Edge survives deflation + Monte-Carlo.",
            "flags": ["drawdown_risk"],
        },
    }


def _fake_pairs_result() -> dict:
    m = _fake_metrics()
    # the pairs engine reports n_trades + win_rate, no benchmark
    m["benchmark_return_pct"] = None
    return {"pair": {"a": "TCS", "b": "INFY"}, "metrics": m}


def _fake_portfolio_result() -> dict:
    m = _fake_metrics()
    # the portfolio engine reports n_rebalances, not n_trades
    del m["n_trades"]
    m["n_rebalances"] = 11
    m["benchmark_return_pct"] = None
    return {"symbols": ["TCS", "INFY", "WIPRO"], "metrics": m}


@pytest.fixture
def make_expression(
    view_db: Session, make_curated_view: Callable[..., MarketView]
) -> Callable[..., ViewExpression]:
    """Persist a ViewExpression (all five disclosures filled) for a kind/config."""

    def _make(
        *,
        kind: str,
        config: dict,
        tier: str = "balanced",
        view: MarketView | None = None,
    ) -> ViewExpression:
        v = view or make_curated_view()
        row = ViewExpression(
            view_id=v.id,
            tier=ExpressionTier(tier),
            expression_kind=ExpressionKind(kind),
            config=config,
            rationale="why it may work",
            risk_profile="defined risk",
            capital_intensity="low",
            historical_strength="moderate",
            time_horizon="3m",
        )
        view_db.add(row)
        view_db.flush()
        return row

    return _make


def _assert_block_shape(block: dict) -> None:
    assert set(block) == set(TRUST_BLOCK_KEYS)
    assert set(block["metrics"]) == set(TRUST_METRICS_KEYS)


# --------------------------------------------------------------------------- #
# Routing + attachment (happy paths)
# --------------------------------------------------------------------------- #
def test_basket_routes_to_portfolio_and_attaches_trust(
    view_db, make_expression, monkeypatch
) -> None:
    called: dict = {}

    def _fake_run(symbols, **kw):
        called["symbols"] = symbols
        called["kw"] = kw
        return _fake_portfolio_result()

    monkeypatch.setattr(
        "backend.services.backtest.portfolio.engine.run_portfolio_backtest", _fake_run
    )
    # pairs/workflow engines must NOT be touched for a basket
    monkeypatch.setattr(
        "backend.services.backtest.pairs.engine.run_pairs_backtest",
        lambda *a, **k: pytest.fail("pairs engine wrongly called for a basket"),
    )

    expr = make_expression(
        kind="basket",
        config={
            "structure": {"weights": {"TCS": 0.34, "INFY": 0.33, "WIPRO": 0.33}},
        },
    )
    block = backtest_expression(view_db, expr, period="3y")

    _assert_block_shape(block)
    assert called["symbols"] == ["TCS", "INFY", "WIPRO"]
    assert called["kw"]["period"] == "3y"
    assert block["engine"] == "portfolio"
    assert block["degraded"] is False
    assert block["verdict"] == "promising"
    # n_trades falls back to the portfolio engine's n_rebalances
    assert block["metrics"]["n_trades"] == 11
    assert block["metrics"]["forward_stats"]["deflated_sharpe"] == 0.85
    # the gated expression dial is attached (not suppressed for 'promising')
    assert block["alignment"]["suppressed"] is False
    assert isinstance(block["alignment"]["score"], int)

    # attachment onto the row
    assert expr.backtest_run_id == block["backtest_run_id"]
    assert expr.config["scores"]["trust"] == block
    conf = (
        view_db.query(ViewConfidence)
        .filter(
            ViewConfidence.view_id == expr.view_id,
            ViewConfidence.dimension == ConfidenceDimension.expression,
        )
        .one()
    )
    assert conf.score == pytest.approx(block["alignment"]["score"] / 100.0)


def test_multi_asset_routes_to_portfolio_over_equity_sleeve(
    view_db, make_expression, monkeypatch
) -> None:
    seen: dict = {}

    def _fake_run(symbols, **kw):
        seen["symbols"] = symbols
        return _fake_portfolio_result()

    monkeypatch.setattr(
        "backend.services.backtest.portfolio.engine.run_portfolio_backtest", _fake_run
    )
    expr = make_expression(
        kind="multi_asset",
        config={
            "structure": {
                "asset_class_scheme": "risk_parity",
                "sleeves": [
                    {
                        "kind": "equity_basket",
                        "weight": 0.6,
                        "detail": {"weights": {"TCS": 0.5, "INFY": 0.5}},
                    },
                    {"kind": "gold_etf", "weight": 0.4, "detail": {}},
                ],
            }
        },
    )
    block = backtest_expression(view_db, expr)
    assert block["engine"] == "portfolio"
    assert seen["symbols"] == ["TCS", "INFY"]


def test_pair_routes_to_pairs_engine(view_db, make_expression, monkeypatch) -> None:
    seen: dict = {}

    def _fake_run(a, b, **kw):
        seen["a"], seen["b"], seen["kw"] = a, b, kw
        return _fake_pairs_result()

    monkeypatch.setattr(
        "backend.services.backtest.pairs.engine.run_pairs_backtest", _fake_run
    )
    expr = make_expression(
        kind="pair",
        config={
            "structure": {
                "a": "TCS",
                "b": "INFY",
                "lookback": 90,
                "z_entry": 2.5,
                "z_exit": 0.4,
                "z_stop": 4.5,
                "backtest_available": True,
            }
        },
    )
    block = backtest_expression(view_db, expr)
    assert (seen["a"], seen["b"]) == ("TCS", "INFY")
    assert seen["kw"]["lookback"] == 90 and seen["kw"]["entry_z"] == 2.5
    assert block["engine"] == "pairs"
    assert block["metrics"]["benchmark_return_pct"] is None


# --------------------------------------------------------------------------- #
# Honest degrade
# --------------------------------------------------------------------------- #
def test_pair_backtest_unavailable_never_calls_engine(
    view_db, make_expression, monkeypatch
) -> None:
    monkeypatch.setattr(
        "backend.services.backtest.pairs.engine.run_pairs_backtest",
        lambda *a, **k: pytest.fail("engine must NOT run when backtest_available=False"),
    )
    expr = make_expression(
        kind="pair",
        config={"structure": {"a": "GOLD", "b": "SILVER", "backtest_available": False}},
    )
    block = backtest_expression(view_db, expr)
    _assert_block_shape(block)
    assert block["verdict"] == "insufficient_data"
    assert block["degraded"] is True
    assert block["engine"] == "none"
    assert block["alignment"] is None
    # NO fabricated numbers
    assert all(block["metrics"][k] is None for k in TRUST_METRICS_KEYS)


def test_commodity_missing_history_degrades_with_leverage_note(
    view_db, make_expression, monkeypatch
) -> None:
    """An MCX-commodity basket whose price history the engine can't serve degrades
    to honest ``insufficient_data`` — no fabricated curve, carries the leverage
    note."""

    def _raise(symbols, **kw):
        raise PortfolioError("too few symbols returned aligned data.")

    monkeypatch.setattr(
        "backend.services.backtest.portfolio.engine.run_portfolio_backtest", _raise
    )
    expr = make_expression(
        kind="basket",
        config={
            "structure": {"weights": {"CRUDEOIL": 0.5, "GOLD": 0.5}},
            "instruments": [
                {"symbol": "CRUDEOIL", "segment": "MCX-FUT",
                 "instrument_type": "commodity_future"},
                {"symbol": "GOLD", "segment": "MCX-FUT",
                 "instrument_type": "commodity_future"},
            ],
        },
    )
    block = backtest_expression(view_db, expr)
    _assert_block_shape(block)
    assert block["verdict"] == "insufficient_data"
    assert block["degraded"] is True
    assert block["engine"] == "none"
    assert all(block["metrics"][k] is None for k in TRUST_METRICS_KEYS)
    # LEVERAGED → the commodity leverage note rides along honestly
    assert "LEVERAGED" in block["data_note"]
    assert "register-not-execute" in block["data_note"]


def test_option_strategy_degrades_without_running_any_engine(
    view_db, make_expression, monkeypatch
) -> None:
    """register-not-execute + honest degrade: the equity sim can't price option
    legs, so option_strategy degrades to insufficient_data and touches NO engine
    (and places no order)."""
    for path in (
        "backend.services.backtest.portfolio.engine.run_portfolio_backtest",
        "backend.services.backtest.pairs.engine.run_pairs_backtest",
        "backend.services.workflow_backtester.backtest_workflow",
    ):
        monkeypatch.setattr(
            path, lambda *a, **k: pytest.fail("no engine/order may run for options")
        )
    expr = make_expression(
        kind="option_strategy",
        config={
            "structure": {
                "template": "bull_call_spread",
                "pop": 0.62,
                "max_loss": -5000.0,
                "max_profit": 7000.0,
            }
        },
    )
    block = backtest_expression(view_db, expr)
    _assert_block_shape(block)
    assert block["verdict"] == "insufficient_data"
    assert block["degraded"] is True
    assert "option legs" in block["data_note"]


# --------------------------------------------------------------------------- #
# Trial-group deflation + persist flag
# --------------------------------------------------------------------------- #
def test_trial_group_deflation_threads_selection_bias(
    view_db, make_expression, monkeypatch
) -> None:
    """Two distinct variants under one trial_group inflate the effective N and
    re-deflate the Sharpe (the post-hoc deflation path for the portfolio engine).
    The verdict primitive is re-run on the deflated stats."""
    group = "test-trials-vm-deflate"
    reset_group(group)
    monkeypatch.setattr(
        "backend.services.backtest.portfolio.engine.run_portfolio_backtest",
        lambda symbols, **kw: _fake_portfolio_result(),
    )
    e1 = make_expression(
        kind="basket", config={"structure": {"weights": {"TCS": 0.5, "INFY": 0.5}}}
    )
    e2 = make_expression(
        kind="basket", config={"structure": {"weights": {"WIPRO": 0.5, "HCLTECH": 0.5}}}
    )
    b1 = backtest_expression(view_db, e1, trial_group=group)
    b2 = backtest_expression(view_db, e2, trial_group=group)
    # second distinct variant => num_trials grows under the shared group
    assert b1["metrics"]["forward_stats"]["num_trials"] == 1
    assert b2["metrics"]["forward_stats"]["num_trials"] == 2
    reset_group(group)


def test_persist_false_does_not_touch_the_row(
    view_db, make_expression, monkeypatch
) -> None:
    monkeypatch.setattr(
        "backend.services.backtest.pairs.engine.run_pairs_backtest",
        lambda *a, **k: _fake_pairs_result(),
    )
    expr = make_expression(
        kind="pair",
        config={"structure": {"a": "TCS", "b": "INFY", "backtest_available": True}},
    )
    block = backtest_expression(view_db, expr, persist=False)
    assert block["engine"] == "pairs"
    # nothing written back
    assert expr.backtest_run_id is None
    assert "scores" not in (expr.config or {})
    assert (
        view_db.query(ViewConfidence)
        .filter(ViewConfidence.view_id == expr.view_id)
        .count()
        == 0
    )


def test_trust_block_shape_on_both_paths(view_db, make_expression, monkeypatch) -> None:
    # success path
    monkeypatch.setattr(
        "backend.services.backtest.pairs.engine.run_pairs_backtest",
        lambda *a, **k: _fake_pairs_result(),
    )
    ok = backtest_expression(
        view_db,
        make_expression(
            kind="pair",
            config={"structure": {"a": "TCS", "b": "INFY", "backtest_available": True}},
        ),
    )
    _assert_block_shape(ok)
    # degrade path
    bad = backtest_expression(
        view_db,
        make_expression(kind="hedge", config={"structure": {}}),
    )
    _assert_block_shape(bad)
    assert bad["verdict"] == "insufficient_data"


def test_engine_map_is_referenced_not_reinvented() -> None:
    assert bt.ENGINE_BY_KIND["basket"] == "portfolio"
    assert bt.ENGINE_BY_KIND["pair"] == "pairs"
    assert bt.ENGINE_BY_KIND["option_strategy"] == "workflow"
