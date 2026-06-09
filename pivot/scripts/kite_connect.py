"""Simple, repeatable Kite Connect login — run this whenever the daily
token expires (~6 AM IST).

    .venv/bin/python scripts/kite_connect.py            # interactive
    .venv/bin/python scripts/kite_connect.py <token>    # token as arg
    .venv/bin/python scripts/kite_connect.py "<full redirect URL>"

What it does
------------
1. Builds the Kite login URL from KITE_API_KEY (.env) and opens it in your
   browser.
2. You log in on Zerodha (user id + password + 2FA). Nothing 2FA-related is
   passed through here — that happens entirely on Zerodha's page.
3. Zerodha redirects to the app's registered Redirect URL with
   ``?request_token=XXXX``. Paste either the bare token OR the whole
   redirected URL — this script extracts the token either way.
   (If the redirect lands on a Pivot "invalid_state" error page, the
   backend logs the token: look for a line like "Recover manually …" in
   /tmp/uvicorn_8000.log and paste that token.)
4. It exchanges the (single-use, ~2-minute) request_token for the daily
   access_token via KiteConnect.generate_session (SHA-256 checksum handled
   by the SDK), stores an ACTIVE KiteSession for your user (encrypted at
   rest if KITE_TOKEN_ENC_KEY is set), verifies it, and prints a live
   quote to prove the connection.

Then restart :8000 so the data/streaming endpoints pick up the new session.
"""
from __future__ import annotations

import os
import re
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import settings  # noqa: E402
from backend.database import SessionLocal  # noqa: E402
from backend.kite.auth import (  # noqa: E402
    get_authenticated_kite,
    read_kite_access_token,
    verify_token_valid,
)
from backend.routers.kite import _upsert_session  # noqa: E402


_TOKEN_RE = re.compile(r"request_token=([A-Za-z0-9]+)")


def _extract_token(raw: str) -> str:
    """Accept a bare token or a full redirect URL and return the token."""
    raw = (raw or "").strip().strip('"').strip("'")
    m = _TOKEN_RE.search(raw)
    if m:
        return m.group(1)
    # Not a URL — assume the whole thing is the token (strip stray query bits).
    return raw.split("&")[0].split("?")[0].strip()


def _default_user_id() -> int:
    """Reuse the existing KiteSession owner so a re-login updates it in
    place; fall back to 43 (the historical local dev user)."""
    try:
        from backend.models import KiteSession
        db = SessionLocal()
        try:
            row = db.query(KiteSession).order_by(KiteSession.id.asc()).first()
            return int(row.user_id) if row and row.user_id else 43
        finally:
            db.close()
    except Exception:
        return 43


def main() -> int:
    api_key = (getattr(settings, "kite_api_key", "") or "").strip()
    api_secret = (getattr(settings, "kite_api_secret", "") or "").strip()
    if not api_key or not api_secret:
        print("ERROR: KITE_API_KEY / KITE_API_SECRET not set in .env")
        return 1

    login_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"

    # Token can come from argv (bare token or full URL) or interactively.
    raw = sys.argv[1] if len(sys.argv) > 1 else ""
    if not raw:
        print("\nStep 1 — log in to Zerodha (user id + password + 2FA):")
        print(f"  {login_url}")
        try:
            webbrowser.open(login_url)
            print("  (opened in your browser)")
        except Exception:
            pass
        print(
            "\nStep 2 — after login Zerodha redirects with ?request_token=…\n"
            "  Paste the bare token OR the whole redirected URL below.\n"
        )
        try:
            raw = input("request_token / URL > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\naborted")
            return 1

    request_token = _extract_token(raw)
    if not request_token:
        print("ERROR: no request_token found in input")
        return 1

    user_id = (
        int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit()
        else _default_user_id()
    )

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
        print("(request_token is single-use and expires in ~2 min — re-open "
              "the login URL and paste a FRESH one)")
        return 1

    access_token = sess.get("access_token")
    if not access_token:
        print("ERROR: no access_token in response:", sess)
        return 1

    db = SessionLocal()
    try:
        row = _upsert_session(
            db, user_id,
            access_token=access_token,
            request_token=request_token,
            kite_user_id=sess.get("user_id"),
        )
        print(
            f"\n✓ SESSION STORED  pivot_user={user_id}  "
            f"kite_user={sess.get('user_id')}  active={row.is_active}  "
            f"expires≈{row.token_expires_at}"
        )
        tok = read_kite_access_token(row)
        if verify_token_valid(tok):
            try:
                live = get_authenticated_kite(tok).quote(
                    ["NSE:INFY", "NSE:RELIANCE"]
                )
                print("✓ LIVE QUOTE:",
                      {k: v.get("last_price") for k, v in live.items()})
            except Exception as e:  # noqa: BLE001
                print("stored, but live quote failed:",
                      type(e).__name__, str(e)[:160])
        else:
            print("WARNING: token stored but verify failed — it may already "
                  "be expired (Kite tokens die at 6 AM IST).")
        print("\nNext: restart :8000 so data/streaming endpoints use the new "
              "session.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
