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


# P1 cost convergence (2026-05-29 audit): was a flat 10 bps/leg that
# under-counted real frictions. The per-leg average from the shared India
# delivery model — round-trip (1±buy)+(1±sell) is identical to per-side, so a
# single averaged constant keeps every (1±_FRICTION) fill realistic without
# touching the fill loops. See backend/services/trading_costs.py.
from backend.services.trading_costs import leg_bps as _leg_bps
_FRICTION = (_leg_bps("buy") + _leg_bps("sell")) / 2.0
_STARTING_CAPITAL = 1_000_000.0


# ── Eligibility ──────────────────────────────────────────────────────


# Trigger types we can replay against historical bars.
_BACKTESTABLE_TRIGGERS = {
    "trigger.schedule",
    "trigger.indicator",
    "trigger.price",
    # Market-relative-time anchors (open / close / pre_open / post_close +
    # signed offset) resolve to a deterministic cron via the runtime
    # scheduler helper. The backtester normalises them to trigger.schedule
    # during eligibility parsing — see `check_eligibility` below.
    "trigger.market_relative_time",
    # Compound entry trigger — fire-time enumerated by walking the
    # DSL tree against historical bars (one row per bar where the
    # tree returns Ternary.TRUE).
    "trigger.compound",
    # Compound exit trigger — fires on every bar but only after the
    # position-aware tree returns True against current SimState. The
    # branch's place_order(sell) self-clamps when no position is held.
    "trigger.exit_compound",
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
    # fetch.fundamental: resolved on demand by _resolve_ref against the
    # Moneycontrol-derived financials DB with point-in-time
    # (availability_date <= ts) filtering. See _resolve_ref.
    "fetch.fundamental",
}

# Steps that block backtesting entirely.
_BLOCKING_STEPS_REASON = {
    "trigger.event":   "trigger.event needs an historical event calendar we don't keep — try a schedule or indicator trigger.",
    "trigger.webhook": "trigger.webhook can only fire from external traffic, so there's nothing historical to replay.",
    "trigger.manual":  "trigger.manual fires when you click 'Run now' — there's no historical signal to replay.",
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
        action.allocate_basket and every DSL-tree leaf symbol on
        trigger.compound / trigger.exit_compound / condition.compound.
        Used to build the multi-symbol bar registry."""
        out: set[str] = set()
        ts = self.trigger_symbol()
        if ts:
            out.add(ts)
        # Tree symbols from the trigger config (compound / exit_compound).
        if self.trigger_type in ("trigger.compound", "trigger.exit_compound"):
            _collect_tree_symbols(self.trigger_config.get("entry"), out)
            target = self.trigger_config.get("target_symbol")
            if isinstance(target, str) and target.strip():
                out.add(target.upper().strip())
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
            # condition.compound / trigger.compound nested trees.
            if step.get("step_type") in (
                "condition.compound", "trigger.compound", "trigger.exit_compound",
            ):
                _collect_tree_symbols(cfg.get("entry"), out)
        return out


def _collect_tree_symbols(node: Any, out: set[str]) -> None:
    """Recursively pull every ``symbol`` (and ``a`` / ``b`` for spread
    nodes) from a DSL tree dict. Skips nodes without a symbol field
    (constant, math, logic, comparison, etc.). The structural walk
    intentionally doesn't validate node shape — it's the validator's
    job to reject malformed trees; here we just collect leaf symbols
    for bar pre-loading."""
    if isinstance(node, dict):
        sym = node.get("symbol")
        if isinstance(sym, str) and sym.strip():
            out.add(sym.upper().strip())
        # SpreadNode uses 'a' / 'b' instead of 'symbol'.
        for key in ("a", "b"):
            s = node.get(key)
            if isinstance(s, str) and s.strip():
                out.add(s.upper().strip())
        for v in node.values():
            _collect_tree_symbols(v, out)
    elif isinstance(node, list):
        for item in node:
            _collect_tree_symbols(item, out)


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

    # Normalise market_relative_time → schedule. The simulator dispatches
    # on `trigger_type == "trigger.schedule"`, so once we've resolved the
    # anchor+offset to a concrete cron, the existing `_expand_schedule`
    # path handles fire-time computation. We mutate the Branch in place
    # because nothing downstream needs the original type.
    for b in branches:
        if b.trigger_type == "trigger.market_relative_time":
            try:
                from backend.workflows.scheduler import (
                    _resolve_market_relative_time,
                )
                cron, tz = _resolve_market_relative_time(b.trigger_config)
            except Exception as e:  # noqa: BLE001 — bubble as eligibility fail
                return Eligibility(
                    False,
                    f"Can't backtest this market_relative_time trigger: {e}",
                )
            b.trigger_type = "trigger.schedule"
            b.trigger_config = {"cron": cron, "timezone": tz}

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
    # Entry timestamp of the currently-open long lot per symbol. Set
    # when a flat-to-long transition records a buy; cleared when the
    # position fully closes. Used by trigger.exit_compound to resolve
    # PositionNode.bars_held / peak_unrealised_pct / drawdown_from_peak_pct.
    entry_ts: dict[str, pd.Timestamp] = field(default_factory=dict)


def _yf_symbol(symbol: str, exchange: str = "NSE") -> str:
    # [C4] delegate to the shared resolver so index aliases map to ^
    # tickers (NIFTY→^NSEI, SENSEX→^BSESN, BANKNIFTY→^NSEBANK) and
    # shorthand (RIL→RELIANCE). The old naive ".NS" suffix turned NIFTY
    # into the dead ticker NIFTY.NS → "insufficient data … got 0 bars"
    # for any backtest whose trigger references an index.
    from backend.market.yfinance_service import resolve_symbol
    return resolve_symbol(symbol)


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

            # fetch.fundamental does not depend on price bars — resolve it
            # before the bars-existence guard so it works even when the
            # symbol has no yfinance OHLCV at this timestamp. Generic
            # named-metric / formula handling lives in financials_db.resolve_metric.
            if st == "fetch.fundamental":
                from backend.market.financials_db import resolve_metric
                return resolve_metric(
                    sym,
                    str(cfg.get("metric") or "").lower(),
                    formula=cfg.get("formula"),
                    as_of_date=ts.date(),
                )

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


class _BarStrictAccessor:
    """Minimal DataAccessor for evaluating a DSL tree at one historical
    bar inside the steps[] backtester.

    Wraps the simulator's ``symbol_bars`` dict + the current
    ``ts``. Honours the no-lookahead invariant by indexing strictly
    ``<= ts``. Implements just enough of the DataAccessor protocol for
    condition.compound / trigger.exit_compound to evaluate.

    Position-aware evaluation goes through ``_PositionAwareAccessor``
    which composes around this one — kept separate so entry-tree
    evaluation (condition.compound) and exit-tree evaluation
    (trigger.exit_compound) reuse the same market-data path.
    """

    def __init__(
        self,
        symbol_bars: dict[str, pd.DataFrame],
        ts: pd.Timestamp,
    ) -> None:
        self._symbol_bars = symbol_bars
        self._ts = ts
        # Per-walk indicator cache (same role as LiveDataAccessor._call_cache).
        self._cache: dict[tuple, Optional[float]] = {}

    def _bars_up_to(self, symbol: str) -> Optional[pd.DataFrame]:
        df = self._symbol_bars.get(symbol.upper())
        if df is None or df.empty:
            return None
        sliced = df[df.index <= self._ts]
        return sliced if not sliced.empty else None

    def get_price(
        self,
        *,
        symbol: str,
        exchange: str = "NSE",
        basis: str = "close",
        offset: int = 0,
    ) -> Optional[float]:
        df = self._bars_up_to(symbol)
        if df is None:
            return None
        idx = len(df) - 1 - int(offset)
        if idx < 0:
            return None
        col = {"open": "Open", "high": "High", "low": "Low", "close": "Close"}.get(
            (basis or "close").lower(),
        )
        if col is None or col not in df.columns:
            return None
        val = df[col].iloc[idx]
        if pd.isna(val):
            return None
        return float(val)

    def get_indicator(
        self,
        *,
        symbol: str,
        indicator: str,
        period: int,
        exchange: str = "NSE",
        component: Optional[str] = None,
        offset: int = 0,
    ) -> Optional[float]:
        comp_key = component.lower() if component else None
        key = (
            "ind", symbol.upper(), indicator.lower(), int(period),
            comp_key, int(offset),
        )
        if key in self._cache:
            return self._cache[key]
        df = self._bars_up_to(symbol)
        if df is None:
            self._cache[key] = None
            return None
        from backend.services.backtest_indicators import (
            compute_series_component,
        )
        # The backtester's bars carry capitalised OHLCV columns;
        # compute_series_component normalises internally.
        try:
            series = compute_series_component(df, indicator, period, component=comp_key)
        except Exception:
            series = None
        if series is None:
            self._cache[key] = None
            return None
        cleaned = series.dropna()
        if cleaned.empty or len(cleaned) <= int(offset):
            self._cache[key] = None
            return None
        val = cleaned.iloc[-1 - int(offset)]
        result = None if val is None or pd.isna(val) else float(val)
        self._cache[key] = result
        return result

    def get_volume(
        self,
        *,
        symbol: str,
        bars: int = 1,
        exchange: str = "NSE",
        offset: int = 0,
    ) -> Optional[float]:
        df = self._bars_up_to(symbol)
        if df is None or "Volume" not in df.columns:
            return None
        end = len(df) - int(offset)
        start = max(0, end - int(bars))
        if end <= 0 or start >= end:
            return None
        window = df["Volume"].iloc[start:end]
        if window.isna().any():
            return None
        return float(window.sum())

    def get_position_field(
        self, *, field: str, basis: Optional[str] = None,
    ) -> Optional[float]:
        # Entry-tree default; exit-tree evaluation wraps this.
        return None

    _WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

    def get_session_day(self) -> Optional[str]:
        try:
            return self._WEEKDAYS[self._ts.dayofweek]
        except (AttributeError, IndexError):
            return None


class _BacktestPositionAwareAccessor:
    """Backtest analogue of scheduler._PositionAwareAccessor.

    Wraps a ``_BarStrictAccessor`` and answers PositionNode reads
    against the current ``SimState`` for ``symbol``. Returns None for
    fields that can't be resolved (no held position, no entry_ts on
    record, etc.) — Kleene UNKNOWN handles the rest."""

    def __init__(
        self,
        inner: "_BarStrictAccessor",
        state: SimState,
        symbol: str,
        symbol_bars: dict[str, pd.DataFrame],
        ts: pd.Timestamp,
    ) -> None:
        self._inner = inner
        self._state = state
        self._symbol = symbol.upper()
        self._symbol_bars = symbol_bars
        self._ts = ts

    def get_price(self, **kw):
        return self._inner.get_price(**kw)

    def get_indicator(self, **kw):
        return self._inner.get_indicator(**kw)

    def get_volume(self, **kw):
        return self._inner.get_volume(**kw)

    def get_session_day(self):
        return self._inner.get_session_day()

    def get_position_field(
        self, *, field: str, basis: Optional[str] = None,
    ) -> Optional[float]:
        sym = self._symbol
        qty = self._state.holdings.get(sym, 0)
        if qty <= 0:
            return None  # No open long position.
        entry_price = self._state.avg_buy_price.get(sym)
        if entry_price is None or entry_price == 0.0:
            return None
        if field == "entry_price":
            return float(entry_price)
        if field in ("unrealised_pct", "unrealised_abs"):
            current = self._current_price(basis)
            if current is None:
                return None
            if field == "unrealised_abs":
                return current - entry_price
            return (current - entry_price) / entry_price
        if field == "bars_held":
            entry_ts = self._state.entry_ts.get(sym)
            if entry_ts is None:
                return None
            df = self._symbol_bars.get(sym)
            if df is None:
                return None
            # bars between entry_ts (exclusive) and current ts (inclusive).
            window = df[(df.index > entry_ts) & (df.index <= self._ts)]
            return float(len(window))
        if field in ("peak_unrealised_pct", "drawdown_from_peak_pct"):
            entry_ts = self._state.entry_ts.get(sym)
            df = self._symbol_bars.get(sym)
            if entry_ts is None or df is None:
                return None
            window = df[(df.index >= entry_ts) & (df.index <= self._ts)]
            if window.empty:
                return None
            peak_close = float(window["Close"].max())
            peak_pct = (peak_close - entry_price) / entry_price
            if field == "peak_unrealised_pct":
                return peak_pct
            current = self._current_price(basis="close")
            if current is None:
                return None
            cur_pct = (current - entry_price) / entry_price
            return max(0.0, peak_pct - cur_pct)
        return None

    def _current_price(self, basis: Optional[str]) -> Optional[float]:
        return self._inner.get_price(
            symbol=self._symbol, basis=(basis or "close"), offset=0,
        )


def _eval_exit_compound(
    cfg: dict[str, Any], state: SimState,
    symbol_bars: dict[str, pd.DataFrame],
    ts: pd.Timestamp, branch: "Branch",
) -> bool:
    """Backtest gate for trigger.exit_compound. Returns True when the
    tree fires (so the branch body should run), False otherwise.

    Resolves the target symbol from the trigger config or — when the
    user didn't specify — from the first place_order(sell) in the
    branch body. The position-aware accessor returns None for
    PositionNode leaves when no position is held, so the natural
    Kleene flow keeps the trigger silent on flat bars."""
    entry_raw = cfg.get("entry")
    if not isinstance(entry_raw, dict):
        return False
    target = cfg.get("target_symbol")
    if not (isinstance(target, str) and target.strip()):
        # Fall back to the first place_order(sell)'s symbol.
        for step in branch.body:
            if step.get("step_type") == "action.place_order":
                step_cfg = step.get("config") or {}
                if str(step_cfg.get("side", "")).lower() == "sell":
                    candidate = step_cfg.get("symbol")
                    if isinstance(candidate, str) and candidate.strip():
                        target = candidate
                        break
    if not (isinstance(target, str) and target.strip()):
        return False
    symbol = target.upper().strip()
    if state.holdings.get(symbol, 0) <= 0:
        return False  # Nothing to exit.
    try:
        from pydantic import TypeAdapter
        from backend.workflows.dsl.evaluator import Ternary, evaluate
        from backend.workflows.dsl.schema import Tree
        tree = TypeAdapter(Tree).validate_python(entry_raw)
    except Exception as exc:  # noqa: BLE001
        logger.info("[backtest.exit_compound] tree parse failed: %s", exc)
        return False
    inner = _BarStrictAccessor(symbol_bars, ts)
    accessor = _BacktestPositionAwareAccessor(
        inner, state, symbol, symbol_bars, ts,
    )
    try:
        result = evaluate(tree, accessor=accessor, prev_state={})
    except Exception as exc:  # noqa: BLE001
        logger.info("[backtest.exit_compound] eval crashed: %s", exc)
        return False
    return result.value is Ternary.TRUE


def _expand_compound(
    cfg: dict[str, Any], bars: pd.DataFrame,
    symbol_bars: dict[str, pd.DataFrame],
) -> list[pd.Timestamp]:
    """Per-bar fire-time enumeration for trigger.compound. Walks the
    period and returns every bar where the entry tree evaluates True
    against the bar-strict accessor. crosses_above / crosses_below
    state is threaded via a single prev_state dict so transitions
    fire correctly across consecutive bars."""
    entry_raw = cfg.get("entry")
    if not isinstance(entry_raw, dict):
        return []
    try:
        from pydantic import TypeAdapter
        from backend.workflows.dsl.evaluator import Ternary, evaluate
        from backend.workflows.dsl.schema import Tree
        tree = TypeAdapter(Tree).validate_python(entry_raw)
    except Exception as exc:  # noqa: BLE001
        logger.info("[backtest.compound] tree parse failed: %s", exc)
        return []
    prev_state: dict[str, float] = {}
    fires: list[pd.Timestamp] = []
    for ts in bars.index:
        accessor = _BarStrictAccessor(symbol_bars, ts)
        try:
            result = evaluate(tree, accessor=accessor, prev_state=prev_state)
        except Exception:
            continue
        prev_state = result.new_state
        if result.value is Ternary.TRUE:
            fires.append(ts)
    return fires


def _expand_exit_compound(union_index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Exit-compound fires every bar — the tree itself decides whether
    to act, and the position-aware gate in ``_execute_branch`` skips
    branches whose tree returns FALSE/UNKNOWN."""
    return list(union_index)


def _eval_condition_compound(
    cfg: dict[str, Any], symbol_bars: dict[str, pd.DataFrame],
    ts: pd.Timestamp,
) -> bool:
    """Walk a DSL tree against historical bars at ``ts``. Returns True
    to continue the branch, False on FALSE or UNKNOWN — same Kleene
    halt semantics as the live executor."""
    entry_raw = cfg.get("entry")
    if not isinstance(entry_raw, dict):
        return False
    try:
        from pydantic import TypeAdapter
        from backend.workflows.dsl.evaluator import Ternary, evaluate
        from backend.workflows.dsl.schema import Tree
        tree = TypeAdapter(Tree).validate_python(entry_raw)
    except Exception as exc:  # noqa: BLE001
        logger.info("[backtest.cond_compound] tree parse failed: %s", exc)
        return False
    accessor = _BarStrictAccessor(symbol_bars, ts)
    try:
        result = evaluate(tree, accessor=accessor, prev_state={})
    except Exception as exc:  # noqa: BLE001
        logger.info("[backtest.cond_compound] eval crashed: %s", exc)
        return False
    return result.value is Ternary.TRUE


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
        state.entry_ts.pop(sym, None)
    elif prev == 0 or (prev > 0) != (new > 0):
        # Fresh open OR sign flip (closed and reopened on the other
        # side in one trade — the entry price is THIS bar's price).
        state.avg_buy_price[sym] = price
        state.product[sym] = product
        state.entry_ts[sym] = ts
    elif abs(new) > abs(prev):
        # Same-direction increase → weighted-average entry price.
        prev_avg = state.avg_buy_price.get(sym, 0.0)
        added = abs(signed_qty)
        state.avg_buy_price[sym] = (
            (prev_avg * abs(prev) + price * added) / abs(new)
        )
        state.product.setdefault(sym, product)
        # Keep the original entry_ts — pyramiding doesn't reset bars_held.
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


# Triggers whose fire is computed from the SAME bar's OHLC (its close, range,
# or an indicator off its close). An order they raise can only be filled at the
# NEXT bar's open — the first price knowable after the signal printed —
# otherwise the backtest trades on information it did not yet have (look-ahead
# bias). Schedule fires (incl. normalised market_relative_time) are known
# a-priori, so they legitimately fill at the current bar's open. This mirrors
# the next-open discipline already enforced by Engine 2b (dsl/backtest).
_SIGNAL_TRIGGERS = frozenset({
    "trigger.indicator",
    "trigger.price",
    "trigger.compound",
    "trigger.exit_compound",
})


def _next_bar_ts(
    bars: pd.DataFrame, ts: pd.Timestamp,
) -> Optional[pd.Timestamp]:
    """First bar strictly after ``ts`` in ``bars.index``, or ``None`` when
    ``ts`` is the last bar (a signal on the final bar can't be filled — there
    is no subsequent open). ``ts`` is assumed present in the index."""
    idx = bars.index
    try:
        pos = idx.get_loc(ts)
    except KeyError:
        return None
    if isinstance(pos, slice):  # duplicate timestamps — shouldn't occur daily
        pos = (pos.stop or len(idx)) - 1
    if not isinstance(pos, int):
        return None
    if pos + 1 >= len(idx):
        return None
    return idx[pos + 1]


def _execute_branch(
    branch: Branch, state: SimState,
    symbol_bars: dict[str, pd.DataFrame],
    ts: pd.Timestamp,
    signals_out: list[dict], trades_out: list[dict],
) -> None:
    """Walk a branch's body for one fire. Updates ``state`` in place
    and appends to the chart payload buffers. Each step that operates
    on a symbol resolves its bars via ``symbol_bars[sym]`` so cross-
    asset workflows ('buy A when B's RSI < 30') execute correctly.

    Exit-compound branches re-evaluate the trigger tree against
    ``state`` at this bar; if the tree didn't fire (no position OR
    tree returned FALSE/UNKNOWN), the branch is skipped before any
    body step runs."""
    # Signal-driven branches decide on THIS bar's data → their orders fill at
    # the next bar's open (no look-ahead). Schedule branches fill same-bar.
    signal_driven = branch.trigger_type in _SIGNAL_TRIGGERS
    if branch.trigger_type == "trigger.exit_compound":
        if not _eval_exit_compound(
            branch.trigger_config, state, symbol_bars, ts, branch,
        ):
            return
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
        if st == "condition.compound":
            if not _eval_condition_compound(cfg, symbol_bars, ts):
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
            # No-look-ahead fill bar: a signal-driven order executes at the
            # NEXT bar's open (the first price knowable after the signal
            # printed); a schedule order fills at the current bar's open.
            fill_ts = _next_bar_ts(sym_bars, ts) if signal_driven else ts
            if fill_ts is None:
                # Signal printed on the final bar — no subsequent open to
                # fill against. Mark it but don't fabricate a fill.
                signals_out.append({
                    "t": ts.date().isoformat(),
                    "side": "no_fill_bar",
                    "price": round(float(sym_bars.at[ts, "Close"]), 2),
                    "qty": qty,
                })
                continue
            row = sym_bars.loc[fill_ts]
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
                        "t": fill_ts.date().isoformat(),
                        "side": "buy_skipped",
                        "price": fill_price,
                        "qty": qty,
                    })
                    continue
                _record_trade(
                    state, sym, +qty, fill_price, product, fill_ts,
                    signals_out, trades_out, reason="trade",
                )
            elif side == "sell":
                # Long close — clamp to held long quantity. (Use 'short'
                # to open a new short.)
                exec_qty = min(qty, max(0, held))
                if exec_qty <= 0:
                    continue
                _record_trade(
                    state, sym, -exec_qty, fill_price, product, fill_ts,
                    signals_out, trades_out, reason="sell",
                )
            elif side == "short":
                # Short open or extension. Naive margin model: deny if
                # the proceeds would push notional shorted past 50% of
                # current equity (rough margin check, at the decision bar).
                # This is generous — real brokers cap at 25-33% — but keeps
                # the simulator from spiraling on absurd workflows.
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
                        "t": fill_ts.date().isoformat(),
                        "side": "short_skipped",
                        "price": fill_price,
                        "qty": qty,
                    })
                    continue
                _record_trade(
                    state, sym, -qty, fill_price, product, fill_ts,
                    signals_out, trades_out, reason="trade",
                )
            elif side == "cover":
                # Buy-to-close-short — clamp to current short quantity.
                short_held = -min(0, held)
                exec_qty = min(qty, short_held)
                if exec_qty <= 0:
                    continue
                _record_trade(
                    state, sym, +exec_qty, fill_price, product, fill_ts,
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
    trial_group: Optional[str] = None,
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
    # The chat path validates drafts via DraftStep (propose.py), whose
    # Pydantic dump drops `step_index`. Without it, `_resolve_ref` can't
    # match `{{context.N.value}}` references — so any condition.numeric
    # that reads a fetch.* output silently evaluates to False and no
    # trades fire. Re-attach step_index from list position before
    # eligibility parsing so chat-built workflows simulate correctly.
    steps = [
        ({**s, "step_index": s.get("step_index", i)} if isinstance(s, dict) else s)
        for i, s in enumerate(steps)
    ]
    # R4a: pre-flight Mustache-ref resolvability BEFORE eligibility so
    # the user sees the structured blocker text ("backtester cannot
    # resolve {{ context.1.total_value_inr }}") instead of the
    # downstream `could not convert string to float` crash.
    try:
        from backend.services.backtest_resolvability import check_draft
        ref_ok, ref_blockers = check_draft(steps)
    except Exception:
        ref_ok, ref_blockers = True, []
    if not ref_ok:
        raise ValueError(
            "This workflow uses runtime values the backtester can't "
            "resolve from historical bars: "
            + "; ".join(ref_blockers[:3])
            + (". " if ref_blockers else "")
            + "Backtest needs literal numbers or whitelisted refs "
            "(`buying_power`, `holdings.<SYM>.quantity`, fetch.* "
            "step outputs)."
        )
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
    # [C5] track per-branch fire counts so we can warn (instead of
    # silently returning an all-zero card) when an ENTRY trigger never
    # fired across the whole window.
    branch_fire_counts: dict[int, int] = {}
    for i, b in enumerate(elig.branches):
        trigger_sym = b.trigger_symbol() or b.primary_symbol() or primary_symbol
        bars_for_trigger = symbol_bars.get(trigger_sym, primary_bars)
        if b.trigger_type == "trigger.schedule":
            fires = _expand_schedule(b.trigger_config, union_index)
        elif b.trigger_type == "trigger.indicator":
            fires = _expand_indicator(b.trigger_config, bars_for_trigger)
        elif b.trigger_type == "trigger.price":
            fires = _expand_price(b.trigger_config, bars_for_trigger)
        elif b.trigger_type == "trigger.compound":
            fires = _expand_compound(b.trigger_config, bars_for_trigger, symbol_bars)
        elif b.trigger_type == "trigger.exit_compound":
            # Fire every bar; the per-fire gate in _execute_branch
            # consults SimState to decide whether the exit actually runs.
            fires = _expand_exit_compound(union_index)
        else:
            fires = []
        branch_fire_counts[i] = len(fires)
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
    # Next-open fills stamp a trade one bar AFTER its signal, so sort by
    # execution date before the single-pass walker consumes them (stable sort
    # preserves same-day entry-before-exit order). Same-bar fills are already
    # ordered; this is correctness insurance for the shifted ones.
    trades.sort(key=lambda tr: tr["t"])
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
    # CAGR on a CALENDAR-year basis (standardized 2026-05-29; was n_days/252,
    # which over-states CAGR on short windows). Falls back to bar-count years
    # if the curve has no usable dates.
    from backend.services.backtest_metrics import (
        calendar_cagr_pct, daily_returns_from_equity, sharpe_sortino,
    )
    if len(equity_curve) >= 2:
        cagr_pct = round(calendar_cagr_pct(
            _STARTING_CAPITAL, final_equity,
            equity_curve[0]["t"], equity_curve[-1]["t"],
        ), 2)
    else:
        cagr_pct = 0.0
    _sharpe, _sortino = sharpe_sortino(
        daily_returns_from_equity([p["v"] for p in equity_curve])
    )
    # Bailey/Lopez de Prado rigor battery on the backtest equity curve — the
    # SAME lens the live forward-test scorecards apply to paper NAV. PSR =
    # confidence the Sharpe is genuinely > 0; MinTRL = sample needed to prove
    # it; DSR deflates for multiple-trials selection bias (num_trials=1 until
    # trial-tracking lands, so DSR == PSR(0)).
    from backend.services.backtest.validation import (
        monte_carlo_robustness,
        sub_period_robustness,
    )
    from backend.services.forward_stats import forward_stats_block
    _eq_vals = [p["v"] for p in equity_curve]
    forward_stats = forward_stats_block(_eq_vals)
    # Circular-block-bootstrap drawdown / terminal-wealth distribution — how
    # lucky was this single path? (5%-worst drawdown, P(end in loss)).
    monte_carlo = monte_carlo_robustness(daily_returns_from_equity(_eq_vals))
    # Time-concentration: is the edge spread across sub-periods or one window?
    sub_periods = sub_period_robustness(_eq_vals)
    # Deflate DSR for how many DISTINCT strategy variants this session has
    # backtested (multiple-trials selection-bias guard). No group → num_trials
    # stays 1 → DSR == PSR(0).
    if trial_group:
        from backend.services.backtest.validation.trials import (
            record_and_deflate, strategy_fingerprint,
        )
        forward_stats = record_and_deflate(
            forward_stats, trial_group,
            strategy_fingerprint(
                steps, primary_symbol, period, start_date, end_date,
            ),
        )
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
        _bench_gross = (
            float(bench_bars["Close"].iloc[-1])
            / float(bench_bars["Close"].iloc[0])
        )
        # Net of one round-trip so it's apples-to-apples with the cost-bearing
        # strategy (a frictionless benchmark would unfairly beat it).
        _rt = (1 - _FRICTION) ** 2
        bench_pct = round((_bench_gross * _rt - 1) * 100, 2)
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
        "sharpe": _sharpe,
        "sortino": _sortino,
        "n_trades": n_trades,
        "n_wins": n_wins,
        "hit_rate_pct": hit_rate_pct,
        "benchmark_return_pct": bench_pct,
        "starting_capital": _STARTING_CAPITAL,
        "ending_value": round(final_equity, 2),
        "forward_stats": forward_stats,
        "monte_carlo": monte_carlo,
        "sub_periods": sub_periods,
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

    # [C5] When NOTHING traded, don't ship a silent all-zero card —
    # explain why. The most common cause is an ENTRY trigger whose
    # threshold can't physically occur over the window (e.g. a 1-day
    # -10% move on a large-cap). %-change thresholds are signed
    # fractions: -0.1 means -10%, so 0.1% is -0.001.
    if n_trades == 0:
        zero_entry = any(
            branch_fire_counts.get(i, 0) == 0
            and b.trigger_type != "trigger.exit_compound"
            for i, b in enumerate(elig.branches)
        )
        if zero_entry:
            elig.warnings.append(
                "the entry condition never triggered across this period — "
                "the threshold may be unreachable (e.g. a single-day move "
                "that large never happens for this stock). Try a wider "
                "lookback window (a multi-day dip) or a smaller threshold"
            )

    from backend.services.backtest_metrics import methodology_note
    _method = methodology_note(period_label=period)
    _sharpe_txt = f" Sharpe {_sharpe:.2f}." if _sharpe is not None else ""
    _psr = forward_stats.get("psr")
    _psr_txt = (
        f" PSR {_psr:.0%} (confidence the Sharpe is genuinely > 0)."
        if isinstance(_psr, (int, float)) else ""
    )
    _mc_txt = (
        f" Monte-Carlo: 5%-worst drawdown {monte_carlo['dd_p95_severity_pct']:.0f}%,"
        f" P(end in loss) {monte_carlo['prob_loss']:.0%}."
        if monte_carlo else ""
    )
    _nt = forward_stats.get("num_trials") or 1
    _dsr = forward_stats.get("deflated_sharpe")
    _dsr_txt = (
        f" After {_nt} variants this session, deflated-Sharpe DSR {_dsr:.0%}."
        if _nt > 1 and isinstance(_dsr, (int, float)) else ""
    )
    _conc = (sub_periods or {}).get("concentration")
    _sp_txt = (
        f" ⚠ Fragile: {_conc:.0%} of the return came from a single sub-period."
        if isinstance(_conc, (int, float)) and _conc > 0.6 else ""
    )
    summary = (
        f"Backtested {name!r} on {primary_symbol} over {period}. "
        f"Strategy returned {total_return_pct:+.1f}% across {n_trades} trade(s); "
        f"buy-and-hold returned {bench_pct:+.1f}%.{_sharpe_txt}{_psr_txt} "
        f"Results are {_method['costs']}, on {_method['basis']}.{_mc_txt}{_dsr_txt}{_sp_txt}"
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
        methodology=_method,
    )
