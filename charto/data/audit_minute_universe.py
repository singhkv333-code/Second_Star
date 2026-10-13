#!/usr/bin/env python3
"""Read-only import gate. Compare stored rollups to the serving resampler.

Uses bounded, indexed windows, not a full billion-row scan. Reports gaps;
never fabricates or repairs data. Continuous placeholders are reported
separately: land_universe materialises their actual contract segments.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import sqlite3
from pathlib import Path

GROUPS = {"nse_1m": "NSE_EQ", "bse_1m": "BSE_EQ", "idx_nse": "NSE_IDX",
          "idx_bse": "BSE_IDX", "idx_mcx": "MCX_IDX", "fut_nfo": "NFO",
          "fut_bfo": "BFO", "fut_mcx": "MCX", "fut_nco": "NCO", "fut_cds": "CDS"}


def resampler(source: str):
    names = {"_bucket_stamp", "_resample_intraday"}
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if len(functions) != len(names):
        raise ValueError("Missing authoritative resampler functions")
    namespace = {"NSE_SESSION": (555, 19800)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "dataserver-resampler", "exec"), namespace)
    return namespace["_resample_intraday"]


def audit(paths: list[Path], sessions: dict, fold) -> dict:
    report = {"stores": [], "covered": 0, "passed": 0, "failed": [], "continuous_placeholders": []}
    for path in paths:
        db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=10)
        db.execute("PRAGMA query_only=ON")
        store = path.stem
        cols = {r[1] for r in db.execute("PRAGMA table_info(instruments)")}
        kind = "kind" if "kind" in cols else "NULL"
        total = passed = 0
        for symbol, instrument_kind in db.execute(f"SELECT symbol,{kind} FROM instruments").fetchall():
            if not db.execute("SELECT 1 FROM bars WHERE symbol=? LIMIT 1", (symbol,)).fetchone():
                continue
            total += 1
            if instrument_kind == "cont":
                report["continuous_placeholders"].append([store, symbol])
                continue
            reasons = []
            session = sessions.get("symbols", {}).get(symbol) if store in {"idx_global", "idx_nseix"} else None
            session = session or sessions.get("groups", {}).get(GROUPS.get(store))
            if not session:
                reasons.append("missing measured session")
            last = db.execute("SELECT ts FROM bars_15m WHERE symbol=? ORDER BY ts DESC LIMIT 1", (symbol,)).fetchone()
            if not last:
                reasons.append("missing 15-minute rollups")
            if not db.execute("SELECT 1 FROM bars_1d WHERE symbol=? LIMIT 1", (symbol,)).fetchone():
                reasons.append("missing daily bars")
            if not db.execute("SELECT 1 FROM fix_report WHERE symbol=? LIMIT 1", (symbol,)).fetchone():
                reasons.append("missing derivation record")
            if session and last:
                # A fixed wall-clock window misses sparse/illiquid series.
                # Bound by actual observations instead, still using the PK.
                raw = list(reversed(db.execute(
                    "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts<? ORDER BY ts DESC LIMIT 200",
                    (symbol, last[0] + 900)).fetchall()))
                start, end = (raw[0][0], last[0] + 900) if raw else (last[0], last[0] + 900)
                for ts, o, h, l, c, v in raw:
                    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (o, h, l, c, v)) or h < max(o, c, l) or l > min(o, c) or v < 0:
                        reasons.append("invalid sampled OHLCV"); break
                open_min = session["open"]
                clock = (0, 19800 - open_min * 60) if session["wrap"] else (open_min, 19800)
                expected = fold(raw, 15, clock)
                # The bucket stamp can precede its first real minute (a thin
                # instrument may first trade at 09:21 in the 09:15 bucket).
                # Query by bucket address, not by the first observed trade.
                stored_start = expected[0][0] if expected else start
                stored = {r[0]: r for r in db.execute("SELECT ts,o,h,l,c,v FROM bars_15m WHERE symbol=? AND ts>=? AND ts<?",
                                                     (symbol, stored_start, end))}
                # First bucket may straddle the bounded window. Compare all
                # remaining complete inputs, including sparse trading minutes.
                comparable = expected[1:] if len(raw) == 200 else expected
                for row in comparable:
                    got = stored.get(row[0])
                    if got is None or any(not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-7)
                                          for a, b in zip(row[1:], got[1:])):
                        reasons.append("rollup differs from serving resampler"); break
                if not comparable:
                    reasons.append("insufficient sampled minute bars")
            if reasons:
                report["failed"].append({"store": store, "symbol": symbol, "reasons": reasons})
            else:
                passed += 1
        report["stores"].append({"store": store, "minute_series": total, "sample_passed": passed})
        report["covered"] += total; report["passed"] += passed
        db.close()
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--src", type=Path, nargs="+", required=True)
    p.add_argument("--sessions", type=Path, required=True)
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    paths = sorted(path for folder in args.src for path in folder.glob("*.db"))
    result = audit(paths, json.loads(args.sessions.read_text()),
                   resampler(Path(__file__).with_name("dataserver.py").read_text()))
    if args.report:
        args.report.write_text(json.dumps(result))
        from collections import Counter
        print(json.dumps({"covered": result["covered"], "passed": result["passed"],
                          "failure_reasons": dict(Counter(reason for item in result["failed"] for reason in item["reasons"])),
                          "placeholders": len(result["continuous_placeholders"]), "stores": result["stores"]}))
    else:
        print(json.dumps(result))
    return bool(result["failed"])


if __name__ == "__main__":
    raise SystemExit(main())
