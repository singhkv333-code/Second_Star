"""Research history must have explicit daily sampling and a comparable basis."""
from unittest.mock import MagicMock, patch
import pandas as pd
from backend.routers import markets


def _history():
    return pd.DataFrame({"Open": [100.0, 110.0], "High": [102.0, 112.0],
                         "Low": [99.0, 108.0], "Close": [101.0, 111.0], "Volume": [10, 20]},
                        index=pd.to_datetime(["2025-01-01", "2025-01-02"]))


def test_research_yfinance_uses_daily_closes_without_dividend_adjustment():
    ticker = MagicMock()
    ticker.history.return_value = _history()
    with patch.object(markets, "redis_client") as cache, patch.object(markets.yf, "Ticker", return_value=ticker):
        cache.get.return_value = None
        result = markets.get_ohlc("TCS", range="5Y", exchange="NSE", provider="yfinance", price_basis="unadjusted", _user_id=1)
    ticker.history.assert_called_once_with(period="5y", interval="1d", auto_adjust=False)
    assert result.price_basis == "unadjusted"
    assert result.source == "yfinance"
    assert result.interval == "1d"


def test_existing_chart_history_keeps_its_sampling_defaults():
    ticker = MagicMock()
    ticker.history.return_value = _history()
    with patch.object(markets, "redis_client") as cache, patch.object(markets.yf, "Ticker", return_value=ticker):
        cache.get.return_value = None
        result = markets.get_ohlc("TCS", range="5Y", exchange="NSE", provider="yfinance", price_basis="provider", _user_id=1)
    ticker.history.assert_called_once_with(period="5y", interval="1mo")
    assert result.price_basis == "provider"


def test_kite_remains_primary_and_daily_for_research():
    rows = [{"date": "2025-01-01T15:30:00+05:30", "open": 100, "high": 110, "low": 95, "close": 105, "volume": 10}]
    with patch("backend.kite.historical.get_kite_historical", return_value=rows) as kite, patch.object(markets.yf, "Ticker") as yf:
        result = markets.get_ohlc("NIFTY 50", range="5Y", exchange="NSE", provider="auto", price_basis="unadjusted", _user_id=1)
    kite.assert_called_once_with("NIFTY 50", period="5y", exchange="NSE", interval="1d")
    yf.assert_not_called()
    assert result.source == "kite" and result.price_basis == "unadjusted"


def test_provider_and_raw_cache_keys_do_not_collide():
    ticker = MagicMock()
    ticker.history.return_value = _history()
    with patch.object(markets, "redis_client") as cache, patch.object(markets.yf, "Ticker", return_value=ticker):
        cache.get.return_value = None
        for basis in ("provider", "unadjusted"):
            markets.get_ohlc("TCS", range="1M", exchange="NSE", provider="yfinance", price_basis=basis, _user_id=1)
        keys = [call.args[0] for call in cache.get.call_args_list]
    assert len(set(keys)) == 2
