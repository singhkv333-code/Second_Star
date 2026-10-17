"""
backend/utils/time_utils.py

Single source of truth for all time operations in Pivot.
ALL times returned by these functions are IST-aware.
ALL string representations include the literal word "IST".
"""

from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")


def now_ist() -> datetime:
    """Returns the current datetime in IST, timezone-aware."""
    return datetime.now(IST)


def to_ist(dt: datetime) -> datetime:
    """
    Converts any datetime to IST.
    Handles: naive datetimes (assumes UTC), UTC-aware, any other tz.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(IST)


def format_ist(dt: datetime, include_seconds: bool = True) -> str:
    """
    Formats a datetime as a human-readable IST string.
    Always includes the literal "IST" suffix.

    Examples:
        "01 May 2026, 09:15:23 IST"
        "01 May 2026, 09:15 IST"  (include_seconds=False)
    """
    if dt is None:
        return "—"
    dt_ist = to_ist(dt)
    if include_seconds:
        return dt_ist.strftime("%d %b %Y, %H:%M:%S IST")
    return dt_ist.strftime("%d %b %Y, %H:%M IST")


def format_ist_short(dt: datetime) -> str:
    """
    Short format for logs and confirmations.
    Example: "09:15:23 IST"
    """
    if dt is None:
        return "—"
    return to_ist(dt).strftime("%H:%M:%S IST")


def format_ist_date(dt: datetime) -> str:
    """
    Date only format.
    Example: "01 May 2026 IST"
    """
    if dt is None:
        return "—"
    return to_ist(dt).strftime("%d %b %Y IST")


def is_market_open() -> bool:
    """
    Returns True if NSE market is currently open.
    Market hours: 09:15 to 15:30 IST, Monday to Friday.
    Does not account for holidays — use is_trading_day() for that.
    """
    now = now_ist()
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def is_trading_day(dt: datetime = None) -> bool:
    """
    Returns True if the given date is a weekday.
    Pass your own dt or leave None for today IST.
    For full holiday support, extend this to check trading_holidays table.
    """
    check = to_ist(dt) if dt else now_ist()
    return check.weekday() < 5


def next_market_open() -> datetime:
    """
    Returns the next 9:15 AM IST on a trading day.
    Used to schedule SIPs: if today is a holiday or weekend,
    push to next valid trading day.
    """
    candidate = now_ist().replace(hour=9, minute=15, second=0, microsecond=0)
    if now_ist() >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def next_monthly_execution(day_of_month: int) -> datetime:
    """
    Returns the next IST datetime for a monthly SIP.
    If day_of_month has already passed this month, goes to next month.
    Always returns 9:15 AM IST on the target day.
    If the target day falls on a weekend, rolls to next Monday.
    """
    now = now_ist()
    try:
        candidate = now.replace(
            day=day_of_month, hour=9, minute=15,
            second=0, microsecond=0,
        )
    except ValueError:
        import calendar
        last_day = calendar.monthrange(now.year, now.month)[1]
        candidate = now.replace(
            day=last_day, hour=9, minute=15,
            second=0, microsecond=0,
        )

    if candidate <= now:
        if now.month == 12:
            candidate = candidate.replace(year=now.year + 1, month=1)
        else:
            candidate = candidate.replace(month=now.month + 1)

    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)

    return candidate


def next_weekly_execution(day_of_week: int) -> datetime:
    """
    Returns the next IST datetime for a weekly SIP.
    day_of_week: 0=Monday, 4=Friday
    Always returns 9:15 AM IST on the target weekday.
    """
    now = now_ist()
    days_ahead = day_of_week - now.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    candidate = (now + timedelta(days=days_ahead)).replace(
        hour=9, minute=15, second=0, microsecond=0
    )
    return candidate


def next_daily_execution() -> datetime:
    """Returns 9:15 AM IST on the next trading day."""
    return next_market_open()
