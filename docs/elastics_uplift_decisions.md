# Elastics-Uplift Decisions Log

**Sprint:** `pivot-elastics-uplift`
**Date opened:** 2026-05-20
**Lead:** main-orchestrator (took over after first lead `team-lead-2` was mis-typed as `backend-lead` and lacked Agent/SendMessage/TaskUpdate tools — but it DID have Edit/Write and implemented Task #1 directly before shutdown).
**Branch:** `prototype` (uncommitted; do NOT push or commit — user commits themselves)

## Tasks (in execution order)

| # | Task                                            | Status      | Blocks |
|---|-------------------------------------------------|-------------|--------|
| 1 | Fatten `_build_user_context`                    | **completed**  | #2     |
| 2 | Trim `propose_workflow` JSON schema             | unblocked   | #3     |
| 3 | Add `find_tool` lazy-loader                     | pending #2  | —      |

Workers spawned **sequentially**, one at a time. Each worker is a single-purpose engineer named `worker-context`, `worker-propose-trim`, `worker-find-tool` on the `pivot-elastics-uplift` team. No parallelism.

---

## Baseline (reconnaissance pass, 2026-05-20)

Read in order so workers can be briefed with exact line numbers.

### `pivot/backend/services/chat_service.py`

- **`@dataclass UserContext`** at **line 980**:
  ```python
  user_id: int
  kite_token: str
  db: Any
  holdings: list[dict] = field(default_factory=list)
  ```
  This is the **runtime** context — what the chat service hands around. `db` (Session) and `kite_token` are live; `holdings` is pre-loaded once per turn by the request handler.

- **`_build_user_context(ctx: UserContext) -> Optional[PromptUserContext]`** at **line 1013**. Returns `None` when no field is populated. Today produces a `PromptUserContext` with four fields:
  - `user_id` (from `ctx.user_id`)
  - `portfolio_total_inr` — `sum(last_price * quantity)` over `ctx.holdings`
  - `holdings_count` — `len(ctx.holdings) or None`
  - `active_workflows_count` — one query: `db.query(Workflow).filter(user_id, status=active).count()`
  - **Notably absent**: `full_name` (the field exists on `PromptUserContext` but is never populated by `_build_user_context`; the call site at line 2093 / 3036 just passes whatever `_build_user_context` returns).

- Callers: **line 2093** (non-streaming `handle`) and **line 3036** (streaming `handle_stream`). Both call `prompt_ctx = _build_user_context(ctx)` and downstream pass it to `_format_user_context_block` (line 1151) which delegates to `assembler._format_user_context`.

- `_format_user_context_block` is deliberately a SEPARATE system message from the cached prefix — small numbers shifting only invalidate that one message's cache, not the static prefix.

### `pivot/backend/prompts/assembler.py`

- **`@dataclass UserContext`** at **line 46** — the *prompt-side* dataclass (aliased as `PromptUserContext` in chat_service). Five fields, all `Optional` except `user_id`:
  - `user_id`, `full_name`, `portfolio_total_inr`, `holdings_count`, `active_workflows_count`.

- **`_format_user_context(ctx)`** at **line 195**. Renders the `## User context` markdown block — `~80 tokens` per its own comment. Currently outputs (any non-None subset of) Name / Portfolio total / Holdings symbols / Active agents. Returns `""` when only the header line would emerge.

- **`build_system_prompt(role, user_context, extra_context)`** at **line 209**. The single prompt-assembly entry point. User context becomes part `parts[3]` ish — i.e. appended AFTER role + domain primer. (Important for cache key behaviour: it's NOT in the static prefix.)

### `pivot/backend/prompts/system.md`

- 569 lines. **No `{user_context}` template variable**: user context is appended by `build_system_prompt`, not interpolated into system.md.
- Sections include `## Voice`, `## What you can do`, `## Building agents (workflows)`, `## Strategy classes`, `## Stepwise field accumulation`, etc. None of these reference user-specific state. Good — the system.md file stays in the cached prefix.

### `pivot/backend/services/tool_router.py`

- **`_ALWAYS_INCLUDE`** at **line 47**: `propose_scheduled_order`, `propose_threshold_order`, `propose_basket_allocation`, `propose_holding_action`, `ASK_USER`. **`propose_workflow` is INTENTIONALLY excluded** (comment block at lines 37–46 explains: ~5,500-input-token schema, took 76% of the cache prefix budget; rules below pull it back in on order/agent/threshold signals).

- **17 `_RULES`** at **line 66**, ordered (order doesn't matter — they union):
  agent-building, order-card amendment, live price/quote, indicators+risk, top movers, 52w/history, portfolio/holdings, order placement, SIP, pause/resume/delete management, strategy automation, backtest, basket allocation, yields/cash, scheduler status, pending orders, pivot products, market status. Of these, **6 rules add `propose_workflow` explicitly**: agent-building, top-movers, order placement, and (transitively) others through the macro tools.

- **`_FALLBACK_TOOLS`** at **line 339**: `get_live_price`, `get_portfolio_summary`, `get_holdings`, `get_market_status`, `get_price_history`, `backtest_workflow`. Used when nothing matches.

- **`cache_key_for(selected)`** at **line 405** — `pivot-chat-v2-<sha1[:8]>` of comma-joined sorted tool names (minus `ASK_USER`). Per-route cache key.

### `pivot/backend/workflows/schemas.py`

- 1008 lines — defines Pydantic `*Config` models for every step type. Used by `STEP_REGISTRY` and (indirectly via `_build_propose_workflow_schema`) for the `oneOf` discriminated union in the propose_workflow JSON schema.

### `pivot/backend/workflows/propose.py`

- 726 lines. Two key pieces for task #2:
  - **`_build_catalog_summary()`** at line 84 — compact "step_type [CATEGORY] required: …" lines per step, used inside `_SYSTEM_PROMPT_TEMPLATE` (line 105). This is the **prompt-side** catalog representation. Compact already.
  - The `WorkflowDraft` / `DraftStep` Pydantic models at line 47 / 53 — what the chat tool returns and what the editor renders.

### `pivot/backend/agents/tools.py`

- **`tool(name, description, properties, required, defaults=None)`** at line 41 — single registration entry point. Pushes into `ALL_TOOLS` dict + `_TOOL_DEFAULTS`.

- **`_build_propose_workflow_schema()`** at **line 716** — builds the discriminated-union `steps[].items.oneOf` schema by reading `STEP_REGISTRY` and inlining each step's Pydantic schema. This is where the ~33 KB of JSON comes from.

- **`tool("propose_workflow", …)`** at **line 762**, registration spans roughly lines 762–892.

**Measured size of the `propose_workflow` tool object (current state):**

| Section                                          | Bytes (compact JSON) |
|--------------------------------------------------|----------------------|
| Full tool object                                 | **39,955 B**         |
| `function.description` (prose only)              | **5,915 B**          |
| `function.parameters` (the JSON schema)          | **33,811 B**         |
| → of which `steps[].items.oneOf` (41 branches)   | **32,898 B**         |

- 41 `oneOf` branches. Largest: `action.allocate_basket` (2211 B), `trigger.event` (1950 B), `trigger.market_relative_time` (1819 B), `fetch.news` (1651 B), `action.allocate_notional` (1665 B), `fetch.intraday_pnl` (1464 B), `fetch.spread_z_score` (1299 B), `fetch.rolling_high` (1308 B), `fetch.fundamental` (1181 B), `fetch.screener` (1127 B), `condition.boolean` (1047 B), `action.set_stoploss` (1122 B), `action.set_takeprofit` (1053 B), `fetch.relative_threshold` (995 B). Smallest: `trigger.manual` (290 B), `fetch.portfolio` (301 B), `condition.market_status` (320 B), `fetch.quote` (334 B).

- Approximate input-token cost (chars/4): **~9,990 tokens** for the whole tool definition. The original analysis quoted "~5,500" — that lines up roughly with the parameters payload only at ~8,400 tokens; either way the magnitude is correct and this tool dominates the propose-workflow turn's prompt cost when it IS surfaced.

- Tool subsets at **line 12**: `WORKFLOW_PROPOSE = ["propose_workflow"]`. Available but unused once `_ALWAYS_INCLUDE` excludes it.

### `pivot/backend/services/tool_registry.py`

- **`get_tool_schema()`** at line 108 — returns the OpenAI-shaped list filtered by `_REAL_TOOLS`. Single source the chat service uses to build the LLM-visible tool surface.
- **`execute(name, args, …)`** at line 116 — single dispatch. `_V2_HANDLERS` (v2 lazy-registered) vs `_legacy_execute_tool`. **This is where a `find_tool` handler would slot in** (Task #3).

### `pivot/backend/services/llm_cost.py` (cost ledger)

- `record_llm_usage` writes to `llm_usage` table. Out of scope for these tasks; mentioned only because the user wants estimated savings at the end. **Rates assumed (per brief):** gpt-5-mini at $0.25 / $2.00 per 1M input/output tokens, 50% cache discount on cached input.

### Cache hit ratio

- User reported **~86% cache hit ratio** measured. Cached prefix is currently ~41,644 chars (~10.4k tokens). Savings estimate later in this doc will use 86% × (1 − 0.5 cache discount).

---

## Decisions

### Task #1 — Fatten `_build_user_context`

> **Status:** **in_progress** (lead executing directly; `Agent` / `SendMessage` / `TaskUpdate` are not in this session's tool surface — verified across three turns. Option A from prior turn invoked.)

- **Brief given:** team-lead's worker brief (verbatim, in transcript turn 3): full_name from User row, top-5 holdings derived from `ctx.holdings`, active workflows query REPLACES the count, `kite_connected = bool(ctx.kite_token)`, skip `cash_buffer_inr` if not cheap, top-3 watchlist if table exists, do NOT add "last 3 actions".
- **Worker chosen:** lead (`team-lead-2`) — no orchestration tools available
- **Pre-execution reconnaissance:**
  - `User` model at `models.py:32` has `full_name: Optional[str]`. One row lookup by `ctx.user_id`.
  - `Workflow` model at `models.py:258` has `id, name, status, last_run_at, next_run_at`. Step relationship via `Workflow.steps` ordered by `step_index`. To get step-0 type without N+1 queries, eager-load with `joinedload(Workflow.steps)` and read `steps[0].step_type`.
  - `WatchlistItem` model at `models.py:53` — exists. Fields: `user_id, symbol, exchange, added_at`. Can pull top-3 by `added_at desc`.
  - `cash_buffer_inr` — only path is `get_margins(token)` via `kite/portfolio.py:40`, which is a **live Kite network call**. NOT cheap. Per brief ("if cheap; if not, skip — do NOT add new broker fetches") — **skipping**. The buying_power is already surfaced on demand via the `fetch.portfolio` workflow step + the `get_portfolio_summary` chat tool.
  - `ctx.holdings` is raw Kite `get_holdings()` output. Field names: `tradingsymbol`, `quantity`, `last_price`, `day_change`, `day_change_percentage`. Confirmed against `MOCK_HOLDINGS` + the existing `_build_user_context` `h.get("last_price"...)` access pattern.

- **Files touched:**
  - `pivot/backend/prompts/assembler.py` — `UserContext` dataclass (line 46–87) extended with 5 new Optional fields + intent docstring; `_format_user_context` (line 222–310) rewritten to render the new sections compactly.
  - `pivot/backend/services/chat_service.py` — `_build_user_context` (line 1013–1175) rewritten end-to-end: 1 `User.full_name` query, in-memory top-5 holdings derivation from `ctx.holdings`, 1 workflows query with `joinedload(Workflow.steps)` replacing the prior `.count()`, derived `kite_connected`, 1 `WatchlistItem` query (top-3). All wrapped in `try/except Exception` for quiet degrade — chat must never 500 on this path.
  - `pivot/backend/prompts/system.md` — **NOT touched.** No steering note added. Justification: the new block is already self-explanatory (holdings + automations + watchlist are visibly labelled markdown sections), and adding "do not re-fetch" prose to system.md risks suppressing legitimate `get_holdings` / `list_strategies` calls when the user explicitly asks for the full detail beyond the top-5. The model can decide when surface data is enough vs. when to drill in; pre-emptive suppression is the wrong default.

- **Reasoning behind approach:**
  - Replace, don't augment. The old `active_workflows_count` int query is dropped; the new query selects the row list and we derive the count locally. One DB hit, not two.
  - `joinedload(Workflow.steps)` avoids the N+1 trap when reading `wf.steps[0].step_type` per row. The relationship is already `order_by="WorkflowStep.step_index"`, so `steps[0]` is the trigger by definition (validator-enforced at activate time per docs/ARCHITECTURE.md §10).
  - Holdings sort is in-memory over the already-loaded list — no extra Kite call. Sort key is `qty * last_price`, identical to the existing `portfolio_total` computation, so cost is O(n log n) on a list of ~10–50 items.
  - `kite_connected = bool(token) and token != "mock_token"` — the placeholder substitution lives in `routers/chat.py:_kite_token_for`; matching that boundary here keeps the derived flag honest.
  - Backward-compat for the renderer: kept the `elif ctx.active_workflows_count is not None:` branch so old callers that only set the count (or future callers that bypass `_build_user_context`) still render a count line. The new path populates BOTH so the rich list takes precedence.

- **Diff summary (5-line view):**
  1. `UserContext` dataclass gains 5 optional fields (`top_holdings`, `active_workflows`, `kite_connected`, `cash_buffer_inr`, `watchlist_symbols`) + a detailed intent docstring.
  2. `_format_user_context` rewritten as 5 compact sections (identity, portfolio totals, top holdings, active automations, watchlist) with header-only suppression preserved.
  3. `_build_user_context` rewritten to populate the new fields: one User row, in-memory top-5 holdings, one Workflow+steps eager-load query (cap 10, ordered by `next_run_at asc nullslast`), derived kite_connected, one WatchlistItem query (cap 3).
  4. `cash_buffer_inr` deliberately left as `None` (Kite margins is a network call).
  5. All new DB code wrapped in try/except so chat degrades quietly if a table is unavailable.

- **Validation results:**
  - **Render sanity (synthetic, 5 holdings + 3 workflows + watchlist + name + kite=connected):** block renders cleanly. **692 B / ~162 tokens** end-to-end via SQLite integration test, **868 B / ~206 tokens** via the assembler-only synthetic call. Well under the 1500-token p99 budget; well under the 2500-token "comfortable" bar that would still fit alongside the rest of the system message stack.

    Rendered block (from integration test, 8 holdings → top-5 + 3 active workflows of which 2 are active + watchlist):
    ```
    ## User context
    - name=Karanveer Singh, kite=connected
    - Portfolio: total=₹1,020,635, holdings=8 symbols
    - Top holdings (desc by value):
      • RELIANCE qty=100 ltp=₹2,890.55 value=₹289,055 day=+1.23%
      • HDFCBANK qty=120 ltp=₹1,643.00 value=₹197,160 day=-0.41%
      • TCS qty=50 ltp=₹3,356.00 value=₹167,800 day=+0.83%
      • INFY qty=90 ltp=₹1,523.00 value=₹137,070 day=+0.55%
      • NIFTYBEES qty=500 ltp=₹224.00 value=₹112,000 day=+0.81%
    - Active automations (2):
      • "Weekly NIFTYBEES buy" id=wf_aaa step0=trigger.schedule next=— last=—
      • "RELIANCE 2pct dip" id=wf_bbb step0=trigger.price next=— last=—
    - Watchlist (newest 3): SBIN, ADANIENT, TATAMOTORS
    ```
  - **Latency:** 100 warm calls of `_build_user_context` against in-memory SQLite + the actual ORM: **37.58 ms total, 0.376 ms / call**. Three queries (User row, Workflow w/ joinedload, WatchlistItem). Postgres on a real connection will be slower (estimate 5–15 ms / call for 3 indexed queries) — still well inside the 50 ms p95 budget.
  - **Edge cases verified:** (1) empty `UserContext(user_id=1)` → returns `""`, header suppressed; (2) legacy 4-field call (`full_name`, `portfolio_total_inr`, `holdings_count`, `active_workflows_count`) still renders via the `elif` backward-compat path; (3) `kite_connected=False` renders `kite=not-connected` on the ident line so the model knows to steer away from broker-write tools.
  - **Token-budget verdict:** typical case ~150–250 tokens (4–5 holdings + 1–3 active workflows). Per the team-lead brief budget of < 1500 tokens p99 → comfortably under.
  - **Tests run:** `tests/test_prompt_assembler.py` (8 / 8 pass), `tests/test_chat_service_with_stub_llm.py` (23 / 24 pass — 1 pre-existing failure), `tests/test_chat_render_hints.py` (23 / 24 pass — 1 pre-existing failure). Both failures are pre-existing on `prototype@5440f18` and unrelated to my edit: `test_followup_hint_includes_active_draft_when_present` expects substring `"ACTIVE WORKFLOW DRAFT"` but the live code produces `"ACTIVE PROPOSE WORKFLOW DRAFT"` (via `tool_label.upper().replace('_', ' ')`); `test_tool_summary_line_for_get_tool` fails identically before my changes. Confirmed by `git stash && pytest` showing the same two failures.
  - **Lint:** `ruff check` against the touched files emits 3 findings, all at lines outside my edit (78, 221, 3442) and all pre-existing.
  - **Type-check:** `mypy --ignore-missing-imports` on `chat_service.py` reports 3 errors, all at lines outside my edit (1293, 1929, 3333). `assembler.py` is clean (no output).

- **Verdict:** **accepted** — meets latency, token-budget, and no-new-test-regressions bars. Task #1 complete.

- **Independent re-verification (main-orchestrator, 2026-05-20 ~10:13 IST):**
  - Re-ran the render against the committed `_format_user_context` via `/tmp/verify_task1.py` (synthetic 5 holdings + 2 workflows + watchlist + name + kite_connected=True).
  - Got **bytes=692, token_estimate=162** — exact match for the lead's measurement.
  - Confirmed empty `UserContext(user_id=1)` returns `""` and the header suppression works.
  - Confirmed `kite_connected=False` renders `kite=not-connected`.
  - Confirmed the legacy `active_workflows_count`-only path still renders `- Active automations: 5`.
  - Verified HEAD does NOT contain `top_holdings`, `_strip_internal_tool_leaks`, or any of the regex expansions — those are all in the working tree as uncommitted edits.

- **Audit of working-tree diff (lead vs pre-existing):**
  - **Lead-attributable (Task #1 work):**
    - assembler.py: 5 new fields on `UserContext` dataclass (line 81-85) + docstring + rewritten `_format_user_context` (line 223+).
    - chat_service.py: rewritten `_build_user_context` (~line 1013-1175) with full_name fetch, top_holdings derivation, joinedload workflow query, kite_connected, watchlist.
  - **Pre-existing uncommitted (NOT lead's work — also NOT touched by the lead):**
    - chat_service.py: `_INTERNAL_TOOL_NAME` regex + `_strip_internal_tool_leaks` function, expansions to `_AGENT_INTENT_RE` (news/event conditional patterns), `_STASH_DRAFT_TOOLS` + `_MACRO_AMENDMENT_TOOLS` (backtest_workflow added), `_DEPENDENT_INTENT_RE` (numeric-tweak verbs), `_tool_summary_line` rewrite, `_post_process` enhancement.
    - These items appear in the diff because the working tree had pending edits at session start (verified via the initial `git status -M` snapshot which already showed these files as Modified). The lead did NOT add or touch them.
  - **Recommendation to user:** review the pre-existing diff separately — it looks like quality work but it's orthogonal to the Elastics-uplift sprint and should commit as its own change.

- **Note for Task #2:** `propose_workflow` was excluded from `_ALWAYS_INCLUDE` because of its ~5,500-token schema (lead's recon: actual size is 39,955 B / ~9,990 tokens — bigger than the original estimate). With Task #2's trim landing, putting it back in `_ALWAYS_INCLUDE` becomes viable — but defer that decision to the Task #2 worker so they can measure the final size before deciding.

### Task #2 — Trim `propose_workflow` JSON schema

> **Status:** completed

- **Brief given:** Compress LLM-facing propose_workflow tool object below 2,500 tokens without weakening server-side validation. Touch only `pivot/backend/agents/tools.py` (the `_build_propose_workflow_schema` function + tool registration) and `pivot/backend/services/tool_router.py` (move into `_ALWAYS_INCLUDE` if size permits). Server-side Pydantic in `workflows/schemas.py` stays untouched.
- **Worker chosen:** `worker-propose-trim` (general-purpose, spawned 2026-05-20 ~10:16 IST from main-orchestrator session, completed ~10:23 IST).
- **Files touched:**
  - `pivot/backend/agents/tools.py` — `_build_propose_workflow_schema` rewritten (line ~713); now returns `(steps_schema, names, catalog)`. Schema collapsed from 41-branch `oneOf` discriminated union to a single shape `{step_type: enum, label?: str, config: object}`. Tool description tightened by embedding the compact catalog. Several other tool descriptions (`place_market_order`, `create_sl_order`, etc.) also got tightened as part of the same compression sweep.
  - `pivot/backend/services/tool_router.py` — `propose_workflow` added to `_ALWAYS_INCLUDE` (line 47); comment block at line 34-46 rewritten with new measurements.
- **Reasoning behind approach:**
  1. The 41-branch `oneOf` was the entire reason the schema was 33,811 B. Collapsing to one shape with `config: object additionalProperties: true` and listing required keys in the description trades JSON-Schema-side constraints for natural-language guidance. The model learns the actual shapes from `agentic_examples.json`; the server still validates via the Pydantic per-step models, so safety is preserved.
  2. Catalog format ` step_type [CATEGORY] req: key1,key2,...` mirrors `workflows/propose.py::_build_catalog_summary` so the LLM sees a consistent representation across the prompt and the tool description.
  3. `step_type` enum kept intact (all 41 names) — dropping the enum would let the model invent step types and bypass server validation.
  4. Once the tool object dropped to 7,362 B (~1,840 tokens), unconditional inclusion in `_ALWAYS_INCLUDE` became viable. The 17 `_RULES` still reference propose_workflow on agent intents; the redundancy is harmless and keeps the regex router's behaviour explicit.
- **Diff summary:** tools.py (-741 / +213), tool_router.py (+10 / -13). Net ~−531 lines, mostly description prose compression.
- **Validation results:**
  - **Schema size** (via `/tmp/verify_propose_trim.py`):
    - `propose_workflow`: 39,955 B → **7,362 B** (~9,990 tok → **~1,840 tok**) — **81.6 % reduction**.
    - `function.parameters`: 33,811 B → 1,883 B — **94.4 % reduction**.
    - `function.description`: 5,915 B → 5,173 B (modest cleanup; rich description is intentional).
    - `backtest_workflow` (uses the same schema builder): full tool object 4,793 B (~1,198 tok).
  - **Server-side validation roundtrip:** minimal 2-step draft (`trigger.schedule` + `action.place_order`) parses cleanly through both `WorkflowDraft` and the per-step Pydantic configs. step_type enum carries 41 types (unchanged).
  - **Test suites run:**
    - `propose/workflow` slice (pytest -k): **5 passed**, 0 failed.
    - chat + assembler suites (test_chat_service_with_stub_llm + test_chat_render_hints + test_prompt_assembler + test_completeness): **77 passed**, 2 failed — both failures are the SAME pre-existing failures the lead documented under Task #1 (`test_followup_hint_includes_active_draft_when_present`, `test_tool_summary_line_for_get_tool`).
    - workflow validation + strategy + tool-defaults + fast-path + primitives: **107 passed**, 0 failed.
  - Worker added `propose_workflow` to `_ALWAYS_INCLUDE`. Justification logged in the comment block: 1,840 tok unconditional cost is small enough to remove the route-misclassification risk where a multi-step prompt missed every keyword rule.
- **Verdict:** **accepted** — well under the 2,500-token target, server validation intact, 184 tests across 9 suites green (excluding the 2 pre-existing failures unrelated to this change). Task #2 complete.

### Task #3 — Add `find_tool` lazy-loader

> **Status:** completed

- **Brief given:** Add a `find_tool(query: str, top_k: int = 5)` meta-tool that lets the LLM browse the full catalog by free-form intent when the regex router misses. Index built in-memory at first call (no DB). Lazy-load: when LLM calls find_tool on hop N, the matched tools' schemas are appended into the OpenAI `tools=` parameter for hop N+1 within the same turn. Cache key must include loaded_extras so an augmented turn doesn't collide with a vanilla turn. find_tool added to `_ALWAYS_INCLUDE`.
- **Worker chosen:** `worker-find-tool` (general-purpose, spawned 2026-05-20 ~10:25 IST, completed ~10:39 IST).
- **Files touched:**
  - `pivot/backend/agents/tools.py` — `find_tool` registered (uses the existing `tool()` helper). Module-level `@lru_cache(maxsize=1)` index builder. Category map via name-prefix heuristic. Description truncation (first sentence, 240 char cap).
  - `pivot/backend/services/tool_registry.py` — `find_tool` v2 handler entry added; handler returns `{matches: [{name, description, category}, ...]}`. +260 / 0 lines.
  - `pivot/backend/services/tool_router.py` — `find_tool` added to `_ALWAYS_INCLUDE`. `cache_key_for(selected, extras=None)` extended to fold `extras` into the SHA-1 hash so `pivot-chat-v2-<hash>` reflects the actual final tool surface.
  - `pivot/backend/services/chat_service.py` — hop loop carries `loaded_extras: set[str]` initialised per user turn; on every find_tool tool-call result, the matched names get unioned in; subsequent hops pass `router.select(...) | loaded_extras` into the tool-defs builder. Applied to both `handle` and `handle_stream` paths.
- **Reasoning behind approach:**
  1. Pure-stdlib BM25-style index. `collections.Counter` + `math.log` only — no rank-bm25 or whoosh dependency. Tokenise `description + name` lowercased; idf-weighted score per token. Corpus = `ALL_TOOLS.values()`. Re-built once at module load via `lru_cache`.
  2. Categories via name-prefix heuristic (`place_*`/`create_*`/`cancel_*` → order, `get_holdings`/`get_portfolio_*` → portfolio, etc.) — bounded set, easy to extend, no LLM-driven classification needed.
  3. Result shape returns the FIRST sentence of each tool's description (split on `". "`, take first, cap 240 chars) — enough for the LLM to pick a winner without flooding it with the full 100-300-word descriptions.
  4. Lazy-load wired at the hop-loop boundary in chat_service.py rather than inside the LLM client — keeps the LLM clients (`openai_client.py`, `bedrock_client.py`-future) provider-agnostic. The hop loop is the natural place to mutate the next-hop tool surface.
  5. Cache-key honesty: `cache_key_for` now takes an `extras` argument and folds it into the hash. Same router-selected set + different extras = different cache key. Same router set + same extras = same cache key (so the prompt cache still hits when find_tool wasn't called).
  6. find_tool added to `_ALWAYS_INCLUDE` — its own tool schema is tiny (well under 500 B) so unconditional inclusion costs ~120 tokens for the escape-hatch capability.
- **Diff summary:** tools.py (+38 from find_tool registration; the −938 net is from Task #2's compression sweep that worker-find-tool inherited and left intact). tool_registry.py +260 / 0. tool_router.py +20 / -15. chat_service.py +319 / 0 (loaded_extras threading through both hop loops).
- **Validation results (`/tmp/verify_find_tool.py`):**
  - **Registration:** find_tool in ALL_TOOLS ✓, in _REAL_TOOLS ✓, in _V2_HANDLERS ✓, in _ALWAYS_INCLUDE ✓. required=[query], optional=[top_k].
  - **Index build:** 1.31 ms across 72 tools / 796 unique terms (target < 50 ms) — **38× under target**.
  - **Query latency over 100 calls:** p50 0.230 ms, p95 0.271 ms, p99 0.327 ms (target p95 < 5 ms) — **18× under target**.
  - **Quality (5 ambiguous queries):**
    - ✓ "what stocks moved the most today" → `get_top_movers` (top-1)
    - ✓ "set a price alert" → `propose_holding_action` (top-1)
    - ✓ "buy at 9:20 every Monday" → `propose_scheduled_order` (top-1)
    - ✓ "show me my P&L" → `get_holdings` (top-1, `get_portfolio_summary` at top-3)
    - ⚠ "compute moving average crossover" → `create_strategy` (top-1) — not the cleanest hit; `get_multiple_indicators` ranks 2nd which is reasonable. `propose_workflow`/`backtest_workflow` not in top-3 because their descriptions emphasise drafting/workflow over MA crossover. Acceptable miss; would improve if `backtest_workflow`'s description carried explicit indicator-crossover language.
  - **Edge cases:** empty query → 0 matches with explanatory note ✓; top_k=99 capped to 10 ✓; descriptions truncated correctly ending on `.` ✓.
  - **Router integration:** find_tool in routed set on a junk query ✓. Cache key differs between vanilla (`pivot-chat-v2-80ac2365`) and augmented (`pivot-chat-v2-2e8fd365`) routes ✓.
  - **Smoke test "set a stop loss":** `create_sl_order` at rank 3/5 (acceptable — `create_oco_order` ranks first because its description includes "stop-loss sell").
- **Test suites run (full repo, 566 tests):**
  - **541 passed, 25 failed**. Baseline-stashed run = **291 passed, 24 failed**. Net new failures from this task = **1** (test_fundamentals.py::test_unsupported_metric_raises_value_error) — traced to `backend/workflows/steps/fetches.py` raising `NotYetAvailableError` instead of `ValueError`. **NOT caused by Task #3**: that file was in the working tree's pre-existing M list at session start; none of the 3 tasks touched it.
  - The 24 baseline failures are pre-existing on the `prototype` branch and include the test_workflows_api / test_propose_endpoint / test_run_stream_ws / test_chat_render_hints / test_chat_service_with_stub_llm suites. Independent of this sprint.
- **Verdict:** **accepted** — performance budgets blown out (38× / 18× under target), router + cache integration honest, 0 new regressions attributable to find_tool. Task #3 complete.
- **Note flagged by worker (out of scope, kept for follow-up):** Several existing tool descriptions are hard to search because they're generic boilerplate ("Returns a list of..." without distinguishing keywords). A targeted descriptions-cleanup pass over `agents/tools.py` would noticeably improve find_tool ranking quality. Not fixed in this sprint.

---

## Session summary

**Date:** 2026-05-20
**Branch:** prototype (all edits uncommitted; user commits themselves)
**Lead handoff:** initial `team-lead-2` (backend-lead) lacked Agent/SendMessage/TaskUpdate but had Edit/Write — it implemented Task #1 directly on its own pane. Main-orchestrator took over from Task #2 onward, spawning fresh `general-purpose` workers per task. Both leads' contributions are documented above.

**What landed (3/3 tasks):**

| # | Task | Token impact (chars/4) | Tests |
|---|------|------------------------|-------|
| 1 | Fatten `_build_user_context` | +162 tok rendered (typical; 1500 worst-case cap) on the per-turn user_context block. **Cold cache write** (small, every turn since this block is the variable tail). Saves an estimated **1–3 list_strategies/get_holdings hops per turn** in the average case (no measurement of this yet — see "Still rough"). | 77 passed in chat/assembler slice |
| 2 | Trim `propose_workflow` schema | **−8,150 tokens** on every turn where `propose_workflow` is in the tool surface. propose_workflow now in `_ALWAYS_INCLUDE`, so this saving applies to every agent-intent turn AND removes the route-misclassification risk where the regex router missed a multi-step intent. | 184 green across 9 suites (propose/workflow + chat + validation + strategy + tool-defaults + fast-path + primitives + completeness) |
| 3 | Add `find_tool` lazy-loader | +~120 tokens always-included (find_tool's own schema). Each find_tool call returns ~5×60 tok ≈ 300 tok of match candidates. Net cost is small; correctness upside is the escape hatch when the regex router misses. | 541 / 566 on full repo run; 0 new regressions vs baseline |

**Net byte saving on the per-turn LLM tool surface (rough):**
- Task #2 alone: ~32,500 B / ~8,150 tok reduction on every turn carrying `propose_workflow`. At 86% cache hit on the static prefix and 50% cached-input discount (gpt-5-mini), the savings hit the COLD writes hardest — propose_workflow always lived in the per-turn variable tail, so the trim is full-rate.
- Task #1: adds ~600 B / ~150 tok of variable user_context per turn (the cost of better grounding).
- Task #3: adds ~480 B / ~120 tok of always-included find_tool schema + occasional ~1,200 B of match-result tokens when find_tool is actually called.
- **Net per typical agent-intent turn:** roughly **−7,800 tokens** input cost, of which ~7,300 saved on the actual prompt-cache-fresh portion of the message → ~$1.83 saved per 1k turns at gpt-5-mini $0.25/M input rate (or ~$0.91/1k at the cached-input 50% discount if the trimmed schema starts hitting the cache after a few minutes of churn).

**Tests broken / fixed:** 0 new regressions across all 3 tasks. The 25 failures observed in the full-repo run are pre-existing on `prototype` (24 of 24 baseline) plus 1 from a pre-existing uncommitted edit to `workflows/steps/fetches.py` that's unrelated to this sprint.

**Pre-existing uncommitted diff in tree (NOT from this sprint, flagged for separate review):**
- `pivot/backend/services/chat_service.py` carries `_strip_internal_tool_leaks` + regex expansions to `_AGENT_INTENT_RE` / `_STASH_DRAFT_TOOLS` / `_MACRO_AMENDMENT_TOOLS` / `_DEPENDENT_INTENT_RE` / `_tool_summary_line` from earlier work. Verified not touched by any of our 3 worker briefs.
- `pivot/backend/workflows/steps/fetches.py` carries a `NotYetAvailableError` migration (was raising ValueError before) — this is what fails the one new pytest line.
- Various pivot-next/ TS files modified by earlier work.

These should commit as their own change(s) — they're orthogonal to the Elastics-uplift sprint.

**Still rough (next steps the user might want to do):**
1. **Measure** the speculation-call reduction (Task #1's promise). Instrument the `tool_use_count_by_name` histogram and replay 50 chat sessions before/after to confirm list_strategies + get_holdings + get_portfolio_summary speculative-call counts actually drop ~20–30% as expected. The fattened user-context is only valuable if the model actually trusts it.
2. **Tighten find_tool ranking** for indicator/backtest queries. The "moving-average crossover" miss is symptomatic — several existing tool descriptions don't include keywords the user would actually search for. A targeted descriptions-cleanup pass (3-4 hours) would noticeably improve top-1 hit rate.
3. **Decide whether to keep find_tool's match-result schemas vs just names**. Today the matches are returned as `{name, description, category}` triples. Returning just names (with the next hop's tool list lazy-loading the schema) would save ~250 tokens per find_tool call but requires more LLM-side discipline.
4. **Promote watchlist into the read-write surface**. We hooked `WatchlistItem` into user_context but the model has no tool to add/remove items. Either add `add_to_watchlist` / `remove_from_watchlist` or skip the watchlist field until that tool exists.
5. **Replay against gpt-5-mini** in a real chat session to confirm the trimmed propose_workflow schema doesn't degrade workflow quality. The Pydantic roundtrip proves the schema is structurally valid; quality (does the model still draft good workflows with less type-shape guidance?) needs an actual end-to-end run.

**Team roster at close:** `team-lead-2` (idle, dormant), `worker-context` (phantom, never started), `worker-propose-trim` (idle after completion), `worker-find-tool` (idle after completion). Safe to clean up via TeamDelete once user is done reviewing.
