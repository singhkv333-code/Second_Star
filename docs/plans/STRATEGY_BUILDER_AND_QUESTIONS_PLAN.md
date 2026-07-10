# Strategy/Basket Builder + Dynamic Clarifying-Questions + Continuous-Improvement Harness — Approval Plan

Owner: lead architect · Status: ready for approval · Target surface: `pivot/backend` + `pivot-next`

This is a **systemic redesign**, not a band-aid. We do **not** "add a few `system.md` examples." We add (A) a real value-of-information question engine that **dynamically generates** questions per request, (B) a creative, fundamentals-DB-driven multi-asset strategy/basket builder, and (C) a GAN-like continuous-improvement harness that gates regressions. The three workstreams are co-designed: questions feed the builder; the harness measures both.

---

## 1. Problem & evidence

### 1a. What the system does today (bland by construction)

The blandness is **structural**, located in code, not a prompting accident:

- **Equal-weight is the hardwired default.** `services/workflow_macros.py:363` — `strategy: Literal["equal", "mcap_weighted"] = "equal"`. The LLM almost never overrides it, so every basket is 1/N unless the user literally says "mcap weighted." Applied unchanged at `workflow_macros.py:480` to the `action.allocate_notional` step.
- **Constituent selection is "top market-cap," never fundamentals.** `workflow_macros.py:466-471` builds the screener step with `"sort_by": "mcap"` and `"limit": 10`. The result is always the sector's index heavyweights — a size/momentum bet in disguise. No `screen_fundamentals`, no ROE/PE/D-E/F-score gate is ever chained in (confirmed: the workflow step sequence is `trigger.schedule → fetch.screener(sort_by=mcap) → action.allocate_notional(strategy=equal) → notify`).
- **The fundamentals tools exist but are never wired into the basket path.** `agents/tools.py:727-775` (`screen_fundamentals`: pe/roe/roce/de/payout/sector filters) and `agents/tools.py:777-786` (`fetch_fundamentals`) are available to chat, but the basket macro does not call them. `system.md:727-750` lists `screen_fundamentals` as a *separate* tool, and the basket guidance at `system.md:79-82` / `1707-1711` has **no** "gate constituents on fundamentals" instruction.
- **Options strategies are 15 hardcoded templates with a static risk ranking.** `services/option_strategies.py:67-159` defines the templates; `option_strategies.py:163-168` maps view→candidates with conservative ranked first **by convention**, not by assessing the user's capital/margin. No sizing-to-account, no skip-if-margin-insufficient guard.
- **No multi-asset, no concentration control, no hedges.** No MF/ETF core, no gold (SGB/ETF) sleeve, no protective-put/collar overlay, no sector cap, no correlation check. `tools.py` `ORDER_BASKET` subset is `[place_basket_order, get_live_price]` only.
- **Clarification is a single, shallow question.** `services/validation_handler.py:54-133` (`AskUserArgs`) supports **one** `question` plus an optional flat `options[]`. It only fires reactively when a required field is missing (`validation_handler.py:435-545`) — there is **no proactive, ranked, multi-question elicitation** and no question *card*. It surfaces with `raw_data={"_render_hint": "ask_user"}` (chat_service.py:4213, 5655, 5902, 7091, 7295) and is labelled "Asking you for one detail" in the FE (`ChatDemo.tsx:321`).

### 1b. Live-observation status

The live capture of the 6 basket/strategy prompts **could not be run**: the backend at `http://localhost:8000` was not functionally serving HTTP. `POST /chat/stream` hung the full 20s and returned `code 000` (no status line). Root cause was a failed `uvicorn --reload` restart — the surviving listener (PID 39674) inherited the port FD but logged `ERROR: [Errno 48] Address already in use`, so it accepted TCP but ran no ASGI app. A secondary, likely pre-existing instability was visible in the pre-truncation log: repeated `PendingRollbackError` from `backend/workflows/engine.py:155` plus `OperationalError`s consistent with lost connectivity to the Azure Central India Postgres. The capture harness is written (`/tmp/obs_chat.py`, venv python) and can be re-run once the backend owns the port and the DB is reachable.

**Consequence for this plan:** the blandness diagnosis is grounded in the **code map** (file:line above), which is authoritative and does not depend on a live run. The harness in Workstream C makes the live capture a **permanent, gated** step so we never again diagnose from a dead backend — and so the "before/after" on these exact prompts becomes a regression test, not a one-off.

### 1c. The two quality bars (from CLAUDE.md) this plan moves

1. **Execution correctness** — right intent, right tool, right card, faithful parse. Today: dropped fundamentals, ignored view, equal-weight collapse.
2. **Output quality** — data-rich, structured, defended. Today: "top-5 large caps, equal weight" with no rationale = a correct-but-thin failure.

---

## 2. Workstream A — DYNAMIC clarifying-questions system (CENTERPIECE)

**Thesis:** ask only what would **change the build**, generate every question **per request** (no hardcoded list), render as a question **card**, and fold answers straight into the builder. Grounded in VOI/EVPI/EIG literature (SAGE-Agent, Active Preference Inference, Modeling-Future-Turns, CLAM).

### 2a. The metric — Decision-relevance VOI with a burden penalty

A question is worth asking iff its answer would materially change the strategy card we'd build. For each candidate question `q`:

```
score(q) = StrategyEIG(q) − λ · BurdenCost(q)
StrategyEIG(q) = E_{a ∈ options(q)} [ Distance( build(request, a) , build(request, ∅) ) ]
```

- `Distance` = how much the would-be card's key parameters change across answers (direction, weighting scheme, leg structure, sizing, instrument, sleeve). High distance ⇒ the answer flips the build ⇒ high VOI. This is the operational form of EVPI ("ask only if the answer changes the action") and is provably equivalent to the EIG / KL model-change objective.
- `BurdenCost(q)` penalizes (i) aspects already specified in the request, (ii) aspects already asked this conversation, (iii) raw cognitive load. (SAGE-Agent's redundancy term `Cost(q) = λ·Σ_a n_a`.)
- **Cheap surrogate (production-realistic):** we do **not** run N full builds. The ranking LLM *estimates* `Distance` directly in one pass ("if the user answered each way, how differently would the resulting strategy be built? 0–1"), CoT over the candidate pool — exactly the surrogate the literature uses.

### 2b. The dynamic generation mechanism — generate → rank → validate → render

A new per-request pass (no static questionnaire anywhere):

1. **Slot inference.** From the request, classify each strategy-build slot as (i) already specified, (ii) inferable from data/defaults, or (iii) **unknown AND decision-relevant**. Slots: `view`, `risk`, `horizon`, `capital`, `asset_prefs/instrument`, `constraints` (see §3 builder inputs).
2. **Candidate generation.** Prompt the LLM to emit ~8–10 candidate questions covering **only** unknown+decision-relevant slots. Each candidate carries 4–5 answer **options grounded in the concrete request** (real tickers/structures/numbers the request implies — e.g. for "TCS options," instrument options are concrete TCS structures, not "stocks vs bonds"), plus a mandatory `Something else` (free-text) and `Skip`. The generator prompt enforces **MECE** (options mutually exclusive + collectively exhaustive modulo the catch-all) and **usage-framing** ("what do you want to do with this?") over abstract attributes.
3. **Rank** by `score(q)` (§2a); keep the top `k` per the stopping rule (§2c).
4. **Validate** each survivor: MECE check (reject overlapping/duplicate options), grounding check (options reference real instruments/data), de-dup against the request and prior turns.
5. **Render** as paginated single-question cards ("N of M").

### 2c. The stopping rule — how many, and when to skip entirely

- **Skip-entirely gate (run FIRST).** If the request is already specific — top strategy-candidate confidence ≥ `τ_high`, OR the margin between the top-2 candidate structures > `m` — **build directly, ask nothing.** Reuses the intent-confidence signals already computed at routing time. "Don't ask on reflex."
- **Per-question gate.** Keep `q` only if `score(q) ≥ τ_q` (its answer must materially change the build) — auto-prunes low-VOI questions.
- **Budget cap.** Ask **at most 4–5** (literature caps 3–4; cold-start users won't tolerate more). Stop early when `max_q score(q) < α · current_confidence`.
- **Honor Skip / "just build it".** A skipped question ⇒ fall back to that slot's sensible default and never re-ask it. A "build it now" short-circuits all remaining questions (respects deliberate under-specification).

### 2d. Backend design — new tool + endpoint + data shape

We **supersede** the thin single-question `ASK_USER` with a structured, multi-question generator while keeping the existing `ask_user` render-hint plumbing.

- **New synthetic tool `ASK_USER_DYNAMIC`** (registered alongside `ASK_USER` in `_ALWAYS_INCLUDE`, `tool_router.py:49-62`; defined next to `validation_handler.ask_user_tool_def`). The LLM does **not** author the questions field-by-field; it calls the tool with the *request context*, and the **backend** runs §2b generate→rank→validate. This keeps generation in code (testable, gateable), not buried in the prompt.
- **New service module `services/clarify_engine.py`** — owns slot inference, candidate generation, VOI ranking, MECE/grounding validation, and the stopping rule. Pure functions, unit-testable, no I/O except the LLM call + a read-only `screen_fundamentals`/`sector_universe` peek to ground options.
- **Emission via the existing channel.** `chat_service.py` emits the result as a tool-result with `raw_data={"_render_hint": "clarify_card", ...}` (mirrors the existing `ask_user` hint sites at chat_service.py:4213/5655/5902/7091/7295). `validation_handler` returns `needs_clarification=True` so the turn pauses without going to an executor (its existing intercept, validation_handler.py:1-26).
- **Data shape** (the `clarify_card` payload):

```jsonc
{
  "_render_hint": "clarify_card",
  "clarify": {
    "session_slot_state": { "view": null, "risk": "balanced(assumed)", ... },
    "total": 3,                 // M
    "index": 0,                 // N (0-based) → "1 of 3"
    "questions": [              // ranked, ≤5
      {
        "id": "q_view",
        "slot": "view",
        "prompt": "What's your read on TCS here?",
        "voi": 0.81,
        "options": [            // 4–5, MECE, grounded
          {"id":"bull","label":"Bullish — expect it to rise"},
          {"id":"bear","label":"Bearish — expect a drop"},
          {"id":"neutral","label":"Range-bound / sideways"},
          {"id":"vol","label":"Big move, unsure direction"}
        ],
        "free_text": true,      // → "Something else"
        "skippable": true       // → "Skip"
      }
    ]
  }
}
```

- **Answer ingestion endpoint / event.** Answers come back through the **existing chat turn** (the FE posts the chosen option label or free text as the next user message, tagged with `clarify_answer:{question_id}`), OR a thin `POST /chat/clarify_answer` that writes the answer into the conversation's slot-state in Redis (`services/conversation_store.py` / `turn_context.py`) and re-enters the build. We prefer the in-band message path first (no new endpoint, reuses per-session isolation) with the dedicated endpoint as a P2 optimization. Multi-question "N of M" pagination is driven by `index`/`total`; each answer advances `index` until the budget is exhausted or the stopping rule fires, then the builder runs.

### 2e. Frontend design — the question CARD

New message kind + component, slotted into the existing dispatch (`ChatDemo.tsx:512-530` union; `_render_hint` switch at `ChatDemo.tsx:841+`):

- **`kind: "clarify"`** in the `Message` union; dispatched when `hint === "clarify_card"`.
- **New component `pivot-next/components/chat/ClarifyCard.tsx`** matching the reference:
  - The question prompt + **4–5 option chips/radios** (one-click pick).
  - **"Something else"** → expands a free-text input.
  - **"Skip"** → advances without answering (sends `skip` for that `question_id`).
  - **"N of M"** pager (from `index`/`total`); answering advances; a back affordance to revise.
  - Footer microcopy **"…or reply directly"** so a user can ignore the chips and type a sentence (the LLM parses it).
- Reuses the editable-card visual language of `WorkflowDraftCard.tsx` / `OptionStrategyCard.tsx` (DS v2). Per-session isolation already holds (FE mints `s_<uuid>` per mount).

### 2f. How answers feed the build

Each answer (chip id, free text, or skip→default) is normalized into the **slot-state** object (§2d). When the stopping rule fires, `chat_service` hands the *filled* slot-state to the Workstream-B builder (`build_strategy(request, slots)`). Skipped slots carry their default and are flagged "(assumed)" so the resulting card can state the assumption and let the user amend — never blocking the build.

---

## 3. Workstream B — creative, DB-driven strategy/basket builder

**Thesis:** replace the equal-weight/mcap macro with a construction *pipeline* that names a weighting scheme, gates constituents on the **fundamentals DB**, supports multi-asset sleeves (options/commodities/MFs/hedges), and maps `{view × risk × horizon × capital × asset_prefs}` → a concrete, defensible structure.

### 3a. Construction logic (ordered pipeline)

**Inputs** (filled by Workstream A or inferred; missing ⇒ stated-assumption default):
```
view {direction: bull|bear|neutral|none, target: stock|sector|index|market, conviction}
risk {conservative|balanced|aggressive}
horizon {tactical<1y | medium 1-5y | long 5y+}
capital ₹  (gates lots, #names, SGB tickets)
asset_prefs allow/deny {equity, etf/mf, options, gold} + exclusions (sectors, PSU, ESG)
theme  optional ("quality compounders", "rate-cut beneficiaries", ...)
```

**Step 1 — Universe & selection (query the fundamentals DB HERE).** Build the candidate universe from theme/sector/index, then **gate/rank on the DB** whenever selection is fundamental: **Piotroski F-score gate** (drop < ~6–7) and/or **Magic-Formula rank** (Return-on-Capital × Earnings-Yield) and/or a **multi-factor score** (quality+value from the DB; momentum+low-vol from price data we already fetch). Skip the DB only for pure price/technical baskets, but still drop fundamentally broken names if data exists. **Always enforce a sector cap (≤~30–35%) + correlation check** so the basket can't collapse into one sector.

**Step 2 — Pick the weighting scheme (decision rule, not a default):**
```
#names ≤ 4 & single asset class      → equal-weight (honest, cost-efficient)
goal == capital_preservation/low-risk → minimum-variance
multi-asset OR "balance everything"   → risk-parity / ERC      ← smart default (beats 1/N ~84%)
explicit "diversify broadly"          → maximum-diversification
user stated name-level tilts/views    → Black-Litterman (mcap prior + chat view = the BL input)
theme is a factor                     → factor-weighted (BLEND 2 factors to fight cyclicality)
"own the market" passive              → market-cap
else                                  → risk-parity (smarter-than-1/N fallback)
```

**Step 3 — Macro structure:** `"safe + moonshots"` → **barbell** (75–85% safe / 15–25% aggressive); `"invest for me, long term"` → **core-satellite** (60–80% ETF core + satellites); specific directional bet → single focused structure.

**Step 4 — Sleeves (only when they earn their place):**
- **Options sleeve** — when view + capital + F&O allowed. Map view→structure (bullish→long call / bull-call spread / short put; bearish→long put / bear-put spread / covered call; neutral→iron condor / butterfly), and **pick buy-vs-sell from live IV/PCR** (high IV → sell condor/credit spread; low IV → buy spread). Owned position + hedge intent → **collar / protective put** with real strikes/greeks from the chain.
- **Commodity (gold) sleeve** — 5–15% when conservative / long horizon / inflation-rupee hedge intent; surfaced as **SGB (long core) + Gold ETF (liquid)**. MCX stays research-only.
- **MF/ETF core** — index/debt/sector ETFs as core when capital is small or low-effort wanted.
- **Hedge sleeve** — bearish-but-invested or conservative+long: protective put on index proxy, or gold/long-bond ballast (All-Weather logic).

**Step 5 — Sizing & feasibility vs capital.** Round to lots/feasible tickets; if an options lot or SGB minimum doesn't fit, **say so and offer the nearest real structure** (ETF instead of basket; spread instead of naked option). Honest boundaries over fake success.

**Anti-bland guardrails asserted before render:** (1) no naked equal-weight unless ≤4 names/single class; (2) selection must name a gate, never "top mcap" alone; (3) sector cap + correlation check enforced; (4) a stated view must map to a structure (tilt/BL/options); (5) honest boundaries when a sleeve is infeasible.

### 3b. Exactly what changes where (design level, real names)

| Layer | File / symbol | Change |
|---|---|---|
| **Tool schema** | `agents/tools.py` (near `propose_basket_allocation`, 1708-1749) | Add `build_strategy` tool: inputs = the §3a slot object. Extend `propose_basket_allocation` schema: `strategy` enum gains `risk_parity \| min_variance \| max_diversification \| black_litterman \| factor`; add `selection_gate` (`fscore \| magic_formula \| multifactor \| none`), `sector_cap`, `sleeves[]` (options/gold/hedge). |
| **Executor** | `agents/tool_executor.py` (`_propose_basket_allocation`, 884-885; `_build_option_strategy`, 1790-1831) | Route `build_strategy` to a new `services/strategy_builder.py`. Keep option-template path but let the builder *compose* it as a sleeve. |
| **Construction engine** | **new `services/strategy_builder.py`** | Owns Steps 1–5. Calls `services/fundamentals_screen.py` + `screen_fundamentals` for the DB gate, `services/sector_universe.py` / `thematic_map.py` for the universe, `services/option_strategies.py` for option sleeves, price data for momentum/low-vol. Produces a `strategy_builder_card` payload. |
| **Weighting** | **new `services/weighting.py`** | `equal / mcap / erc / min_variance / max_diversification / black_litterman / factor`. Covariance from price history; BL prior from mcap + chat view. |
| **Basket macro** | `services/workflow_macros.py:358-527` | Replace the hardwired `strategy="equal"` (363) and `sort_by="mcap"` (466-471) defaults: the macro now consumes `strategy`, `selection_gate`, `sector_cap`, `sleeves` from the builder and inserts a `screen_fundamentals` step **before** allocation. Equal-weight only survives for ≤4 names. |
| **Tool router** | `services/tool_router.py:629-638` (basket) & `757-783` (options) | Surface `build_strategy` on basket/strategy intents; co-surface `screen_fundamentals` + `fetch_fundamentals` with the basket tools so the DB gate is always reachable. Add `build_strategy` to `_ALWAYS_INCLUDE` (49-62). |
| **System prompt** | `prompts/system.md` (basket 79-82/1707-1711; fundamentals 727-750) | Behavioural contract only (NOT examples): "baskets must name a weighting scheme and a selection gate; never bare equal-weight/top-mcap; enforce sector cap; map any stated view to a structure or sleeve." |
| **FE card** | **new `pivot-next/components/chat/StrategyBuilderCard.tsx`** + `ChatDemo.tsx` union/dispatch (512-530, 841+) | `kind: "strategy_builder"` on `hint === "strategy_builder_card"`. Shows constituents+weights with **scheme named** + **gate named**, sleeves (option legs w/ greeks, gold %, hedge), and a one-paragraph rationale tying back to `{view×risk×horizon×capital}`. Editable like `WorkflowDraftCard`; register-not-execute; ends with the not-advice disclaimer. |

### 3c. How it consumes the question answers

`strategy_builder.build_strategy(request, slots)` takes the slot-state object that Workstream A fills. Each slot drives a branch in Steps 1–5 (view→structure/sleeve; risk→scheme + defined-vs-undefined; horizon→expiry/timeframe + gold ballast; capital→#names/lots/feasibility; asset_prefs→which sleeves are allowed; constraints→guardrails). Skipped/assumed slots take defaults and are surfaced as "(assumed …)" in the card rationale.

---

## 4. Workstream C — continuous-improvement harness (GAN-like)

**Chosen approach (from the research shortlist):**

**(1) Eval/data flywheel with calibrated LLM-as-judge + regression gates — adopt first.** Everything else is unmeasurable without it. Pivot's own history is a string of one-off eval runs (GAN R3/R4, automation 29%→55%, quality 17/8/8) plus the standing rules "evals must be multi-turn + live" and the "tokens+latency+quality triad." That *is* the spec.

**(2) Tool-use verification loop + inference-time self-critique against Pivot's constitution — layer on top of #1.** Highest correctness ROI for lowest infra. Pivot uniquely owns ground-truth tools (Kite, option chains, fundamentals DB) + a written constitution (`system.md` + CLAUDE.md). A pre-send pass that (a) asserts every number in the prose traces to a card/tool value and (b) self-critiques against the explicit rules (no fabrication, end-with-disclaimer, no fake-success, tables for comparisons, anti-bland guardrails of §3a). Gate to high-stakes turns to respect the RTT-bound latency budget.

**(3) Phase-2 only: DSPy program optimization** of the multi-stage pipeline (intent → clarify → build → card) — strictly downstream of #1, because every prompt optimizer needs a stable scored metric.

*Deliberately not adopted:* RLHF/RLAIF + rejection-sampling fine-tuning (need training infra Pivot doesn't run); full multi-agent debate (N× latency vs RTT budget). Inference-time best-of-N is kept as a *selective* booster on rare high-value turns only.

### 4a. How it runs (online + offline)

- **Offline (CI gate):** a **frozen multi-turn golden set** seeded from the 6 basket/strategy prompts (the ones the live obs couldn't capture) + the historical bug repros (silent DSL amendment collapse, basket collapse, fake-success). `auto_batch_eval.py`-style harness runs them against a live `:8000`, the judge scores each turn, results compared to a stored baseline. Tooling that fits the stack: DeepEval (pytest-style) / Braintrust / LangSmith.
- **Online (sampled):** a cheap reference-free judge runs on a small % of live chat traffic; failures are captured **trace-to-dataset** and queued for human label → they become permanent offline cases (the flywheel).

### 4b. What it measures (the triad, per item — non-negotiable)

Every item carries **tokens + latency + quality** (CLAUDE.md "quality-check triad"):
- **Execution correctness:** right intent/tool/card, faithful parse, no dropped condition.
- **Output quality:** scheme named, gate named, sector cap present, view mapped, markdown table for comparisons, defended view, not-advice disclaimer, no fabricated numbers (tool-verification assert).
- **Anti-bland metrics (new, builder-specific):** % baskets that are bare-equal-weight (target ↓), % using a fundamental gate (target ↑), sector-concentration (capped), sleeve-attach rate when view+capital present.
- **Clarify metrics:** % turns that asked (should be low — only when VOI high), avg questions/turn (≤ budget), skip rate, post-answer build-change rate (proves VOI was real).

### 4c. How it gates regressions

CI fails the build if any golden case regresses below its baseline on **any** triad dimension, or if an anti-bland metric crosses its threshold (e.g. bare-equal-weight rate rises, fabricated-number assert trips). A/B/canary (~5% traffic) promotes a new prompt/builder version only if quality holds or improves. Resolved bugs become frozen cases so they can never silently return.

---

## 5. Architecture, data-flow, phases, risks

### 5a. Data-flow (text diagram)

```
user message
   │
   ▼
tool_router.py  ── intent + confidence signals ──┐
   │                                             │
   ▼                                             ▼
chat_service turn loop                  [Skip-entirely gate]  confident? → BUILD directly
   │                                             │ ambiguous?
   ▼                                             ▼
ASK_USER_DYNAMIC  ───────────────►  clarify_engine.py
                                     slot-infer → generate ~8-10 candidates
                                     → VOI rank (StrategyEIG − λ·Burden)
                                     → MECE/grounding validate → stopping rule (≤5)
   │                                             │
   │   _render_hint:"clarify_card" {questions[], N of M}
   ▼                                             │
ClarifyCard.tsx  ◄───────────────────────────────┘
   │  user picks option / "Something else" / "Skip" / replies directly
   ▼
slot-state (Redis via conversation_store / turn_context)
   │  (filled or default-on-skip)
   ▼
strategy_builder.py  build_strategy(request, slots)
   │   Step1 universe + fundamentals-DB gate (fundamentals_screen / screen_fundamentals)
   │   Step2 weighting.py (ERC/min-var/BL/factor…)   Step3 macro (barbell/core-sat)
   │   Step4 sleeves (option_strategies / gold-SGB-ETF / hedge)   Step5 sizing
   │   anti-bland guardrails assert
   ▼
_render_hint:"strategy_builder_card"  →  StrategyBuilderCard.tsx  (editable, register-not-execute)
   │
   ▼
[Workstream C]  online judge samples this turn → trace-to-dataset → golden set → CI gate
```

### 5b. Phased execution (P0 → Pn)

- **P0 — Foundation/harness (Workstream C #1).** Freeze the multi-turn golden set (6 prompts + bug repros), wire the calibrated judge to the triad, add the CI regression gate. *Ships:* a trustworthy "before" baseline + the gate. (Also fix the backend port/DB instability so live capture works.)
- **P1 — Builder core (Workstream B Steps 1–3).** `strategy_builder.py` + `weighting.py` + DB gate + sector cap + `build_strategy` tool/executor/router wiring + `StrategyBuilderCard.tsx`. *Ships:* non-bland baskets (named scheme + named gate), measured against P0 baseline.
- **P2 — Dynamic questions (Workstream A).** `clarify_engine.py` (generate→rank→validate→stop), `ASK_USER_DYNAMIC`, `ClarifyCard.tsx`, slot-state in Redis, answers→builder. *Ships:* the question card, gated by VOI; clarify metrics in the harness.
- **P3 — Sleeves & multi-asset (Workstream B Step 4).** Option overlays (buy/sell from live IV/PCR), gold (SGB/ETF), hedges, barbell/core-satellite. *Ships:* creative multi-asset structures.
- **P4 — Verification + self-critique (Workstream C #2).** Pre-send number-traceability assert + constitution self-critique on high-stakes turns; online sampling judge live.
- **P5 (optional) — DSPy optimization (Workstream C #3).** Optimize the pipeline prompts/few-shots against the now-trustworthy metric.

### 5c. Risks & open decisions

- **Latency (RTT-bound budget).** Generate-then-rank + self-critique add LLM calls. *Mitigation:* single-pass surrogate `Distance` (no N builds), gate clarify behind the skip-entirely test, gate self-critique to high-stakes turns, cache covariance/fundamentals.
- **Backend/DB stability** (live-obs root cause). *Open decision:* must be fixed in P0 or the harness can't run; verify Azure Central India reachability + clean port ownership.
- **Over-asking UX.** *Mitigation:* hard budget ≤5, `τ_q` per-question gate, "just build it" escape, online "% asked" metric watched.
- **Covariance quality for ERC/min-var/BL** on thin/illiquid names. *Open decision:* min history window + shrinkage; fall back to equal-weight with a stated reason when covariance is unreliable (honest boundary).
- **Fundamentals DB coverage gaps** (Moneycontrol + yfinance fallback). *Mitigation:* gate degrades gracefully; if data missing, state it and rank on what's available.
- **MECE enforcement** is LLM-judged. *Mitigation:* post-hoc validator rejects overlapping/duplicate options; "Something else" guarantees exhaustiveness.
- **Scope of register-not-execute** unchanged — sleeves register, never auto-execute; MCX stays research-only.

---

## 6. Non-goals & how the design avoids hardcoding/blandness

**Non-goals:**
- No model training / fine-tuning / RL pipeline (Pivot consumes a hosted model). No full multi-agent debate. No best-of-N on every turn.
- No live broker auto-execution; no MCX trading; no personalised buy/sell advice — data + frameworks only, every card ends with the not-advice disclaimer.
- **No hardcoded question list** anywhere in `system.md` or code. No static "investor questionnaire."
- Not a "few more `system.md` examples" — the contract changes are behavioural rules; the *logic* lives in `clarify_engine.py` / `strategy_builder.py` / `weighting.py`.

**How hardcoding is structurally prevented:**
- Questions are **generated per request** (slot-infer → LLM candidate pool → VOI rank → validate). The prompt holds *how to generate/rank*, never *the questions*. Options are grounded in the request's real instruments, so they cannot be a fixed list.
- The stopping rule is **computed** (skip-entirely gate + `τ_q` + budget), so the system asks 0 questions when confident — the opposite of a reflex questionnaire.

**How blandness is structurally prevented:**
- The equal-weight default (`workflow_macros.py:363`) and mcap-sort (`:466-471`) are **removed** as defaults; the builder must *choose* a scheme via the Step-2 decision rule (ERC is the smart fallback, not 1/N).
- Selection **must name a fundamentals-DB gate** (F-score / Magic-Formula / multi-factor); "top market-cap alone" is rejected by guardrail #2.
- **Sector cap + correlation check** prevent single-sector collapse; **multi-asset sleeves** (options/gold/MF/hedge) make a basket *do something*.
- The **harness** (Workstream C) measures bare-equal-weight rate, gate-usage rate, sector concentration, and sleeve-attach rate, and **gates regressions** — so blandness cannot silently return.
