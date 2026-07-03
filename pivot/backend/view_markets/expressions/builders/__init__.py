"""View Markets — Phase 3 per-``expression_kind`` builders.

One builder per ``ExpressionKind`` (NOT one per strategy). Each builder takes a
curated view + a catalog archetype + a tier, delegates to the EXISTING repo
engine for that kind, applies the tier knobs and the India expressability guards
(``honest_short`` / ``screens``), and returns a filled
``config_schema.ExpressionConfig`` envelope (a plain dict). Dispatch wraps the
returned dict into a ``ViewExpression`` ORM row and enforces the disclosures.

Builder contract (uniform signature)::

    build_<kind>_expression(
        db: Session,
        view: MarketView,
        archetype: catalog.Archetype,
        tier: str,                 # ExpressionTier value
        **ctx,                     # kind-specific context (symbols, underlying, …)
    ) -> dict                      # an ExpressionConfig envelope

Delegations (real engines, never reinvented):
  * option   → ``services.option_strategies`` (TEMPLATES + resolve/greeks/POP/
               payoff/margin/critique) + ``view_markets.implied_move``.
  * pair     → ``services.backtest.pairs`` (EG/Johansen/OU/z-bands/beta-hedge) +
               ``honest_short`` for the short leg.
  * basket   → ``propose_basket_allocation`` macro / ``weighting`` +
               ``sector_universe``, gated by ``screens``.
  * multi-asset → ``weighting`` risk-parity at the asset-class level + gold ETF
               sleeve + ``hedge_builder``.
  * hedge    → ``services.option_strategies`` collar / protective put / covered
               call at INDEX level (NIFTY/BANKNIFTY).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.view_markets.expressions.builders.basket_builder import (
    build_basket_expression,
)
from backend.view_markets.expressions.builders.hedge_builder import (
    build_hedge_expression,
)
from backend.view_markets.expressions.builders.multi_asset_builder import (
    build_multi_asset_expression,
)
from backend.view_markets.expressions.builders.option_builder import (
    build_option_expression,
)
from backend.view_markets.expressions.builders.pair_builder import (
    build_pair_expression,
)

# Dispatch key: ExpressionKind value → builder callable. Each builder shares the
# uniform ``(db, view, archetype, tier, **ctx) -> ExpressionConfig`` signature, so
# the value type is a single ``Callable`` the dispatch loop can index + call.
BUILDERS: dict[str, Callable[..., dict[str, Any]]] = {
    "option_strategy": build_option_expression,
    "pair": build_pair_expression,
    "basket": build_basket_expression,
    "multi_asset": build_multi_asset_expression,
    "hedge": build_hedge_expression,
}

__all__ = [
    "BUILDERS",
    "build_option_expression",
    "build_pair_expression",
    "build_basket_expression",
    "build_multi_asset_expression",
    "build_hedge_expression",
]
