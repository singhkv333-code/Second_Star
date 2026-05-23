"""Chat-tool handlers that bridge the DSL backtester / workflow
proposal into the chat-tool dispatch surface.

Two tools:

  ``backtest_dsl_tree``  — given a natural-language entry condition and
                           a primary symbol + window, translate to a DSL
                           tree and run the Phase B backtester. Returns
                           a payload the FE renders with the existing
                           ``indicator_backtest_chart`` card.

  ``propose_dsl_workflow`` — given a natural-language entry condition,
                           action, and symbol, build a workflow draft
                           with a ``trigger.compound`` step (carrying the
                           translated tree) and an action step. Returns a
                           ``workflow_draft_card`` the user activates
                           from chat.

Both handlers do the LLM tree-translation server-side (via
``backend.workflows.dsl.llm_translate``) so the chat-side LLM only has
to extract the user's intent — it doesn't need to know the DSL grammar.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

from backend.workflows.dsl.llm_translate import (
    TranslationError,
    translate_condition_to_tree,
)


logger = logging.getLogger(__name__)


# ── backtest_dsl_tree ────────────────────────────────────────────────


async def backtest_dsl_tree(args: dict) -> dict:
    """Run a DSL-tree backtest from a natural-language condition.

    Args (all required unless noted):
      condition         — NL trading entry condition (string)
      primary_symbol    — symbol the trade fires on (e.g. "TCS")
      start_date        — ISO date (optional; defaults to 3y ago)
      end_date          — ISO date (optional; defaults to today)
      exit_kind         — "n_day_hold" | "stop_loss_pct" (default n_day_hold)
      exit_bars         — int, used when exit_kind=n_day_hold (default 10)
      exit_pct          — float in 0..1, used when exit_kind=stop_loss_pct
      starting_capital  — ₹, default 100_000
      quantity          — shares per fire, default 10
    """
    args = args or {}
    condition = (args.get("condition") or "").strip()
    primary = (args.get("primary_symbol") or "").strip().upper()
    if not condition:
        raise ValueError(
            "backtest_dsl_tree needs a 'condition' (natural-language "
            "entry condition like 'Buy TCS when RSI(14) drops below 30')."
        )
    if not primary:
        raise ValueError(
            "backtest_dsl_tree needs a 'primary_symbol' (the symbol the "
            "trade fires on, e.g. TCS)."
        )

    try:
        tree, tx_meta = await translate_condition_to_tree(
            condition, cache_key="dsl.chat.backtest.v1",
        )
    except TranslationError as exc:
        raise ValueError(
            f"could not translate condition into a DSL tree: {exc}"
        ) from None

    # Date window — default to 3 years ending today.
    today = date.today()
    try:
        end_d = (
            date.fromisoformat(args["end_date"])
            if args.get("end_date") else today
        )
    except ValueError:
        end_d = today
    try:
        start_d = (
            date.fromisoformat(args["start_date"])
            if args.get("start_date")
            else end_d - timedelta(days=365 * 3 + 2)
        )
    except ValueError:
        start_d = end_d - timedelta(days=365 * 3 + 2)
    if end_d <= start_d:
        end_d = start_d + timedelta(days=365)

    # Exit policy — shape the discriminated union the request body
    # expects.
    exit_kind = (args.get("exit_kind") or "n_day_hold").lower()
    if exit_kind not in ("n_day_hold", "stop_loss_pct"):
        exit_kind = "n_day_hold"
    if exit_kind == "n_day_hold":
        exit_policy = {"kind": "n_day_hold",
                       "bars": int(args.get("exit_bars") or 10)}
    else:
        v = float(args.get("exit_pct") or 0.05)
        v = max(0.001, min(0.5, v))
        exit_policy = {"kind": "stop_loss_pct", "value": v}

    # Build BacktestRequest and run engine in a worker thread.
    from backend.workflows.dsl.backtest.engine import run_backtest
    from backend.workflows.dsl.backtest.schema import BacktestRequest
    from backend.workflows.dsl.validators import (
        DSLValidationError, semantic_validate,
    )
    from backend.workflows.dsl.schema import Tree
    from pydantic import TypeAdapter, ValidationError

    payload = {
        "tree": tree,
        "primary_symbol": primary,
        "start_date": start_d.isoformat(),
        "end_date": end_d.isoformat(),
        "starting_capital": float(args.get("starting_capital") or 100_000.0),
        "quantity": int(args.get("quantity") or 10),
        "exit_policy": exit_policy,
        "save": False,
    }
    try:
        request = BacktestRequest.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"request validation failed: {exc.errors()[0]['msg']}"
        ) from None

    # Belt-and-suspenders: validate the tree separately so the error
    # message is DSL-flavoured rather than Pydantic-generic.
    try:
        parsed_tree = TypeAdapter(Tree).validate_python(payload["tree"])
        semantic_validate(parsed_tree)
    except (DSLValidationError, ValidationError) as exc:
        raise ValueError(f"tree validation failed: {exc}") from None

    try:
        result = await asyncio.to_thread(
            run_backtest, request=request, user_id=0, fetcher=None,
        )
    except ValueError as exc:
        raise ValueError(f"backtest engine: {exc}") from None
    except Exception as exc:  # noqa: BLE001 — surface anything else cleanly
        logger.exception("[chat.dsl.backtest] engine crashed: %s", exc)
        raise ValueError(f"backtest engine crashed: {type(exc).__name__}")

    # Shape the BacktestResult into the same chart-card payload the FE
    # already renders for legacy backtests, PLUS the DSL-specific
    # extras (tree_summary, full trades list) that the card's
    # extended renderer uses when present. We keep render_hint =
    # "indicator_backtest_chart" so existing ChatDemo dispatch is
    # unchanged; the card itself sniffs `tree_summary` and trades to
    # decide which surface to draw.
    metrics = result.metrics
    summary = (
        f"Strategy returned {metrics.total_return_pct:+.1f}% across "
        f"{metrics.total_trades} trade(s). Max drawdown {metrics.max_drawdown_pct:.1f}%. "
        f"Win rate {metrics.win_rate_pct:.0f}%."
    )

    # Build the legacy-shaped signals list (buy + sell as separate
    # entries) AND a richer per-trade list so the card can show
    # entry/exit pairs.
    signals: list[dict] = []
    rich_trades: list[dict] = []
    for t in result.trades:
        signals.append({
            "t": t.entry_date.isoformat(),
            "side": "buy",
            "price": float(t.entry_price),
            "indicator_value": None,
        })
        if t.exit_date is not None and t.exit_price is not None:
            signals.append({
                "t": t.exit_date.isoformat(),
                "side": "sell",
                "price": float(t.exit_price),
                "indicator_value": None,
            })
        rich_trades.append({
            "trade_id": t.trade_id,
            "entry_date": t.entry_date.isoformat(),
            "entry_price": float(t.entry_price),
            "exit_date": t.exit_date.isoformat() if t.exit_date else None,
            "exit_price": float(t.exit_price) if t.exit_price is not None else None,
            "quantity": int(t.quantity),
            "net_pnl": float(t.net_pnl),
            "return_pct": float(t.return_pct),
            "exit_reason": t.exit_reason,
        })

    n_wins = metrics.winning_trades
    n_trades = metrics.total_trades

    return {
        "_render_hint": "indicator_backtest_chart",
        "symbol": result.request.primary_symbol,
        # Compound DSL trees don't fit the (indicator,period,operator,
        # threshold) shape. The FE's IndicatorBacktestCard now checks
        # tree_summary FIRST and falls back to indicator-based when
        # that field is absent — set sane no-ops here.
        "indicator": "compound",
        "indicator_period": 0,
        "operator": "tree",
        "threshold": 0.0,
        "period_label": (
            f"{start_d.isoformat()} → {end_d.isoformat()}"
        ),
        # Map equity curve into both panels so the FE has something
        # in each thumbnail slot.
        "price_curve": [
            {"t": p.date.isoformat(), "v": float(p.equity)}
            for p in result.equity_curve
        ],
        "equity_curve": [
            {"t": p.date.isoformat(), "v": float(p.equity)}
            for p in result.equity_curve
        ],
        "indicator_curve": [],
        "signals": signals,
        "metrics": {
            # Legacy-shape keys the existing IndicatorBacktestCard reads.
            "total_return_pct": float(metrics.total_return_pct),
            "cagr_pct": float(metrics.cagr_pct),
            "max_drawdown_pct": float(metrics.max_drawdown_pct),
            "hit_rate_pct": float(metrics.win_rate_pct),
            "n_trades": int(n_trades),
            "n_wins": int(n_wins),
            "starting_capital": float(request.starting_capital),
            "ending_value": float(metrics.ending_value),
        },
        "bench_buy_hold_return_pct": None,
        "summary_text": summary,
        # DSL-native fields — present ONLY on DSL responses. The card
        # uses these to render the readback as the title and (later)
        # a trades-list expansion.
        "tree_summary": result.tree_summary,
        "trades": rich_trades,
        "diagnostics": {
            "bars_evaluated": result.diagnostics.bars_evaluated,
            "fire_bars": result.diagnostics.fire_bars,
            "unknown_value_bars": result.diagnostics.unknown_value_bars,
        },
        "translation_meta": tx_meta,
    }


# ── propose_dsl_workflow ────────────────────────────────────────────


async def propose_dsl_workflow(args: dict) -> dict:
    """Build a workflow draft whose trigger is a DSL ``trigger.compound``
    step. The chat user sees a confirmation card; activating it
    registers the workflow with the live watcher.

    Args:
      condition         — NL entry condition
      name              — short human label for the workflow
      primary_symbol    — symbol the action targets
      action_kind       — "notify_only" (default) | "buy_market" | "buy_limit"
      quantity          — int, only used when action_kind starts with 'buy'
      limit_price       — float, only used when action_kind=buy_limit
    """
    args = args or {}
    condition = (args.get("condition") or "").strip()
    primary = (args.get("primary_symbol") or "").strip().upper()
    label = (args.get("name") or "").strip() or f"{primary} compound trigger"
    action_kind = (args.get("action_kind") or "notify_only").lower()
    if not condition:
        raise ValueError(
            "propose_dsl_workflow needs a 'condition' (NL entry "
            "condition such as 'when RSI(14) < 30 and price > SMA(50)')."
        )
    if not primary:
        raise ValueError(
            "propose_dsl_workflow needs a 'primary_symbol' — the "
            "symbol the action fires on."
        )

    try:
        tree, tx_meta = await translate_condition_to_tree(
            condition, cache_key="dsl.chat.propose.v1",
        )
    except TranslationError as exc:
        raise ValueError(
            f"could not translate condition into a DSL tree: {exc}"
        ) from None

    # Validate the tree before we wrap it in a workflow step.
    from backend.workflows.dsl.schema import Tree
    from backend.workflows.dsl.validators import (
        DSLValidationError, semantic_validate,
    )
    from pydantic import TypeAdapter, ValidationError
    try:
        parsed = TypeAdapter(Tree).validate_python(tree)
        semantic_validate(parsed)
    except (DSLValidationError, ValidationError) as exc:
        raise ValueError(f"translated tree is invalid: {exc}") from None

    from backend.workflows.dsl.readback import tree_to_english
    readback = tree_to_english(parsed)

    # Build the action step shape. For v1 we only support three:
    #   notify_only  → notify.message (push channel)
    #   buy_market   → place.market_order
    #   buy_limit    → place.limit_order
    if action_kind not in ("notify_only", "buy_market", "buy_limit"):
        action_kind = "notify_only"

    qty = int(args.get("quantity") or 1)
    limit_px = args.get("limit_price")

    if action_kind == "notify_only":
        action_step = {
            "step_type": "notify.message",
            "config": {
                "channel": "push",
                "message": (
                    f"{label} fired — entry condition: {readback}"
                ),
            },
        }
    elif action_kind == "buy_market":
        action_step = {
            "step_type": "order.register",
            "config": {
                "side": "BUY",
                "order_type": "MARKET",
                "symbol": primary,
                "exchange": "NSE",
                "quantity": qty,
            },
        }
    else:   # buy_limit
        if limit_px is None:
            raise ValueError(
                "buy_limit action requires 'limit_price'"
            )
        action_step = {
            "step_type": "order.register",
            "config": {
                "side": "BUY",
                "order_type": "LIMIT",
                "symbol": primary,
                "exchange": "NSE",
                "quantity": qty,
                "limit_price": float(limit_px),
            },
        }

    # Assemble the workflow draft — same shape that propose_workflow
    # already returns so the FE's draft card renders unchanged.
    draft = {
        "_render_hint": "workflow_draft_card",
        "draft_id": str(uuid.uuid4()),
        "name": label,
        "description": f"Trigger: {readback}",
        "steps": [
            {
                "step_type": "trigger.compound",
                "config": {
                    "entry": tree,
                    "symbol": primary,
                    "exchange": "NSE",
                },
            },
            action_step,
        ],
        # Surfaced for the chat assistant so it can present the
        # readback without re-rendering server-side.
        "readback": readback,
        "translation_meta": tx_meta,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    return draft
