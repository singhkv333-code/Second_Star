#!/usr/bin/env python3
"""Resumable parallel V3 scan directly over Azure one-minute Parquet blobs."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chart_patterns_v3 as v3  # noqa: E402
import dataserver as ds  # noqa: E402

ACCOUNT = "https://pivotmarketdata.blob.core.windows.net/kite-1min"
INTERVAL_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15,
                    "30m": 30, "1h": 60}


def _safe(symbol: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in symbol)


def _access_token() -> str:
    query = urllib.parse.urlencode({
        "api-version": "2018-02-01",
        "resource": "https://storage.azure.com/",
    })
    req = urllib.request.Request(
        f"http://169.254.169.254/metadata/identity/oauth2/token?{query}",
        headers={"Metadata": "true"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]


def _download(symbol: str) -> bytes:
    blob = urllib.parse.quote(f"nse/1min/{symbol}_1min.parquet")
    req = urllib.request.Request(
        f"{ACCOUNT}/{blob}",
        headers={"Authorization": f"Bearer {_access_token()}",
                 "x-ms-version": "2023-11-03"})
    return urllib.request.urlopen(req, timeout=180).read()


def _rows(data: bytes) -> list[tuple]:
    table = pq.read_table(io.BytesIO(data), columns=["epoch", "o", "h", "l", "c", "v"])
    cols = [table.column(name).to_numpy(zero_copy_only=False) for name in
            ("epoch", "o", "h", "l", "c", "v")]
    return [(int(ts), float(o) / 100.0, float(h) / 100.0,
             float(l) / 100.0, float(c) / 100.0, float(vol))
            for ts, o, h, l, c, vol in zip(*cols)
            if o > 0 and h > 0 and l > 0 and c > 0]


def _enrich(events: list[dict], rows: list[tuple]) -> list[dict]:
    for event in events:
        for index_key, time_key in (("formation_start_i", "formation_start_ts"),
                                    ("formation_end_i", "formation_end_ts"),
                                    ("first_detectable_i", "first_detectable_ts"),
                                    ("completion_i", "completion_ts")):
            i = event.get(index_key)
            if isinstance(i, int) and 0 <= i < len(rows):
                event[time_key] = int(rows[i][0])
        i = event.get("completion_i")
        if isinstance(i, int) and 0 <= i < len(rows):
            event["completion_close"] = rows[i][4]
    return events


def scan_symbol(symbol: str, intervals: list[str], out_root: str) -> dict:
    started = time.time(); target = Path(out_root) / f"symbol={_safe(symbol)}"
    final = target / "events.json"
    if final.exists():
        try:
            old = json.loads(final.read_text())
            if (old.get("detector_version") == v3.DETECTOR_VERSION and
                    old.get("intervals") == intervals):
                return {"symbol": symbol, "status": "skipped",
                        "events": sum(len(x["events"]) for x in old["results"])}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    native = _rows(_download(symbol)); session = ds.session_for(symbol)
    results = []
    for interval in intervals:
        rows = native if interval == "1m" else [tuple(x) for x in
            ds._resample_intraday(native, INTERVAL_MINUTES[interval], session)]
        events = _enrich(v3.event_driven_edge_patterns(
            rows, symbol, set(ds._EDGE_ONLY)), rows)
        results.append({"interval": interval, "bars": len(rows), "events": events})
    target.mkdir(parents=True, exist_ok=True)
    payload = {"detector_version": v3.DETECTOR_VERSION, "symbol": symbol,
               "source": f"nse/1min/{symbol}_1min.parquet",
               "intervals": intervals, "native_bars": len(native),
               "results": results, "elapsed_seconds": round(time.time()-started, 3)}
    tmp = final.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    os.replace(tmp, final)
    return {"symbol": symbol, "status": "complete", "native_bars": len(native),
            "events": sum(len(x["events"]) for x in results),
            "elapsed_seconds": payload["elapsed_seconds"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols-file", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--intervals", default="1m,3m,5m,15m,30m,1h")
    ap.add_argument("--workers", type=int, default=12); ap.add_argument("--heartbeat", required=True)
    args = ap.parse_args(); symbols = json.loads(Path(args.symbols_file).read_text())
    intervals = [x.strip() for x in args.intervals.split(",") if x.strip()]
    unknown = set(intervals) - set(INTERVAL_MINUTES)
    if unknown: raise SystemExit(f"unsupported intervals: {sorted(unknown)}")
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    hb = Path(args.heartbeat); hb.parent.mkdir(parents=True, exist_ok=True)
    completed: list[dict] = []; failures: list[dict] = []; started = time.time()
    lock = threading.Lock(); stop = threading.Event()

    def ping(status="running"):
        with lock:
            state = {"phase": "blob_native_chart_v3", "status": status,
                     "updated_epoch": time.time(), "completed": len(completed),
                     "total": len(symbols), "failed": len(failures),
                     "elapsed_seconds": round(time.time()-started, 1),
                     "intervals": intervals, "workers": min(args.workers, len(symbols))}
        tmp = hb.with_suffix(".tmp"); tmp.write_text(json.dumps(state, sort_keys=True)+"\n")
        os.replace(tmp, hb)

    def heartbeat_loop():
        while not stop.wait(30): ping()

    ping(); thread = threading.Thread(target=heartbeat_loop, daemon=True); thread.start()
    with ProcessPoolExecutor(max_workers=min(args.workers, len(symbols))) as pool:
        jobs = {pool.submit(scan_symbol, s, intervals, str(out)): s for s in symbols}
        for future in as_completed(jobs):
            symbol = jobs[future]
            try:
                result = future.result()
                with lock: completed.append(result)
            except Exception as exc:
                with lock: failures.append({"symbol": symbol, "error": repr(exc)})
            ping(); print(json.dumps(json.loads(hb.read_text())), flush=True)
    stop.set(); thread.join(timeout=2)
    manifest = {"detector_version": v3.DETECTOR_VERSION, "symbols": len(symbols),
                "intervals": intervals, "completed": completed, "failures": failures,
                "elapsed_seconds": round(time.time()-started, 2)}
    (out / "scan-manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    ping("failed" if failures else "complete")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
