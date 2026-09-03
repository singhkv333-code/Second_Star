"""Pivotted — a research and analysis chat for Indian equities, on :5175.

What this is: Charto's chat loop with the chart taken out, the ink tools
removed, and the fundamentals half rebuilt against the whole listed universe
instead of the 500 symbols we store bars for.

What it deliberately is NOT: a router. Pivot decides what a turn means before
the model sees it — intent classification, reply-class budgets, special-case
detectors, redirects — across 12,593 lines, and then spends 20,500 tokens of
system prompt telling the model what it may do about the decision. That
machinery earns its cost when a turn can COMMIT something: register an order,
arm an automation, deploy a strategy. A wrong guess there is a wrong trade.

Nothing here commits anything. Every tool is a read. So the whole apparatus is
replaced by the shape the model is already good at — a short brief, a well
described tool table, and a loop that lets it choose — and what would have
been spent on routing is spent on running the tools it chooses CONCURRENTLY
instead.

Run:  pivotted/run.sh          (or pivot/.venv/bin/python pivotted/server.py)
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tools as T                      # noqa: E402
from prompt import system_prompt       # noqa: E402

PORT = int(os.environ.get("PIVOTTED_PORT", "5175"))

# Same Azure deployment Charto talks to; credentials come from the same place
# so there is one key to rotate, not two.
AZURE_ENDPOINT = T.ds.AZURE_ENDPOINT
AZURE_KEY = T.ds.AZURE_KEY
LLM_DEPLOYMENT = T.ds.LLM_DEPLOYMENT
LLM_SERVICE_TIER = T.ds.LLM_SERVICE_TIER

# Charto reasons at "medium" because it decides GEOMETRY — which swing is the
# anchor, whether a line's touches count, what a pattern is worth. Those are
# judgement calls made from a wall of numbers.
#
# Research turns are not that shape. The judgement is in the reply text, and
# the decision the reasoning budget actually buys — which tool, which fields —
# is nearly always obvious from a well-described tool table. Measured on the
# first eval: ~6s of the ~6.5s per round was the model, against 1.27s average
# for the tools it was deciding between.
#
# So the default drops to "low" here, overridable when a build needs it back.
LLM_EFFORT = (os.environ.get("PIVOTTED_LLM_EFFORT") or "low").strip()

# Four rounds, for a different reason than Charto's four. Charto needed the
# fourth because drawing is a three-hop path (explain → anchor → draw) and a
# compound turn could run out mid-drawing. Nothing here draws; the fourth
# round is for the genuinely layered research turn — screen, then read the
# names that came back, then check what the web says about the outlier — which
# is exactly the shape a router would have had to anticipate and this does not.
MAX_ROUNDS = 4
_MAX_OUTPUT = 3000


def _ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _payload(wire: list[dict], allow_tools: bool, stream: bool) -> dict:
    return {
        "model": LLM_DEPLOYMENT,
        "input": wire,
        "tools": T.TOOLS,
        "tool_choice": "auto" if allow_tools else "none",
        "max_output_tokens": _MAX_OUTPUT,
        "reasoning": {"effort": LLM_EFFORT},
        "service_tier": LLM_SERVICE_TIER,
        **({"stream": True} if stream else {}),
    }


def _request(wire: list[dict], allow_tools: bool, stream: bool):
    return urllib.request.Request(
        f"{AZURE_ENDPOINT}/responses",
        data=json.dumps(_payload(wire, allow_tools, stream)).encode(),
        headers={"api-key": AZURE_KEY, "Content-Type": "application/json"},
        method="POST")


def _post(wire: list[dict], allow_tools: bool = True) -> dict:
    with urllib.request.urlopen(_request(wire, allow_tools, False),
                                timeout=120, context=_ssl_ctx()) as r:
        return json.loads(r.read())


# Idle deadline on the model's own stream, not a budget for the turn: it is a
# socket timeout, so it fires only after this long with NOTHING arriving. A
# turn that streams steadily for ten minutes is fine; one that goes quiet is
# the failure worth reporting, and it has happened (Azure stalled mid-answer
# on a two-turn transcript).
_LLM_STREAM_IDLE = 180


def _post_stream(wire: list[dict], allow_tools: bool = True):
    with urllib.request.urlopen(_request(wire, allow_tools, True),
                                timeout=_LLM_STREAM_IDLE, context=_ssl_ctx()) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if not body or body == "[DONE]":
                continue
            try:
                yield json.loads(body)
            except json.JSONDecodeError:
                continue


# The composer's tagged context, as Pivot writes it into the prompt.
#
# Pivot's chat takes an `attachments` array beside `messages` and renders one
# line per item ahead of the user's own words (`_fmt_attachment` in
# `backend/routers/chat.py`); its stock-page ask bar sends exactly one, the
# company whose page is open. Reading only `messages` meant that array arrived
# and was discarded, so "is it expensive?" typed on 3M India's page reached
# the model as a question about nothing and the first tool call had to guess a
# symbol — which, on a research build whose whole risk is answering for the
# wrong company, is the one failure that looks like an answer.
#
# Only `security` is understood here. Positions, baskets and agents are
# Pivot's commit-side context and this build has no use for them; an
# attachment it cannot read is skipped rather than half-rendered.
_MAX_ATTACH_FIELD = 120


def _attachment_line(att: dict) -> str | None:
    """One human-readable line for a tagged security, or None."""
    if not isinstance(att, dict) or str(att.get("kind", "")).lower() != "security":
        return None
    sym = str(att.get("symbol") or "")[:_MAX_ATTACH_FIELD].strip().upper()
    if not sym:
        return None
    name = str(att.get("name") or "")[:_MAX_ATTACH_FIELD].strip()
    return f"- Security: {sym}" + (f" ({name})" if name else "")


def _apply_attachments(messages: list[dict], attachments) -> list[dict]:
    """Prefix the LAST user turn with the tagged context, verbatim Pivot.

    The wording is Pivot's, not a paraphrase: it is what its model has been
    answering against, and two builds describing the same envelope in two
    voices is how they start behaving differently on the same input. Only the
    last user message is wrapped — an earlier turn's context is already in
    what the assistant replied.
    """
    if not isinstance(attachments, list) or not attachments:
        return messages
    lines = [ln for ln in (_attachment_line(a) for a in attachments) if ln]
    if not lines:
        return messages
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            block = "\n".join(lines)
            out[i] = dict(out[i], content=(
                "The user attached the following context to this message "
                "(tagged via the composer). Treat these as the specific "
                "subject(s) being discussed — resolve pronouns like "
                "'it'/'this' to them, and use their exact symbols/ids when "
                "calling tools:\n"
                f"{block}\n\n"
                f"User message:\n{out[i].get('content') or ''}"
            ))
            break
    return out


def _wire_messages(messages: list[dict]) -> list[dict]:
    """History → Responses-API input items."""
    out = []
    for m in messages:
        role = m.get("role")
        text = (m.get("content") or "").strip()
        if role not in ("user", "assistant") or not text:
            continue
        out.append({"role": role, "content": text})
    return out


def _collect(data: dict) -> tuple[list[dict], str]:
    """(tool calls, prose) out of one non-streamed response."""
    calls, parts = [], []
    for item in data.get("output", []):
        kind = item.get("type")
        if kind == "function_call":
            calls.append({"id": item.get("call_id"), "name": item.get("name"),
                          "raw": item.get("arguments"),
                          "args": T.parse_args(item.get("arguments"))})
        elif kind == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
    return calls, "".join(parts)


def _feed(wire: list[dict], calls: list[dict], results: list[dict]) -> None:
    """Append this round's calls and their results to the wire, in order."""
    for call, result in zip(calls, results):
        wire.append({"type": "function_call", "call_id": call["id"],
                     "name": call["name"], "arguments": call["raw"]})
        wire.append({"type": "function_call_output", "call_id": call["id"],
                     "output": json.dumps(result, default=str)})


def chat(messages: list[dict]) -> dict:
    """One turn, tools run concurrently per round."""
    if not AZURE_ENDPOINT or not AZURE_KEY:
        return {"error": T.ds._creds_error()}
    block = system_prompt()
    wire: list[dict] = [{"role": "system", "content": block}]
    wire += _wire_messages(messages)

    trace: list[dict] = []
    tok_in = tok_out = 0
    for rnd in range(MAX_ROUNDS):
        # On the last round the tools are withdrawn so the model must answer
        # from what it has. Running out of rounds otherwise surfaces a
        # dead-end apology on top of perfectly good tool results.
        data = _post(wire, allow_tools=rnd < MAX_ROUNDS - 1)
        u = data.get("usage", {})
        tok_in += u.get("input_tokens") or 0
        tok_out += u.get("output_tokens") or 0
        calls, text = _collect(data)
        if not calls:
            return {"text": text or "(empty reply)",
                    "usage": {"input_tokens": tok_in, "output_tokens": tok_out},
                    "tools_used": trace, "context_preview": block,
                    "rounds": rnd + 1}
        t0 = time.time()
        results = T.run_round(calls)
        took = round(time.time() - t0, 2)
        for call, result in zip(calls, results):
            trace.append({"name": call["name"], "args": call["args"],
                          "ok": "error" not in result})
        logging.info("pivotted round %d: %d call(s) in %.2fs — %s", rnd + 1,
                     len(calls), took, ", ".join(c["name"] for c in calls))
        _feed(wire, calls, results)

    return {"text": "I couldn't finish that lookup — try narrowing the question.",
            "usage": {"input_tokens": tok_in, "output_tokens": tok_out},
            "tools_used": trace, "context_preview": block}


def chat_stream(messages: list[dict]):
    """Same loop, yielding SSE frames. Only prose is worth streaming.

    Tool calls arrive as complete items and mean nothing half-built, so every
    round streams but only a round that produces prose shows anything — which
    is the last one. The user sees the answer being written rather than
    waiting for the whole tool chain.
    """
    if not AZURE_ENDPOINT or not AZURE_KEY:
        yield {"type": "error", "error": T.ds._creds_error()}
        return
    block = system_prompt()
    wire: list[dict] = [{"role": "system", "content": block}]
    wire += _wire_messages(messages)
    trace: list[dict] = []
    tok_in = tok_out = 0

    for rnd in range(MAX_ROUNDS):
        calls: dict[str, dict] = {}
        text_parts: list[str] = []
        for ev in _post_stream(wire, allow_tools=rnd < MAX_ROUNDS - 1):
            kind = ev.get("type", "")
            if kind == "response.output_text.delta":
                d = ev.get("delta") or ""
                if d:
                    text_parts.append(d)
                    yield {"type": "delta", "text": d}
            elif kind == "response.output_item.done":
                item = ev.get("item") or {}
                if item.get("type") == "function_call":
                    calls[item.get("call_id")] = {
                        "id": item.get("call_id"), "name": item.get("name"),
                        "raw": item.get("arguments"),
                        "args": T.parse_args(item.get("arguments"))}
            elif kind == "response.completed":
                u = (ev.get("response") or {}).get("usage") or {}
                tok_in += u.get("input_tokens") or 0
                tok_out += u.get("output_tokens") or 0

        ordered = list(calls.values())
        if not ordered:
            yield {"type": "done", "text": "".join(text_parts),
                   "usage": {"input_tokens": tok_in, "output_tokens": tok_out},
                   "tools_used": trace}
            return
        yield {"type": "tools", "names": [c["name"] for c in ordered]}
        results = T.run_round(ordered)
        done = []
        for call, result in zip(ordered, results):
            ok = "error" not in result
            trace.append({"name": call["name"], "args": call["args"], "ok": ok})
            done.append({"name": call["name"], "ok": ok,
                         "error": None if ok else str(result.get("error"))})
        # Per ROUND, not per turn: a four-round turn that only reported
        # outcomes at the end would leave the first round's tools showing as
        # still running for the whole answer.
        yield {"type": "tools_done", "calls": done}
        _feed(wire, ordered, results)

    yield {"type": "done",
           "text": "I couldn't finish that lookup — try narrowing the question.",
           "usage": {"input_tokens": tok_in, "output_tokens": tok_out},
           "tools_used": trace}


def chat_stream_pivot(messages: list[dict]):
    """The same turn, in Pivot's SSE dialect, so its chat tab can point here.

    Pivot's `ChatDemo.tsx` is 2,990 lines and speaks a fixed event union —
    start / tool_start / tool_done / delta / replace / error / done, with the
    final text under `response`. Re-shaping THIS end is a few lines; teaching
    that component a second dialect is not, and a de-wiring meant to be
    reversible should not leave edits scattered through the surface it
    borrowed.

    Fields Pivot's component also accepts — logiccard, raw_data — are
    deliberately never emitted. They are how a card becomes committable, and
    there is nothing here to commit.
    """
    t0 = time.time()
    yield {"type": "start"}
    names: list[str] = []
    try:
        for frame in chat_stream(messages):
            kind = frame.get("type")
            if kind == "delta":
                yield {"type": "delta", "text": frame.get("text", "")}
            elif kind == "tools":
                for n in frame.get("names", []):
                    names.append(n)
                    yield {"type": "tool_start", "name": n}
            elif kind == "tools_done":
                for call in frame.get("calls", []):
                    yield {"type": "tool_done", "name": call.get("name"),
                           "ok": bool(call.get("ok")),
                           "error": call.get("error")}
            elif kind == "error":
                yield {"type": "error", "message": frame.get("error", "")}
                return
            elif kind == "done":
                yield {"type": "done", "response": frame.get("text", ""),
                       "tools_called": [c.get("name")
                                        for c in frame.get("tools_used", [])],
                       "latency_ms": int((time.time() - t0) * 1000),
                       "usage": frame.get("usage")}
                return
    except Exception as exc:                        # noqa: BLE001
        logging.exception("pivotted: pivot-dialect stream failed")
        # Name what failed. "The read operation timed out" is a socket's
        # phrase, and a reader looking at half an answer cannot tell from it
        # whether the data was wrong, the server died, or the model went
        # quiet — which is the only one of the three that actually happened.
        detail = (f"the model sent nothing for {_LLM_STREAM_IDLE}s"
                  if isinstance(exc, TimeoutError)
                  else str(exc) or exc.__class__.__name__)
        yield {"type": "error", "message": f"the answer was cut off — {detail}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "pivotted"

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        # Authorization matters even though nothing here reads it: Pivot's
        # chat component attaches a Bearer token whenever one is in
        # localStorage, and a preflight that does not allow the header fails
        # the request in the browser — while curl, which sends no preflight,
        # keeps working. That asymmetry is worth a header.
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:      # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:          # noqa: N802
        path = urlparse(self.path).path
        if path in ("/health", "/"):
            return self._send(200, {"ok": True, "service": "pivotted",
                                    "tools": len(T.TOOLS)})
        if path == "/meta":
            # Everything a caller needs to describe this build honestly.
            return self._send(200, {
                "service": "pivotted",
                "model": LLM_DEPLOYMENT,
                "tools": [t["name"] for t in T.TOOLS],
                "dropped_from_charto": sorted(T.DROPPED),
                "max_rounds": MAX_ROUNDS,
                "system_prompt_chars": len(system_prompt()),
                "price_universe": len(T.stored_symbols()),
            })
        return self._send(404, {"error": "not found"})

    def do_POST(self) -> None:         # noqa: N802
        path = urlparse(self.path).path
        # /chat/stream is Pivot's route and Pivot's event dialect, so its chat
        # tab can be repointed here by changing a base URL and nothing else.
        if path not in ("/chat", "/chat/stream"):
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, TypeError):
            return self._send(400, {"error": "bad JSON body"})
        messages = [m for m in (body.get("messages") or [])
                    if isinstance(m, dict)]
        if not messages:
            return self._send(400, {"error": "messages[] required"})
        messages = _apply_attachments(messages, body.get("attachments"))
        try:
            if path == "/chat/stream":
                return self._stream(messages, dialect="pivot")
            if body.get("stream"):
                return self._stream(messages, dialect="native")
            out = chat(messages)
            # Pivot's non-streaming caller reads `response`; ours reads
            # `text`. Both are present so either can consume this.
            out.setdefault("response", out.get("text", ""))
            return self._send(200, out)
        except Exception as exc:       # noqa: BLE001
            logging.exception("pivotted: turn failed")
            return self._send(500, {"error": str(exc)})

    def _stream(self, messages: list[dict], dialect: str = "native") -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # nginx buffers SSE by default and the reply then lands in one lump at
        # the end, which looks exactly like a hang.
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        gen = chat_stream_pivot if dialect == "pivot" else chat_stream
        try:
            for frame in gen(messages):
                self.wfile.write(
                    f"data: {json.dumps(frame, default=str)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass               # the user navigated away mid-answer
        except Exception as exc:       # noqa: BLE001
            logging.exception("pivotted: stream failed")
            try:
                self.wfile.write(
                    f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
                    .encode())
            except OSError:
                pass

    def log_message(self, fmt: str, *args) -> None:
        pass


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s pivotted %(levelname)s %(message)s")
    logging.info("tools: %d (%d from charto, %d fundamentals) — dropped %s",
                 len(T.TOOLS), len(T.TOOLS) - 6, 6, ", ".join(sorted(T.DROPPED)))
    logging.info("price universe: %d symbols archived; daily bars fetchable "
                 "for any listed company; filings: all of them",
                 len(T.stored_symbols()))
    # Resolve the price-service import here, single-threaded, so the first
    # concurrent tool round does not race the circular import and lose.
    logging.info("price service warm: %s", T.bars.warm())
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
