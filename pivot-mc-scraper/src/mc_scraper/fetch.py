"""URL builders + paginated fetch loop for Moneycontrol financial statement pages."""
from __future__ import annotations

import asyncio
import gzip
import random
from dataclasses import dataclass
from typing import List, Optional, Tuple

import httpx

from .http import fetch
from .parse.quarterly import looks_like_redirect_shell, parse_quarterly_html
from .parse.statement import ParsedStatement, parse_statement_html


_VI_SLUG = {
    "balance_sheet": "balance-sheetVI",
    "profit_loss": "profit-lossVI",
    "cash_flow": "cash-flowVI",
    "ratios": "ratiosVI",
}


def build_url(company_slug: str, sc_id: str, statement: str, basis: str, page: int = 1) -> str:
    if statement == "quarterly_results":
        seg = "quarterly-results" if basis == "standalone" else "consolidated-quarterly-results"
        base = f"https://www.moneycontrol.com/financials/{company_slug}/results/{seg}/{sc_id}"
    else:
        slug = _VI_SLUG[statement]
        if basis == "consolidated":
            slug = f"consolidated-{slug}"
        base = f"https://www.moneycontrol.com/financials/{company_slug}/{slug}/{sc_id}"
    return base if page <= 1 else f"{base}/{page}"


@dataclass
class FetchedPage:
    page_no: int
    url: str
    http_status: int
    html: str
    parsed: Optional[ParsedStatement]


async def fetch_all_pages(
    client: httpx.AsyncClient,
    *,
    company_slug: str,
    sc_id: str,
    statement: str,
    basis: str,
    rate_limiter=None,
    max_pages: int = 20,
) -> Tuple[List[FetchedPage], str]:
    """Walk pagination until empty/404/loop. Returns (pages, status_code).

    `status_code` is one of: 'ok', 'no_data', 'js_only'. The latter is for
    quarterly pages that come back as the Next.js shell.
    """
    pages: List[FetchedPage] = []
    seen_period_keys: set[tuple] = set()

    for page_no in range(1, max_pages + 1):
        url = build_url(company_slug, sc_id, statement, basis, page_no)

        if rate_limiter is not None:
            await rate_limiter.acquire()

        resp = await fetch(client, url, allow_404=True)
        if resp is None:
            break
        if resp.status_code == 404:
            break
        # MC sometimes redirects unknown statements to the company home page.
        if resp.url.path.rstrip("/").endswith(sc_id) and "/financials/" not in str(resp.url):
            break

        html = resp.text
        if statement == "quarterly_results":
            if looks_like_redirect_shell(html):
                # We can't get data this way; bail and let caller mark no_data.
                if not pages:
                    return [], "js_only"
                break
            parsed = parse_quarterly_html(html)
        else:
            parsed = parse_statement_html(html, statement_kind="annual")

        if parsed is None or not parsed.periods:
            break

        period_key = tuple(p.label for p in parsed.periods)
        if period_key in seen_period_keys:
            break  # loop guard: same window as last page
        seen_period_keys.add(period_key)

        pages.append(
            FetchedPage(
                page_no=page_no,
                url=str(resp.url),
                http_status=resp.status_code,
                html=html,
                parsed=parsed,
            )
        )

    if not pages:
        return [], "no_data"
    return pages, "ok"


def gzip_html(html: str) -> bytes:
    return gzip.compress(html.encode("utf-8"), compresslevel=6)
