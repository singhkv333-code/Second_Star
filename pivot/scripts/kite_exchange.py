"""One-shot Kite request_token -> access_token exchange + session store + verify.

Usage (run from pivot/, with .venv active):

    python scripts/kite_exchange.py <request_token> [pivot_user_id]

Where <request_token> is the value from the Kite login redirect URL after you
log in (+ 2FA) in your browser at:
    https://kite.zerodha.com/connect/login?api_key=<KITE_API_KEY>&v=3

Notes
-----
- 2FA happens in your browser on Zerodha's page; nothing 2FA-related is passed
  here. The request_token is single-use and expires in a few minutes -- run
  this promptly.
- The api_secret never leaves the server (read from .env via settings).
- On success this stores an ACTIVE KiteSession (encrypted at rest if
  KITE_TOKEN_ENC_KEY is set) and prints a live quote to prove the connection.
- pivot_user_id defaults to the existing KiteSession owner (43).
"""
from __future__ import annotations

import os
import sys

# Make `backend` importable when run as `python scripts/kite_exchange.py`
# from the pivot/ root (the script's own dir is on sys.path, not pivot/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import settings
from backend.database import SessionLocal
from backend.kite.auth import (
    get_authenticated_kite,
    read_kite_access_token,
    verify_token_valid,
)
from backend.routers.kite import _upsert_session


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("usage: python scripts/kite_exchange.py <request_token> [pivot_user_id]")
        return 2
    request_token = sys.argv[1].strip()
    pivot_user_id = int(sys.argv[2]) if len(sys.argv) > 2 else 43

    api_key = (getattr(settings, "kite_api_key", "") or "").strip()
    api_secret = (getattr(settings, "kite_api_secret", "") or "").strip()
    if not api_key or not api_secret:
        print("ERROR: KITE_API_KEY / KITE_API_SECRET not set in .env")
        return 1

    # Canonical Kite Connect exchange (well-documented return shape):
    # generate_session(request_token, api_secret) -> {access_token, user_id, ...}
    try:
        from kiteconnect import KiteConnect
    except Exception as e:  # noqa: BLE001
        print("ERROR: kiteconnect not importable:", e)
        return 1

    kc = KiteConnect(api_key=api_key)
    try:
        sess = kc.generate_session(request_token, api_secret=api_secret)
    except Exception as e:  # noqa: BLE001
        print("EXCHANGE FAILED:", type(e).__name__, str(e)[:240])
        print("(request_token may be stale/already-used — re-open the login URL "
              "and copy a fresh one)")
        return 1

    access_token = sess.get("access_token")
    kite_user_id = sess.get("user_id")
    if not access_token:
        print("ERROR: no access_token in generate_session response:", sess)
        return 1

    db = SessionLocal()
    try:
        row = _upsert_session(
            db, pivot_user_id,
            access_token=access_token,
            request_token=request_token,
            kite_user_id=kite_user_id,
        )
        print(f"SESSION STORED: pivot_user={pivot_user_id} kite_user={kite_user_id} "
              f"is_active={row.is_active} expires_at={row.token_expires_at}")
        tok = read_kite_access_token(row)
        ok = verify_token_valid(tok)
        print("verify_token_valid:", ok)
        if ok:
            try:
                live = get_authenticated_kite(tok).quote(["NSE:INFY", "NSE:RELIANCE"])
                print("LIVE QUOTE:", {k: v.get("last_price") for k, v in live.items()})
                print("\nKite is LIVE. Restart :8000 so all data-streaming endpoints "
                      "pick up the new session.")
            except Exception as e:  # noqa: BLE001
                print("stored, but live quote failed:", type(e).__name__, str(e)[:160])
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
