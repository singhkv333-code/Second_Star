# Pivotted — research and analysis, without the execution

A chat that researches Indian securities and cannot trade them. It is Charto's
chat loop with the chart removed, the ink tools stripped out, and the
fundamentals half rebuilt against **every listed company** instead of the ~500
we happen to store price bars for.

Nothing here registers an order, arms an automation, builds a strategy or
deploys anything. That is not a gap; it is the reason the thing is cheap.

```
pivotted/
  server.py        HTTP on :5175 — the tool loop, both SSE dialects
  tools.py         the trimmed tool table + concurrent dispatch
  fundamentals.py  all-company filings, via Pivot's own query layer
  prompt.py        the entire behavioural contract (222 tokens)
  run.sh           start/restart on pivot's venv
```

Run it:

```bash
./pivotted/run.sh          # or: pivot/.venv/bin/python pivotted/server.py
curl localhost:5175/meta   # tools, universe size, what was dropped
```

It runs on `pivot/.venv` because it needs SQLAlchemy/psycopg2 for the filings
Postgres and certifi for the Azure call (macOS system python ships no CA
bundle). No files outside this folder were moved or deleted — Charto and Pivot
are imported, never modified.

## Why it is fast

Pivot's chat spends **~20,500 tokens of system prompt** and **12,593 lines of
deterministic pre-LLM routing** before the model sees a turn. That machinery
protects a *commit* surface: a wrong intent classification there becomes a
wrong order. Pivotted has no commit surface, so it has no router.

| | Pivot | Pivotted |
|---|---:|---:|
| System prompt | ~20,500 tok | **222 tok** |
| Pre-LLM routing | 12,593 lines | none |
| Tool schemas | ~90 tools | 24 / ~8,615 tok |
| Tool calls per round | sequential | **concurrent** |

Measured turns: 15.6s / 14.5k input for a two-company comparison; 21.6s /
6 tool calls for a sector screen that then enriched five names **in one
parallel round**. Most of that wall time is the model, not the data — the
tools themselves run in 10ms (local SQLite) to 1.4s (Azure Postgres).

## What was trimmed, and why

Charto ships 25 tools written for a chat sitting beside a chart the user can
draw on. Seven do not survive the move:

| dropped | reason |
|---|---|
| `open_chart` | there are no panes here |
| `get_anchors` | exists only to mint ids for `draw_shape` |
| `draw_shape` | the ink itself |
| `evaluate_line` | scores a line the **user drew** (`drawing_id`) |
| `evaluate_fib` | scores a fib the **user drew** |
| `evaluate_drawing` | scores a box/band/channel the **user drew** |
| `plan_position` | entry/stop/target sizing — trade construction |

`plan_position` is the one dropped on principle rather than plumbing: research
stops before the trade. The survivors are then stripped of their ink arguments
(`draw`, `mark_points`, `mark_levels`, `connect`, `remove`, `clear_marks`),
which eight of them carried.

**25 tools / ~10,282 tok → 18 / ~7,064** — a 31% cut of the dominant per-turn
cost. Six fundamentals tools bring it to 24 / ~8,615.

## The coverage split (the one real trap)

Two universes, and they are not the same size:

- **Fundamentals, ratios, filings, screens — every listed company.** 11,256 in
  the `mc` schema, 18.3M statement lines.
- **Bars, indicators, patterns, levels, flows, volume profile — ~557 symbols.**

A company can be perfectly real, screenable on fundamentals, and still have no
price history here. The prompt says so, every price tool returns a named error
rather than a proxy, and the model is told not to substitute an index or a
peer. This is the single failure no downstream check could catch: a proxy that
looks right and belongs to another company.

**The filings are annual.** All 18.3M rows are `period_kind='annual'`; there is
no quarterly statement data at all. A question about last quarter cannot be
answered from this DB, and the tool description says so.

## Why it runs Pivot's query code

`fundamentals.py` imports `backend.market.financials_db` and
`backend.services.fundamentals_screen` rather than writing SQL against `mc`.
That is the same choice `charto/data/sync_financials.py` made, for the same
reason: a second implementation of "what does ROE mean in this DB" would
quietly disagree with the stock page about the same company.

What those modules carry that a fresh reimplementation would take months to
rediscover — all of it audited in their own docstrings:

- **line-item synonyms** — MC writes one concept under many strings across
  years and bases; banks file their top line as `Total Interest Earned`
- **basis preference** — consolidated, falling back to standalone only where
  consolidated has no row (ROE is standalone-only in this DB)
- **a recency floor** — latest-per-company alone surfaces dormant shells whose
  newest filing is from 2009 with absurd ratios
- **P/E from the `enrich` DB**, because MC stores earnings yield rounded to
  2 dp and `1/EY` therefore snaps onto a visible grid (25.00, 16.67, 12.50…)

## Concurrency, and the trap in it

Charto runs a round's tool calls in a `for` loop — free when every call was
10ms of local SQLite, expensive now that half of them cross to Azure. Pivotted
runs them in a pool (max 8).

The catch: Charto's request state (`ds._req.symbol`, the scene buffers) lives
in `threading.local()`. A worker thread inherits none of it, and `run_tool`
falls back to a hardcoded `RELIANCE` — every number real, and belonging to the
wrong company. `tools._call` re-establishes that state per call, from the
call's own argument. There is a cross-symbol contamination test in the commit
message; re-run it if you touch that function.

## Wiring into Pivot's chat tab

Pivotted serves `POST /chat/stream` in **Pivot's own SSE dialect**
(`start` / `tool_start` / `tool_done` / `delta` / `done{response}`), so the
de-wiring is a base URL and nothing else:

```bash
# pivot-next/.env.local
NEXT_PUBLIC_PIVOTTED_BASE=http://localhost:5175
```

Comment that line out and Pivot's chat is back. `ChatDemo.tsx` has one guarded
branch and is otherwise untouched.

`logiccard` and `raw_data` are deliberately never emitted, so no card renders
and nothing is committable — which is the whole split.

Its own dialect is on `POST /chat` (`{messages, stream?}` → `{text, usage,
tools_used, rounds}`), which is easier to read when debugging.

## Next

The capability work we scoped but have not built: valuation percentile vs a
company's own history (`/api/markets/metric-series` is still a stub returning
`available: false`), promoter holding and pledge, concall transcripts scored
against what actually happened, quality-of-earnings flags, and cross-sectional
percentile ranks. All were deferred deliberately; none are blocked.
