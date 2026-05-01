"""Moneycontrol JSON appfeeds source.

Probes ``appfeeds.moneycontrol.com/jsonapi/stocks/<endpoint>`` for every
statement-type × basis combination and parses the JSON shape documented by
the user: each array element is one period, with reserved keys ``yrc``
(period label) and ``yrc0`` (duration); every other key is a line item.

As of probe runs in April 2026 the endpoint host responds with valid JSON
shells (``{"data": "", "firstcol": "", "count": 1}``) but with empty data
for every parameter combination tested without an internal API key. The
probe report records this verbatim so future runs can detect re-opening of
the endpoint without code changes; the parser is wired and ready.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

import asyncpg
import httpx

from ..parse.periods import parse_numeric, parse_period
from ..parse.statement import ParsedStatement, StatementLine


HOST = "https://appfeeds.moneycontrol.com"

# (statement, basis) → list of (endpoint_path, extra_query) tuples to try in order.
# Probed Apr-2026: the *_responsive endpoints exist but return the
# {"data":"","firstcol":"","count":1} stub for every parameter combination
# tried (likely auth-gated for the MC mobile app). The non-_responsive
# ``/jsonapi/stocks/{balance_sheet,ratios}`` paths return real data with the
# {"company_data": {balancesheet|ratios: [...]}} shape parsed below.
# Note: the type=S/type=C parameter is currently ignored by the host (S and C
# return byte-identical responses), so these endpoints effectively cover one
# basis. We keep both registry entries so persistence still tags both jobs.
ENDPOINT_REGISTRY: Dict[tuple, List[Tuple[str, Dict[str, str]]]] = {
    # NOTE: balance_sheet and ratios appfeeds endpoints DISABLED (May 2026).
    # Reason: the appfeeds host ignores the type=S/type=C parameter and returns
    # byte-identical responses for both — meaning the "consolidated" basis was
    # silently being populated with standalone values. The HTML path correctly
    # distinguishes the two and gives ~22 years of history (vs appfeeds' 5).
    # Quarterly variants are kept here even though they currently come back
    # empty — wired so a future endpoint flip auto-detects.
    ("profit_loss", "standalone"): [
        ("/jsonapi/stocks/profit_loss", {"type": "S"}),
        ("/jsonapi/stocks/profit_loss_responsive", {"type": "S"}),
        ("/jsonapi/stocks/profitnloss_responsive", {"type": "S"}),
    ],
    ("profit_loss", "consolidated"): [
        ("/jsonapi/stocks/profit_loss", {"type": "C"}),
        ("/jsonapi/stocks/profit_loss_responsive", {"type": "C"}),
    ],
    ("cash_flow", "standalone"): [
        ("/jsonapi/stocks/cash_flow", {"type": "S"}),
        ("/jsonapi/stocks/cash_flow_responsive", {"type": "S"}),
        ("/jsonapi/stocks/cashflow_responsive", {"type": "S"}),
    ],
    ("cash_flow", "consolidated"): [
        ("/jsonapi/stocks/cash_flow", {"type": "C"}),
        ("/jsonapi/stocks/cash_flow_responsive", {"type": "C"}),
    ],
    ("quarterly_results", "standalone"): [
        ("/jsonapi/stocks/quarterly_results", {"type": "S"}),
        ("/jsonapi/stocks/quarterly_results_responsive", {"type": "S"}),
    ],
    ("quarterly_results", "consolidated"): [
        ("/jsonapi/stocks/quarterly_results", {"type": "C"}),
        ("/jsonapi/stocks/quarterly_results_responsive", {"type": "C"}),
        ("/jsonapi/stocks/cons_quarterly_results_responsive", {"type": "C"}),
    ],
}


# Inner payload key per statement type for the {company_data: {<key>: [...]}}
# shape. Detected empirically on RI; configurable for future endpoints.
INNER_KEY = {
    "balance_sheet": ("balancesheet",),
    "profit_loss": ("profitloss", "profitnloss", "profit_loss"),
    "cash_flow": ("cashflow", "cash_flow"),
    "ratios": ("ratios", "keyratios"),
    "quarterly_results": ("quarterly", "quarterly_results", "quarterlyresults"),
}


_MONTH_NUM = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _yrc_from_year_month(year: Any, month: Any) -> str:
    try:
        y = int(str(year))
    except (TypeError, ValueError):
        return ""
    m_raw = str(month or "").strip()
    if not m_raw:
        return ""
    m_short = m_raw[:3].title() if m_raw.isalpha() else m_raw
    return f"{m_short} {y % 100:02d}"


@dataclass
class ProbeResult:
    statement: str
    basis: str
    endpoint: str
    url: str
    http_status: int
    is_json: bool
    has_data: bool
    sample: str = ""
    parsed: Optional[ParsedStatement] = None


def build_url(scid: str, path: str, extra: Dict[str, str]) -> str:
    qs = {"scid": scid, **extra}
    query = "&".join(f"{k}={v}" for k, v in qs.items())
    return f"{HOST}{path}?{query}"


def _looks_empty(payload: Any) -> bool:
    """The host's empty-response shell: {"data": "", "firstcol": "", "count": 1}.
    Only treats the response as empty when the explicit empty-shell shape is
    present; missing keys are NOT empty (the working shape uses ``company_data``).
    """
    if payload in ([], None, ""):
        return True
    if isinstance(payload, dict):
        if "data" in payload and "firstcol" in payload and "count" in payload:
            data = payload.get("data")
            if data in ("", None, [], {}):
                return True
    return False


def _extract_period_array(
    payload: Any, statement: str
) -> Optional[List[dict]]:
    """Find the array of period objects inside the wrapper.

    Two shapes supported:
      A) {"company_data": {"<key>": [{year, month, type, item:[...]}, ...]}}  (current)
      B) [{"yrc": "...", "yrc0": "...", "<line_item>": "...", ...}]           (documented)
    """
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload
    if isinstance(payload, dict):
        # Shape A: nested under company_data.
        cd = payload.get("company_data")
        if isinstance(cd, dict):
            for key in INNER_KEY.get(statement, ()):
                v = cd.get(key)
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
            # Last resort: return the first list-of-dicts found.
            for v in cd.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
        # Generic wrappers.
        for key in ("data", "rows", "result", "results"):
            v = payload.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
            if isinstance(v, dict):
                inner = _extract_period_array(v, statement)
                if inner is not None:
                    return inner
    return None


_RESERVED_KEYS = {"yrc", "yrc0", "duration", "endDate", "PeriodEnd", "Period"}


def _period_label_from_row(row: dict) -> Optional[str]:
    """Each period row may carry either ``yrc`` (documented) or ``year``+``month``
    (current shape). Returns a Mar-style label."""
    label = str(row.get("yrc") or "").strip()
    if label:
        return label
    if "year" in row and "month" in row:
        out = _yrc_from_year_month(row["year"], row["month"])
        return out or None
    return None


def parse_appfeeds_payload(
    payload: Any,
    *,
    statement: str,
    statement_kind: str = "annual",
) -> Optional[ParsedStatement]:
    """Build a ParsedStatement from either the {company_data:{...:[items]}}
    shape or the documented yrc-keyed shape. Treats every key/item-name except
    yrc/yrc0 as a line item, per spec."""
    rows = _extract_period_array(payload, statement)
    if not rows:
        return None

    # Detect which shape we have by looking at the first row.
    first = rows[0]
    has_nested_items = isinstance(first.get("item"), list)

    # Build period list (one entry per row).
    periods = []
    durations: List[Optional[str]] = []
    for r in rows:
        label = _period_label_from_row(r)
        if not label:
            return None
        periods.append(parse_period(label, statement_kind=statement_kind))
        dur = r.get("yrc0")
        durations.append(str(dur).strip() if dur not in (None, "") else None)

    statement_lines: List[StatementLine] = []
    section: Optional[str] = None

    if has_nested_items:
        # Shape A: each period has an ``item`` array of {name, value, head_flag}.
        # Use the first period's item order as canonical; map subsequent periods
        # by item name.
        canonical = first["item"]
        order = 0
        for it in canonical:
            name = str(it.get("name") or "").strip()
            if not name:
                continue
            head_flag = it.get("head_flag")
            is_header = bool(head_flag) and head_flag != 0 and head_flag != "0"
            if is_header:
                section = name
                statement_lines.append(
                    StatementLine(
                        section=None, line_item=name, line_order=order,
                        values=[""] * len(rows), is_section_header=True,
                    )
                )
                order += 1
                continue
            values: List[str] = []
            for r in rows:
                period_value = ""
                for sibling in r.get("item", []):
                    if str(sibling.get("name", "")).strip() == name:
                        period_value = str(sibling.get("value") or "").strip()
                        break
                values.append(period_value)
            statement_lines.append(
                StatementLine(
                    section=section, line_item=name, line_order=order,
                    values=values, is_section_header=False,
                )
            )
            order += 1
    else:
        # Shape B: top-level keys per row are line items (per documented spec).
        line_items: List[str] = []
        seen = set()
        for r in rows:
            for k in r.keys():
                if k in _RESERVED_KEYS:
                    continue
                if k not in seen:
                    seen.add(k)
                    line_items.append(k)
        for order, item in enumerate(line_items):
            is_header_candidate = (
                item.isupper() and all(_is_empty(r.get(item)) for r in rows)
            )
            if is_header_candidate:
                section = item
                statement_lines.append(
                    StatementLine(
                        section=None, line_item=item, line_order=order,
                        values=[""] * len(rows), is_section_header=True,
                    )
                )
                continue
            values = [str(r.get(item, "") or "").strip() for r in rows]
            statement_lines.append(
                StatementLine(
                    section=section, line_item=item, line_order=order,
                    values=values, is_section_header=False,
                )
            )

    if not any(not l.is_section_header for l in statement_lines):
        return None

    return ParsedStatement(
        periods=periods,
        durations=durations,
        unit="Rs. Cr",
        lines=statement_lines,
    )


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    s = str(v).strip()
    return s in {"", "-", "--"}


async def fetch_one(
    client: httpx.AsyncClient,
    scid: str,
    statement: str,
    basis: str,
) -> ProbeResult:
    """Walk the registry for (statement, basis); first non-empty hit wins.
    Always returns a ProbeResult (even on failure) for visibility."""
    last: Optional[ProbeResult] = None
    statement_kind = "quarterly" if statement == "quarterly_results" else "annual"
    for path, extra in ENDPOINT_REGISTRY.get((statement, basis), []):
        url = build_url(scid, path, extra)
        try:
            resp = await client.get(url, timeout=15)
        except httpx.HTTPError as exc:  # noqa: BLE001
            last = ProbeResult(
                statement=statement, basis=basis, endpoint=path,
                url=url, http_status=0, is_json=False, has_data=False,
                sample=f"transport_error: {exc}",
            )
            continue
        body = resp.text
        sample = body[:200]
        try:
            payload = json.loads(body)
            is_json = True
        except json.JSONDecodeError:
            payload = None
            is_json = False
        if not is_json or _looks_empty(payload):
            last = ProbeResult(
                statement=statement, basis=basis, endpoint=path,
                url=url, http_status=resp.status_code,
                is_json=is_json, has_data=False, sample=sample,
            )
            continue
        parsed = parse_appfeeds_payload(
            payload, statement=statement, statement_kind=statement_kind
        )
        if parsed is None or not parsed.periods:
            last = ProbeResult(
                statement=statement, basis=basis, endpoint=path,
                url=url, http_status=resp.status_code,
                is_json=is_json, has_data=False, sample=sample,
            )
            continue
        return ProbeResult(
            statement=statement, basis=basis, endpoint=path,
            url=url, http_status=resp.status_code,
            is_json=True, has_data=True, sample=sample, parsed=parsed,
        )
    return last or ProbeResult(
        statement=statement, basis=basis, endpoint="(none)",
        url="(none)", http_status=0, is_json=False, has_data=False,
        sample="no endpoints registered",
    )


async def probe_all(
    client: httpx.AsyncClient, scid: str, *, save_pool: Optional[asyncpg.Pool] = None
) -> List[ProbeResult]:
    """Probe every (statement, basis) for one company. Optionally persists to
    mc.appfeeds_probe for later inspection."""
    results: List[ProbeResult] = []
    for (stmt, basis) in ENDPOINT_REGISTRY:
        r = await fetch_one(client, scid, stmt, basis)
        results.append(r)

    if save_pool is not None and results:
        async with save_pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO mc.appfeeds_probe
                    (sc_id, endpoint, requested_url, http_status, is_json, has_data, sample)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                [(scid, r.endpoint, r.url, r.http_status, r.is_json, r.has_data,
                  (r.sample or "")[:500]) for r in results],
            )
    return results
