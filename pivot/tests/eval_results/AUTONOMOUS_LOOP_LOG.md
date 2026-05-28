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

