# Chat latency dissection — 2026-07-03

One instrumented live pass (13 grouped prompts + 1 unmeasured warmup) against
the real `/chat/stream` pipeline on `:8000` (Azure gpt-5.4-mini, reasoning
effort `minimal`→`none`). Four independent measurement layers, cross-checked:

1. **Client** — perf_counter timestamp of every SSE event (first delta = user-perceived TTFT).
2. **Server** — `latency_breakdown` from the `done` event (`llm_hop_N`, `tool_X`).
3. **LLM** — `PIVOT_LLM_TRACE` JSONL: per-call wall latency, TTFT, input/cached/output/reasoning tokens.
4. **IO** — new `PIVOT_PERF_TRACE` (backend/services/perf_trace.py): every SQL
   statement + Redis command with duration, caller, and conversation id.

Harness: `pivot/scripts/latency_probe.py`. Raw report:
`Markdowns/chat_latency_report_2026-07-03.json`. Environment caveats, stated
honestly: run at 15:54 IST (just post-close); Kite daily token expired →
some tools fell back to yfinance; a live FE tab and the screener background
fundamentals-warm were running concurrently (filtered out of per-prompt
attribution by conv-id/caller, but they DO load the same Azure DB).

## Per-prompt results (latency + tokens + quality — the triad)

| group | total | TTFT (first text) | LLM ms (hops) | tok in / cached / out | SQL n/ms | Redis n/ms | quality |
|---|---:|---:|---:|---|---:|---:|---|
| baseline_fastpath ("hi") | 0.52s | 0.51s | 0 (0) | — | 6/348 | 3/110 | PASS (fast-path, server 113ms) |
| capability | 5.2s | 3.0s | 3.8s (1) | 53.8k / 0 / 91 | 10/591 | 17/690 | PASS |
| read_single_price | 9.2s | 7.6s | 6.6s (2) | 102.6k / 51.2k / 52 | 11/754 | 18/748 | PASS (honest yfinance tag) |
| read_single_analysis | 19.7s | 13.4s | 13.1s (2) | 106.7k / 52.2k / 880 | 38/2659 | 22/979 | PASS (rich, sectioned) |
| read_multi_compare | 14.3s | 10.0s | 9.3s (2) | 106.3k / 52.2k / 522 | 64/3290 | 23/890 | PASS (markdown table) |
| read_screen_multi | **50.7s** | 49.0s | 6.1s (2) | 105.2k / 52.2k / 360 | 12/**43431** | 44/1983 | MARGINAL (microcaps first: `HITKITGLO`) |
| order_entry_single | 6.5s | 4.4s | 4.1s (2) | 95.8k / 47.6k / 90 | 11/660 | 23/890 | PASS (registered + honest caveat) |
| alert_trigger_price | 6.5s | 6.3s | 4.9s (2) | 57.0k / 9.6k / 105 | 11/650 | 20/834 | **CHECK** — narration says "places the configured order" for an *alert* verb |
| auto_indicator_entry_exit | 10.0s | 10.0s | 8.4s (3) | 57.0k / 2.8k / 203 | 12/755 | 16/640 | MARGINAL (10s to ask ONE clarify question) |
| auto_schedule | 0.52s | 0.52s | 0 (0) | — | 6/346 | 3/120 | PASS (skeleton path, server 127ms) |
| fno_chain | 6.7s | 4.8s | 4.8s (2) | 97.2k / 47.1k / 201 | 17/946 | 19/745 | PASS (option_chain_card) |
| fno_strategy | 11.9s | 8.6s | 8.8s (2) | ≥47.7k / 0 / ≥43 (hop2 usage missing) | 19/967 | 26/983 | PASS (bull put spread) |
| backtest_indicator | 14.0s | 11.5s | 10.7s (2) | 109.5k / 54.3k / 124 | 11/1106 | 18/800 | **FAIL** — "backtest service is rate-limited" after burning 13.6s |

Cross-checks held: client_total ≈ server_total + ~0.4s constant (auth ~40ms
Redis + `_kite_token_for` 2×~75ms SQL + network/SSE); server breakdown parts +
measured IO ≈ total within ~0.2s everywhere.

## Where the time actually goes (ranked)

### 1. LLM prompt ingestion — NOT reasoning (~60–90% of every LLM-bound turn)

Reasoning tokens were **0 on every hop** (effort `minimal`→`none` confirmed
on the wire). The time is *prefill*: **every chat hop ships ~161,800 chars
(~52k tokens)** — `prompts/system.md` (~2,300 lines) + 13–32 routed tool
schemas + REPLY-CLASS directive — and a normal tool turn does it **twice**
(tool-selection hop + answer hop).

- Hop-1 is **prompt-cache COLD in every new conversation** (`cached=0` on 11
  of 12 LLM turns; one partial hit of 9.6k). TTFT 1.3–3.9s, hop total 1.9–6.3s.
- Hop-2 hits the cache (~52k cached) but still pays 1.2–2.8s TTFT + decode.
- Why cold: the router narrows the toolset per intent (9 distinct subsets in
  13 prompts). Tool schemas serialize into the cached prefix, so *every
  intent family fragments the cache*; `prompt_cache_key` (per-subset) can't
  help when the bytes differ. Cross-conversation reuse only happens for
  same-subset traffic inside the provider's ~5–10 min cache TTL.
- The deployment's TTFT floor is ~1.2s even fully cached — 2 hops ⇒ ~2.5s
  floor before any tool or decode work.

**This answers the core question: "why so slow even at low reasoning" — the
model is spending its time reading a 52k-token prompt twice per turn, not
thinking.**

### 2. The screener tool — one 42.7-second SQL statement

`screen_by_fundamentals` (fundamentals_screen.py:482) ran a single query:
`WITH m_pe AS (SELECT DISTINCT ON (sl.sc_id) … FROM mc.statement_lines WHERE
line_item = ANY(…))` — **no company narrowing before the metric CTEs**, so it
scans the whole market's statement lines on Azure PG: **42,745ms**. The
concurrent FE background fundamentals-warm was firing its own 4–7s
`fetch_gate_inputs` queries at the same table throughout, contending.

### 3. Azure round-trip tax: ~1.3–2s per LLM-bound turn

Both Postgres and Redis are in Central India (~40–80ms/op from this machine).
Per turn, measured: **10–20 SQL + 16–26 Redis calls, all sequential, all
sync-in-async-loop**. Named offenders per turn:

- `conversation_store` (Redis): `get_active_draft` called **4×** (~150ms),
  plus `get/clear_pending`, `clear_pending_resolution`, `append` — ~0.5–0.8s.
- `_persist_turn` (chat router): 3-4 statements ~0.3s (post-stream, hidden
  from perceived latency but holds the worker).
- `llm_cost.record_llm_usage`: 2×~80ms **inline in the turn**.
- `_kite_token_for`: 2 queries ~150ms every request.
- `auth.revocation.is_revoked`: ~40ms every request.

### 4. fetch_fundamentals: 3.4–3.7s per call — classic N+1

The compare turn issued **31× `_get_line_item_value` + 18× `resolve_symbol`**
sequential financials-DB queries (~2.3s of pure RTT) per 2-symbol comparison.

### 5. Tool sub-LLM calls on automation turns

`propose_dsl_workflow` internally runs its own slim LLM call (~3.4k chars,
1.6–1.9s) — a good pattern (small prompt!) but it stacks: hop1 (52k) +
propose sub-call + (skipped) narration hop.

### 6. What is NOT the problem

- The deterministic fast paths are excellent: "hi" 113ms, scheduled-buy
  skeleton **127ms** server-side, full drafts included.
- The main `pivot_db` operational queries (~75ms each) are RTT-bound but few.
- Reasoning effort is already minimal; no reasoning tokens were paid anywhere.

## Fix list (impact-ranked)

**P0-a — Screener query.** Narrow to the sector/universe symbol set *before*
the metric CTEs (the tool already knows sector='it'), add a covering index
(`sc_id, line_item, period_end`) or — better — read the same warmed metrics
table the FE screener uses instead of raw `statement_lines`. 50s → ~1-2s.

**P0-b — Shrink the per-hop prompt.** 52k tokens/hop is the latency. The
router already classifies intent; ship an intent-sliced system prompt
(core contract + relevant sections, ~10–15k tokens) instead of all of
system.md on every hop. Expected: hop TTFT 2.5–3.9s → ~0.8–1.5s cold.

**P0-c — Stop re-sending tool schemas on the answer hop.** Hop-2 exists to
narrate a tool result; it doesn't need 13–32 schemas re-serialized (the
propose sub-call already proves the slim pattern at 3.4k chars). Also
stabilizes the cache prefix.

**P1-a — Batch the conversation_store.** One pipelined MGET for
draft/pending/resolution state instead of 8–12 serial GETs (~0.6s → ~50ms);
`get_active_draft` alone is read 4× per turn — read once, pass down.

**P1-b — Move per-turn bookkeeping off the hot path.** `record_llm_usage`
(2×80ms) → queue/batch; `_kite_token_for` → per-user in-process TTL cache
(150ms/turn); `is_revoked` → short in-process TTL cache (40ms).

**P1-c — Fewer distinct tool subsets.** 9 subsets in 13 prompts fragments the
prompt cache. Collapse to ~3–4 stable intent-family supersets so hop-1 hits
warm prefixes for repeat traffic.

**P2-a — fetch_fundamentals N+1** → one batched line-items query per symbol
set (~3.5s → <1s).

**P2-b — Bugs found by the probe** (correctness, not latency):
- `backtest_workflow` returned "backtest service is rate-limited" on a fresh
  conversation — find/loosen that limiter (it also reported `tools_called=[]`
  while the breakdown shows the tool ran — reporting inconsistency).
- Alert-verb prompt produced draft narration "places the configured order" —
  verify the notify-not-order hard gate on `propose_dsl_workflow` args.
- fno_strategy hop-2 stream closed without usage data in the trace — check
  `response.completed` handling for that path.

## Expected end-state if P0/P1 land

Simple read turn: ~6.5–9s today → **~2.5–4s**. Analysis turn: ~20s → ~8–10s
(decode of an 800-token answer is ~6s of it; consider trimming ANALYSIS word
budget or accepting it). Screener: 50s → ~2s. Automation draft: 6.5–10s →
~4–5s. Deterministic paths stay ~0.1–0.5s.
