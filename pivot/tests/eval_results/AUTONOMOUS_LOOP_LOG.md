# Autonomous loop log — improving chat quality

Started: 2026-05-29
Branch: Eventtriggers
Mode: solo, no push, commit incrementally

## Working method

1. Pick a focus area
2. Hand-craft probe prompts and send through live /chat (not the
   automated eval verdict script — I judge each response myself)
3. Read every response in full
4. Identify the root cause of any quality issue
5. Make the structural fix
6. Re-test the same probe + an adjacent shape
7. Commit when the change is coherent
8. Move to next focus area

## Index

(Each entry below is one probe → fix → retest cycle.)

---

## L01 — S04 over-confirmation regression (RESOLVED)

**Probe:** 5 variants of "build agent buy X if Y above resistance" → answer with "use 20-day rolling high" / "use 50-day low" / "use 1700".

**Initial state:** 1/5 sessions failed (S04 exact replay — render hint stayed `ask_user`, model emitted prose "I can run that as-is").

**Root causes (3 independent bugs, cascading):**

1. **Null arg rejection** — Azure's function-calling layer emits explicit
   `null` for optional fields the model decided not to use (observed
   on `propose_dsl_workflow.exit_condition: null`). The JSON-schema
   validator was rejecting null → `success=False, error="exit_condition: expected string, got NoneType."` → loop hop 2 → LLM wrote prose
   instead of a draft. **Fix:** `_validate_args_against_schema` now
   treats `null` on optional fields as "field omitted" (validation_handler.py).

2. **"none" placeholder string** — Once the validator accepts null, the
   LLM started passing the literal string `"none"` for the same
   field. The DSL handler tried to translate `"none"` into a tree and
   produced a vacuous comparison (1.0 == 1.0). **Fix:** Handler now
   treats `{"none", "null", "n/a", "no exit", "—", "-"}` as empty
   (_dsl_chat_tools.py).

3. **`_is_post_order_clarification` over-fires on agent intent** — The
   helper forced `intent_kind="automation"` when (a) prior msgs
   contained "buy", (b) current msg was ≤40 chars, (c) prior asst
   ended in '?'. This matched "use 20-day rolling high" after a
   "Build agent — buy HDFCBANK ..." prompt. Automation intent strips
   propose_dsl_workflow from the tool surface, so the LLM picked
   `backtest_dsl_tree` instead. **Fix:** Helper now bails when the
   FIRST user message classifies as 'agent' intent
   (chat_service.py).

4. **Weak followup_hint on clarification merge** — Even after the
   tool was visible, the LLM passed `condition="stock closes above
   resistance"` unchanged (placeholder NOT substituted with the
   user's reply). **Fix:** Hint now carries an explicit example:
   "original 'buy HDFCBANK if closes above resistance' + reply 'use
   20-day rolling high' → condition='close above the 20-day rolling
   high'." Also explicitly forbids passing `null` / `'none'` for
   optional fields (chat_service.py).

**Retest:** 5/5 sessions PASS, all render `workflow_draft_card` with
correct `trigger.compound + action.place_order` shape.

**Open sub-issue (deferred):** When the suggested default (e.g.
"20-day rolling high") is offered AND the user provides a literal
value ("use 1700"), the model sometimes still picks the default
instead of the literal. The pending_resolution hint says "Map it to
one of the options if possible" but no enumerated option for the
literal exists. Will revisit in L03 or later.

---

## L02 — boundary tool selection (15 canonical shapes, MOSTLY RESOLVED)

**Probe:** Hand-crafted prompts on the threshold/scheduled/dsl/workflow
boundary where I know the correct tool.

**Initial state (judged by reading each response):**
- 11/15 PASS: 01-05 (single + AND/OR), 08 (3-branch), 10-12, 14-15
- 4 FAIL: 06 (cross-symbol silently corrupted by skeleton → trigger.price(₹3)),
  07 (multi-symbol → "draft validation issue" prose), 09 (trailing SL →
  create_sl_order, no trailing support), 13 (relative-threshold →
  trigger.manual instead of trigger.schedule)

**Root causes + fixes (2 commits):**

1. **workflow_skeleton: cross-symbol guard** — `_distinct_ticker_tokens`
   helper + 2+-ticker bail at the entry to `try_workflow_skeleton`.
   Otherwise the skeleton grabs the first ticker and silently produces
   a wrong draft in <30ms (was the worst class of failure — user
   can't see it's wrong).

2. **DSL multi-symbol guard refined to multi-ACTION detection** —
   `_has_multi_action_tickers` walks each action verb and collects
   tickers up to the trigger word. 2+ in the action span = refuse
   (multi-action). 1 in action + others elsewhere = allow (cross-symbol
   trigger, DSL-friendly).

**Retest after fixes (live):**
- L02_06 cross-symbol → DSL draft ✓
- L02_06 variant (different phrasing) → DSL draft ✓
- L02_07 two-symbols → propose_workflow multi-branch draft ✓
- L02_13 relative-threshold → propose_workflow with trigger.schedule ✓

**Remaining open:**
- **L02_09 trailing SL** — model still picks `create_sl_order` (lacks
  trailing support) instead of `propose_holding_action` (which does).
  Tool description nudging needed. Defer to L03.
- **L02_07 trigger choice** — emits trigger.manual rather than
  trigger.schedule for the auto-firing intent. Less critical because
  the draft still works (user can run manually or convert), but worth
  fixing.

---

## L03 — clarification merging + multi-turn drift (12 sessions, 4/5 FAILS RESOLVED)

**Probe:** 12 sessions probing clarification + multi-turn shapes:
- yes after disambiguation, fixed vs trailing SL, change-mind,
  off-topic mid-draft, cancel, two drafts in one session,
  amendment, negative response, long drift, explain-then-build.

**Initial state (judged by reading):**
- 4 PASS: 02, 05, 07, 09
- 3 PARTIAL: 01, 03, 10 (10 = test design issue)
- 5 FAIL: 04 (hallucinated draft), 06 (fabricated error), 08
  (sell→notify confusion), 11 (drift broken), 12 (covered call
  misinterpreted)

**Fixes (1 commit):**

1. **PendingResolution forces tool emit** — when PendingResolution is
   active and user reply is NOT a pure 'yes', `agent_tool_choice` is
   forced to `required` AND the propose_* tools are added to the
   surface. Previously the model wrote "Drafted: M&M buy on RSI <
   30" prose with no actual tool call. Both `handle()` and
   `handle_stream()` patched.

2. **Independent-intent regex extensions**:
   - Price-history / chart-data patterns ("show me last week's
     price", "chart of X") — were treated as draft amendments.
   - "Now also build / build another agent / new agent" override —
     was caught by the stepwise "at <number>" amendment rule.

3. **create_sl_order description sharpened** — points at
   propose_holding_action for trailing / holding-based shapes.

**Retest after fixes (live probe):**
- L03_04 "yes that one" → propose_threshold_order draft ✓
- L03_06 off-topic during draft → live price returned cleanly ✓
- L03_08 "now also build a sell agent" → place_limit_order ✓
- L03_11 long drift → no spurious workflows on data lookups ✓

**Remaining open (deferred):**
- L03_01 trailing SL: model picks `propose_dsl_workflow` but no
  draft emitted. propose_holding_action would be the right tool.
- L03_12 covered call: F&O limitation should surface explicitly
  rather than building a sell-on-RSI workflow.



