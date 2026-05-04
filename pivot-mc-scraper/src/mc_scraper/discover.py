"""Discover companies from /india/stockpricequote/<letter> and seed scrape jobs."""
from __future__ import annotations

import asyncio
import re
import string
from dataclasses import dataclass
from typing import List, Optional

import asyncpg
import httpx
from selectolax.parser import HTMLParser

from .http import fetch, make_client


LETTERS = list(string.ascii_uppercase) + ["others"]
_URL_RE = re.compile(
    r"^/india/stockpricequote/(?P<industry>[^/]+)/(?P<slug>[^/]+)/(?P<sc_id>[^/]+)/?$"
)


@dataclass
class DiscoveredCompany:
    sc_id: str
    company_name: str
    company_slug: str
    industry_slug: Optional[str]
    home_url: str


def _parse_listing_page(html: str) -> List[DiscoveredCompany]:
    tree = HTMLParser(html)
    out: List[DiscoveredCompany] = []
    for a in tree.css("a.bl_12"):
        href = a.attributes.get("href") or ""
        # Normalize to a path for regex matching.
        if href.startswith("https://www.moneycontrol.com"):
            path = href[len("https://www.moneycontrol.com") :]
        else:
            path = href
        m = _URL_RE.match(path)
        if not m:
            continue
        name = a.text(strip=True)
        if not name:
            continue
        out.append(
            DiscoveredCompany(
                sc_id=m.group("sc_id"),
                company_name=name,
                company_slug=m.group("slug"),
                industry_slug=m.group("industry"),
                home_url=f"https://www.moneycontrol.com{path}",
            )
        )
    return out


async def fetch_letter(client: httpx.AsyncClient, letter: str) -> List[DiscoveredCompany]:
    url = f"https://www.moneycontrol.com/india/stockpricequote/{letter}"
    resp = await fetch(client, url, allow_404=True)
    if resp is None or resp.status_code != 200:
        return []
    return _parse_listing_page(resp.text)


_STATEMENTS = ["balance_sheet", "profit_loss", "cash_flow", "ratios", "quarterly_results"]
_BASES = ["standalone", "consolidated"]


async def upsert_companies_and_jobs(
    pool: asyncpg.Pool, companies: List[DiscoveredCompany]
) -> tuple[int, int]:
    """Returns (inserted_companies, inserted_jobs)."""
    if not companies:
        return 0, 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Companies upsert.
            await conn.executemany(
                """
                INSERT INTO mc.companies
                    (sc_id, company_name, company_slug, industry_slug, home_url, last_seen_at)
                VALUES ($1, $2, $3, $4, $5, now())
                ON CONFLICT (sc_id) DO UPDATE SET
                    company_name = EXCLUDED.company_name,
                    company_slug = EXCLUDED.company_slug,
                    industry_slug = EXCLUDED.industry_slug,
                    home_url = EXCLUDED.home_url,
                    last_seen_at = now()
                """,
                [
                    (c.sc_id, c.company_name, c.company_slug, c.industry_slug, c.home_url)
                    for c in companies
                ],
            )
            # Jobs: 10 per company (skip rows that already exist).
            job_rows = [
                (c.sc_id, stmt, basis)
                for c in companies
                for stmt in _STATEMENTS
                for basis in _BASES
            ]
            inserted = await conn.fetchval(
                """
                WITH ins AS (
                    INSERT INTO mc.scrape_jobs (sc_id, statement, basis)
                    SELECT * FROM unnest($1::text[], $2::mc.statement_type[], $3::mc.basis[])
                    ON CONFLICT (sc_id, statement, basis) DO NOTHING
                    RETURNING 1
                )
                SELECT count(*)::int FROM ins
                """,
                [r[0] for r in job_rows],
                [r[1] for r in job_rows],
                [r[2] for r in job_rows],
            )
            return len(companies), int(inserted or 0)


async def discover_all(pool: asyncpg.Pool, *, concurrency: int = 4) -> dict:
    """Walk every listing page, upsert companies + jobs. Returns summary dict."""
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, int] = {}

    async with make_client() as client:
        async def _one(letter: str):
            async with sem:
                companies = await fetch_letter(client, letter)
                inserted_c, inserted_j = await upsert_companies_and_jobs(pool, companies)
                results[letter] = len(companies)
                return letter, len(companies), inserted_j

        tasks = [_one(l) for l in LETTERS]
        rows = await asyncio.gather(*tasks)

    total_seen = sum(c for _, c, _ in rows)
    total_jobs = sum(j for _, _, j in rows)
    return {
        "letters": dict((l, c) for l, c, _ in rows),
        "companies_seen": total_seen,
        "jobs_inserted": total_jobs,
    }
