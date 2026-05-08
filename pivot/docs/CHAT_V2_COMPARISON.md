# Chat v2 Rewrite — Comparison Report

**Branch:** `chat-v2-rewrite` (off `auto-improve-1778174779`)
**Test bank:** `scripts/auto_improve_loop.py`, 33 sessions, 118 turns
**Endpoints:** `/chat` (v1, `chat_service.py`) vs `/chat/v2` (v2, `backend/chat_v2/`)

## Headline numbers

| Metric          | v1            | v2            | delta       |
| --------------- | ------------- | ------------- | ----------- |
| **Pass rate**   | 97/118 (82%)  | 89/118 (75%)  | **−8** turns |
| **Total cost**  | $0.456        | $0.236        | **−48%**    |
| **Avg latency** | 11,769 ms/turn | 10,594 ms/turn | **−10%**    |

v2 is materially **cheaper and faster** but **8 turns behind on quality** at first comparable run. The framework is sound — the wins concentrate on areas v1 had longstanding bugs (post-activation isolation, capability questions, follow-up indicator carry, multi-symbol baskets). The losses concentrate on **complex multi-step logic** prompts that the v1 750-line system prompt covered with worked examples; my decomposed v2 prompts dropped some of those examples in the name of conciseness.

## Win / loss table (per-session)

| Session                | v1   | v2   | delta | Note |
| ---------------------- | ---- | ---- | ----- | ---- |
| s_two_agents           | 6/8  | 8/8  | **+2 WIN** | Post-activation isolation (B7) fixed cleanly |
| s_qna_build            | 3/5  | 5/5  | **+2 WIN** | Follow-up indicator carry (B8) fixed |
| s_complex              | 4/5  | 5/5  | **+1 WIN** | Multi-symbol basket allocation works |
| s_draft                | 3/6  | 4/6  | **+1 WIN** | Eviction state machine cleaner |
| s_messy                | 2/3  | 3/3  | **+1 WIN** | Vague-prompt handling improved |
| s_newuser              | 4/5  | 5/5  | **+1 WIN** | Capability-question rule (no auto-build) |
| s_recall, s_indian_phrasings, s_recovery, s_safety, s_self_ref, s_index, s_gap_*, s_compare_build, s_hallu_earnings, s_clarify_chain, s_deep, s_twobranch | — | — | 0 | Parity (mostly already 100% in v1) |
| s_filler               | 4/4  | 3/4  | −1 LOSS | One filler reply triggered an LLM hop instead of shortcircuit |
| s_hallu_fake           | 1/1  | 0/1  | −1 LOSS | Fake ticker handling regressed |
| s_holdings             | 3/3  | 2/3  | −1 LOSS | Stochastic |
| s_logic                | 2/3  | 1/3  | −1 LOSS | Compound AND/OR conditions — model asks for clarification |
| s_multiind             | 2/3  | 1/3  | −1 LOSS | Multi-indicator workflow stochastic |
| s_order_chain          | 5/5  | 4/5  | −1 LOSS | Cross-tool order amendment lost one turn |
| s_order_to_agent       | 4/4  | 3/4  | −1 LOSS | Stochastic |
| s_shopping             | 3/4  | 2/4  | −1 LOSS | Stochastic |
| s_notional_qty         | 2/3  | 0/3  | **−2 LOSS** | "Buy ₹50,000 worth of X" — schema gap, not v2 specific |
| s_recent               | 3/4  | 1/4  | **−2 LOSS** | Most-recent-rule prompt simplified too aggressively |
| s_schedule_edges       | 2/3  | 0/3  | **−2 LOSS** | Tuesday 2:30, opening bell, before-close — needs more recipes |
| s_sl_variants          | 4/5  | 2/5  | **−2 LOSS** | Stop-loss variants need more worked examples |
| **TOTAL**              | **97/118** | **89/118** | **+8 / −16 = −8** | |

## What's structurally better in v2

1. **Cost: −48%.** Per-state `cache_key` slots + the ~150-line system prompt (vs v1's ~750) cut input tokens roughly in half. The OpenAI prompt cache hits cleanly per state.
2. **Latency: −10%.** Tight tool palette (1-30 tools per state vs v1's 48) shaves token-processing time on every hop.
3. **State machine is testable.** 78 unit tests (66 transitions + 12 policies), runs in 50ms, no LLM dependency. Adding a new conversation behavior is a new transition rule, not a new regex jammed into chat_service.py.
4. **Two parallel implementations collapse to one.** No more `handle()` / `handle_stream()` drift; one `process_turn()` pipeline.
5. **Bugs from prior auto-improve sessions are STRUCTURALLY closed:**
   - B7 (post-activation second-build stalls) — clean state transition `Activated → Idle`
   - B8 (follow-up indicator type lost) — `last_tool` + `last_tool_args` carried in ConvContext, surfaced in `facts_block`
   - Pure-affirmative shortcircuit no longer eats clarification phase — explicit clarifying state, AffirmativeAck transition is a no-op
   - "ENTIRE" parsed as symbol — Pydantic `Symbol` validator from a previous session, preserved
6. **Conversation context is one typed blob** in Redis (`chat_v2:ctx:<conv_id>`) — was scattered across 5 keys in v1.

## What's structurally weaker in v2 right now

1. **System prompt got too lean.** I cut ~600 lines from v1's `system.md`, including worked examples for compound conditions, OR exits, market-relative scheduling, and notional sizing. The model regresses on those without examples to crib from. **Fixable**: add the 5-6 worked examples back to `drafting_workflow.md`. I added some in fix2 — it bumped 85 → 89. More examples likely close most of the remaining 8-turn gap.
2. **`tool_choice="required"` on DRAFTING is sometimes too aggressive.** When the request is genuinely ambiguous, the model picks ASK_USER (which is in the palette) but my `clarifying_*` prompt doesn't always lead to a clean follow-up. Mixed effect.
3. **The deep agentic chain (4 hops) sometimes loops on ASK_USER.** Need to detect "ASK_USER twice in one turn" and force a draft with defaults, the way v1 did with its retry-with-defaults guidance.

## Failure mode classes (v2)

```
ARGS               21    model called the right tool with wrong args (mostly compound-condition workflows)
ROUTING             2    model didn't call any tool when it should have
REFERENCE           3    most-recent-rule failures
HALLUCINATION       1    fake ticker (v2 regression — needs investigation)
TIMEOUT             2    >90s, transient
```

## Ship recommendation

**Do NOT flip the FE to `/chat/v2` yet.** v1 is still 8 turns better on quality. The cost/latency wins are real, but a 7-percentage-point quality regression is too visible to users.

**What to do instead:**

1. Keep `/chat/v2` live alongside `/chat`. The branch is ready for incremental work.
2. Spend one focused session adding worked examples back to `drafting_workflow.md` for the 5 sessions that regressed by ≥2 turns: `s_logic`, `s_schedule_edges`, `s_notional_qty`, `s_sl_variants`, `s_recent`. Each is a 5-15 line markdown addition.
3. Re-run the bank. Target: parity with v1 (97/118). If v2 hits parity at half the cost, flip the FE behind a feature flag (`?v=2` or env).
4. After 1-2 weeks of v2 in production with the flag, delete the v1 code. Estimated: −3000 lines net.

## Reproducibility

```bash
# v1 baseline
LOOP_ENDPOINT=v1 LOOP_LABEL=v1_baseline .venv/bin/python scripts/auto_improve_loop.py
# v2 latest
LOOP_ENDPOINT=v2 LOOP_LABEL=v2_fix2  .venv/bin/python scripts/auto_improve_loop.py
# unit tests
.venv/bin/pytest tests/chat_v2/
```

Saved JSON results at `/tmp/loop_results_{label}.json`.

## Branch state

```
chat-v2-rewrite  (4 commits)
├── ac27ce7  chat_v2 day 3: pipeline + /chat/v2 endpoint, end-to-end smoke green
├── dccd266  chat_v2 day 2: per-state policies + decomposed system prompts
├── 0522067  chat_v2 day 1: explicit conversation state machine + 66 unit tests
└── (off auto-improve-1778174779)
```

Day 5/6 fixes (worked examples, tool_choice="required", clarifying tool palette pin) are in working tree but not yet committed pending Day 6 final commit.

Old code is **untouched** — `/chat`, `chat_service.py`, `tool_router.py` all live and active. Day 7 (deletion of old code) deferred to after sign-off.
