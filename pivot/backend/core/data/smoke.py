"""Smoke test for backend.core.data module.

Run with: python -m backend.core.data.smoke
From the pivot/ directory.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Fetch RELIANCE close prices and print summary."""
    print("Smoke test: backend.core.data")
    print("-" * 40)

    try:
        from backend.core.data import get_close_series, get_ohlcv
    except ImportError as e:
        print(f"Import error: {e}")
        return 1

    symbol = "RELIANCE"
    period = "1mo"

    print(f"Fetching {symbol} close prices ({period})...")

    try:
        series = get_close_series(symbol, period=period)
    except Exception as e:
        print(f"Error fetching data: {e}")
        return 1

    if series.empty:
        print("No data returned.")
        return 1

    print(f"\nDate range: {series.index[0].date()} to {series.index[-1].date()}")
    print(f"Total data points: {len(series)}")
    print(f"\nLast 5 closing prices:")
    print("-" * 30)

    for date, price in series.tail(5).items():
        print(f"  {date.date()}  Rs {price:,.2f}")

    print("-" * 30)
    print("\nSmoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
