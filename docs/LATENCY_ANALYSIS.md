# Chat latency analysis & reduction plan — 12 Jun 2026

Measured live against the running stack (backend :8000, Azure Postgres
Central India, Azure OpenAI `deploymentpivot111`, model `gpt-5.4-mini`,
`reasoning_effort=minimal`). Probe: event-level SSE timing on
`/chat/stream` (the endpoint the FE actually uses), cold + warm run per
class. The metric that matters: **FIRST_DELTA** — when the user first
sees text streaming.

## 1. Measured baseline (this is what the user feels)

| Class | first_byte (`start` ev) | tool done | **FIRST_DELTA** | done |
|---|---|---|---|---|
| greeting (1 LLM hop, 0 tools) | 0.26s | — | **4.6–5.2s** | 5.9–6.3s |
| price (2 hops + get_live_price) | 0.27s | 4.0–5.0s | **8.6–10.2s** | 10.7s |
| fundamentals (2 hops + 2 tools) | 0.27s | 7.0–8.7s | **10.6–11.5s** | 12.3s |
| analysis (2 hops + 2 tools) | 0.26s | 5.2–6.8s | **9.5–10.8s** | 11.1–12.3s |
| build_agent (skeleton fast-path, **0 LLM**) | 0.26s | 0.27s | **0.27s** | **0.27s** |

Supporting network/infra measurements:

| Probe | Result |
|---|---|
| Azure Postgres `SELECT 1` (warm conn) | **65ms** / round trip |
| Azure OpenAI endpoint TCP connect | **~306ms** (≈1 RTT → resource is in a far region, ~US) |
| Azure OpenAI TLS handshake complete | **~840ms** |
| `api.openai.com` TCP connect (edge, for contrast) | 18ms |
| Assembled `chat` system prompt | **132,587 chars ≈ 33,100 tokens** per hop |

## 2. Where the time actually goes

The three suspects ranked by measured guilt:

### (a) Azure LLM hops — ~85–95% of wall clock. Everything else is noise.
Each hop costs **~4–5s to first token**, and any tool turn pays it
twice (hop 1 decides the tool, hop 2 narrates). Decomposition per hop:

| Component | Cost | Why |
|---|---|---|
| TCP+TLS handshake | **~0.85s** | `openai_client.py:254` and `:350` open a fresh `httpx.AsyncClient` **per request** — no keep-alive, every hop renegotiates TLS with a ~306ms-RTT endpoint |
| Request RTT | ~0.3s | resource deployed in a far region; your users + DB are in India |
| Prefill | ~1–3s uncached | **33k-token system prompt** re-sent every hop; `prompt_cache_key` exists (`tool_router.py` route-stable keys) but warm runs showed little gain — cache hits are unreliable (per-route keys fragment the cache; Azure cache TTL is minutes) |
| Reasoning + first output token | ~1–2s | already `minimal`; mini-class model on a loaded region |

### (b) Server pre-work (auth, Redis history, router, prompt assembly, DB)
**0.26–0.27s total** to the `start` event. Azure PG at 65ms/RT is fine
here because the hot path leans on Redis (history, pending, draft) and
only a few PG round trips. **Not the problem.**

### (c) Tool execution
`get_live_price`, `fetch_fundamentals`, `get_price_history` all returned
in **~0ms** in the probe (Redis-cached). Cold paths: screens 0.5–3s,
yfinance history 100–500ms, backtests are their own world (10–60s,
async by design). Tools are **mostly not the problem** — with two
exceptions noted in P2.

The skeleton fast-path (0.27s end-to-end) is the existence proof: when
no Azure hop happens, the product feels instant.

## 3. The plan

### P0 — this week, no architecture change (cuts ~40–50%)

1. **Reuse the HTTP connection** (hours, zero risk — biggest single win).
   Module-level singleton `httpx.AsyncClient` (HTTP/2, keep-alive) shared
   by `complete()` and `stream_openai()` instead of `async with
   httpx.AsyncClient(...)` per call at `backend/llm/openai_client.py:254,350`.
   **Saves ~0.8–1.1s per hop, ~1.6–2.2s per tool turn.**

2. **Move the Azure OpenAI resource to a near region** (config + redeploy).
   The DB already lives in Central India; the LLM resource answers from
   ~300ms away. Deploy `gpt-5.4-mini` in Central India if available,
   else South India / Southeast Asia (~40–80ms). If Azure region options
   are poor, A/B OpenAI direct (`api.openai.com`, 18ms edge) with the
   same model and compare TTFT — keep whichever wins.
   **Saves ~0.3–0.5s per hop, more on cold connects.**

3. **Make the prompt cache actually hit.**
   - Log `cached_tokens` per hop (already in `breakdown`) and alert when
     hit-rate < 80%.
   - Keep-warm ping: a scheduler job fires one 1-token request per
     route cache key every ~4 min during market hours so the 33k-token
     prefix stays cached.
   - Reduce key fragmentation: collapse near-identical toolsets into one
     canonical set per intent family (fewer distinct `prompt_cache_key`s
     = fewer cold prefills).
   **Saves ~1–2s on every hop that would have missed.**

### P1 — structural, ~1 week (gets FIRST_DELTA to ~2–3s on tool turns)

4. **Shrink the per-hop prompt from 33k tokens to ≤12k.**
   `system.md` (2,037 lines) ships in full on every hop. Split it:
   always-on core (identity, boundaries, voice, formats — the draft in
   `docs/drafts/SYSTEM_IDENTITY_DRAFT.md` is the start, ~8k incl.
   examples) + route-conditional sections the tool router already knows
   how to pick (options rules only on F&O routes, backtest rules only on
   backtest routes, workflow-amendment rules only when a draft is
   active). Smaller prefix = faster uncached prefill, faster cache
   writes, cheaper tokens. **Quality guard: run the existing eval
   harnesses (auto_batch_eval / quality) before+after.**

5. **Cheap narration hop.** Hop 2 re-sends the full 33k prompt just to
   turn a tool JSON into prose. Use the existing `narrate_tool_result`
   role: narration call = tiny system prompt (~1k) + user msg + tool
   result, no tools. Prefill drops 30k → ~2k.
   **Saves ~1–2s on every tool turn; also halves token cost.**

6. **Stream an acknowledgment from hop 1.** The model can emit text AND
   a tool call in the same response. Let hop 1 stream one short line
   ("Pulling TCS quote…") before/with the function call so FIRST_DELTA
   moves from ~9s to **~hop-1 TTFT (~2s after P0)**. If prompting for
   dual output proves flaky, fake it deterministically: when the router
   confidently predicts the route, emit a canned per-route ack delta at
   t≈0.3s (the FE already renders the witty ticker — give it words).

7. **Overlap tool execution with the stream.** Execute a tool the moment
   its `function_call` item completes in the hop-1 stream (don't wait
   for `response.completed`), and `asyncio.gather` multiple tool calls
   (the loop currently executes sequentially). Router-predicted
   prefetch: on a price/analysis route, fire `get_live_price` /
   `get_price_history` for the parsed symbol concurrently with hop 1 —
   by the time the model asks, Redis already has it.

### P2 — DB & tools (smaller, do opportunistically)

8. **Screens & fundamentals:** Redis-cache `screen_fundamentals` result
   sets (keyed by normalized filter, TTL ~1h in-session) and pin the
   financials hot tables into a local daily snapshot (SQLite/parquet) —
   screens go 0.5–3s → <100ms and stop paying 65ms/RT × N queries.
9. **Batch the PG round trips** on the chat path into one query where
   possible (user context: holdings + workflows + watchlist in a single
   round trip / view) — worth ~100–150ms.
10. **Keep backtests async** (already are) but emit `tool_start`
    progress events so long runs never look frozen.

### P3 — experiments (validate with the bench, keep winners)

11. **Two-tier models:** `gpt-5.4-nano` for hop 1 (tool selection is a
    classification task; the router already narrows to 8–12 tools) and
    mini only for final prose. Hop-1 TTFT should drop well under 1s.
12. **Single-hop design for the top routes:** for price/news/snapshot,
    skip hop 2 entirely — render the card from tool JSON and let hop 1's
    streamed ack be the prose. The FE cards already carry the data.

## 4. Expected trajectory (FIRST_DELTA, warm)

| Stage | greeting | price/analysis turn |
|---|---|---|
| Today | 4.6s | 8.6–10.8s |
| After P0 (connection + region + cache) | ~2.0–2.5s | ~4.5–6s |
| After P1 (slim prompt, cheap narration, hop-1 ack, overlap) | ~1.2–1.8s | **~1.5–2.5s to first text**, ~4–5s to done |
| After P2/P3 | ~1s | ~1–2s consistently |

## 5. How to verify

`pivot/scripts/latency_bench.py` (TTFB/TOTAL) is necessary but not
sufficient — its "TTFB" is the `start` event (0.27s today), not visible
text. Add the event-level probe (FIRST_DELTA per class, plus
`latency_breakdown` from the `done` event: `llm_hop_N`,
`llm_hop_N_cached`, `tool_*`) as `scripts/latency_probe.py` and run it
once after each P0/P1 change lands. Per the quality-check triad, report
tokens + latency + quality verdict together when the prompt-slimming
work (P1.4/P1.5) ships.
