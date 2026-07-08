"""Seed the Phase-3 EXPRESSION ENGINE on one curated view, end-to-end, OFFLINE.

Companion to ``scripts/seed_view_example.py`` (which authors a curated view by
hand). This script takes the same RBI rate-cut **EVENT** view and runs it through
the Phase-3 dispatch — ``view_markets.expressions.dispatch.suggest_expressions``
— to GENERATE its Conservative / Balanced / Aggressive expressions as
``ViewExpression`` rows, demonstrating that the engine is NOT "always a basket":

    curation.create_view                       # the belief (EVENT, resolution date)
      -> transmission.seed_transmission_from_scenario("rate_cut")
         + curation.attach_transmission         # cause -> effect DAG (symbol seed)
      -> dispatch.suggest_expressions(db, view) # 3 tiered ViewExpression rows
           Conservative -> E1 defined-risk bull-call/credit spread (option)
           Balanced     -> E2 NBFC-vs-bank PAIR  (the NON-basket, honest short)
           Aggressive   -> E3 event straddle      (option)

The market engines (option chain, pairs cointegration, option costs) are mocked
with deterministic illustrative payloads so the script needs NO network and is
safe against a throwaway in-memory SQLite DB. It DELIBERATELY does not touch
Azure / Postgres, creates no workflow and arms no order (register-not-execute:
``config.timing`` is a workflow SPEC only).

Usage (from ``pivot/``)::

    .venv/bin/python scripts/seed_view_expressions_example.py

Or import ``seed_rate_cut_expressions(db)`` and pass your own session (e.g. the
in-memory test session).
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterator
from unittest.mock import patch

# Make `backend` importable when run directly (mirrors scripts/kite_connect.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.schemas import MarketViewCreate  # noqa: E402
from backend.view_markets import curation as _curation  # noqa: E402
from backend.view_markets import transmission as _transmission  # noqa: E402
from backend.view_markets.expressions import dispatch  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import MarketView, ViewExpression


_SCENARIO_KEY = "rate_cut"


# ── deterministic, offline engine fakes (clearly illustrative) ───────────────


def _illustrative_pairs_backtest(a: str, b: str, **_kw: Any) -> dict[str, Any]:
    """A representative cointegration payload (illustrative — not a live fit)."""
    return {
        "cointegration": {
            "alpha": 0.12, "beta": 0.82, "adf_tstat": -3.8,
            "half_life_days": 11.0, "cointegrated_at": "5%",
        },
        "metrics": {}, "series": {},
    }


def _illustrative_resolve_strategy(
    db: Any, underlying: str, template_name: str, *,
    expiry: Any = None, qty_lots: int = 1,
    explicit_legs: Any = None, chain: Any = None,
) -> dict[str, Any]:
    """A deterministic defined-risk option payload (bounded max loss)."""
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
            "pop": 0.54, "breakevens": [50250.0],
            "net_greeks": {"delta": 9.0, "gamma": 0.1, "theta": -48.0, "vega": 28.0},
            "capital_required": 2400.0, "margin_estimate": 2400.0,
        },
        "critique": {"verdict": "ok", "flags": [], "summary": "illustrative"},
        "validation": {"liquidity_flags": []},
    }


def _illustrative_implied_move(
    db: Any, underlying: str, *, expiry: Any = None,
    horizon_days: Any = None, width: int = 10,
) -> Any:
    from backend.view_markets import implied_move as _im

    return _im.ImpliedMove(
        underlying=underlying, expiry=expiry, forward=50000.0, atm_strike=50000.0,
        atm_iv=0.16, t_years=0.08, expected_move_abs=1200.0, expected_move_pct=2.4,
        low=48800.0, high=51200.0, straddle_price=1400.0, source="iv", asof=None,
    )


@contextmanager
def _mock_market_engines() -> Iterator[None]:
    """Patch the builders' market seams with the illustrative offline fakes."""
    from backend.services import trading_costs
    from backend.view_markets.expressions.builders import option_builder, pair_builder

    with patch.object(pair_builder, "run_pairs_backtest", _illustrative_pairs_backtest), \
        patch.object(option_builder._opt, "resolve_strategy", _illustrative_resolve_strategy), \
        patch.object(option_builder._im, "implied_move", _illustrative_implied_move), \
        patch.object(trading_costs, "option_leg_bps", lambda side, **k: 3.0):
        yield


# ── the seed ─────────────────────────────────────────────────────────────────


def seed_rate_cut_expressions(db: "Session") -> tuple["MarketView", list["ViewExpression"]]:
    """Author the RBI rate-cut view and GENERATE its 3 tiered expressions.

    Runs ``curation.create_view`` + a transmission seed, then
    ``dispatch.suggest_expressions`` under the offline engine mocks, and commits.
    Returns ``(view, expressions)``. The caller owns the session lifecycle.
    """
    resolution_date = datetime.now(timezone.utc) + timedelta(days=21)

    view = _curation.create_view(
        db,
        MarketViewCreate(
            view_type="event",
            title="RBI cuts the repo rate at the next MPC meeting",
            thesis=(
                "A rate-cut cycle lowers funding costs and supports credit "
                "growth and EMI-sensitive demand; banks and NBFCs are the "
                "primary beneficiaries, life insurers a relative loser."
            ),
            category="rbi_mpc",
            time_horizon="1-3 months",
            resolution_date=resolution_date,
        ),
    )

    # Cause -> effect transmission DAG (also seeds the symbol universe dispatch
    # resolves from — winners/losers come from the rate_cut scenario).
    edges = _transmission.seed_transmission_from_scenario(
        _SCENARIO_KEY, include_losers=True,
    )
    _curation.attach_transmission(db, view.id, edges, replace=True)

    with _mock_market_engines():
        expressions = dispatch.suggest_expressions(db, view)

    db.commit()
    return view, expressions


def _print_summary(view: "MarketView", expressions: list["ViewExpression"]) -> None:
    from backend.view_markets.expressions.catalog import KIND_BASKET

    print("Seeded curated view + GENERATED expressions (Phase-3 dispatch):")
    print(f"  view id     = {view.id}")
    print(f"  title       = {view.title}")
    print(f"  view_type   = {view.view_type.value}")
    print(f"  tiers built = {len(expressions)}")
    non_basket = 0
    for ex in expressions:
        cfg = ex.config
        struct = cfg.get("structure", {})
        kind = ex.expression_kind.value
        if kind != KIND_BASKET:
            non_basket += 1
        print()
        print(f"  ── {ex.tier.value.upper()} :: {kind} :: {cfg.get('archetype')}")
        print(f"     label             : {cfg.get('label')}")
        print(f"     rationale         : {ex.rationale}")
        print(f"     risk_profile      : {ex.risk_profile}")
        print(f"     capital_intensity : {ex.capital_intensity}")
        print(f"     historical_strength: {ex.historical_strength}")
        print(f"     time_horizon      : {ex.time_horizon}")
        short = struct.get("short_leg")
        if short:
            print(
                f"     short_leg (honest): mode={short['mode']} "
                f"tradeable={short['tradeable']} degraded={short['degraded']}"
            )
        timing = cfg.get("timing", {})
        modes = [t["trigger"]["step_type"] for t in timing.get("tranches", [])]
        print(f"     timing            : {timing.get('mode')} via {modes}")
        print(f"     workflow_id={ex.workflow_id}  backtest_run_id={ex.backtest_run_id}")
    print()
    print(
        f"  NON-basket tiers: {non_basket}/{len(expressions)} "
        "(the engine is NOT 'always a basket')."
    )
    print(
        "  register-not-execute: every expression is an ARMED workflow SPEC "
        "(no workflow created, no order placed)."
    )


def _main() -> None:
    """Run the seed against a throwaway in-memory SQLite DB (NEVER Azure)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend import models  # noqa: F401  (register every table on Base)
    from backend.database import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    try:
        view, expressions = seed_rate_cut_expressions(db)
        _print_summary(view, expressions)
    finally:
        db.close()


if __name__ == "__main__":
    _main()
