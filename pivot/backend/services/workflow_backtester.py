"""Backtest a Pivot workflow draft against historical price data.

The user sees an agent draft in chat → clicks "Backtest this" → we run
the workflow's logic over historical daily bars and surface the same
chart card the indicator backtester uses (equity curve + signals +
metrics). Confirms, before activating, that the strategy actually
made money historically.

Pipeline
========

  1. ``check_eligibility(steps)`` — pure-Python parse of the draft
     into branches. Returns ``Eligibility(eligible, reason, branches)``.

     A workflow is *eligible* when every trigger is one of
     ``trigger.schedule`` / ``trigger.indicator`` / ``trigger.price``
     and every action is ``action.place_order``. Notify / wait steps
     are silently skipped during simulation. Anything else
     (``trigger.event``, ``trigger.webhook``, ``action.cancel_orders``)
     marks the workflow not-eligible with a specific reason the FE
     surfaces verbatim.

  2. ``backtest_workflow(steps, period='5y')`` — runs the simulation.

     For each branch, generates the fire-time series:
       - schedule:  cron expansion over the period
       - indicator: bar dates where the threshold crossing happens
       - price:     bar dates where HIGH ≥ value (up) or LOW ≤ value

     Events from all branches are merged + sorted by date. The walker
     iterates the merged series and for each event:

       a. Walks the branch's body steps in order (fetch.* / condition.* /
          action.*). condition.numeric resolves Mustache refs against
          the simulator's current state.
       b. ``action.place_order`` simulates a fill at the day's OPEN
          price (the realistic execution proxy for an order placed
          on a daily-close signal).
       c. Holdings + cash + average buy price are updated; trades and
          signals are recorded for the chart.

  3. Returns ``IndicatorBacktestResult`` so the existing FE chart
     card renders without needing a new component.

Limitations (call out in the FE):
  * Daily granularity. "Buy at 09:15 IST" is approximated as the day's
    OPEN — fine for SIPs, off by a few % for high-vol intraday
    strategies.
  * 5y default period. yfinance has more, but 5y matches the indicator
    backtest default and 99% of retail use cases.
  * Slippage + brokerage = 10 bps per side, same as the indicator
    backtester. Realistic for retail equity.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd  # type: ignore[import-untyped]
import pandas_ta_classic as ta  # type: ignore[import-untyped]
import yfinance as yf  # type: ignore[import-untyped]
from pytz import timezone as pytz_timezone  # type: ignore[import-untyped]

from backend.services.indicator_backtest import IndicatorBacktestResult


logger = logging.getLogger(__name__)


_FRICTION = 0.001
_STARTING_CAPITAL = 1_000_000.0


# ── Eligibility ──────────────────────────────────────────────────────


# Trigger types we can replay against historical bars.
_BACKTESTABLE_TRIGGERS = {
    "trigger.schedule",
    "trigger.indicator",
    "trigger.price",
}

# Action types we can simulate. action.place_order is the only one that
# moves money in v1; everything else is silently skipped or marks the
# workflow partial.
_BACKTESTABLE_ACTIONS = {"action.place_order"}

# Steps we silently skip during backtest (no historical effect).
_SKIPPABLE_STEPS = {
    "notify.message",
    "notify.log",
    "wait.approval",
    "wait.delay",
    "fetch.portfolio",   # synthesised from sim state when condition refs ask for it
    "fetch.quote",       # synthesised from the day's bar
    "fetch.indicator",   # computed on demand if a condition references its output
    "condition.market_status",
    "condition.time_window",
    "condition.position",
}

# Steps that block backtesting entirely.
_BLOCKING_STEPS_REASON = {
    "trigger.event":   "trigger.event needs an historical event calendar we don't keep — try a schedule or indicator trigger.",
    "trigger.webhook": "trigger.webhook can only fire from external traffic, so there's nothing historical to replay.",
    "trigger.manual":  "trigger.manual fires when you click 'Run now' — there's no historical signal to replay.",
    "fetch.fundamental": "fetch.fundamental needs the financials DB, not yfinance prices. Use the /expr-backtest path for fundamentals strategies.",
    "fetch.news":      "fetch.news depends on real-time feed history we don't store.",
}


@dataclass
class Branch:
    """One trigger-rooted branch within a workflow.

    ``body`` is the list of step dicts after the trigger and up to the
    next trigger (or end of workflow). The simulator walks these on
    each fire."""
    trigger_step_index: int
    trigger_type: str
    trigger_config: dict[str, Any]
    body: list[dict[str, Any]]

    def primary_symbol(self) -> Optional[str]:
        """Return the stock symbol most likely targeted by this branch.
        Looks first at the trigger's ``symbol`` config (price /
        indicator) then at the first place_order step's symbol."""
        sym = self.trigger_config.get("symbol")
        if isinstance(sym, str) and sym.strip():
            return sym.upper().strip()
        for step in self.body:
            if step.get("step_type") == "action.place_order":
                cfg = step.get("config") or {}
                s = cfg.get("symbol")
                if isinstance(s, str) and s.strip():
                    return s.upper().strip()
        return None


@dataclass
class Eligibility:
    eligible: bool
    reason: Optional[str] = None
    branches: list[Branch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _parse_branches(steps: list[dict[str, Any]]) -> list[Branch]:
    """Slice a step list into ``Branch`` objects on every trigger.*."""
    out: list[Branch] = []
    current: Optional[Branch] = None
    for s in steps:
        st = str(s.get("step_type") or "")
        if st.startswith("trigger."):
            if current is not None:
                out.append(current)
            current = Branch(
                trigger_step_index=int(s.get("step_index", len(out))),
                trigger_type=st,
                trigger_config=dict(s.get("config") or {}),
                body=[],
            )
        else:
            if current is None:
                # Step before any trigger — workflow shape error; let the
                # validator catch it. We just stop slicing here.
                continue
            current.body.append(s)
    if current is not None:
        out.append(current)
    return out


def check_eligibility(steps: list[dict[str, Any]]) -> Eligibility:
    """Pure-Python: can this workflow be backtested?

    Returns ``eligible=False`` with a user-readable ``reason`` if any
    step is fundamentally non-replayable (event / webhook / fundamentals
    fetch). Returns ``eligible=True`` with parsed ``branches`` and a
    list of ``warnings`` (e.g. partial-fidelity action types) otherwise.
    """
    if not steps:
        return Eligibility(False, "Workflow has no steps.")

    # Hard blockers (one bad step → not backtestable).
    for s in steps:
        st = str(s.get("step_type") or "")
        if st in _BLOCKING_STEPS_REASON:
            return Eligibility(False, _BLOCKING_STEPS_REASON[st])

    branches = _parse_branches(steps)
    if not branches:
        return Eligibility(False, "Workflow has no trigger.* step.")

    warnings: list[str] = []
    has_any_order = False
    for b in branches:
        if b.trigger_type not in _BACKTESTABLE_TRIGGERS:
            return Eligibility(
                False,
                f"{b.trigger_type} can't be replayed historically.",
            )
        for step in b.body:
            st = str(step.get("step_type") or "")
            if st in _BACKTESTABLE_ACTIONS:
                has_any_order = True
                continue
            if st.startswith("condition.") and st not in _SKIPPABLE_STEPS:
                # condition.numeric is the only one we genuinely
                # simulate; other conditions are silently passed.
                if st != "condition.numeric":
                    warnings.append(
                        f"{st} treated as always-true during backtest"
                    )
                continue
            if st in _SKIPPABLE_STEPS:
                continue
            if st.startswith("action.") and st not in _BACKTESTABLE_ACTIONS:
                warnings.append(
                    f"{st} skipped during backtest (only place_order moves money)"
                )
                continue
            # Anything else we don't recognise.
            warnings.append(f"{st} ignored during backtest")

    if not has_any_order:
        return Eligibility(
            False,
            "No action.place_order step — there's nothing to simulate.",
        )

    return Eligibility(True, None, branches, warnings)


# ── Simulation ────────────────────────────────────────────────────────


@dataclass
class SimState:
    cash: float = _STARTING_CAPITAL
    holdings: dict[str, int] = field(default_factory=dict)
    avg_buy_price: dict[str, float] = field(default_factory=dict)


def _yf_symbol(symbol: str, exchange: str = "NSE") -> str:
    sym = symbol.upper().strip()
    if sym.endswith((".NS", ".BO")):
        return sym
    suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
    return f"{sym}{suffix}"


def _load_bars(symbol: str, period: str) -> pd.DataFrame:
    """Daily OHLCV from yfinance, cleaned. Raises ValueError on
    insufficient data so the caller surfaces a clean error."""
    yf_sym = _yf_symbol(symbol)
    hist = yf.Ticker(yf_sym).history(period=period, interval="1d")
    if hist.empty or len(hist) < 30:
        raise ValueError(
            f"insufficient data for {symbol} over {period} (got {len(hist)} bars)"
        )
    # yfinance returns tz-aware; drop tz so we can do plain date math.
    hist.index = pd.to_datetime(hist.index).tz_localize(None).normalize()
    return hist[["Open", "High", "Low", "Close", "Volume"]]


# ── Trigger fire-time enumeration ─────────────────────────────────────


_CRON_DOW_MAP = {
    0: 6,  # cron Sunday=0 → pandas Sunday=6
    1: 0,  # Monday
    2: 1,
    3: 2,
    4: 3,
    5: 4,
    6: 5,
    7: 6,  # cron Sunday=7 → Sunday
}


def _expand_schedule(
    cfg: dict[str, Any], dates: pd.DatetimeIndex,
) -> list[pd.Timestamp]:
    """Match each trading-day index to a cron expression. We only
    honour the day-of-week field; minute/hour are interpretation
    metadata for live execution but the backtest fires once per day
    that matches the DOW.

    Cron format: ``minute hour day-of-month month day-of-week``.
    Day-of-week supports ``*``, single digits, comma-lists, and
    ranges like ``1-5``.
    """
    cron = str(cfg.get("cron") or "").strip()
    if not cron:
        return []
    parts = cron.split()
    if len(parts) < 5:
        return []
    dow_field = parts[4]
    dows: set[int] = set()
    if dow_field == "*":
        dows = {0, 1, 2, 3, 4, 5, 6}
    else:
        for token in dow_field.split(","):
            token = token.strip()
            if "-" in token:
                a, b = token.split("-", 1)
                try:
                    lo, hi = int(a), int(b)
                except ValueError:
                    continue
                for v in range(lo, hi + 1):
                    py_dow = _CRON_DOW_MAP.get(v % 8)
                    if py_dow is not None:
                        dows.add(py_dow)
            else:
                try:
                    v = int(token)
                except ValueError:
                    continue
                py_dow = _CRON_DOW_MAP.get(v % 8)
                if py_dow is not None:
                    dows.add(py_dow)
    out: list[pd.Timestamp] = []
    for ts in dates:
        if ts.dayofweek in dows:
            out.append(ts)
    return out


def _expand_indicator(
    cfg: dict[str, Any], bars: pd.DataFrame,
) -> list[pd.Timestamp]:
    """Trigger fires when a daily indicator reading crosses the
    threshold. Mirrors trigger.indicator's runtime semantics."""
    indicator = str(cfg.get("indicator") or "").lower()
    period_n = int(cfg.get("period") or 14)
    operator = str(cfg.get("operator") or "<")
    value = float(cfg.get("value") or 0.0)

    closes = bars["Close"].astype(float)
    if indicator == "rsi":
        series = ta.rsi(closes, length=period_n)
    elif indicator == "sma":
        series = ta.sma(closes, length=period_n)
    elif indicator == "ema":
        series = ta.ema(closes, length=period_n)
    else:
        return []
    if series is None or series.dropna().empty:
        return []

    fires: list[pd.Timestamp] = []
    prev: Optional[float] = None
    for ts, v in series.items():
        if pd.isna(v):
            prev = None
            continue
        v_f = float(v)
        if operator == ">":
            if v_f > value:
                fires.append(ts)
        elif operator == "<":
            if v_f < value:
                fires.append(ts)
        elif operator == "crosses_above":
            if prev is not None and prev <= value < v_f:
                fires.append(ts)
        elif operator == "crosses_below":
            if prev is not None and prev >= value > v_f:
                fires.append(ts)
        prev = v_f
    return fires


def _expand_price(
    cfg: dict[str, Any], bars: pd.DataFrame,
) -> list[pd.Timestamp]:
    """trigger.price fires whenever the day's range touches the
    threshold. Approximation: HIGH ≥ value for upward, LOW ≤ value
    for downward."""
    operator = str(cfg.get("operator") or "<")
    value = float(cfg.get("value") or 0.0)
    fires: list[pd.Timestamp] = []
    prev_close: Optional[float] = None
    for ts, row in bars.iterrows():
        hi = float(row["High"])
        lo = float(row["Low"])
        close = float(row["Close"])
        if operator == ">":
            if hi >= value:
                fires.append(ts)
        elif operator == "<":
            if lo <= value:
                fires.append(ts)
        elif operator == "crosses_above":
            if prev_close is not None and prev_close <= value <= hi:
                fires.append(ts)
        elif operator == "crosses_below":
            if prev_close is not None and prev_close >= value >= lo:
                fires.append(ts)
        prev_close = close
    return fires


# ── Condition evaluation ─────────────────────────────────────────────


_REF_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def _resolve_ref(
    expr: Any, state: SimState, bars: pd.DataFrame, ts: pd.Timestamp,
    branch: Branch,
) -> Any:
    """Best-effort resolver for the few ref shapes that show up in
    backtested conditions. Falls back to the original expression if
    nothing matches — Pydantic-style numeric coercion will then catch
    the type mismatch downstream.

    Supported:
      ``{{ context.<idx>.buying_power }}``           → state.cash
      ``{{ context.<idx>.holdings.<SYM>.quantity }}`` → state.holdings[SYM]
      ``{{ context.<idx>.value }}`` (fetch.indicator output) → indicator at ts
      Bare numbers / strings → returned as-is.
    """
    if isinstance(expr, (int, float)):
        return float(expr)
    if not isinstance(expr, str):
        return expr
    s = expr.strip()
    m = _REF_RE.fullmatch(s)
    if not m:
        # Try parsing as a plain number string.
        try:
            return float(s)
        except ValueError:
            return s
    inner = m.group(1).strip()
    parts = inner.split(".")
    if not parts or parts[0] != "context":
        return s
    if len(parts) >= 3 and parts[2] == "buying_power":
        return float(state.cash)
    if (
        len(parts) >= 5
        and parts[2] == "holdings"
        and parts[4] == "quantity"
    ):
        sym = parts[3].upper()
        return float(state.holdings.get(sym, 0))
    if len(parts) >= 3 and parts[2] == "value":
        # Look up the immediately-preceding fetch.indicator step in the
        # branch and compute its value at this date.
        try:
            ref_idx = int(parts[1])
        except ValueError:
            return s
        for step in branch.body:
            if int(step.get("step_index", -1)) != ref_idx:
                continue
            cfg = step.get("config") or {}
            ind = str(cfg.get("indicator") or "").lower()
            n = int(cfg.get("period") or 14)
            sym = str(cfg.get("symbol") or branch.primary_symbol() or "").upper()
            if not sym:
                return s
            # Use the same bars (single symbol assumed for now).
            closes = bars["Close"].astype(float)
            if ind == "rsi":
                series = ta.rsi(closes, length=n)
            elif ind == "sma":
                series = ta.sma(closes, length=n)
            elif ind == "ema":
                series = ta.ema(closes, length=n)
            else:
                return s
            if series is None or ts not in series.index:
                return s
            v = series.loc[ts]
            return None if pd.isna(v) else float(v)
    return s


def _eval_condition_numeric(
    cfg: dict[str, Any], state: SimState, bars: pd.DataFrame,
    ts: pd.Timestamp, branch: Branch,
) -> bool:
    """Evaluate ``condition.numeric``. Returns True (continue) or False
    (halt this branch's iteration on this fire)."""
    left = _resolve_ref(cfg.get("left"), state, bars, ts, branch)
    right = _resolve_ref(cfg.get("right"), state, bars, ts, branch)
    op = str(cfg.get("operator") or "==")
    try:
        l = float(left)  # type: ignore[arg-type]
        r = float(right)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if op == ">":
        return l > r
    if op == "<":
        return l < r
    if op == ">=":
        return l >= r
    if op == "<=":
        return l <= r
    if op == "==":
        return l == r
    if op == "!=":
        return l != r
    return False


# ── Order simulation ─────────────────────────────────────────────────


def _resolve_quantity(
    raw_qty: Any, state: SimState, bars: pd.DataFrame, ts: pd.Timestamp,
    branch: Branch,
) -> int:
    """Mustache refs in quantity (the "sell entire holding" pattern)
    resolve against sim state. Anything else gets coerced to int."""
    resolved = _resolve_ref(raw_qty, state, bars, ts, branch)
    try:
        return max(0, int(float(resolved)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _execute_branch(
    branch: Branch, state: SimState, bars: pd.DataFrame, ts: pd.Timestamp,
    signals_out: list[dict], trades_out: list[dict],
) -> None:
    """Walk a branch's body for one fire. Updates ``state`` in place
    and appends to the chart payload buffers."""
    for step in branch.body:
        st = str(step.get("step_type") or "")
        cfg = step.get("config") or {}
        if st == "condition.numeric":
            if not _eval_condition_numeric(cfg, state, bars, ts, branch):
                return
            continue
        if st == "action.place_order":
            sym = str(cfg.get("symbol") or "").upper()
            side = str(cfg.get("side") or "buy").lower()
            qty = _resolve_quantity(
                cfg.get("quantity"), state, bars, ts, branch,
            )
            if qty <= 0 or not sym:
                continue
            row = bars.loc[ts] if ts in bars.index else None
            if row is None:
                continue
            fill_price = float(row["Open"]) * (
                1 + _FRICTION if side == "buy" else 1 - _FRICTION
            )
            value = fill_price * qty
            if side == "buy":
                if value > state.cash:
                    # Insufficient cash — skip (live system would
                    # also reject). Record as a no-op signal so the
                    # chart shows the missed signal.
                    signals_out.append({
                        "t": ts.date().isoformat(),
                        "side": "buy_skipped",
                        "price": fill_price,
                        "qty": qty,
                    })
                    continue
                state.cash -= value
                prev_qty = state.holdings.get(sym, 0)
                prev_avg = state.avg_buy_price.get(sym, 0.0)
                new_qty = prev_qty + qty
                state.holdings[sym] = new_qty
                state.avg_buy_price[sym] = (
                    (prev_avg * prev_qty + fill_price * qty) / new_qty
                )
                trades_out.append({
                    "t": ts.date().isoformat(), "side": "buy", "symbol": sym,
                    "qty": qty, "price": round(fill_price, 2),
                })
                signals_out.append({
                    "t": ts.date().isoformat(), "side": "buy",
                    "price": round(fill_price, 2), "qty": qty,
                })
            elif side == "sell":
                held = state.holdings.get(sym, 0)
                exec_qty = min(qty, held)
                if exec_qty <= 0:
                    continue
                state.cash += fill_price * exec_qty
                state.holdings[sym] = held - exec_qty
                if state.holdings[sym] == 0:
                    state.avg_buy_price.pop(sym, None)
                trades_out.append({
                    "t": ts.date().isoformat(), "side": "sell", "symbol": sym,
                    "qty": exec_qty, "price": round(fill_price, 2),
                })
                signals_out.append({
                    "t": ts.date().isoformat(), "side": "sell",
                    "price": round(fill_price, 2), "qty": exec_qty,
                })
        # Other step types are silently skipped per the eligibility map.


# ── Public entry ─────────────────────────────────────────────────────


def backtest_workflow(
    steps: list[dict[str, Any]],
    *,
    period: str = "5y",
    name: str = "Workflow",
) -> IndicatorBacktestResult:
    """Simulate a workflow draft over historical daily bars.

    Returns the same ``IndicatorBacktestResult`` shape the indicator
    backtester produces so the FE chart card reuses without changes.
    Raises ``ValueError`` when the workflow is not eligible or when
    the data fetch fails.
    """
    elig = check_eligibility(steps)
    if not elig.eligible:
        raise ValueError(elig.reason or "workflow not backtestable")

    # All branches share a primary symbol in v1 — multi-symbol agents
    # would need multi-feed simulation; skip for now and use whichever
    # symbol the first eligible branch targets.
    primary_symbol: Optional[str] = None
    for b in elig.branches:
        s = b.primary_symbol()
        if s:
            primary_symbol = s
            break
    if primary_symbol is None:
        raise ValueError(
            "couldn't infer a target symbol from any branch — "
            "every place_order step needs `symbol` set."
        )

    bars = _load_bars(primary_symbol, period)

    # Build the merged event timeline.
    events: list[tuple[pd.Timestamp, int]] = []  # (date, branch index)
    for i, b in enumerate(elig.branches):
        if b.trigger_type == "trigger.schedule":
            fires = _expand_schedule(b.trigger_config, bars.index)
        elif b.trigger_type == "trigger.indicator":
            fires = _expand_indicator(b.trigger_config, bars)
        elif b.trigger_type == "trigger.price":
            fires = _expand_price(b.trigger_config, bars)
        else:
            fires = []
        events.extend((ts, i) for ts in fires)
    events.sort(key=lambda e: e[0])

    # Walk events, updating sim state.
    state = SimState()
    signals: list[dict] = []
    trades: list[dict] = []
    for ts, branch_idx in events:
        if ts not in bars.index:
            continue
        _execute_branch(
            elig.branches[branch_idx], state, bars, ts, signals, trades,
        )

    # Build the equity curve from daily closes.
    price_curve: list[dict] = []
    equity_curve: list[dict] = []
    # Walk bars and replay trades in date order to compute mark-to-market.
    walking_state = SimState()
    trade_iter = iter(trades)
    next_trade = next(trade_iter, None)
    for ts, row in bars.iterrows():
        # Apply any trades scheduled today.
        while next_trade is not None and next_trade["t"] == ts.date().isoformat():
            tr = next_trade
            sym = tr["symbol"]
            if tr["side"] == "buy":
                walking_state.cash -= tr["price"] * tr["qty"]
                walking_state.holdings[sym] = (
                    walking_state.holdings.get(sym, 0) + tr["qty"]
                )
            elif tr["side"] == "sell":
                walking_state.cash += tr["price"] * tr["qty"]
                walking_state.holdings[sym] = (
                    walking_state.holdings.get(sym, 0) - tr["qty"]
                )
            next_trade = next(trade_iter, None)
        close = float(row["Close"])
        market_value = sum(
            qty * close for sym, qty in walking_state.holdings.items()
        )
        equity = walking_state.cash + market_value
        price_curve.append({"t": ts.date().isoformat(), "v": close})
        equity_curve.append({"t": ts.date().isoformat(), "v": round(equity, 2)})

    # Metrics: total return %, CAGR, max drawdown, win rate, n_trades.
    final_equity = equity_curve[-1]["v"] if equity_curve else _STARTING_CAPITAL
    total_return_pct = round(
        (final_equity - _STARTING_CAPITAL) / _STARTING_CAPITAL * 100, 2,
    )
    n_days = len(equity_curve) or 1
    years_elapsed = max(n_days / 252.0, 1 / 252.0)
    cagr_pct = round(
        ((final_equity / _STARTING_CAPITAL) ** (1 / years_elapsed) - 1) * 100, 2,
    ) if final_equity > 0 else 0.0
    peak = _STARTING_CAPITAL
    max_dd = 0.0
    for p in equity_curve:
        if p["v"] > peak:
            peak = p["v"]
        if peak > 0:
            dd = (p["v"] - peak) / peak
            if dd < max_dd:
                max_dd = dd
    max_drawdown_pct = round(max_dd * 100, 2)

    # Buy & hold benchmark for the same primary symbol.
    if len(bars) >= 2:
        bench_pct = round(
            (float(bars["Close"].iloc[-1]) / float(bars["Close"].iloc[0]) - 1)
            * 100, 2,
        )
    else:
        bench_pct = 0.0

    n_trades = len(trades)
    # Hit rate: pair sells to their preceding same-symbol buys (FIFO)
    # and count how many sells were profitable. SIPs without sells
    # show 0 wins / 0 hit-rate, which the chart card displays sensibly.
    n_wins = 0
    by_symbol_buys: dict[str, list[dict]] = {}
    for tr in trades:
        sym = tr["symbol"]
        if tr["side"] == "buy":
            by_symbol_buys.setdefault(sym, []).append(tr)
        elif tr["side"] == "sell":
            queue = by_symbol_buys.get(sym) or []
            qty_left = tr["qty"]
            while qty_left > 0 and queue:
                buy = queue[0]
                take = min(qty_left, buy["qty"])
                if tr["price"] > buy["price"]:
                    n_wins += 1
                qty_left -= take
                buy["qty"] -= take
                if buy["qty"] <= 0:
                    queue.pop(0)
    n_sells = sum(1 for t in trades if t["side"] == "sell")
    hit_rate_pct = round((n_wins / n_sells * 100) if n_sells else 0.0, 1)

    # The IndicatorBacktestCard expects these extra metric fields —
    # leaving them out crashes the FE with `undefined.toFixed`. Always
    # include them, even when meaningless (e.g. SIP with no sells).
    metrics = {
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "n_trades": n_trades,
        "n_wins": n_wins,
        "hit_rate_pct": hit_rate_pct,
        "starting_capital": _STARTING_CAPITAL,
        "ending_value": round(final_equity, 2),
    }

    # Signals must carry the fields the FE chart card reads. The card
    # types `side: 'buy' | 'sell'` and expects `indicator_value` (may
    # be null). Coerce buy_skipped → buy with a flag so the chart still
    # plots a marker, and inject indicator_value=None for every signal
    # since the workflow backtester doesn't compute one.
    enriched_signals = [
        {
            "t": s["t"],
            "side": "buy" if s.get("side", "").startswith("buy") else "sell",
            "price": s["price"],
            "indicator_value": None,
        }
        for s in signals
    ]

    summary = (
        f"Backtested {name!r} on {primary_symbol} over {period}. "
        f"Strategy returned {total_return_pct:+.1f}% across {n_trades} trade(s); "
        f"buy-and-hold returned {bench_pct:+.1f}%."
    )
    if elig.warnings:
        summary += " Notes: " + "; ".join(elig.warnings[:3]) + "."

    return IndicatorBacktestResult(
        symbol=primary_symbol,
        # Tag the indicator slot with the trigger type so the chart
        # card's caption reads sensibly. The card doesn't gate on
        # value here, only displays.
        indicator=elig.branches[0].trigger_type.split(".", 1)[-1],
        indicator_period=0,
        operator="-",
        threshold=0.0,
        period_label=period,
        price_curve=price_curve,
        equity_curve=equity_curve,
        indicator_curve=[],
        signals=enriched_signals,
        metrics=metrics,
        bench_buy_hold_return_pct=bench_pct,
        summary_text=summary,
    )
