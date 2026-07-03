"""Build the markdown benchmark report from raw_results.json.

Produces MODEL_BENCHMARK_2026-06-21.md:
  - methodology + pricing caveat
  - aggregate latency/token/cost tables (by model, by model x level, by level)
  - per-prompt sections with every model x level answer + a metrics line
The quality-judgment + overview prose is authored separately (by the analyst).
"""
from __future__ import annotations
import json, statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
data = json.loads((HERE / "raw_results.json").read_text())
meta, results = data["meta"], data["results"]
MODELS, LEVELS = meta["models"], meta["levels"]
ok = [r for r in results if not r["error"]]


def avg(xs):
    xs = [x for x in xs if x is not None]
    return round(st.mean(xs), 1) if xs else None


def fmt_usd(x):
    return f"${x:.6f}" if x is not None else "—"


def avg_usd(rs):
    cs = [r["cost_usd"] for r in rs if r.get("cost_usd") is not None]
    return fmt_usd(sum(cs) / len(cs)) if cs else "—"


def sel(model=None, level=None):
    return [r for r in ok if (model is None or r["model"] == model) and (level is None or r["level"] == level)]


L = []
w = L.append
w("# Azure Foundry model benchmark — gpt-5.4 nano / mini / full\n")
_mot = meta.get('max_output_tokens')
w(f"_Date: 2026-06-21 · {len(ok)}/{len(results)} cells succeeded · wall {meta['wall_seconds']}s · "
  f"max_output_tokens={'unlimited' if _mot is None else _mot} · concurrency={meta['concurrency']}_\n")
_trunc = [r for r in ok if r.get('incomplete_reason')]
if _trunc:
    _items = ", ".join("{}/{}/{}({})".format(r['model'], r['level'], r['prompt_id'], r['incomplete_reason']) for r in _trunc)
    w("_⚠️ {} cell(s) hit a natural incomplete stop: {}_\n".format(len(_trunc), _items))
w("**What this is.** Each of 10 execution/analysis prompts (basket building, F&O strategy, "
  "comparison, automation specs) was sent to all three Azure Foundry deployments at three "
  "reasoning-effort levels (low / medium / high) — a 10×3×3 = 90-cell matrix. Calls ran in "
  "parallel over the Responses API with SSE streaming. There were no tools and no live market "
  "data: models use illustrative numbers, so this measures **model behaviour, reasoning, "
  "structure and writing — not data accuracy**.\n")
w("**Latency = TTFT.** Reported latency is **time-to-first-visible-token** (request sent → first "
  "`response.output_text.delta`), i.e. when the answer *starts* streaming, not when it finishes. "
  "`total_ms` (to completion) is shown alongside for context.\n")
w("**Tokens / cost.** `output_tokens` from the Responses API already includes reasoning tokens; "
  "cost bills input + output once at the model's rate (cached input at 50%). Reasoning tokens are "
  "shown separately as the share of output spent thinking.\n")

# pricing
w("\n## Pricing used (USD per 1M tokens)\n")
w("| Model | Input | Output | Source |")
w("|---|--:|--:|---|")
for m in MODELS:
    p = meta["pricing_usd_per_1m"][m]
    src = "**ESTIMATE**" if p.get("est") else "repo `llm_cost.py` (placeholder)"
    w(f"| `{m}` | {p['input']:.2f} | {p['output']:.2f} | {src} |")
w("\n> ⚠️ The repo only prices `gpt-5.4-mini`. nano and full rates are **estimates** "
  "(GPT-5 family ratio: nano ≈ 1/5 mini, full ≈ 5× mini). Cost = tokens × rate, so if you "
  "drop in the real Azure rates the dollar columns rescale linearly — the relative picture holds.\n")

# sanity: output includes reasoning?
viol = [r for r in ok if r["reasoning_tokens"] and r["output_tokens"] and r["reasoning_tokens"] > r["output_tokens"]]
w(f"\n_Sanity: reasoning_tokens ≤ output_tokens in {len(ok)-len(viol)}/{len(ok)} cells "
  f"→ output_tokens includes reasoning (no double-count).”_\n".replace("”", ""))

# aggregate by model
w("\n## Aggregate — by model (averaged across all 10 prompts × 3 levels)\n")
w("| Model | avg TTFT | avg total | avg out tok | avg rsn tok | rsn % of out | avg cost | total cost |")
w("|---|--:|--:|--:|--:|--:|--:|--:|")
for m in MODELS:
    rs = sel(model=m)
    if not rs:
        w(f"| `{m}` | — | — | — | — | — | — | — |"); continue
    ao, ar = avg([r["output_tokens"] for r in rs]), avg([r["reasoning_tokens"] for r in rs])
    pct = round(100*ar/ao, 0) if ao and ar else 0
    tot = sum(r["cost_usd"] for r in rs)
    w(f"| `{m}` | {avg([r['ttft_ms'] for r in rs])}ms | {avg([r['total_ms'] for r in rs])}ms | "
      f"{ao} | {ar} | {pct:.0f}% | {avg_usd(rs)} | {fmt_usd(tot)} |")

# by model x level
w("\n## Aggregate — by model × reasoning level\n")
w("| Model | Level | avg TTFT | avg total | avg out tok | avg rsn tok | avg cost |")
w("|---|---|--:|--:|--:|--:|--:|")
for m in MODELS:
    for lv in LEVELS:
        rs = sel(model=m, level=lv)
        if not rs:
            w(f"| `{m}` | {lv} | — | — | — | — | — |"); continue
        w(f"| `{m}` | {lv} | {avg([r['ttft_ms'] for r in rs])}ms | {avg([r['total_ms'] for r in rs])}ms | "
          f"{avg([r['output_tokens'] for r in rs])} | {avg([r['reasoning_tokens'] for r in rs])} | "
          f"{avg_usd(rs)} |")

# by level
w("\n## Aggregate — by reasoning level (across all models)\n")
w("| Level | avg TTFT | avg total | avg out tok | avg rsn tok | avg cost |")
w("|---|--:|--:|--:|--:|--:|")
for lv in LEVELS:
    rs = sel(level=lv)
    w(f"| {lv} | {avg([r['ttft_ms'] for r in rs])}ms | {avg([r['total_ms'] for r in rs])}ms | "
      f"{avg([r['output_tokens'] for r in rs])} | {avg([r['reasoning_tokens'] for r in rs])} | "
      f"{avg_usd(rs)} |")

# errors
errs = [r for r in results if r["error"]]
if errs:
    w(f"\n## Errors ({len(errs)})\n")
    for r in errs:
        w(f"- `{r['model']}` / {r['level']} / {r['prompt_id']}: {r['error']}")

# per-prompt answers
w("\n---\n\n# Answers by prompt\n")
by_pid = {}
for r in results:
    by_pid.setdefault(r["prompt_id"], []).append(r)
for pid, prompt in meta["prompts"].items():
    w(f"\n## {pid}\n")
    w(f"> {prompt}\n")
    rows = sorted(by_pid.get(pid, []), key=lambda r: (MODELS.index(r['model']), LEVELS.index(r['level'])))
    for r in rows:
        w(f"\n### `{r['model']}` · {r['level']}\n")
        if r["error"]:
            w(f"**ERROR:** {r['error']}\n"); continue
        w(f"`TTFT {r['ttft_ms']}ms · total {r['total_ms']}ms · in {r['input_tokens']} · "
          f"out {r['output_tokens']} (rsn {r['reasoning_tokens']}) · cost {fmt_usd(r['cost_usd'])}`\n")
        w("\n" + (r["answer"] or "_(empty)_").strip() + "\n")

out = HERE / "MODEL_BENCHMARK_2026-06-21.md"
out.write_text("\n".join(L))
print(f"wrote {out} ({out.stat().st_size//1024} KB)")
