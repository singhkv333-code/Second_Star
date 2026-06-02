"""Tests for the P1 IPO-feed enrichment helpers.

What this exercises:
  - ``fetch_subscription``: realistic per-category active-category payload
    parses into the right floats; "Missing Symbol" / "[]" / {} / unreachable
    all return honest-null (note set, NO fabricated 0s); the 15-min cache
    key is ``ipo_feed:sub:{SYM}`` and does NOT collide with the
    45-min list cache key ``ipo_feed:list``.
  - ``resolve_listing_date``: parses listingDate (top-level + nested _raw)
    to ISO; None when absent.
  - ``resolve_rhp``: returns the first plausible URL when present; None
    when absent (no fabrication).
  - ``detect_registrar``: (None, None) when the registrar field isn't in
    the record (the P1 live reality); maps a known registrar token to
    its allotment deep-link when injected.
  - GMP key is ABSENT from the propose-application payload when
    IPO_GMP_ENABLED is False (the v1 state).
  - GET /ipo-subscription/{symbol} returns the right shape.

The NSE HTTP layer is stubbed via monkeypatch of ``ipo_feed._warmed_client``
+ ``httpx.Client``; we never hit the network in tests. Stubbing mirrors
the realistic shape researched in the P1 plan rather than the empty live
response (zero open IPOs at build time).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from backend.services import ipo_feed
from backend.services.ipo_feed import (
    RTA_ALLOTMENT_URLS,
    detect_registrar,
    fetch_subscription,
    gmp_payload,
    resolve_listing_date,
    resolve_rhp,
)


# ── Helpers ────────────────────────────────────────────────────────────────


class _StubResponse:
    """Minimal httpx-Response stand-in: ``status_code``, ``text``, ``json``,
    ``content``. ``json`` raises ValueError when ``_json`` is the
    sentinel ``_NO_JSON`` to model bare-string responses."""

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
    """Stand-in for the ``httpx.Client`` returned by ``_warmed_client``.

    Routes GETs to a per-URL handler dict. Used as a context manager.
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
        # Honour an exact URL match first.
        if url in self._routes:
            return self._routes[url]
        # Then a prefix match for parameterised endpoints.
        for route_url, resp in self._routes.items():
            if url.startswith(route_url):
                return resp
        return _StubResponse(status_code=404, text="")


@pytest.fixture(autouse=True)
def _flush_ipo_feed_cache() -> None:
    """Wipe any cache entries from prior tests so cache asserts are
    deterministic. Works for both the MockRedis fallback and real
    Redis (the dev/CI default when Redis is reachable on localhost)."""
    rc = ipo_feed.redis_client
    # MockRedis path — clear the in-memory dict.
    store = getattr(rc, "_store", None)
    if isinstance(store, dict):
        for k in [k for k in list(store.keys()) if str(k).startswith("ipo_feed:")]:
            store.pop(k, None)
        return
    # Real Redis path — SCAN + DEL on the test keys we know we set.
    try:
        for key in rc.scan_iter(match="ipo_feed:sub:*"):  # type: ignore[attr-defined]
            rc.delete(key)
        rc.delete("ipo_feed:list")
        rc.delete("ipo_feed:list:raw")
    except Exception:  # noqa: BLE001
        # If SCAN isn't supported, fall back to delete-by-known-symbols.
        for sym in ("TIKONA", "NOACTIVE", "BLOCKED", "FAIL", "BIGBUCKS"):
            try:
                rc.delete(f"ipo_feed:sub:{sym}")
            except Exception:  # noqa: BLE001
                pass


# ── Realistic active-category payload (the P1 plan's "expected populated
#    shape"). The live API returns per-category subscription multiples;
#    we model two plausible shapes — keyed-dict (common) and list-of-
#    records (an alternative NSE shape).

_REALISTIC_KEYED_PAYLOAD: dict[str, Any] = {
    "QIB":              {"noOfTimesSubscribed": "2.10"},
    "sNII":             {"noOfTimesSubscribed": "0.40"},
    "bNII":             {"noOfTimesSubscribed": "0.45"},
    "RII":              {"noOfTimesSubscribed": "1.80"},
    "Employee":         {"noOfTimesSubscribed": "0.50"},
    "Total":            {"noOfTimesSubscribed": "1.55"},
}

_REALISTIC_LIST_PAYLOAD: list[dict[str, Any]] = [
    {"category": "QIB",   "subscription": 1.4},
    {"category": "sNII",  "subscription": 0.4},
    {"category": "bNII",  "subscription": 0.4},
    {"category": "RII",   "subscription": 2.1},
    {"category": "Total", "subscription": 1.3},
]


# ── fetch_subscription ──────────────────────────────────────────────────────


def test_fetch_subscription_parses_keyed_active_category_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Realistic per-category dict → correct floats per bucket; sNII+bNII
    fold into a single NII multiple; Total → overall; missing categories
    stay None (no fabricated 0)."""
    stub = _StubClient({
        "https://www.nseindia.com/api/ipo-active-category": _StubResponse(
            status_code=200,
            text=json.dumps(_REALISTIC_KEYED_PAYLOAD),
            json_data=_REALISTIC_KEYED_PAYLOAD,
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    body = fetch_subscription("TIKONA")

    assert body["source"] == "nse"
    assert body["note"] is None
    assert isinstance(body["as_of"], str) and body["as_of"]
    sub = body["subscription"]
    assert isinstance(sub, dict)
    assert sub["qib"] == pytest.approx(2.10)
    # sNII (0.40) + bNII (0.45) summed into nii.
    assert sub["nii"] == pytest.approx(0.85)
    assert sub["rii"] == pytest.approx(1.80)
    assert sub["employee"] == pytest.approx(0.50)
    # Shareholder absent in this payload — must stay None, NOT 0.
    assert sub["shareholder"] is None
    assert sub["overall"] == pytest.approx(1.55)


def test_fetch_subscription_parses_list_active_category_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alternate list-of-records shape parses identically — values per
    category, sNII+bNII folded into nii."""
    stub = _StubClient({
        "https://www.nseindia.com/api/ipo-active-category": _StubResponse(
            status_code=200,
            text=json.dumps(_REALISTIC_LIST_PAYLOAD),
            json_data=_REALISTIC_LIST_PAYLOAD,
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    body = fetch_subscription("TIKONA")
    sub = body["subscription"]
    assert isinstance(sub, dict)
    assert sub["qib"] == pytest.approx(1.4)
    assert sub["nii"] == pytest.approx(0.8)  # 0.4 + 0.4
    assert sub["rii"] == pytest.approx(2.1)
    assert sub["overall"] == pytest.approx(1.3)
    assert sub["employee"] is None      # honest None, not 0
    assert sub["shareholder"] is None   # honest None, not 0


@pytest.mark.parametrize(
    "stub_resp,reason_token",
    [
        # Bare-string body — "Missing Symbol" when symbol arg is missing.
        (_StubResponse(status_code=200, text="Missing Symbol"), "no subscription record"),
        # Empty list literal — symbol present but no active record.
        (_StubResponse(status_code=200, text="[]", json_data=[]), "no subscription record"),
        # Empty JSON object.
        (_StubResponse(status_code=200, text="{}", json_data={}), "no subscription record"),
    ],
)
def test_fetch_subscription_honest_null_on_empty_responses(
    monkeypatch: pytest.MonkeyPatch,
    stub_resp: _StubResponse,
    reason_token: str,
) -> None:
    """"Missing Symbol" / "[]" / {} → subscription=None + honest note +
    source still "nse" (reachable, just empty). No fabricated 0s."""
    stub = _StubClient({
        "https://www.nseindia.com/api/ipo-active-category": stub_resp,
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    body = fetch_subscription("NOACTIVE")
    assert body["subscription"] is None
    assert body["source"] == "nse"
    assert body["note"] is not None
    assert reason_token in body["note"].lower()


def test_fetch_subscription_unreachable_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-200 from NSE → source='unreachable' + honest note."""
    stub = _StubClient({
        "https://www.nseindia.com/api/ipo-active-category": _StubResponse(
            status_code=403, text=""
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    body = fetch_subscription("BLOCKED")
    assert body["subscription"] is None
    assert body["source"] == "unreachable"
    assert "403" in (body["note"] or "")


def test_fetch_subscription_cache_key_separate_from_list_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subscription cache key must be ``ipo_feed:sub:{SYM}`` — distinct
    from the list cache key ``ipo_feed:list`` so the 45-min list and
    the 15-min per-symbol sub don't clobber each other.
    """
    captured: list[tuple[str, int | None]] = []
    real_set = ipo_feed.redis_client.set

    def _capturing_set(key: Any, value: Any, ex: int | None = None) -> Any:
        captured.append((str(key), ex))
        return real_set(key, value, ex=ex)

    monkeypatch.setattr(ipo_feed.redis_client, "set", _capturing_set)

    stub = _StubClient({
        "https://www.nseindia.com/api/ipo-active-category": _StubResponse(
            status_code=200,
            text=json.dumps(_REALISTIC_KEYED_PAYLOAD),
            json_data=_REALISTIC_KEYED_PAYLOAD,
        ),
    })
    monkeypatch.setattr(ipo_feed, "_warmed_client", lambda: stub)

    fetch_subscription("TIKONA")

    sub_keys = [k for (k, _ex) in captured if k.startswith("ipo_feed:sub:")]
    assert sub_keys == ["ipo_feed:sub:TIKONA"], captured
    # Must NOT collide with the 45-min list cache.
    assert "ipo_feed:list" not in [k for (k, _ex) in captured]
    # And the TTL we set is 15 minutes (900s), distinct from 45m (2700).
    sub_ttls = [ex for (k, ex) in captured if k == "ipo_feed:sub:TIKONA"]
    assert sub_ttls == [15 * 60]


def test_fetch_subscription_cache_hit_skips_network_on_second_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful body is cached; the second call must NOT make an HTTP
    call (so the warmed-client constructor is never invoked again)."""
    stub = _StubClient({
        "https://www.nseindia.com/api/ipo-active-category": _StubResponse(
            status_code=200,
            text=json.dumps(_REALISTIC_KEYED_PAYLOAD),
            json_data=_REALISTIC_KEYED_PAYLOAD,
        ),
    })
    call_count = {"n": 0}

    def _counting_warmed() -> _StubClient:
        call_count["n"] += 1
        return stub

    monkeypatch.setattr(ipo_feed, "_warmed_client", _counting_warmed)

    a = fetch_subscription("TIKONA")
    b = fetch_subscription("TIKONA")
    assert a["subscription"] == b["subscription"]
    assert call_count["n"] == 1, (
        "Second call should be cache-hit; warmed client must not be re-built."
    )


def test_fetch_subscription_unreachable_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreachable responses must NOT be cached, so the next call
    retries instead of being stuck for 15 minutes."""
    stub = _StubClient({
        "https://www.nseindia.com/api/ipo-active-category": _StubResponse(
            status_code=403, text=""
        ),
    })
    call_count = {"n": 0}

    def _counting_warmed() -> _StubClient:
        call_count["n"] += 1
        return stub

    monkeypatch.setattr(ipo_feed, "_warmed_client", _counting_warmed)

    fetch_subscription("FAIL")
    fetch_subscription("FAIL")
    assert call_count["n"] == 2


def test_fetch_subscription_empty_symbol_returns_honest_null() -> None:
    body = fetch_subscription("")
    assert body["subscription"] is None
    assert body["source"] == "nse"
    assert body["note"]


# ── resolve_listing_date ────────────────────────────────────────────────────


def test_resolve_listing_date_from_top_level_field() -> None:
    rec = {"symbol": "X", "listingDate": "21-MAY-2026"}
    assert resolve_listing_date(rec) == "2026-05-21"


def test_resolve_listing_date_from_nested_raw() -> None:
    rec = {"symbol": "X", "_raw": {"listingDate": "2026-06-03"}}
    assert resolve_listing_date(rec) == "2026-06-03"


def test_resolve_listing_date_none_when_absent() -> None:
    assert resolve_listing_date({"symbol": "X"}) is None
    assert resolve_listing_date({}) is None


# ── resolve_rhp ─────────────────────────────────────────────────────────────


def test_resolve_rhp_none_when_no_url_present() -> None:
    """Live NSE schema doesn't carry an RHP link — must return None
    rather than fabricate one."""
    rec = {
        "symbol": "TIKONA",
        "companyName": "Tikona Infinet",
        "_raw": {"securityType": "Equity", "lotSize": 110},
    }
    assert resolve_rhp(rec) is None


def test_resolve_rhp_returns_explicit_rhp_link_when_present() -> None:
    rec = {"symbol": "X", "_raw": {"rhpLink": "https://example.com/rhp.pdf"}}
    assert resolve_rhp(rec) == "https://example.com/rhp.pdf"


def test_resolve_rhp_returns_first_plausible_http_url() -> None:
    rec = {
        "symbol": "X",
        "_raw": {
            "homepage": "https://issuer.example.com",
            "logo": "not-a-url",
        },
    }
    assert resolve_rhp(rec) == "https://issuer.example.com"


# ── detect_registrar ────────────────────────────────────────────────────────


def test_detect_registrar_none_none_when_absent() -> None:
    """P1 reality: NSE feed doesn't carry the registrar name → (None, None)."""
    rec = {"symbol": "TIKONA", "_raw": {"securityType": "Equity"}}
    assert detect_registrar(rec) == (None, None)


def test_detect_registrar_maps_known_token() -> None:
    """When a future source yields a registrar name, the static map
    resolves the deep-link."""
    rec = {"symbol": "X", "registrar": "KFin Technologies Limited"}
    name, url = detect_registrar(rec)
    assert name == "KFin Technologies Limited"
    assert url == RTA_ALLOTMENT_URLS["kfintech"]


def test_detect_registrar_unknown_name_keeps_name_drops_url() -> None:
    rec = {"symbol": "X", "registrar": "Some New RTA Pvt Ltd"}
    name, url = detect_registrar(rec)
    assert name == "Some New RTA Pvt Ltd"
    assert url is None


# ── GMP fail-closed ─────────────────────────────────────────────────────────


def test_gmp_payload_disabled_returns_none() -> None:
    """v1 default: IPO_GMP_ENABLED is False → gmp_payload returns None
    so the caller omits the key entirely."""
    assert ipo_feed.IPO_GMP_ENABLED is False
    assert gmp_payload("ANY") is None


def test_propose_ipo_application_omits_gmp_key_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The propose-application payload must NOT carry a 'gmp' key in
    v1 (flag off). Absence-vs-null is load-bearing — the FE shows no
    chip only when the key is absent."""
    import asyncio

    from backend.agents.tool_executor import _propose_ipo_application

    fake_ipo = {
        "found": True,
        "ipo": {
            "name": "Tikona Infinet", "symbol": "TIKONA",
            "price_band": "125-132", "open_date": "2026-06-03",
            "close_date": "2026-06-05", "lot_size": 110,
            "issue_size": "₹1,200 cr", "type": "mainboard",
            "status": "open",
        },
        "extra": {},
        "source": "nse",
    }
    monkeypatch.setattr(
        "backend.services.ipo_feed.get_ipo_details", lambda _q: fake_ipo,
    )
    monkeypatch.setattr(
        "backend.services.ipo_feed.fetch_subscription",
        lambda _sym: {
            "subscription": None,
            "as_of": "01 Jan 2026, 09:00 IST",
            "source": "nse",
            "note": "no subscription record",
        },
    )
    # Flag must be OFF for this assertion (the v1 default).
    monkeypatch.setattr("backend.services.ipo_feed.IPO_GMP_ENABLED", False)

    out = asyncio.run(_propose_ipo_application(
        {"name_or_symbol": "TIKONA"}, None, None, 1,
    ))
    assert out["success"] is True
    payload = out["data"]
    assert "gmp" not in payload, payload.keys()


# ── GET /ipo-subscription/{symbol} ──────────────────────────────────────────


def test_ipo_subscription_endpoint_returns_shape(
    client, auth_headers, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Router returns {symbol, subscription, as_of, source, note} —
    pulled straight from fetch_subscription."""
    monkeypatch.setattr(
        "backend.routers.ipo_applications.fetch_subscription",
        lambda sym: {
            "subscription": {
                "qib": 2.1, "nii": 0.85, "rii": 1.8,
                "employee": None, "shareholder": None, "overall": 1.5,
            },
            "as_of": "02 Jun 2026, 14:30 IST",
            "source": "nse",
            "note": None,
        },
    )
    r = client.get("/ipo-subscription/TIKONA", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "TIKONA"
    sub = body["subscription"]
    assert sub["qib"] == 2.1
    assert sub["rii"] == 1.8
    assert sub["employee"] is None    # honest None preserved on the wire
    assert sub["shareholder"] is None
    assert body["source"] == "nse"
    assert body["as_of"]
    assert body["note"] is None


def test_ipo_subscription_endpoint_requires_auth(client) -> None:
    r = client.get("/ipo-subscription/TIKONA")
    assert r.status_code == 401


def test_ipo_subscription_endpoint_unreachable_passthrough(
    client, auth_headers, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When fetch_subscription is unreachable, the router surfaces it
    honestly — source='unreachable', subscription=None, note set."""
    monkeypatch.setattr(
        "backend.routers.ipo_applications.fetch_subscription",
        lambda sym: {
            "subscription": None,
            "as_of": "02 Jun 2026, 14:30 IST",
            "source": "unreachable",
            "note": "NSE HTTP 403 for ipo-active-category",
        },
    )
    r = client.get("/ipo-subscription/BLOCKED", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["subscription"] is None
    assert body["source"] == "unreachable"
    assert "403" in body["note"]
