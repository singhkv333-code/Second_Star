"""byok.py: the key never leaves sealed, each provider gets its own dialect,
and a reply quoting Pivot's instructions is caught. No network: the HTTP
layer is replaced by canned streams in each provider's documented shape."""
import io
import json
import sqlite3
import threading

import pytest

import byok


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _db(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTO_BYOK_SECRET", "unit-test-secret-" + "0" * 32)
    monkeypatch.setattr(byok, "_aead_cache", None)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
    byok.bind(con, threading.Lock())
    yield con


def _sse(events, named=False):
    out = b""
    for e in events:
        if named:
            out += f"event: {e['type']}\n".encode()
        out += b"data: " + json.dumps(e).encode() + b"\n\n"
    return out


WIRE = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": [{"type": "input_text", "text": "look"},
                                     {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]},
        {"type": "function_call", "call_id": "c1", "name": "get_levels",
         "arguments": "{\"draw\": true}", "_sig": "SIG"},
        {"type": "function_call_output", "call_id": "c1", "output": "{\"levels\": [1]}"}]
TOOLS = [{"type": "function", "name": "get_levels", "description": "d",
          "parameters": {"type": "object", "properties": {}}}]


def _capture(monkeypatch, body=b""):
    cap = {}

    def fake(url, headers, payload=None, **k):
        cap.update(url=url, headers=headers, body=payload)
        return _Resp(body)
    monkeypatch.setattr(byok, "_http", fake)
    return cap


def test_sealed_and_bound_to_the_account():
    blob = byok._seal(7, "openai", "sk-test-abcdefghijklmnopqrstuvwx")
    assert b"sk-test" not in blob
    assert byok._open(7, "openai", blob) == "sk-test-abcdefghijklmnopqrstuvwx"
    with pytest.raises(byok.Refused):
        byok._open(8, "openai", blob)          # another user's row
    with pytest.raises(byok.Refused):
        byok._open(7, "anthropic", blob)       # another provider's row


def test_state_never_carries_the_secret(_db, monkeypatch):
    monkeypatch.setattr(byok, "_list_models", lambda p, a: [{"id": "gpt-5.1", "label": "gpt-5.1"}])
    key = "sk-proj-" + "q" * 40
    st = byok.connect_key(1, "openai", key)
    assert key not in json.dumps(st) and key[:16] not in json.dumps(st)
    p = next(x for x in st["providers"] if x["id"] == "openai")
    assert p["connected"] and p["hint"] == "qqqq" and p["model"] == "gpt-5.1"


def test_malformed_key_is_refused_without_a_call(monkeypatch):
    monkeypatch.setattr(byok, "_list_models", lambda *a: pytest.fail("called the provider"))
    with pytest.raises(byok.Refused, match="an Anthropic key"):
        byok.connect_key(1, "anthropic", "hello")


def test_scrub():
    assert "sk-proj" not in byok.scrub("bad key sk-proj-abcdefghijklmnop1234 given")


def test_anthropic_dialect(monkeypatch):
    cap = _capture(monkeypatch)
    list(byok.Engine(1, "anthropic", "sk-ant-x", "claude-x").stream(WIRE, TOOLS, True))
    b = cap["body"]
    assert b["system"] == "SYS" and cap["headers"]["x-api-key"] == "sk-ant-x"
    assert [m["role"] for m in b["messages"]] == ["user", "assistant", "user", "assistant", "user"]
    assert b["messages"][3]["content"][0]["type"] == "tool_use"
    assert b["messages"][4]["content"][0]["type"] == "tool_result"


def test_gemini_dialect_keeps_the_signature_and_the_key_out_of_the_url(monkeypatch):
    cap = _capture(monkeypatch)
    list(byok.Engine(1, "google", "AIzaSECRET", "gemini-x").stream(WIRE, TOOLS, False))
    assert "AIzaSECRET" not in cap["url"] and cap["headers"]["x-goog-api-key"] == "AIzaSECRET"
    call = cap["body"]["contents"][3]["parts"][0]
    assert call["functionCall"]["name"] == "get_levels" and call["thoughtSignature"] == "SIG"
    assert cap["body"]["toolConfig"]["functionCallingConfig"]["mode"] == "NONE"


def test_openai_dialect_stores_nothing(monkeypatch):
    cap = _capture(monkeypatch)
    list(byok.Engine(1, "openai", "sk-x", "gpt-x").stream(WIRE, TOOLS, True))
    assert cap["body"]["store"] is False
    assert all("_sig" not in w for w in cap["body"]["input"])


def test_streams_become_responses_events(monkeypatch):
    anth = [{"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
            {"type": "content_block_start", "index": 1,
             "content_block": {"type": "tool_use", "id": "t1", "name": "get_levels"}},
            {"type": "content_block_delta", "index": 1,
             "delta": {"type": "input_json_delta", "partial_json": "{\"draw\": true}"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "message_delta", "usage": {"output_tokens": 5}}]
    _capture(monkeypatch, _sse(anth, named=True))
    ev = list(byok.Engine(1, "anthropic", "k", "m").stream([], [], True))
    assert ev[0] == {"type": "response.output_text.delta", "delta": "Hi"}
    assert ev[1]["item"]["name"] == "get_levels" and json.loads(ev[1]["item"]["arguments"]) == {"draw": True}
    assert ev[-1]["response"]["usage"] == {"input_tokens": 10, "output_tokens": 5}

    chat = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c", "function": {"name": "get_trend", "arguments": "{\"a\""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ":1}"}}]}}]}]
    _capture(monkeypatch, _sse(chat))
    ev = list(byok.Engine(1, "openrouter", "k", "m").stream([], [], True))
    assert ev[0]["item"]["arguments"] == "{\"a\":1}"


def test_leak_guard():
    rules = ("When the user asks about a level you must call get_levels first and quote the "
             "returned price exactly never round it and always show the base rate and sample size")
    ordinary = byok.LeakGuard(rules)
    assert not ordinary.feed("The stock sits near 2,450 support; RSI is 61 and rising. " * 4) \
        and not ordinary.check()
    quoting = byok.LeakGuard(rules)
    quoting.feed("Sure. " + rules)
    assert quoting.check()


def test_rate_limit(monkeypatch):
    monkeypatch.setattr(byok, "TURNS_PER_10MIN", 2)
    byok._hits.clear()
    byok._admit(5)
    byok._admit(5)
    with pytest.raises(byok.Refused):
        byok._admit(5)
