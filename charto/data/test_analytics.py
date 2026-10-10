"""Server analytics: off without a token, never throws, and the events the
product depends on carry the right shape. A fake client stands in for
PostHog, so nothing leaves the machine.

Run: ../../pivot/.venv/bin/python -m pytest test_analytics.py -q
"""
from __future__ import annotations

import analytics
import dataserver as server


class _Fake:
    def __init__(self):
        self.events, self.errors = [], []

    def capture(self, event, distinct_id=None, properties=None):
        self.events.append((event, distinct_id, properties or {}))

    def capture_exception(self, exc, distinct_id=None, properties=None):
        self.errors.append((type(exc).__name__, distinct_id, properties or {}))


def _with_fake(monkeypatch):
    fake = _Fake()
    monkeypatch.setattr(analytics, "_client", fake)
    monkeypatch.setattr(analytics, "_key", "phc_test")
    monkeypatch.setattr(analytics, "_host", "https://us.i.posthog.com")
    return fake


def test_off_without_a_token(monkeypatch):
    monkeypatch.setattr(analytics, "_client", None)
    monkeypatch.delenv("POSTHOG_PROJECT_TOKEN", raising=False)
    assert analytics.init() is False
    assert analytics.config() == {}
    analytics.capture(1, "anything")            # a no-op, not an error


def test_capture_shapes_and_never_raises(monkeypatch):
    fake = _with_fake(monkeypatch)
    analytics.capture(7, "chat_turn", {"mode": "chat"})
    analytics.capture(None, "plan_limit_hit")
    ev, who, props = fake.events[0]
    assert (ev, who, props["surface"], props["mode"]) == ("chat_turn", "charto:7", "charto", "chat")
    assert fake.events[1][2]["$process_person_profile"] is False   # anonymous: no person
    assert analytics.config() == {"key": "phc_test", "host": "https://us.i.posthog.com"}

    class _Boom:
        def capture(self, *a, **k):
            raise RuntimeError("posthog down")
    monkeypatch.setattr(analytics, "_client", _Boom())
    analytics.capture(1, "x")                   # swallowed


def test_signup_and_login_are_captured(monkeypatch):
    fake = _with_fake(monkeypatch)
    monkeypatch.setattr(server, "_auth_signup", lambda b: (200, {"user": {"id": 41}}))
    monkeypatch.setattr(server, "_auth_login", lambda b: (401, {"error": "nope"}))
    monkeypatch.setattr(server, "_auth_google", lambda b: (200, {"user": {"id": 42}}))
    h = object.__new__(server.Handler)
    assert h._account_post("/auth/signup", {})[0] == 200
    assert h._account_post("/auth/login", {})[0] == 401          # failures are not events
    assert h._account_post("/auth/google", {})[0] == 200
    got = [(e, w, p["method"]) for e, w, p in fake.events]
    assert got == [("user_signed_up", "charto:41", "email"), ("user_logged_in", "charto:42", "google")]


def test_a_chat_turn_reports_cost_and_tools(monkeypatch):
    fake = _with_fake(monkeypatch)
    server._req.user = (5, "a@b.c")
    server._req.chat_mode = "chat"
    server._req.engine = None
    server._req.symbol = "HDFCBANK"
    h = object.__new__(server.Handler)
    done = {"type": "done", "usage": {"input_tokens": 1200, "output_tokens": 80},
            "tools_used": [{"name": "get_bars"}, {"name": "workspace"}],
            "view_ops": [{"kind": "workspace", "ops": [{"op": "open"}, {"op": "write"}]}],
            "cards": [{"kind": "screen"}]}
    h._chat_event(done, False, 1.5, 4.25, False)
    ev, who, p = fake.events[-1]
    assert (ev, who, p["ok"], p["tool_count"], p["workspace_ops"]) == ("chat_turn", "charto:5", True, 2, 2)
    assert (p["input_tokens"], p["first_text_s"], p["total_s"], p["cards"]) == (1200, 1.5, 4.25, ["screen"])
    h._chat_event({"type": "done", "error": "model timed out"}, True, None, 30.0, True)
    assert fake.events[-1][2]["ok"] is False and "timed out" in fake.events[-1][2]["error"]
