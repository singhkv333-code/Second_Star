"""Azure Foundry model benchmark — gpt-5.4-{nano,mini,full} x {low,medium,high}.

For every (prompt, model, reasoning) cell we stream the Responses API and record:
  - ttft_ms      : request-sent -> first VISIBLE output token (response.output_text.delta)
  - first_evt_ms : request-sent -> first SSE event of any kind
  - total_ms     : request-sent -> response.completed
  - input/output/reasoning/cached tokens (output_tokens already INCLUDES reasoning)
  - cost_usd     : input*in_rate + output*out_rate (cached at 50%); reasoning is part of output
  - the full answer text

Calls run in parallel (asyncio + semaphore). 429/5xx are retried with backoff.
"""
from __future__ import annotations
import asyncio, json, time, httpx
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]  # .../pivot

def _env(key: str) -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""

BASE = _env("AZURE_OPENAI_ENDPOINT").rstrip("/")
KEY = _env("AZURE_KEY")
URL = f"{BASE}/responses"
HDR = {"api-key": KEY, "Content-Type": "application/json", "Accept": "text/event-stream"}

MODELS = ["gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4"]
LEVELS = ["low", "medium", "high"]
MAX_OUT = None  # no cap — omit max_output_tokens so the model runs to its natural stop
CONCURRENCY = 8
MAX_RETRIES = 3

# USD per 1,000,000 tokens. mini = repo value (placeholder, gpt-5-mini parity).
# nano / full are ESTIMATES (repo prices only mini) using the GPT-5 family
# ratio pattern (nano ~1/5 mini, full ~5x mini). Cost = tokens * rate; recompute
# trivially if the real Azure rates differ.
PRICING = {
    "gpt-5.4-nano": {"input": 0.05, "output": 0.40, "est": True},
    "gpt-5.4-mini": {"input": 0.25, "output": 2.00, "est": False},
    "gpt-5.4":      {"input": 1.25, "output": 10.00, "est": True},
}
CACHED_DISCOUNT = Decimal("0.5")

SYSTEM = (
    "You are Pivot, a chat-first investing copilot for Indian retail investors. "
    "Give data-rich, structured, decision-useful answers: use markdown headers and "
    "tables, concrete numbers and explicit parameters, and a clearly defended view "
    "when one is warranted. You provide analysis and frameworks, NOT personalised "
    "buy/sell advice — end any analysis with a short 'This is analysis, not financial "
    "advice.' Markets are NSE/BSE equities, indices, and NSE options (NFO); currency is "
    "INR (rupees). When live data is unavailable, use clearly illustrative numbers and "
    "say so. Be complete but not padded."
)

PROMPTS = [
    ("basket_invvol_it",
     "Build me a basket of 5 Indian large-cap IT stocks weighted by inverse volatility, "
     "with Rs 5,00,000 to deploy. Show the allocation %, share counts at current prices, "
     "and explain the inverse-volatility weighting logic."),
    ("basket_thematic_defence",
     "I want a Rs 2,00,000 'India defence manufacturing' thematic basket of 6 stocks. "
     "Pick the names, set weights, justify each pick in one line, and flag the "
     "concentration and liquidity risks."),
    ("fno_bull_call_spread",
     "NIFTY is at 24,800 and I'm mildly bullish into monthly expiry. Construct a bull "
     "call spread: pick the two strikes, estimate net debit, max profit, max loss, "
     "breakeven, and sketch the payoff at expiry."),
    ("fno_covered_call",
     "I hold 1,000 shares of RELIANCE and want monthly income. Design a covered-call "
     "program: which strike/delta to sell, expected premium yield, assignment risk, and "
     "the rule for when to roll."),
    ("fno_iron_condor",
     "BANKNIFTY weekly IV looks elevated before the RBI policy. Build an iron condor: "
     "choose the four strikes, net credit, max risk, the two breakevens, and explain how "
     "IV crush helps or hurts the position."),
    ("compare_hdfc_icici",
     "Compare HDFC Bank vs ICICI Bank as a 3-year hold: growth, asset quality, valuation "
     "(P/E, P/B), ROE, and which you'd overweight and why. Use a markdown table."),
    ("compare_instruments_tcs",
     "For a Rs 1,00,000 bullish view on TCS over 1 month, compare three ways to express "
     "it: buy 100 shares, buy 1 ATM call, or a call spread. Lay out cost, max loss, "
     "breakeven, and effective leverage in a table, then recommend one."),
    ("exec_rsi_automation",
     "Turn this into a precise automation spec: 'Buy 10 INFY when RSI(14) drops below 30, "
     "take profit at +8%, stop-loss at -4%.' Define the trigger conditions, order "
     "parameters, and the edge cases that could break it."),
    ("exec_rebalance_plan",
     "I have a 10-stock portfolio that has drifted from equal weight. Describe a "
     "disciplined quarterly rebalancing execution plan: drift thresholds, order "
     "sequencing, tax and cost awareness, and slippage control."),
    ("analysis_maruti_swing",
     "Give me a structured analysis of MARUTI for a swing trade over the next 2-4 weeks: "
     "trend, key support/resistance levels, momentum, a clear directional bias, and an "
     "invalidation level. End with the standard not-advice line."),
]


def compute_cost(model, input_tokens, output_tokens, cached_tokens):
    r = PRICING[model]
    in_rate = Decimal(str(r["input"])) / Decimal(1_000_000)
    out_rate = Decimal(str(r["output"])) / Decimal(1_000_000)
    in_t = max(0, int(input_tokens or 0))
    cached = max(0, min(int(cached_tokens or 0), in_t))
    full = in_t - cached
    out_t = max(0, int(output_tokens or 0))  # already includes reasoning tokens
    cost = (Decimal(full) * in_rate
            + Decimal(cached) * in_rate * CACHED_DISCOUNT
            + Decimal(out_t) * out_rate)
    return float(cost.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP))


async def run_cell(client, sem, pid, prompt, model, level):
    async with sem:
        payload = {
            "model": model,
            "input": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "reasoning": {"effort": level},
        }
        if MAX_OUT is not None:
            payload["max_output_tokens"] = MAX_OUT
        for attempt in range(MAX_RETRIES):
            text_parts, usage = [], {}
            status = incomplete_reason = None
            t0 = time.monotonic()
            t_first_evt = t_first_text = t_done = None
            err = None
            retry = False
            try:
                async with client.stream("POST", URL, headers=HDR, json={**payload, "stream": True}) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", "replace")[:300]
                        if resp.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                            ra = resp.headers.get("retry-after")
                            await asyncio.sleep(float(ra) if ra else 2 * (attempt + 1))
                            retry = True
                        else:
                            return _result(pid, model, level, None, None, None, {}, 0.0,
                                           f"HTTP {resp.status_code}: {body}")
                    else:
                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data or data == "[DONE]":
                                continue
                            if t_first_evt is None:
                                t_first_evt = time.monotonic()
                            try:
                                ev = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            et = ev.get("type", "")
                            if et == "response.output_text.delta":
                                if t_first_text is None:
                                    t_first_text = time.monotonic()
                                text_parts.append(ev.get("delta") or "")
                            elif et in ("response.completed", "response.incomplete", "response.failed"):
                                t_done = time.monotonic()
                                ro = ev.get("response") or {}
                                usage = ro.get("usage") or {}
                                status = ro.get("status")
                                incomplete_reason = (ro.get("incomplete_details") or {}).get("reason")
                                if et == "response.failed":
                                    err = ((ro.get("error") or {}).get("message")) or "response.failed"
                            elif et == "error":
                                err = ev.get("message", "stream error")
            except httpx.HTTPError as e:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                return _result(pid, model, level, None, None, None, {}, 0.0,
                               f"transport: {type(e).__name__}: {e}")
            if retry:
                continue
            if err and not text_parts:
                if attempt < MAX_RETRIES - 1:
                    continue
                return _result(pid, model, level, None, None, None, {}, 0.0, err)
            ttft = int((t_first_text - t0) * 1000) if t_first_text else None
            first_evt = int((t_first_evt - t0) * 1000) if t_first_evt else None
            total = int(((t_done or time.monotonic()) - t0) * 1000)
            cost = compute_cost(model,
                                usage.get("input_tokens", 0),
                                usage.get("output_tokens", 0),
                                (usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
            return _result(pid, model, level, ttft, first_evt, total, usage, cost,
                           None, "".join(text_parts), status, incomplete_reason)
        return _result(pid, model, level, None, None, None, {}, 0.0, "exhausted retries")


def _result(pid, model, level, ttft, first_evt, total, usage, cost, err, text="",
            status=None, incomplete_reason=None):
    return {
        "prompt_id": pid, "model": model, "level": level,
        "ttft_ms": ttft, "first_evt_ms": first_evt, "total_ms": total,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
        "cached_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens"),
        "cost_usd": cost, "status": status, "incomplete_reason": incomplete_reason,
        "error": err, "answer": text,
    }


async def main():
    sem = asyncio.Semaphore(CONCURRENCY)
    timeout = httpx.Timeout(connect=10, read=600, write=15, pool=10)
    cells = [(pid, prompt, m, lvl)
             for (pid, prompt) in PROMPTS for m in MODELS for lvl in LEVELS]
    print(f"running {len(cells)} cells, concurrency={CONCURRENCY}", flush=True)
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [run_cell(client, sem, pid, prompt, m, lvl) for (pid, prompt, m, lvl) in cells]
        results = []
        done = 0
        for fut in asyncio.as_completed(tasks):
            r = await fut
            results.append(r)
            done += 1
            tag = "ERR " + (r["error"] or "")[:40] if r["error"] else f"ttft={r['ttft_ms']}ms out={r['output_tokens']}"
            print(f"[{done}/{len(cells)}] {r['model']:13} {r['level']:6} {r['prompt_id']:24} {tag}", flush=True)
    wall = int(time.monotonic() - t0)
    out = {
        "meta": {"models": MODELS, "levels": LEVELS, "max_output_tokens": MAX_OUT,
                 "concurrency": CONCURRENCY, "n_cells": len(cells), "wall_seconds": wall,
                 "pricing_usd_per_1m": PRICING, "system_prompt": SYSTEM,
                 "prompts": {pid: p for pid, p in PROMPTS}},
        "results": sorted(results, key=lambda r: (r["prompt_id"], MODELS.index(r["model"]), LEVELS.index(r["level"]))),
    }
    (HERE / "raw_results.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    ok = sum(1 for r in results if not r["error"])
    print(f"\nDONE in {wall}s — {ok}/{len(cells)} ok. wrote raw_results.json", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
