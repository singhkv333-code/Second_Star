"""Quarterly results parser.

Status: as of Apr 2026, Moneycontrol's quarterly results page no longer
server-renders the data table (the legacy URL `/financials/<slug>/results/
quarterly-results/<sc_id>` redirects to a JS-rendered Next.js route, and the
data is loaded client-side). The static-HTTP scraper therefore cannot capture
it without a Selenium/Playwright dependency, which the spec forbids.

This module provides a `looks_empty()` probe so the worker can mark such jobs
as `no_data` cleanly. If/when an HTTP-friendly endpoint is identified, plug
the parser in here.
"""
from __future__ import annotations

from selectolax.parser import HTMLParser

from .statement import ParsedStatement, parse_statement_html


def parse_quarterly_html(html: str) -> ParsedStatement | None:
    """Try the same mctable1 strategy first; many quarterly pages still work for
    legacy / niche tickers. Returns None when the page is the JS-rendered shell."""
    parsed = parse_statement_html(html, statement_kind="quarterly")
    if parsed and parsed.periods:
        return parsed
    return None


def looks_like_redirect_shell(html: str) -> bool:
    """True if the response is the Next.js shell with no usable data."""
    tree = HTMLParser(html)
    has_next_data = bool(tree.css('script#__NEXT_DATA__'))
    has_mctable = bool(tree.css('table.mctable1'))
    return has_next_data and not has_mctable
