"""Scheduled Kite session auto-relogin — kills the ~7:30 IST daily death.

Zerodha invalidates every Kite access token each morning; until someone
re-logged in manually, all market data silently degraded to the yfinance
fallback for the rest of the day. The encrypted-credential replay ALREADY
existed (`KiteConnector.mint_access_token`: persistence_mode="totp_login" +
auto_login_opt_in, credentials stored encrypted via backend/security/
encryption.py, TOTP codes generated from the stored 2FA *seed*) — but
nothing ever CALLED it on a schedule. This module is that caller.

Runs as an APScheduler cron (registered in workflows/scheduler.py; this
function is MODULE-LEVEL — the F&O gotcha: closures kill the scheduler)
daily shortly after the token-expiry window, plus a catch-up retry. For
each opted-in Kite session whose token no longer validates it replays the
login, then makes sure the tick WebSocket is running under a live token.

Sessions WITHOUT stored credentials are left alone — they keep the honest
"reconnect in the app" behaviour. Everything is best-effort: a failed
replay logs a warning and the day continues on the tagged fallback.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def refresh_kite_sessions() -> None:
    """Mint fresh tokens for auto-login-opted Kite sessions with dead
    tokens; then ensure the ticker runs under a live token. Module-level
    for APScheduler."""
    from backend.brokers.registry import get_connector
    from backend.database import SessionLocal
    from backend.kite import auth as kite_auth
    from backend.kite.auth import read_kite_access_token
    from backend.models import BrokerSession

    connector = get_connector("kite")
    if connector.mock_mode():
        return

    db = SessionLocal()
    minted = 0
    try:
        sessions = (
            db.query(BrokerSession)
            .filter(
                BrokerSession.broker == "kite",
                BrokerSession.persistence_mode == "totp_login",
                BrokerSession.auto_login_opt_in.is_(True),
            )
            .all()
        )
        if not sessions:
            logger.info("kite auto-relogin: no opted-in sessions")
            return
        for s in sessions:
            token = read_kite_access_token(s)
            try:
                if token and not token.startswith("mock_") and \
                        kite_auth.verify_token_valid(token):
                    # Still alive. If some earlier failure-sniff wrongly
                    # deactivated the session (2026-07-10: a transient error
                    # tripped main's token-invalid heuristic and the flag was
                    # a one-way trap — every read path filters on is_active),
                    # a VERIFIED token is proof the session is good: reactivate.
                    if not s.is_active:
                        s.is_active = True
                        db.add(s)
                        db.commit()
                        minted += 1  # counts as a heal → ticker restart below
                        logger.info(
                            "kite auto-relogin: token verified fine — "
                            "reactivated wrongly-inactive session for user %s",
                            s.user_id,
                        )
                    continue  # nothing to mint
            except Exception:  # noqa: BLE001 — treat verify failure as dead
                pass
            try:
                connector.mint_access_token(db, s)
                minted += 1
                logger.info(
                    "kite auto-relogin: fresh token minted for user %s",
                    s.user_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "kite auto-relogin FAILED for user %s: %s — data will "
                    "fall back to yfinance until a manual reconnect",
                    s.user_id, str(e)[:200],
                )
    finally:
        db.close()

    # Ticker under a live token: if we minted (old WS is on a dead token) or
    # it simply isn't running, restart it seeded with holdings + universe.
    try:
        from backend.kite.ticker import get_ticker_manager

        manager = get_ticker_manager()
        if minted or not manager.status().get("running"):
            if manager.status().get("running"):
                manager.stop()
            _start_ticker_fresh()
    except Exception as e:  # noqa: BLE001
        logger.warning("kite auto-relogin: ticker restart failed: %s", e)


def _start_ticker_fresh() -> None:
    """Start the ticker under the most recent active session (holdings +
    sector-universe seeds) — mirrors main._maybe_autostart_kite_ticker,
    which can't be imported here (main → scheduler → this module)."""
    from backend.brokers.sessions import get_active_kite_session
    from backend.database import SessionLocal
    from backend.kite.auth import read_kite_access_token
    from backend.kite.portfolio import get_holdings
    from backend.kite.ticker import get_ticker_manager

    db = SessionLocal()
    try:
        session = get_active_kite_session(db)
        if session is None:
            return
        token = read_kite_access_token(session)
        if not token or token.startswith("mock_"):
            return
        seeds: list[str] = []
        try:
            for h in get_holdings(token) or []:
                ts = h.get("tradingsymbol") if isinstance(h, dict) else None
                if ts:
                    seeds.append(str(ts))
        except Exception:  # noqa: BLE001 — holdings seed is best-effort
            pass
        try:
            from backend.services.sector_universe import _UNIVERSE

            have = {s.upper() for s in seeds}
            seeds.extend(
                r.symbol for r in _UNIVERSE if r.symbol.upper() not in have
            )
        except Exception:  # noqa: BLE001
            pass
        get_ticker_manager().start(
            access_token=token,
            user_id=int(session.user_id) if session.user_id else None,
            seed_symbols=seeds,
        )
        logger.info("kite auto-relogin: ticker restarted (%d seeds)", len(seeds))
    finally:
        db.close()


__all__ = ["refresh_kite_sessions"]
