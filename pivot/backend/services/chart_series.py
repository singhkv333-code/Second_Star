"""Charts drawn from the data a tool already computed its answer from.

A tool attaches them under ``_charts`` (a list). The flex loop gives each an id
and tells the model it may place it inline by writing ``[[chart:<id>]]`` on its
own line; the reply renders the chart there. The model never sees the points
(``ToolResult`` strips the key), only each chart's id and title, and it decides
whether a chart earns a place and where.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Iterable

MAX_POINTS = 260
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _num(v: Any) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _thin(points: list[dict]) -> list[dict]:
    """Every k-th point, always keeping the last: shape survives, bytes don't."""
    if len(points) <= MAX_POINTS:
        return points
    step = math.ceil(len(points) / MAX_POINTS)
    out = points[::step]
    if out[-1] is not points[-1]:
        out.append(points[-1])
    return out


def _as_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def period_label(d: date, quarterly: bool = False) -> str:
    """FY26 for a March year-end, Q1 FY27 for June 2026, else Dec 25."""
    if quarterly:
        fy = d.year + (1 if d.month > 3 else 0)
        return f"Q{((d.month - 4) % 12) // 3 + 1} FY{fy % 100:02d}"
    if d.month == 3:
        return f"FY{d.year % 100:02d}"
    return f"{_MONTHS[d.month - 1]} {d.year % 100:02d}"


def series_chart(
    title: str,
    series: Iterable[tuple[str, Iterable[tuple[Any, Any]]]],
    *,
    unit: str = "inr",
    normalize: bool = True,
) -> dict | None:
    """A line per (label, [(date, value), ...]), dates ascending. ``normalize``
    plots % change from each line's first point (prices on different scales)."""
    lines = []
    for label, pts in series:
        points = [{"t": str(t)[:10], "v": round(v, 4)}
                  for t, v in ((t, _num(v)) for t, v in pts) if v is not None]
        if len(points) >= 2:
            lines.append({"symbol": str(label), "points": _thin(points)})
    if not lines:
        return None
    return {"_render_hint": "series_chart_card", "title": title, "unit": unit,
            "normalize": normalize, "series": lines[:6]}


def bar_chart(
    title: str,
    series: Iterable[tuple[str, Iterable[tuple[Any, Any]]]],
    *,
    unit: str = "",
    quarterly: bool = False,
    max_periods: int = 12,
) -> dict | None:
    """Vertical bars of a reported figure over periods, one colour per company.

    Periods are aligned across companies by period end, so a company missing
    a year shows a gap rather than a shifted bar. None under three periods.
    """
    by_sym: list[tuple[str, dict[date, float]]] = []
    for label, pts in series:
        vals = {}
        for t, v in pts:
            d, v = _as_date(t), _num(v)
            if d is not None and v is not None:
                vals[d] = v
        if vals:
            by_sym.append((str(label), vals))
    periods = sorted({d for _, vals in by_sym for d in vals})[-max_periods:]
    if len(periods) < 3:
        return None
    return {
        "_render_hint": "financial_bars",
        "title": title,
        "unit": unit,
        "labels": [period_label(d, quarterly) for d in periods],
        "series": [{"symbol": s, "values": [vals.get(d) for d in periods]}
                   for s, vals in by_sym[:4]],
    }
