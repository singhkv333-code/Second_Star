"""Parse Moneycontrol period labels and numeric cells."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional


_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}

# Matches Mar-24, Mar 24, Mar'24, MAR-2024, etc.
_MONTH_YEAR = re.compile(
    r"^\s*(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*[\-'\s]*\s*(?P<yr>\d{2,4})\s*$",
    re.IGNORECASE,
)
# YYYYMM, e.g. 202403
_YYYYMM = re.compile(r"^\s*(?P<yr>\d{4})(?P<mon>0[1-9]|1[0-2])\s*$")
# Duration tokens like "12 mths" / "9 mths".
_DURATION = re.compile(r"^\s*(?P<n>\d{1,2})\s*m(?:ths?|onths?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedPeriod:
    label: str
    period_end: Optional[date]
    period_kind: Optional[str]


def _last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _expand_year(yr: str) -> int:
    n = int(yr)
    if len(yr) == 2:
        # Moneycontrol pages cover 2000–present, so 00-79 → 2000s, 80-99 → 1900s.
        return 2000 + n if n < 80 else 1900 + n
    return n


def parse_period(label: str, *, statement_kind: str = "annual") -> ParsedPeriod:
    """Best-effort parse of a column header like 'Mar 24'.

    `statement_kind` is a hint: 'quarterly' biases period_kind toward quarter.
    Never raises — falls back to (None, None) on failure.
    """
    raw = (label or "").strip()
    if not raw:
        return ParsedPeriod(label=raw, period_end=None, period_kind=None)

    m = _MONTH_YEAR.match(raw)
    if m:
        month = _MONTHS[m.group("mon").lower()]
        year = _expand_year(m.group("yr"))
        kind = "quarter" if statement_kind == "quarterly" else "annual"
        return ParsedPeriod(label=raw, period_end=_last_day(year, month), period_kind=kind)

    m = _YYYYMM.match(raw)
    if m:
        year = int(m.group("yr"))
        month = int(m.group("mon"))
        kind = "quarter" if statement_kind == "quarterly" else "annual"
        return ParsedPeriod(label=raw, period_end=_last_day(year, month), period_kind=kind)

    m = _DURATION.match(raw)
    if m:
        n = int(m.group("n"))
        return ParsedPeriod(label=raw, period_end=None, period_kind=f"{n}M")

    return ParsedPeriod(label=raw, period_end=None, period_kind=None)


_NUM_CLEAN = re.compile(r"[,\s]")


def parse_numeric(text: str) -> Optional[float]:
    """'1,430.80' → 1430.80, '(123.45)' → -123.45, '--'/''→ None."""
    if text is None:
        return None
    s = text.strip()
    if s in {"", "-", "--", "—", "N.A.", "NA", "n/a"}:
        return None
    sign = 1.0
    if s.startswith("(") and s.endswith(")"):
        sign = -1.0
        s = s[1:-1]
    s = _NUM_CLEAN.sub("", s)
    if not s:
        return None
    try:
        return sign * float(s)
    except ValueError:
        return None
