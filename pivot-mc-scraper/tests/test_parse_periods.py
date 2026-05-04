from datetime import date

from mc_scraper.parse.periods import parse_numeric, parse_period


def test_month_year_variants():
    for label in ["Mar 24", "Mar-24", "Mar'24", " MAR  2024 "]:
        p = parse_period(label)
        assert p.period_end == date(2024, 3, 31), label
        assert p.period_kind == "annual"


def test_yyyymm():
    p = parse_period("202412")
    assert p.period_end == date(2024, 12, 31)
    assert p.period_kind == "annual"


def test_quarterly_hint():
    p = parse_period("Dec 23", statement_kind="quarterly")
    assert p.period_end == date(2023, 12, 31)
    assert p.period_kind == "quarter"


def test_duration_token():
    p = parse_period("12 mths")
    assert p.period_end is None
    assert p.period_kind == "12M"

    p = parse_period("9 Months")
    assert p.period_kind == "9M"


def test_unparseable_keeps_label():
    p = parse_period("FY26-Provisional")
    assert p.period_end is None
    assert p.label == "FY26-Provisional"


def test_two_digit_year_window():
    assert parse_period("Mar 79").period_end == date(2079, 3, 31)
    assert parse_period("Mar 80").period_end == date(1980, 3, 31)


def test_parse_numeric():
    assert parse_numeric("1,430.80") == 1430.80
    assert parse_numeric("(123.45)") == -123.45
    assert parse_numeric("--") is None
    assert parse_numeric("") is None
    assert parse_numeric(None) is None
    assert parse_numeric(" 0 ") == 0.0
