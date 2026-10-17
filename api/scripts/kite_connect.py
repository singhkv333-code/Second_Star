"""Simple, repeatable Kite Connect login — run this whenever the daily
token expires (~6 AM IST).

    .venv/bin/python scripts/kite_connect.py            # interactive
    .venv/bin/python scripts/kite_connect.py <user_id>  # attach to a specific Pivot user

What it does
------------
This goes through the SAME path as the frontend's "Connect broker" button
(``backend/routers/brokers.py`` / ``backend/brokers/kite.py``), not a
hand-rolled OAuth flow — so the resulting BrokerSession is identical to one
created by clicking Connect in the app, and market-data reads that pull
"any active Kite session" (``get_active_kite_session``) pick it up either way.

1. Builds a signed state-JWT for the target Pivot user (same
   ``_make_state_token`` the FE's ``GET /brokers/kite/login_url`` uses) and
   embeds it in the Kite login URL via ``redirect_params`` — Kite echoes it
   back on the callback as ``state``.
2. Opens the login URL in your browser. You log in on Zerodha (user id +
   password + 2FA) — nothing 2FA-related passes through this script.
3. If the Kite app's registered Redirect URL points at THIS backend
   (``http://<host>/callback``), the running ``:8000`` server receives the
   redirect and completes the connection automatically — just press Enter
   once you've logged in.
4. If the redirect lands somewhere else (e.g. a deployed frontend, or the
   backend wasn't running), paste the full redirected URL instead — this
   script extracts ``request_token`` and completes the exchange directly via
   the same ``KiteConnector.complete_auth`` the callback route calls, no
   running server required for this step.

Then restart :8000 (if it wasn't already up) so streaming endpoints pick up
the new session — a plain reload isn't needed for REST reads since sessions
are read fresh from the DB per request.
"""
from __future__ import annotations

import os
import re
import sys
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.brokers.registry import get_connector  # noqa: E402
from backend.brokers.sessions import (  # noqa: E402
    get_active_kite_session,
    get_broker_session,
    read_broker_access_token,
)
from backend.database import SessionLocal  # noqa: E402
from backend.kite.auth import get_authenticated_kite, verify_token_valid  # noqa: E402
from backend.routers.brokers import _make_state_token, _read_state_token  # noqa: E402

_TOKEN_RE = re.compile(r"request_token=([A-Za-z0-9]+)")


def _extract_token(raw: str) -> str:
    """Accept a bare token or a full redirect URL and return the token."""
    raw = (raw or "").strip().strip('"').strip("'")
    m = _TOKEN_RE.search(raw)
    if m:
        return m.group(1)
    return raw.split("&")[0].split("?")[0].strip()


def _default_user_id(db) -> int:
    """Reuse whichever Pivot user already has a Kite BrokerSession — prefer
    the currently-active one, else the most recently touched row — so a
    re-login updates the same identity in place. Falls back to 43 (the
    historical local dev user) if no kite BrokerSession exists yet."""
    from backend.models import BrokerSession

    active = get_active_kite_session(db)
    if active and active.user_id:
        return int(active.user_id)
    row = (
        db.query(BrokerSession)
        .filter(BrokerSession.broker == "kite")
        .order_by(BrokerSession.updated_at.desc().nullslast(), BrokerSession.id.desc())
        .first()
    )
    return int(row.user_id) if row and row.user_id else 43


def main() -> int:
    connector = get_connector("kite")

    db = SessionLocal()
    try:
        user_id = (
            int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit()
            else _default_user_id(db)
        )

        if connector.mock_mode():
            print("KITE_API_KEY not set — connecting in MOCK mode.")
            connector.complete_auth(db, user_id, {})
            print(f"✓ Mock session stored for pivot_user={user_id}")
            return 0

        state = _make_state_token(user_id, "kite")
        login_url = connector.get_login_url(state)

        print(f"\nStep 1 — log in to Zerodha (user id + password + 2FA), for pivot_user={user_id}:")
        print(f"  {login_url}")
        try:
            webbrowser.open(login_url)
            print("  (opened in your browser)")
        except Exception:
            pass

        print(
            "\nStep 2a — if this backend (:8000) is reachable at the Kite app's "
            "registered Redirect URL, the login completes automatically.\n"
            "  Just press Enter here once you've finished logging in.\n"
            "Step 2b — otherwise paste the full redirected URL (with "
            "?request_token=...) below.\n"
        )
        try:
            raw = input("Enter, or paste redirected URL > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\naborted")
            return 1

        if raw:
            # Manual path: complete the exchange directly, no server round-trip.
            request_token = _extract_token(raw)
            if not request_token:
                print("ERROR: no request_token found in input")
                return 1
            m = re.search(r"state=([^&]+)", raw)
            if m:
                state_user_id, state_broker = _read_state_token(m.group(1))
                if state_user_id and state_user_id != user_id:
                    print(f"  (state says pivot_user={state_user_id} — using that)")
                    user_id = state_user_id
            try:
                connector.complete_auth(db, user_id, {"request_token": request_token})
            except Exception as e:  # noqa: BLE001
                print("EXCHANGE FAILED:", type(e).__name__, str(e)[:240])
                print("(request_token is single-use and expires in ~2 min — "
                      "re-open the login URL and paste a FRESH one)")
                return 1
        else:
            # Automatic path: poll for the callback (hit by the running
            # server) to have landed within the last couple of minutes.
            print("Checking for an automatic callback...")
            for _ in range(15):
                db.expire_all()
                session = get_broker_session(db, user_id, "kite")
                if session and session.is_active and read_broker_access_token(session):
                    break
                time.sleep(2)
            else:
                print(
                    "ERROR: no session appeared. Either the callback didn't "
                    "reach this backend, or login didn't complete — re-run "
                    "and paste the redirected URL manually instead."
                )
                return 1

        session = get_broker_session(db, user_id, "kite")
        tok = read_broker_access_token(session)
        print(
            f"\n✓ SESSION STORED  pivot_user={user_id}  "
            f"kite_user={session.broker_user_id}  active={session.is_active}  "
            f"expires≈{session.token_expires_at}"
        )
        if verify_token_valid(tok):
            try:
                live = get_authenticated_kite(tok).quote(["NSE:INFY", "NSE:RELIANCE"])
                print("✓ LIVE QUOTE:", {k: v.get("last_price") for k, v in live.items()})
            except Exception as e:  # noqa: BLE001
                print("stored, but live quote failed:", type(e).__name__, str(e)[:160])
        else:
            print("WARNING: token stored but verify failed — it may already "
                  "be expired (Kite tokens die at 6 AM IST).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
