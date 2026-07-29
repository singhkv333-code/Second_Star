"""View Markets — Phase 4 tier comparison: ``compare_tiers``.

Backtest all three tiers (Conservative / Balanced / Aggressive) of a view through
:func:`backtest_expression`, rank them by **Trust verdict then Alignment**, and
return a comparison the FE card (Phase 6) renders — with the recommended tier
flagged HONESTLY (a higher tier only "wins" when its statistics actually clear
the bar; a thin sample stays ``insufficient_data`` and can never be recommended
over a proven lower tier).

Parallel-safe: each tier's backtest is independent and the engines are pure
(numpy + read-only DB), so the three may run concurrently. They share ONE
``trial_group`` so the Deflated-Sharpe selection-bias guard treats the three
tiers as three variants of the same view — picking the best of three without
deflation is exactly the overfit this guards against.

register-not-execute: comparison only reads/evaluates; it arms nothing.
Does NOT commit (caller owns the txn).

Returned shape (FROZEN — see :func:`compare_tiers`)::

    {
      "view_id": "...",
      "tiers": [                      # one per backtested tier, tier order
        {"tier": "conservative", "expression_id": "...",
         "expression_kind": "pair", "engine": "pairs", "trust": {<trust block>}},
        ...
      ],
      "ranking": ["balanced", "aggressive", "conservative"],   # best → worst
      "recommended_tier": "balanced" | None,    # None when nothing clears no_edge
      "recommendation_rationale": "Balanced is the only tier the data supports …",
      "as_of": "<ISO-8601>"
    }

Skeleton: raises ``NotImplementedError``; the contract is frozen for BUILD agents.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from backend.models import ExpressionTier
from backend.view_markets.deployment.backtest import (
    VERDICT_RANK,
    backtest_expression,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import MarketView, ViewExpression


# Tier render order (Cons → Bal → Aggr) for the ``tiers`` list. Ranking is a
# separate, statistics-driven order computed below.
_TIER_ORDER: tuple[str, ...] = (
    ExpressionTier.conservative.value,
    ExpressionTier.balanced.value,
    ExpressionTier.aggressive.value,
)

# A tier is only *recommendable* when the Trust verdict clears this floor — an
# ``insufficient_data`` (0) or ``no_edge`` (1) tier is NEVER recommended.
_RECOMMEND_FLOOR: int = VERDICT_RANK["unproven"]


def _alignment_score(trust: dict) -> float:
    """Pull the Phase-2 expression dial's 0..100 score from the trust block for
    ranking. Suppressed / missing (``insufficient_data`` → ``score=None``) sorts
    LAST among tiers sharing a verdict — never fabricated to a number."""
    align = trust.get("alignment")
    if isinstance(align, dict):
        for key in ("expression_score", "score"):
            val = align.get(key)
            if isinstance(val, (int, float)):
                return float(val)
    return -1.0


def _confidence(trust: dict) -> float:
    """The verdict confidence (0..100) as the final ranking tie-break; ``None``
    (honestly undefined) sorts last."""
    val = trust.get("confidence")
    return float(val) if isinstance(val, (int, float)) else -1.0


def _rank_key(entry: dict) -> tuple[int, float, float]:
    trust = entry["trust"]
    return (
        VERDICT_RANK.get(trust.get("verdict"), 0),
        _alignment_score(trust),
        _confidence(trust),
    )


def _resolve_tier_expressions(
    db: "Session", view: "MarketView",
) -> dict[str, "ViewExpression"]:
    """One ``ViewExpression`` per tier: reuse existing rows, build any missing
    tier via the ONE public Phase-3 entry ``dispatch.suggest_expressions(db,
    view, tier=…)``. A tier the catalog cannot express honestly is simply absent
    from the comparison (never faked)."""
    # Lazy import: dispatch pulls the full builder/option/pairs chain — keep the
    # deployment package import-light.
    from backend.view_markets.expressions.dispatch import (
        ExpressionDispatchError,
        suggest_expressions,
    )

    by_tier: dict[str, "ViewExpression"] = {}
    for expr in view.expressions:
        tier = str(getattr(expr.tier, "value", expr.tier))
        # Keep the most recently created row per tier (stable for re-compares).
        prev = by_tier.get(tier)
        if prev is None or (expr.created_at and prev.created_at
                            and expr.created_at >= prev.created_at):
            by_tier[tier] = expr

    for tier in _TIER_ORDER:
        if tier in by_tier:
            continue
        try:
            built = suggest_expressions(db, view, tier=tier)
        except ExpressionDispatchError:
            # No archetype expresses this view at this tier — honestly skip it.
            continue
        for row in built:
            row_tier = str(getattr(row.tier, "value", row.tier))
            by_tier.setdefault(row_tier, row)

    return by_tier


def _recommendation_rationale(
    ranked: list[dict], recommended: Optional[str],
) -> str:
    """Plain-English why — honest about the data not supporting any tier."""
    if not ranked:
        return (
            "No tier could be expressed and backtested for this view — there is "
            "nothing to recommend yet."
        )
    if recommended is None:
        worst_to_best = ", ".join(
            f"{e['tier']} ({e['trust'].get('verdict', 'unknown')})"
            for e in ranked
        )
        return (
            "None of the tiers clears the bar — the backtest evidence does not "
            f"yet support deploying any of them ({worst_to_best}). Treat this as "
            "watch-only until more data accrues."
        )
    top = next(e for e in ranked if e["tier"] == recommended)
    trust = top["trust"]
    label = trust.get("label") or trust.get("verdict") or "supported"
    note = (
        f"{recommended.capitalize()} is the strongest tier the data actually "
        f"supports (verdict: {label}"
    )
    conf = trust.get("confidence")
    if isinstance(conf, (int, float)):
        note += f", confidence {int(conf)}/100"
    note += "). Higher-risk tiers were not recommended over it without statistics to justify them."
    return note


def compare_tiers(
    db: "Session",
    view: "MarketView",
    *,
    trial_group: Optional[str] = None,
    period: Optional[str] = None,
) -> dict:
    """Backtest + rank all three tiers of ``view`` and recommend one honestly.

    Resolves the view's existing :class:`~backend.models.ViewExpression` rows (one
    per tier; builds any missing tier via ``dispatch.suggest_expressions(db, view,
    tier=…)`` when absent), runs :func:`backtest_expression` on each through ONE
    shared ``trial_group`` (so DSR deflates the best-of-three selection), then
    ranks by ``(VERDICT_RANK[trust.verdict], trust.alignment.expression_score,
    trust.confidence)`` descending.

    The recommendation is the top-ranked tier whose verdict is at least
    ``unproven`` (an ``insufficient_data`` / ``no_edge`` tier is never recommended
    — ``recommended_tier`` is then ``None`` with a rationale saying the data
    doesn't yet support any tier). Higher risk does NOT auto-win: a fragile
    Aggressive tier with deeper Monte-Carlo drawdown or a ``return_concentrated``
    flag ranks below a credible Balanced one.

    Returns the comparison dict above. Does NOT commit.
    """
    # ONE shared trial group across the three tiers: the Deflated-Sharpe guard
    # then treats Cons/Bal/Aggr as three variants of the SAME view, so picking
    # the best-of-three cannot inflate an in-sample Sharpe past deflation.
    group = trial_group or f"view:{view.id}:tier-compare"

    by_tier = _resolve_tier_expressions(db, view)

    tiers_out: list[dict] = []
    for tier in _TIER_ORDER:
        expr = by_tier.get(tier)
        if expr is None:
            continue
        # backtest_expression reuses each engine's Trust Battery + honestly
        # degrades to insufficient_data (e.g. missing MCX commodity history);
        # compare only READS the trust block — it never re-derives statistics.
        trust = backtest_expression(
            db, expr, trial_group=group, period=period,
        )
        tiers_out.append(
            {
                "tier": tier,
                "expression_id": expr.id,
                "expression_kind": str(
                    getattr(expr.expression_kind, "value", expr.expression_kind)
                ),
                "engine": trust.get("engine"),
                "trust": trust,
            }
        )

    # Rank best → worst: Trust verdict first, then the (gated) alignment dial,
    # then verdict confidence. Higher risk never auto-wins — a fragile Aggressive
    # tier with a weaker verdict / suppressed dial ranks below a credible one.
    ranked = sorted(tiers_out, key=_rank_key, reverse=True)

    recommended: Optional[str] = None
    for entry in ranked:
        if VERDICT_RANK.get(entry["trust"].get("verdict"), 0) >= _RECOMMEND_FLOOR:
            recommended = entry["tier"]
            break

    return {
        "view_id": view.id,
        "tiers": tiers_out,
        "ranking": [e["tier"] for e in ranked],
        "recommended_tier": recommended,
        "recommendation_rationale": _recommendation_rationale(
            ranked, recommended
        ),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


__all__ = ["compare_tiers"]
