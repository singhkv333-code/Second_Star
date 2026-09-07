#!/usr/bin/env python3
"""Fixed-interval heartbeat monitor for chart-pattern batch jobs."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("heartbeat"); ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--stale-after", type=int, default=300)
    ap.add_argument("--log", required=True)
    a = ap.parse_args(); hb = Path(a.heartbeat); log = Path(a.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    while True:
        now = time.time(); state = {}
        if hb.exists():
            try: state = json.loads(hb.read_text())
            except (OSError, json.JSONDecodeError): state = {"status": "invalid"}
        age = now - float(state.get("updated_epoch", 0)) if state else None
        ping = {"monitor_epoch": now, "heartbeat": str(hb),
                "heartbeat_age_seconds": round(age, 1) if age is not None else None,
                "healthy": age is not None and age <= a.stale_after,
                "state": state}
        with log.open("a") as f: f.write(json.dumps(ping, sort_keys=True) + "\n")
        print(json.dumps(ping), flush=True)
        if state.get("status") in ("complete", "failed"):
            return 0 if state["status"] == "complete" else 1
        time.sleep(max(5, a.interval))


if __name__ == "__main__":
    raise SystemExit(main())
