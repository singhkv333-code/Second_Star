"""Unit tests for :mod:`backend.market.global_quotes`.

All HTTP I/O is exercised via the injected ``http_get`` seam — no network
is touched. yfinance fallback for commodities is monkey-patched on the
module to keep the test isolated.

NOTE: the Settings model is frozen / strict in this codebase and the new
config fields (``global_quotes_mock``, ``twelvedata_api_key``,
``global_price_triggers_enabled``, ...) are added in a separate phase-2
task — so this test patches the *module-level* helpers (``_mock_enabled``,
``settings.twelvedata_api_key`` via a SimpleNamespace shim) rather than
the real Settings instance.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import pytest

from backend.market import global_quotes as gq


@pytest.fixture(autouse=True)
def _isolated_settings_and_cache(monkeypatch):
    """Fresh cache + a stand-in settings object the tests can mutate freely.

    The real ``backend.config.settings`` is a strict Pydantic model that
    rejects unknown fields. We swap in a ``SimpleNamespace`` carrying the
    same base-URL defaults documented at the top of the module under test
    so production code paths see plausible URLs while tests can freely add
    ``twelvedata_api_key`` etc. via attribute assignment.
    """
    from backend.cache import MockRedis

    fresh = MockRedis()
    monkeypatch.setattr(gq, "redis_client", fresh)

    shim = SimpleNamespace(
        kraken_api_base_url="https://api.kraken.com/0/public",
        coingecko_api_base_url="https://api.coingecko.com/api/v3",
        twelvedata_api_base_url="https://api.twelvedata.com",
        frankfurter_api_base_url="https://api.frankfurter.app",
        twelvedata_api_key="",
        global_quotes_mock=False,
    )
    monkeypatch.setattr(gq, "settings", shim)
    monkeypatch.delenv("GLOBAL_QUOTES_MOCK", raising=False)
    yield shim


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeHTTP:
    """Captures (url, params) calls and returns a canned response per URL fragment.

    Match is by substring on the URL — keeps the test setup close to the
    docstring URLs without binding to the full settings-driven base.
    """

    def __init__(self, routes: dict[str, object]):
        # routes: {url_fragment: response_or_exception}
        self.routes = routes
        self.calls: list[tuple[str, Optional[dict]]] = []

    def __call__(self, url: str, params: Optional[dict] = None) -> dict:
        self.calls.append((url, params))
        for fragment, resp in self.routes.items():
            if fragment in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp  # type: ignore[return-value]
        raise RuntimeError(f"FakeHTTP: no route for {url}")


# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------

def test_crypto_kraken_primary_success():
    http = FakeHTTP({
        "kraken.com": {"result": {"XXBTZUSD": {"c": ["67890.12345", "0.001"]}}},
    })
    q = gq.get_global_quote("crypto", "btc", http_get=http)
    assert q is not None
    assert q.source == "kraken"
    assert q.asset_class == "crypto"
    assert q.symbol == "BTC"
    assert q.quote_currency == "USD"
    assert q.price == pytest.approx(67890.12345)
    # Should NOT have called CoinGecko.
    assert not any("coingecko" in url for url, _ in http.calls)


def test_crypto_kraken_fails_falls_back_to_coingecko():
    http = FakeHTTP({
        "kraken.com": {"result": {}},  # empty -> primary fails parse
        "coingecko.com": {"bitcoin": {"usd": 67500.0}},
    })
    q = gq.get_global_quote("crypto", "BTC", http_get=http)
    assert q is not None
    assert q.source == "coingecko"
    assert q.price == pytest.approx(67500.0)


def test_crypto_kraken_raises_falls_back_to_coingecko():
    http = FakeHTTP({
        "kraken.com": RuntimeError("boom"),
        "coingecko.com": {"bitcoin": {"usd": 67500.0}},
    })
    q = gq.get_global_quote("crypto", "BTC", http_get=http)
    assert q is not None
    assert q.source == "coingecko"


def test_crypto_inr_quote_skips_to_coingecko():
    http = FakeHTTP({
        "coingecko.com": {"bitcoin": {"inr": 5_700_000.0}},
        "kraken.com": {"result": {"XXBTZUSD": {"c": ["67000", "0.001"]}}},
    })
    q = gq.get_global_quote("crypto", "BTC", quote_currency="INR", http_get=http)
    assert q is not None
    assert q.source == "coingecko"
    assert q.quote_currency == "INR"
    assert q.price == pytest.approx(5_700_000.0)


def test_crypto_all_providers_fail_returns_none():
    http = FakeHTTP({
        "kraken.com": RuntimeError("kraken down"),
        "coingecko.com": RuntimeError("cg down"),
    })
    q = gq.get_global_quote("crypto", "BTC", http_get=http)
    assert q is None


# ---------------------------------------------------------------------------
# Forex
# ---------------------------------------------------------------------------

def test_forex_twelvedata_when_key_present(monkeypatch):
    gq.settings.twelvedata_api_key = "test-key"
    http = FakeHTTP({
        "twelvedata.com/price": {"price": "1.0856"},
    })
    q = gq.get_global_quote("forex", "EURUSD", http_get=http)
    assert q is not None
    assert q.source == "twelvedata"
    assert q.symbol == "EURUSD"
    assert q.quote_currency == "USD"
    assert q.price == pytest.approx(1.0856)
    # Twelve Data symbol must include the slash.
    td_call = next(c for c in http.calls if "twelvedata" in c[0])
    assert td_call[1] is not None
    assert td_call[1]["symbol"] == "EUR/USD"


def test_forex_twelvedata_no_key_falls_back_to_frankfurter(monkeypatch):
    gq.settings.twelvedata_api_key = ""
    http = FakeHTTP({
        "frankfurter.app": {
            "amount": 1.0,
            "base": "EUR",
            "date": "2026-06-20",
            "rates": {"USD": 1.0852},
        },
    })
    q = gq.get_global_quote("forex", "EUR/USD", http_get=http)
    assert q is not None
    assert q.source == "frankfurter"
    # Symbol is normalised to remove the slash.
    assert q.symbol == "EURUSD"
    assert q.price == pytest.approx(1.0852)


def test_forex_twelvedata_error_status_falls_back(monkeypatch):
    gq.settings.twelvedata_api_key = "test-key"
    http = FakeHTTP({
        "twelvedata.com/price": {"status": "error", "code": 401, "message": "Invalid API key"},
        "frankfurter.app": {"rates": {"INR": 84.12}},
    })
    q = gq.get_global_quote("forex", "USDINR", http_get=http)
    assert q is not None
    assert q.source == "frankfurter"
    assert q.quote_currency == "INR"
    assert q.price == pytest.approx(84.12)


def test_forex_invalid_pair_returns_none(monkeypatch):
    gq.settings.twelvedata_api_key = ""
    http = FakeHTTP({})
    q = gq.get_global_quote("forex", "EUR", http_get=http)
    assert q is None


def test_forex_all_providers_fail(monkeypatch):
    gq.settings.twelvedata_api_key = "test-key"
    http = FakeHTTP({
        "twelvedata.com/price": RuntimeError("td down"),
        "frankfurter.app": RuntimeError("fr down"),
    })
    q = gq.get_global_quote("forex", "EURUSD", http_get=http)
    assert q is None


# ---------------------------------------------------------------------------
# Commodity
# ---------------------------------------------------------------------------

def test_commodity_twelvedata_primary(monkeypatch):
    gq.settings.twelvedata_api_key = "k"
    http = FakeHTTP({
        "twelvedata.com/price": {"price": "73.45"},
    })
    q = gq.get_global_quote("commodity", "WTI", http_get=http)
    assert q is not None
    assert q.source == "twelvedata"
    assert q.symbol == "WTI"
    assert q.quote_currency == "USD"
    assert q.price == pytest.approx(73.45)
    # Confirm normalisation: WTI -> WTI/USD on the wire.
    td_call = next(c for c in http.calls if "twelvedata" in c[0])
    assert td_call[1] is not None
    assert td_call[1]["symbol"] == "WTI/USD"


def test_commodity_yfinance_fallback(monkeypatch):
    # No TD key + monkey-patched yfinance returning a usable last close.
    gq.settings.twelvedata_api_key = ""

    def fake_yf(yf_ticker: str) -> Optional[float]:
        assert yf_ticker == "GC=F"
        return 2345.6

    monkeypatch.setattr(gq, "_fetch_yfinance_futures", fake_yf)
    http = FakeHTTP({})  # commodity path won't hit HTTP when TD key empty
    q = gq.get_global_quote("commodity", "XAUUSD", http_get=http)
    assert q is not None
    assert q.source == "yfinance"
    assert q.price == pytest.approx(2345.6)


def test_commodity_all_fail(monkeypatch):
    gq.settings.twelvedata_api_key = "k"
    monkeypatch.setattr(gq, "_fetch_yfinance_futures", lambda _t: None)
    http = FakeHTTP({
        "twelvedata.com/price": RuntimeError("td down"),
    })
    q = gq.get_global_quote("commodity", "WTI", http_get=http)
    assert q is None


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------

def test_mock_mode_deterministic_and_repeatable(monkeypatch):
    gq.settings.global_quotes_mock = True
    http = FakeHTTP({})  # would raise if anyone tried to call

    q1 = gq.get_global_quote("crypto", "BTC", http_get=http)
    # Second call hits the cache; clear it to confirm the deterministic seed.
    monkeypatch.setattr(gq, "redis_client", __import__("backend.cache", fromlist=["MockRedis"]).MockRedis())
    q2 = gq.get_global_quote("crypto", "btc", http_get=http)

    assert q1 is not None and q2 is not None
    assert q1.source == "mock"
    assert q2.source == "mock"
    assert q1.price == q2.price
    assert q1.symbol == "BTC"
    # No HTTP was attempted in mock mode.
    assert http.calls == []


def test_mock_mode_different_symbols_yield_different_prices(monkeypatch):
    gq.settings.global_quotes_mock = True
    q_btc = gq.get_global_quote("crypto", "BTC")
    q_eth = gq.get_global_quote("crypto", "ETH")
    assert q_btc is not None and q_eth is not None
    assert q_btc.price != q_eth.price


def test_mock_mode_forex_uses_pair_quote(monkeypatch):
    gq.settings.global_quotes_mock = True
    q = gq.get_global_quote("forex", "USDINR")
    assert q is not None
    assert q.quote_currency == "INR"
    assert q.symbol == "USDINR"
    assert q.source == "mock"


def test_mock_mode_via_env(monkeypatch):
    monkeypatch.setenv("GLOBAL_QUOTES_MOCK", "1")
    gq.settings.global_quotes_mock = False
    q = gq.get_global_quote("commodity", "WTI")
    assert q is not None
    assert q.source == "mock"


# ---------------------------------------------------------------------------
# Validation, normalisation, examples, caching
# ---------------------------------------------------------------------------

def test_unknown_asset_class_returns_none():
    assert gq.get_global_quote("equity", "AAPL") is None
    assert gq.get_global_quote("", "BTC") is None


def test_empty_symbol_returns_none():
    assert gq.get_global_quote("crypto", "") is None
    assert gq.get_global_quote("crypto", "   ") is None


def test_symbol_normalisation_uppercase_and_strip():
    http = FakeHTTP({
        "kraken.com": {"result": {"X": {"c": ["1.0", "0"]}}},
    })
    q = gq.get_global_quote("crypto", "  btc  ", http_get=http)
    assert q is not None
    assert q.symbol == "BTC"


def test_supported_examples_shape():
    ex = gq.supported_examples()
    assert set(ex.keys()) == set(gq.ASSET_CLASSES)
    assert "BTC" in ex["crypto"]
    assert "EURUSD" in ex["forex"]
    assert "WTI" in ex["commodity"]


def test_result_is_cached_for_subsequent_calls():
    calls: list[str] = []

    def http(url: str, params: Optional[dict] = None) -> dict:
        calls.append(url)
        return {"result": {"X": {"c": ["42000.0", "0"]}}}

    q1 = gq.get_global_quote("crypto", "BTC", http_get=http)
    q2 = gq.get_global_quote("crypto", "BTC", http_get=http)
    assert q1 is not None and q2 is not None
    # The second call should be served from the cache — only one HTTP hit.
    assert len(calls) == 1
    assert q1.price == q2.price


def test_globalquote_is_frozen_dataclass():
    q = gq.GlobalQuote(
        asset_class="crypto",
        symbol="BTC",
        price=1.0,
        quote_currency="USD",
        source="mock",
        as_of="2026-06-21T00:00:00Z",
    )
    with pytest.raises((AttributeError, Exception)):
        q.price = 2.0  # type: ignore[misc]
