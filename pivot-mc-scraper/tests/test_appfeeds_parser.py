import json
from pathlib import Path

from mc_scraper.sources.appfeeds import parse_appfeeds_payload

FIX = Path(__file__).parent / "fixtures"


def test_parses_balance_sheet_real_shape():
    payload = json.loads((FIX / "RI_appfeeds_bs.json").read_text())
    stmt = parse_appfeeds_payload(payload, statement="balance_sheet")
    assert stmt is not None
    assert len(stmt.periods) >= 3
    # Reliance balance_sheet covers Mar 22..Mar 26
    labels = [p.label for p in stmt.periods]
    assert any("Mar" in l for l in labels)
    # Equity Share Capital must be a leaf line item with numeric values.
    eq = next(
        (l for l in stmt.lines if l.line_item == "Equity Share Capital"), None
    )
    assert eq is not None
    assert eq.values
    assert any(v.replace(",", "").replace(".", "").isdigit() for v in eq.values)


def test_parses_ratios_real_shape():
    payload = json.loads((FIX / "RI_appfeeds_ratios.json").read_text())
    stmt = parse_appfeeds_payload(payload, statement="ratios")
    assert stmt is not None
    assert len(stmt.lines) > 5
    # Should have at least one numeric leaf row.
    numeric_rows = [l for l in stmt.lines if not l.is_section_header and any(l.values)]
    assert numeric_rows
