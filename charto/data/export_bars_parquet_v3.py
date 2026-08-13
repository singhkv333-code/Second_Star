#!/usr/bin/env python3
"""One-time SQLite → partitioned Parquet conversion for chart-v3.

Each symbol is read once.  All requested native intervals are resampled from
that stream, then written as immutable symbol/interval/year partitions.  The
manifest is content-addressed so reruns skip already validated partitions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweep_patterns as base  # noqa: E402

SCHEMA_VERSION = 3
COLS = ("ts", "open", "high", "low", "close", "volume")
ARROW_SCHEMA = pa.schema([
    ("ts", pa.int64()), ("open", pa.float64()), ("high", pa.float64()),
    ("low", pa.float64()), ("close", pa.float64()), ("volume", pa.float64()),
])


def _safe_symbol(symbol: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in symbol)


def _year(ts: int) -> int:
    return time.gmtime(ts).tm_year


def _fingerprint(rows: list[tuple]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(("|".join(str(x) for x in row) + "\n").encode())
    return h.hexdigest()


def _write_partition(root: str, symbol: str, interval: str, year: int,
                     rows: list[tuple]) -> dict:
    folder = Path(root) / f"interval={interval}" / f"symbol={_safe_symbol(symbol)}" / f"year={year}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "bars.parquet"
    digest = _fingerprint(rows)
    meta = folder / "manifest.json"
    if target.exists() and meta.exists():
        old = json.loads(meta.read_text())
        if old.get("sha256") == digest and old.get("rows") == len(rows):
            return {**old, "path": str(target), "skipped": True}
    arrays = list(zip(*rows))
    table = pa.Table.from_arrays(
        [pa.array(arrays[i], type=ARROW_SCHEMA.field(i).type)
         for i in range(len(COLS))], schema=ARROW_SCHEMA)
    tmp = target.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd", row_group_size=131_072,
                   use_dictionary=False, write_statistics=True)
    os.replace(tmp, target)
    record = {"schema_version": SCHEMA_VERSION, "symbol": symbol,
              "interval": interval, "year": year, "rows": len(rows),
              "min_ts": int(rows[0][0]), "max_ts": int(rows[-1][0]),
              "sha256": digest, "bytes": target.stat().st_size}
    meta.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    return {**record, "path": str(target), "skipped": False}


def export_symbol(db: str, root: str, symbol: str,
                  intervals: list[str]) -> dict:
    started = time.time()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    try:
        series, dropped = base._series(con, symbol, intervals)
    finally:
        con.close()
    parts = []
    for interval, rows in series.items():
        grouped: dict[int, list[tuple]] = {}
        for row in rows:
            grouped.setdefault(_year(int(row[0])), []).append(row)
        for year, group in grouped.items():
            parts.append(_write_partition(root, symbol, interval, year, group))
    return {"symbol": symbol, "partitions": parts, "dropped": dropped,
            "elapsed_seconds": round(time.time() - started, 3)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--intervals", default="1m,3m,5m,15m,30m,1h,1d")
    ap.add_argument("--symbols", default=""); ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--heartbeat", default="")
    a = ap.parse_args(); db = str(Path(a.db).resolve()); out = str(Path(a.out).resolve())
    intervals = [x.strip() for x in a.intervals.split(",") if x.strip()]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    requested = [x.strip() for x in a.symbols.split(",") if x.strip()]
    if requested:
        # Explicit Batch tasks must never enumerate the 71M-row source just
        # to rediscover their input.  Primary-key probes are logarithmic.
        symbols = [symbol for symbol in requested if con.execute(
            "SELECT 1 FROM bars WHERE symbol=? LIMIT 1", (symbol,)).fetchone()]
    else:
        symbols = [r[0] for r in con.execute(
            "SELECT symbol FROM bars GROUP BY symbol ORDER BY symbol")]
    con.close()
    heartbeat = Path(a.heartbeat) if a.heartbeat else Path(out) / "export-heartbeat.json"
    Path(out).mkdir(parents=True, exist_ok=True); completed = []; failures = []
    started = time.time(); workers = max(1, min(a.workers, len(symbols)))
    state_lock = threading.Lock(); stop_ping = threading.Event()

    def ping_loop():
        while not stop_ping.is_set():
            with state_lock:
                state = {"phase": "parquet_export", "status": "running",
                         "updated_epoch": time.time(), "completed": len(completed),
                         "total": len(symbols), "failed": len(failures),
                         "elapsed_seconds": round(time.time() - started, 1),
                         "message": "workers active"}
            heartbeat.write_text(json.dumps(state, sort_keys=True) + "\n")
            stop_ping.wait(30)

    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    ping_thread = threading.Thread(target=ping_loop, name="export-heartbeat", daemon=True)
    ping_thread.start()

    def record(symbol, future=None):
            nonlocal completed, failures
            try:
                result = (future.result() if future else
                          export_symbol(db, out, symbol, intervals))
                with state_lock: completed.append(result)
            except Exception as exc:  # one partition must not erase the run
                with state_lock: failures.append({"symbol": symbol, "error": repr(exc)})
            state = {"phase": "parquet_export", "status": "running",
                     "updated_epoch": time.time(), "completed": len(completed),
                     "total": len(symbols), "failed": len(failures),
                     "elapsed_seconds": round(time.time() - started, 1),
                     "last_symbol": symbol}
            heartbeat.write_text(json.dumps(state, sort_keys=True) + "\n")
            print(json.dumps(state), flush=True)
    if workers == 1:
        for symbol in symbols:
            record(symbol)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            jobs = {pool.submit(export_symbol, db, out, s, intervals): s for s in symbols}
            for future in as_completed(jobs):
                record(jobs[future], future)
    stop_ping.set(); ping_thread.join(timeout=2)
    manifest = {"schema_version": SCHEMA_VERSION, "source_db": db,
                "intervals": intervals, "symbols": len(symbols),
                "completed": completed, "failures": failures,
                "elapsed_seconds": round(time.time() - started, 2)}
    (Path(out) / "dataset-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    heartbeat.write_text(json.dumps({"phase": "parquet_export",
        "status": "failed" if failures else "complete", "updated_epoch": time.time(),
        "completed": len(completed), "total": len(symbols),
        "failed": len(failures)}, sort_keys=True) + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
