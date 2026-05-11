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
import yfinance as yf  # type: ignore[import-untyped]
from pytz import timezone as pytz_timezone  # type: ignore[import-untyped]

from backend.services.backtest_indicators import (
    basis_for as _ind_basis,
    compute_series as _ind_series,
    get_spec as _ind_spec,
)
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

# Action types we can simulate.
#   * action.place_order             — fills at next-bar OPEN
#   * action.set_stoploss            — registers a sell-stop, evaluated
#                                      bar-by-bar against LOW
#   * action.squareoff_symbol        — closes one symbol at OPEN
#   * action.squareoff_all_intraday  — closes every MIS lot at OPEN
# Anything else falls through to "skipped" and surfaces as a warning.
_BACKTESTABLE_ACTIONS = {
    "action.place_order",
    "action.set_stoploss",
    "action.set_takeprofit",
    "action.squareoff_symbol",
    "action.squareoff_all_intraday",
    "action.squareoff_all",
    "action.allocate_basket",
}

# Steps we silently skip during backtest (no historical effect on
# their own — but their outputs ARE resolved on demand by _resolve_ref
# whenever a downstream condition.numeric references them via
# {{ context.<idx>.<field> }}).
_SKIPPABLE_STEPS = {
    "notify.message",
    "notify.log",
    "wait.approval",
    "wait.delay",
    "fetch.portfolio",
    "fetch.quote",
    "fetch.indicator",
    "fetch.day_open",
    "fetch.prior_close",
    "fetch.relative_threshold",
    "fetch.rolling_high",
    "fetch.rolling_low",
    "fetch.spread_z_score",
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

    def trigger_symbol(self) -> Optional[str]:
        """The symbol the trigger evaluates against. May differ from
        the action symbol — supports cross-asset workflows like
        'buy RELIANCE when TCS RSI < 30'."""
        sym = self.trigger_config.get("symbol")
        return sym.upper().strip() if isinstance(sym, str) and sym.strip() else None

    def all_symbols(self) -> set[str]:
        """Every symbol referenced by this branch — trigger.symbol,
        every step's `symbol`, plus every leg.symbol on
        action.allocate_basket. Used to build the multi-symbol bar
        registry."""
        out: set[str] = set()
        ts = self.trigger_symbol()
        if ts:
            out.add(ts)
        for step in self.body:
            cfg = step.get("config") or {}
            for key in ("symbol", "symbol_a", "symbol_b"):
                s = cfg.get(key)
                if isinstance(s, str) and s.strip():
                    out.add(s.upper().strip())
            # Basket legs.
            legs = cfg.get("legs")
            if isinstance(legs, list):
                for leg in legs:
                    if isinstance(leg, dict):
                        leg_sym = leg.get("symbol")
                        if isinstance(leg_sym, str) and leg_sym.strip():
                            out.add(leg_sym.upper().strip())
        return out


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
            if st.startswith("condition."):
                # All four condition types are now simulated:
                # condition.numeric, .position, .market_status,
                # .time_window — see _eval_condition_numeric and
                # _eval_simple_condition. No warnings.
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
class StoplossOrder:
    """One active sell-stop. Evaluated bar-by-bar against the day's LOW;
    when LOW ≤ trigger_price the position closes at trigger_price (with
    one-side friction). Cancelled when the underlying position is fully
    exited via any path (sell order, squareoff, take-profit, prior
    stoploss hit).

    ``trailing`` + ``trail_pct`` enable a ratcheting stop: each bar we
    update ``high_water_mark = max(hwm, bar.High)`` and reset
    ``trigger_price = hwm * (1 - trail_pct/100)``. The stop only ever
    moves UP, never down — protecting profits as the underlying rallies."""
    trigger_price: float
    quantity: int
    set_at: pd.Timestamp
    trailing: bool = False
    trail_pct: float = 0.0
    high_water_mark: float = 0.0


@dataclass
class TakeprofitOrder:
    """One active sell-stop on the upside — fires when bar HIGH ≥
    trigger_price. Symmetric to StoplossOrder but with friction-adjusted
    fill at trigger * (1 - friction)."""
    trigger_price: float
    quantity: int
    set_at: pd.Timestamp


@dataclass
class SimState:
    cash: float = _STARTING_CAPITAL
    holdings: dict[str, int] = field(default_factory=dict)
    avg_buy_price: dict[str, float] = field(default_factory=dict)
    # Per-symbol product tag (CNC / MIS) of the open lot. Squareoff_all_
    # intraday filters on this; place_order writes it from cfg.product.
    product: dict[str, str] = field(default_factory=dict)
    # Active stop-losses keyed by symbol.
    stoplosses: dict[str, list[StoplossOrder]] = field(default_factory=dict)
    # Active take-profits keyed by symbol.
    takeprofits: dict[str, list[TakeprofitOrder]] = field(default_factory=dict)


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


def _cron_field(field: str, lo: int, hi: int) -> set[int]:
    """Parse one cron field (e.g. day-of-month '14', month '2,5', dow
    '1-5') into the set of matching integers. Returns the full
    [lo, hi] range when the field is '*'."""
    if field == "*":
        return set(range(lo, hi + 1))
    out: set[int] = set()
    for tok in field.split(","):
        tok = tok.strip()
        if "-" in tok:
            a, b = tok.split("-", 1)
            try:
                la, lb = int(a), int(b)
            except ValueError:
                continue
            for v in range(la, lb + 1):
                if lo <= v <= hi:
                    out.add(v)
        else:
            try:
                v = int(tok)
                if lo <= v <= hi:
                    out.add(v)
            except ValueError:
                continue
    return out


def _expand_schedule(
    cfg: dict[str, Any], dates: pd.DatetimeIndex,
) -> list[pd.Timestamp]:
    """Match each trading-day index to a cron expression.

    Cron format: ``minute hour day-of-month month day-of-week``. We
    honour day-of-month (1–31), month (1–12), and day-of-week (cron's
    Sun=0/7, Mon=1, …) — the trio that constrains WHICH days fire.
    Minute / hour are interpretation metadata for live execution; the
    backtest fires at most once per matching trading day (the bar's
    OPEN for entries, CLOSE for squareoffs — set by the executor).

    A day matches when ALL THREE fields match (cron's standard "AND"
    semantics when both DOM and DOW are explicit). When either DOM or
    DOW is '*' the unconstrained field doesn't filter.
    """
    cron = str(cfg.get("cron") or "").strip()
    if not cron:
        return []
    parts = cron.split()
    if len(parts) < 5:
        return []
    dom_field = parts[2]
    mon_field = parts[3]
    dow_field = parts[4]

    doms = _cron_field(dom_field, 1, 31)
    months = _cron_field(mon_field, 1, 12)
    # cron DOW: 0=Sun (or 7), 1=Mon, …, 6=Sat. Convert to Python's
    # Monday=0..Sunday=6 via the existing _CRON_DOW_MAP.
    cron_dows = _cron_field(dow_field, 0, 7)
    py_dows: set[int] = set()
    for v in cron_dows:
        py = _CRON_DOW_MAP.get(v % 8)
        if py is not None:
            py_dows.add(py)

    # Standard cron "OR" semantics when DOM and DOW are both explicit
    # (vista-cron / Vixie cron). When one is '*' the other is the
    # constraint. We follow Vixie semantics here so '0 9 14 2 *' means
    # "Feb 14, regardless of DOW" — matching what the LLM expects.
    dow_explicit = dow_field != "*"
    dom_explicit = dom_field != "*"

    out: list[pd.Timestamp] = []
    for ts in dates:
        if ts.month not in months:
            continue
        dom_match = ts.day in doms
        dow_match = ts.dayofweek in py_dows
        if dom_explicit and dow_explicit:
            # OR (Vixie semantics)
            if not (dom_match or dow_match):
                continue
        else:
            if not (dom_match and dow_match):
                continue
        out.append(ts)
    return out


def _expand_indicator(
    cfg: dict[str, Any], bars: pd.DataFrame,
) -> list[pd.Timestamp]:
    """Trigger fires when a daily indicator reading crosses the threshold.

    Mirrors ``trigger.indicator``'s runtime semantics. Honours both
    ``basis="value"`` indicators (RSI, MACD-hist, %B, Stoch, …) where
    the threshold is compared against the indicator series, and
    ``basis="price"`` indicators (SMA, EMA, WMA, PSAR, VWAP, Keltner-mid,
    Donchian-mid, …) where the day's CLOSE is compared against the
    indicator series and the user-supplied ``value`` is ignored
    (the indicator IS the threshold).
    """
    indicator = str(cfg.get("indicator") or "").lower()
    period_n = int(cfg.get("period") or 0)
    operator = str(cfg.get("operator") or "<")
    value = float(cfg.get("value") or 0.0)

    spec = _ind_spec(indicator)
    if spec is None:
        return []
    series = _ind_series(bars, indicator, period_n)
    if series is None:
        return []

    if spec.basis == "price":
        # Compare close vs indicator. The user's "value" field is the
        # threshold offset (0 for plain crossings; non-zero is exotic
        # and rare — passed through as an additive bias).
        closes = bars["Close"].astype(float)
        threshold_series = series + value if value else series
    else:
        # Compare indicator vs scalar threshold.
        closes = series  # the comparison basis
        threshold_series = pd.Series(value, index=series.index)

    fires: list[pd.Timestamp] = []
    prev_basis: Optional[float] = None
    prev_thr: Optional[float] = None
    for ts in series.index:
        if ts not in closes.index:
            continue
        basis_v = closes.loc[ts]
        thr_v = threshold_series.loc[ts]
        if pd.isna(basis_v) or pd.isna(thr_v):
            prev_basis = None
            prev_thr = None
            continue
        b, t = float(basis_v), float(thr_v)
        if operator == ">":
            if b > t:
                fires.append(ts)
        elif operator == "<":
            if b < t:
                fires.append(ts)
        elif operator == "crosses_above":
            if prev_basis is not None and prev_thr is not None \
                    and prev_basis <= prev_thr < b:
                fires.append(ts)
        elif operator == "crosses_below":
            if prev_basis is not None and prev_thr is not None \
                    and prev_basis >= prev_thr > b:
                fires.append(ts)
        prev_basis, prev_thr = b, t
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
    expr: Any, state: SimState, symbol_bars: dict[str, pd.DataFrame],
    ts: pd.Timestamp, branch: Branch,
) -> Any:
    """Best-effort resolver for the few ref shapes that show up in
    backtested conditions. Falls back to the original expression if
    nothing matches — Pydantic-style numeric coercion will then catch
    the type mismatch downstream.

    Supported:
      ``{{ context.<idx>.buying_power }}``           → state.cash
      ``{{ context.<idx>.holdings.<SYM>.quantity }}`` → state.holdings[SYM]
      ``{{ context.<idx>.value }}`` (fetch.indicator output) → indicator at ts
                                     Resolves against the indicator step's
                                     ``symbol`` config — supports
                                     cross-asset refs.
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
    if len(parts) >= 3 and parts[2] in {
        "value", "ltp", "open", "high", "low", "close", "volume",
        "day_open", "prior_close", "prior_high", "prior_low",
    }:
        # Resolve a fetch.* step's output at the current bar.
        # Supported fetch types:
        #   - fetch.indicator → context.<idx>.value (canonical scalar
        #     from the unified registry).
        #   - fetch.quote → context.<idx>.{ltp, open, high, low, close,
        #     volume} from that day's bar.
        #   - fetch.day_open → context.<idx>.value (today's open).
        #   - fetch.prior_close → context.<idx>.value (prior close).
        #   - fetch.relative_threshold → context.<idx>.value (precomputed
        #     absolute level from the reference + offset).
        try:
            ref_idx = int(parts[1])
        except ValueError:
            return s
        field = parts[2]
        for step in branch.body:
            if int(step.get("step_index", -1)) != ref_idx:
                continue
            st = str(step.get("step_type") or "")
            cfg = step.get("config") or {}
            # spread_z_score uses symbol_a / symbol_b rather than the
            # singular `symbol` field other fetches use.
            sym = str(
                cfg.get("symbol")
                or cfg.get("symbol_a")
                or branch.primary_symbol() or "",
            ).upper()
            if not sym:
                return s
            ref_bars = symbol_bars.get(sym)
            if ref_bars is None or ts not in ref_bars.index:
                return s

            if st == "fetch.indicator":
                ind = str(cfg.get("indicator") or "").lower()
                n = int(cfg.get("period") or 0)
                series = _ind_series(ref_bars, ind, n)
                if series is None or ts not in series.index:
                    return s
                v = series.loc[ts]
                return None if pd.isna(v) else float(v)

            if st == "fetch.quote":
                # Map ref field → bar column. ltp ≈ close on daily bars.
                col_map = {
                    "ltp": "Close", "close": "Close",
                    "open": "Open", "high": "High", "low": "Low",
                    "volume": "Volume",
                }
                col = col_map.get(field)
                if col is None:
                    return s
                v = ref_bars.at[ts, col]
                return None if pd.isna(v) else float(v)

            if st == "fetch.day_open":
                v = ref_bars.at[ts, "Open"]
                return None if pd.isna(v) else float(v)

            if st == "fetch.prior_close":
                back = int(cfg.get("sessions_back") or 1)
                pos = ref_bars.index.get_loc(ts)
                if not isinstance(pos, int) or pos - back < 0:
                    return s
                v = ref_bars["Close"].iloc[pos - back]
                return None if pd.isna(v) else float(v)

            if st == "fetch.spread_z_score":
                # Two-symbol fetch — the outer `sym` (used by ref_bars)
                # is symbol_a. Resolve symbol_b independently from
                # ``cfg``.
                sym_b = str(cfg.get("symbol_b") or "").upper()
                bars_b = symbol_bars.get(sym_b)
                if bars_b is None or ts not in bars_b.index:
                    return s
                lookback = int(cfg.get("lookback", 30))
                pos_a = ref_bars.index.get_loc(ts)
                pos_b = bars_b.index.get_loc(ts)
                if not isinstance(pos_a, int) or not isinstance(pos_b, int):
                    return s
                start_a = max(0, pos_a - lookback + 1)
                start_b = max(0, pos_b - lookback + 1)
                window_a = ref_bars["Close"].iloc[start_a: pos_a + 1]
                window_b = bars_b["Close"].iloc[start_b: pos_b + 1]
                # Align on dates that exist in both.
                aligned = pd.concat(
                    [window_a, window_b], axis=1, join="inner"
                ).dropna()
                if len(aligned) < 5:
                    return s
                spread = aligned.iloc[:, 0] - aligned.iloc[:, 1]
                m = float(spread.mean())
                sd = float(spread.std(ddof=0))
                if sd <= 0:
                    return 0.0
                cur = float(spread.iloc[-1])
                return (cur - m) / sd

            if st == "fetch.rolling_high":
                lookback = int(cfg.get("lookback", 20))
                mult = float(cfg.get("multiplier", 1.0))
                pos = ref_bars.index.get_loc(ts)
                if not isinstance(pos, int):
                    return s
                start = max(0, pos - lookback + 1)
                window = ref_bars["High"].iloc[start: pos + 1]
                if window.empty:
                    return s
                return float(window.max()) * mult

            if st == "fetch.rolling_low":
                lookback = int(cfg.get("lookback", 20))
                mult = float(cfg.get("multiplier", 1.0))
                pos = ref_bars.index.get_loc(ts)
                if not isinstance(pos, int):
                    return s
                start = max(0, pos - lookback + 1)
                window = ref_bars["Low"].iloc[start: pos + 1]
                if window.empty:
                    return s
                return float(window.min()) * mult

            if st == "fetch.relative_threshold":
                ref = str(cfg.get("reference") or "day_open")
                pct = float(cfg.get("offset_pct") or 0.0)
                anchor: Optional[float] = None
                if ref == "day_open":
                    anchor = float(ref_bars.at[ts, "Open"])
                elif ref == "prior_close":
                    pos = ref_bars.index.get_loc(ts)
                    if isinstance(pos, int) and pos > 0:
                        anchor = float(ref_bars["Close"].iloc[pos - 1])
                elif ref == "prior_high":
                    pos = ref_bars.index.get_loc(ts)
                    if isinstance(pos, int) and pos > 0:
                        anchor = float(ref_bars["High"].iloc[pos - 1])
                elif ref == "prior_low":
                    pos = ref_bars.index.get_loc(ts)
                    if isinstance(pos, int) and pos > 0:
                        anchor = float(ref_bars["Low"].iloc[pos - 1])
                if anchor is None:
                    return s
                return anchor * (1 + pct / 100.0)

            return s
    return s


# NSE trading hours, in IST. Used by condition.market_status /
# condition.time_window during simulation. The live engine resolves
# these against real time; we resolve them against the bar's date so
# "execute only when market is open" no longer silently passes on
# weekend or holiday signals (in practice trigger expansion already
# filters to trading days, but condition.time_window for "after 14:30"
# previously evaluated to True on every bar).
_NSE_OPEN = (9, 15)
_NSE_CLOSE = (15, 30)


def _bar_is_market_open(ts: pd.Timestamp) -> bool:
    """yfinance daily bars are NSE trading days — every bar in the
    series is by definition a market-open day."""
    return ts.weekday() < 5  # belt-and-braces: skip weekends if any


def _time_within(start: str, end: str) -> bool:
    """Daily bars don't have intraday timestamps. We treat any window
    that covers an instant inside [09:15, 15:30] as 'true' — the
    semantics most users mean by "between 09:30 and 15:00". Windows
    entirely outside trading hours evaluate False so users can author
    "between 19:00 and 20:00" guards that prevent fires on regular
    daily bars."""
    def parse(s: str) -> tuple[int, int]:
        h, _, m = s.partition(":")
        return int(h), int(m or 0)

    s_h, s_m = parse(start)
    e_h, e_m = parse(end)
    s_total = s_h * 60 + s_m
    e_total = e_h * 60 + e_m
    open_total = _NSE_OPEN[0] * 60 + _NSE_OPEN[1]
    close_total = _NSE_CLOSE[0] * 60 + _NSE_CLOSE[1]
    # Window must overlap [open, close].
    return not (e_total < open_total or s_total > close_total)


def _eval_simple_condition(
    step_type: str, cfg: dict[str, Any], state: SimState,
    ts: pd.Timestamp,
) -> bool:
    """Resolve the three formerly-skipped condition types against the
    sim state at ``ts``. Returns True to continue the branch, False to
    halt (matches condition.numeric semantics)."""
    if step_type == "condition.position":
        sym = str(cfg.get("symbol") or "").upper()
        require = str(cfg.get("require") or "held").lower()
        # "Held" = any non-zero position (long OR short). Short legs of
        # a pairs trade are still positions for the purposes of the
        # 'don't double-up' guard.
        held = state.holdings.get(sym, 0) != 0
        return held if require == "held" else not held
    if step_type == "condition.market_status":
        require = str(cfg.get("require") or "open").lower()
        is_open = _bar_is_market_open(ts)
        if require == "open":
            return is_open
        if require == "closed":
            return not is_open
        # 'pre' / 'post' don't apply to daily bars — let them pass
        # through so workflows that include them don't silently die.
        return True
    if step_type == "condition.time_window":
        return _time_within(
            str(cfg.get("start_time") or "09:15"),
            str(cfg.get("end_time") or "15:30"),
        )
    return True


def _eval_condition_numeric(
    cfg: dict[str, Any], state: SimState,
    symbol_bars: dict[str, pd.DataFrame],
    ts: pd.Timestamp, branch: Branch,
) -> bool:
    """Evaluate ``condition.numeric``. Returns True (continue) or False
    (halt this branch's iteration on this fire)."""
    left = _resolve_ref(cfg.get("left"), state, symbol_bars, ts, branch)
    right = _resolve_ref(cfg.get("right"), state, symbol_bars, ts, branch)
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
    raw_qty: Any, state: SimState,
    symbol_bars: dict[str, pd.DataFrame], ts: pd.Timestamp,
    branch: Branch,
) -> int:
    """Mustache refs in quantity (the "sell entire holding" pattern)
    resolve against sim state. Anything else gets coerced to int."""
    resolved = _resolve_ref(raw_qty, state, symbol_bars, ts, branch)
    try:
        return max(0, int(float(resolved)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _record_trade(
    state: SimState, sym: str, signed_qty: int, price: float,
    product: str, ts: pd.Timestamp,
    signals_out: list[dict], trades_out: list[dict],
    reason: str = "trade",
) -> None:
    """Single bookkeeping primitive for every position change.

    ``signed_qty > 0`` increases the holdings position (buy or
    buy-to-cover). ``signed_qty < 0`` decreases it (sell or
    sell-to-open-short). Cash flows opposite to ``signed_qty``: spending
    on buys, receiving on sells. ``avg_buy_price`` tracks the weighted
    entry price of the OPEN side; it resets when the position closes
    or flips sign.

    Mark-to-market downstream uses ``cash + Σ(holdings × close)`` which
    is correct for both long (positive qty) and short (negative qty):
    a short with entry S and current C contributes -|qty|×C to value
    and the cash already carries +|qty|×S from the open, so equity
    delta is |qty|×(S − C) — exactly the short's P&L.
    """
    if signed_qty == 0:
        return
    prev = state.holdings.get(sym, 0)
    new = prev + signed_qty

    state.cash -= signed_qty * price
    state.holdings[sym] = new

    # avg_buy_price (really avg_entry_price — name kept for back-compat
    # with refs like {{context.K.avg_buy_price}}):
    if new == 0:
        # Position fully closed.
        state.avg_buy_price.pop(sym, None)
        state.product.pop(sym, None)
        state.stoplosses.pop(sym, None)
        state.takeprofits.pop(sym, None)
    elif prev == 0 or (prev > 0) != (new > 0):
        # Fresh open OR sign flip (closed and reopened on the other
        # side in one trade — the entry price is THIS bar's price).
        state.avg_buy_price[sym] = price
        state.product[sym] = product
    elif abs(new) > abs(prev):
        # Same-direction increase → weighted-average entry price.
        prev_avg = state.avg_buy_price.get(sym, 0.0)
        added = abs(signed_qty)
        state.avg_buy_price[sym] = (
            (prev_avg * abs(prev) + price * added) / abs(new)
        )
        state.product.setdefault(sym, product)
    # else: same-direction decrease → avg unchanged, partial close.

    # Side label for the chart card.
    if signed_qty > 0:
        side = "buy" if prev >= 0 else "cover"
    else:
        side = "sell" if prev > 0 else "short"
    if reason in {"stoploss", "takeprofit", "squareoff"}:
        side = reason  # surface these explicitly on the signals layer

    trades_out.append({
        "t": ts.date().isoformat(),
        "side": "buy" if signed_qty > 0 else "sell",
        "symbol": sym,
        "qty": abs(signed_qty),
        "price": round(price, 2),
        "product": state.product.get(sym, product),
        "reason": reason,
    })
    signals_out.append({
        "t": ts.date().isoformat(),
        "side": side,
        "price": round(price, 2),
        "qty": abs(signed_qty),
    })


def _record_buy(
    state: SimState, sym: str, qty: int, price: float, product: str,
    ts: pd.Timestamp, signals_out: list[dict], trades_out: list[dict],
) -> None:
    """Back-compat shim for existing call sites — buys with qty > 0."""
    _record_trade(
        state, sym, +int(qty), price, product, ts,
        signals_out, trades_out, reason="trade",
    )


def _record_sell(
    state: SimState, sym: str, qty: int, price: float, ts: pd.Timestamp,
    signals_out: list[dict], trades_out: list[dict], reason: str = "sell",
) -> None:
    """Back-compat shim — sells with qty > 0. Used by stoploss /
    takeprofit / squareoff paths that already clamp qty to held."""
    _record_trade(
        state, sym, -int(qty), price, "CNC", ts,
        signals_out, trades_out, reason=reason,
    )


def _evaluate_stoplosses(
    state: SimState, symbol_bars: dict[str, pd.DataFrame],
    ts: pd.Timestamp, signals_out: list[dict], trades_out: list[dict],
) -> None:
    """Run BEFORE the bar's events. For every active sell-stop AND
    take-profit, evaluate against the bar's range and fill at the
    trigger price with one-side friction. Trailing stops also ratchet
    their trigger upward against the bar's HIGH before evaluating.

    Order on a single bar: trailing stops update first → take-profits
    fire (HIGH ≥ trigger) → stoplosses fire (LOW ≤ trigger). Pessimistic
    when both hit on the same bar: the SL wins, since intraday lows
    typically print before highs in volatile sessions and we'd rather
    underestimate strategy returns than inflate them.

    Multi-symbol-aware: each stop's bar comes from ``symbol_bars[sym]``."""
    # Pass A — ratchet trailing stops against this bar's HIGH.
    for sym, stops in state.stoplosses.items():
        bars = symbol_bars.get(sym)
        if bars is None or ts not in bars.index:
            continue
        high = float(bars.at[ts, "High"])
        for stop in stops:
            if not stop.trailing or stop.trail_pct <= 0:
                continue
            if high > stop.high_water_mark:
                stop.high_water_mark = high
                new_trigger = high * (1 - stop.trail_pct / 100.0)
                if new_trigger > stop.trigger_price:
                    stop.trigger_price = new_trigger

    # Pass B — take-profits (HIGH ≥ trigger).
    for sym in list(state.takeprofits.keys()):
        tps = state.takeprofits.get(sym) or []
        if not tps:
            continue
        bars = symbol_bars.get(sym)
        if bars is None or ts not in bars.index:
            continue
        high = float(bars.at[ts, "High"])
        # Fire lowest-trigger take-profit first (closer to market).
        tps.sort(key=lambda t: t.trigger_price)
        remaining_tps: list[TakeprofitOrder] = []
        for tp in tps:
            if high < tp.trigger_price:
                remaining_tps.append(tp)
                continue
            held = state.holdings.get(sym, 0)
            exec_qty = min(tp.quantity, held)
            if exec_qty <= 0:
                continue
            fill = tp.trigger_price * (1 - _FRICTION)
            _record_sell(
                state, sym, exec_qty, fill, ts,
                signals_out, trades_out, reason="takeprofit",
            )
        if state.holdings.get(sym, 0) > 0 and remaining_tps:
            state.takeprofits[sym] = remaining_tps
        else:
            state.takeprofits.pop(sym, None)

    # Pass C — stoplosses (LOW ≤ trigger).
    for sym in list(state.stoplosses.keys()):
        stops = state.stoplosses.get(sym) or []
        if not stops:
            continue
        bars = symbol_bars.get(sym)
        if bars is None or ts not in bars.index:
            continue
        low = float(bars.at[ts, "Low"])
        # Process highest-trigger stops first so order matches live
        # broker semantics (closer-to-market stop fires earlier).
        stops.sort(key=lambda s: s.trigger_price, reverse=True)
        remaining: list[StoplossOrder] = []
        for stop in stops:
            if low > stop.trigger_price:
                remaining.append(stop)
                continue
            held = state.holdings.get(sym, 0)
            exec_qty = min(stop.quantity, held)
            if exec_qty <= 0:
                continue
            fill = stop.trigger_price * (1 - _FRICTION)
            _record_sell(
                state, sym, exec_qty, fill, ts,
                signals_out, trades_out, reason="stoploss",
            )
        if state.holdings.get(sym, 0) > 0 and remaining:
            state.stoplosses[sym] = remaining
        else:
            state.stoplosses.pop(sym, None)


def _execute_branch(
    branch: Branch, state: SimState,
    symbol_bars: dict[str, pd.DataFrame],
    ts: pd.Timestamp,
    signals_out: list[dict], trades_out: list[dict],
) -> None:
    """Walk a branch's body for one fire. Updates ``state`` in place
    and appends to the chart payload buffers. Each step that operates
    on a symbol resolves its bars via ``symbol_bars[sym]`` so cross-
    asset workflows ('buy A when B's RSI < 30') execute correctly."""
    for step in branch.body:
        st = str(step.get("step_type") or "")
        cfg = step.get("config") or {}
        if st == "condition.numeric":
            if not _eval_condition_numeric(
                cfg, state, symbol_bars, ts, branch,
            ):
                return
            continue
        if st in {
            "condition.position",
            "condition.market_status",
            "condition.time_window",
        }:
            if not _eval_simple_condition(st, cfg, state, ts):
                return
            continue
        if st == "action.place_order":
            sym = str(cfg.get("symbol") or "").upper()
            side = str(cfg.get("side") or "buy").lower()
            product = str(cfg.get("product", "CNC")).upper()
            qty = _resolve_quantity(
                cfg.get("quantity"), state, symbol_bars, ts, branch,
            )
            if qty <= 0 or not sym:
                continue
            sym_bars = symbol_bars.get(sym)
            if sym_bars is None or ts not in sym_bars.index:
                continue
            row = sym_bars.loc[ts]
            # Friction direction depends on the cash flow direction:
            # buying / covering pays the offer (open + friction), selling
            # / shorting hits the bid (open - friction).
            paying = side in {"buy", "cover"}
            fill_price = float(row["Open"]) * (
                1 + _FRICTION if paying else 1 - _FRICTION
            )
            held = state.holdings.get(sym, 0)
            if side == "buy":
                # Long open or extension. Cash check: don't go negative.
                if fill_price * qty > state.cash:
                    signals_out.append({
                        "t": ts.date().isoformat(),
                        "side": "buy_skipped",
                        "price": fill_price,
                        "qty": qty,
                    })
                    continue
                _record_trade(
                    state, sym, +qty, fill_price, product, ts,
                    signals_out, trades_out, reason="trade",
                )
            elif side == "sell":
                # Long close — clamp to held long quantity. (Use 'short'
                # to open a new short.)
                exec_qty = min(qty, max(0, held))
                if exec_qty <= 0:
                    continue
                _record_trade(
                    state, sym, -exec_qty, fill_price, product, ts,
                    signals_out, trades_out, reason="sell",
                )
            elif side == "short":
                # Short open or extension. Naive margin model: deny if
                # the proceeds would push notional shorted past 50% of
                # current equity (rough margin check). This is generous
                # — real brokers cap at 25-33% — but keeps the simulator
                # from spiraling on absurd workflows.
                cur_equity = state.cash + sum(
                    q * float(symbol_bars[s].at[ts, "Close"])
                    for s, q in state.holdings.items()
                    if s in symbol_bars and ts in symbol_bars[s].index
                )
                short_notional = (
                    sum(
                        abs(q) * float(symbol_bars[s].at[ts, "Close"])
                        for s, q in state.holdings.items()
                        if q < 0 and s in symbol_bars
                        and ts in symbol_bars[s].index
                    )
                    + qty * fill_price
                )
                if short_notional > 0.5 * max(cur_equity, _STARTING_CAPITAL):
                    signals_out.append({
                        "t": ts.date().isoformat(),
                        "side": "short_skipped",
                        "price": fill_price,
                        "qty": qty,
                    })
                    continue
                _record_trade(
                    state, sym, -qty, fill_price, product, ts,
                    signals_out, trades_out, reason="trade",
                )
            elif side == "cover":
                # Buy-to-close-short — clamp to current short quantity.
                short_held = -min(0, held)
                exec_qty = min(qty, short_held)
                if exec_qty <= 0:
                    continue
                _record_trade(
                    state, sym, +exec_qty, fill_price, product, ts,
                    signals_out, trades_out, reason="cover",
                )
            continue
        if st == "action.set_stoploss":
            sym = str(cfg.get("symbol") or "").upper()
            if not sym or state.holdings.get(sym, 0) <= 0:
                continue
            held = state.holdings.get(sym, 0)
            qty_raw = cfg.get("quantity")
            qty = (
                _resolve_quantity(qty_raw, state, symbol_bars, ts, branch)
                if qty_raw is not None else held
            )
            qty = min(qty if qty > 0 else held, held)
            trigger = cfg.get("trigger_price")
            trail_pct = float(cfg.get("trigger_offset_pct") or 0.0)
            if trigger is None and trail_pct > 0:
                avg = state.avg_buy_price.get(sym, 0.0)
                trigger = avg * (1 - trail_pct / 100.0)
            if trigger is None or float(trigger) <= 0:
                continue
            # Initial high-water mark = current bar's CLOSE so the
            # trail starts at "today's close × (1 - pct)" — matches what
            # a trader expects on day one.
            sym_bars = symbol_bars.get(sym)
            init_hwm = (
                float(sym_bars.at[ts, "Close"])
                if sym_bars is not None and ts in sym_bars.index
                else state.avg_buy_price.get(sym, 0.0)
            )
            state.stoplosses.setdefault(sym, []).append(
                StoplossOrder(
                    trigger_price=float(trigger),
                    quantity=int(qty),
                    set_at=ts,
                    trailing=bool(cfg.get("trailing", False)),
                    trail_pct=trail_pct,
                    high_water_mark=init_hwm,
                )
            )
            continue
        if st == "action.set_takeprofit":
            sym = str(cfg.get("symbol") or "").upper()
            if not sym or state.holdings.get(sym, 0) <= 0:
                continue
            held = state.holdings.get(sym, 0)
            qty_raw = cfg.get("quantity")
            qty = (
                _resolve_quantity(qty_raw, state, symbol_bars, ts, branch)
                if qty_raw is not None else held
            )
            qty = min(qty if qty > 0 else held, held)
            trigger = cfg.get("trigger_price")
            if trigger is None and cfg.get("trigger_offset_pct") is not None:
                avg = state.avg_buy_price.get(sym, 0.0)
                trigger = avg * (1 + float(cfg["trigger_offset_pct"]) / 100.0)
            if trigger is None or float(trigger) <= 0:
                continue
            state.takeprofits.setdefault(sym, []).append(
                TakeprofitOrder(
                    trigger_price=float(trigger),
                    quantity=int(qty),
                    set_at=ts,
                )
            )
            continue
        if st == "action.squareoff_symbol":
            sym = str(cfg.get("symbol") or "").upper()
            product_filter = str(cfg.get("product", "MIS")).upper()
            held = state.holdings.get(sym, 0)
            if held <= 0:
                continue
            if state.product.get(sym, "CNC") != product_filter:
                continue
            sym_bars = symbol_bars.get(sym)
            if sym_bars is None or ts not in sym_bars.index:
                continue
            row = sym_bars.loc[ts]
            # Squareoff is an EOD-style action — the live executor fires
            # toward the day's close, so we fill at CLOSE not OPEN. This
            # matters for intraday MIS scenarios where the buy filled at
            # OPEN of the same bar; using CLOSE captures the realised
            # intraday move instead of zeroing it out.
            fill = float(row["Close"]) * (1 - _FRICTION)
            _record_sell(
                state, sym, held, fill, ts,
                signals_out, trades_out, reason="squareoff",
            )
            continue
        if st == "action.squareoff_all_intraday":
            for sym in list(state.holdings.keys()):
                if state.product.get(sym) != "MIS":
                    continue
                qty = state.holdings.get(sym, 0)
                if qty == 0:
                    continue
                sym_bars = symbol_bars.get(sym)
                if sym_bars is None or ts not in sym_bars.index:
                    continue
                # Long → sell at close (1-f); short → buy-to-cover at
                # close (1+f). Use signed_qty = -current_qty to flatten.
                fill = float(sym_bars.at[ts, "Close"]) * (
                    1 - _FRICTION if qty > 0 else 1 + _FRICTION
                )
                _record_trade(
                    state, sym, -qty, fill, state.product.get(sym, "MIS"),
                    ts, signals_out, trades_out, reason="squareoff",
                )
            continue
        if st == "action.squareoff_all":
            # Close every position regardless of product (long + short,
            # CNC + MIS). Used as the basket-exit step.
            for sym in list(state.holdings.keys()):
                qty = state.holdings.get(sym, 0)
                if qty == 0:
                    continue
                sym_bars = symbol_bars.get(sym)
                if sym_bars is None or ts not in sym_bars.index:
                    continue
                fill = float(sym_bars.at[ts, "Close"]) * (
                    1 - _FRICTION if qty > 0 else 1 + _FRICTION
                )
                _record_trade(
                    state, sym, -qty, fill,
                    state.product.get(sym, "CNC"),
                    ts, signals_out, trades_out, reason="squareoff",
                )
            continue
        if st == "action.allocate_basket":
            legs_cfg = cfg.get("legs") or []
            total_inr_raw = cfg.get("total_inr")
            total_inr = (
                float(_resolve_ref(total_inr_raw, state, symbol_bars, ts, branch))
                if total_inr_raw is not None else 0.0
            )
            if not legs_cfg or total_inr <= 0:
                continue
            # Normalise weights so they sum to 1.
            weights_sum = sum(
                float(leg.get("weight", 0)) for leg in legs_cfg
            ) or 1.0
            for leg in legs_cfg:
                leg_sym = str(leg.get("symbol") or "").upper()
                leg_side = str(leg.get("side", "long")).lower()
                w = float(leg.get("weight", 0)) / weights_sum
                if not leg_sym or w <= 0:
                    continue
                lb = symbol_bars.get(leg_sym)
                if lb is None or ts not in lb.index:
                    continue
                slice_inr = total_inr * w
                # Long fills at OPEN+f, short fills at OPEN-f (proceeds
                # reduced by friction on the open side, same as
                # set_stoploss / takeprofit).
                paying = leg_side == "long"
                fill = float(lb.at[ts, "Open"]) * (
                    1 + _FRICTION if paying else 1 - _FRICTION
                )
                qty_abs = int(slice_inr // fill)
                if qty_abs <= 0:
                    continue
                signed = +qty_abs if paying else -qty_abs
                # Long-side cash check.
                if paying and slice_inr > state.cash:
                    signals_out.append({
                        "t": ts.date().isoformat(),
                        "side": "buy_skipped",
                        "price": fill,
                        "qty": qty_abs,
                    })
                    continue
                _record_trade(
                    state, leg_sym, signed, fill, "CNC", ts,
                    signals_out, trades_out, reason="basket",
                )
            continue
        # Other step types are silently skipped per the eligibility map.


# ── Public entry ─────────────────────────────────────────────────────


def backtest_workflow(
    steps: list[dict[str, Any]],
    *,
    period: str = "5y",
    name: str = "Workflow",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    benchmark_symbol: Optional[str] = None,
) -> IndicatorBacktestResult:
    """Simulate a workflow draft over historical daily bars.

    Returns the same ``IndicatorBacktestResult`` shape the indicator
    backtester produces so the FE chart card reuses without changes.
    Raises ``ValueError`` when the workflow is not eligible or when
    the data fetch fails.

    ``start_date`` / ``end_date`` (ISO ``YYYY-MM-DD``) clip the bar
    series to a fixed window AFTER the period fetch. Useful for event-
    driven backtests like "the 4 weeks around 2022-02-24" — pass
    ``start_date='2022-02-10'`` and ``end_date='2022-03-10'``. When
    omitted, the full ``period`` window is simulated.

    ``benchmark_symbol`` overrides the buy-and-hold benchmark which
    defaults to the primary trade symbol. Useful for basket / pairs
    backtests where comparing to a single leg is misleading — pass
    ``benchmark_symbol='NIFTYBEES'`` to compare against the NIFTY 50.
    """
    elig = check_eligibility(steps)
    if not elig.eligible:
        raise ValueError(elig.reason or "workflow not backtestable")

    # Collect every symbol any branch references — trigger.symbol +
    # action symbols + stoploss + squareoff. Multi-symbol workflows
    # like 'buy RELIANCE when TCS RSI < 30' need both feeds.
    all_symbols: set[str] = set()
    for b in elig.branches:
        all_symbols.update(b.all_symbols())
    if not all_symbols:
        raise ValueError(
            "couldn't infer any target symbol from the workflow — "
            "every branch needs at least one symbol on its trigger or"
            " an order/stoploss/squareoff step."
        )

    # Pick one anchor symbol for the chart's price curve + buy-and-hold
    # benchmark + summary line. Prefer the symbol the workflow actually
    # TRADES (first place_order target) over its trigger symbol — for
    # cross-asset workflows like 'buy RELIANCE when TCS RSI < 30' the
    # user reads the strategy as "RELIANCE", not "TCS".
    primary_symbol: Optional[str] = None
    for b in elig.branches:
        for step in b.body:
            if step.get("step_type") == "action.place_order":
                cfg = step.get("config") or {}
                s = cfg.get("symbol")
                if isinstance(s, str) and s.strip():
                    primary_symbol = s.upper().strip()
                    break
        if primary_symbol:
            break
    if primary_symbol is None:
        # No place_order anywhere. Fall back to whatever primary_symbol()
        # picked (trigger symbol of the first branch), then to a
        # deterministic alphabetical pick from all_symbols.
        for b in elig.branches:
            ps = b.primary_symbol()
            if ps:
                primary_symbol = ps
                break
    if primary_symbol is None:
        primary_symbol = sorted(all_symbols)[0]

    # Fetch bars for every referenced symbol — plus the benchmark if
    # the user supplied one. Failure on any one symbol surfaces the
    # per-symbol error rather than silently dropping it.
    symbols_to_fetch = set(all_symbols)
    bench_sym = (benchmark_symbol or "").upper().strip() or None
    if bench_sym:
        symbols_to_fetch.add(bench_sym)
    symbol_bars: dict[str, pd.DataFrame] = {}
    for sym in sorted(symbols_to_fetch):
        symbol_bars[sym] = _load_bars(sym, period)

    # Fixed-window clip: trim every loaded bar series to [start, end]
    # before the simulator walks it. Keeps the equity curve, trade
    # log, and benchmark all aligned to the user's window.
    if start_date or end_date:
        start_ts = pd.Timestamp(start_date) if start_date else None
        end_ts = pd.Timestamp(end_date) if end_date else None
        for sym in list(symbol_bars.keys()):
            df = symbol_bars[sym]
            if start_ts is not None:
                df = df[df.index >= start_ts]
            if end_ts is not None:
                df = df[df.index <= end_ts]
            if df.empty:
                raise ValueError(
                    f"window {start_date}..{end_date} contains no bars "
                    f"for {sym}"
                )
            symbol_bars[sym] = df

    primary_bars = symbol_bars[primary_symbol]

    # Build a per-bar event lookup. Each branch's trigger is expanded
    # against the trigger.symbol's bars (or the branch's primary symbol
    # for trigger.schedule). The walker iterates the union of every
    # symbol's trading days so cross-asset signals don't get dropped
    # when only one feed traded on a given date.
    union_index = primary_bars.index
    for sym, bars in symbol_bars.items():
        union_index = union_index.union(bars.index)
    union_index = union_index.sort_values()

    events_by_ts: dict[pd.Timestamp, list[int]] = {}
    for i, b in enumerate(elig.branches):
        trigger_sym = b.trigger_symbol() or b.primary_symbol() or primary_symbol
        bars_for_trigger = symbol_bars.get(trigger_sym, primary_bars)
        if b.trigger_type == "trigger.schedule":
            fires = _expand_schedule(b.trigger_config, union_index)
        elif b.trigger_type == "trigger.indicator":
            fires = _expand_indicator(b.trigger_config, bars_for_trigger)
        elif b.trigger_type == "trigger.price":
            fires = _expand_price(b.trigger_config, bars_for_trigger)
        else:
            fires = []
        for ts in fires:
            events_by_ts.setdefault(ts, []).append(i)

    state = SimState()
    signals: list[dict] = []
    trades: list[dict] = []
    for ts in union_index:
        # 1. Evaluate active stoplosses against each held symbol's bar
        #    range BEFORE any events fire — the live broker would also
        #    process triggered stops before new orders on the same tick.
        _evaluate_stoplosses(state, symbol_bars, ts, signals, trades)
        # 2. Execute any branches whose trigger fired this bar.
        for branch_idx in events_by_ts.get(ts, []):
            _execute_branch(
                elig.branches[branch_idx], state, symbol_bars, ts,
                signals, trades,
            )

    # Bind the chart-equity-curve walker below to the primary symbol's
    # bar series for the price curve, but mark-to-market the entire
    # multi-symbol portfolio.
    bars = primary_bars

    # Build the equity curve from daily closes. Multi-symbol-aware: each
    # held symbol is marked to market against ITS OWN daily close, not
    # the primary's. The chart's price_curve still tracks the primary
    # symbol so the user has one anchor to read the strategy against.
    price_curve: list[dict] = []
    equity_curve: list[dict] = []
    walking_state = SimState()
    trade_iter = iter(trades)
    next_trade = next(trade_iter, None)
    for ts, row in bars.iterrows():
        # Apply any trades scheduled today. Uses signed qty so short
        # legs replay correctly: trade rows still have side='buy'/'sell'
        # for cash-flow direction, but holdings can go negative.
        while next_trade is not None and next_trade["t"] == ts.date().isoformat():
            tr = next_trade
            sym = tr["symbol"]
            signed = (
                +int(tr["qty"]) if tr["side"] == "buy" else -int(tr["qty"])
            )
            walking_state.cash -= signed * tr["price"]
            prev_qty = walking_state.holdings.get(sym, 0)
            new_qty = prev_qty + signed
            walking_state.holdings[sym] = new_qty
            if new_qty == 0:
                walking_state.avg_buy_price.pop(sym, None)
            elif prev_qty == 0 or (prev_qty > 0) != (new_qty > 0):
                walking_state.avg_buy_price[sym] = tr["price"]
            elif abs(new_qty) > abs(prev_qty):
                prev_avg = walking_state.avg_buy_price.get(sym, tr["price"])
                walking_state.avg_buy_price[sym] = (
                    (prev_avg * abs(prev_qty) + tr["price"] * abs(signed))
                    / abs(new_qty)
                )
            next_trade = next(trade_iter, None)
        close = float(row["Close"])
        market_value = 0.0
        for sym, qty in walking_state.holdings.items():
            if qty <= 0:
                continue
            sym_bars = symbol_bars.get(sym)
            if sym_bars is not None and ts in sym_bars.index:
                market_value += qty * float(sym_bars.at[ts, "Close"])
            else:
                # Symbol didn't trade today (or isn't in the registry —
                # shouldn't happen). Carry the previous close, falling
                # back to the entry price so equity stays conservative.
                market_value += qty * walking_state.avg_buy_price.get(
                    sym, close,
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

    # Buy & hold benchmark — defaults to the primary trade symbol but
    # the caller can pass benchmark_symbol='NIFTYBEES' (or any other
    # symbol in symbol_bars) to compare against an index / proxy.
    bench_bars = (
        symbol_bars.get(bench_sym, primary_bars)
        if bench_sym else primary_bars
    )
    if len(bench_bars) >= 2:
        bench_pct = round(
            (
                float(bench_bars["Close"].iloc[-1])
                / float(bench_bars["Close"].iloc[0]) - 1
            ) * 100, 2,
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

    # Pull a meaningful indicator label + series for the chart card's
    # bottom panel. The card titles the panel "<INDICATOR>(<PERIOD>)"
    # and renders a line chart of indicator_curve. Previously we
    # hardcoded indicator="indicator", period=0, curve=[] which read as
    # "INDICATOR(0)" with an empty box. Now: prefer the first
    # trigger.indicator branch for a real (indicator, period, operator,
    # threshold, series) tuple. For schedule-/price-only workflows
    # there's nothing to chart in the indicator panel — leave the
    # series empty but use a descriptive label.
    chart_indicator = "schedule"
    chart_period = 0
    chart_operator = "-"
    chart_threshold = 0.0
    chart_curve: list[dict] = []
    for b in elig.branches:
        if b.trigger_type != "trigger.indicator":
            continue
        cfg = b.trigger_config or {}
        ind_key = str(cfg.get("indicator") or "").lower()
        period_n = int(cfg.get("period") or 0) or (
            _ind_spec(ind_key).default_period if _ind_spec(ind_key) else 0
        )
        sym = (b.trigger_symbol() or primary_symbol).upper()
        bars_for_ind = symbol_bars.get(sym, primary_bars)
        series = _ind_series(bars_for_ind, ind_key, period_n)
        chart_indicator = ind_key or "indicator"
        chart_period = period_n
        chart_operator = str(cfg.get("operator") or "-")
        chart_threshold = float(cfg.get("value") or 0.0)
        if series is not None:
            chart_curve = [
                {"t": ts.date().isoformat(), "v": round(float(v), 4)}
                for ts, v in series.dropna().items()
            ]
        break
    if chart_indicator == "schedule" and elig.branches:
        # No indicator trigger anywhere — label by the first branch's
        # trigger type so "schedule"/"price" reads sensibly instead of
        # the literal string "indicator".
        chart_indicator = (
            elig.branches[0].trigger_type.split(".", 1)[-1] or "trigger"
        )

    return IndicatorBacktestResult(
        symbol=primary_symbol,
        indicator=chart_indicator,
        indicator_period=chart_period,
        operator=chart_operator,
        threshold=chart_threshold,
        period_label=period,
        price_curve=price_curve,
        equity_curve=equity_curve,
        indicator_curve=chart_curve,
        signals=enriched_signals,
        metrics=metrics,
        bench_buy_hold_return_pct=bench_pct,
        summary_text=summary,
    )
