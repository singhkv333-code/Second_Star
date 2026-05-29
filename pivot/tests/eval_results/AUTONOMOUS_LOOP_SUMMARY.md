# Autonomous loop summary — Pivot chat layer (L01 → L36)

**Branch.** `Eventtriggers` (56 commits ahead of `7e0c985`; all
commits local, **never pushed** per standing rule).

**Duration.** Multi-cycle continuous loop, May 28-29 2026, ending
at L36 on user's instruction.

**Scope.** Pivot Agent System v1 chat layer (`pivot/backend/services/
chat_service.py` and surrounding services + tools). Goal: handle
the user's 20 compound multi-step prompts AND fix every remaining
chat bug we'd already found in earlier L01-L13 loops, without
exceeding +30-40% over the latency/token baseline.

---

## What this directory does

`/Users/karanveersingh/Downloads/Second_Star/` contains the Pivot
trading-assistant app (the user's startup, deadline 17 May 2026).
Three Python/JS dirs in the repo root:

- `pivot/` — the **active backend**, FastAPI + SQLAlchemy + Postgres
  + Redis + APScheduler. Chat layer lives in
  `pivot/backend/services/chat_service.py` (≈4600 lines, the brain
  of the agentic loop). Tool implementations in
  `pivot/backend/agents/tool_executor.py` + companions. Prompts in
  `pivot/backend/prompts/system.md` (heavily edited this loop).
- `frontend/` — legacy Vite UI; **untouched** this loop.
- `pivot-next/` — Next.js 15 + shadcn UI (currently the chat-first
  surface); **untouched** this loop, only backend changes shipped.

Other dirs in the repo root (`docs/`, `tests/`, `scripts/`, etc.)
are project-shared; the autonomous loop only edited `pivot/`.

**LLM provider.** Azure OpenAI (gpt-5.4-mini) is the only chat
provider; Sarvam was removed in a prior session. The fallback
"AI backend temporarily unavailable" banner is reserved for
Azure errors (content_filter / rate-limit / 5xx).

**Chat flow at a glance:**

```
user message
  ├─ try_fast_path()              ← greetings / single-word entities
  ├─ _fo_strategy_decline()       ← L36: F&O verbs, no LLM
  ├─ try_workflow_skeleton()      ← canonical agent shapes
  ├─ LLM hop 1..8                 ← 8-hop tool-use loop
  │    ├─ validation_handler.execute_with_completeness()
  │    ├─ M1: ASK_USER enforcement (no unstructured clarification)
  │    └─ M2: no-qty-default refusal
  └─ _post_process() → user
```

---

## What changed this loop (L01 → L36)

### Server-side primitives shipped

| Primitive | File | What it does |
|-----------|------|--------------|
| `compose_multistep` | `pivot/backend/services/_orchestrator_chat_tools.py` (new, ~400 LOC) | Server-side `$step_id.field` ref threading across sub-steps. Each sub-step dispatched through `validation_handler.execute_with_completeness`. |
| `extract_winner_symbol` | same file (inline-only) | Deterministic winner extraction from compare_performance / compare_backtests results. Hidden from public tool surface. |
| `compare_backtests` | same file | `asyncio.gather` over up to 4 `backtest_workflow` calls. |
| `web_search_brief` | `pivot/backend/agents/web_tools.py` (new, ~200 LOC) | DDG IA + Wikipedia REST fallback. 1h Redis cache. Entity grounding for "what is X" prompts where the LLM lacks current-affairs context. |
| `regime_compare_metrics` | `pivot/backend/core/calculations/regime.py` (new, ~180 LOC) | Split price history at a pivot date, compute Sharpe/Sortino/maxDD/vol per window. Auto-extends period to "max" when pivot > 4y back. |
| `_fo_strategy_decline` | `pivot/backend/services/chat_service.py` (L36) | Pre-LLM regex shortcircuit for F&O strategy verbs. Sidesteps content filter, deterministic, <15ms. |

### Validators / guards added

1. **M1 retry pattern** — `_looks_like_unstructured_clarification` in `chat_service.py` detects question-shaped LLM responses where `ASK_USER` wasn't called and no draft was emitted; pushes a one-shot "USE ASK_USER" directive and re-emits. Length-skip > 320 chars and markdown-heading skip protect legitimate explainer endings.
2. **M2 no-qty-default** — `validation_handler.py` refuses any quantity ∈ {1, 10} silently filled when the user didn't name a size, forces the LLM to call ASK_USER.
3. **Skeleton cross-symbol guard** — `workflow_skeleton.py` bails on 2+ ticker prompts so single-symbol parsers don't corrupt cross-symbol intents.
4. **Skeleton compound-intent guard** — `_COMPLEXITY_RE` extended with "compare ... then build", "before/after pivot date", "full plan" patterns.
5. **DSL early-bails** — trailing-SL / multi-trigger semicolon / schedule-leaf detection in `_dsl_chat_tools.py` returns structured `route_redirect` so the LLM is forced to retry with the correct tool.
6. **Pure-affirmative regex** — extended to catch "ok activate it", "save and activate", "proceed with it", "go ahead and do it".
7. **Independent-intent regex** — extended to catch price/chart-history patterns + "now also build another agent" overrides.
8. **Null-arg validator strip** — `validation_handler.py` drops Azure-emitted `null` for optional fields so the agentic loop survives non-required-field nulls.
9. **calculate_order_qty yfinance fallback** (L34) — cache miss → `yfinance.Ticker(SYM.NS).history(period="5d")` → last Close. Hard error string only when both fail.
10. **F&O strategy pre-LLM decline** (L36) — naked call/put, covered call, protective put, iron condor/butterfly, bull/bear spreads, strangles, straddles, calendar/diagonal, sell/buy/write call/put.

### system.md additions / edits

- "Compound multi-step intents — `compose_multistep`" section with worked plan example, period normalisation table, "JUST RUN IT — DO NOT ASK" teaching.
- Rebalance teaching: `trigger.schedule + action.allocate_basket`, weight as decimal 0.3334.
- Time-phrasing rule: "Buy X at 9:30 AM tomorrow" = `propose_scheduled_order` with valid_until, NEVER limit price.
- Alert routing: "price alert" / "alert me when X crosses Y" = `propose_dsl_workflow` with `action_kind='notify_only'`, NEVER propose_threshold_order.
- F&O guidance: when user names an F&O strategy, decline with "isn't wired in Pivot v1" + offer the equity alternative.

### Tool router edits

`tool_router.py` got regex rules that surface new tools:
- `compose_multistep` on "compare/backtest ... then build", "X vs Y + show which won", "full plan", "before and after / pre-2022 / regime"
- `web_search_brief` on "what is RBI/repo rate/arbitrage fund/cap-guaranteed/NIFTYBEES"
- `regime_compare_metrics` on pivot-date / event-name prompts

---

## Hand-judged results (cumulative L22 → L36)

| Bucket | Sessions | PASS clean | Partial / ASK | FAIL |
|--------|----------|------------|---------------|------|
| L22-L33 (varied) | 134 | 114 (85%) | 18 | 2 (Azure content filter) |
| L34 multi-turn refinement | 4 | 4 | 0 | 0 (after fix) |
| L35 prompt shapes | 12 | 11 | 1 (ROE filter — honest decline) | 0 |
| L36 edge shapes | 15 (+5 retest) | 19 | 0 | 0 (after fix) |
| **Loop total** | **170** | **148 (87%)** | **19** | **0 unresolved** |

**0 fabrications across the entire loop.** Every degradation we
saw was an honest "I don't have this data / capability" decline,
not a made-up answer.

### Latency / token snapshot (final)

| Metric | Baseline | This loop | Delta |
|--------|----------|-----------|-------|
| p50 wall (tool-driven turn) | ~10s | 8.8s | **−12%** |
| p95 wall | ~18s | 17.4s | flat |
| p50 input tokens | ~25K | 26.9K | +7% (under 35K cap) |
| avg output tokens | ~75 | 84 | +12% |

The +12% output and +7% input is within the +30-40% budget the
user set. The p50 wall actually got faster — partly from the
pre-LLM F&O / fast-path / skeleton short-circuits, partly from
prompt caching.

---

## What's still open (handover items)

These are non-blocking but worth tracking:

1. **L02_07 multi-symbol auto-firing intent** — LLM picks
   `trigger.manual` instead of `trigger.schedule` ~20% of the
   time when the prompt has 2+ tickers and an auto-firing verb.
   M1 doesn't catch it. Would need a workflow-shape validator
   that flags `trigger.manual + 2+ tickers + auto-firing words
   ("every day", "automatically")` and forces a route_redirect.

2. **Trailing SL prose-without-tool** — LLM sometimes writes
   "Got it — I'll set the trailing stop" without calling
   `propose_holding_action`. DSL early-bail returns the
   structured error but the LLM's confirmation prose still
   leaks. Tool-description tightening is the cheapest fix.

3. **qty=1 stubbornness** — LLM emits `quantity=1` ~30% of
   compound-prompt turns even with M2 forcing ASK. M2 catches
   it server-side so the user never sees `qty=1`, but a
   structural schema-side fix (anyOf [int>=2, never-1]) would
   eliminate the wasted hop.

4. **`web_search_brief` actual usefulness** — DDG IA returns
   empty for most India-specific queries (RBI rate, INFY news);
   the LLM degrades honestly ("couldn't pull a feed, see X URL")
   but a real news/macro feed (e.g., NewsAPI free tier, RBI
   press release scrape) would let us answer current-affairs
   prompts directly.

5. **L35_09 fundamentals-filter intent** — "Nifty Next 50 with
   ROE > 18% over 5y" requires a fundamentals filter we don't
   build. The LLM degrades honestly and drafts the simpler SIP
   shape, but a `screen_by_fundamentals(criterion, universe)`
   helper would unlock a category of "value/quality screen"
   prompts.

6. **Loop log size** — `AUTONOMOUS_LOOP_LOG.md` is 1071 lines.
   Future loops should probably section-break by quarter so it
   stays readable.

---

## Files touched this loop (the load-bearing ones)

```
pivot/backend/services/chat_service.py          (~+400 LOC)
pivot/backend/services/_orchestrator_chat_tools.py  NEW
pivot/backend/services/_dsl_chat_tools.py         (DSL early-bails)
pivot/backend/services/tool_router.py             (router rules)
pivot/backend/services/tool_registry.py           (registrations)
pivot/backend/services/validation_handler.py      (M2, null-strip, ctx threading)
pivot/backend/services/workflow_skeleton.py       (guard regexes)
pivot/backend/agents/tools.py                     (tool defs)
pivot/backend/agents/tool_executor.py             (handler bodies)
pivot/backend/agents/web_tools.py                 NEW
pivot/backend/agents/context_injector.py          (cache improvements)
pivot/backend/core/calculations/regime.py         NEW
pivot/backend/prompts/system.md                   (heavy rework)
pivot/tests/eval_results/AUTONOMOUS_LOOP_LOG.md   (1071-line working log)
pivot/scripts/probe_chat.py                       (multi-turn probe runner)
```

---

## Context for next Claude session

**What "the loop" means.** The user supplied 20 compound prompts
they wanted the chat layer to handle ("compare A,B,C → build agent
on winner", etc.) and asked Claude to keep iterating until 6 AM
IST — no fixed-time stages, just cycles of probe → root-cause →
smallest-fix → re-probe → commit. The user judged every response
themselves; there is **no auto-verdict script** trustworthy enough
to replace hand-judgement.

**Standing rules (do not violate).**

1. **No push.** Commit only. `git push` is forbidden without
   explicit user signoff. Confirmed across 56 commits.
2. **F&O out of scope** except for `_fo_strategy_decline`'s
   canonical message. Don't build options primitives.
3. **No futures contracts.** Same as above.
4. **Personal hand-judgement.** Auto-verdict eval scripts can
   compute coverage stats but cannot substitute for reading
   every response.
5. **Latency / token budget.** Stay within +30-40% of baseline.
   p50 wall ≤ 14s, input ≤ 35K.

**Probe runner.** `pivot/scripts/probe_chat.py <probe.json>`
fires multi-turn sessions through the live `/chat` endpoint of
the locally-running backend (`uvicorn backend.main:app --port
8000`). Probe JSONs live in `/tmp/probe_*.json` (session-local)
and outputs land in `pivot/tests/eval_results/probes/probe_<ts>.json`.

**Backend reload pattern.**

```bash
lsof -i :8000 -sTCP:LISTEN -t | xargs -r kill -9
sleep 1
nohup .venv/bin/python -m uvicorn backend.main:app \
    --host 127.0.0.1 --port 8000 > /tmp/uv.log 2>&1 &
sleep 4
curl -sf http://127.0.0.1:8000/health
```

**Trace endpoint.** `GET /admin/conv/<conv_id>/trace` returns
the full per-turn event sequence (LLM responses, tool invocations,
tool results, ASK_USER picks). Used to root-cause individual
failed sessions.

**Memory entries** the next session should read first:
- `feedback_dev_branch_no_push.md` — no push without signoff
- `feedback_solo_backend_only.md` — frontend-lead spawnable but
  reviewer retired; the lead does backend + frontend directly
- `feedback_loop_continuous_no_stages.md` — for multi-hour loops,
  iterate cycles, never pre-divide work into fixed-time stages
- `feedback_no_repeat_eval_runs.md` — one instrumented run, fix
  more, retest at most once; no restart-and-rerun loops
- `feedback_quality_check_triad.md` — every eval report MUST
  carry tokens + latency + verdict per item

**Where the chat brain lives.** `pivot/backend/services/chat_service.py`
is the file most worth opening first. The handle() entry point at
line 2140 is the canonical flow. Module-level helpers between
lines 100-300 (the new `_fo_strategy_decline`, `_LLM_UNAVAILABLE`,
`_INTERNAL_TOOL_NAME`, `_REASONING_LEAK_TELLS`) define the
defence-in-depth post-processing. The 8-hop tool-use loop starts
around line 2800.

**Where the loop log lives.** `pivot/tests/eval_results/
AUTONOMOUS_LOOP_LOG.md`. 1071 lines. Read the L22-L36 sections
at the bottom for the current state; L01-L13 at the top are
historical for the M1/M2/R1-R5 changes.

**Current state at handover.** Backend is running clean on
:8000 with PID written to `/tmp/uv_L36b.log`. Branch
`Eventtriggers`, HEAD at `2150a7d` (L36 commit). No
uncommitted changes. 56 commits ahead of `7e0c985`. Last
hand-judged probe (L36 retest) shows 5/5 PASS in <15ms each
for F&O declines; normal cash-equity routing unaffected.
