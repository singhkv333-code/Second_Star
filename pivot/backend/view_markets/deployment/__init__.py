"""View Markets — Phase 4 deployment package: backtest · compare · deploy.

The seam that takes a built :class:`~backend.models.ViewExpression` (Phase 3
``dispatch.suggest_expressions``) from *described* to *evaluated and armed*:

  * :func:`backtest_expression` — route the expression to its real engine
    (portfolio / pairs / workflow per :data:`ENGINE_BY_KIND`), run the shared
    **Trust Battery** (forward-stats + Monte-Carlo + sub-periods + trial-deflated
    verdict), and attach ``backtest_run_id`` + ``config.scores.trust`` + the
    Phase-2 expression confidence dial. Honest ``insufficient_data`` when MCX
    commodity price history is missing — never a fabricated curve.
  * :func:`compare_tiers` — backtest all three tiers under one ``trial_group``,
    rank by Trust verdict then Alignment, recommend the supported tier honestly.
  * :func:`deploy_expression` — synthesize the ARMED workflow draft (trigger
    branches from ``config.timing`` + the kind's approval-gated action step),
    persist it, link ``ViewExpression.workflow_id``, optionally activate.
    register-not-execute: arms the trigger, NEVER places an order.

Reuse, don't reinvent: the battery, the engines, the timing→trigger mapper, the
workflow create/activate path, and the Phase-2 confidence scorer are all existing
interfaces — this package only wires them to ``ViewExpression``.
"""
from __future__ import annotations

from backend.view_markets.deployment.backtest import (
    ENGINE_BY_KIND,
    TRUST_BLOCK_KEYS,
    TRUST_METRICS_KEYS,
    VERDICT_RANK,
    backtest_expression,
)
from backend.view_markets.deployment.compare import compare_tiers
from backend.view_markets.deployment.deploy import (
    ACTION_STEP_BY_KIND,
    REQUIRES_APPROVAL,
    deploy_expression,
)

__all__ = [
    "ENGINE_BY_KIND",
    "VERDICT_RANK",
    "TRUST_BLOCK_KEYS",
    "TRUST_METRICS_KEYS",
    "backtest_expression",
    "compare_tiers",
    "ACTION_STEP_BY_KIND",
    "REQUIRES_APPROVAL",
    "deploy_expression",
]
