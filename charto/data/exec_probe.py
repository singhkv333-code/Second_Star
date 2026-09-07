"""Drive execution mode through the live /chat endpoint and report the triad.

A unit test proves the seam is connected; only a real turn proves the model
uses it. This sends prompts to the running dataserver exactly as the sidebar
does — same body, same mode flag — and prints, per prompt: which tools fired,
what card came back, tokens, latency, and the reply.

Usage:
    exec_probe.py suite.json [-o results.json]     run a file of prompts
    exec_probe.py -m "buy 10 INFY when RSI < 30"   run one prompt

A prompt row may be a bare string or {"prompt", "symbol", "expect_tool",
"note"}. `expect_tool` is compared but never enforced — a mismatch is a
finding to read, not a failure to hide, and several prompts have more than
one defensible route.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

ENDPOINT = "http://127.0.0.1:5174/chat"


def ask(prompt: str, symbol: str = "RELIANCE", *, mode: str = "execution",
        history: list | None = None, timeout: float = 240.0) -> dict:
    body = {
        "messages": (history or []) + [{"role": "user", "content": prompt}],
        "context": {"symbol": symbol},
        "stream": False,
        "mode": mode,
        "chat_id": f"probe_{int(time.time() * 1000)}",
    }
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001 — a dead turn is a result too
        return {"error": f"{type(exc).__name__}: {exc}",
                "latency_s": round(time.time() - started, 1)}
    payload["latency_s"] = round(time.time() - started, 1)
    return payload


def summarize(prompt: str, res: dict, expect: str = "") -> dict:
    tools = [t.get("name") for t in (res.get("tools_used") or [])]
    failed = [t.get("name") for t in (res.get("tools_used") or [])
              if not t.get("ok")]
    cards = res.get("cards") or []
    usage = res.get("usage") or {}
    return {
        "prompt": prompt,
        "expect_tool": expect,
        "tools": tools,
        "tool_failures": failed,
        "matched": (expect in tools) if expect else None,
        "cards": [c.get("kind") for c in cards],
        "card_steps": [len(c.get("steps") or []) for c in cards
                       if c.get("kind") == "workflow_draft"],
        "in_tok": usage.get("input_tokens"),
        "out_tok": usage.get("output_tokens"),
        "latency_s": res.get("latency_s"),
        "error": res.get("error"),
        "reply": (res.get("text") or "")[:600],
    }


def render(row: dict) -> None:
    mark = "·"
    if row["error"]:
        mark = "ERR"
    elif row["matched"] is True:
        mark = "OK "
    elif row["matched"] is False:
        mark = "ROUTE"
    for t in row.get("setup") or []:
        print(f"\n  (setup) {t}")
    print(f"\n[{mark}] {row['prompt']}")
    print(f"      tools: {row['tools'] or '—'}"
          + (f"  expected: {row['expect_tool']}" if row["expect_tool"] else ""))
    if row["tool_failures"]:
        print(f"      FAILED: {row['tool_failures']}")
    print(f"      cards: {row['cards'] or '—'}"
          + (f" steps={row['card_steps']}" if row["card_steps"] else ""))
    print(f"      {row['latency_s']}s  in={row['in_tok']} out={row['out_tok']}")
    if row["error"]:
        print(f"      error: {row['error']}")
    print(f"      reply: {row['reply'][:300]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("suite", nargs="?", help="JSON file of prompts")
    ap.add_argument("-m", "--message", help="single prompt")
    ap.add_argument("-s", "--symbol", default="RELIANCE")
    ap.add_argument("-o", "--out", help="write results JSON here")
    ap.add_argument("--mode", default="execution")
    args = ap.parse_args()

    if args.message:
        rows = [{"prompt": args.message, "symbol": args.symbol}]
    elif args.suite:
        raw = json.loads(open(args.suite).read())
        rows = [{"prompt": r} if isinstance(r, str) else r
                for r in (raw.get("prompts") if isinstance(raw, dict) else raw)]
        for r in rows:
            r.setdefault("prompt", (r.get("turns") or [""])[-1])
    else:
        ap.error("give a suite file or -m")

    out = []
    for row in rows:
        # A row is either one prompt or a conversation. `turns` runs every
        # message in order against the SAME history, and only the last one is
        # graded — the earlier turns exist to build the state the last one
        # needs (a draft to amend, a backtest to mark, a basket to size).
        turns = row.get("turns") or [row["prompt"]]
        sym = row.get("symbol", args.symbol)
        history: list = []
        res = {}
        for turn in turns:
            res = ask(turn, sym, mode=args.mode, history=history)
            history = history + [
                {"role": "user", "content": turn},
                {"role": "assistant", "content": res.get("text") or ""},
            ]
        summary = summarize(turns[-1], res, row.get("expect_tool", ""))
        summary["note"] = row.get("note", "")
        summary["setup"] = turns[:-1]
        out.append(summary)
        render(summary)

    ok = sum(1 for r in out if r["matched"] is True)
    routed = sum(1 for r in out if r["matched"] is False)
    errs = sum(1 for r in out if r["error"])
    carded = sum(1 for r in out if r["cards"])
    lat = sorted(r["latency_s"] for r in out if r["latency_s"])
    med = lat[len(lat) // 2] if lat else 0
    print(f"\n{'='*70}\n{len(out)} prompts · {ok} expected-tool · {routed} other "
          f"route · {errs} errored · {carded} produced a card · median {med}s")
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(out, fh, indent=2)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
