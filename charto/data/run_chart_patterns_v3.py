#!/usr/bin/env python3
"""Parallel chart-v3 pilot over symbol/interval Parquet partitions."""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pyarrow.dataset as pads

import chart_patterns_v3 as v3
import dataserver as ds


def _safe(symbol: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in symbol)


def scan_task(root: str, out: str, symbol: str, interval: str) -> dict:
    started = time.time()
    path = Path(root) / f"interval={interval}" / f"symbol={_safe(symbol)}"
    files = sorted(str(p) for p in path.glob("year=*/bars.parquet"))
    if not files:
        raise FileNotFoundError(f"no Parquet partitions under {path}")
    table = pads.dataset(files, format="parquet").to_table(
        columns=["ts", "open", "high", "low", "close", "volume"])
    table = table.sort_by([("ts", "ascending")])
    cols = [table.column(i).to_pylist() for i in range(6)]
    rows = list(zip(*cols))
    events = v3.event_driven_edge_patterns(rows, symbol, set(ds._EDGE_ONLY))
    target = Path(out) / f"interval={interval}" / f"symbol={_safe(symbol)}"
    target.mkdir(parents=True, exist_ok=True)
    payload = {"detector_version": v3.DETECTOR_VERSION, "symbol": symbol,
               "interval": interval, "bars": len(rows), "events": events,
               "elapsed_seconds": round(time.time() - started, 3)}
    tmp = target / "events.json.tmp"; final = target / "events.json"
    tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    tmp.replace(final)
    return {k: payload[k] for k in ("symbol", "interval", "bars",
                                      "elapsed_seconds")} | {"events": len(events)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parquet", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--symbols", required=True); ap.add_argument("--intervals", required=True)
    ap.add_argument("--workers", type=int, default=8); ap.add_argument("--heartbeat", required=True)
    a = ap.parse_args(); symbols = [x.strip() for x in a.symbols.split(",") if x.strip()]
    intervals = [x.strip() for x in a.intervals.split(",") if x.strip()]
    tasks = [(s, iv) for s in symbols for iv in intervals]; out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True); hb = Path(a.heartbeat); done = []; failed = []
    started = time.time()
    def ping(status="running"):
        state = {"phase": "chart_v3_scan", "status": status,
                 "updated_epoch": time.time(), "completed": len(done),
                 "total": len(tasks), "failed": len(failed),
                 "elapsed_seconds": round(time.time() - started, 1)}
        hb.write_text(json.dumps(state, sort_keys=True) + "\n"); return state
    ping()
    workers = min(a.workers, len(tasks))
    if workers == 1:
        for symbol, interval in tasks:
            try: done.append(scan_task(a.parquet, str(out), symbol, interval))
            except Exception as exc: failed.append(
                {"task": (symbol, interval), "error": repr(exc)})
            print(json.dumps(ping()), flush=True)
    else:
      with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(scan_task, a.parquet, str(out), s, iv): (s, iv)
                for s, iv in tasks}
        while jobs:
            completed = []
            try:
                for future in as_completed(jobs, timeout=30):
                    completed.append(future)
            except TimeoutError:
                pass
            for future in completed:
                task = jobs.pop(future)
                try: done.append(future.result())
                except Exception as exc: failed.append({"task": task, "error": repr(exc)})
            print(json.dumps(ping()), flush=True)
    manifest = {"detector_version": v3.DETECTOR_VERSION, "tasks": done,
                "failures": failed, "elapsed_seconds": round(time.time()-started, 2)}
    (out / "scan-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    ping("failed" if failed else "complete")
    return 1 if failed else 0


if __name__ == "__main__": raise SystemExit(main())
