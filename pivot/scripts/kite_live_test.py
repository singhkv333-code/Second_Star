"""
One-shot live test of Kite Connect with the user-provided credentials.

Flow:
  1. Exchange the supplied request_token for an access_token using the API secret.
  2. PERSIST the access_token immediately to /tmp/pivot_kite_state.json so a
     downstream error doesn't force a fresh OAuth round-trip.
  3. Confirm authentication by fetching kite.profile().
  4. Place a LIMIT BUY 1 share of TCS at a price comfortably below market.
     If the regular variety is rejected (markets are closed after 15:30 IST),
     fall back to AMO (after-market order).
  5. Print the order book row for the just-placed order.

The market-data subscription is NOT required for order placement — only for
quote/LTP/historical APIs. We skip kite.ltp() and use a hardcoded limit price.
"""
import json
import os
import sys
import time

from kiteconnect import KiteConnect

# Read credentials from env so the secret never lives in source.
# Matches backend/config.py:Settings field names.
#   export KITE_API_KEY=...
#   export KITE_API_SECRET=...
API_KEY = os.environ.get("KITE_API_KEY", "")
API_SECRET = os.environ.get("KITE_API_SECRET", "")

if not API_KEY or not API_SECRET:
    print(
        "KITE_API_KEY and KITE_API_SECRET env vars must be set.",
        file=sys.stderr,
    )
    sys.exit(1)

# Hardcoded LIMIT price for the TCS BUY test. TCS typically trades in the
# ₹3500-4500 range; ₹3500 is below market, well within the daily ±10%
# circuit limit, so the order is accepted but will not fill.
LIMIT_PRICE = 3500.0

STATE_PATH = "/tmp/pivot_kite_state.json"


def _save_state(**kwargs) -> None:
    try:
        with open(STATE_PATH) as fh:
            existing = json.load(fh)
    except (OSError, json.JSONDecodeError):
        existing = {}
    existing.update(kwargs)
    with open(STATE_PATH, "w") as fh:
        json.dump(existing, fh)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: kite_live_test.py <request_token>")
        return 1
    request_token = sys.argv[1]

    kite = KiteConnect(api_key=API_KEY)

    print(f"[1/4] Exchanging request_token={request_token[:6]}… for access_token")
    session = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = session["access_token"]
    kite_user_id = session.get("user_id")
    print(f"      ✔ access_token acquired (user_id={kite_user_id})")

    # Persist the access_token IMMEDIATELY so any later failure doesn't
    # waste the (single-use) request_token.
    _save_state(
        access_token=access_token,
        kite_user_id=kite_user_id,
        api_key=API_KEY,
    )
    print(f"      ✔ persisted to {STATE_PATH}")

    kite.set_access_token(access_token)

    print("[2/4] kite.profile()")
    profile = kite.profile()
    print(
        f"      user_id={profile.get('user_id')}  "
        f"user_name={profile.get('user_name')}  email={profile.get('email')}"
    )

    print(f"[3/4] Placing LIMIT BUY 1 TCS @ ₹{LIMIT_PRICE} (below market — should not fill)")

    def _try_place(variety: str) -> str | None:
        try:
            return kite.place_order(
                variety=variety,
                exchange="NSE",
                tradingsymbol="TCS",
                transaction_type="BUY",
                quantity=1,
                product="CNC",
                order_type="LIMIT",
                price=LIMIT_PRICE,
                tag="pivot-test",
            )
        except Exception as exc:
            print(f"      variety={variety!r} failed: {exc}")
            return None

    order_id = _try_place("regular")
    used_variety = "regular"
    if not order_id:
        order_id = _try_place("amo")
        used_variety = "amo"

    if not order_id:
        print("Order placement failed for both regular and AMO. Aborting.")
        return 2

    print(f"      ✔ Order placed (variety={used_variety}): order_id={order_id}")
    _save_state(order_id=order_id, variety=used_variety, limit_price=LIMIT_PRICE)

    # Give Kite a beat to register the order.
    time.sleep(1.5)

    print("\n[4/4] Reading order book for pivot-test tagged orders")
    matched = []
    for o in kite.orders():
        if o.get("tag") == "pivot-test":
            matched.append(o)
            print(
                f"  order_id={o['order_id']}  status={o['status']}  "
                f"{o['transaction_type']} {o['tradingsymbol']} qty={o['quantity']} "
                f"price={o['price']}  variety={o.get('variety')}"
            )
    if not matched:
        print("  (no pivot-test rows yet — order may still be propagating)")

    print(f"\nDone. Access token + order_id saved to {STATE_PATH}.")
    print("Cancel with:  python3 scripts/kite_cancel.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
