"""Run a prompt set through a live Pivotted and record what it cost.

Sequential on purpose. Running the turns concurrently would halve the wall
clock and make every latency number a lie — they would contend on the same
Azure deployment and the same Postgres read pool, which is not the condition a
single user experiences.

Talks to the NATIVE /chat dialect rather than /chat/stream, because the
non-streamed response already carries `usage`, `rounds` and `tools_used`; the
FE dialect drops rounds and reports token usage only at the end.

Multi-turn is a list of user strings. Each assistant reply is threaded back in
as history, which is the only way a follow-up ("and its peers?") is a real
test — a fresh single-turn eval never exercises the case where the model has
to carry a subject across turns.

  pivot/.venv/bin/python pivotted/eval.py [--out DIR] [--only N]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://localhost:5175"

# Research and financial analysis, deliberately light on chart technicals:
# the point of this build is the half Charto could not serve. A handful are
# multi-turn (a list), and #3 and #20 are known-trap probes rather than
# capability probes — see the report notes.
PROMPTS: list[dict] = [
    {"id": 1, "tag": "compare", "turns": [
        "Compare TCS and Infosys on capital efficiency and margins. Which is the better business?"]},
    {"id": 2, "tag": "screen", "turns": [
        "Which pharma companies have ROE above 25% and debt-to-equity below 0.5? Top 5."]},
    {"id": 3, "tag": "trap-resolution", "turns": [
        "What does Titan do as a business, and how profitable is it?"]},
    {"id": 4, "tag": "single-co", "turns": [
        "Walk me through Reliance Industries' debt position over the last six years. Is leverage rising?"]},
    {"id": 5, "tag": "screen", "turns": [
        "Find me profitable small-cap companies with almost no debt — ROE over 18 and D/E under 0.2."]},
    {"id": 6, "tag": "multiturn", "turns": [
        "How has Asian Paints' return on capital trended over the last five years?",
        "Now compare that with Berger Paints."]},
    {"id": 7, "tag": "valuation", "turns": [
        "Is Nestle India expensive right now? Use whatever valuation measures you actually have."]},
    {"id": 8, "tag": "sector", "turns": [
        "Which Indian IT services companies look cheapest on earnings, and is the cheapness deserved?"]},
    {"id": 9, "tag": "balance-sheet", "turns": [
        "Compare the balance sheets of Tata Steel and JSW Steel. Who is carrying more risk?"]},
    {"id": 10, "tag": "banks", "turns": [
        "Which private banks have the best net interest margins, and what does that tell me?"]},
    {"id": 11, "tag": "multiturn", "turns": [
        "What is driving Bajaj Finance's profitability?",
        "Is that sustainable given its leverage?"]},
    {"id": 12, "tag": "explainer", "turns": [
        "Explain why ROCE matters more than ROE for a capital-heavy business, with an Indian example."]},
    {"id": 13, "tag": "growth", "turns": [
        "Show me Dixon Technologies' revenue and profit growth over the years you have."]},
    {"id": 14, "tag": "compare", "turns": [
        "Maruti Suzuki versus Mahindra & Mahindra — margins, returns on capital, and leverage."]},
    {"id": 15, "tag": "risk", "turns": [
        "What are the biggest risks visible in Vodafone Idea's balance sheet?"]},
    {"id": 16, "tag": "screen", "turns": [
        "Screen capital goods companies with interest coverage above 10 and revenue growth above 15%."]},
    {"id": 17, "tag": "multiturn", "turns": [
        "How does Avenue Supermarts' asset turnover compare to its retail peers?",
        "Does its valuation make sense given those numbers?"]},
    {"id": 18, "tag": "quality", "turns": [
        "Is there anything worrying about the gap between reported profit and operating cash flow at any large cement company?"]},
    {"id": 19, "tag": "coverage", "turns": [
        "Give me the fundamentals for Camlin Fine Sciences and tell me if the business is turning around."]},
    {"id": 20, "tag": "trap-quarterly", "turns": [
        "How did HDFC Bank do last quarter versus the quarter before?"]},
]


def turn(messages: list[dict], timeout: int = 240) -> dict:
    req = urllib.request.Request(
        f"{BASE}/chat",
        data=json.dumps({"messages": messages}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"wall_s": round(time.time() - t0, 2), "error": str(exc),
                "text": "", "usage": {}, "tools_used": [], "rounds": 0}
    out["wall_s"] = round(time.time() - t0, 2)
    return out


def run(items: list[dict], out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / "eval_raw.jsonl"
    results: list[dict] = []
    with raw.open("w") as fh:
        for i, spec in enumerate(items, 1):
            history: list[dict] = []
            rec = {"id": spec["id"], "tag": spec["tag"], "turns": []}
            for t_i, user in enumerate(spec["turns"], 1):
                history.append({"role": "user", "content": user})
                res = turn(history)
                text = res.get("text") or ""
                history.append({"role": "assistant", "content": text})
                u = res.get("usage") or {}
                rec["turns"].append({
                    "n": t_i, "prompt": user, "wall_s": res.get("wall_s"),
                    "rounds": res.get("rounds"),
                    "tools": [t.get("name") for t in res.get("tools_used") or []],
                    "tool_ok": [t.get("ok") for t in res.get("tools_used") or []],
                    "in_tok": u.get("input_tokens"), "out_tok": u.get("output_tokens"),
                    "chars": len(text), "error": res.get("error"),
                    "text": text,
                })
                print(f"  [{i:>2}/{len(items)}] t{t_i} {spec['tag']:<16} "
                      f"{res.get('wall_s')}s  r={res.get('rounds')}  "
                      f"tools={[t.get('name') for t in res.get('tools_used') or []]}",
                      flush=True)
            results.append(rec)
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
    return results


def summarise(results: list[dict]) -> str:
    flat = [t for r in results for t in r["turns"]]
    ok = [t for t in flat if not t.get("error")]

    def col(k):
        return [t[k] for t in ok if isinstance(t.get(k), (int, float))]

    walls, ins, outs, rounds = col("wall_s"), col("in_tok"), col("out_tok"), col("rounds")
    tool_counts: dict[str, int] = {}
    fails: list[str] = []
    for t in ok:
        for name, good in zip(t["tools"], t["tool_ok"]):
            tool_counts[name] = tool_counts.get(name, 0) + 1
            if not good:
                fails.append(name)

    lines = [
        "# Pivotted — 20-prompt research eval",
        "",
        f"{len(results)} prompts, {len(flat)} turns ({len(flat) - len(results)} follow-ups), "
        f"{len(flat) - len(ok)} transport failures.",
        "",
        "## Cost and latency",
        "",
        "| metric | median | mean | p90 | max |",
        "|---|---:|---:|---:|---:|",
    ]

    def row(label, xs, fmt="{:.0f}"):
        if not xs:
            return f"| {label} | — | — | — | — |"
        s = sorted(xs)
        p90 = s[min(len(s) - 1, int(len(s) * 0.9))]
        return (f"| {label} | {fmt.format(statistics.median(xs))} "
                f"| {fmt.format(statistics.mean(xs))} | {fmt.format(p90)} "
                f"| {fmt.format(max(xs))} |")

    lines += [
        row("latency (s)", walls, "{:.1f}"),
        row("input tokens", ins),
        row("output tokens", outs),
        row("rounds (LLM hops)", rounds, "{:.1f}"),
        "",
        f"Total input tokens: {sum(ins):,} · output: {sum(outs):,} · "
        f"tool calls: {sum(tool_counts.values())} "
        f"({len(fails)} returned an error)",
        "",
        "## Tool usage",
        "",
        "| tool | calls |",
        "|---|---:|",
    ]
    for name, n in sorted(tool_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{name}` | {n} |")

    lines += ["", "## Per prompt", "",
              "| # | tag | turns | latency (s) | hops | in tok | out tok | tools |",
              "|---:|---|---:|---:|---:|---:|---:|---|"]
    for r in results:
        ts = r["turns"]
        tools = ", ".join(sorted({x for t in ts for x in t["tools"]})) or "—"
        lines.append(
            f"| {r['id']} | {r['tag']} | {len(ts)} | "
            f"{sum(t['wall_s'] or 0 for t in ts):.1f} | "
            f"{sum(t['rounds'] or 0 for t in ts)} | "
            f"{sum(t['in_tok'] or 0 for t in ts):,} | "
            f"{sum(t['out_tok'] or 0 for t in ts):,} | {tools} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/pivotted_eval")
    ap.add_argument("--only", type=int, default=0, help="first N prompts")
    a = ap.parse_args()
    items = PROMPTS[:a.only] if a.only else PROMPTS
    try:
        urllib.request.urlopen(f"{BASE}/health", timeout=5)
    except Exception as exc:                        # noqa: BLE001
        sys.exit(f"pivotted is not up on {BASE}: {exc}")
    out = Path(a.out)
    print(f"running {len(items)} prompts -> {out}", flush=True)
    t0 = time.time()
    res = run(items, out)
    report = summarise(res)
    (out / "eval_report.md").write_text(report)
    print(f"\ndone in {time.time() - t0:.0f}s")
    print(report)
