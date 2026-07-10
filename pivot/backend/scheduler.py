"""
backend/scheduler.py

APScheduler setup for Pivot.
All scheduled times are in IST (Asia/Kolkata).
All log messages and confirmations include "IST" in time strings.

Jobs registered:
  1. execute_due_sips        — 09:15 IST every trading weekday
  2. check_strategy_triggers — every 60s, 09:15-15:30 IST weekdays
  3. refresh_broker_tokens   — 07:30 IST every weekday
  4. daily_market_summary    — 15:45 IST every weekday
"""

import logging
import json

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.triggers.cron import CronTrigger

from backend.utils.time_utils import (
    IST, now_ist, format_ist, format_ist_short,
    is_trading_day, next_monthly_execution,
    next_weekly_execution, next_daily_execution,
)

logger = logging.getLogger(__name__)


def create_scheduler(database_url: str = None) -> AsyncIOScheduler:
    """
    Builds an AsyncIOScheduler. Jobs persist to the SQL job store if a
    database_url is provided; otherwise jobs live in memory only.
    """
    jobstores = {}
    if database_url:
        try:
            jobstores["default"] = SQLAlchemyJobStore(url=database_url)
        except Exception as e:
            logger.warning(
                f"Could not set up SQLAlchemy job store: {e}. "
                f"Using memory store."
            )

    executors = {"default": AsyncIOExecutor()}

    job_defaults = {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 3600,
    }

    return AsyncIOScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
        timezone=IST,
    )


scheduler: AsyncIOScheduler = None


def init_scheduler(database_url: str = None) -> AsyncIOScheduler:
    """Called from main.py startup event."""
    global scheduler
    scheduler = create_scheduler(database_url)
    _register_jobs()
    scheduler.start()
    logger.info(
        f"[{format_ist_short(now_ist())}] "
        f"Scheduler started. Timezone: IST (Asia/Kolkata)"
    )
    return scheduler


def _register_jobs():
    """Register all recurring jobs. All times in IST."""

    scheduler.add_job(
        execute_due_sips,
        trigger=CronTrigger(
            hour=9, minute=15, second=0,
            day_of_week="mon-fri",
            timezone=IST,
        ),
        id="execute_due_sips",
        name="Execute Due SIPs at 09:15 IST",
        replace_existing=True,
    )

    scheduler.add_job(
        check_strategy_triggers,
        trigger=CronTrigger(
            second=0,
            hour="9-15",
            minute="15-59",
            day_of_week="mon-fri",
            timezone=IST,
        ),
        id="check_strategy_triggers",
        name="Check Strategy Triggers (Market Hours IST)",
        replace_existing=True,
    )

    scheduler.add_job(
        refresh_broker_tokens,
        trigger=CronTrigger(
            hour=7, minute=30, second=0,
            day_of_week="mon-fri",
            timezone=IST,
        ),
        id="refresh_kite_tokens",
        name="Refresh Broker Tokens at 07:30 IST",
        replace_existing=True,
    )

    scheduler.add_job(
        send_daily_summary,
        trigger=CronTrigger(
            hour=15, minute=45, second=0,
            day_of_week="mon-fri",
            timezone=IST,
        ),
        id="daily_market_summary",
        name="Daily Summary at 15:45 IST",
        replace_existing=True,
    )

    # F&O P0: instrument-master refresh + dynamic universe selection.
    # 08:35 IST — after Kite regenerates the daily instruments dump
    # (~08:30) and before market open, so lot-size revisions and new
    # weekly expiries land before any chain is quoted.
    # NOTE: MUST be a module-level function (refresh_fno_instruments
    # below) — the SQLAlchemy jobstore serializes callables by textual
    # reference, and a closure here silently killed scheduler.start()
    # for EVERY job ("This Job cannot be serialized").
    scheduler.add_job(
        refresh_fno_instruments,
        trigger=CronTrigger(
            hour=8, minute=35, second=0,
            day_of_week="mon-fri",
            timezone=IST,
        ),
        id="fno_instrument_master_refresh",
        name="F&O: instrument master + universe refresh at 08:35 IST",
        replace_existing=True,
    )

    # Paper-trading jobs (only when the feature is on): fill resting orders
    # on a market-hours interval, and snapshot each paper account's NAV at
    # EOD (the equity curve). NAV mark-to-market is otherwise lazy-on-read.
    from backend.config import settings as _settings
    if getattr(_settings, "paper_trading_enabled", True):
        scheduler.add_job(
            tick_paper_resting_orders,
            trigger=CronTrigger(
                minute="*/5", hour="9-15",
                day_of_week="mon-fri", timezone=IST,
            ),
            id="paper_tick_resting",
            name="Paper: fill resting orders (every 5m, market hours IST)",
            replace_existing=True,
        )
        scheduler.add_job(
            mark_paper_positions_intraday,
            trigger=CronTrigger(
                # Offset +2min off the resting tick's */5 boundary so the two
                # jobs never coincide (matches the 15:37 NAV-snapshot pattern
                # below, which is likewise offset off the tick boundary).
                minute="2-59/5", hour="9-15",
                day_of_week="mon-fri", timezone=IST,
            ),
            id="paper_mark_positions",
            name="Paper: mark open positions intraday (every 5m, market hours IST)",
            replace_existing=True,
        )
        scheduler.add_job(
            snapshot_paper_navs,
            trigger=CronTrigger(
                # 15:37 — deliberately OFF the */5 resting-tick boundary so
                # the two jobs never coincide; after the 15:30 close + the
                # last tick, so the snapshot sees the final marks.
                hour=15, minute=37, second=0,
                day_of_week="mon-fri", timezone=IST,
            ),
            id="paper_nav_snapshot",
            name="Paper: daily NAV snapshot at 15:37 IST",
            replace_existing=True,
        )

        # F&O P2: portfolio-Greeks snapshot — 15:39, after the NAV
        # snapshot so both EOD rows reflect the same closing marks.
        # Module-level callable (same serialization constraint as above).
        scheduler.add_job(
            snapshot_paper_greeks_eod,
            trigger=CronTrigger(
                hour=15, minute=39, second=0,
                day_of_week="mon-fri", timezone=IST,
            ),
            id="paper_greeks_snapshot",
            name="Paper: daily portfolio-Greeks snapshot at 15:39 IST",
            replace_existing=True,
        )

    # Screener price baselines: recompute each universe symbol's 1-year-ago
    # close + 52-week range from Kite historical once a day, so the grid's
    # 1-year-return column needs NO yfinance bulk download. 08:00 IST every
    # weekday — after the 07:30 Kite token refresh, before market open.
    # Module-level callable (SQLAlchemy jobstore serializes by textual ref).
    scheduler.add_job(
        precompute_screener_baseline,
        trigger=CronTrigger(
            hour=8, minute=0, second=0,
            day_of_week="mon-fri",
            timezone=IST,
        ),
        id="screener_baseline_precompute",
        name="Screener: Kite 1Y/52w baseline precompute at 08:00 IST",
        replace_existing=True,
    )

    logger.info(
        f"[{format_ist_short(now_ist())}] Registered "
        f"{len(scheduler.get_jobs())} scheduler jobs. All times in IST."
    )


async def precompute_screener_baseline():
    """Nightly warm of the screener's Kite price baselines (1-year-ago close +
    52-week range) for the whole market universe, so the grid's 1-year-return
    no longer needs a yfinance bulk download. Rate-limit friendly: one symbol
    at a time with a small delay, offloaded to threads so the event loop stays
    free. Lazily-warmed gaps during the day are handled by the screener itself.
    """
    import asyncio

    from backend.routers import screener

    try:
        universe = await asyncio.to_thread(screener._full_universe)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[screener] baseline precompute: universe load failed: %s", exc)
        return

    syms = [u.get("symbol") for u in universe if u.get("symbol")]
    done = 0
    for sym in syms:
        try:
            if await asyncio.to_thread(screener._compute_baseline, sym):
                done += 1
        except Exception:  # noqa: BLE001 — per-symbol, keep sweeping
            pass
        await asyncio.sleep(0.3)  # stay under Kite's historical rate limit
    logger.info(
        "[screener] baseline precompute warmed %d/%d symbols", done, len(syms)
    )


# ── F&O jobs (module-level — the SQLAlchemy jobstore serializes
# callables by textual reference; closures break scheduler.start()) ──


async def refresh_fno_instruments():
    """F&O P0: daily instrument-master refresh + universe selection."""
    from backend.market.instrument_master import refresh_instrument_master_job

    await refresh_instrument_master_job()


async def snapshot_paper_greeks_eod():
    """F&O P2: EOD portfolio-Greeks snapshot per paper account."""
    import asyncio

    def _run() -> None:
        from backend.database import SessionLocal
        from backend.services.portfolio_greeks import snapshot_portfolio_greeks

        db = SessionLocal()
        try:
            snapshot_portfolio_greeks(db)
        finally:
            db.close()

    try:
        await asyncio.to_thread(_run)
    except Exception:
        logger.exception("[greeks-snapshot] EOD snapshot failed")


# ── Paper-trading jobs ───────────────────────────────────────────────────────

async def tick_paper_resting_orders():
    """Fill paper resting LIMIT/SL/GTT orders whose live price has crossed.
    Runs every 5 minutes during market hours."""
    from backend.database import SessionLocal
    from backend.paper.jobs import tick_paper_accounts

    db = SessionLocal()
    try:
        summary = tick_paper_accounts(db)
        db.commit()
        if summary["filled"] or summary["cancelled"]:
            logger.info(
                f"[paper] resting tick: filled={len(summary['filled'])} "
                f"cancelled={len(summary['cancelled'])} across "
                f"{summary['accounts']} account(s)"
            )
    except Exception:
        db.rollback()
        logger.exception("paper resting tick failed")
    finally:
        db.close()


async def mark_paper_positions_intraday():
    """Refresh last_price (and thus unrealized/day P&L) for every open paper
    position. Runs every 5 minutes during market hours — without this, a
    position's P&L was frozen at its fill-time value until the once-daily
    15:37 NAV snapshot (found 2026-07-06 live-testing the beta)."""
    from backend.database import SessionLocal
    from backend.paper.jobs import mark_open_positions

    db = SessionLocal()
    try:
        summary = mark_open_positions(db)
        db.commit()
        if summary["positions_marked"]:
            logger.info(
                f"[paper] intraday mark: {summary['positions_marked']} "
                f"position(s) across {summary['accounts']} account(s)"
            )
    except Exception:
        db.rollback()
        logger.exception("paper intraday marking failed")
    finally:
        db.close()


async def snapshot_paper_navs():
    """Write each paper account's daily NAV snapshot (the equity curve).
    Runs at 15:35 IST."""
    from backend.database import SessionLocal
    from backend.paper.jobs import snapshot_all_navs
    from backend.paper.scorecards import refresh_all_idea_scorecards

    db = SessionLocal()
    try:
        nifty = None
        try:
            from backend.kite.market_data import get_nifty_level
            nifty = get_nifty_level()
        except Exception:
            pass
        n = snapshot_all_navs(db, nifty_close=nifty)
        # Forward-test (P6): same EOD txn + same NIFTY close — write each
        # idea's idea-grain NAV snapshot and refresh its scorecard_cache
        # (metrics + verdict + promotion gate), so account and idea series
        # share one benchmark and commit atomically.
        m = refresh_all_idea_scorecards(db, nifty_close=nifty)
        db.commit()
        logger.info(
            f"[paper] NAV snapshot written for {n} account(s); "
            f"scorecards refreshed for {m} idea(s)"
        )
    except Exception:
        db.rollback()
        logger.exception("paper NAV snapshot failed")
    finally:
        db.close()


# ── Job 1: Execute Due SIPs ──────────────────────────────────────────────────

async def execute_due_sips():
    """
    Runs at 09:15 IST every trading weekday.
    Finds all active SIPs whose next_execution_at is today or earlier.
    Places market orders via Kite. Updates next_execution_at in DB.
    """
    from backend.database import SessionLocal
    from backend.kite.auth import read_kite_access_token
    from backend.models import SIPSchedule, TradeLog, User
    # Route SIP buys through the paper shim too, so a paper-mode user's
    # recurring SIP fills into the same structured portfolio as their chat
    # + workflow orders (not a separate kite-mock book).
    from backend.paper.routing import submit_order_for_user
    from backend.cache import get_redis

    fired_at = now_ist()
    logger.info(f"[{format_ist_short(fired_at)}] SIP execution job started")

    if not is_trading_day():
        logger.info(
            f"[{format_ist_short(fired_at)}] "
            f"Not a trading day — skipping SIP execution"
        )
        return

    db = SessionLocal()
    executed_count = 0
    failed_count = 0

    try:
        due_sips = (
            db.query(SIPSchedule)
            .filter(
                SIPSchedule.is_active == True,  # noqa: E712
                SIPSchedule.next_execution_at <= fired_at,
            )
            .all()
        )

        logger.info(
            f"[{format_ist_short(fired_at)}] "
            f"Found {len(due_sips)} SIP(s) due for execution"
        )

        for sip in due_sips:
            try:
                current_price = _get_price_for_symbol(sip.symbol)
                quantity = (
                    max(1, int(sip.amount / current_price))
                    if current_price > 0 else 1
                )

                user = db.query(User).filter(User.id == sip.user_id).first()
                kite_token = (
                    read_kite_access_token(user.active_broker_session)
                    if user and user.active_broker_session
                    else ""
                ) or "mock_token"

                result = submit_order_for_user(
                    db, sip.user_id,
                    access_token=kite_token,
                    tradingsymbol=sip.symbol,
                    exchange="NSE",
                    transaction_type="BUY",
                    quantity=quantity,
                    order_type="MARKET",
                    product="CNC",
                    tag=f"sip_{sip.id}",
                    # retry-stable per SIP per day so a re-run doesn't double-fill
                    client_request_id=f"sip:{sip.id}:{now_ist().strftime('%Y-%m-%d')}",
                    source="sip",
                )

                execution_time_ist = format_ist(now_ist())

                trade = TradeLog(
                    user_id=sip.user_id,
                    kite_order_id=result.get("order_id"),
                    symbol=sip.symbol,
                    exchange="NSE",
                    transaction_type="BUY",
                    order_type="MARKET",
                    quantity=quantity,
                    status=result.get("status", "PENDING"),
                    source="sip",
                    source_id=sip.id,
                )
                db.add(trade)

                sip.total_invested = (sip.total_invested or 0) + sip.amount
                sip.total_units_bought = (sip.total_units_bought or 0) + quantity

                if sip.frequency == "monthly":
                    sip.next_execution_at = next_monthly_execution(
                        sip.day_of_month or 1
                    )
                elif sip.frequency == "weekly":
                    sip.next_execution_at = next_weekly_execution(
                        sip.day_of_week or 0
                    )
                elif sip.frequency == "daily":
                    sip.next_execution_at = next_daily_execution()

                db.commit()
                executed_count += 1

                logger.info(
                    f"[{format_ist_short(now_ist())}] "
                    f"SIP {sip.id} executed: {sip.symbol} x{quantity} "
                    f"@ ~₹{current_price:.2f} | "
                    f"Order: {result.get('order_id')} | "
                    f"Next run: "
                    f"{format_ist(sip.next_execution_at, include_seconds=False)}"
                )

                _store_notification(
                    f"notification:sip:{sip.user_id}:{sip.id}",
                    {
                        "type": "sip_executed",
                        "sip_id": sip.id,
                        "symbol": sip.symbol,
                        "quantity": quantity,
                        "amount": sip.amount,
                        "order_id": result.get("order_id"),
                        "status": result.get("status", "PENDING"),
                        "executed_at": execution_time_ist,
                        "next_run": format_ist(
                            sip.next_execution_at, include_seconds=False
                        ),
                        "message": (
                            f"SIP executed: Bought {quantity} units of "
                            f"{sip.symbol} at {execution_time_ist}. "
                            f"Next run: "
                            f"{format_ist(sip.next_execution_at, include_seconds=False)}."
                        ),
                    },
                    ttl=86400,
                )

            except Exception as e:
                failed_count += 1
                logger.error(
                    f"[{format_ist_short(now_ist())}] "
                    f"SIP {sip.id} ({sip.symbol}) FAILED: {e}"
                )
                db.rollback()

                _store_notification(
                    f"notification:sip_failed:{sip.user_id}:{sip.id}",
                    {
                        "type": "sip_failed",
                        "sip_id": sip.id,
                        "symbol": sip.symbol,
                        "failed_at": format_ist(now_ist()),
                        "reason": str(e),
                        "message": (
                            f"SIP for {sip.symbol} could not execute at "
                            f"{format_ist(now_ist())}. Reason: {str(e)[:100]}"
                        ),
                    },
                    ttl=86400,
                )

    finally:
        db.close()

    logger.info(
        f"[{format_ist_short(now_ist())}] "
        f"SIP job complete: {executed_count} executed, {failed_count} failed"
    )


def _get_price_for_symbol(symbol: str) -> float:
    """
    Best-effort live price for a NSE symbol. Falls back to a conservative
    estimate so a SIP market order can still be sized when quotes are
    unavailable. Never raises.
    """
    try:
        from backend.cache import get_redis
        rc = get_redis()
        cached = rc.get(f"price:{symbol}")
        if cached:
            data = json.loads(cached)
            ltp = float(data.get("ltp", 0))
            if ltp > 0:
                return ltp
    except Exception:
        pass

    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{symbol}.NS")
        hist = ticker.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning(
            f"[{format_ist_short(now_ist())}] "
            f"yfinance lookup failed for {symbol}: {e}"
        )

    logger.warning(
        f"[{format_ist_short(now_ist())}] "
        f"No price available for {symbol} — using fallback ₹100"
    )
    return 100.0


def _store_notification(key: str, payload: dict, ttl: int):
    """Best-effort Redis notification write. Swallows Redis errors."""
    try:
        from backend.cache import get_redis
        rc = get_redis()
        rc.set(key, json.dumps(payload), ex=ttl)
    except Exception as e:
        logger.warning(
            f"[{format_ist_short(now_ist())}] "
            f"Could not store notification {key}: {e}"
        )


# ── Job 2: Strategy Trigger Checker ──────────────────────────────────────────

async def check_strategy_triggers():
    """
    Runs every 60 seconds during market hours (09:15-15:30 IST weekdays).
    Evaluates active strategy conditions against live prices.
    """
    from backend.database import SessionLocal
    from backend.models import Strategy, StrategyStatus
    from backend.kite.market_data import get_nifty_level

    if not is_trading_day():
        return

    db = SessionLocal()
    try:
        strategies = (
            db.query(Strategy)
            .filter(Strategy.status == StrategyStatus.active)
            .all()
        )

        if not strategies:
            return

        nifty_level = get_nifty_level()
        check_time = now_ist()

        for strategy in strategies:
            try:
                condition = json.loads(strategy.trigger_condition or "{}")
                triggered = False

                if strategy.strategy_type == "price_drop":
                    threshold_pct = condition.get("threshold_pct", 5)
                    reference = condition.get("reference_price", nifty_level)
                    if reference and nifty_level < reference * (1 - threshold_pct / 100):
                        triggered = True

                elif strategy.strategy_type == "price_cross":
                    target = condition.get("target_price", 0)
                    symbol_price = condition.get("current_price", 0)
                    direction = condition.get("direction", "above")
                    if direction == "above" and symbol_price >= target:
                        triggered = True
                    elif direction == "below" and symbol_price <= target:
                        triggered = True

                if triggered:
                    trigger_time_ist = format_ist(check_time)
                    logger.info(
                        f"[{format_ist_short(check_time)}] "
                        f"Strategy {strategy.id} triggered "
                        f"({strategy.strategy_type}) at {trigger_time_ist}"
                    )
                    strategy.last_triggered_at = check_time
                    db.commit()

                    _store_notification(
                        f"notification:strategy:{strategy.user_id}:{strategy.id}",
                        {
                            "type": "strategy_triggered",
                            "strategy_id": strategy.id,
                            "strategy_name": strategy.name,
                            "triggered_at": trigger_time_ist,
                            "message": (
                                f"Strategy '{strategy.name}' triggered at "
                                f"{trigger_time_ist}."
                            ),
                        },
                        ttl=3600,
                    )

            except Exception as e:
                logger.error(
                    f"[{format_ist_short(now_ist())}] "
                    f"Strategy {strategy.id} check error: {e}"
                )

    finally:
        db.close()


# ── Job 3: Refresh Broker Tokens ─────────────────────────────────────────────

def refresh_broker_tokens():
    """
    Runs at 07:30 IST every weekday — before any order jobs.

    For every active BrokerSession, attempt a silent token refresh via the
    broker's connector (``mint_access_token``). Brokers with an unattended
    path (Dhan rolling renew, Fyers refresh token, Kite opt-in TOTP) roll
    forward with no human step; brokers without one raise
    ``NeedsManualLogin`` — we log that the session needs a manual reconnect
    (leaving it active so the UI can prompt the user) and move on. Never
    lets one bad session kill the sweep.

    Module-level (NOT a closure) because the SQLAlchemy jobstore serializes
    callables by textual reference — a closure breaks scheduler.start().
    """
    from backend.brokers.audit import record_audit
    from backend.brokers.base import NeedsManualLogin
    from backend.brokers.registry import get_connector
    from backend.database import SessionLocal
    from backend.models import BrokerSession

    check_time = now_ist()
    logger.info(
        f"[{format_ist_short(check_time)}] Broker token refresh starting"
    )

    db = SessionLocal()
    refreshed = 0
    manual = 0
    failed = 0
    try:
        sessions = (
            db.query(BrokerSession)
            .filter(BrokerSession.is_active == True)  # noqa: E712
            .all()
        )

        for session in sessions:
            try:
                get_connector(session.broker).mint_access_token(db, session)
                refreshed += 1
                record_audit(
                    db, user_id=session.user_id, broker=session.broker,
                    event_type="token_refresh", status="ok",
                )
            except NeedsManualLogin as exc:
                # No unattended path — keep the session active so the UI can
                # prompt a reconnect; just flag it. SIP / automations on this
                # broker may fail until the user re-authenticates.
                manual += 1
                record_audit(
                    db, user_id=session.user_id, broker=session.broker,
                    event_type="token_refresh_failed",
                    status="needs_manual_login", detail=str(exc),
                )
                logger.warning(
                    f"[{format_ist_short(now_ist())}] "
                    f"BrokerSession {session.id} ({session.broker}, user "
                    f"{session.user_id}) needs manual reconnect — no "
                    f"unattended token refresh available."
                )
            except Exception as e:  # noqa: BLE001
                failed += 1
                record_audit(
                    db, user_id=session.user_id, broker=session.broker,
                    event_type="token_refresh_failed",
                    status="error", detail=str(e),
                )
                logger.error(
                    f"[{format_ist_short(now_ist())}] "
                    f"BrokerSession {session.id} ({session.broker}) token "
                    f"refresh failed: {e}"
                )
        db.commit()
        logger.info(
            f"[{format_ist_short(now_ist())}] "
            f"Broker token refresh complete: {refreshed} refreshed, "
            f"{manual} need manual reconnect, {failed} failed "
            f"({len(sessions)} active session(s))"
        )
    except Exception:
        db.rollback()
        logger.exception("broker token refresh job failed")
    finally:
        db.close()


# ── Job 4: Daily Market Summary ──────────────────────────────────────────────

async def send_daily_summary():
    """
    Runs at 15:45 IST every weekday — 15 minutes after market close.
    Stores a daily summary notification for each active user.
    """
    from backend.database import SessionLocal
    from backend.models import User, SIPSchedule

    summary_time = now_ist()
    logger.info(
        f"[{format_ist_short(summary_time)}] Daily summary job running"
    )

    db = SessionLocal()
    try:
        users = (
            db.query(User)
            .filter(User.is_active == True)  # noqa: E712
            .all()
        )

        for user in users:
            upcoming_sips = (
                db.query(SIPSchedule)
                .filter(
                    SIPSchedule.user_id == user.id,
                    SIPSchedule.is_active == True,  # noqa: E712
                )
                .order_by(SIPSchedule.next_execution_at)
                .limit(3)
                .all()
            )

            upcoming = [
                {
                    "symbol": s.symbol,
                    "amount": s.amount,
                    "next_run": format_ist(
                        s.next_execution_at, include_seconds=False
                    ),
                }
                for s in upcoming_sips
                if s.next_execution_at
            ]

            summary = {
                "type": "daily_summary",
                "date": summary_time.strftime("%d %b %Y IST"),
                "generated_at": format_ist(summary_time),
                "upcoming_sips": upcoming,
                "message": (
                    f"Market closed at 15:30 IST. "
                    + (
                        f"Your next SIP: {upcoming[0]['symbol']} on "
                        f"{upcoming[0]['next_run']}."
                        if upcoming else
                        "No upcoming SIPs scheduled."
                    )
                ),
            }

            _store_notification(
                f"notification:daily_summary:{user.id}",
                summary,
                ttl=86400,
            )

    finally:
        db.close()

    logger.info(
        f"[{format_ist_short(now_ist())}] Daily summary job complete"
    )
