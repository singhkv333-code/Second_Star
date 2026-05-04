"""HTTP client for the live Pivot chatbot endpoint.

The chat is server-stateless per request: the client carries the conversation
history as the ``messages`` array. So ``new_conversation`` returns an opaque
token that the eval uses to track state locally; ``send`` updates that state
and posts to ``/chat``.

Tool detection is derived from the response shape because the chat router
returns dedicated fields for different tool families (``tool_call``,
``screen_data``, ``expr_backtest_data``, ``chart_data``, ``backtest_data``,
``logiccard``). We synthesise a uniform ``tools_called`` list so judges can
reason about it without coupling to that response shape.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Settings, get_settings


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class ChatResponse:
    text: str
    tools_called: list[ToolCall]
    intent: str | None
    raw: dict
    latency_ms: int


class ChatbotClient:
    """Talks to the live Pivot FastAPI backend."""

    def __init__(self, settings: Settings | None = None, *, timeout: float = 90.0):
        self.settings = settings or get_settings()
        self._token: str | None = self.settings.pivot_bearer_token or None
        self._client = httpx.Client(
            base_url=self.settings.pivot_base_url,
            timeout=timeout,
        )
        # Per-conversation message log, keyed by an internal id we mint.
        self._conversations: dict[str, list[dict]] = {}
        self._next_conv = 0

    # ---- lifecycle -------------------------------------------------

    def __enter__(self):
        if not self._token:
            self._login()
        return self

    def __exit__(self, *exc):
        self._client.close()

    def close(self):
        self._client.close()

    # ---- auth ------------------------------------------------------

    def _login(self) -> None:
        creds = {
            "email": self.settings.pivot_login_email,
            "password": self.settings.pivot_login_password,
        }
        resp = self._client.post("/auth/login", json=creds)
        if resp.status_code != 200:
            # Maybe the user doesn't exist yet — try register, then login.
            reg = self._client.post(
                "/auth/register",
                json={**creds, "full_name": "Eval Smoke"},
            )
            if reg.status_code not in (200, 201):
                raise RuntimeError(
                    f"login + register both failed: "
                    f"{resp.status_code}/{reg.status_code} — {reg.text[:200]}"
                )
            self._token = reg.json()["access_token"]
            return
        self._token = resp.json()["access_token"]

    @property
    def _auth_header(self) -> dict:
        if not self._token:
            self._login()
        return {"Authorization": f"Bearer {self._token}"}

    # ---- conversations --------------------------------------------

    def new_conversation(self) -> str:
        cid = f"conv-{self._next_conv}"
        self._next_conv += 1
        self._conversations[cid] = []
        return cid

    def send(self, conversation_id: str, message: str) -> ChatResponse:
        history = self._conversations[conversation_id]
        history.append({"role": "user", "content": message})

        started = time.monotonic()
        try:
            resp = self._client.post(
                "/chat",
                json={"messages": history, "include_portfolio_context": False},
                headers=self._auth_header,
            )
        except httpx.RequestError as e:
            raise RuntimeError(f"chat HTTP call failed: {e}") from None

        latency_ms = int((time.monotonic() - started) * 1000)

        if resp.status_code == 401:
            # Token may have expired — re-login once and retry.
            self._token = None
            self._login()
            resp = self._client.post(
                "/chat",
                json={"messages": history, "include_portfolio_context": False},
                headers=self._auth_header,
            )

        if resp.status_code != 200:
            raise RuntimeError(
                f"chat returned {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()
        text = (data.get("response") or "").strip()
        history.append({"role": "assistant", "content": text})
        tools = _extract_tools(data)
        return ChatResponse(
            text=text,
            tools_called=tools,
            intent=data.get("intent"),
            raw=_strip_secrets(data),
            latency_ms=latency_ms,
        )


# ---- Tool extraction --------------------------------------------------


def _extract_tools(data: dict) -> list[ToolCall]:
    """Synthesise a uniform tool list from the response's signal fields.

    Pivot's chat router doesn't echo a single tools_used array; different
    pathways populate different keys. Treating the presence of a data block
    as proof that a tool fired keeps the eval grounded in observable behaviour.
    """
    tools: list[ToolCall] = []

    tc = data.get("tool_call")
    if isinstance(tc, dict) and tc.get("name"):
        tools.append(ToolCall(name=str(tc["name"]), args=tc.get("arguments") or {}))

    if data.get("screen_data"):
        tools.append(ToolCall(name="run_expression_screen",
                              args={"expression": data["screen_data"].get("expression")}))
    if data.get("expr_backtest_data"):
        tools.append(ToolCall(name="run_expression_backtest",
                              args={"expression": data["expr_backtest_data"].get("expression")}))
    if data.get("chart_data"):
        tools.append(ToolCall(name="run_compare", args={}))
    if data.get("backtest_data"):
        tools.append(ToolCall(name="run_backtest", args={}))
    if data.get("logiccard"):
        tools.append(ToolCall(name="logiccard_emitted",
                              args={"strategy_type": data["logiccard"].get("strategy_type")}))

    return tools


def _strip_secrets(data: dict) -> dict:
    """Defensive scrub before persisting raw payloads — never let tokens land in run files."""
    SAFE = dict(data)
    for k in list(SAFE):
        if any(s in k.lower() for s in ("token", "secret", "password", "auth")):
            SAFE[k] = "<redacted>"
    return SAFE
