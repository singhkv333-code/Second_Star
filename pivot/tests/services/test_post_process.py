"""Unit tests for the post-process safety net.

This is the regression alarm — if any of these fail, a leak source has been
re-introduced upstream and the safety net is the only thing keeping the user
from seeing it.
"""
from __future__ import annotations

import pytest

from backend.services.chat_service import _post_process, _GENERIC_FALLBACK


def test_strips_unclosed_tool_call_block():
    raw = 'Here is your data <TOOL_CALL>{"name":"get_ohlc","arguments":{"symbol":"INFY"}'
    out, sanitised = _post_process(raw)
    assert "<TOOL_CALL>" not in out
    assert sanitised is True


def test_strips_closed_tool_call_block():
    raw = 'Result: <TOOL_CALL>{"name":"get_quote"}</TOOL_CALL> got it'
    out, sanitised = _post_process(raw)
    assert "<TOOL_CALL>" not in out
    assert "Result:" in out
    assert "got it" in out
    assert sanitised is True


def test_strips_uppercase_placeholders():
    raw = "TCS last traded at <LTP>. Strike: <STRIKE_LONG>."
    out, sanitised = _post_process(raw)
    assert "<LTP>" not in out
    assert "<STRIKE" not in out
    assert sanitised is True


def test_keeps_normal_text_unchanged():
    raw = "Reliance closed at ₹2,945, up 1.2% today."
    out, sanitised = _post_process(raw)
    assert out == raw
    assert sanitised is False


def test_replaces_canned_pitch_with_fallback():
    """Even if Sarvam regresses to the legacy pitch we don't ship it."""
    raw = ("Execute orders on Zerodha. Build capital protection and income "
           "products. Automate SIP and strategy rules. Analyse your portfolio.")
    out, sanitised = _post_process(raw)
    assert "Execute orders on Zerodha" not in out
    assert sanitised is True


def test_empty_response_falls_back():
    out, sanitised = _post_process("")
    assert out == _GENERIC_FALLBACK
    assert sanitised is True


def test_whitespace_only_response_falls_back():
    out, sanitised = _post_process("   \n\t  ")
    assert out == _GENERIC_FALLBACK
    assert sanitised is True
