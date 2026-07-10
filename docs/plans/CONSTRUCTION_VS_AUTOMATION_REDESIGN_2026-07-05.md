# Construction vs Automation — routing redesign + backtest holding (2026-07-05)

Designed by the lead (Fable). Implementation split across Opus agents (waves below).
Philosophy alignment: `Markdowns/SYSTEM_PROMPT_REARCH_2026-07-03.md` (fix the
principle, not the prompt; core + intent packs; structural enforcement over prose)
and `docs/plans/STRATEGY_BUILDER_AND_QUESTIONS_PLAN.md` (build_strategy is THE
basket system).

## 0. The doctrine (one principle, applied everywhere)

Pivot's chat produces two different artifact families and must never confuse them:

- **CONSTRUCTION** — *what to own now.* A basket / portfolio / strategy that
  expresses a view (theme, event-positioning, factor, sector, quality). Artifact:
  `build_strategy` → `strategy_builder_card`. It exists the moment it is built.
- **AUTOMATION/AGENT** — *what to do later, contingently.* A trigger→action rule
  (schedule, price/indicator condition, verified event outcome, alert). Artifact:
  `propose_workflow`/macros → `workflow_draft_card`.

**The contingency test decides:** does the message state a *contingent future
action* — a schedule/cadence ("every Friday", "monthly"), a runtime condition
("when RSI<30", "if it drops 5%"), an alert/notify verb, or "when <event>
resolves/happens, do X"? If YES → agent/automation (existing paths, unchanged).
If NO — and the ask is to build/own something expressing a view — → CONSTRUCTION.

Corollaries:
- An *event-positioning* ask ("make a strategy around the RBI rate decision",
  "profit from a good monsoon") with no stated contingent action is CONSTRUCTION.
  After the basket card, the reply may OFFER the wired trigger (e.g.
  `trigger.scheduled_macro`) as an optional follow-up — offer, never substitute.
- A hybrid ("monsoon basket, rebalance quarterly") has a stated cadence → the
  workflow shape is correct, BUT its legs must be explicit named symbols
  (`action.allocate_basket` with legs), never a nameless screener step.
- "Strategy" is a CONSTRUCTION noun by default. It is an agent noun only when the
  contingency test passes or the user says agent/automation/rule/bot/workflow.
  Options strategies keep their existing F&O path (untouched).

## 1. Root causes being fixed (from the code maps)

1. `_thematic_guard_text` (chat_service.py:2867-2916) hard-instructs
   `propose_workflow` → `workflow_draft_card` for the six macro scenarios;
   `_apply_scenario_routing` (:2979) forces `_THEMATIC_BASKET_TOOLS`
   (propose_workflow…) with tool_choice=required. → monsoon ask renders an Agent
   card (the screenshot bug).
2. `_AGENT_INTENT_RE` (chat_service.py:339) lumps "build me a **strategy**" with
   agent intent → strips order tools, forces propose_workflow; no construction
   intent class exists; propose_workflow vs build_strategy tie is model-luck.
3. `build_strategy` has **no symbols allow-list** (tools.py:1966-2054) — the
   thematic DISCOVER→VET→JUDGE→BUILD flow cannot feed vetted names to the
   builder (thematic.md §5 references a field that doesn't exist).
4. `strategy_builder_card` is display-only (StrategyBuilderCard.tsx) — no
   save/deploy path, while the equity-basket CRUD
   (`routers/strategy.py` `/strategies/baskets` + `/{id}/trade`) sits disconnected.
5. Backtest holding gaps: (a) DSL tree silently force-sells after 10 bars when no
   exit given (`_dsl_chat_tools.py:591-596`, `workflows/dsl/backtest/schema.py:206`);
   (b) `workflow_backtester._expand_schedule` (:523-581) is cron-only — no one-time
   `run_at` entry, so buy-once-and-hold is inexpressible (live scheduler already
   supports run_at); (c) no initial-position seeding ("I hold 50 INFY @1400");
   (d) a real `hold` exit exists in `backtester/engine.py:423,440` but unreachable
   from chat; (e) nothing surfaces the assumed exit → silent-wrong results.

## 2. Wave A (Opus) — backtest holding semantics

Files: `services/_dsl_chat_tools.py`, `workflows/dsl/backtest/schema.py` +
`engine.py`, `services/workflow_backtester.py`, `agents/tools.py` (backtest tool
schemas/descriptions ONLY), `prompts/modules/backtest.md`, tests.

A1. **`hold_to_end` exit kind** in the DSL backtest: new
    `ExitPolicyDeclarative(kind="hold_to_end")` — position exits only at the
    window's final bar (engine already force-closes there; make the policy emit
    no earlier exit). Expose `exit_kind="hold_to_end"` in the `backtest_dsl_tree`
    tool schema; description: use it whenever the user says hold / don't sell /
    gives no exit AND phrases a hold. Keep `n_day_hold(10)` as the no-exit default
    for signal strategies BUT the result payload must carry an explicit
    `assumptions` entry ("exit: 10-bar hold (assumed) — say 'hold till end' to
    change") that the reply must state. Never silent.
A2. **One-time entry in `backtest_workflow`**: extend `_expand_schedule` to honour
    the one-time `run_at` schedule config the LIVE scheduler already supports
    (see workflow schemas / `project_one_time_schedule`) — fires exactly once at
    the given date (or window start if in the past + note). This makes
    "buy X on <date>/in <year> and hold" and one-time `action.allocate_basket`
    (basket buy-and-hold, MTM at end — already works at :2163-2183) expressible.
    Report n_trades=0 holds honestly: show MTM equity + unrealized P&L labelled.
A3. **Initial-position seeding** in `backtest_dsl_tree`: optional
    `initial_position {quantity, avg_price?, entry_date?}` on the primary symbol —
    engine seeds the open position at window start (cost basis = avg_price, else
    first bar open), exit conditions then apply. Enables "I'm holding 50 INFY from
    ₹1400 — backtest selling at RSI>70".
A4. **`prompts/modules/backtest.md`**: teach the three shapes (hold-to-end,
    one-time buy-and-hold via backtest_workflow run_at, seeded holding) as
    principles with one canonical example each; state-the-assumption rule.
A5. Tests: unit tests for hold_to_end exits at final bar; run_at fires once;
    seeded position P&L math; assumption string present on default-exit runs.

## 3. Wave B (Opus) — build_strategy symbols + basket card becomes real

Files: `agents/tools.py` (build_strategy block ONLY), `agents/tool_executor.py`,
`services/strategy_builder.py`, `services/strategy_contracts.py`,
`routers/strategy.py` (if a bridge helper is needed), FE:
`pivot-next/components/chat/StrategyBuilderCard.tsx` + `lib/agentsApi.ts` (reuse),
tests.

B1. **`symbols` allow-list param** on `build_strategy`: `symbols: string[]`
    (optional) — "explicit NSE constituents the caller has already vetted
    (e.g. via the DISCOVER→VET→JUDGE flow); pins the universe". Builder: when
    present, universe = these symbols exactly (no discovery); still fetch
    fundamentals for gate_metrics display (missing data → honest "(no data)",
    never drop a pinned name); weighting scheme + sizing still computed; sector
    cap becomes advisory (warn, don't drop) since the user/flow chose the names.
    Thread through `_map_build_strategy_args` and SlotState if needed.
B2. **Factor themes**: verify/extend theme resolution so factor-style themes
    (momentum, quality, value, low-vol) map to a broad liquid universe +
    factor-weighted scheme / multifactor gate — "a strategy that benefits from
    momentum" must produce a real momentum-tilted basket, not an empty pool.
B3. **Card actions** (FE): StrategyBuilderCard gains a footer action row:
    - **Save as basket** → `POST /strategies/baskets` (members = constituents
      {symbol, weight: weight_pct}, weighting "custom", capital_inr when known;
      name = card title). Show saved state + where it lives (Agents → Strategies).
    - **Deploy** (post-save) → the existing basket trade path
      (`POST /strategies/baskets/{id}/trade`, dry_run preview first), reusing
      `BasketTradeModal` if cleanly importable; register-not-execute language.
    - **Backtest** → sends a prefilled chat message ("Backtest this basket,
      buy-and-hold, last 3 years") through the existing composer path — the
      Wave-A run_at + allocate_basket shape handles it.
    Keep the card's amend-via-chat contract; strict TS; match card design language.
B4. Gold sleeve constituents with a real listed proxy (e.g. GOLDBEES) may be
    included as members on save; anything non-listable is omitted with a note.
B5. Tests: builder pins symbols; save payload mapping; tsc clean.

## 4. Wave C (Opus, after A+B land) — the construction intent layer

Files: `services/chat_service.py`, `services/tool_router.py`,
`prompts/system_core.md`, `prompts/modules/{thematic,baskets,events}.md`, tests.

C1. **New intent kind `construction`** in `_classify_intent`, checked BEFORE the
    agent regex. Construction = (build-verb + strategy/basket/portfolio/allocation
    noun, or "basket/portfolio of", or positioning phrasing
    "(strategy|basket|portfolio|stocks) (that|to|which) (benefit|profit|gain|play)s? (from|on)")
    AND NOT contingency (`_HAS_CONTINGENCY_RE`: every-<period>, at-<time>,
    when/if-<condition>, alert/notify/remind/watch, rebalance-<cadence>,
    "whenever") AND NOT explicit agent nouns (agent/automation/rule/bot/workflow)
    AND NOT F&O-mentions (options keep their path). One helper, used by BOTH
    handle() and handle_stream() (the known drift trap — single function).
C2. **Construction scope surgery** (mirror of the agent branch at :5335):
    force IN {build_strategy, ask_user_dynamic, screen_fundamentals,
    fetch_fundamentals, get_multiple_indicators, get_performance_metrics,
    compare_performance, get_price_history, get_live_price,
    propose_basket_allocation}; force OUT {propose_workflow, propose_dsl_workflow,
    propose_scheduled_order, propose_threshold_order, immediate order tools} —
    structural enforcement: a construction ask CANNOT render a workflow card.
    tool_choice="required" with the existing question-shaped relaxation.
    reply_class → strategy.
C3. **Thematic path rebuilt on construction**: rewrite `_thematic_guard_text`
    step 3 → "call `build_strategy` with `symbols=[<seed winners>]`, theme, the
    user's capital → `strategy_builder_card`"; keep decode / winners-losers table /
    confirm-invalidate / caveat / one-sharpening-question contract, keep the
    seeded names. `_THEMATIC_BASKET_TOOLS` → {build_strategy, ask_user_dynamic,
    screen_fundamentals, fetch_fundamentals, get_live_price}. Scenario routing
    still drops bare ASK_USER, keeps tool_choice=required.
C4. **Prompt modules** (principle-level, no per-example band-aids):
    - `thematic.md`: step 3 basket = build_strategy(symbols=vetted winners);
      BUILD step feeds survivors via `symbols` (the field now exists). Hybrid rule:
      stated cadence/trigger → workflow with explicit legs, never screener-only.
    - `baskets.md`: no-cadence basket/portfolio asks → build_strategy (the basket
      system); `propose_basket_allocation` reserved for stated-cadence
      rebalancing/SIP baskets; weight-decimal + cron guidance stays.
    - `events.md`: add the corollary — event-*positioning* without stated
      contingent action = construction basket now + optional offer of the wired
      trigger; contingent instruction = the existing trigger families.
    - `system_core.md`: add the CONSTRUCTION vs AUTOMATION doctrine + contingency
      test to the routing doctrine (compact); carve "strategy" out of the
      agent-noun list in the AGENT definition section.
C5. **tool_router**: extend the basket/construction rule (:677) to catch
    "strategy that benefits from momentum/quality/value" and factor keywords →
    build_strategy family; make module selection inject `baskets` on construction
    verbs and keep `thematic` on theme/scenario cues. The agent-build rule (:325)
    stops being the only owner of the word "strategy".
C6. Tests: intent-classifier unit tests (the four canonical prompts + agent
    counter-examples: "buy when RSI<30", "every Friday…", "alert me…",
    "basket rebalanced quarterly" → NOT construction); render-hint tests
    (monsoon prompt → strategy_builder_card steer, not workflow_draft_card);
    both handlers stay in sync.

## 5. Invariants (do not violate)

- Register-not-execute, never-fabricate, honest boundaries — unchanged.
- Existing agent/automation/backtest routes for genuine contingent asks must not
  regress (alert verbs → notify-only; time phrasing → schedule; etc.).
- Both handle() and handle_stream() get identical routing changes.
- Commit with explicit file paths (repo gotcha: concurrent tasks + bare `git add`
  have caused resets). Never push.
- Migration count: NO new migrations needed (equity baskets reuse legacy table).

## 6. Acceptance (the eval will test exactly this)

1. "Make me a basket of stocks that profit from a good monsoon." → thesis decode,
   winners/losers table, **strategy_builder_card** with named constituents+weights.
2. "Build me a strategy that benefits from momentum." → momentum-tilted
   **strategy_builder_card** with named securities.
3. "Create a strategy around the RBI rate decision." → positioning basket card +
   optional offer of the scheduled_macro trigger.
4. "Buy 10 NIFTYBEES every Friday" / "when RSI<30 buy INFY" / "alert me at 4000"
   → still workflow/automation cards (no regression).
5. "Backtest buying RELIANCE in Jan 2023 and holding it." → one-time entry,
   hold-to-end, MTM result, no silent 10-bar sale.
6. "I hold 50 INFY from ₹1400 — backtest selling at RSI>70." → seeded position.
7. Basket card → Save as basket → appears in Strategies tab; Deploy dry-run works.
8. Under-specified "build me a strategy" → ask_user_dynamic clarify card, then
   builds after answers.
