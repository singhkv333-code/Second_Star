"""Cancel the pivot-test order created by kite_live_test.py."""
import json
import sys

from kiteconnect import KiteConnect

STATE_PATH = "/tmp/pivot_kite_state.json"


def main() -> int:
    with open(STATE_PATH) as fh:
        state = json.load(fh)

    kite = KiteConnect(api_key=state["api_key"])
    kite.set_access_token(state["access_token"])

    order_id = state.get("order_id")
    variety = state.get("variety", "regular")
    if not order_id:
        print("No order_id in state file.")
        return 1

    print(f"Cancelling order_id={order_id} (variety={variety})")
    kite.cancel_order(variety=variety, order_id=order_id)
    print("✔ Cancel request submitted.")

    print("\nOrders after cancel:")
    for o in kite.orders():
        if o.get("order_id") == order_id:
            print(
                f"  order_id={o['order_id']}  status={o['status']}  "
                f"price={o['price']}  variety={o.get('variety')}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
