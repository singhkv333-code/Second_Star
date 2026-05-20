# Pivot Chat — Model Architecture, Context & Cost Pipeline

Single reference for how a user message becomes a response: which paths
short-circuit before the LLM, how the system prompt is assembled, how
prior turns / drafts / pending tool calls are carried across turns, how
tool errors are fed back to the model, and how every token gets priced
and persisted. Numbers cited from probes were measured live on `dev`
against `gpt-5-mini` on the OpenAI Responses API.

Source files referenced throughout sit under `pivot/backend/`.

---

## 1. Request lifecycle (top-down)

```
FE ──► POST /chat ──► routers/chat.py ──► ChatService.handle()
                                              │
              ┌──────────────────────────────┐│
              │ A. Pre-LLM short-circuits    │▼
              │    (services/fast_path.py,   │  Backtest router fast-path
              │     routers/chat.py,         │  Greetings / definitions / continuations
              │     services/workflow_skel…) │  Workflow-skeleton fast-path
              │                              │  Cancel-draft fast-path
              └──────────────────────────────┘
                                              │
              ┌──────────────────────────────┐│
              │ B. Context loading           │▼
              │    (services/conversation_   │  Load chat:active_draft / chat:pending
              │     store.py)                │  Independent-prompt eviction (drops stale draft)
              │                              │  Mode-override eviction (pill switch)
              │                              │  Load last 6 history turns (window)
              └──────────────────────────────┘
                                              │
              ┌──────────────────────────────┐│
              │ C. Routing                   │▼
              │    (services/tool_router.py) │  select_tool_names(message) → 17 regex rules
              │                              │  classify_intent → agent | automation |
              │                              │      backtest | other
              │                              │  Mode-pin / typo-amend / advisory strips
              └──────────────────────────────┘
                                              │
              ┌──────────────────────────────┐│
              │ D. Prompt assembly           │▼
              │    (prompts/assembler.py)    │  [1] role text from system.md
              │                              │  [2] agentic_examples.json (cached prefix)
              │                              │  [3] domain_primer.md (cached prefix)
              │                              │  [4] dynamic user-context block (≈80 chars)
              │                              │  + Mode-pin as second system msg if any
              │                              │  + Last 6 history turns
              │                              │  + Synthetic ASK_USER tool def
              └──────────────────────────────┘
                                              │
              ┌──────────────────────────────┐│
              │ E. LLM hop loop              │▼
              │    (llm/openai_client.py,    │  POST /v1/responses
              │     services/chat_service)   │    tools = filtered tool defs (5–25 / turn)
              │                              │    tool_choice = "required" on hop 1 for
              │                              │      agent intent, else "auto"
              │                              │    reasoning.effort = "minimal" for agent,
              │                              │      "low" elsewhere
              │                              │    prompt_cache_key = pivot-chat-v2-<hash>
              │                              │    max_output_tokens = 1500 (50 post-macro)
              │                              │  hop_index < _MAX_TOOL_CALLS (8)
              │                              │  propose_workflow / backtest_workflow
              │                              │    get 1 retry on validation error
              └──────────────────────────────┘
                                              │
              ┌──────────────────────────────┐│
              │ F. Tool exec + completeness  │▼
              │    (services/tool_registry,  │  execute_with_completeness(name, args)
              │     agents/tool_executor.py, │  ASK_USER / needs_clarification →
              │     services/validation_…)   │      surface question + stash chat:pending
              │                              │  success → append function_call_output
              │                              │  error   → format_recoverable_failure_question
              │                              │            OR _llm_clarification fallback
              └──────────────────────────────┘
                                              │
              ┌──────────────────────────────┐│
              │ G. Post-processing           │▼
              │    (services/chat_service)   │  _post_process: strip TOOL_CALL / PLACEHOLDER
              │                              │  _strip_reasoning_leakage
              │                              │  _is_repeat_fallback → vary canned msg
              │                              │  _ensure_widget_caption (cards always captioned)
              └──────────────────────────────┘
                                              │
              ┌──────────────────────────────┐│
              │ H. Persistence + ledger      │▼
              │    (conversation_store,      │  store.append(conv_id, user_msg, asst_msg)
              │     llm/_trace.py,           │  set_active_draft if macro succeeded
              │     services/llm_cost.py)    │  CallTrace.__exit__ → record_llm_usage
              │                              │    → llm_usage row + structured log line
              └──────────────────────────────┘
                                              │
                                              ▼
                                       ChatTurn JSON
```

---

## 2. Tool surface

| Layer | Count | Notes |
|---|---|---|
| `ALL_TOOLS` (`agents/tools.py`) | ~63 | Declared catalog incl. some stubs. |
| `_REAL_TOOLS` whitelist (`services/tool_registry.py`) | ~53 | Whitelisted shape; stubs hidden. |
| Router rules (`services/tool_router.py:_RULES`) | 17 | Regex rules; each match unions a tool family. |
| Always-include floor (`_ALWAYS_INCLUDE`) | 5 | 4 macros + synthetic `ASK_USER`. `propose_workflow` is intentionally excluded — only added by matching rules to save ~5.5K tokens on non-agent turns. |
| Fallback floor (`_FALLBACK_TOOLS`) | 6 | Read tools used when only `_ALWAYS_INCLUDE` matched. |
| Visible to LLM **per turn** | 5–25 | Median ≈ 10. |

All tools are sent as Responses-API `function` items with `strict=True`,
Pydantic v2 backing on the server. Args arrive as a JSON string;
`_parse_response` decodes it (or surfaces `_parse_error: True` for a
focused retry hop).

**Cache key per route.** `cache_key_for(selected_names)` returns
`pivot-chat-v2-<8 hex>`. `ASK_USER` is filtered out before hashing so
its presence/absence doesn't shift keys. Same routed toolset across
different conversations shares the same cached prefix.

**Hop budget.** `_MAX_TOOL_CALLS = 8`. Most turns finish in 1–2 hops.
`propose_workflow` and `backtest_workflow` are the only tools that get
1 self-correction retry on validation error (so 2 attempts max).
`tool_choice="required"` on hop 1 of agent intent + drop to `"auto"`
on every subsequent hop (otherwise the loop never exits).

---

## 3. Prompt assembly (`prompts/assembler.py`)

`build_system_prompt(role, user_context, extra_context)` is the **only**
function that builds a system prompt anywhere in the codebase. Layers,
in stable order:

1. **Role identity / instructions.** For `role="chat"` we load
   `prompts/system.md` (~41,644 chars / ~10K tokens). Other roles
   (`propose_workflow`, `narrate_tool_result`) carry their own short
   prose blocks inline.
2. **Calibration examples** (chat role only). `agentic_examples.json`
   rendered as labelled `prompt → tool(args) [conf=…]` blocks —
   ~40% cheaper than dumping JSON. Includes confidence + ASK cues.
3. **Domain primer.** `prompts/domain_primer.md` (always included).
4. **User context block** (only when supplied). Compact ~80-char
   summary: name, portfolio total, holdings count, active agents.

The three files (`system.md`, `agentic_examples.json`,
`domain_primer.md`) are loaded once with `@lru_cache(maxsize=1)` so the
byte-identical prefix is reused across every request. **This is the
prefix OpenAI's prompt cache locks onto.** Dynamic content (portfolio
totals, mode pins) is appended as separate system messages or as the
user/tool message stream — never spliced into the cached prefix.

Reload during dev: `prompts.reload_prompts()` clears all three caches
without a process restart.

---

## 4. Context retention across turns

Three keys, all keyed by `conv_id`. Implementation lives in
`services/conversation_store.py`.

| Redis key | TTL | Payload | Purpose |
|---|---|---|---|
| `chat:conv:{conv_id}` | 24 h | List of `{role, content}` (plain text only) | Conversation history; trimmed to `CONV_MAX_TURNS=20`; only last 6 (`CONV_PROMPT_WINDOW_TURNS`) injected per LLM call. |
| `chat:pending:{conv_id}` | 10 min | `PendingToolCall` JSON | Deterministic resume — when a tool emitted `needs_clarification` with `missing_field`, the next user reply is coerced into that field's type and the tool is re-executed without an LLM hop. |
| `chat:active_draft:{conv_id}` | 10 min | `ActiveDraft` JSON (`tool_name`, full draft args, last caption) | Multi-turn amendment of a workflow / order draft. Followup hint splices the JSON inline so the LLM amends the same shape rather than reconstructing from history text. |

Why these design choices land where they do:

- **No tool-call payloads in history.** Storage is `role + content` strings
  only. Storing assistant tool plans had caused `<TOOL_CALL>` text to
  leak into later turns; that path is closed.
- **Tight prompt window (6 turns, not 20).** Storage stays at 20 turns
  so the transcript is debuggable, but only the trailing 6 hit the LLM.
  Longer tails resurfaced stale tickers and stale drafts (the "user
  typed RELIANCE 5 turns ago, now asks 'sell it'" case).
- **10-min active-draft TTL.** Originally 1 h. A draft hanging around
  for an hour leaked into completely unrelated turns. 10 min is enough
  for natural amend-and-activate, short enough to clear on topic shift.
- **Independent-prompt eviction.** If the active draft was a buy-order
  card and the user types `"pros and cons of Reliance"`, the draft is
  evicted before the LLM hop — otherwise the model keeps amending
  yesterday's card.

Cancellation off-ramps:
- `_try_cancel_active_draft` matches `cancel / discard / nevermind /
  start over` and drops both `active_draft` and `pending`.
- Explicit `chat:pending` cancel clears it AND any active draft so the
  user can't get stuck in a cascade.

---

## 5. The LLM hop loop and error feedback

Implementation: `services/chat_service.py` (~4,500 lines). The loop is
**not** a validation-retry loop. The shape is:

1. **Hop 1.** `tool_choice` either `"required"` (agent intent) or
   `"auto"`. `max_output_tokens=1500` (drops to 50 on post-macro prose
   hops). `reasoning_effort="minimal"` for agent, `"low"` elsewhere.
2. **Read response.** Three outcomes:
   - `finish_reason="error"` → return `_unavailable()`. No retry.
   - `finish_reason="stop"` (final text) → post-process, persist,
     return.
   - `finish_reason="tool_calls"` → execute each call via
     `execute_with_completeness`.
3. **Tool result handling:**
   - **Completeness / ASK_USER** → write the question into history,
     stash `chat:pending` for deterministic resume, return.
   - **Success** → append a `function_call_output` to messages,
     stash `chat:active_draft` if it was a macro-draft tool, and let
     the loop continue (the model gets one more hop to chain or write
     final text). On every subsequent hop `tool_choice="auto"` so the
     model can emit prose and exit the loop.
   - **Error on `propose_workflow` / `backtest_workflow`** → append
     the error as a `function_call_output` and continue the loop ONCE
     (`_PROPOSE_WORKFLOW_MAX_ATTEMPTS = 2`). The model sees its own
     malformed call and the validation error, and self-corrects
     (unknown step types, missing trigger.* at step 0, etc.).
   - **Error on any other tool** → no LLM retry. The error is rendered
     via `_format_recoverable_failure_question` (deterministic template
     keyed on tool name / error class) or, when that returns
     `_LLM_CLARIFY_SENTINEL`, via `_llm_clarification` (a small LLM
     call that produces a tailored question). The user gets a
     specific clarification instead of "I had trouble."
   - **`propose_workflow` out of retries** → macro fallback
     (`_try_macro_fallback`) — emit a simplified manual-trigger draft
     so the user has something to edit, instead of a dead end.

Why no retry-against-the-model loop: it was burning 2–6 seconds per
turn for problems the *deterministic* question-builder could surface
in <1 ms. The two exceptions (`propose_workflow`, `backtest_workflow`)
share the `steps[]` schema and have well-known one-shot self-fixable
failure modes; everything else fails fast.

Last-error transparency: the most recent tool error is held in
`last_tool_error` so the circuit-breaker fallback can name the actual
problem instead of returning a generic message.

---

## 6. Caching layers

Two distinct caches do different jobs.

### 6.1 OpenAI prompt cache (server-side, automatic)

- **Stored:** tokenised attention K/V for the prefix bytes of each
  request, keyed by `prompt_cache_key` + content hash.
- **Skips:** re-tokenisation of the prefix. Prefix tokens billed at
  the cached-input rate.
- **Does NOT skip:** model inference. GPT still runs.
- **Visibility:** `usage.input_tokens_details.cached_tokens` is read
  per response, surfaced on `LLMResponse.cached_tokens`, and recorded
  in both the JSONL trace and the `llm_usage` table.
- **TTL:** ~5 min idle, ~1 h max (OpenAI-managed).
- **Discount rate:** `CACHED_INPUT_DISCOUNT = 0.5` in
  `services/llm_cost.py` — cached input tokens bill at 50% of the
  normal input rate on the Responses API. Update if OpenAI changes
  the published rate.
- **Hit rate (measured):** ~86% across a 13-turn probe.

### 6.2 Redis (our box, real `redis-server`)

| Key prefix | TTL | Payload | Where |
|---|---|---|---|
| `chat:conv:{conv_id}` | **24 h** | Last 20 turns (list) | `services/conversation_store.py` |
| `chat:pending:{conv_id}` | **10 min** | `PendingToolCall` JSON | same |
| `chat:active_draft:{conv_id}` | **10 min** | `ActiveDraft` JSON | same |
| `portfolio:summary:{user_id}` | 30 s | Portfolio summary JSON | `services/portfolio_cache.py` |
| `portfolio:holdings:{user_id}` | 30 s | Holdings list JSON | same |
| `index:level:{ticker}` | 10 s | Index value + change | `routers/markets._fetch_index` |
| `top_movers:{universe}:{dir}:{limit}` | 60 s | Top mover rows | `services/top_movers.py` |
| `chart:{symbol}:{period}:{interval}` | ~5 min | yfinance OHLC | `market/yfinance_service` |
| `backtest:{sha1(strategy)}` | 1 h | Backtest result JSON | `routers/backtest` |
| `yield:mf:{scheme_code}` | 1 h | Yield value | `agents/yield_scanner` |
| `webhook:rate:{src}` | 70 s | Inbound webhook count | rate limiter |

Mock fallback (`backend/cache.py:MockRedis`) implements only
`get / set / delete / exists / ping`; list / hash ops fail silently
when real Redis is unavailable. Production uses real Redis.

---

## 7. Token usage & session cost ledger

### 7.1 What we measure on every call

`LLMOpenAI.complete` reads from `data["usage"]` on the Responses API:

```
input_tokens                                  → total prompt tokens
input_tokens_details.cached_tokens            → cached subset
output_tokens                                 → generation tokens
output_tokens_details.reasoning_tokens        → reasoning tokens
```

All four land on the `LLMResponse` dataclass and pass through
`CallTrace.set_response` (or `set_stream_result` for streams). The
`cached_tokens` setter is also called explicitly for clarity at the
extraction site.

### 7.2 Pricing (`services/llm_cost.py`)

```python
PRICING = {                                # USD per 1,000,000 tokens
    "gpt-5-mini":  {"input": 0.25, "output": 2.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o":      {"input": 2.50, "output": 10.00},
    "sarvam-m":    {"input": 0.00, "output": 0.00},
}
CACHED_INPUT_DISCOUNT = Decimal("0.5")     # 50% off on cached subset
```

Cost formula in `compute_cost`:

```
cost = (input - cached) * input_rate
     + cached           * input_rate * CACHED_INPUT_DISCOUNT
     + (output + reasoning) * output_rate
```

Reasoning tokens bill at the **output** rate (Responses API). Negative
or NaN values are clamped to 0; `cached > input` is clamped to
`input`. Unknown models cost 0 and emit a `llm_cost.unknown_model`
warning once.

### 7.3 Persistence: `llm_usage` table

Schema (`backend/models.py:LlmUsage`):

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `user_id` | FK users.id (nullable) | Falls back to `user_id_var` request-scope contextvar. |
| `conversation_id` | str(64) | indexed |
| `turn_id` | str(64) | |
| `request_id` | str(64) | indexed; pulled from `request_id_var` |
| `endpoint` | str(64) | `chat` / `propose` / `router` / `agentic` / `validation` / `warmup` |
| `provider` | str(32) | `openai` / `sarvam` |
| `model` | str(64) | |
| `input_tokens` | int | |
| `cached_input_tokens` | int | added in migration 0006; subset of `input_tokens` |
| `output_tokens` | int | |
| `reasoning_tokens` | int | |
| `total_tokens` | int | denormalised sum |
| `cost_usd` | Numeric(12,6) | 6 decimal places of USD |
| `latency_ms` | float | |
| `created_at` | timestamptz | indexed |

Write path (`record_llm_usage`):

1. Compute cost via `compute_cost(...)`.
2. Emit a structured `event="llm.usage"` log line FIRST. The log line
   is the cheap fallback — if the DB write fails, we still have a row
   in the aggregator.
3. Open a fresh `SessionLocal()`, insert the row, commit, close. This
   is intentionally a **new session** because the trace can close
   from outside any FastAPI request scope (scheduler tick, agentic
   worker, background task).
4. Every exception path is swallowed locally. Cost tracking that
   breaks production is worse than no cost tracking.

Endpoint label inference: `_trace._infer_endpoint()` walks the stack
above `backend.llm.*` and matches against `_ENDPOINT_MODULE_PREFIXES`
(longest-prefix first). Caller modules can override by passing
`endpoint=...` to `CallTrace`.

Zero-token rows are **skipped** so transport errors / missing-API-key
short-circuits don't pollute the ledger.

### 7.4 Verbose JSONL trace (opt-in)

`PIVOT_LLM_TRACE=/tmp/llm_trace.jsonl` toggles per-call JSONL records
with prompts and responses (PII-bearing — dev-only). Independent of
the ledger: the ledger is always on.

Per record: `ts, caller, kind, endpoint, provider, model,
reasoning_effort, prompt_cache_key, max_output_tokens, tools_count,
tool_names, input_messages, input_chars_total, response_text,
tool_calls, usage{input,cached,output,reasoning,finish_reason},
latency_ms, ttft_ms, error`.

---

## 8. Measured numbers (probe 2026-05-07, `gpt-5-mini`)

| Metric | Value |
|---|---|
| Avg input tokens per turn | **22,413** |
| Avg cached tokens per turn | **19,210** |
| Avg cache hit ratio | **86.0 %** |
| Min hit (cold-start on a brand-new route) | **0 %** |
| Max hit (warm route) | **100 %** |
| Avg output tokens | ~150 (chat reply) – ~800 (workflow draft) |

System message structure on every call:

```
role=system     chars=41,644   ←  cached prefix (system.md + agentic_examples + primer)
role=system     chars=    83   ←  dynamic user context (portfolio totals)
…history turns…
role=user       chars=variable
```

Cold-start hits 0% once on a never-before-seen route; second turn on
the same route warms to ≥96%. Routes stay warm for ~5 min idle.

---

## 9. Latency profile

| Path | Typical latency |
|---|---|
| Fast-path (greetings / edu / continuation) | 6–60 ms |
| Workflow skeleton fast-path | 20–80 ms |
| Backtest router fast-path | 2–6 s (yfinance + indicator computation) |
| LLM hop — read intent ("TCS price") | 2–4 s |
| LLM hop — order draft ("buy 10 RELIANCE") | 4–9 s |
| LLM hop — agent draft (`propose_workflow`) | 8–25 s (≥2 hops, max 30 s) |
| Index-level cache hit | ~1 ms (vs 595 ms cold yfinance) |
| Portfolio cache hit | 0 ms (vs 4–200 ms broker) |
| Top-movers cache hit | <1 ms (vs ~4 s yfinance batch) |
| Tool router (regex) | <1 ms |
| Post-processing | <1 ms |

Bottleneck: output decoding + reasoning. With 86% prompt-cache hit the
input tokens are nearly free; latency is dominated by output-token
generation. Dropping reasoning effort `low` → `minimal` on agent
turns cut p50 by ~10 s with no measurable quality loss.

---

## 10. Pre-LLM short-circuits

| Path | Trigger | Latency |
|---|---|---|
| Backtest router fast-path | indicator-backtest regex | 2–6 s |
| Fast-path classifier — greeting | exact-match: hi / hello / good morning | 30–60 ms |
| Fast-path — thanks | exact-match: thanks / ty | 30–60 ms |
| Fast-path — help | exact-match: what can you do / help | 30–60 ms |
| Fast-path — edu definitions | "what is RSI" etc. (28 curated terms) | 20–35 ms |
| Fast-path — continuation | "what else" / "anything else" | 6–25 ms |
| Workflow skeleton fast-path | deterministic agent shapes (RSI threshold, scheduled order) | 20–80 ms |
| Cancel-draft fast-path | "cancel" / "discard" with active draft | <50 ms |

Together these absorb ~10–15% of traffic at near-zero token cost.

---

## 11. Correctness guards (for context)

- **Reasoning-leak sanitizer.** Strips paragraphs containing ≥2
  internal-monologue tells (`"the user now says…"`,
  `"we must answer…"`).
- **Typo-amendment guard.** Active draft + short bare alphabetic
  token → strip order + macro tools so the model can't re-emit the
  prior card.
- **Repeat-fallback variation.** If the same canned reject would fire
  two turns in a row, return a reset prompt instead.
- **Mode pins.** Automation strips macros; Agent strips immediate-
  order tools but keeps macros; Backtest narrows to backtest + read.
- **Schema guards.** `notify.message.channel` is `Literal["push"]`
  only — email / SMS asks fail validation and route through the
  email-aware canned reject.
- **Widget caption guarantee.** `_ensure_widget_caption` enforces a
  prose line whenever a card renders, so the FE never shows a card
  with no text bubble.

---

## 12. Where to look

| Concern | File |
|---|---|
| Orchestrator | `pivot/backend/services/chat_service.py` |
| Tool routing + cache key | `pivot/backend/services/tool_router.py` |
| Tool registry / dispatcher | `pivot/backend/services/tool_registry.py` |
| Tool execution + completeness | `pivot/backend/services/_v2_tools.py`, `pivot/backend/agents/tool_executor.py` |
| Validation handler | `pivot/backend/services/validation_handler.py` |
| Pre-LLM fast paths | `pivot/backend/services/fast_path.py` |
| Workflow skeleton | `pivot/backend/services/workflow_skeleton.py` |
| Macro fallback | `pivot/backend/services/workflow_macros.py` |
| Tool catalog | `pivot/backend/agents/tools.py` |
| Responses API client | `pivot/backend/llm/openai_client.py` |
| Tracer + ledger hook | `pivot/backend/llm/_trace.py` |
| Cost computation + persistence | `pivot/backend/services/llm_cost.py` |
| `llm_usage` schema | `pivot/backend/models.py:LlmUsage` |
| System prompt assembly | `pivot/backend/prompts/assembler.py` |
| Chat role text | `pivot/backend/prompts/system.md` |
| Calibration examples | `pivot/backend/prompts/agentic_examples.json` |
| Conversation store | `pivot/backend/services/conversation_store.py` |
| Redis client + mock | `pivot/backend/cache.py` |
| Workflow proposer | `pivot/backend/workflows/propose.py` |
| Step schemas | `pivot/backend/workflows/schemas.py` |
| Step executors | `pivot/backend/workflows/steps/` |

Probe scripts (`pivot/scripts/`):

- `cache_probe2.py` — token / cache stats
- `redis_verify.py` — Redis path verification
- `regression_trace.py` — basket / market_relative / cross_draft
- `leak_regression.py` — reasoning-leak sanitizer
- `typo_amend_regression.py` — bare-token re-emit guard
- `email_regression.py` — email-substitution guard
- `broad_tester.py` — 14-category coverage suite

---

## 13. Interactive view

`docs/model_architecture.html` — open in any browser. Click each stage
to see what the code does, which files are involved, and where the
tokens / latency / cache hits land. Same data as this document, in a
shape that's easier to navigate. No build step, no dependencies.
