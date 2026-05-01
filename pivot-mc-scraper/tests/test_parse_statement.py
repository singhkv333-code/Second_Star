from datetime import date
from pathlib import Path

from mc_scraper.parse.statement import iter_cells, parse_statement_html

FIX = Path(__file__).parent / "fixtures"


def test_balance_sheet_basic_shape():
    html = (FIX / "RI_balance_sheet_p1.html").read_text()
    stmt = parse_statement_html(html)
    assert stmt is not None
    assert len(stmt.periods) == 5
    # The current MC RI page reports Mar 22 .. Mar 26.
    labels = [p.label for p in stmt.periods]
    assert any("Mar" in l and "26" in l for l in labels)
    assert any("Mar" in l and "22" in l for l in labels)
    # Annual statements get period_kind=annual when parseable.
    assert all(p.period_kind == "annual" for p in stmt.periods if p.period_end)
    # Mar 24 → 2024-03-31
    mar24 = next(p for p in stmt.periods if "24" in p.label and "Mar" in p.label)
    assert mar24.period_end == date(2024, 3, 31)


def test_balance_sheet_sections_and_lines():
    html = (FIX / "RI_balance_sheet_p1.html").read_text()
    stmt = parse_statement_html(html)
    # Section headers themselves should be present.
    headers = {l.line_item for l in stmt.lines if l.is_section_header}
    assert "EQUITIES AND LIABILITIES" in headers
    assert "SHAREHOLDER'S FUNDS" in headers
    # Equity Share Capital is a leaf line under the closest sub-section.
    eq = next(l for l in stmt.lines if l.line_item == "Equity Share Capital")
    assert eq.section == "SHAREHOLDER'S FUNDS"
    # First numeric column should parse cleanly.
    first_text = eq.values[0]
    assert first_text != ""


def test_iter_cells_yields_numeric():
    html = (FIX / "RI_balance_sheet_p1.html").read_text()
    stmt = parse_statement_html(html)
    rows = list(iter_cells(stmt))
    assert rows, "expected at least one cell"
    # At least one (Equity Share Capital, Mar 24) row with a positive numeric.
    found = [
        (line.line_item, num)
        for _, period, line, _, num in rows
        if line.line_item == "Equity Share Capital" and num is not None and num > 0
    ]
    assert found
