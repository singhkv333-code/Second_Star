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
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

from backend.workflows.dsl.llm_translate import (
    TranslationError,
    translate_condition_to_tree,
)


logger = logging.getLogger(__name__)


# Tokens that look like NSE tickers (3-15 uppercase letters) but aren't.
# Used to count REAL tickers in a NL condition string when deciding
# whether the prompt is single-symbol (DSL handles it) vs multi-symbol
# (must go through propose_workflow with one branch per symbol).
_DSL_NON_TICKER_TOKENS: frozenset[str] = frozenset({
    # Day-of-week / time
    "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
    "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
    "SATURDAY", "SUNDAY", "TODAY", "YESTERDAY", "TOMORROW",
    # Exchanges / boilerplate
    "NSE", "BSE", "INR", "IST", "EOD",
    # Indicators / order types
    "RSI", "SMA", "EMA", "MACD", "ADX", "ATR", "BB", "VIX",
    "WMA", "OBV", "VWAP", "CCI", "MFI", "ROC", "TRIX", "PSAR",
    "GTT", "OCO", "SL", "TP", "MP", "MIS", "CNC", "NRML",
    # Logical / order-noise words that get uppercased by accident
    "AND", "OR", "NOT", "IF", "WHEN", "THEN", "ELSE", "AT", "ON",
    "OF", "TO", "FROM", "IN", "IS", "AS",
    "BUY", "SELL", "PLACE", "SET", "ADD", "STOP", "LOSS",
    "AGENT", "STRATEGY", "WORKFLOW", "AUTOMATION", "ALERT",
    "MARKET", "LIMIT", "OPEN", "CLOSE", "HIGH", "LOW",
    "PRICE", "QUANTITY", "SHARES", "STOCK", "STOCKS",
    "ENTIRE", "FULL", "WHOLE", "ALL", "COMPLETE", "TOTAL", "EVERY",
    "HOLDING", "HOLDINGS", "POSITION", "POSITIONS",
})


_TICKER_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9\-_]{2,15}\b")


def _distinct_tickers_in(*texts: str) -> list[str]:
    """Return the distinct ticker-shaped tokens across all supplied
    strings, filtering out NSE/RSI/EMA/etc. that match the same regex
    but aren't tickers. Used by the multi-symbol guard below."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for txt in texts:
        if not txt:
            continue
        for m in _TICKER_TOKEN_RE.finditer(txt):
            tok = m.group(0).upper()
            if tok in _DSL_NON_TICKER_TOKENS:
                continue
            if tok in seen_set:
                continue
            seen_set.add(tok)
            seen.append(tok)
    return seen


_ACTION_VERB_RE = re.compile(
    r"\b(buy|buys|buying|sell|sells|selling|short|exit)\b",
    re.IGNORECASE,
)
# Tokens that interrupt a "buy A and B" sequence — once we hit one
# of these in the post-verb scan, we stop collecting tickers.
_ACTION_TERMINATORS_RE = re.compile(
    r"\b(when|if|whenever|while|at\s+(?:\d|the\s+open|the\s+close|"
    r"market\s+open|market\s+close|open|close)|on\s+(?:mon|tue|wed|"
    r"thu|fri|sat|sun)|every|after|before|until|till)\b",
    re.IGNORECASE,
)


def _has_multi_action_tickers(condition: str) -> bool:
    """True when the condition string contains 2+ distinct
    action-ticker pairs (the user is asking for orders on multiple
    symbols). False when only ONE action-ticker pair appears (a
    legitimate cross-symbol trigger, fine for DSL).

    Strategy: split on action verbs and within the action span
    (verb → end of clause / trigger word), collect ticker-shaped
    tokens. 2+ distinct in the action span = multi-action.

    Examples:
      "buy RELIANCE 10 and TCS 5 when RSI<30" → True  (2 actions)
      "buy 10 HDFCBANK when ICICIBANK drops 3%" → False (1 action)
      "sell my INFY and TCS at 3pm" → True (2 actions)
    """
    if not condition:
        return False
    msg = condition
    distinct: set[str] = set()
    for verb_match in _ACTION_VERB_RE.finditer(msg):
        start = verb_match.end()
        rest = msg[start: start + 200]
        # Trim at the first trigger word — "when ICICIBANK drops"
        # marks the end of the action span.
        term = _ACTION_TERMINATORS_RE.search(rest)
        action_span = rest[: term.start()] if term else rest
        for m in _TICKER_TOKEN_RE.finditer(action_span):
            tok = m.group(0).upper()
            if tok in _DSL_NON_TICKER_TOKENS:
                continue
            distinct.add(tok)
        if len(distinct) >= 2:
            return True
    return len(distinct) >= 2


# ── backtest_dsl_tree ────────────────────────────────────────────────


async def backtest_dsl_tree(args: dict) -> dict:
    """Run a DSL-tree backtest from a natural-language condition.

    Args (all required unless noted):
      condition         — NL trading ENTRY condition only. If the user
                          stated a sell/exit rule too, pass it as
                          ``exit_condition`` — never bake it into this
                          field as an AND, that produces a contradiction.
      primary_symbol    — symbol the trade fires on (e.g. "TCS")
      start_date        — ISO date (optional; defaults to 3y ago)
      end_date          — ISO date (optional; defaults to today)
      exit_condition    — Optional NL EXIT condition. When set, the
                          tool translates it to a DSL exit tree and
                          overrides the declarative exit_kind/bars/pct.
      exit_kind         — "n_day_hold" | "stop_loss_pct"
                          (default n_day_hold). Ignored when
                          exit_condition is set.
      exit_bars         — int, used when exit_kind=n_day_hold (default 10)
      exit_pct          — float in 0..1, used when exit_kind=stop_loss_pct
      starting_capital  — ₹, default 100_000
      quantity          — shares per fire, default 10
    """
    args = args or {}
    condition = (args.get("condition") or "").strip()
    primary = (args.get("primary_symbol") or "").strip().upper()
    exit_condition_text = (args.get("exit_condition") or "").strip()
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
            condition,
            primary_symbol=primary,
            cache_key="dsl.chat.backtest.v1",
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

    # Exit policy — exit_condition (NL) wins over declarative fields
    # so a chat prompt like "buy on RSI<30, sell on RSI>70" gets a
    # real tree exit and not a degenerate AND.
    exit_tx_meta: Optional[dict] = None
    if exit_condition_text:
        try:
            exit_tree_dict, exit_tx_meta = await translate_condition_to_tree(
                exit_condition_text,
                allow_position=True,
                primary_symbol=primary,
                cache_key="dsl.chat.backtest.exit.v1",
            )
        except TranslationError as exc:
            raise ValueError(
                f"could not translate exit_condition into a DSL tree: "
                f"{exc}"
            ) from None
        exit_policy = {
            "kind": "tree",
            "tree": exit_tree_dict,
            "exit_at": "next_open",
        }
    else:
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
        "exit_translation_meta": exit_tx_meta,
    }


# ── propose_dsl_workflow ────────────────────────────────────────────


async def propose_dsl_workflow(args: dict) -> dict:
    """Build a workflow draft whose entry trigger is a DSL
    ``trigger.compound`` tree, with an optional exit branch driven by
    ``trigger.exit_compound`` for runtime-position-aware exits.

    Args:
      condition         — NL entry condition (required)
      name              — short human label for the workflow
      primary_symbol    — symbol the action targets (required)
      action_kind       — "notify_only" (default) | "buy_market" | "buy_limit"
      quantity          — int, only used when action_kind starts with 'buy'
      limit_price       — float, only used when action_kind=buy_limit
      exit_condition    — OPTIONAL NL exit condition. When set, the tool
                          translates it to a DSL tree (with PositionNode
                          leaves allowed — entry_price, unrealised_pct,
                          bars_held, drawdown_from_peak_pct, ...) and
                          emits a SECOND branch:
                              trigger.exit_compound + fetch.portfolio +
                              action.place_order(sell, qty=runtime ref).
                          Use for prompts like "buy X when RSI<30, sell
                          when price > upper Bollinger band" or "exit
                          when drawdown from peak >= 5%".
    """
    args = args or {}
    condition = (args.get("condition") or "").strip()
    primary = (args.get("primary_symbol") or "").strip().upper()
    label = (args.get("name") or "").strip() or f"{primary} compound trigger"
    action_kind = (args.get("action_kind") or "notify_only").lower()
    exit_condition_text = (args.get("exit_condition") or "").strip()
    # Normalize "no exit" placeholders the LLM occasionally emits when
    # there isn't an exit condition. Without this, the translator tries
    # to translate the placeholder and produces a vacuous tree
    # (1.0 == 1.0) → "translated exit tree is invalid" error.
    if exit_condition_text.lower() in {
        "none", "null", "n/a", "na", "no exit", "no", "—", "-",
    }:
        exit_condition_text = ""
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

    # ── Multi-symbol guard ────────────────────────────────────────
    # propose_dsl_workflow is SINGLE-ACTION-SYMBOL: one entry trigger
    # fires actions on the primary symbol, optionally with one exit
    # branch on the same symbol.
    #
    # Two failure shapes to distinguish:
    #
    # (1) Multi-ACTION ticker — "buy RELIANCE, TCS and BAJFINANCE when
    #     they drop 2% from open". The user expects orders on ALL named
    #     symbols. The DSL would silently use only the primary, dropping
    #     the others. → refuse and route to propose_workflow.
    #
    # (2) Cross-symbol TRIGGER — "buy 10 HDFCBANK when ICICIBANK drops
    #     3% intraday". The user expects ONE action (HDFCBANK) gated by
    #     a condition on a different symbol (ICICIBANK). The DSL's
    #     PriceLeaf / IndicatorLeaf grammar accepts arbitrary symbols
    #     on leaves, so this IS supported. Refusing here forces the LLM
    #     into prose and disappoints the user.
    #
    # Heuristic: only fire the guard when MULTIPLE distinct tickers
    # appear immediately AFTER an action verb (buy/sell). A single
    # action ticker + condition tickers elsewhere is fine.
    tickers = _distinct_tickers_in(condition, exit_condition_text)
    extras = [t for t in tickers if t != primary]
    if len(extras) >= 1 and _has_multi_action_tickers(condition):
        all_named = sorted(set([primary] + tickers))
        raise ValueError(
            f"propose_dsl_workflow is single-symbol but the condition "
            f"names multiple tickers in the ACTION position "
            f"({', '.join(all_named)}). The DSL can only build a "
            f"workflow for ONE primary symbol — use propose_workflow "
            f"instead with one branch per (symbol × action), or call "
            f"this tool once per ticker."
        )

    try:
        tree, tx_meta = await translate_condition_to_tree(
            condition,
            primary_symbol=primary,
            cache_key="dsl.chat.propose.v1",
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

    # ── Optional exit-tree translation (allow_position=True) ──
    exit_tree = None
    exit_tx_meta = None
    exit_readback = None
    if exit_condition_text:
        try:
            exit_tree, exit_tx_meta = await translate_condition_to_tree(
                exit_condition_text,
                allow_position=True,
                primary_symbol=primary,
                cache_key="dsl.chat.propose.exit.v1",
            )
        except TranslationError as exc:
            raise ValueError(
                f"could not translate exit_condition into a DSL tree: {exc}"
            ) from None
        try:
            parsed_exit = TypeAdapter(Tree).validate_python(exit_tree)
            semantic_validate(parsed_exit, allow_position=True)
        except (DSLValidationError, ValidationError) as exc:
            raise ValueError(
                f"translated exit tree is invalid: {exc}"
            ) from None
        exit_readback = tree_to_english(parsed_exit)

    # Build the entry action step. For v1 we only support three:
    #   notify_only  → notify.message (push channel)
    #   buy_market   → action.place_order(side=buy, order_type=market)
    #   buy_limit    → action.place_order(side=buy, order_type=limit)
    if action_kind not in ("notify_only", "buy_market", "buy_limit"):
        action_kind = "notify_only"

    # Refuse silent qty=1 default for buy actions. The user must
    # have specified a quantity (the LLM should have asked first).
    # notify_only is exempt because no order is placed.
    raw_qty = args.get("quantity")
    if action_kind in ("buy_market", "buy_limit"):
        if raw_qty is None or (isinstance(raw_qty, (int, float)) and int(raw_qty) <= 0):
            raise ValueError(
                "propose_dsl_workflow: 'quantity' is required when "
                f"action_kind='{action_kind}'. Call ASK_USER first: "
                "'How many shares per fire?'. Do NOT default to 1 — "
                "silent defaults have produced wrong-size trades."
            )
    qty = int(raw_qty) if raw_qty is not None else 1
    limit_px = args.get("limit_price")

    if action_kind == "notify_only":
        entry_action = {
            "step_type": "notify.message",
            "config": {
                "channel": "push",
                "message": (
                    f"{label} fired — entry condition: {readback}"
                ),
            },
        }
    elif action_kind == "buy_market":
        entry_action = {
            "step_type": "action.place_order",
            "config": {
                "symbol": primary,
                "side": "buy",
                "quantity": qty,
                "order_type": "market",
                "product": "CNC",
            },
        }
    else:   # buy_limit
        if limit_px is None:
            raise ValueError(
                "buy_limit action requires 'limit_price'"
            )
        entry_action = {
            "step_type": "action.place_order",
            "config": {
                "symbol": primary,
                "side": "buy",
                "quantity": qty,
                "order_type": "limit",
                "limit_price": float(limit_px),
                "product": "CNC",
            },
        }

    # ── Assemble steps[] ──
    steps: list[dict] = [
        {
            "step_type": "trigger.compound",
            "config": {
                "entry": tree,
                "symbol": primary,
                "exchange": "NSE",
            },
        },
        entry_action,
    ]

    # Optional exit branch — only if an exit_condition was supplied AND
    # the entry actually opens a position (notify_only has nothing to
    # exit, so skip the exit branch in that case).
    if exit_tree is not None and action_kind != "notify_only":
        exit_trigger_idx = len(steps)
        fetch_portfolio_idx = exit_trigger_idx + 1
        steps.extend([
            {
                "step_type": "trigger.exit_compound",
                "config": {
                    "entry": exit_tree,
                    "target_symbol": primary,
                },
            },
            {
                "step_type": "fetch.portfolio",
                "config": {},
            },
            {
                "step_type": "action.place_order",
                "config": {
                    "symbol": primary,
                    "side": "sell",
                    # Runtime reference — sell whatever quantity is
                    # currently held in this symbol. fetch.portfolio
                    # populated it at index `fetch_portfolio_idx`.
                    "quantity": (
                        "{{ context." + str(fetch_portfolio_idx)
                        + ".holdings." + primary + ".quantity }}"
                    ),
                    "order_type": "market",
                    "product": "CNC",
                },
            },
        ])

    description = f"Entry: {readback}"
    if exit_readback:
        description += f" · Exit: {exit_readback}"

    valid_until_raw = (args.get("valid_until") or "").strip() or None
    draft = {
        "_render_hint": "workflow_draft_card",
        "draft_id": str(uuid.uuid4()),
        "name": label,
        "description": description,
        "steps": steps,
        "readback": readback,
        "exit_readback": exit_readback,
        "translation_meta": tx_meta,
        "exit_translation_meta": exit_tx_meta,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    if valid_until_raw:
        draft["valid_until"] = valid_until_raw
    # R4a: pre-flight backtest resolvability so the FE knows whether
    # to surface the Backtest button — and so the runtime float-cast
    # error never fires for an unresolvable Mustache ref.
    try:
        from backend.services.backtest_resolvability import check_draft
        bt_ok, bt_blockers = check_draft(steps)
        draft["backtestable"] = bool(bt_ok)
        draft["backtest_blockers"] = bt_blockers
    except Exception:
        draft["backtestable"] = True
        draft["backtest_blockers"] = []
    # R4b follow-up: derive expires_at from valid_until in one place.
    try:
        from backend.agents.tool_executor import _stamp_expires_at
        _stamp_expires_at(draft)
    except Exception:
        pass
    return draft
