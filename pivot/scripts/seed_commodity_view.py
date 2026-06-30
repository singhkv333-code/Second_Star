"""Seed ONE curated COMMODITY (MCX) View-Markets view, end-to-end, OFFLINE.

The companion to ``scripts/seed_view_example.py``, for the COMMODITY pass
(2026-06-29 — MCX commodities became tradeable via register-not-execute). It
authors a single curated **gold-vs-silver RELATIVE** view and runs the Phase-3
expression ENGINE over it::

    curation.create_view                      # the belief (RELATIVE bullion ratio)
      -> dispatch.suggest_expressions(db, view)   # 3-tier COMMODITY expressions

``dispatch`` recognises the MCX commodity named in the view (``commodities.
normalize_commodity``) and prefers the CM archetypes — here the **gold/silver MCX
ratio pair** (CM4, the required NON-basket) across the safer tiers and the
crude-style **producer-vs-importer pair** (CM3) at the aggressive tier. Every
commodity expression carries the leverage note in its ``risk_profile`` disclosure
and is register-not-execute (an armed trigger SPEC, never a placed order).

It is fully OFFLINE/honest by construction: direct MCX bullion legs have NO
aligned OHLCV in the pairs data layer, so the gold/silver spread is BUILT
construct-only (``backtest_available=False``) — β / half-life / z stay ``None``,
never a fabricated cointegration. The short leg is a TRADEABLE MCX future via
``honest_short`` (commodities are symmetrically shortable), never an AVOID.

Usage (from ``pivot/``)::

    .venv/bin/python scripts/seed_commodity_view.py     # in-memory SQLite demo

Or import ``seed_commodity_view(db)`` and pass your own (test) session. It
DELIBERATELY uses only SQLite and never touches Azure / Postgres / Kite.
"""
from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

# Make `backend` importable when run directly (mirrors seed_view_example.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.schemas import MarketViewCreate  # noqa: E402
from backend.view_markets import curation as _curation  # noqa: E402
from backend.view_markets.expressions import (  # noqa: E402
    config_schema as _config_schema,
)
from backend.view_markets.expressions import dispatch as _dispatch  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import MarketView, ViewExpression


def seed_commodity_view(db: "Session") -> tuple["MarketView", list["ViewExpression"]]:
    """Author one curated gold-vs-silver COMMODITY view + its 3 tiered expressions.

    Creates the ``MarketView`` and runs ``dispatch.suggest_expressions`` (the ONE
    public Phase-3 entry), which persists one ``ViewExpression`` per tier. The
    caller owns the session lifecycle; this performs a single ``db.commit()`` at
    the end. Returns ``(view, expressions)``.
    """
    view = _curation.create_view(
        db,
        MarketViewCreate(
            view_type="relative",
            title="Gold outperforms silver as the gold/silver ratio mean-reverts",
            thesis=(
                "The gold/silver ratio is stretched above its long-run band; with "
                "real rates rolling over, gold's bullion bid beats silver's "
                "industrial leg over the window. Expressed on MCX as a leveraged "
                "long-gold / short-silver bullion ratio."
            ),
            category="commodities",
            time_horizon="6m",
        ),
        user_id=None,
    )
    db.flush()

    # The Phase-3 engine: a commodity view -> tiered CM expressions (the gold/silver
    # MCX ratio pair is the NON-basket the spec asks for). No commit inside dispatch.
    expressions = _dispatch.suggest_expressions(db, view)

    db.commit()
    return view, expressions


def _short_leg(expr: "ViewExpression") -> dict[str, object]:
    return (expr.config.get("structure", {}) or {}).get("short_leg", {}) or {}


def _is_commodity_expr(expr: "ViewExpression") -> bool:
    if (expr.config.get("structure", {}) or {}).get("is_commodity"):
        return True
    for inst in expr.config.get("instruments", []) or []:
        if _config_schema.is_commodity_segment(inst.get("segment")):
            return True
    return False


def _main() -> None:
    """Run the commodity seed against a throwaway in-memory SQLite DB (NEVER Azure)."""
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
        view, expressions = seed_commodity_view(db)
        print("Seeded curated COMMODITY view:")
        print(f"  id          = {view.id}")
        print(f"  title       = {view.title}")
        print(f"  view_type   = {view.view_type.value}")
        print(f"  expressions = {len(expressions)} tier(s)")
        non_basket = 0
        for expr in expressions:
            cfg = expr.config
            structure = cfg.get("structure", {}) or {}
            kind = expr.expression_kind.value
            if kind != "basket":
                non_basket += 1
            short = _short_leg(expr)
            leverage = "LEVERAGED" in (expr.risk_profile or "")
            timing = cfg.get("timing", {}) or {}
            armed = "armed" in (timing.get("note") or "").lower()
            print(f"  - {expr.tier.value:12} {kind:14} {cfg.get('archetype')}")
            print(
                f"      commodity={_is_commodity_expr(expr)} "
                f"backtest_available={structure.get('backtest_available')} "
                f"leverage_note_in_risk_profile={leverage}"
            )
            if short:
                print(
                    f"      short_leg: mode={short.get('mode')} "
                    f"tradeable={short.get('tradeable')} degraded={short.get('degraded')}"
                )
            print(
                f"      register-not-execute: workflow_id={expr.workflow_id} "
                f"backtest_run_id={expr.backtest_run_id} armed_trigger={armed}"
            )
        print(f"  NON-basket expressions: {non_basket} (spec wants >= 1)")
    finally:
        db.close()


if __name__ == "__main__":
    _main()
