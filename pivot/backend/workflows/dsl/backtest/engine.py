"""Backtest engine — bar-by-bar walk over the master calendar.

Contract (mirrors the existing backend/backtester/engine.py
no-lookahead rules):

  - Tree evaluated on the close of bar ``i`` → entry at bar
    ``i + 1``'s OPEN. If ``i + 1`` is past the end of the calendar,
    the signal is dropped (no synthetic out-of-window fills).

  - Exit policy is a DSL tree (`ExitPolicyTree`). The declarative
    shapes (`stop_loss_pct`, `n_day_hold`) are lowered to trees on
    request entry — there is exactly one exit-evaluation code path.

  - ``stop_loss_pct``-shaped trees use BAR-LOW semantics: they fire
    when the bar's low is at-or-below the stop level, and the
    position fills at the stop price itself (the realistic Indian
    retail SL behaviour, not the next bar's open).

  - All other exit shapes fill at the next bar's OPEN by default,
    matching the entry-tree fill semantic. ``exit_at='current_close'``
    is available for users who want immediate close-of-bar fills.

  - If a position is still open at the last bar, it's force-closed
    at the last bar's close.

Bills the trade through ``buy_cost`` + ``sell_cost`` from
``backend.backtester.engine`` so the cost model is shared with the
legacy backtester. **DO NOT duplicate the cost constants** — when
the existing path updates Zerodha brokerage or STT, this engine
inherits the change for free.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date as date_cls, datetime, timezone
from typing import Any, Optional

import pandas as pd
from pydantic import TypeAdapter

from backend.workflows.dsl.backtest.bar_loader import LoadedBars, load_bars
from backend.workflows.dsl.backtest.data_accessor import BacktestDataAccessor
from backend.workflows.dsl.backtest.schema import (
    BacktestDiagnostics,
    BacktestMetrics,
    BacktestRequest,
    BacktestResult,
    EquityPoint,
    ExitPolicyTree,
    TradeRow,
    lower_exit_policy,
)
from backend.workflows.dsl.evaluator import Ternary, evaluate
from backend.workflows.dsl.readback import tree_to_english
from backend.workflows.dsl.schema import Tree
from backend.workflows.dsl.validators import semantic_validate

logger = logging.getLogger(__name__)


_TREE_ADAPTER = TypeAdapter(Tree)


# ── Public entry point ──────────────────────────────────────────────


def run_backtest(
    *,
    request: BacktestRequest,
    user_id: int,
    fetcher=None,
) -> BacktestResult:
    """Synchronous run. The router wraps this in ``asyncio.to_thread``.

    ``fetcher`` is the OHLCV fetcher passed through to ``bar_loader``;
    leave ``None`` in production (uses yfinance). Tests inject a
    fixed-data fetcher.
    """
    requested_at = datetime.now(timezone.utc)

    # Validate the entry tree at the engine boundary — same shape the
    # registry validator uses. Belt-and-suspenders for direct
    # invocations.
    tree = _TREE_ADAPTER.validate_python(request.tree)
    semantic_validate(tree)

    # Lower the exit policy into the canonical tree shape so the
    # simulation loop only knows about one exit mechanism. The
    # declarative shapes (stop_loss_pct / n_day_hold) get rewritten
    # into trees over the ``position`` leaf.
    exit_policy = lower_exit_policy(request.exit_policy)
    exit_tree = _TREE_ADAPTER.validate_python(exit_policy.tree)
    semantic_validate(exit_tree, allow_position=True)

    loaded = load_bars(
        tree, start=request.start_date, end=request.end_date, fetcher=fetcher,
    )

    primary_key = (request.primary_symbol, request.exchange)
    if primary_key not in loaded.by_symbol:
        raise ValueError(
            f"primary_symbol {request.primary_symbol!r} not in tree — "
            f"add a node referencing it or pick a symbol the tree uses"
        )
    primary_bars = loaded.by_symbol[primary_key]

    warmup_idx = _compute_warmup_idx(tree, len(loaded.master_dates))

    state = _SimState(
        request=request,
        loaded=loaded,
        primary_bars=primary_bars,
        warmup_idx=warmup_idx,
        exit_policy=exit_policy,
    )
    _simulate(tree, exit_tree, state)

    metrics = _metrics_from_sim(state)
    diagnostics = _diagnostics_from_sim(state, loaded)

    completed_at = datetime.now(timezone.utc)
    return BacktestResult(
        request_id=str(uuid.uuid4()),
        user_id=user_id,
        requested_at=requested_at,
        completed_at=completed_at,
        tree_summary=tree_to_english(tree),
        request=request,
        trades=state.trades,
        equity_curve=state.equity_curve,
        metrics=metrics,
        diagnostics=diagnostics,
    )


# ── Simulation core ─────────────────────────────────────────────────


class _SimState:
    """Mutable bookkeeping for the simulation loop. Public attrs
    only — no methods, the loop manipulates fields directly."""

    def __init__(
        self,
        *,
        request: BacktestRequest,
        loaded: LoadedBars,
        primary_bars: pd.DataFrame,
        warmup_idx: int,
        exit_policy: ExitPolicyTree,
    ) -> None:
        self.request = request
        self.loaded = loaded
        self.primary_bars = primary_bars
        self.warmup_idx = warmup_idx
        self.exit_policy = exit_policy
        self.cash = float(request.starting_capital)
        self.position: Optional[_OpenPos] = None
        self.trades: list[TradeRow] = []
        self.equity_curve: list[EquityPoint] = []
        self.next_trade_id: int = 1
        self.fire_bars: int = 0
        self.unknown_bars: int = 0
        self.bars_evaluated: int = 0


class _OpenPos:
    """The single in-flight position. Phase B is one-symbol-at-a-
    time. Tracks ``peak_unrealised_pct`` for trailing-stop semantics.
    """

    __slots__ = (
        "entry_idx", "entry_date", "entry_price", "qty", "costs",
        "trade_id", "peak_unrealised_pct",
    )

    def __init__(
        self, *, entry_idx: int, entry_date: date_cls, entry_price: float,
        qty: int, costs: float, trade_id: int,
    ) -> None:
        self.entry_idx = entry_idx
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.qty = qty
        self.costs = costs
        self.trade_id = trade_id
        self.peak_unrealised_pct = 0.0


class _PositionAwareAccessor:
    """Decorator over the bar accessor that resolves the ``position``
    leaf. Owns no data of its own — delegates every non-position
    method back to the wrapped accessor so the same indicator cache
    is reused across exit-tree and entry-tree evaluation."""

    def __init__(
        self,
        inner: BacktestDataAccessor,
        position: "_OpenPos",
        primary_bars: pd.DataFrame,
        as_of_idx: int,
    ) -> None:
        self._inner = inner
        self._pos = position
        self._bars = primary_bars
        self._idx = as_of_idx

    # ── DataAccessor protocol passthroughs ─────────────────────────

    def get_price(self, **kw):
        return self._inner.get_price(**kw)

    def get_indicator(self, **kw):
        return self._inner.get_indicator(**kw)

    def get_volume(self, **kw):
        return self._inner.get_volume(**kw)

    # ── position leaf ──────────────────────────────────────────────

    def get_position_field(
        self, *, field: str, basis: Optional[str] = None,
    ) -> Optional[float]:
        if field == "entry_price":
            return float(self._pos.entry_price)
        if field == "bars_held":
            return float(self._idx - self._pos.entry_idx)
        if field == "peak_unrealised_pct":
            return float(self._pos.peak_unrealised_pct)
        if field == "drawdown_from_peak_pct":
            cur = self._unrealised_pct("close")
            if cur is None:
                return None
            return max(0.0, self._pos.peak_unrealised_pct - cur)
        if field == "unrealised_pct":
            return self._unrealised_pct(basis or "close")
        if field == "unrealised_abs":
            ref = self._bar_price(basis or "close")
            if ref is None:
                return None
            return float(ref - self._pos.entry_price)
        return None

    def _unrealised_pct(self, basis: str) -> Optional[float]:
        ref = self._bar_price(basis)
        if ref is None or self._pos.entry_price <= 0:
            return None
        return float((ref - self._pos.entry_price) / self._pos.entry_price)

    def _bar_price(self, basis: str) -> Optional[float]:
        if basis == "close":
            return _safe_close(self._bars, self._idx)
        if basis == "low":
            v = self._bars["low"].iloc[self._idx]
            return None if pd.isna(v) else float(v)
        if basis == "high":
            v = self._bars["high"].iloc[self._idx]
            return None if pd.isna(v) else float(v)
        return None


def _simulate(tree, exit_tree, st: _SimState) -> None:
    """The hot loop. Single pass over the master calendar."""
    accessor = BacktestDataAccessor(st.loaded)
    entry_state: dict[str, float] = {}
    exit_state: dict[str, float] = {}
    total_bars = len(st.loaded.master_dates)
    if total_bars <= st.warmup_idx + 1:
        return   # not enough bars to do anything meaningful

    # Lazy import to keep the cost model coupled to the existing
    # backtester package rather than duplicating constants.
    from backend.backtester.engine import buy_cost, sell_cost

    for idx in range(total_bars):
        bar_date = _idx_date(st.loaded.master_dates, idx)
        close = _safe_close(st.primary_bars, idx)
        _record_equity(st, bar_date, close)

        if idx < st.warmup_idx:
            continue

        accessor.advance_to(idx)
        ev = evaluate(tree, accessor=accessor, prev_state=entry_state)
        entry_state = ev.new_state
        st.bars_evaluated += 1
        if ev.value is Ternary.UNKNOWN:
            st.unknown_bars += 1
        elif ev.value is Ternary.TRUE:
            st.fire_bars += 1

        # ── Exit checks come BEFORE entry checks, so the same bar
        #    can both close one position and consider opening a new
        #    one. Exits run through the exit_tree, evaluated with a
        #    position-aware accessor wrapper. ──
        if st.position is not None:
            _update_peak(st.position, st.primary_bars, idx)
            pos_accessor = _PositionAwareAccessor(
                accessor, st.position, st.primary_bars, idx,
            )
            xv = evaluate(
                exit_tree, accessor=pos_accessor, prev_state=exit_state,
            )
            exit_state = xv.new_state
            if xv.value is Ternary.TRUE:
                _close_via_policy(
                    st, signal_idx=idx, sell_cost_fn=sell_cost,
                )

        # ── Entry: signal evaluated on current bar's close →
        #    open at next bar's open. ──
        if (
            st.position is None
            and ev.value is Ternary.TRUE
            and idx + 1 < total_bars
        ):
            _open_position(st, idx + 1, buy_cost)
            # New position opens fresh — clear stale exit-state for
            # crossings that referenced the previous position.
            exit_state = {}

    # End-of-window force close.
    if st.position is not None:
        last_idx = total_bars - 1
        last_close = _safe_close(st.primary_bars, last_idx)
        if last_close is not None:
            _close_position_at_price(
                st, exit_idx=last_idx, exit_price=last_close,
                exit_reason="force_close", sell_cost_fn=sell_cost,
            )


def _update_peak(
    pos: "_OpenPos", bars: pd.DataFrame, idx: int,
) -> None:
    """Walk the running maximum of (high - entry) / entry. We use the
    bar HIGH rather than close so trailing stops latch onto the
    intra-bar peak."""
    h = bars["high"].iloc[idx]
    if pd.isna(h):
        return
    if pos.entry_price <= 0:
        return
    cur = (float(h) - pos.entry_price) / pos.entry_price
    if cur > pos.peak_unrealised_pct:
        pos.peak_unrealised_pct = cur


def _close_via_policy(
    st: _SimState, *, signal_idx: int, sell_cost_fn,
) -> None:
    """Apply the exit policy's ``exit_at`` selector to pick the fill
    price, then close the position."""
    policy = st.exit_policy
    pos = st.position
    if pos is None:
        return

    if policy.exit_at == "stop_price" and policy.stop_price_pct is not None:
        # Lowered stop_loss_pct: fill at exactly the stop price on
        # the current bar (realistic Indian-retail SL semantic).
        exit_price = pos.entry_price * (1.0 - float(policy.stop_price_pct))
        _close_position_at_price(
            st, exit_idx=signal_idx, exit_price=exit_price,
            exit_reason="stop_loss", sell_cost_fn=sell_cost_fn,
        )
        return

    if policy.exit_at == "current_close":
        cur_close = _safe_close(st.primary_bars, signal_idx)
        if cur_close is None:
            return
        _close_position_at_price(
            st, exit_idx=signal_idx, exit_price=cur_close,
            exit_reason=_exit_reason_for_policy(policy),
            sell_cost_fn=sell_cost_fn,
        )
        return

    # Default ``next_open``: fill at the next bar's open. Falls back
    # to current close at end-of-window — same edge-case behaviour
    # as the previous engine.
    _close_position(
        st, signal_idx,
        _exit_reason_for_policy(policy),
        sell_cost_fn,
    )


def _exit_reason_for_policy(policy: ExitPolicyTree) -> str:
    """Map an exit policy back to the categorical reason recorded
    on each trade. The two declarative shapes keep their original
    labels; user-written trees record as ``exit_tree``."""
    if policy.exit_at == "stop_price":
        return "stop_loss"
    if policy.stop_price_pct is not None:
        return "stop_loss"
    # Lowered n_day_hold has no stop_price_pct and exit_at='next_open'
    # → fall through to either n_day_hold (its lowering form) or
    # exit_tree (user-written). We can't reliably tell the two apart
    # from the policy alone, so we use a structural sniff on the
    # tree.
    t = policy.tree
    if (isinstance(t, dict)
            and t.get("op") == ">="
            and isinstance(t.get("left"), dict)
            and t["left"].get("type") == "position"
            and t["left"].get("field") == "bars_held"):
        return "n_day_hold"
    return "exit_tree"


# ── Helpers ─────────────────────────────────────────────────────────


def _idx_date(dates: pd.DatetimeIndex, idx: int) -> date_cls:
    return dates[idx].to_pydatetime().date()


def _safe_close(df: pd.DataFrame, idx: int) -> Optional[float]:
    v = df["close"].iloc[idx]
    return None if pd.isna(v) else float(v)


def _safe_open(df: pd.DataFrame, idx: int) -> Optional[float]:
    v = df["open"].iloc[idx]
    return None if pd.isna(v) else float(v)


def _record_equity(
    st: _SimState, bar_date: date_cls, current_close: Optional[float],
) -> None:
    if st.position is None or current_close is None:
        equity = st.cash
    else:
        equity = st.cash + current_close * st.position.qty
    st.equity_curve.append(EquityPoint(date=bar_date, equity=equity))


def _open_position(st: _SimState, entry_idx: int, buy_cost_fn) -> None:
    """Open at entry_idx's OPEN price. Skips silently if the bar's
    open is NaN (rare — typically a feed gap)."""
    open_px = _safe_open(st.primary_bars, entry_idx)
    if open_px is None:
        return
    qty = int(st.request.quantity)
    net_debit, charges = buy_cost_fn(open_px, qty)
    if st.cash < net_debit:
        # Not enough capital — skip; don't open a leveraged position.
        # Future improvement: record as "skipped, insufficient capital".
        return
    st.cash -= net_debit
    st.position = _OpenPos(
        entry_idx=entry_idx,
        entry_date=_idx_date(st.loaded.master_dates, entry_idx),
        entry_price=open_px,
        qty=qty,
        costs=charges,
        trade_id=st.next_trade_id,
    )
    st.next_trade_id += 1


def _close_position(
    st: _SimState, signal_idx: int, exit_reason: str, sell_cost_fn,
) -> None:
    """Close at the bar AFTER ``signal_idx`` opens. Falls back to the
    current bar's close if there's no next bar."""
    total_bars = len(st.loaded.master_dates)
    exit_idx = signal_idx + 1
    exit_price = None
    if exit_idx < total_bars:
        exit_price = _safe_open(st.primary_bars, exit_idx)
    if exit_price is None:
        # Fall back to current bar's close (end-of-window edge case).
        exit_idx = signal_idx
        exit_price = _safe_close(st.primary_bars, exit_idx)
    if exit_price is None:
        return  # bar is fully NaN; skip
    _close_position_at_price(
        st, exit_idx=exit_idx, exit_price=exit_price,
        exit_reason=exit_reason, sell_cost_fn=sell_cost_fn,
    )


def _close_position_at_price(
    st: _SimState, *, exit_idx: int, exit_price: float,
    exit_reason: str, sell_cost_fn,
) -> None:
    assert st.position is not None
    pos = st.position
    net_credit, sell_charges = sell_cost_fn(exit_price, pos.qty)
    st.cash += net_credit
    gross_pnl = (exit_price - pos.entry_price) * pos.qty
    total_costs = pos.costs + sell_charges
    net_pnl = gross_pnl - total_costs
    return_pct = (
        (exit_price - pos.entry_price) / pos.entry_price
        if pos.entry_price > 0 else 0.0
    )
    st.trades.append(TradeRow(
        trade_id=pos.trade_id,
        symbol=st.request.primary_symbol,
        entry_date=pos.entry_date,
        entry_price=pos.entry_price,
        exit_date=_idx_date(st.loaded.master_dates, exit_idx),
        exit_price=exit_price,
        quantity=pos.qty,
        gross_pnl=gross_pnl,
        costs=total_costs,
        net_pnl=net_pnl,
        return_pct=return_pct,
        exit_reason=exit_reason,  # type: ignore[arg-type]
    ))
    st.position = None


# ── Warmup ─────────────────────────────────────────────────────────


def _compute_warmup_idx(tree, total_bars: int) -> int:
    """How many bars to skip before the first signal evaluation.

    Picks the max indicator period + 5 OR an aggregator's lookback
    window, whichever is larger. Aggregators that read N bars of
    history obviously can't fire before bar N. Offset on any leaf
    contributes too — an indicator with offset=20 needs bar 20 + its
    period in history. Floor of 20 keeps short trees safe."""
    from backend.workflows.dsl.schema import (
        AggregateNode, GapNode, IndicatorNode, PctChangeNode,
        PriceNode, VolumeNode,
    )
    from backend.workflows.dsl.validators import _walk_all

    max_need = 0
    for n in _walk_all(tree):
        if isinstance(n, IndicatorNode):
            max_need = max(max_need, int(n.period) + 5 + int(n.offset or 0))
        elif isinstance(n, PriceNode):
            max_need = max(max_need, int(n.offset or 0))
        elif isinstance(n, VolumeNode):
            max_need = max(max_need, int(n.bars) + int(n.offset or 0))
        elif isinstance(n, AggregateNode):
            # Aggregator needs `bars` of prior history for its window.
            max_need = max(max_need, int(n.bars))
        elif isinstance(n, GapNode):
            # gap reads bar 0 (open) and bar -1 (prev close).
            max_need = max(max_need, 1)
        elif isinstance(n, PctChangeNode):
            max_need = max(max_need, int(n.bars))
    floor = 20
    needed = max(max_need, floor)
    return min(needed, max(0, total_bars - 2))


# ── Metrics + diagnostics extraction ───────────────────────────────


def _metrics_from_sim(st: _SimState) -> BacktestMetrics:
    starting = float(st.request.starting_capital)
    ending = st.equity_curve[-1].equity if st.equity_curve else starting
    total_ret = (ending - starting) / starting if starting > 0 else 0.0

    # CAGR over the calendar span
    if len(st.equity_curve) >= 2 and starting > 0 and ending > 0:
        days = (
            st.equity_curve[-1].date - st.equity_curve[0].date
        ).days or 1
        years = max(days / 365.25, 1.0 / 365.25)
        cagr = (ending / starting) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    # Max drawdown
    max_dd, max_dd_days = _max_drawdown(st.equity_curve)

    total_trades = len(st.trades)
    wins = [t for t in st.trades if t.net_pnl > 0]
    losses = [t for t in st.trades if t.net_pnl <= 0]
    avg_win = (
        sum(t.return_pct for t in wins) / len(wins) if wins else None
    )
    avg_loss = (
        sum(t.return_pct for t in losses) / len(losses) if losses else None
    )
    profit_factor = _profit_factor(st.trades)

    return BacktestMetrics(
        total_return_pct=total_ret * 100.0,
        cagr_pct=cagr * 100.0,
        max_drawdown_pct=max_dd * 100.0,
        max_drawdown_duration_days=max_dd_days,
        win_rate_pct=(len(wins) / total_trades * 100.0) if total_trades else 0.0,
        total_trades=total_trades,
        winning_trades=len(wins),
        losing_trades=len(losses),
        average_win_pct=avg_win * 100.0 if avg_win is not None else None,
        average_loss_pct=avg_loss * 100.0 if avg_loss is not None else None,
        profit_factor=profit_factor,
        sharpe_ratio=None,   # Phase B+1
        sortino_ratio=None,  # Phase B+1
        ending_value=ending,
    )


def _max_drawdown(curve: list[EquityPoint]) -> tuple[float, int]:
    """Return (max_dd_pct as fraction, duration_in_days)."""
    if not curve:
        return 0.0, 0
    peak = curve[0].equity
    peak_date = curve[0].date
    max_dd = 0.0
    max_dur = 0
    for pt in curve:
        if pt.equity > peak:
            peak = pt.equity
            peak_date = pt.date
        dd = (peak - pt.equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            max_dur = (pt.date - peak_date).days
    return max_dd, max_dur


def _profit_factor(trades: list[TradeRow]) -> Optional[float]:
    gross_wins = sum(t.net_pnl for t in trades if t.net_pnl > 0)
    gross_losses = -sum(t.net_pnl for t in trades if t.net_pnl < 0)
    if gross_losses <= 0:
        return None
    return gross_wins / gross_losses


def _diagnostics_from_sim(
    st: _SimState, loaded: LoadedBars,
) -> BacktestDiagnostics:
    return BacktestDiagnostics(
        bars_evaluated=st.bars_evaluated,
        warmup_bars_skipped=st.warmup_idx,
        unknown_value_bars=st.unknown_bars,
        fire_bars=st.fire_bars,
        symbols_loaded=[f"{s}:{e}" for (s, e) in loaded.by_symbol.keys()],
        indicator_cache_keys=[],   # populated by the accessor; future hook
    )
