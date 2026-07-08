"""Kalshi REST worker — the load-bearing seam.

Drives the REAL venue-agnostic PolymarketWSEvaluator through the Kalshi
poll loop with a stubbed source, proving: (a) threshold cross fires once
on the YES side, (b) the NO side watches 1 - yes_price, (c) a settled
market routes to on_resolved, and (d) _extract_step_ws_config maps a
trigger.kalshi step config to a WsRegSpec.
"""
from __future__ import annotations

import pytest

from backend.news_events.pipeline.prediction_market_ws import (
    PolymarketWSEvaluator,
)
from backend.news_events.sources.kalshi import KalshiSnapshot
from backend.news_events.workers import kalshi_rest_worker as krw

pytestmark = pytest.mark.asyncio


def _snap(ticker: str, *, yes: float, status: str = "active",
          result: str = "") -> KalshiSnapshot:
    return KalshiSnapshot(
        market_id=ticker, slug=None, question=ticker, yes_price=yes,
        closed=status not in {"open", "active", "unopened"},
        raw={"ticker": ticker, "status": status, "result": result},
    )


def _evaluator() -> PolymarketWSEvaluator:
    # session_factory unused on the workflow path.
    return PolymarketWSEvaluator(session_factory=lambda: None)  # type: ignore[arg-type]


def _stub_source(monkeypatch, snaps_by_tick: dict[str, KalshiSnapshot]) -> None:
    async def _get_markets(tickers):
        return {t: snaps_by_tick[t] for t in tickers if t in snaps_by_tick}
    monkeypatch.setattr(krw.kalshi_src, "get_markets", _get_markets)


# ── extract step config ──────────────────────────────────────────────


def test_extract_step_ws_config_threshold() -> None:
    reg = krw._extract_step_ws_config({
        "token_id": "T:YES", "mode": "threshold", "threshold": 0.7,
        "direction": "above", "resolve_on": "YES",
    })
    assert reg is not None
    assert (reg.asset_id, reg.mode, reg.threshold, reg.direction) == (
        "T:YES", "threshold", 0.7, "above")


def test_extract_step_ws_config_rejects_threshold_without_value() -> None:
    assert krw._extract_step_ws_config({"token_id": "T:YES", "mode": "threshold"}) is None


def test_extract_step_ws_config_no_token() -> None:
    assert krw._extract_step_ws_config({"mode": "resolution"}) is None


# ── poll loop drives the evaluator ───────────────────────────────────


async def test_yes_side_threshold_fires_once_on_cross(monkeypatch) -> None:
    ev = _evaluator()
    fired: list[dict] = []

    async def _handler(payload: dict) -> None:
        fired.append(payload)

    ev.register(key="kalshi:wf:w1:0", asset_id="T:YES", fire_handler=_handler,
                mode="threshold", threshold=0.7, direction="above")

    # Tick 1 — baseline below threshold, no fire.
    _stub_source(monkeypatch, {"T": _snap("T", yes=0.50)})
    await krw._poll_prices(ev)
    assert fired == []

    # Tick 2 — crosses above 0.7 → fire once.
    _stub_source(monkeypatch, {"T": _snap("T", yes=0.80)})
    await krw._poll_prices(ev)
    assert len(fired) == 1
    assert fired[0]["mode"] == "threshold"
    assert fired[0]["mid_price"] == 0.80


async def test_no_side_watches_one_minus_yes(monkeypatch) -> None:
    """A NO-side asset crosses when (1 - yes_price) crosses the threshold."""
    ev = _evaluator()
    fired: list[dict] = []

    async def _handler(payload: dict) -> None:
        fired.append(payload)

    ev.register(key="kalshi:wf:w2:0", asset_id="T:NO", fire_handler=_handler,
                mode="threshold", threshold=0.6, direction="above")

    # Tick 1 — yes=0.5 → NO mid 0.5 (baseline, below 0.6).
    _stub_source(monkeypatch, {"T": _snap("T", yes=0.50)})
    await krw._poll_prices(ev)
    assert fired == []

    # Tick 2 — yes=0.3 → NO mid 0.7 (crosses above 0.6) → fire.
    _stub_source(monkeypatch, {"T": _snap("T", yes=0.30)})
    await krw._poll_prices(ev)
    assert len(fired) == 1
    assert fired[0]["mid_price"] == pytest.approx(0.70)


async def test_settled_market_routes_to_resolution(monkeypatch) -> None:
    ev = _evaluator()
    fired: list[dict] = []

    async def _handler(payload: dict) -> None:
        fired.append(payload)

    ev.register(key="kalshi:wf:w3:0", asset_id="T:YES", fire_handler=_handler,
                mode="resolution", resolve_on="YES")

    _stub_source(monkeypatch, {"T": _snap("T", yes=1.0, status="settled", result="yes")})
    await krw._poll_prices(ev)
    assert len(fired) == 1
    assert fired[0]["mode"] == "resolution"
    assert fired[0]["winner"] == "YES"


async def test_settled_no_does_not_fire_resolve_on_yes(monkeypatch) -> None:
    ev = _evaluator()
    fired: list[dict] = []

    async def _handler(payload: dict) -> None:  # pragma: no cover
        fired.append(payload)

    ev.register(key="kalshi:wf:w4:0", asset_id="T:YES", fire_handler=_handler,
                mode="resolution", resolve_on="YES")

    _stub_source(monkeypatch, {"T": _snap("T", yes=0.0, status="settled", result="no")})
    await krw._poll_prices(ev)
    assert fired == []
