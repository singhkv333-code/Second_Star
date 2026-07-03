"""Tests for backend.news_events.parsing.event_spec_parser.

LLM is mocked at the get_llm_client boundary so the tests don't
touch the live Azure / OpenAI surface.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from backend.news_events.parsing import event_spec_parser as parser_mod


@dataclass
class _FakeResponse:
    content: str


class _FakeClient:
    def __init__(self, contents):
        if isinstance(contents, str):
            contents = [contents]
        self._contents = list(contents)
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        idx = min(self.calls - 1, len(self._contents) - 1)
        return _FakeResponse(self._contents[idx])


def _ok_tier1_json() -> str:
    return json.dumps(
        {
            "description": "RBI cuts the repo rate",
            "tier": "tier1",
            "keyword_set": {
                "must_have_one": ["RBI", "repo"],
                "must_have_one_of": [],
                "must_not_have": ["speculate"],
            },
            "resolution_criteria": {
                "primary_sources": ["rbi_press_releases"],
                "min_secondary_confirmations": 0,
                "min_confidence": 0.85,
                "prediction_market_threshold": None,
                "conflict_policy": "fire",
            },
            "retraction_policy": {
                "safety_window_minutes": 60,
                "action": "cancel_and_alert",
            },
            "needs_disambiguation": False,
        }
    )


def _ok_tier3_json() -> str:
    return json.dumps(
        {
            "description": "Trump wins the 2028 US presidential election",
            "tier": "tier3",
            "keyword_set": {
                "must_have_one": ["Trump", "wins", "election"],
                "must_have_one_of": [],
                "must_not_have": ["rumour", "speculate"],
            },
            "resolution_criteria": {
                "primary_sources": [],
                "min_secondary_confirmations": 1,
                "min_confidence": 0.85,
                "prediction_market_threshold": None,
                "conflict_policy": "hold",
            },
            "retraction_policy": {
                "safety_window_minutes": 240,
                "action": "cancel_pending_approvals",
            },
            "needs_disambiguation": False,
        }
    )


def test_parser_tier1_happy_path(monkeypatch):
    fake = _FakeClient(_ok_tier1_json())
    monkeypatch.setattr(parser_mod, "get_llm_client", lambda: fake)
    parsed = asyncio.run(parser_mod.parse_event_spec("RBI cuts the repo rate"))
    assert parsed.tier == "tier1"
    assert parsed.needs_disambiguation is False
    assert "rbi_press_releases" in parsed.resolution_criteria.primary_sources
    assert fake.calls == 1


def test_parser_tier3_forces_disambiguation(monkeypatch):
    # Server returns needs_disambiguation=False but tier=tier3 — the
    # parser should override and force True.
    fake = _FakeClient(_ok_tier3_json())
    monkeypatch.setattr(parser_mod, "get_llm_client", lambda: fake)
    parsed = asyncio.run(parser_mod.parse_event_spec("Trump wins 2028"))
    assert parsed.tier == "tier3"
    assert parsed.needs_disambiguation is True


def test_parser_retries_on_validation_error(monkeypatch):
    bad = json.dumps({"description": "hi", "tier": "tier4"})
    good = _ok_tier1_json()
    fake = _FakeClient([bad, good])
    monkeypatch.setattr(parser_mod, "get_llm_client", lambda: fake)

    parsed = asyncio.run(parser_mod.parse_event_spec("RBI cuts rate"))
    assert parsed.tier == "tier1"
    assert fake.calls == 2  # one retry


def test_parser_handles_fenced_json(monkeypatch):
    content = "```json\n" + _ok_tier1_json() + "\n```"
    fake = _FakeClient(content)
    monkeypatch.setattr(parser_mod, "get_llm_client", lambda: fake)
    parsed = asyncio.run(parser_mod.parse_event_spec("RBI cuts the repo rate"))
    assert parsed.tier == "tier1"


def test_parser_raises_on_garbage(monkeypatch):
    fake = _FakeClient(["not json", "still not json"])
    monkeypatch.setattr(parser_mod, "get_llm_client", lambda: fake)
    with pytest.raises(parser_mod.ParserError):
        asyncio.run(parser_mod.parse_event_spec("rbi"))


def test_parser_rejects_short_text():
    with pytest.raises(parser_mod.ParserError):
        asyncio.run(parser_mod.parse_event_spec("abc"))
