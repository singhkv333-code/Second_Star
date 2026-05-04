"""Parse Moneycontrol balance-sheet / P&L / cash-flow / ratios HTML tables.

Page structure (verified Apr-2026):
    <table class="mctable1">
      row 0: [<title>, period_1, period_2, period_3, period_4, period_5, '']
      row 1: ['', '12 mths', '12 mths', ...]   # duration row, may be absent
      row 2..N:
        section headers: only cell[0] non-empty (e.g. 'EQUITIES AND LIABILITIES')
        line items:      cell[0] label, cells[1..5] numeric, cell[6] empty
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from selectolax.parser import HTMLParser

from .periods import ParsedPeriod, parse_numeric, parse_period


PERIOD_RE = re.compile(
    r"^\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*[\-'\s]*\d{2,4}\s*$",
    re.IGNORECASE,
)
DURATION_RE = re.compile(r"^\s*\d{1,2}\s*m(?:ths?|onths?)\s*$", re.IGNORECASE)


@dataclass
class StatementLine:
    section: Optional[str]
    line_item: str
    line_order: int
    values: List[str]            # raw cell text per period
    is_section_header: bool = False


@dataclass
class ParsedStatement:
    periods: List[ParsedPeriod]
    durations: List[Optional[str]] = field(default_factory=list)
    unit: str = "Rs. Cr"
    lines: List[StatementLine] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.periods)


def _row_cells(row) -> List[str]:
    return [c.text(strip=True) for c in row.css("td, th")]


def _strip_trailing_empty(cells: List[str]) -> List[str]:
    while cells and cells[-1] == "":
        cells.pop()
    return cells


def _find_unit(tree: HTMLParser) -> str:
    # MC marks the unit somewhere near the table heading, e.g. "( in Rs. Cr.)" or "Rs Cr".
    for node in tree.css("p, div, td, span, em, h2, h3"):
        txt = node.text(strip=True)
        if not txt:
            continue
        m = re.search(r"\(?\s*(?:in\s+)?(Rs\.?\s*(?:Cr|Lakh|Million|Mn|Bn)\.?)\s*\)?", txt, re.I)
        if m:
            return m.group(1).strip()
    return "Rs. Cr"


def _is_period_header(cells: List[str]) -> bool:
    """True if at least 2 cells past the label match a period pattern."""
    hits = sum(1 for c in cells[1:] if PERIOD_RE.match(c))
    return hits >= 2


def parse_statement_html(
    html: str,
    *,
    statement_kind: str = "annual",
) -> Optional[ParsedStatement]:
    """Return ParsedStatement or None if the page has no recognizable data table."""
    tree = HTMLParser(html)
    table = _select_data_table(tree)
    if table is None:
        return None

    rows = table.css("tr")
    if not rows:
        return None

    # Header row: find the first row whose cells past index 0 contain period labels.
    header_idx = -1
    for i, row in enumerate(rows[:6]):
        cells = _row_cells(row)
        if _is_period_header(cells):
            header_idx = i
            break
    if header_idx < 0:
        return None

    header_cells = _strip_trailing_empty(_row_cells(rows[header_idx]))
    period_strs = header_cells[1:]
    periods = [parse_period(p, statement_kind=statement_kind) for p in period_strs]

    # Optional duration row right below the header.
    durations: List[Optional[str]] = [None] * len(periods)
    body_start = header_idx + 1
    if body_start < len(rows):
        cand = _strip_trailing_empty(_row_cells(rows[body_start]))
        if cand and cand[0] == "" and any(DURATION_RE.match(c) for c in cand[1:]):
            durations = [c if DURATION_RE.match(c or "") else None for c in cand[1:]]
            # Pad/truncate to the period count.
            durations = (durations + [None] * len(periods))[: len(periods)]
            body_start += 1

    section: Optional[str] = None
    out_lines: List[StatementLine] = []
    order = 0
    for row in rows[body_start:]:
        cells = _row_cells(row)
        if not cells:
            continue
        # Drop the trailing decoration cell only if it's empty and we're 1 longer than expected.
        if len(cells) > len(periods) + 1 and cells[-1] == "":
            cells = cells[:-1]
        label = cells[0].strip()
        rest = cells[1 : 1 + len(periods)]
        # Pad short rows with empties.
        if len(rest) < len(periods):
            rest = rest + [""] * (len(periods) - len(rest))

        if not label and all(v == "" for v in rest):
            continue  # blank row

        is_section = bool(label) and all(v == "" for v in rest)
        if is_section:
            section = label
            out_lines.append(
                StatementLine(
                    section=None,
                    line_item=label,
                    line_order=order,
                    values=rest,
                    is_section_header=True,
                )
            )
            order += 1
            continue

        out_lines.append(
            StatementLine(
                section=section,
                line_item=label,
                line_order=order,
                values=rest,
                is_section_header=False,
            )
        )
        order += 1

    return ParsedStatement(
        periods=periods,
        durations=durations,
        unit=_find_unit(tree),
        lines=out_lines,
    )


def _select_data_table(tree: HTMLParser):
    """Return the financial data table.

    Strategy:
      1. Prefer table.mctable1 (current MC class).
      2. Fall back to the largest table containing >=2 period-style headers.
    """
    candidates = tree.css("table.mctable1")
    if candidates:
        return _largest_table(candidates)

    fallback: list = []
    for t in tree.css("table"):
        rows = t.css("tr")
        if len(rows) < 5:
            continue
        # Look at the first 5 rows for a period-style header anywhere.
        for row in rows[:5]:
            cells = _row_cells(row)
            if _is_period_header(cells):
                fallback.append((len(rows), t))
                break
    if not fallback:
        return None
    fallback.sort(key=lambda x: x[0], reverse=True)
    return fallback[0][1]


def _largest_table(tables) -> object:
    return max(tables, key=lambda t: len(t.css("tr")))


def iter_cells(stmt: ParsedStatement):
    """Yield (period_idx, period, line, raw_text, numeric) tuples for non-header rows."""
    for line in stmt.lines:
        if line.is_section_header:
            continue
        for idx, (period, raw) in enumerate(zip(stmt.periods, line.values)):
            yield idx, period, line, raw, parse_numeric(raw)
