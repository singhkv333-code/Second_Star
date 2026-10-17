#!/usr/bin/env python3
"""Read-only Charto concurrency smoke test for deployment verification.

The default ladder mirrors the beta-readiness audit. It never calls chat or
mutates a workspace: every request computes RSI from an existing market-data
series. A run fails on any non-200 response or if p95 crosses the configured
ceiling, and emits one compact JSON document suitable for a deploy log.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction + 0.999) - 1))
    return ordered[index]


def _request(base_url: str, symbol: str, timeout: float) -> dict:
    query = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": "5m",
        "name": "rsi",
        "period": "14",
    })
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/indicator?{query}",
        headers={"Accept": "application/json", "User-Agent": "charto-load-check/1"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        exc.read()
        status = exc.code
    except (OSError, TimeoutError) as exc:
        return {"status": 0, "latency_s": time.perf_counter() - started,
                "error": type(exc).__name__}
    return {"status": status, "latency_s": time.perf_counter() - started}


def _run_level(base_url: str, concurrency: int, symbols: list[str],
               timeout: float) -> dict:
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_request, base_url, symbols[i % len(symbols)], timeout)
                   for i in range(concurrency)]
        results = [future.result() for future in futures]
    wall = time.perf_counter() - started
    latencies = [item["latency_s"] for item in results]
    statuses: dict[str, int] = {}
    for item in results:
        key = str(item["status"])
        statuses[key] = statuses.get(key, 0) + 1
    return {
        "concurrency": concurrency,
        "requests": len(results),
        "statuses": statuses,
        "successes": sum(item["status"] == 200 for item in results),
        "wall_s": round(wall, 3),
        "rps": round(len(results) / wall, 2) if wall else 0.0,
        "latency_s": {
            "median": round(statistics.median(latencies), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
        "errors": [item["error"] for item in results if "error" in item],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://pivot-india.centralindia.cloudapp.azure.com")
    parser.add_argument("--levels", default="1,10,20,40")
    parser.add_argument("--symbols", default="RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-p95", type=float, default=20.0)
    args = parser.parse_args()

    levels = [int(value) for value in args.levels.split(",") if value.strip()]
    symbols = [value.strip().upper() for value in args.symbols.split(",") if value.strip()]
    if not levels or min(levels) < 1 or not symbols:
        parser.error("levels and symbols must be non-empty; levels must be positive")

    reports = [_run_level(args.base_url, level, symbols, args.timeout)
               for level in levels]
    passed = all(
        report["successes"] == report["requests"]
        and report["latency_s"]["p95"] <= args.max_p95
        for report in reports
    )
    print(json.dumps({
        "ok": passed,
        "base_url": args.base_url,
        "max_p95_s": args.max_p95,
        "levels": reports,
    }, separators=(",", ":")))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
