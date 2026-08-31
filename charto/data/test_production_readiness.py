"""Regression tests for Charto's production admission and recovery seams."""
from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request

import pytest

import dataserver as server


def test_default_service_tier_matches_the_deployment() -> None:
    assert server.LLM_SERVICE_TIER in {"default", "priority"}
    if "CHARTO_LLM_SERVICE_TIER" not in server.environ:
        assert server.LLM_SERVICE_TIER == "default"


def test_data_gate_rejects_without_overcommitting(monkeypatch) -> None:
    monkeypatch.setattr(server, "_DATA_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(server, "_DATA_STATE",
                        {"active": 0, "peak": 0, "rejected": 0,
                         "completed": 0})
    assert server._data_slot_acquire(timeout=0)
    assert not server._data_slot_acquire(timeout=0)
    assert server._data_state()["active"] == 1
    assert server._data_state()["rejected"] == 1
    server._data_slot_release()
    assert server._data_state()["active"] == 0


def test_chat_gate_queues_a_second_wave(monkeypatch) -> None:
    monkeypatch.setattr(server, "_CHAT_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(server, "_CHAT_STATE",
                        {"active": 0, "peak": 0, "rejected": 0,
                         "completed": 0})
    monkeypatch.setattr(server, "_CHAT_QUEUE_TIMEOUT_S", 0.2)
    assert server._chat_slot_acquire()

    acquired = []
    waiter = threading.Thread(target=lambda: acquired.append(
        server._chat_slot_acquire()))
    waiter.start()
    server.time.sleep(0.02)
    server._chat_slot_release()
    waiter.join(timeout=1)

    assert acquired == [True]
    assert server._chat_state()["peak"] == 1
    server._chat_slot_release()


def test_tool_overload_is_explicit_and_retryable(monkeypatch) -> None:
    monkeypatch.setattr(server, "_data_slot_acquire", lambda *_a, **_k: False)
    out = server._run_tool("probe", lambda: {"ok": True}, {})
    assert out["retryable"] is True
    assert out["retry_after_s"] >= 1
    assert "busy" in out["error"].lower()


def _http_error(code: int, retry_after: str = "0",
                retry_after_ms: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after}
    if retry_after_ms is not None:
        headers["retry-after-ms"] = retry_after_ms
    return urllib.error.HTTPError(
        "https://example.invalid", code, "failed",
        headers, io.BytesIO(b"failed"))


def test_azure_retry_replays_only_a_pre_response_transient(monkeypatch) -> None:
    calls = []
    sentinel = object()

    def open_once(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429)
        return sentinel

    monkeypatch.setattr(urllib.request, "urlopen", open_once)
    monkeypatch.setattr(server.time, "sleep", lambda _s: None)
    req = urllib.request.Request("https://example.invalid")
    assert server._urlopen_with_retry(
        req, timeout=1, context=None, attempts=3) is sentinel
    assert len(calls) == 2


def test_azure_retry_does_not_replay_a_deterministic_client_error(monkeypatch) -> None:
    calls = []

    def always_bad(*_args, **_kwargs):
        calls.append(1)
        raise _http_error(400)

    monkeypatch.setattr(urllib.request, "urlopen", always_bad)
    req = urllib.request.Request("https://example.invalid")
    with pytest.raises(urllib.error.HTTPError, match="HTTP Error 400"):
        server._urlopen_with_retry(req, timeout=1, context=None, attempts=3)
    assert len(calls) == 1


def test_azure_retry_honours_millisecond_header() -> None:
    assert server._retry_after_seconds(
        _http_error(429, retry_after="", retry_after_ms="750"), 1) == 0.75


def test_backup_health_distinguishes_fresh_from_stale(tmp_path, monkeypatch) -> None:
    marker = tmp_path / "last.json"
    marker.write_text(json.dumps({"completed_at_epoch": 1_000,
                                  "blob": "backup/users/a.db.gz"}))
    monkeypatch.setattr(server, "_BACKUP_MARKER", marker)
    monkeypatch.setattr(server, "_BACKUP_MAX_AGE_S", 7_200)
    monkeypatch.setattr(server, "_REQUIRE_FRESH_BACKUP", True)
    fresh = server._backup_health(now=2_000)
    assert fresh["ok"] and fresh["fresh"]
    stale = server._backup_health(now=10_000)
    assert not stale["ok"] and not stale["fresh"]
    assert stale["age_s"] == 9_000


def test_health_route_is_not_classified_as_heavy_data() -> None:
    assert not server._is_heavy_http_path("/health")
    assert server._is_heavy_http_path("/indicator")
    assert server._is_heavy_http_path("/api/markets/ohlc")


def test_deep_health_makes_execution_part_of_readiness(monkeypatch) -> None:
    monkeypatch.setattr(server, "_backup_health", lambda: {"ok": True})
    monkeypatch.setattr(server, "AZURE_ENDPOINT", "https://example.invalid")
    monkeypatch.setattr(server, "AZURE_KEY", "secret")
    monkeypatch.setattr(server, "_REQUIRE_EXECUTION", False)
    monkeypatch.setattr(server.execution_bridge, "available",
                        lambda: (False, "execution engine unavailable"))

    code, payload = server._health_report(deep=True)

    assert code == 503
    assert payload["ready"] is False
    assert payload["checks"]["execution"]["checked"] is True
    assert payload["checks"]["execution"]["ok"] is False


# ── model fallback ─────────────────────────────────────────────────────────
#
# A deployment outage does not arrive as an HTTP error on the streaming path.
# Measured 2026-08-31: Azure accepted the connection, returned 200, and sent
# the fault in band as an SSE `error` event — so the pre-response retry never
# engaged and the turn died with a healthy sibling deployment available on the
# same endpoint. These pin the seam that now catches it.


def test_stream_error_is_read_from_where_the_api_actually_puts_it() -> None:
    # `ev["message"]` is what the code used to read, and the API has never set
    # it: the whole point of this parse is the nesting.
    assert server._stream_error_parts(
        {"type": "error",
         "error": {"type": "server_error", "message": "no healthy upstream"}},
    ) == ("server_error", "no healthy upstream")
    # `response.failed` nests it one level deeper again.
    assert server._stream_error_parts(
        {"type": "response.failed",
         "response": {"error": {"code": "rate_limit", "message": "slow down"}}},
    ) == ("rate_limit", "slow down")
    assert server._stream_error_parts({"type": "error"}) == ("", "")


def test_only_a_dead_deployment_demotes() -> None:
    """A bad request is not an outage. Retrying a 400 on a second model only
    asks it the same wrong question, and a 429 is capacity on a deployment
    that IS serving — `_urlopen_with_retry` backs off for that already."""
    assert server._llm_unhealthy(503, "")
    assert server._llm_unhealthy(502, "")
    assert server._llm_unhealthy(504, "")
    assert server._llm_unhealthy(None, "no healthy upstream")
    assert not server._llm_unhealthy(400, "invalid tool schema")
    assert not server._llm_unhealthy(429, "rate limit exceeded")
    assert not server._llm_unhealthy(401, "access denied")


def test_demotion_expires_and_stops_retrying_the_dead_arm(monkeypatch) -> None:
    monkeypatch.setattr(server, "LLM_DEPLOYMENT", "primary")
    monkeypatch.setattr(server, "LLM_FALLBACK", "backup")
    monkeypatch.setattr(server, "_llm_demoted_until", 0.0)

    assert server._model() == "primary"
    assert server._stream_models() == ["primary", "backup"]

    assert server._demote("no healthy upstream") == "backup"
    assert server._model() == "backup"
    # One attempt while demoted: a second call to the arm that just failed is
    # not a fallback, it is a 503 charged to every turn of the outage.
    assert server._stream_models() == ["backup"]

    monkeypatch.setattr(server, "_llm_demoted_until",
                        server.time.monotonic() - 1)
    assert server._model() == "primary"


def test_no_fallback_configured_fails_loudly(monkeypatch) -> None:
    monkeypatch.setattr(server, "LLM_DEPLOYMENT", "primary")
    monkeypatch.setattr(server, "LLM_FALLBACK", "")
    monkeypatch.setattr(server, "_llm_demoted_until", 0.0)
    assert server._stream_models() == ["primary"]
    assert server._demote("down") == ""
    assert server._model() == "primary"
