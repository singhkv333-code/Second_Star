"""Tests for the fetch.fundamental yfinance-backed executor.

We monkeypatch yfinance.Ticker so the tests are deterministic and
don't hit the network. The executor's job is only to map our
metric vocabulary to yfinance's info-dict keys + handle missing values.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.workflows.steps.fetches import (
    NotYetAvailableError,
    execute_fetch_fundamental,
)


class _StubCtx:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config


class _FakeTicker:
    """Stand-in for yfinance.Ticker — only `.info` is exercised."""
    def __init__(self, info: dict[str, Any]) -> None:
        self.info = info


def _patch_ticker(monkeypatch: pytest.MonkeyPatch, info: dict[str, Any]) -> None:
    """Make `yfinance.Ticker(...)` return a fake with the given info."""
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda sym: _FakeTicker(info))


@pytest.mark.asyncio
async def test_fetch_pe_uses_trailing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ticker(monkeypatch, {"trailingPE": 28.5, "forwardPE": 30.1})
    out = await execute_fetch_fundamental(
        _StubCtx({"symbol": "INFY", "metric": "pe"})
    )
    assert out is not None
    assert out["value"] == pytest.approx(28.5)
    assert out["source"] == "yfinance"


@pytest.mark.asyncio
async def test_fetch_pe_falls_back_to_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ticker(monkeypatch, {"trailingPE": None, "forwardPE": 30.1})
    out = await execute_fetch_fundamental(
        _StubCtx({"symbol": "INFY", "metric": "pe"})
    )
    assert out is not None
    assert out["value"] == pytest.approx(30.1)


@pytest.mark.asyncio
async def test_fetch_mcap(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ticker(monkeypatch, {"marketCap": 6_30_000_00_00_000})
    out = await execute_fetch_fundamental(
        _StubCtx({"symbol": "RELIANCE", "metric": "mcap"})
    )
    assert out is not None
    assert out["value"] == 6_30_000_00_00_000


@pytest.mark.asyncio
async def test_fetch_roe(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ticker(monkeypatch, {"returnOnEquity": 0.187})
    out = await execute_fetch_fundamental(
        _StubCtx({"symbol": "TCS", "metric": "roe"})
    )
    assert out is not None
    assert out["value"] == pytest.approx(0.187)


@pytest.mark.asyncio
async def test_fetch_de(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ticker(monkeypatch, {"debtToEquity": 0.21})
    out = await execute_fetch_fundamental(
        _StubCtx({"symbol": "HDFCBANK", "metric": "de"})
    )
    assert out is not None
    assert out["value"] == pytest.approx(0.21)


@pytest.mark.asyncio
async def test_fetch_period_end_from_lastFiscalYearEnd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When yfinance returns lastFiscalYearEnd as a UNIX timestamp,
    we surface it as period_end ISO date."""
    # 2026-03-31 UTC midnight in seconds since epoch.
    import datetime as _dt
    ts = int(_dt.datetime(2026, 3, 31, tzinfo=_dt.timezone.utc).timestamp())
    _patch_ticker(monkeypatch, {
        "trailingPE": 25.0, "lastFiscalYearEnd": ts,
    })
    out = await execute_fetch_fundamental(
        _StubCtx({"symbol": "INFY", "metric": "pe"})
    )
    assert out is not None
    assert out.get("period_end") == "2026-03-31"


@pytest.mark.asyncio
async def test_missing_value_raises_not_yet_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """yfinance returning empty / None → NotYetAvailableError per spec
    rule 'never fake data'."""
    _patch_ticker(monkeypatch, {"marketCap": None})
    with pytest.raises(NotYetAvailableError, match="not available"):
        await execute_fetch_fundamental(
            _StubCtx({"symbol": "GHOST", "metric": "mcap"})
        )


@pytest.mark.asyncio
async def test_yfinance_exception_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If yfinance throws, the executor surfaces a clean
    NotYetAvailableError (engine retries handle the rest)."""
    import yfinance as yf

    class _Boom:
        @property
        def info(self) -> dict[str, Any]:
            raise RuntimeError("rate limited")

    monkeypatch.setattr(yf, "Ticker", lambda sym: _Boom())
    with pytest.raises(NotYetAvailableError, match="yfinance lookup failed"):
        await execute_fetch_fundamental(
            _StubCtx({"symbol": "INFY", "metric": "pe"})
        )


@pytest.mark.asyncio
async def test_unsupported_metric_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # When a metric isn't in the financials DB and has no yfinance
    # fallback, execute_fetch_fundamental raises NotYetAvailableError
    # (a semantic subclass of RuntimeError). The previous test expected
    # ValueError, which was the pre-2026 behaviour; the named-exception
    # change is intentional and documented in backend/workflows/steps/
    # fetches.py.
    _patch_ticker(monkeypatch, {"trailingPE": 25.0})
    with pytest.raises(NotYetAvailableError, match="not available"):
        await execute_fetch_fundamental(
            _StubCtx({"symbol": "INFY", "metric": "ev_ebitda"})
        )
