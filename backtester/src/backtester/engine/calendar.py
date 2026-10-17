"""Trading-day arithmetic.

For v1 we use a Mon–Fri "business day" calendar. We don't bother with NSE
holidays — the impact on a multi-year backtest is small, and the price
table itself only contains real trading days, so missing prices fall through
to the last-available close.
"""
from __future__ import annotations

from datetime import date, timedelta


def is_business_day(d: date) -> bool:
    return d.weekday() < 5


def next_business_day(d: date) -> date:
    while not is_business_day(d):
        d = d + timedelta(days=1)
    return d


def rebalance_dates(start: date, end: date, freq: str) -> list[date]:
    """Generate rebalance dates inclusive of start, capped at end.

    freq:
      D — every business day
      W — every Monday (or next business day)
      M — first business day of each month
      Q — first business day of Jan/Apr/Jul/Oct
      Y — first business day of Jan
    """
    freq = freq.upper()
    if freq not in {"D", "W", "M", "Q", "Y"}:
        raise ValueError(f"unknown frequency: {freq}")

    out: list[date] = []
    if freq == "D":
        d = next_business_day(start)
        while d <= end:
            out.append(d)
            d = next_business_day(d + timedelta(days=1))
        return out

    if freq == "W":
        # Mondays.
        d = start
        while d.weekday() != 0:
            d = d + timedelta(days=1)
        while d <= end:
            out.append(next_business_day(d))
            d = d + timedelta(days=7)
        return out

    if freq == "M":
        d = date(start.year, start.month, 1)
        while d <= end:
            bd = next_business_day(d)
            if bd >= start and bd <= end:
                out.append(bd)
            # Next month
            if d.month == 12:
                d = date(d.year + 1, 1, 1)
            else:
                d = date(d.year, d.month + 1, 1)
        return out

    if freq == "Q":
        for y in range(start.year, end.year + 1):
            for m in (1, 4, 7, 10):
                d = date(y, m, 1)
                if start <= d <= end:
                    out.append(next_business_day(d))
                elif d <= start:
                    bd = next_business_day(d)
                    if bd >= start:
                        out.append(bd)
        # Always include the start as the first rebalance if it isn't already.
        if not out or out[0] > start:
            out.insert(0, next_business_day(start))
        return sorted(set(out))

    # Y
    for y in range(start.year, end.year + 1):
        d = date(y, 1, 1)
        bd = next_business_day(d)
        if start <= bd <= end:
            out.append(bd)
    if not out or out[0] > start:
        out.insert(0, next_business_day(start))
    return sorted(set(out))
