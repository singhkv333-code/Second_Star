"""Tests for IPO P0.5 — _list_upcoming_ipos executor render-hint contract.

What this exercises (and only this):
  - The chat tool executor wrapper for ``list_upcoming_ipos`` attaches the
    FE render hint ``"ipo_list_card"`` to ``data`` on EVERY outcome:
      (a) non-empty open list
      (b) empty-but-reachable feed (no live IPOs right now)
      (c) unreachable feed (NSE blew up / timed out)
  - The hint is BYTE-IDENTICAL to the FE discriminator
    (``ChatDemo.resolveStreamingMessage`` switches on this exact string).
  - The ``success`` flag continues to mirror feed reachability —
    ``True`` on reachable (empty or not), ``False`` on unreachable —
    which is the existing contract callers depend on.
  - ``count`` / ``note`` / ``ipos`` / ``source`` passthrough is preserved;
    we attach the hint without disturbing the underlying shape produced
    by ``backend.services.ipo_feed.list_upcoming_ipos``.

We stub ``list_upcoming_ipos`` at the module attribute import site used by
the executor (``backend.services.ipo_feed.list_upcoming_ipos``) so no
network traffic occurs. Mirrors the monkeypatch pattern used in
``tests/test_ipo_applications.py``.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.agents.tool_executor import _list_upcoming_ipos


# ── Stubs ──────────────────────────────────────────────────────────────


_NONEMPTY_FEED: dict[str, Any] = {
    "count": 2,
    "ipos": [
        {
            "name": "Tikona Infinet",
            "symbol": "TIKONA",
            "price_band": "125-132",
            "open_date": "2026-06-03",
            "close_date": "2026-06-05",
            "lot_size": 110,
            "issue_size": "₹1,200 cr",
            "type": "mainboard",
            "status": "open",
        },
        {
            "name": "BigBucks",
            "symbol": "BIGBUCKS",
            "price_band": "1000-1100",
            "open_date": "2026-06-04",
            "close_date": "2026-06-06",
            "lot_size": 100,
            "issue_size": "₹5,000 cr",
            "type": "mainboard",
            "status": "upcoming",
        },
    ],
    "source": "nse",
    "note": None,
    "cached": False,
}

_EMPTY_REACHABLE_FEED: dict[str, Any] = {
    "count": 0,
    "ipos": [],
    "source": "nse",
    "note": "No IPOs open or upcoming right now.",
    "cached": False,
}

_UNREACHABLE_FEED: dict[str, Any] = {
    "count": 0,
    "ipos": [],
    "source": "unreachable",
    "note": "Live IPO feed unreachable: connect timeout to nseindia.com",
    "cached": False,
}


def _run(coro):
    """Drive the async executor synchronously from sync test bodies.

    Uses a fresh event loop per call to stay isolated from any other
    loop pytest may have set up (and to play nicely with pytest-asyncio
    if it's installed).
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _patch_feed(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    """Replace ``list_upcoming_ipos`` at its source module so the
    executor's local ``from ... import`` resolves to the stub."""
    monkeypatch.setattr(
        "backend.services.ipo_feed.list_upcoming_ipos",
        lambda: dict(payload),
    )


# ── Tests ──────────────────────────────────────────────────────────────


def test_render_hint_attached_on_nonempty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_feed(monkeypatch, _NONEMPTY_FEED)

    result = _run(_list_upcoming_ipos({}, None, None, "u1"))

    # Hint is present and BYTE-IDENTICAL to the FE discriminator.
    assert result["data"]["_render_hint"] == "ipo_list_card"
    # success mirrors feed reachability.
    assert result["success"] is True
    # Underlying shape preserved (count / source / ipos passthrough).
    assert result["data"]["count"] == 2
    assert result["data"]["source"] == "nse"
    assert len(result["data"]["ipos"]) == 2
    assert result["data"]["ipos"][0]["symbol"] == "TIKONA"
    # logiccard stays None — list view is not a confirmable card.
    assert result["logiccard"] is None


def test_render_hint_attached_on_empty_but_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty list is a REAL answer ("no live IPOs"), not an error —
    the card must still render so the FE shows the empty state with
    the note, not a fabricated row and not a plain-text fallback."""
    _patch_feed(monkeypatch, _EMPTY_REACHABLE_FEED)

    result = _run(_list_upcoming_ipos({}, None, None, "u1"))

    assert result["data"]["_render_hint"] == "ipo_list_card"
    # Reachable feed -> success True even when empty.
    assert result["success"] is True
    assert result["data"]["count"] == 0
    assert result["data"]["ipos"] == []
    assert result["data"]["source"] == "nse"
    # Note is preserved so the empty-state UI can show the honest message.
    assert result["data"]["note"] == "No IPOs open or upcoming right now."
    assert result["logiccard"] is None


def test_render_hint_attached_on_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreachable feed: hint MUST still be attached so the FE renders
    the card's unreachable state (with the honest note) — never falls
    back to plain text, never fabricates rows."""
    _patch_feed(monkeypatch, _UNREACHABLE_FEED)

    result = _run(_list_upcoming_ipos({}, None, None, "u1"))

    assert result["data"]["_render_hint"] == "ipo_list_card"
    # Unreachable -> success False (this is the existing contract).
    assert result["success"] is False
    assert result["data"]["source"] == "unreachable"
    assert result["data"]["count"] == 0
    assert result["data"]["ipos"] == []
    assert "unreachable" in (result["data"]["note"] or "").lower()
    assert result["logiccard"] is None


def test_underlying_feed_not_mutated_by_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defence-in-depth: the executor must attach the hint via a NEW
    dict (spread), not by mutating the feed payload in place. Other
    callers of ``list_upcoming_ipos`` (/ipo-calendar, get_ipo_details)
    rely on the upstream shape staying clean."""
    captured: dict[str, Any] = {}

    def _stub_feed() -> dict[str, Any]:
        # Hand back a fresh copy of the nonempty payload AND keep a
        # reference so we can re-inspect post-call.
        out = dict(_NONEMPTY_FEED)
        captured["ref"] = out
        return out

    monkeypatch.setattr(
        "backend.services.ipo_feed.list_upcoming_ipos", _stub_feed,
    )

    result = _run(_list_upcoming_ipos({}, None, None, "u1"))

    # Hint landed on the executor's returned dict.
    assert result["data"]["_render_hint"] == "ipo_list_card"
    # But the original payload the feed returned is untouched.
    assert "_render_hint" not in captured["ref"]
