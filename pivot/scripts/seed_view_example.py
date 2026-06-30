"""Seed ONE example curated View-Markets view, end-to-end, OFFLINE.

Demonstrates the beta *manual-curation* authoring path (the EVENT/RELATIVE/THEME
generators are deferred — views are hand-decided by a curator). It builds a
single RBI rate-cut **EVENT** view through the real ``view_markets`` services:

    curation.create_view              # the belief (EVENT, resolution date)
      -> transmission.seed_transmission_from_scenario("rate_cut")
         + curation.attach_transmission   # cause -> effect DAG
      -> curation.attach_expressions      # 3 tiers, all 5 disclosures
      -> expectations.SurpriseFraming
         + curation.attach_expectations   # "what's priced in" vs the view
      -> confidence.score_*_dial / TwoDialScore
         + curation.attach_confidence     # two SEPARATE dials
      -> curation.validate_for_review -> curation.publish_view

Everything is authored from *curated* values, so the script needs NO network
(no Kite option chain, no event-study OHLCV) and is safe to run against a
throwaway SQLite DB. It DELIBERATELY does not touch Azure / Postgres.

Usage (from ``pivot/``)::

    .venv/bin/python scripts/seed_view_example.py        # in-memory SQLite demo

Or import ``seed_rbi_rate_cut_view(db)`` from a test / notebook and pass your own
session (e.g. the in-memory test session) to author against it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

# Make `backend` importable when run directly (the script's own dir is on
# sys.path, not the pivot/ root). Mirrors scripts/kite_connect.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.schemas import (  # noqa: E402
    MarketViewCreate,
    ViewExpressionInput,
)
from backend.view_markets import confidence as _confidence  # noqa: E402
from backend.view_markets import curation as _curation  # noqa: E402
from backend.view_markets import transmission as _transmission  # noqa: E402
from backend.view_markets.expectations import SurpriseFraming  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import MarketView


_SCENARIO_KEY = "rate_cut"


def _example_expressions() -> list[ViewExpressionInput]:
    """Three tiers (conservative / balanced / aggressive), each carrying ALL
    FIVE spec disclosures so the publish gate passes."""
    return [
        ViewExpressionInput(
            tier="conservative",
            expression_kind="basket",
            config={
                "weights": [
                    {"symbol": "HDFCBANK", "weight": 40},
                    {"symbol": "ICICIBANK", "weight": 40},
                    {"symbol": "NIFTYBEES", "weight": 20},
                ]
            },
            rationale=(
                "Own the largest, best-capitalised private banks that gain on "
                "credit growth + treasury gains as the rate-cut cycle plays out."
            ),
            risk_profile="Low — large-cap, liquid, no leverage or options.",
            capital_intensity="Moderate — fully-funded long basket.",
            historical_strength=(
                "Banks have led equity returns in past easing cycles, though "
                "the relationship is regime-dependent (not a guarantee)."
            ),
            time_horizon="3-6 months across the easing cycle.",
        ),
        ViewExpressionInput(
            tier="balanced",
            expression_kind="basket",
            config={
                "weights": [
                    {"symbol": "HDFCBANK", "weight": 30},
                    {"symbol": "ICICIBANK", "weight": 30},
                    {"symbol": "BAJFINANCE", "weight": 40},
                ]
            },
            rationale=(
                "Tilt toward an NBFC (BAJFINANCE) whose funding cost falls "
                "faster than banks', widening lending spreads."
            ),
            risk_profile="Moderate — higher-beta NBFC concentration.",
            capital_intensity="Moderate — fully-funded long basket.",
            historical_strength=(
                "NBFCs are more rate-sensitive than banks; spread expansion is "
                "historically meaningful but cyclically variable."
            ),
            time_horizon="3-6 months.",
        ),
        ViewExpressionInput(
            tier="aggressive",
            expression_kind="option_strategy",
            config={
                "underlying": "BANKNIFTY",
                "structure": "bull_call_spread",
                "note": "directional, defined-risk expression of the cut thesis",
            },
            rationale=(
                "A defined-risk BANKNIFTY bull call spread expresses the same "
                "view with leverage and a capped loss."
            ),
            risk_profile="High — option leverage; max loss = net debit paid.",
            capital_intensity="Low premium outlay, high notional exposure.",
            historical_strength=(
                "Directional option structures amplify the bank-index move when "
                "the thesis is right and decay to zero when it isn't."
            ),
            time_horizon="Single expiry around the MPC decision.",
        ),
    ]


def _example_surprise() -> SurpriseFraming:
    """A hand-authored surprise framing (curated, not a live option read).

    ``expected_value`` is what the market has priced into the policy-rate path;
    ``user_view_value`` is the curator's own number; ``surprise_sign`` is the
    delta. ``source="model"`` (Pivot's own framing). No prediction-market prior
    is set (PROGA — it would be hidden anyway)."""
    return SurpriseFraming(
        underlying="BANKNIFTY",
        expected_value=6.25,           # market-priced terminal repo path (%)
        user_view_value=6.00,          # curator expects one more cut than priced
        surprise_sign="negative",      # user sees a LOWER rate than priced
        surprise_magnitude=0.25,
        implied_probability=0.55,      # curated P(bank index up over the window)
        source="model",
        notes=("curated example — values are illustrative, not a live read",),
    )


def _example_two_dial() -> _confidence.TwoDialScore:
    """Score both dials SEPARATELY from curated inputs (no event study needed).

    Uses an ``unproven`` Trust verdict so the dials are capped at 79 — an honest
    ceiling for a curated example with no live backtest behind it."""
    outcome = _confidence.score_outcome_dial(
        hit_rate=0.62,            # banks up in ~62% of past easing windows
        edge_vs_priced=0.07,      # own prior modestly above priced-in odds
        sample_n=11,              # curated analog count
        verdict="unproven",
    )
    expression = _confidence.score_expression_dial(
        caar_bhar_alignment=0.60,
        significance_p=0.08,
        cost_survival=0.70,
        payoff_pop=0.55,
        verdict="unproven",
    )
    return _confidence.TwoDialScore(
        outcome=outcome,
        expression=expression,
        flags=("curated_example",),
    )


def seed_rbi_rate_cut_view(db: "Session") -> "MarketView":
    """Author + publish one example RBI rate-cut EVENT view on ``db``.

    Runs the full curation pipeline and commits. Returns the published
    ``MarketView``. The caller owns the session lifecycle; this performs a single
    ``db.commit()`` at the end."""
    resolution_date = datetime.now(timezone.utc) + timedelta(days=21)

    # 1) The belief.
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
            time_horizon="3-6 months",
            resolution_date=resolution_date,
        ),
    )

    # 2) Cause -> effect transmission DAG, seeded from the thematic scenario.
    edges = _transmission.seed_transmission_from_scenario(
        _SCENARIO_KEY, include_losers=True,
    )
    _curation.attach_transmission(db, view.id, edges, replace=True)

    # 3) Deployable expressions (one per tier).
    _curation.attach_expressions(
        db, view.id, _example_expressions(), replace=True,
    )

    # 4) Market-expectations / surprise framing.
    _curation.attach_expectations(db, view.id, _example_surprise(), replace=True)

    # 5) Two-dial confidence (kept SEPARATE).
    _curation.attach_confidence(db, view.id, _example_two_dial())

    # 6) Review gate -> publish.
    gate = _curation.validate_for_review(db, view)
    if not gate.ok:
        raise SystemExit(f"review gate failed: {gate.failures}")
    _curation.publish_view(db, view.id)

    db.commit()
    return view


def _main() -> None:
    """Run the seed against a throwaway in-memory SQLite DB (NEVER Azure)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.database import Base
    from backend import models  # noqa: F401  (register every table on Base)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine,
    )

    db = SessionLocal()
    try:
        view = seed_rbi_rate_cut_view(db)
        gate = _curation.validate_for_review(db, view)
        print("Seeded curated view:")
        print(f"  id            = {view.id}")
        print(f"  title         = {view.title}")
        print(f"  view_type     = {view.view_type.value}")
        print(f"  status        = {view.status.value}")
        print(f"  published_at  = {view.published_at}")
        print(f"  transmission  = {len(view.transmission)} edge(s)")
        print(f"  expressions   = {len(view.expressions)} tier(s)")
        print(f"  expectations  = {len(view.expectations)} row(s)")
        print(f"  confidence    = {len(view.confidence)} dial(s)")
        for c in view.confidence:
            print(f"      {c.dimension.value}: score={c.score}")
        print(f"  review gate ok = {gate.ok}")
    finally:
        db.close()


if __name__ == "__main__":
    _main()
