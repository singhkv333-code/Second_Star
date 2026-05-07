# Pivot Chat — Model Architecture & Performance Snapshot

A short reference for outside reviewers (including other LLMs) to
understand how a user message becomes a response, where the time goes,
how tokens are spent, and how caching is layered. Numbers in this
document were measured live on `dev` on 2026-05-07 against
`gpt-5-mini` on the OpenAI Responses API.

---

## 1. Request lifecycle

```
FE  ──────────►  POST /chat ─────────────────────────────────►  Backend
                                                                     │
                  ┌───────────────────────────┐                     │
                  │ pre-LLM short-circuits    │                     │
                  └───────────────────────────┘                     ▼
                  Backtest router fast-path  ◄── routers/chat.py:_run_indicator_backtest
                  Fast-path classifier        ◄── services/fast_path.py
                                                  (greetings, defs, continuations)
                  Workflow-skeleton fast-path ◄── chat_service._workflow_skeleton
                  Cancel-draft fast-path
                                                                     │
                  ┌───────────────────────────┐                     │
                  │ context loading           │                     │
                  └───────────────────────────┘                     ▼
                  Load active_draft / pending state from Redis  ◄── conversation_store
                  Independent-prompt eviction (drop stale draft)
                  Mode-override eviction (Automation / Backtest pill)
                  Load last 6 turns of history (CONV_PROMPT_WINDOW_TURNS = 6)
                                                                     │
                  ┌───────────────────────────┐                     │
                  │ routing                   │                     │
                  └───────────────────────────┘                     ▼
                  select_tool_names(message)              ◄── services/tool_router.py
                    └── 17 regex rules + 6-tool always-include floor
                  classify_intent(message) → agent | automation | backtest | other
                  Mode-pin override (Automation strips macros, Agent strips orders, etc.)
                  Typo-continuation guard (active-draft + bare token → strip orders/macros)
                  Advisory-strip ("should I…" + no build/agent keyword → strip macros)
                                                                     │
                  ┌───────────────────────────┐                     │
                  │ prompt assembly           │                     │
                  └───────────────────────────┘                     ▼
                  build_system_prompt():
                    [1] chat role text from system.md   (≈41,644 chars / ≈10 K tokens)
                    [2] agentic_examples.json           (cached, byte-stable)
                    [3] domain primer                   (cached)
                    [4] dynamic user context            (≈80 chars, portfolio totals)
                  Append mode-pin as second system msg if Automation/Agent/Backtest
                  Append last 6 history turns (user + assistant + function_call_output)
                  Append synthetic ASK_USER tool def
                                                                     │
                  ┌───────────────────────────┐                     │
                  │ LLM hop loop              │                     │
                  └───────────────────────────┘                     ▼
                  POST /v1/responses
                    payload.tools     = N filtered tool defs (5–25 per turn)
                    payload.tool_choice = "required" on hop 1 for agent intent, else "auto"
                    payload.reasoning.effort = "minimal" for agent, "low" elsewhere
                    payload.prompt_cache_key = pivot-chat-v2-<hash(sorted(tool_names))>
                    payload.max_output_tokens = 1500
                  Loop: hop_index < _MAX_TOOL_CALLS (8)
                    For each function_call → execute via tool_executor / v2 handler
                    Append function_call_output to messages
                  propose_workflow gets 1 retry on validation error (max 2 attempts).
                                                                     │
                  ┌───────────────────────────┐                     │
                  │ post-processing           │                     │
                  └───────────────────────────┘                     ▼
                  _post_process(text):
                    strip <TOOL_CALL>…</TOOL_CALL> blocks
                    strip <PLACEHOLDER> tokens
                    _strip_reasoning_leakage    ◄── kills "the user now says…" monologue
                    _LATENT_GREETING_RE check
                  _format_recoverable_failure_question on validation errors
                  _is_repeat_fallback → vary if same canned msg fired last turn
                  Stash draft to chat:active_draft if macro tool succeeded
                  Append (user_msg, assistant_msg) to chat:conv list (1h TTL)
                                                                     │
                  ◄────────────────────────────────────────────────  Response JSON
```

---

## 2. Tool surface

| Layer | Count | Notes |
|---|---|---|
| `ALL_TOOLS` (declared in `agents/tools.py`) | **63** | Includes stubs (F&O, fundamentals) that aren't wired. |
| `_REAL_TOOLS` whitelist (`services/tool_registry.py`) | **53** | Only these are sent to the LLM. Stubs hidden. |
| Router rules (`services/tool_router.py:_RULES`) | **17** | Regex rules. Each matched rule unions a tool set into the visible list. |
| Always-include floor | **6** | `propose_workflow`, 4 macro tools, `ASK_USER` (synthetic). The model always sees the agent-build escape hatches. |
| Fallback floor (when no rule matches) | **6** | Read tools — `get_live_price`, `get_holdings`, `get_portfolio_summary`, etc. |
| Visible to LLM **per turn** | **5–25** | Median ≈ 10. Narrowed from 53 by the router. |

**Schema format.** All tools are sent as Responses-API `function`
items with `strict=True`. JSON schemas use `Literal`, `enum`,
`minimum`, `maximum`, `default` constraints — Pydantic v2 models
back validation server-side. The LLM emits tool args as a JSON
string; `_parse_response` decodes it (or surfaces `_parse_error`
on malformed JSON for a focused retry).

**Cache key per route.** `tool_router.cache_key_for(selected_names)`
returns `pivot-chat-v2-<8 hex>`. Each unique tool subset signs its
own slot in OpenAI's prompt cache, so route changes don't pollute
each other's prefixes. Observed: 4 distinct keys in a 13-turn probe
across 3 conversations.

**Hop budget.** `_MAX_TOOL_CALLS = 8`. Most turns finish in 1–2 hops.
`propose_workflow` validation failures get 1 retry (so 2 LLM attempts
on agent-build turns). `tool_choice="required"` on hop 1 of agent
turns forces a tool call (no think-aloud on the easy path).

---

## 3. Token usage (measured)

13-turn probe across 3 conversations on 2026-05-07,
`gpt-5-mini`, reasoning effort = low/minimal:

| Metric | Value |
|---|---|
| Avg input tokens per turn | **22,413** |
| Avg cached tokens per turn | **19,210** |
| Avg cache hit ratio | **86.0 %** |
| Min hit (cold-start on a brand-new route) | **0 %** |
| Max hit (warm route) | **100 %** |
| Avg output tokens per turn | ~150 (chat reply) – ~800 (workflow draft) |

**System message structure on every call:**

```
role=system     chars=41,644   ←  cached prefix (system.md + agentic_examples + domain primer)
role=system     chars=    83   ←  dynamic user context (portfolio totals)
…history turns…
role=user       chars=variable
```

The 41,644-char prefix is what OpenAI's prompt cache locks onto.
Adding any dynamic content into it breaks the cache; portfolio
totals are kept in a SECOND short system message for that reason.

**Cache breakpoint behaviour.** Cold-starts on a never-before-seen
route hit 0 % once, then immediately warm to ≥96 % on turn 2 of the
same route. Routes stay warm for ~5 min idle (OpenAI's TTL).

---

## 4. Latency profile

| Path | Typical latency |
|---|---|
| Fast-path (greetings / edu / continuation) | **6–60 ms** |
| Workflow skeleton fast-path (deterministic shapes) | **20–80 ms** |
| Backtest router fast-path | **2–6 s** (yfinance + indicator computation) |
| LLM hop — read intent (e.g. "TCS price") | **2–4 s** |
| LLM hop — order draft (e.g. "buy 10 RELIANCE") | **4–9 s** |
| LLM hop — agent draft (`propose_workflow`) | **8–25 s** (≥2 hops, max 30 s) |
| Index-level cache hit | **1 ms** (vs 595 ms cold yfinance call) |
| Portfolio cache hit | **0 ms** (vs 4–200 ms broker round-trip) |
| Top-movers cache hit | **<1 ms** (vs ≈4 s yfinance batch download) |
| Tool router (regex) | **<1 ms** |
| Post-processing (sanitizer + strip) | **<1 ms** |

**Bottleneck.** Output decoding + LLM reasoning. The 86 % prompt-cache
hit means input tokens are nearly free (10× cheaper); latency is
dominated by output-token generation and reasoning trace. Reducing
reasoning effort from `low` → `minimal` cut p50 latency on agent
turns by ~10 s with no measurable quality loss.

---

## 5. Caching layers

Two distinct caches do different jobs.

### 5.1 OpenAI prompt cache (server-side, automatic)

- **Stored:** tokenized attention K/V for the prefix bytes of each
  request, keyed by `prompt_cache_key` + content hash.
- **Skips:** re-tokenisation of the prefix; prefix tokens billed at
  ~10 % of full price.
- **Does NOT skip:** model inference. GPT still runs.
- **Visibility:** we read `usage.input_tokens_details.cached_tokens`
  on each response and persist to the LLM trace.
- **TTL:** ~5 min idle, ~1 hr max (OpenAI-managed).
- **Current hit rate:** 86 % (measured).

### 5.2 Redis (our box, real `redis-server` on `:6379`)

| Key prefix | TTL | What's stored | Where |
|---|---|---|---|
| `chat:conv:{conv_id}` | 1 h | Last 20 message turns (rpush list) | conv history |
| `chat:active_draft:{conv_id}` | 1 h | Macro-tool draft for amendment | cross-turn state |
| `chat:pending:{conv_id}` | short | Pending tool-call awaiting confirmation | multi-hop state |
| `portfolio:summary:{user_id}` | **30 s** | Portfolio summary JSON | services/portfolio_cache |
| `portfolio:holdings:{user_id}` | **30 s** | Holdings list JSON | same |
| `index:level:{ticker}` | **10 s** | Index value + change | routers/markets._fetch_index |
| `top_movers:{universe}:{direction}:{limit}` | **60 s** | Top gainer/loser rows | services/top_movers |
| `chart:{symbol}:{period}:{interval}` | ~5 min | yfinance OHLC | market/yfinance_service |
| `backtest:{sha1(strategy)}` | 1 h | Backtest result JSON | routers/backtest |
| `yield:mf:{scheme_code}` | 1 h | Yield value | agents/yield_scanner |
| `webhook:rate:{src}` | 70 s | Inbound webhook count | rate limiter |

Mock fallback (`backend/cache.py:MockRedis`) implements only `get` /
`set` / `delete` / `exists` / `ping` — list / hash ops fail
silently when real Redis is unavailable. Production uses real Redis.

---

## 6. Where the latency / token budget goes

For a typical agent-build turn (`"build me a workflow that…"`):

```
22 K input tokens × 0.86 cache hit  ≈  3 K billed at full rate, 19 K at 10%
800 output tokens                    ≈  full rate
2 LLM hops                           ≈  16 s wall clock
```

For a typical price query turn (`"TCS price"`):

```
22 K input tokens × 0.97 cache hit  ≈  650 billed at full rate
~80 output tokens                    ≈  full rate
1 LLM hop + 500 ms tool execution   ≈  2.5 s wall clock
```

---

## 7. Pre-LLM short-circuits (paths that DON'T hit GPT)

| Short-circuit | Trigger | Latency |
|---|---|---|
| Backtest router fast-path | message matches indicator-backtest regex | 2–6 s |
| Fast-path classifier — greeting | exact-match: hi / hello / good morning | 30–60 ms |
| Fast-path — thanks | exact-match: thanks / ty | 30–60 ms |
| Fast-path — help | exact-match: what can you do / help | 30–60 ms |
| Fast-path — edu definitions | "what is RSI" etc. (28 curated terms) | 20–35 ms |
| Fast-path — continuation | "what else" / "anything else" / "what now" | 6–25 ms |
| Workflow skeleton fast-path | deterministic agent shapes (RSI threshold, scheduled order) | 20–80 ms |
| Cancel-draft fast-path | "cancel" / "discard" with active draft | <50 ms |

Together these absorb roughly 10–15 % of traffic without touching
the LLM, at near-zero token cost.

---

## 8. Known correctness/UX guards (for context, not deep dive)

- **Reasoning-leak sanitizer:** strips paragraphs containing ≥2
  internal-monologue tells (e.g. `"the user now says…"`,
  `"we must answer…"`).
- **Typo-amendment guard:** when an active draft exists AND the
  message is a short bare alphabetic token not in a known keyword
  set, strip order + macro tools so the model can't re-emit the
  prior card.
- **Repeat-fallback variation:** if the same canned reject would
  fire two turns in a row, return a reset prompt instead.
- **Mode pins:** Automation strips macros, Agent strips order
  tools but allows all four macros, Backtest narrows to backtest
  + read tools.
- **Schema-level guards:** `notify.message.channel` is now
  `Literal["push"]` only — email/SMS asks fail validation and
  route through the email-aware canned reject that names the gap.

---

## 9. What outside review can help with

- **Output token reduction.** Workflow drafts typically produce
  500–1000 output tokens (description + steps + rationale). Most
  of that is the rationale; consider trimming or making it
  optional.
- **Reasoning effort.** Currently `low` for non-agent and `minimal`
  for agent. `medium` improved quality on multi-trigger drafts but
  blew past client timeouts. A targeted `medium` for retry-only
  could be tested.
- **Prompt prefix size.** 41,644 chars is large. Some sections of
  `system.md` (the calibration examples in particular) might be
  trimmable without loss. Worth measuring per-section impact.
- **Cold-start mitigation.** First call on a new tool-route signature
  is at 0 % cache. Pre-warming the top N route signatures at process
  start would smooth the p99.
- **Tool surface tightening.** 5–25 visible per turn is wide. Could
  go narrower for clearly-classified intents (e.g. "show portfolio"
  doesn't need workflow macros in scope).

---

## 10. Telemetry & where to look

- LLM trace (when enabled): `PIVOT_LLM_TRACE=/tmp/llm_trace.jsonl`
  — one record per LLM call: caller, tool count, tool names, cache
  key, input/cached/output/reasoning tokens, latency, ttft, error.
- Redis state: `redis-cli --scan` for live keys, `redis-cli ttl <k>`
  for TTL.
- Server log: `/tmp/uvicorn.log` (verbose with SQL traces).
- Probe scripts:
  - `pivot/scripts/cache_probe2.py` — token / cache stats
  - `pivot/scripts/redis_verify.py` — Redis path verification
  - `pivot/scripts/regression_trace.py` — basket / market_relative / cross_draft
  - `pivot/scripts/leak_regression.py` — reasoning-leak sanitizer
  - `pivot/scripts/typo_amend_regression.py` — bare-token re-emit guard
  - `pivot/scripts/email_regression.py` — email-substitution guard
  - `pivot/scripts/broad_tester.py` — 14-category coverage suite

---

**Source files of interest** (relative to repo root):

```
pivot/backend/services/chat_service.py     — orchestrator (~3,300 lines)
pivot/backend/services/tool_router.py      — regex rules + cache key
pivot/backend/services/tool_registry.py    — _REAL_TOOLS whitelist + dispatcher
pivot/backend/services/fast_path.py        — pre-LLM short-circuits
pivot/backend/services/portfolio_cache.py  — 30s portfolio cache
pivot/backend/services/top_movers.py       — yfinance top movers + seed
pivot/backend/agents/tools.py              — 63 tool definitions
pivot/backend/agents/tool_executor.py      — chat tool dispatch
pivot/backend/llm/openai_client.py         — Responses API wrapper
pivot/backend/llm/_trace.py                — JSONL trace writer
pivot/backend/prompts/system.md            — chat-role instructions
pivot/backend/prompts/agentic_examples.json — calibration examples
pivot/backend/prompts/assembler.py         — system prompt assembly
pivot/backend/workflows/registry.py        — STEP_REGISTRY (workflow steps)
pivot/backend/workflows/schemas.py         — step config Pydantic models
pivot/backend/workflows/steps/             — executors (fetches/actions/notify)
pivot/backend/workflows/propose.py         — propose_workflow → WorkflowDraft
pivot/backend/cache.py                     — Redis client + MockRedis fallback
```
