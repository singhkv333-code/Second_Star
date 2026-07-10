# System-prompt re-architecture — evidence & plan (2026-07-03)

Checkpoint before any edit: git tag `pre-systemmd-restructure` @ c470c31.

## The problem (user's thesis, confirmed)
`system.md` is one monolithic 2,514-line / ~36k-token file, sent **verbatim every
turn**. It has accreted band-aid rules for individual past failures, which now
*suppress* the base model's good instincts and force over-asking / over-building.

## Four independent evidence sources agree

### 1. Structure map (assembler/chat_service)
- `assembler.build_system_prompt()` loads the WHOLE `system.md` every turn (no
  slicing). Cached as a static prefix; per-turn hints appended as extra system msgs.
- **Widget-forcing (the "fires a widget every time" complaint):**
  `agent_tool_choice = "required" if is_agent_intent else "auto"`
  (chat_service.py:5357, mirror ~7180). `is_agent_intent` comes from a broad regex
  `_AGENT_INTENT_RE` — so any message mentioning a condition/automation verb makes
  hop-1 `tool_choice=required` → the model **cannot** return text, must fire a tool.
  Reinforced by scenario routing (`drop_ask_user=True`, forces required) and the
  M1 retry that converts prose questions into `ASK_USER` cards.
- **Seams that already exist:** tool_router is a flat `(regex → tool-set)` union;
  `_build_deterministic_guards()` and `thematic_map` already do
  detector→directive-string→appended-system-message. The conditional-instruction
  pattern is proven; it just isn't used to slice system.md.

### 2. Section inventory (classification of all 64 sections)
- CRITICAL-IDENTITY: 2.4% (863 tok) — tiny, keep untouched.
- CRITICAL-ROUTING: 61.9% (22k) — mostly legit but over-specified (compress examples).
- INTENT-MODULE: 32.2% (11.5k) — options/backtest/thematic/baskets/events/hedge/
  stoploss/webhook/polymarket/news — only needed on their own turns → conditional-load win.
- BAND-AID: 3.5% — §25 (timeframe ASK-FIRST), §31 (rigid onboarding), §37 (WRONG/RIGHT dump).
- **Contradiction:** §3 (omit interval, let platform ask) vs §25 (model must ASK_USER
  and wait) — the root of the "which timeframe?" reflex in eval #2/#4/#23.

### 3. Base model, NO Pivot rules (probe A) — instincts are already good
- "options play on HDFC" → naturally asks *"bullish / bearish / neutral?"* (Pivot
  over-built a neutral Iron Condor).
- "invest in gold every month" → naturally redirects to a **Gold ETF SIP** (Pivot #12 failed).
- "15 stocks of ITC" → reads 15 shares, drafts order, no re-ask.
- "options on Nifty this week" → structures by view, asks for view.
→ The rules are suppressing correct default behaviour, not creating it.

### 4. Model self-critique (probe B) + how it wants to be instructed (probe C)
- Independently found the same contradictions + 9 over-ask rules.
- Missing: "a single priority hierarchy (safety/ambiguity > tool immediacy > style),"
  "a decision tree: intent class → required fields → default/ask/tool," "a canonical
  required-args schema per tool."
- Endorsed core + intent-packs: **Core = safety + truthfulness + default behaviour;
  Intent packs = domain mechanics + calculations + formatting.**

## Plan (4 phases, each committed, reversible via the tag)

**P1 — Behavioural fixes (reason, don't recite).**
- Resolve §3/§25 contradiction: delete the rigid "ASK FIRST" timeframe rule; replace
  with a *clarify-priority principle* (ask the single blocking unknown, ordered
  size/exit > unit ambiguity > soft threshold; a daily default is fine when unstated).
- Loosen tool_choice=required so the chat can answer in text when that's right; keep
  required only for unambiguous order/emit intents.
- Add the missing decision hierarchy + ask-vs-act rule to the core.

**P2 — Split into files + conditional loading ("file discovery").**
- `prompts/system_core.md` (always-on: identity + routing doctrine + decision tree)
  + `prompts/modules/{options,backtest,baskets,thematic,events,hedge,stoploss,
  webhook,polymarket,news}.md`.
- Loader injects core always + the module(s) for the detected intent (reuse the
  tool_router `(regex → …)` seam). Cuts per-turn tokens ~30% and makes editing local.

**P3 — Compress the over-specified CRITICAL-ROUTING** (retail-capability §4, workflow
routing §28, example dumps) into principles + a few canonical edge examples.

**P4 — GAN eval loop** (finder generates diverse vivid prompts + runs them → critic
grades ask-vs-act / correctness / widget-text match → fix the *principle*, not the
prompt → repeat; then a fresh vivid set for final results).
