"""P4 — IPO listing-day tracking (post-listing performance card).

What this exercises:
  - ``fetch_past_issues`` performs the cookie-warmed GET against
    ``/api/public-past-issues``, caches the WHOLE list under
    ``ipo_feed:past_issues`` for 6h, and is honest-on-failure
    (None + no cache on unreachable).
  - ``fetch_listed_ipo`` parses a realistic past-issues record:
    ``issuePrice -> issue_price`` (float), ``listingDate -> ISO``,
    ``securityType -> type`` (SME -> 'sme' else 'mainboard').
  - ``fetch_listed_ipo`` is honest on miss (matches[] + note) and
    unreachable (source='unreachable' + note).
  - ``_get_ipo_listing`` (the executor handler) computes the gain
    when BOTH prices are present, surfaces "listing data pending"
    when current is None, "issue price unavailable" when issue is None,
    and NEVER fabricates a number.
  - The 6h past-issues cache key ``ipo_feed:past_issues`` does NOT
    collide with the 45m list key (``ipo_feed:list``) or the 15m
    sub key (``ipo_feed:sub:*``).
  - The tool routes: a "how did X list" / "X listing gain" message
    surfaces ``get_ipo_listing`` via ``select_tool_names``.
  - ``_propose_ipo_application`` returns the ipo_listed_card for a
    listed-but-not-live symbol (the apply flow's graceful fall-through).

The NSE HTTP layer is stubbed via ``monkeypatch`` of
``ipo_feed._warmed_client``; the live-price path is stubbed via
``monkeypatch`` of ``backend.agents.tool_executor._listed_current_price``.
No network. Mirrors the stubbing pattern in test_ipo_feed_enrichment.py.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from backend.agents import tool_executor
from backend.services import ipo_feed
from backend.services.ipo_feed import (
    fetch_listed_ipo,
    fetch_past_issues,
)


# ── Realistic past-issues records (verified shape from
#    /api/public-past-issues — see lead notes). ──────────────────────────

_TIKONA_LISTED: dict[str, Any] = {
    "companyName": "Tikona Infinet Limited",
    "company": "Tikona Infinet Limited",
    "symbol": "TIKONA",
    "issuePrice": "139",
    "listingDate": "29-MAY-2026",
    "ipoStartDate": "23-MAY-2026",
    "ipoEndDate": "27-MAY-2026",
    "priceRange": "Rs.132 to Rs.139",
    "securityType": "Equity",
}

_SME_LISTED: dict[str, Any] = {
    "companyName": "Microcap SME Limited",
    "symbol": "MICROSME",
    "issuePrice": "55",
    "listingDate": "21-MAY-2026",
    "ipoStartDate": "15-MAY-2026",
    "ipoEndDate": "17-MAY-2026",
    "priceRange": "Rs.50 to Rs.55",
    "securityType": "SME",
}

_MISSING_PRICE_LISTED: dict[str, Any] = {
    "companyName": "NoPrice Limited",
    "symbol": "NOPRICE",
    "issuePrice": "",
    "listingDate": "18-MAY-2026",
    "ipoStartDate": "12-MAY-2026",
    "ipoEndDate": "14-MAY-2026",
    "priceRange": "",
    "securityType": "Equity",
}

_PAST_ISSUES_PAYLOAD: list[dict[str, Any]] = [
    _TIKONA_LISTED, _SME_LISTED, _MISSING_PRICE_LISTED,
]


# ── HTTP / context-manager stubs (mirror test_ipo_feed_enrichment) ─────


class _StubResponse:
    _NO_JSON = object()

    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        json_data: Any = _NO_JSON,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.content = text.encode() if text else b""
        self._json = json_data

    def json(self) -> Any:
        if self._json is self._NO_JSON:
            raise ValueError("no json")
        return self._json


class _StubClient:
    """Stand-in for the httpx.Client returned by ``_warmed_client``.

    Acts as a context manager (matches the new @contextmanager shape).
    Routes by exact URL first, then prefix match.
    """

    def __init__(self, routes: dict[str, _StubResponse]) -> None:
        self._routes = routes
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def __enter__(self) -> "_StubClient":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,  # noqa: ARG002
    ) -> _StubResponse:
        self.calls.append((url, params))
        if url in self._routes:
            return self._routes[url]
        for route_url, resp in self._routes.items():
            if url.startswith(route_url):
                return resp
        return _StubResponse(status_code=404, text="")


@pytest.fixture(autouse=True)
def _flush_ipo_feed_cache() -> None:
    """Wipe ipo_feed:* cache entries between tests so cache asserts are
    deterministic. Works for both the MockRedis fallback and real Redis."""
    rc = ipo_feed.redis_client
    store = getattr(rc, "_store", None)
    if isinstance(store, dict):
        for k in [k for k in list(store.keys()) if str(k).startswith("ipo_feed:")]:
            store.pop(k, None)
        return
    try:
        for key in rc.scan_iter(match="ipo_feed:*"):  # type: ignore[attr-defined]
            rc.delete(key)
    except Exception:  # noqa: BLE001
        for key in (
            "ipo_feed:list",
            "ipo_feed:list:raw",
            "ipo_feed:past_issues",
        ):
            try:
                rc.delete(key)
            except Exception:  # noqa: BLE001
                pass


# ── fetch_past_issues ──────────────────────────────────────────────────


def test_fetch_past_issues_parses_records_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reachable 200 + JSON list lands as a list of dicts and is cached
    under ``ipo_feed:past_issues`` with a 6h TTL — distinct from the
    45m list cache and 15m subscription cache."""
    stub = _StubClient({
        "https://www.nseindia.com/api/public-past-issues": _StubResponse(
            status_code=200,
            text=json.dumps(_PAST_ISSUES_PAYLOAD),
            json_data=_PAST_ISSUES_PAYLOAD,
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    captured: list[tuple[str, int | None]] = []
    real_set = ipo_feed.redis_client.set

    def _capturing_set(key: Any, value: Any, ex: int | None = None) -> Any:
        captured.append((str(key), ex))
        return real_set(key, value, ex=ex)

    monkeypatch.setattr(ipo_feed.redis_client, "set", _capturing_set)

    records = fetch_past_issues()

    assert isinstance(records, list)
    assert len(records) == 3
    symbols = {r.get("symbol") for r in records}
    assert symbols == {"TIKONA", "MICROSME", "NOPRICE"}

    past_keys = [(k, ex) for (k, ex) in captured if k == "ipo_feed:past_issues"]
    assert past_keys, "past-issues must cache under ipo_feed:past_issues"
    assert past_keys[0][1] == 6 * 60 * 60, "past-issues TTL must be 6h"


def test_fetch_past_issues_cache_key_isolated_from_list_and_sub_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 6h past-issues cache MUST NOT collide with ``ipo_feed:list``
    (45m) or ``ipo_feed:sub:*`` (15m). Capture every SET key and assert."""
    stub = _StubClient({
        "https://www.nseindia.com/api/public-past-issues": _StubResponse(
            status_code=200,
            text=json.dumps(_PAST_ISSUES_PAYLOAD),
            json_data=_PAST_ISSUES_PAYLOAD,
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    captured: list[str] = []
    real_set = ipo_feed.redis_client.set

    def _capturing_set(key: Any, value: Any, ex: int | None = None) -> Any:
        captured.append(str(key))
        return real_set(key, value, ex=ex)

    monkeypatch.setattr(ipo_feed.redis_client, "set", _capturing_set)

    fetch_past_issues()

    assert "ipo_feed:past_issues" in captured
    assert "ipo_feed:list" not in captured
    assert not any(k.startswith("ipo_feed:sub:") for k in captured), captured


def test_fetch_past_issues_unreachable_returns_none_and_does_not_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 403 (or any non-200) MUST return None and NOT cache, so the next
    call retries instead of being stuck for 6 hours."""
    stub = _StubClient({
        "https://www.nseindia.com/api/public-past-issues": _StubResponse(
            status_code=403, text=""
        ),
    })
    call_count = {"n": 0}

    def _counting_warmed() -> _StubClient:
        call_count["n"] += 1
        return stub

    monkeypatch.setattr(ipo_feed, "_warmed_client", _counting_warmed)

    a = fetch_past_issues()
    b = fetch_past_issues()
    assert a is None
    assert b is None
    assert call_count["n"] == 2, "unreachable must NOT be cached"


def test_fetch_past_issues_transport_error_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timeout / transport error during the past-issues GET must
    surface as None (honest unreachable)."""

    class _BoomClient(_StubClient):
        def get(
            self,
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
        ) -> _StubResponse:
            raise httpx.TimeoutException("simulated timeout")

    stub = _BoomClient({})
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    assert fetch_past_issues() is None


# ── fetch_listed_ipo ───────────────────────────────────────────────────


def test_fetch_listed_ipo_parses_realistic_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact-symbol match parses issuePrice -> float, listingDate -> ISO,
    securityType=Equity -> 'mainboard'."""
    stub = _StubClient({
        "https://www.nseindia.com/api/public-past-issues": _StubResponse(
            status_code=200,
            text=json.dumps(_PAST_ISSUES_PAYLOAD),
            json_data=_PAST_ISSUES_PAYLOAD,
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    rec = fetch_listed_ipo("TIKONA")
    assert rec["found"] is True
    assert rec["symbol"] == "TIKONA"
    assert rec["name"] == "Tikona Infinet Limited"
    assert rec["type"] == "mainboard"
    assert rec["issue_price"] == pytest.approx(139.0)
    assert rec["listing_date"] == "2026-05-29"


def test_fetch_listed_ipo_securitytype_sme_maps_to_sme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubClient({
        "https://www.nseindia.com/api/public-past-issues": _StubResponse(
            status_code=200,
            text=json.dumps(_PAST_ISSUES_PAYLOAD),
            json_data=_PAST_ISSUES_PAYLOAD,
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    rec = fetch_listed_ipo("MICROSME")
    assert rec["found"] is True
    assert rec["type"] == "sme"
    assert rec["issue_price"] == pytest.approx(55.0)
    assert rec["listing_date"] == "2026-05-21"


def test_fetch_listed_ipo_case_insensitive_name_substring_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubClient({
        "https://www.nseindia.com/api/public-past-issues": _StubResponse(
            status_code=200,
            text=json.dumps(_PAST_ISSUES_PAYLOAD),
            json_data=_PAST_ISSUES_PAYLOAD,
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    rec = fetch_listed_ipo("tikona")
    assert rec["found"] is True
    assert rec["symbol"] == "TIKONA"

    rec2 = fetch_listed_ipo("Tikona Infinet")
    assert rec2["found"] is True
    assert rec2["symbol"] == "TIKONA"


def test_fetch_listed_ipo_unparseable_price_is_none_not_fabricated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When issuePrice + priceRange are both empty/missing, issue_price
    is None — NEVER fabricated."""
    stub = _StubClient({
        "https://www.nseindia.com/api/public-past-issues": _StubResponse(
            status_code=200,
            text=json.dumps(_PAST_ISSUES_PAYLOAD),
            json_data=_PAST_ISSUES_PAYLOAD,
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    rec = fetch_listed_ipo("NOPRICE")
    assert rec["found"] is True
    assert rec["issue_price"] is None
    assert rec["listing_date"] == "2026-05-18"


def test_fetch_listed_ipo_miss_returns_matches_for_disambiguation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubClient({
        "https://www.nseindia.com/api/public-past-issues": _StubResponse(
            status_code=200,
            text=json.dumps(_PAST_ISSUES_PAYLOAD),
            json_data=_PAST_ISSUES_PAYLOAD,
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    rec = fetch_listed_ipo("DOESNOTEXIST")
    assert rec["found"] is False
    assert "matches" in rec
    assert isinstance(rec["matches"], list)
    assert "note" in rec
    # Source NOT 'unreachable' — we DID reach NSE, just no match.
    assert rec.get("source") != "unreachable"


def test_fetch_listed_ipo_unreachable_surfaces_source_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubClient({
        "https://www.nseindia.com/api/public-past-issues": _StubResponse(
            status_code=403, text=""
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    rec = fetch_listed_ipo("TIKONA")
    assert rec["found"] is False
    assert rec["source"] == "unreachable"
    assert "unreachable" in rec.get("note", "").lower()


def test_fetch_listed_ipo_empty_query_returns_honest_null() -> None:
    rec = fetch_listed_ipo("")
    assert rec["found"] is False
    assert "note" in rec


# ── _get_ipo_listing handler ───────────────────────────────────────────


def _stub_past_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    """Helper: stub _warmed_client to serve the realistic past-issues payload."""
    stub = _StubClient({
        "https://www.nseindia.com/api/public-past-issues": _StubResponse(
            status_code=200,
            text=json.dumps(_PAST_ISSUES_PAYLOAD),
            json_data=_PAST_ISSUES_PAYLOAD,
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)


def test_get_ipo_listing_computes_gain_when_both_prices_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TIKONA: issue ₹139, stubbed current ₹208.50 -> +50.00% (rounded)."""
    _stub_past_issues(monkeypatch)
    monkeypatch.setattr(
        tool_executor, "_listed_current_price", lambda sym: 208.50
    )

    out = asyncio.run(
        tool_executor._get_ipo_listing(
            {"name_or_symbol": "TIKONA"}, "", None, 1,
        )
    )
    assert out["success"] is True
    data = out["data"]
    assert data["_render_hint"] == "ipo_listed_card"
    assert data["symbol"] == "TIKONA"
    assert data["type"] == "mainboard"
    assert data["issue_price"] == pytest.approx(139.0)
    assert data["current_price"] == pytest.approx(208.50)
    assert data["listing_gain_pct"] == pytest.approx(50.0)
    assert data["listing_date"] == "2026-05-29"
    assert data["source"] == "nse"
    assert data["note"] is None


def test_get_ipo_listing_no_current_price_says_listing_data_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the live-price path returns None, current_price + gain are
    None and the note is the honest "listing data pending" string —
    NEVER a fabricated number."""
    _stub_past_issues(monkeypatch)
    monkeypatch.setattr(
        tool_executor, "_listed_current_price", lambda sym: None
    )

    out = asyncio.run(
        tool_executor._get_ipo_listing(
            {"name_or_symbol": "TIKONA"}, "", None, 1,
        )
    )
    assert out["success"] is True
    data = out["data"]
    assert data["_render_hint"] == "ipo_listed_card"
    assert data["issue_price"] == pytest.approx(139.0)
    assert data["current_price"] is None
    assert data["listing_gain_pct"] is None
    assert "listing data pending" in (data["note"] or "")


def test_get_ipo_listing_issue_price_unavailable_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the past-issues record carries no issue price, we surface
    "issue price unavailable" + null gain — even if the live price
    fetch DID succeed."""
    _stub_past_issues(monkeypatch)
    monkeypatch.setattr(
        tool_executor, "_listed_current_price", lambda sym: 60.0
    )

    out = asyncio.run(
        tool_executor._get_ipo_listing(
            {"name_or_symbol": "NOPRICE"}, "", None, 1,
        )
    )
    assert out["success"] is True
    data = out["data"]
    assert data["issue_price"] is None
    assert data["listing_gain_pct"] is None
    assert "issue price unavailable" in (data["note"] or "")


def test_get_ipo_listing_unreachable_returns_no_card_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When NSE past-issues is unreachable, success:false + no card
    hint (the chat surface should render the note as plain text)."""
    stub = _StubClient({
        "https://www.nseindia.com/api/public-past-issues": _StubResponse(
            status_code=503, text=""
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    out = asyncio.run(
        tool_executor._get_ipo_listing(
            {"name_or_symbol": "TIKONA"}, "", None, 1,
        )
    )
    assert out["success"] is False
    data = out["data"]
    assert data["found"] is False
    assert data.get("source") == "unreachable"
    # No ipo_listed_card render hint — the chat must render the note plain.
    assert "_render_hint" not in data or data.get("_render_hint") in (None,)


def test_get_ipo_listing_not_found_returns_no_card_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_past_issues(monkeypatch)
    out = asyncio.run(
        tool_executor._get_ipo_listing(
            {"name_or_symbol": "DOESNOTEXIST"}, "", None, 1,
        )
    )
    assert out["success"] is False
    data = out["data"]
    assert data["found"] is False
    assert "_render_hint" not in data or data.get("_render_hint") in (None,)


def test_get_ipo_listing_empty_query_honest_null() -> None:
    out = asyncio.run(
        tool_executor._get_ipo_listing(
            {"name_or_symbol": ""}, "", None, 1,
        )
    )
    assert out["success"] is False
    assert "note" in out["data"]


# ── Tool routing ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "how did the TIKONA IPO list?",
        "how did Tikona list",
        "TIKONA listing gain",
        "what was the listing price of TIKONA",
        "did TIKONA list well",
        "listing day pop for the X IPO",
        "IPO listing gain for TIKONA",
    ],
)
def test_tool_router_surfaces_get_ipo_listing_for_listing_queries(
    message: str,
) -> None:
    """The IPO-listing capability must be reachable for listing phrasings.

    2026-07-16: the keyword router was deleted (full tool visibility every
    turn) and `get_ipo_listing` is consolidation-hidden — the LLM-visible
    surface for this capability is `get_ipo` (view enum includes
    'listing'). Assert THAT is offered; the old assertion checked a name
    the model never actually saw."""
    from backend.services.tool_router import select_tool_names
    selected = select_tool_names(message)
    assert selected is not None
    assert "get_ipo" in selected, (
        f"get_ipo missing for {message!r}; got {sorted(selected)}"
    )


def test_tool_router_still_surfaces_listing_tool_for_generic_ipo_query() -> None:
    """Generic IPO phrasings keep the listing capability in the toolset
    (via the consolidated `get_ipo`), so a follow-up "how did it list"
    routes correctly even when the first turn was generic."""
    from backend.services.tool_router import select_tool_names
    selected = select_tool_names("any IPOs to talk about")
    assert selected is not None
    assert "get_ipo" in selected


# ── propose_ipo_application graceful fall-through to listed card ────────


def test_propose_ipo_application_returns_listed_card_for_listed_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A LISTED IPO has dropped off the live feed, so get_ipo_details
    returns found=False. P4: propose_ipo_application MUST fall through
    to fetch_listed_ipo and return the ipo_listed_card with an
    "applications are closed" note rather than a bare not-found."""

    # Live feed has no match — simulate the post-listing reality.
    def _empty_live(q: str) -> dict[str, Any]:
        return {
            "found": False,
            "query": q,
            "note": "no live IPO matches (test stub)",
            "matches": [],
            "source": "nse",
        }

    monkeypatch.setattr(
        "backend.services.ipo_feed.get_ipo_details", _empty_live
    )
    _stub_past_issues(monkeypatch)
    monkeypatch.setattr(
        tool_executor, "_listed_current_price", lambda sym: 208.50
    )

    out = asyncio.run(
        tool_executor._propose_ipo_application(
            {"name_or_symbol": "TIKONA"}, "", None, 1,
        )
    )

    assert out["success"] is True
    data = out["data"]
    assert data["_render_hint"] == "ipo_listed_card"
    assert data["symbol"] == "TIKONA"
    assert data["issue_price"] == pytest.approx(139.0)
    assert data["current_price"] == pytest.approx(208.50)
    assert data["listing_gain_pct"] == pytest.approx(50.0)
    note = data.get("note") or ""
    assert "already listed" in note.lower()
    assert "applications are closed" in note.lower()
