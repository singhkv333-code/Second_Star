"""Smoke test for propose_pipeline_workflow.

Sends 6 prompts through POST /chat with a fresh conv_id per prompt and
prints a tight verdict line for each. Not a full eval — no markdown
report, no backtester replay. Goal: confirm the new tool builds the
right multi-branch shapes and refuses the out-of-scope ones.
"""
from __future__ import annotations
import json, sys, time, uuid
import httpx

BASE = "http://127.0.0.1:8000"

PROMPTS = [
    {
        "id": "pipe_multi_tier_exit",
        "prompt": (
            "buy 10 RELIANCE when RSI(14)<30 AND MACD hist > 0. "
            "Sell 5 when up 3%, sell 3 more when up 5%, sell the rest if "
            "drawdown from peak > 5% OR held > 30 bars"
        ),
        "expect_tool": "propose_pipeline_workflow",
        "expect_min_triggers": 4,
    },
    {
        "id": "pipe_multi_trigger_fanout",
        "prompt": (
            "every Monday at open buy 5 NIFTYBEES. If NIFTY drops 2% "
            "intraday from open sell 10 from my NIFTYBEES holding. On "
            "Friday close squareoff my full NIFTYBEES position"
        ),
        "expect_tool": "propose_pipeline_workflow",
        "expect_min_triggers": 3,
    },
    {
        "id": "pipe_compound_mixed_action",
        "prompt": (
            "every weekday at 09:30, if RSI(14)<30 AND MACD hist > 0 "
            "send me a notification. If also RSI<20, buy 10 INFY"
        ),
        "expect_tool": "propose_pipeline_workflow",
        "expect_min_triggers": 1,
    },
    {
        "id": "pipe_news_plus_dsl_gate",
        "prompt": (
            "if news confirms RBI cut the repo rate AND BANKNIFTY is up "
            ">1% the next morning, buy 30 HDFCBANK"
        ),
        "expect_tool": "propose_pipeline_workflow",
        "expect_min_triggers": 2,
    },
    {
        "id": "pipe_refuse_ifelse",
        "prompt": (
            "buy 10 INFY when RSI<30. Wait 1 hour, then if INFY is still "
            "above entry set a 2% trailing stop, otherwise sell at market"
        ),
        "expect_tool": "propose_pipeline_workflow",
        "expect_refuse": True,
    },
    {
        "id": "pipe_refuse_voting",
        "prompt": (
            "watch 3 things: RBI rate cut news, NIFTY drops 2%, gold ETF "
            "gaps up 1%. If at least 2 of those 3 fire today, buy 100 GOLDBEES"
        ),
        "expect_tool": "propose_pipeline_workflow",
        "expect_refuse": True,
    },
]


def register() -> str:
    email = f"smoke_{uuid.uuid4().hex[:10]}@p.com"
    r = httpx.post(
        f"{BASE}/auth/register",
        json={"email": email, "password": "password123", "full_name": "smoke"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def call_chat(token: str, prompt: str, conv_id: str) -> tuple[dict, float]:
    t0 = time.monotonic()
    r = httpx.post(
        f"{BASE}/chat",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "messages": [{"role": "user", "content": prompt}],
            "conversation_id": conv_id,
            "include_portfolio_context": True,
        },
        timeout=240,
    )
    dt = (time.monotonic() - t0) * 1000
    if r.status_code != 200:
        return {"_err": f"HTTP {r.status_code}: {r.text[:300]}"}, dt
    return r.json(), dt


def main() -> int:
    token = register()
    print(f"[smoke] running {len(PROMPTS)} prompts against {BASE}", file=sys.stderr)
    print()
    n_pass = n_fail = 0
    for i, p in enumerate(PROMPTS, 1):
        conv = f"s_smoke_{uuid.uuid4().hex[:10]}"
        body, ms = call_chat(token, p["prompt"], conv)
        if "_err" in body:
            print(f"[{i}/{len(PROMPTS)}] {p['id']} → HTTP ERR {body['_err']}")
            n_fail += 1
            continue
        tools = body.get("tools_called") or []
        rd = body.get("raw_data") or {}
        steps = rd.get("steps") if isinstance(rd, dict) else None
        n_steps = len(steps) if isinstance(steps, list) else 0
        n_triggers = sum(
            1 for s in (steps or [])
            if isinstance(s, dict) and (s.get("step_type") or "").startswith("trigger.")
        )
        reply = (body.get("response") or "").strip()
        reply_preview = reply[:160].replace("\n", " ")

        tool_match = p["expect_tool"] in tools
        if p.get("expect_refuse"):
            # Refusal = correct tool called but no draft, prose explains the gap
            ok = tool_match and n_steps == 0 and len(reply) > 20
            tag = "REFUSE" if ok else "FAIL"
        else:
            ok = (
                tool_match
                and n_steps >= 2
                and n_triggers >= p.get("expect_min_triggers", 1)
            )
            tag = "PASS" if ok else "FAIL"

        if ok:
            n_pass += 1
        else:
            n_fail += 1

        print(f"[{i}/{len(PROMPTS)}] {p['id']} → {tag} ({ms:.0f}ms)")
        print(f"    prompt:  {p['prompt'][:140]}")
        print(f"    tools:   {tools}")
        print(f"    steps:   {n_steps} (triggers={n_triggers}, expect≥{p.get('expect_min_triggers', 0)})")
        print(f"    reply:   {reply_preview}")
        if isinstance(steps, list) and steps:
            print(f"    shape:   " + " · ".join(
                s.get("step_type", "?") for s in steps[:10]
            ))
        print()

    print(f"[smoke] {n_pass}/{len(PROMPTS)} OK, {n_fail} FAIL")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
