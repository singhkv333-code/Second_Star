"""Forward-test scorecard writer + read service (P6).

Closes the forward-test loop:

  1. ``snapshot_idea_nav`` — upsert one ``PaperIdeaNavSnapshot`` per
     ``(idea_id, as_of_date)`` from ``compute_idea_nav``. Mirrors the
     account-grain ``snapshot_account_nav`` upsert idiom one-to-one.
  2. ``refresh_idea_scorecard`` — snapshot, recompute the headline
     metrics block from the idea's NAV series + the (soft) backtest
     baseline, REASSIGN ``idea.scorecard_cache`` as a whole dict (the
     column is plain JSON / JSONB — never mutate in place; SQLAlchemy
     would not see the mutation without ``MutableDict``), then advance
     the paper -> candidate gate.
  3. ``refresh_all_idea_scorecards`` — the batch entry point hooked by
     the scheduler immediately after ``snapshot_all_navs`` (under the
     same NIFTY close); per-idea SAVEPOINT so one bad row can't poison
     the whole pass; returns the count.
  4. ``ideas_list`` / ``idea_detail`` — the LEAF read service for the
     paper Ideas tab. Same shape contract as ``portfolio.py``: leaf
     ``(db, user_id, ...) -> dict | list[dict]``, copy ``_iso``, every
     money field float at the JSON edge via ``money_to_float`` (the
     Float ``nifty_close`` uses the null-guarded ``float()`` cast).

Verdict logic (the differentiator — order matters):

  1. ``insufficient_data`` whenever ``n_obs < MIN_OBS`` (=20), PSR is
     None, or MinTRL says we don't have enough data yet — collapses a
     ~3-week idea cleanly without leaking a noisy verdict.
  2. With a stored backtest baseline:
       * ``execution_problem`` if live bleeds where the backtest profited
         (slippage / cost signature).
       * ``decayed`` if a strong backtest Sharpe has at least halved or
         PSR < 0.90 (the alpha decayed live).
  3. Without a baseline: PSR-only — ``decayed`` if PSR < 0.90, else
     ``on_track`` if PSR >= 0.95, else ``decayed``.
  4. ``on_track`` once matured with no degradation/exec flag.

Promotion gate: ``promotion_ready`` ⇔ PSR ≥ 0.95 AND MinTRL satisfied
AND DSR (when defined) ≥ 0.95. A ``paper`` idea passing the gate auto-
advances to ``candidate``. ``candidate -> promoted`` and any path to
``retired`` are deliberately surfaced as flags only (per plan §9.5).

Session discipline: every writer ``db.flush()`` only. The scheduler /
router owns commit. Money columns receive Decimals straight from
``compute_idea_nav``; the JSON read edge casts via ``money_to_float``
(the Float ``nifty_close`` uses a null-guarded ``float()`` cast — NOT
``money_to_float`` per the contract pin).
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from backend.models import ForwardIdea, PaperIdeaNavSnapshot
from backend.paper.idea_valuation import compute_idea_nav
from backend.paper.money import money_to_float
from backend.services.backtest_metrics import (
    daily_returns_from_equity,
    sharpe_sortino,
)
from backend.services.forward_stats import (
    deflated_sharpe_ratio,
    kurtosis,
    max_drawdown_pct as fs_max_drawdown_pct,
    min_track_record_length,
    observed_sharpe,
    psr,
    skewness,
)
from backend.utils.time_utils import now_ist
from backend.workflows.dsl.backtest.persistence import get_run_for_user

logger = logging.getLogger(__name__)

PriceFn = Optional[Callable[[str], Any]]

# Minimum forward observations before we attempt to render a meaningful
# verdict. n_obs = len(NAV series) - 1, so 20 observations roughly
# corresponds to ~one month of trading days — anything shorter is dwarfed
# by sample-size noise (a ~3-week idea collapses to ``insufficient_data``
# per the contract).
MIN_OBS = 20

# PSR thresholds. 0.95 = the conventional 95% probability that the true
# Sharpe exceeds the threshold (zero by default); 0.90 is the soft floor
# below which we flag the alpha as decayed.
PSR_PROMOTE = 0.95
PSR_DECAY = 0.90
DSR_PROMOTE = 0.95

# Backtest "strong enough Sharpe to compare against" — below this, a
# backtest-vs-forward Sharpe ratio test is just noise.
BT_SHARPE_FLOOR = 0.5


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _iso(value: Any) -> Optional[str]:
    """ISO-8601 string for a date/datetime, or None.

    Mirrors ``portfolio._iso`` so the JSON contract stays uniform across
    read services.
    """
    if value is None:
        return None
    return str(value.isoformat())


def _float_or_none(x: Any) -> Optional[float]:
    """Null-guarded float cast for Float columns / plain numerics.

    ``nifty_close`` is a Float (not Numeric) — per the contract pin we
    cast via plain ``float()`` with a null guard, NOT through
    ``money_to_float`` (which would quantize through Decimal).
    """
    if x is None:
        return None
    return float(x)


def _backtest_result(
    db: Session, idea: ForwardIdea,
) -> Optional[dict[str, Any]]:
    """Resolve the (soft) backtest baseline, or ``None`` when unavailable.

    Guards every failure mode — missing run id, cross-user / missing
    row, status != succeeded, ``result`` JSON missing. Returns the raw
    JSON ``result`` dict (BacktestResult-shaped) on success.
    """
    run_id = idea.backtest_run_id
    if run_id is None:
        return None
    row = get_run_for_user(db, run_id=str(run_id), user_id=int(idea.user_id))
    if row is None or row.status != "succeeded" or row.result is None:
        return None
    result = row.result
    if not isinstance(result, dict):
        return None
    return result


# ---------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------


def snapshot_idea_nav(
    db: Session,
    idea: ForwardIdea,
    as_of_date: dt.date,
    price_fn: PriceFn = None,
    nifty_close: Optional[float] = None,
) -> PaperIdeaNavSnapshot:
    """Compute and UPSERT the idea's NAV row for ``as_of_date``.

    Mirror of ``snapshot_account_nav``: query the unique
    ``(idea_id, as_of_date)`` row, create on miss, assign every column
    from ``compute_idea_nav``, ``flush()`` (caller commits).

    Money fields (committed_capital / positions_mv / idea_nav /
    realized_pnl / unrealized_pnl) are Decimals written straight into
    the Numeric columns; ``nifty_close`` is cast via the Float-column
    null-guarded ``float()``.
    """
    computed = compute_idea_nav(db, idea, price_fn)

    row = (
        db.query(PaperIdeaNavSnapshot)
        .filter(
            PaperIdeaNavSnapshot.idea_id == idea.id,
            PaperIdeaNavSnapshot.as_of_date == as_of_date,
        )
        .first()
    )
    if row is None:
        row = PaperIdeaNavSnapshot(
            idea_id=idea.id,
            account_id=idea.account_id,
            as_of_date=as_of_date,
        )
        db.add(row)

    # account_id stays in sync even if (impossibly) the idea was re-
    # parented — the column is NOT NULL so we always re-assert.
    row.account_id = idea.account_id
    row.committed_capital = computed["committed_capital"]
    row.positions_mv = computed["positions_mv"]
    row.idea_nav = computed["idea_nav"]
    row.realized_pnl = computed["realized_pnl"]
    row.unrealized_pnl = computed["unrealized_pnl"]
    row.nifty_close = (
        float(nifty_close) if nifty_close is not None else None
    )

    db.flush()
    return row


def latest_idea_nav(
    db: Session, idea_id: str,
) -> Optional[PaperIdeaNavSnapshot]:
    """Most recent snapshot for the idea by ``as_of_date`` (or None)."""
    return (
        db.query(PaperIdeaNavSnapshot)
        .filter(PaperIdeaNavSnapshot.idea_id == idea_id)
        .order_by(PaperIdeaNavSnapshot.as_of_date.desc())
        .first()
    )


def idea_nav_series(
    db: Session,
    idea_id: str,
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
) -> list[PaperIdeaNavSnapshot]:
    """Snapshots for the idea ordered by ``as_of_date`` ascending.

    Inclusive ``start`` / ``end`` date bounds — backs the FE forward
    curve (mirror of ``snapshots.nav_series``).
    """
    q = db.query(PaperIdeaNavSnapshot).filter(
        PaperIdeaNavSnapshot.idea_id == idea_id,
    )
    if start is not None:
        q = q.filter(PaperIdeaNavSnapshot.as_of_date >= start)
    if end is not None:
        q = q.filter(PaperIdeaNavSnapshot.as_of_date <= end)
    return q.order_by(PaperIdeaNavSnapshot.as_of_date.asc()).all()


# ---------------------------------------------------------------------------
# scorecard metric computation
# ---------------------------------------------------------------------------


def _compute_metrics(
    db: Session,
    idea: ForwardIdea,
) -> dict[str, Any]:
    """Compute the cache dict from the persisted idea NAV series + the
    (optional) backtest baseline. Pure: reads only what's persisted.

    The series ``S`` is the idea's NAV oldest-first; returns ``rets``
    feed the per-period statistical battery (observed_sharpe / skew /
    kurt -> PSR / MinTRL / DSR). The displayed Sharpe stays on the
    SINGLE-SOURCE annualized formula in ``backtest_metrics`` — feeding
    that rounded annualized value into PSR would be a bug (contract
    DECISIONS §5).
    """
    snaps = idea_nav_series(db, str(idea.id))

    nav_floats: list[float] = [float(s.idea_nav) for s in snaps]
    nifty_floats: list[Optional[float]] = [
        (_float_or_none(s.nifty_close)) for s in snaps
    ]

    n_pts = len(nav_floats)

    # n_obs is the ACTUAL number of return observations the stats battery
    # consumes — daily_returns_from_equity drops any step across a non-
    # positive NAV (idea_nav can hit 0 when an idea flattens out between
    # trades), so len(rets) can be < n_pts-1. Use len(rets) everywhere the
    # statistics rest on it (PSR/DSR/MinTRL/MIN_OBS gate) so the sample size
    # and the moment estimators stay on the same sample. Absent zero-NAV
    # gaps this equals n_pts-1 (the contract definition).
    rets = daily_returns_from_equity(nav_floats)
    n_obs = len(rets)

    today = now_ist().date()
    maturity_days: Optional[int] = None
    if idea.inception_date is not None:
        maturity_days = (today - idea.inception_date).days

    # cum_return_pct — guard <2 points and zero starting NAV.
    cum_return_pct: Optional[float] = None
    if n_pts >= 2 and nav_floats[0] > 0:
        cum_return_pct = (nav_floats[-1] / nav_floats[0] - 1.0) * 100.0

    # Display Sharpe (annualized) — the SINGLE source.
    sharpe_display, _sortino_display = sharpe_sortino(rets)

    # NIFTY alpha = idea cum return - benchmark cum return over the
    # SAME window. Skip if any NIFTY close is None or zero start.
    alpha: Optional[float] = None
    if (
        cum_return_pct is not None
        and n_pts >= 2
        and all(v is not None for v in nifty_floats)
    ):
        try:
            nstart = float(nifty_floats[0])  # type: ignore[arg-type]
            nend = float(nifty_floats[-1])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            nstart = nend = 0.0
        if nstart > 0:
            nifty_cum_return_pct = (nend / nstart - 1.0) * 100.0
            alpha = cum_return_pct - nifty_cum_return_pct

    # Raw stats for the Bailey/Lopez de Prado battery.
    sr_hat = observed_sharpe(rets)
    sk = skewness(rets)
    kt = kurtosis(rets)  # raw, not excess — what PSR expects
    psr_val = psr(sr_hat, n_obs, sk, kt, sr_threshold=0.0)
    mintrl_val = min_track_record_length(sr_hat, sk, kt)
    dsr_val = deflated_sharpe_ratio(
        sr_hat, n_obs, sk, kt, int(idea.cohort_trial_count or 1),
    )
    mdd_pct = fs_max_drawdown_pct(nav_floats)

    # Verdict + promotion gate.
    bt = _backtest_result(db, idea)
    has_backtest = bt is not None

    verdict, promotion_ready = _verdict_and_gate(
        n_obs=n_obs,
        psr_val=psr_val,
        mintrl_val=mintrl_val,
        dsr_val=dsr_val,
        cum_return_pct=cum_return_pct,
        sharpe=sharpe_display,
        bt=bt,
    )

    return {
        "cum_return_pct": cum_return_pct,
        "sharpe": sharpe_display,
        "alpha": alpha,
        "psr": psr_val,
        "max_drawdown_pct": mdd_pct,
        "mintrl": mintrl_val,
        "dsr": dsr_val,
        "verdict": verdict,
        "promotion_ready": promotion_ready,
        "n_obs": n_obs,
        "maturity_days": maturity_days,
        "has_backtest": has_backtest,
    }


def _verdict_and_gate(
    *,
    n_obs: int,
    psr_val: Optional[float],
    mintrl_val: Optional[float],
    dsr_val: Optional[float],
    cum_return_pct: Optional[float],
    sharpe: Optional[float],
    bt: Optional[dict[str, Any]],
) -> tuple[str, bool]:
    """The verdict ladder — order matters.

    (1) Hard short-circuit on insufficient evidence (n_obs<MIN_OBS,
        missing PSR, or MinTRL says we don't have enough data yet).
    (2) Matured + baseline present: execution-problem first (slippage
        signature), then decay (Sharpe halved OR PSR<0.90).
    (3) Matured + no baseline: PSR-only ladder.
    (4) Default: on_track.

    Promotion gate (PSR>=0.95, MinTRL satisfied, DSR (if defined) >=0.95)
    is ANDed with an on_track verdict: an idea the scorecard flags as
    decayed / execution_problem is NEVER promotion-ready, even if the raw
    statistical thresholds pass (a contradictory "promote this decayed
    idea" signal otherwise).
    """
    # (1) Insufficient data: any of the three short-circuits.
    if (
        n_obs < MIN_OBS
        or psr_val is None
        or (mintrl_val is not None and n_obs < mintrl_val)
    ):
        return "insufficient_data", False

    # (2) Matured. Promotion gate uses the same building blocks.
    promotion_ready = (
        psr_val >= PSR_PROMOTE
        and mintrl_val is not None
        and n_obs >= mintrl_val
        and (dsr_val is None or dsr_val >= DSR_PROMOTE)
    )

    bt_sharpe: Optional[float] = None
    bt_return: Optional[float] = None
    if bt is not None:
        metrics = bt.get("metrics") if isinstance(bt, dict) else None
        if isinstance(metrics, dict):
            raw_sh = metrics.get("sharpe_ratio")
            raw_ret = metrics.get("total_return_pct")
            try:
                bt_sharpe = float(raw_sh) if raw_sh is not None else None
            except (TypeError, ValueError):
                bt_sharpe = None
            try:
                bt_return = float(raw_ret) if raw_ret is not None else None
            except (TypeError, ValueError):
                bt_return = None

    if bt is not None:
        # execution_problem: live bled while the backtest profited —
        # the slippage / cost / regime signature.
        if (
            cum_return_pct is not None
            and cum_return_pct < 0
            and bt_return is not None
            and bt_return > 0
        ):
            # A degradation verdict is never promotion-ready.
            return "execution_problem", False

        # decayed: high backtest Sharpe + ≤ half live Sharpe, OR PSR
        # under the soft floor. Either fails the "still the same
        # alpha?" question.
        decay_sharpe = (
            bt_sharpe is not None
            and bt_sharpe > BT_SHARPE_FLOOR
            and sharpe is not None
            and sharpe <= 0.5 * bt_sharpe
        )
        decay_psr = psr_val < PSR_DECAY
        if decay_sharpe or decay_psr:
            return "decayed", False
    else:
        # (3) PSR-only ladder when no baseline.
        if psr_val < PSR_DECAY:
            return "decayed", False
        if psr_val >= PSR_PROMOTE:
            return "on_track", promotion_ready
        return "decayed", False

    # (4) Matured, baseline present, nothing flagged.
    return "on_track", promotion_ready


# ---------------------------------------------------------------------------
# scorecard refresh entry points
# ---------------------------------------------------------------------------


def refresh_idea_scorecard(
    db: Session,
    idea: ForwardIdea,
    price_fn: PriceFn = None,
    nifty_close: Optional[float] = None,
) -> None:
    """End-of-day refresh: snapshot the NAV, recompute the cache dict,
    advance the paper -> candidate gate. Flush-only (caller commits).

    Three side effects in order:
      1. ``snapshot_idea_nav`` — append/upsert today's NAV point.
      2. ``idea.scorecard_cache = {...}`` — whole-dict reassign so the
         JSON column actually persists (no MutableDict on the column).
      3. paper -> candidate auto-advance when the promotion gate
         passes. Never auto-promote candidate->promoted or auto-retire.
    """
    today = now_ist().date()
    snapshot_idea_nav(db, idea, today, price_fn, nifty_close)

    metrics = _compute_metrics(db, idea)
    # REASSIGN the whole dict (the column is plain JSON / JSONB).
    idea.scorecard_cache = metrics

    if (
        idea.status == "paper"
        and bool(metrics.get("promotion_ready"))
    ):
        idea.status = "candidate"
        idea.status_changed_at = now_ist()

    db.flush()


def refresh_all_idea_scorecards(
    db: Session,
    as_of_date: Optional[dt.date] = None,
    price_fn: PriceFn = None,
    nifty_close: Optional[float] = None,
) -> int:
    """Batch refresh hook for the EOD scheduler.

    Mirrors ``snapshot_all_navs``: loop active ideas (status in
    paper/candidate/promoted; retired excluded), each under a SAVEPOINT
    so one bad row can't poison the whole pass. Returns the number of
    ideas refreshed.

    ``as_of_date`` is currently surfaced for parity with the account
    snapshot hook but ``snapshot_idea_nav`` defaults to ``now_ist`` via
    the per-idea refresh; passing a date in is reserved for backfill.
    """
    ideas = (
        db.query(ForwardIdea)
        .filter(ForwardIdea.status.in_(("paper", "candidate", "promoted")))
        .all()
    )
    n = 0
    for idea in ideas:
        try:
            with db.begin_nested():
                if as_of_date is None:
                    refresh_idea_scorecard(db, idea, price_fn, nifty_close)
                else:
                    # Backfill path: write the snapshot at the requested
                    # date, then recompute the cache from the full
                    # series (refresh_idea_scorecard uses today() for
                    # the snapshot only).
                    snapshot_idea_nav(
                        db, idea, as_of_date, price_fn, nifty_close,
                    )
                    metrics = _compute_metrics(db, idea)
                    idea.scorecard_cache = metrics
                    if (
                        idea.status == "paper"
                        and bool(metrics.get("promotion_ready"))
                    ):
                        idea.status = "candidate"
                        idea.status_changed_at = now_ist()
                    db.flush()
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                "idea scorecard refresh failed for idea %s",
                idea.id, exc_info=True,
            )
            continue
        n += 1
    return n


# ---------------------------------------------------------------------------
# read service (LEAF — `(db, user_id, ...) -> dict | list[dict]`)
# ---------------------------------------------------------------------------


def _idea_list_row(idea: ForwardIdea) -> dict[str, Any]:
    """Project one ForwardIdea + its scorecard_cache to the list shape.

    Headline metrics read from the cache (the whole point — the cache
    is the list-view copy). When the cache is missing (a brand-new
    idea before the first refresh), all metric fields collapse to
    ``None`` so the FE renders DASHes uniformly.
    """
    cache: dict[str, Any] = dict(idea.scorecard_cache or {})

    def _g(k: str) -> Any:
        return cache.get(k)

    return {
        "id": idea.id,
        "label": idea.label,
        "origin_kind": idea.origin_kind,
        "status": idea.status,
        "inception_date": _iso(idea.inception_date),
        "maturity_days": _g("maturity_days"),
        "n_obs": _g("n_obs"),
        "cum_return_pct": _g("cum_return_pct"),
        "sharpe": _g("sharpe"),
        "alpha": _g("alpha"),
        "psr": _g("psr"),
        "max_drawdown_pct": _g("max_drawdown_pct"),
        "verdict": _g("verdict"),
        "has_backtest": idea.backtest_run_id is not None,
    }


def ideas_list(db: Session, user_id: int) -> list[dict[str, Any]]:
    """List all ideas owned by ``user_id``, newest first. JSON-ready.

    No account check — a user is exactly one account in v1 (the
    ``ForwardIdea.user_id`` FK is the authoritative ownership key).
    Empty list is the valid empty-book shape (mirrors
    ``portfolio.holdings``).
    """
    rows = (
        db.query(ForwardIdea)
        .filter(ForwardIdea.user_id == int(user_id))
        .order_by(ForwardIdea.created_at.desc())
        .all()
    )
    return [_idea_list_row(r) for r in rows]


def _bt_baseline_detail(
    db: Session, idea: ForwardIdea,
) -> Optional[dict[str, Any]]:
    """The detail-view backtest block: just the headline metric fields
    + equity curve (no trades, no diagnostics — the detail page is the
    forward decay chart; trades belong on the backtest page).
    """
    bt = _backtest_result(db, idea)
    if bt is None:
        return None

    metrics = bt.get("metrics") if isinstance(bt, dict) else None
    request = bt.get("request") if isinstance(bt, dict) else None
    equity = bt.get("equity_curve") if isinstance(bt, dict) else None
    if not isinstance(metrics, dict):
        metrics = {}
    if not isinstance(request, dict):
        request = {}

    def _g_float(d: dict[str, Any], k: str) -> Optional[float]:
        v = d.get(k)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _g_int(d: dict[str, Any], k: str) -> Optional[int]:
        v = d.get(k)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def _g_str(d: dict[str, Any], k: str) -> Optional[str]:
        v = d.get(k)
        return str(v) if v is not None else None

    eq_rows: list[dict[str, Any]] = []
    if isinstance(equity, list):
        for pt in equity:
            if not isinstance(pt, dict):
                continue
            d_val = pt.get("date")
            e_val = pt.get("equity")
            try:
                eq_rows.append({
                    "date": str(d_val) if d_val is not None else None,
                    "equity": (
                        float(e_val) if e_val is not None else 0.0
                    ),
                })
            except (TypeError, ValueError):
                continue

    # Backtest max DD is stored as a POSITIVE magnitude; normalize to the
    # NEGATIVE-percent convention the forward stat + the FE use.
    _bt_mdd = _g_float(metrics, "max_drawdown_pct")
    if _bt_mdd is not None:
        _bt_mdd = -abs(_bt_mdd)

    return {
        "sharpe_ratio": _g_float(metrics, "sharpe_ratio"),
        "total_return_pct": _g_float(metrics, "total_return_pct"),
        "cagr_pct": _g_float(metrics, "cagr_pct"),
        "max_drawdown_pct": _bt_mdd,
        "benchmark_return_pct": _g_float(metrics, "benchmark_return_pct"),
        "total_trades": _g_int(metrics, "total_trades"),
        "start_date": _g_str(request, "start_date"),
        "end_date": _g_str(request, "end_date"),
        "primary_symbol": _g_str(request, "primary_symbol"),
        "equity_curve": eq_rows,
    }


def _gate_rows(
    *,
    cache: dict[str, Any],
    bt: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the side-by-side gate table — forward vs backtest, plus a
    boolean ``pass`` flag (None when no baseline).

    Rows in the order the FE renders: Sharpe, Cum return %, Max DD %,
    PSR. ``pass`` semantics per row:
      * Sharpe: forward >= backtest * 0.5 (matches the decay test)
      * Cum return %: same sign as backtest (or both positive)
      * Max DD %: forward shallower (closer to zero) than backtest
      * PSR: forward PSR >= 0.95
    """
    fwd_sharpe = cache.get("sharpe")
    fwd_cum = cache.get("cum_return_pct")
    fwd_mdd = cache.get("max_drawdown_pct")
    fwd_psr = cache.get("psr")

    bt_metrics: dict[str, Any] = {}
    if bt is not None:
        m = bt.get("metrics") if isinstance(bt, dict) else None
        if isinstance(m, dict):
            bt_metrics = m

    def _g(d: dict[str, Any], k: str) -> Optional[float]:
        v = d.get(k)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    bt_sharpe = _g(bt_metrics, "sharpe_ratio") if bt is not None else None
    bt_cum = _g(bt_metrics, "total_return_pct") if bt is not None else None
    bt_mdd = _g(bt_metrics, "max_drawdown_pct") if bt is not None else None
    # The DSL backtest stores max drawdown as a POSITIVE magnitude
    # (engine: max_dd*100, max_dd=(peak-equity)/peak >= 0), while
    # forward_stats.max_drawdown_pct returns a NEGATIVE percent. Normalize
    # the backtest to the same negative sign so the row reads consistently
    # and the shallower-drawdown comparison below is sign-correct.
    if bt_mdd is not None:
        bt_mdd = -abs(bt_mdd)

    def _pass_sharpe() -> Optional[bool]:
        if bt is None or fwd_sharpe is None or bt_sharpe is None:
            return None
        if bt_sharpe <= 0:
            # Backtest had no real Sharpe to beat — any non-negative
            # forward Sharpe passes.
            return float(fwd_sharpe) >= 0
        return float(fwd_sharpe) >= 0.5 * float(bt_sharpe)

    def _pass_cum() -> Optional[bool]:
        if bt is None or fwd_cum is None or bt_cum is None:
            return None
        if bt_cum >= 0:
            return float(fwd_cum) >= 0
        # If backtest was negative, "pass" requires forward at least as
        # bad-or-better (>= backtest).
        return float(fwd_cum) >= float(bt_cum)

    def _pass_mdd() -> Optional[bool]:
        if bt is None or fwd_mdd is None or bt_mdd is None:
            return None
        # Both are negative percents (or 0). Forward "passes" when its
        # drawdown is shallower (closer to zero), i.e. fwd >= bt.
        return float(fwd_mdd) >= float(bt_mdd)

    def _pass_psr() -> Optional[bool]:
        if fwd_psr is None:
            return None
        return float(fwd_psr) >= PSR_PROMOTE

    return [
        {
            "label": "Sharpe",
            "forward": fwd_sharpe,
            "backtest": bt_sharpe,
            "pass": _pass_sharpe(),
        },
        {
            "label": "Cum return %",
            "forward": fwd_cum,
            "backtest": bt_cum,
            "pass": _pass_cum(),
        },
        {
            "label": "Max DD %",
            "forward": fwd_mdd,
            "backtest": bt_mdd,
            "pass": _pass_mdd(),
        },
        {
            "label": "PSR",
            "forward": fwd_psr,
            "backtest": None,
            "pass": _pass_psr(),
        },
    ]


def idea_detail(
    db: Session, user_id: int, idea_id: str,
) -> Optional[dict[str, Any]]:
    """Full drill-in payload for one idea. ``None`` on miss / cross-user
    (the router maps to 404 — same convention as the rest of the
    Agent System).

    Returned shape:
      * every field from ``_idea_list_row``,
      * plus ``cohort_trial_count``, ``backtest_run_id``,
        ``status_changed_at``, ``mintrl``, ``dsr``, ``promotion_ready``,
      * plus ``forward_curve`` — the idea's NAV snapshot series as
        JSON-ready dicts (money fields floats via ``money_to_float``;
        ``nifty_close`` via the Float-column null-guarded ``float()``),
      * plus ``backtest`` — null or a stripped backtest block,
      * plus ``gates`` — the side-by-side comparison rows.
    """
    idea = (
        db.query(ForwardIdea)
        .filter(
            ForwardIdea.id == str(idea_id),
            ForwardIdea.user_id == int(user_id),
        )
        .first()
    )
    if idea is None:
        return None

    cache: dict[str, Any] = dict(idea.scorecard_cache or {})

    base = _idea_list_row(idea)
    snaps = idea_nav_series(db, str(idea.id))
    forward_curve = [
        {
            "as_of_date": _iso(s.as_of_date),
            "idea_nav": money_to_float(s.idea_nav),
            "committed_capital": money_to_float(s.committed_capital),
            "positions_mv": money_to_float(s.positions_mv),
            "realized_pnl": money_to_float(s.realized_pnl),
            "unrealized_pnl": money_to_float(s.unrealized_pnl),
            "nifty_close": _float_or_none(s.nifty_close),
        }
        for s in snaps
    ]

    bt = _backtest_result(db, idea)
    bt_payload = _bt_baseline_detail(db, idea)
    gates = _gate_rows(cache=cache, bt=bt)

    extra: dict[str, Any] = {
        "cohort_trial_count": int(idea.cohort_trial_count or 1),
        "backtest_run_id": idea.backtest_run_id,
        "status_changed_at": _iso(idea.status_changed_at),
        "mintrl": cache.get("mintrl"),
        "dsr": cache.get("dsr"),
        "promotion_ready": bool(cache.get("promotion_ready", False)),
        "forward_curve": forward_curve,
        "backtest": bt_payload,
        "gates": gates,
    }
    base.update(extra)
    return base


__all__ = [
    "MIN_OBS",
    "snapshot_idea_nav",
    "latest_idea_nav",
    "idea_nav_series",
    "refresh_idea_scorecard",
    "refresh_all_idea_scorecards",
    "ideas_list",
    "idea_detail",
]
