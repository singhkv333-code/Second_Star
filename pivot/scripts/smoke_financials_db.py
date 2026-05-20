"""Smoke test for backend.market.financials_db against the live financials DB.

Run from the pivot/ directory:

    python -m scripts.smoke_financials_db

Exits non-zero on first hard failure (missing required calls) so it can be
used in CI later.
"""
from __future__ import annotations

import sys
from datetime import date
from pprint import pprint

from backend.market import financials_db as fdb


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    failures: list[str] = []

    section("Supported fields")
    fields = fdb.list_supported_fields()
    print(f"{len(fields)} fields registered: {fields}")

    section("Symbol resolution")
    for sym in ("RELIANCE", "INFY", "HDFCBANK", "RI", "DEFINITELY_NOT_A_SYMBOL"):
        sc_id = fdb.resolve_symbol(sym)
        print(f"  {sym!r:<28} -> {sc_id!r}")

    section("Company lookup: RELIANCE")
    co = fdb.get_company("RELIANCE")
    if co is None:
        failures.append("get_company('RELIANCE') returned None")
    else:
        pprint(co.to_dict())

    section("Latest fundamentals: RELIANCE (no as_of)")
    for field in ("revenue", "net_profit", "eps_basic", "roe", "debt_to_equity",
                  "current_ratio", "price_to_book", "ev_to_ebitda"):
        v = fdb.get_fundamental("RELIANCE", field)
        if v is None:
            print(f"  {field:<20} = (no data)")
        else:
            print(
                f"  {field:<20} = {v.value_numeric!r:<15} "
                f"period={v.period_label:<10} avail={v.availability_date} "
                f"basis={v.basis} via {v.line_item!r}"
            )

    section("Point-in-time: RELIANCE roe as of 2020-01-01")
    v = fdb.get_fundamental("RELIANCE", "roe", as_of_date=date(2020, 1, 1))
    if v is None:
        print("  no rows available as of 2020-01-01 (DB may not have that vintage)")
    else:
        print(f"  roe={v.value_numeric} period={v.period_label} avail={v.availability_date}")

    section("History: RELIANCE net_profit (last 8)")
    hist = fdb.get_fundamental_history("RELIANCE", "net_profit", limit=8)
    if not hist:
        failures.append("net_profit history empty for RELIANCE")
    for h in hist:
        print(f"  {h.period_end} ({h.period_label}) = {h.value_numeric}")

    section("OHLCV: RELIANCE (first 5 bars if any)")
    bars = fdb.get_ohlcv("RELIANCE")
    print(f"  total bars: {len(bars)}")
    for b in bars[:5]:
        print(f"  {b.trade_date} O={b.open} H={b.high} L={b.low} C={b.close} V={b.volume}")

    section("has_fundamentals checks")
    for sym in ("RELIANCE", "INFY", "DEFINITELY_NOT_A_SYMBOL"):
        print(f"  {sym}: {fdb.has_fundamentals(sym)}")

    section("Raw line_item escape hatch")
    v = fdb.get_line_item("RELIANCE", "Asset Turnover Ratio (%)")
    if v is None:
        print("  no value for 'Asset Turnover Ratio (%)'")
    else:
        print(f"  Asset Turnover Ratio (%) = {v.value_numeric} period={v.period_label}")

    if failures:
        print("\n--- FAILURES ---")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nOK: all smoke checks completed without hard failure")
    return 0


if __name__ == "__main__":
    sys.exit(main())
