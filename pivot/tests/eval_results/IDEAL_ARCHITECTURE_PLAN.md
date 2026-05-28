# Ideal architecture: AI-at-core automation chat

Written 2026-05-29 after L01-L08 probes surfaced ~30 distinct
failure modes. The user asked: "research and plan what should be
the ideal way of prompts being like and for such automation that
has AI at its core to have as functions."

This document explores what an ideal function/tool surface looks
like for an AI-driven automation chat (Pivot's domain), then lays
out an incremental migration path with the highest-impact pieces
to implement first.

---

## Current state — what's working and what isn't

### What's working

- Skeleton fast-path for canonical shapes (RSI/SMA/EMA, single
  trigger, single condition, schedule) — ~20-30ms, no LLM hop.
  Handles ~40% of agent prompts directly.
- DSL translator for compound conditions — LLM hop with full
  grammar in scope, produces validated trees.
- Macro hydrators (threshold_order, scheduled_order,
  holding_action, basket_allocation) — server-side composition.
- R5 reply-class budget — explainer/short/capability/small_talk
  routing with proper markdown.
- Pending-resolution ledger — deterministic "yes" → option.

### What's still failing (root-cause buckets)

1. **Tool choice noise** — LLM picks the wrong drafter when
   multiple are plausible. Symptoms: propose_workflow when DSL
   would be cleaner; propose_dsl_workflow when propose_workflow
   should win; create_sl_order picked for trailing SL (no
   trailing support).

2. **Silent defaults** — LLM defaults qty=1 / RSI=14 / period=50
   when the user didn't say. The card carries the default, the
   user clicks Activate without noticing.

3. **Clarifications as free prose** — when the model isn't sure,
   it sometimes WRITES a question instead of calling ASK_USER.
   The pending_resolution ledger has no entry, so the next-turn
   resolution path can't fire deterministically.

4. **Multi-turn state drift** — active_draft leaks across topic
   shifts; the model re-emits stale workflows for unrelated
   queries.

5. **Fabricated errors / tool gaps** — model fabricates "indicator
   library not available" / "valid_until not supported" etc. when
   a tool path it tried failed silently.

6. **F&O / unsupported intents** — handled well but sometimes
   the model still tries to build a degenerate cash-equity proxy
   instead of declining cleanly.

---

## Design principles for "AI-at-core automation"

Reading across the failure buckets, six principles fall out:

### P1. Tools should be SHAPED LIKE USER INTENT, not engine primitives

Today's surface has propose_workflow / propose_dsl_workflow /
propose_threshold_order / propose_scheduled_order /
propose_holding_action / propose_basket_allocation /
place_market_order / place_limit_order / create_sl_order /
create_oco_order / create_dip_buy / create_gtt_order / create_sip.

The LLM has to learn which of these maps to a user's natural
intent. The model picks badly at boundaries because the
boundaries are engine-shaped, not user-shaped.

**Ideal:** 3-4 verbs the LLM picks between, each shaped like
user intent. The server-side handler decides which engine
primitive to use.

  - `trade_now` — place an order at market or limit, right now.
    Server picks place_market/limit/basket based on args.
  - `schedule_repeat` — recurring buy/sell/SIP. Server picks
    create_sip / propose_scheduled_order.
  - `gate_action` — when X, do Y. Server picks
    propose_threshold_order / propose_dsl_workflow /
    propose_workflow depending on condition complexity.
  - `protect_holding` — stop loss / take profit / trail on an
    existing position. Server picks create_sl_order /
    propose_holding_action / create_oco_order.
  - `alert_only` — notify when X. Same as gate_action with
    action_kind=notify.

The 4-5 narrow tools collapse to ~5 intent-shaped verbs. The
LLM's tool-pick problem goes away. The server's translator does
the engine routing.

### P2. ASK_USER must be STRUCTURED, never prose

The chat path forbids the LLM from writing free-form clarifying
prose. EVERY clarification goes through ASK_USER with:

```
{
  "question": str,
  "options": list[str] OR null,
  "default_on_yes": str OR null,
  "original_intent": str (the user's first request that started this),
  "queued_tool": str OR null (the tool we'd call once clarified),
  "queued_args": dict OR null (the args we'd pass)
}
```

The chat layer post-validates assistant messages: if the message
ends with "?" and contains question-marker words but no ASK_USER
tool was called, the layer routes the turn through the model
again with a "USE ASK_USER" directive.

### P3. State is a ledger, not implicit

Today: pending_tool_call + active_draft + pending_resolution +
conversation history live in Redis with different TTLs and no
cross-validation. The FE supplies history that can disagree.

Ideal: one `conv_ledger:{conv_id}` carrying:
```
{
  "state": "idle" | "drafting" | "awaiting_clarification" |
           "awaiting_confirmation" | "analytical",
  "active_draft": {tool, args, draft_id, rendered_at_ms},
  "pending_resolution": {question, options, default_on_yes,
                         queued_tool, queued_args},
  "pending_tool_call": {...},
  "original_intent": str,
  "turn_index": int,
  "updated_at_ms": int
}
```

State transitions are explicit. The router consults `state` to
narrow the tool surface (P1 ensures the surface is small).

### P4. No silent defaults — fail closed at validation

For action.place_order, quantity/notional MUST be explicitly
provided. The validator rejects qty=1 unless the user's prompt
contains "1" in a quantity context. The LLM is forced to
ASK_USER.

Same rule for: stop-loss values, threshold values, periods
(unless industry-standard: RSI=14, MACD=12/26/9), schedule times
(default 09:15 acceptable for SIP).

The validator surfaces structured errors with a "call ASK_USER
with this question" hint — not a free-form error the LLM has to
re-interpret.

### P5. Validate at DRAFT time, not run time

Mustache-ref backtest resolvability, expires_at parsing, multi-
symbol guards, F&O detection — all run at draft creation. The
server-side returns a structured draft + warnings + errors. The
chat layer renders warnings inline with the card so the user
sees the limitations before activating.

Today partial: R4a (backtest_resolvability), R4b (expires_at)
exist. Need: F&O detection, dynamic-sizing detection, time-
window detection (user said "intraday only" — engine doesn't
support, surface a warning).

### P6. Make the model's prompt small — push prompt engineering into the system, not user-typed text

A 25K-token system prompt is a code smell. The model is given a
catalog dump every turn. Ideal: the prompt is intent-shape
specific:

  - On a `trade_now` turn, the model sees only `trade_now`'s
    schema + 2-3 calibration examples.
  - On a `gate_action` turn, the model sees the gate_action
    schema + the DSL grammar (only if the trigger is compound).

This is achievable today by tightening the tool router. Tools
are already filtered per-turn but not aggressively enough.

---

## Proposed function shape (ideal)

```python
# Five intent-shaped tools — the LLM picks between these.
# Server-side handlers decide which engine primitive to use.

tool("trade_now",
  "Place an order RIGHT NOW. Cash equity, market or limit, one or "
  "many symbols. Use when the user names an immediate action with "
  "no scheduling or condition.",
  args = {
    legs: [{symbol, side, quantity OR notional_inr, order_type:
           market|limit|sl, limit_price?, stop_price?}],
    user_intent: str  # original user message verbatim for handler
  })

tool("schedule_repeat",
  "Recurring buy/sell/SIP on a schedule. Use for 'SIP', 'every X'."
  args = {
    symbol_or_sector,
    side,
    quantity OR notional_inr,
    cadence: {kind: daily|weekly|monthly, day_of_week?, day_of_month?,
              time_ist?},
    expires_at?,
    user_intent: str
  })

tool("gate_action",
  "When CONDITION, do ACTION. Covers indicator triggers, price "
  "triggers, compound conditions, news/event triggers, runtime-"
  "relative triggers. Server-side picks the engine shape.",
  args = {
    primary_symbol,
    entry_condition: str (verbatim NL),
    exit_condition?: str (verbatim NL),
    action_kind: notify | buy_market | buy_limit | sell_market | sell_limit,
    quantity OR notional_inr,
    limit_price?,
    expires_at?,
    user_intent: str
  })

tool("protect_holding",
  "Apply a stop-loss / target / OCO on an existing or newly-opened "
  "holding. Includes trailing stops. Server-side picks SL tool.",
  args = {
    symbol,
    sl_kind: fixed_price | percent_drop | trailing_percent | atr_multiple,
    sl_value: float,
    target_value?: float,
    quantity?: int,  # auto-resolved from holdings if missing
    user_intent: str
  })

tool("analyze",
  "Read-only analytics: explain, compare, fetch metrics, run "
  "backtest. Server-side picks the right read tools.",
  args = {
    kind: explain | compare | metric_fetch | backtest | snapshot,
    targets: list[str],  # symbols / topics
    detail_level: brief | standard | deep,
    user_intent: str
  })

# Plus a tightened ASK_USER:

tool("ASK_USER",
  args = {
    question: str (5-300 chars),
    options: list[str] OR null,
    default_on_yes: str OR null,
    original_intent: str,
    queued_tool: str OR null,
    queued_args: dict OR null
  })
```

Five drafter verbs + ASK_USER + a handful of read tools
(get_live_price, get_market_status, get_portfolio_summary,
get_indicator). Total tool surface: ~10. Today: ~45.

---

## Migration path — incremental, no big-bang

The ideal architecture is a Phase-2 project (~2 weeks of work).
For the autonomous loop horizon (hours, not weeks), the highest-
impact pieces to do incrementally:

### M1 (THIS LOOP): forbid free-form clarification prose

When the model writes a question instead of calling ASK_USER,
the chat layer detects and rejects. Adds a post-validator:
- If assistant text ends with "?" AND no ASK_USER tool was
  called this turn AND no draft was emitted AND the message is
  a question-shaped clarification, set a retry flag and re-emit
  with a "USE ASK_USER" directive.

This is the single highest-impact incremental fix. It catches:
- "yes that one" → no draft fabrication
- L08_17 multi-branch over-confirmation
- L07_03 trailing SL prose-only response

### M2 (THIS LOOP): server-enforced no-default validator

Add a post-build validator: walk the draft, for each
action.place_order, check the original user message for
quantity/notional indicators. If quantity is 1 OR not in the
user message AND notional_inr is missing, raise →
ValidationError → LLM retries with ASK.

### M3 (NEXT LOOP): unified intent state machine

Consolidate Redis state into a single ledger record. State
transitions explicit. Each state restricts tool surface.

### M4 (FUTURE): intent-shaped tool consolidation

Replace propose_* with the 5 intent verbs (P1). This is a
significant rewrite of system.md, tool_router, and the
agent_intent classifier. Not for the autonomous loop.

---

## What I'm implementing in THIS loop

M1 — forbid clarification prose without ASK_USER. Single chat-
layer guard. If detected, re-emit the turn forcing ASK_USER.

M2 — server-enforced no-default. Patch workflow_macros and
_dsl_chat_tools to detect "qty=1 from a prompt without a 1 in
quantity context" and reject.

After these are in, I'll commit, document, and re-probe.
