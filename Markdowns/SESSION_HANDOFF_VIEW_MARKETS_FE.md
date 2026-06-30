# Session Handoff — View Markets "Views" Tab (FE + API)

> **Date:** 2026-06-30 · **Branch:** `Eventtriggers` · **Head:** `f4d3bed`
> **State:** everything below is **BUILT + VERIFIED LIVE but NOT committed and NOT pushed.**
> Pick up here. When facts drift from code, the code wins — verify file/flag/IDs before relying on them.

---

## 0. TL;DR — what this session delivered

Built **Phase 6 of View Markets (V2)**: the chat-less **"Views" tab** end-to-end, then **redesigned it to a quant-grade visual bar** after a design-quality rejection. Net result: a new top-level nav tab **Chat · Views · Portfolio · Agents · Calendar · Screener** that renders the 3 curated market-belief views with real backtest metrics, risk/return visuals, and a one-click register-not-execute deploy path.

Two pieces of work, in order:
1. **Build** — a new `/api/views` backend router + the full `components/views/*` FE tab, wired into `AppShell`, flag flipped on.
2. **Redesign (Opus ultracode)** — replaced clunky mono numerals, killed the semicircle "confidence dials", added shadcn charts + a real option **payoff diagram**, QuantConnect-style stat strips, fixed layout/alignment. Verified in **light + dark**.

---

## 1. Current state / how to run

- **Servers (already running):** backend `uvicorn backend.main:app` on `:8000` (with `--reload`), frontend `next dev` on `:3000`.
- **Feature flag:** `pivot/backend/config.py` → `view_markets_enabled: bool = True` (was `False`; flipped to `True` for the beta — comment `# V2 beta: Views tab live`). This gates the `/api/views` router **and** the lifecycle scheduler worker.
- **Migration:** `0023_view_markets` is **already applied to Azure** (6 tables + 6 enums). No new migration this session.
- **Auth note for testing:** the app redirects to `/login` without a JWT. The reads (`GET /api/views`) are **global / no auth**, but the shell gates. To screenshot/test, mint a token:
  ```python
  # from pivot/, .venv/bin/python
  from backend.auth.jwt_handler import create_access_token
  from backend.database import SessionLocal; from backend import models
  db = SessionLocal(); u = db.query(models.User).get(1)
  print(create_access_token(u.id, u.email))   # user 1 = test@pivot.com
  ```
  then `localStorage.setItem("pivot_jwt", "<token>")` in the browser, go to `/#views`.
- **GOTCHA — deploy ownership:** the curated workflow drafts are owned by **`user_id=1`** (built during the strategies session). `GET /api/workflows/{id}` filters by user, so "Review & Arm" only opens the editor for **user 1**. For any other user it silently no-ops (a real multi-user gap — see §6).

---

## 2. Backend — `/api/views` router (NEW)

**File:** `pivot/backend/routers/views.py` (prefix `/api`, tag `Views`). Registered in `pivot/backend/main.py` (`app.include_router(views_router)` after `option_strategies_router`). Pydantic v2 response models in-file. Every endpoint gated on `settings.view_markets_enabled` → canonical 404 when off.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/views?status=&view_type=&category=` | List `ViewSummary[]` (non-archived, newest first) |
| GET | `/api/views/{view_id}` | `ViewDetail` (confidence dials, transmission, expectations, expressions+scores) |
| POST | `/api/views/expressions/{expression_id}/deploy` `{activate?,timing_mode?}` | Build/return the **register-not-execute** workflow draft → `{workflow_id,status,steps_count,activated}` |
| POST | `/api/views/{view_id}/compare` | `compare_tiers` — ranked tiers + `recommended_tier` + rationale |
| POST | `/api/views/expressions/{expression_id}/backtest` | On-demand `backtest_expression` (re-run + persist) |
| POST/DELETE | `/api/views/{view_id}/follow` | per-user follow toggle (401 if no user) |

- **Reuses** `backend/view_markets/{curation, deployment/{deploy,backtest,compare}, confidence}`. Does NOT reimplement engines.
- **Projection:** `expression.config` → `scores` (passthrough `backtest{}` + `construction_alignment` + `alignment_kind`), `structure` passthrough, `best_expression` by `(VERDICT_RANK, expression_score)` among scored only. Confidence DB score `0..1 ×100 → 0..100` + letter band (A≥85/B≥70/C≥55/D≥40 else F). Missing scores → `scores: null` (never fabricated).
- **Deploy semantics:** if `expression.workflow_id` set & `activate` falsy → returns the existing draft (no re-arm). Else `deploy_expression` (register-not-execute; every order step `requires_approval=True`, `book='live'`; **no order ever placed**). 422 on ValueError.
- **Tests:** `pivot/tests/test_views_router.py` — **9 passing** (list/filter, detail projection, missing-scores→null, flag-off 404, unknown-id 404, deploy short-circuit with a broker-call trip-wire, follow round-trip).

---

## 3. Frontend — the Views tab

**Wiring:** `pivot-next/components/AppShell.tsx` gained `"views"` in `TabKey`, a `NAV_ITEMS` entry `{ key:"views", label:"Views", Icon: Telescope }`, and a render branch → `<ViewsTab onOpenWorkflowById={openWorkflowById} />`. The deploy CTA reuses the existing `openWorkflowById` → AgentPanel workflow-draft editor (the proven activate/approve flow).

**Data layer:** `pivot-next/lib/types.ts` (View* types incl. `ExpressionInstrument`), `pivot-next/lib/api.ts` (`listViews/getView/deployExpression/compareViewTiers/backtestExpression/followView/unfollowView`, all via the `/api`-based `request<>()`).

**Component inventory** (`pivot-next/components/views/`):
- **Tab/cards:** `ViewsTab` (master↔detail state, filters, grid), `ViewCard`, `ViewFilters`, `ViewDetailPage` (hero → confidence → transmission → expectations → lifecycle → ladder), `ViewTransmissionMap`, `ViewLifecycle`, `ExpectationsSurprise`, `FollowButton`.
- **Expression surfaces:** `ExpressionLadder` (3 tiers + recommended badge + "+N more"), `ExpressionCard` (stat strip → per-kind viz → meters/trust/risk → rationale → **Review & Arm** CTA), `RiskReturnPanel` (per-kind viz dispatcher: option→payoff, basket→donut+distribution, pair→legs+benchmark).
- **Primitives/helpers:** `Stat.tsx` (`Num`/`Stat`/`StatStrip` — the Inter-tabular numeral primitive, replaces shared `Figure`/`Delta`), `ConfidenceMeter.tsx` (replaces deleted `ConfidenceDial`), `use-token-color.ts` (CSS-var reader, re-themes on `.dark`), `view-format.ts` (color/format helpers).
- **Charts** (`components/views/charts/`): `PayoffDiagram` + `payoff-math.ts`, `AllocationDonut`, `ReturnDistribution`, `BenchmarkCompare`, `RiskStrip`, `TrustLadder`.
- **shadcn chart primitive:** `pivot-next/components/ui/chart.tsx` (NEW; recharts-based, themed via CSS vars). Currently lightly used — charts mostly use raw recharts + `useTokenColors` (intentional).

**Design system (HARD rules — see `app/globals.css`):**
- **Numerals = `var(--font-display)` (Inter) + `tabular-nums`.** `var(--font-numeric)` (JetBrains Mono) is **banned in Views** (the original complaint). Audit = 0 occurrences.
- Letters = `var(--font-ui)` (Inter); serif `var(--font-experiment)` only for the belief-title hero.
- Tokens: `--bg-*`, `--glass-border*`, `--text-*`, `--color-profit/loss/warn`, `--pivot-blue`, `--radius-*`, `--ease-quartr`. Works light + dark.

---

## 4. The data it renders (3 curated views, already in Azure DB)

| View | type / id | expressions | best |
|---|---|---|---|
| **India's IT giants are in trouble** | event · `4f40f896-0953-4d66-bf6f-1932667b531e` | 6 | **R2 Defence+Auto basket**, Grade **A** / PROMISING, +46.0% (excess +47.6%), expr `3080d77a`, wf `faf26f7d` |
| **Monsoon trade — Kharif rural** | theme · `81809245-feeb-4ead-9f35-eb8166757cb7` | 3 | Conservative basket, +95% |
| **Crude / Geopolitical (de-escalation importer)** | event · `19f04e99-b704-4166-b99a-697049885d44` | 4 | **RC1 importer basket**, Grade **B** / UNPROVEN, expr `ac66729f` |

Each expression carries real `config.scores.backtest` (PSR/DSR/MaxDD/win-rate/MC prob-loss/MinTRL/CAAR/sub-period returns/outcome+expression dials/total+excess return) + `structure` (weights for baskets, legs for options/pairs) + rationale/risk/warnings + a linked `workflow_id` draft. These were produced in the prior **IT/Monsoon/Crude strategies research** (top-gainer + OLS-connectedness grounded; "genuine vs spurious" event linkage) — all backed by real yfinance/Kite data, never fabricated.

---

## 5. The redesign (what changed + why)

The first build was rejected as "AI slop" (mono numerals, weak semicircle gauges, thin charts, blank/unaligned detail). An **Opus ultracode workflow** (design → 3 build agents → verify) rebuilt the visuals to reference **Kalshi/Polymarket** (cleanliness) + **QuantConnect/Streak/Composer** (quant depth):
- **Font:** JetBrains-Mono numerals → **Inter-tabular** (matches the Screener).
- **"Stupid circles" → `ConfidenceMeter`:** segmented horizontal meter (letter chip + filled track + score), equal-height/aligned.
- **Charts:** allocation donut, return distribution, benchmark-compare, risk strip, **trust-ladder** stepper, and a real **option payoff diagram**.
- **Stat strips:** QuantConnect-style (big colored values, micro-labels, sub-captions).
- **Layout:** denser hero (chip + stat rail), aligned 2-col confidence, real cause→effect causal map.

---

## 6. Key decisions, gotchas & live-caught bugs

- **Register-not-execute preserved:** deploy only builds/arms a workflow draft; user confirms + places in their broker. No order path in the router (trip-wire test guards it).
- **Deploy = reuse the existing editor:** "Review & Arm" opens the linked `workflow_id` in the AgentPanel (or `deployExpression` first). Simple, consistent. **Open gap:** curated drafts are owned by `user_id=1`; other users can't open them (silent no-op). Fix later = per-user deploy drafts that don't mutate the shared `expression.workflow_id`.
- **Numeral primitive isolation:** Views uses a **local `Num`/`Stat`** instead of the shared `Figure`/`Delta` DS primitives (those hardcode `--font-numeric` and are used app-wide — editing them would re-skin the whole app).
- **Option payoff is normalized:** option legs use *relative* `strike_offset` and a string underlying (e.g. "NIFTY IT"), so the payoff is drawn on a **spot=100 normalized** moneyness axis, labeled "structure only — premium not priced" (premium isn't persisted → never invented).
- **Bugs I fixed live (workflow verify was tsc/lint-only and passed; these only surfaced at runtime):**
  1. `ReturnDistribution` used shadcn `ChartTooltipContent` outside a `ChartContainer` → `useChart` threw → **blank page**. Fixed with a self-contained tooltip.
  2. `ConfidenceDial` (old) treated an undefined `dial` as suppressed → every meter rendered blank. Fixed the suppression predicate (now `ConfidenceMeter`).
  3. `instruments` rendered as `[object Object]` (they're objects, not strings) → map to `.symbol`.
  4. `ViewDetailPage` shipped with inline **stubs** for `ConfidenceDial`/`ExpressionLadder` → rewired to the real components.

---

## 7. Verification done

- Backend: `ruff` clean, `pytest tests/test_views_router.py` → 9 passed, `import backend.main` OK.
- Frontend: `tsc --noEmit` clean, `eslint components/views components/ui/chart.tsx` clean, **font audit = 0** `--font-numeric`, `vitest` no new failures (pre-existing ~45 router-mock fails are unrelated).
- Live: `curl /api/views` + `/api/views/{IT}` return real projections; Playwright screenshots of grid + detail + expression cards + payoff in **light and dark** (in `…/scratchpad/redesign-*.png`).

---

## 8. NOT done / open threads (next session)

- **Commit + push** — nothing is committed. **HARD RULE: never push without explicit permission** (any branch/remote). 42 modified + 16 untracked files (Views FE/API + the prior view_markets pkg, migration 0023, strategies scripts). Commit freely; ask before pushing.
- **Multi-user deploy ownership** (see §6) — make deploy mint a per-user draft.
- **Phase 5 — chat integration** (deferred): summon/explore/express/deploy a View from the chat box (`propose_*_view` tools, routing, render-hints). Reuse `thematic_map.detect_thematic_scenario` + `_POSITIONING_RE`.
- **Priced option payoff** — wire the existing option-strategy compute endpoint for a real (priced) payoff instead of the normalized structural shape.
- **⚠️ PROGA / Polymarket regulatory finding** still UNVERIFIED — verify independently before any PM-odds-facing work (affects `trigger.polymarket/kalshi` + the "what's priced in" surface).

---

## 9. Pointers

- Spec/checklist: `Markdowns/Version2.md`, `Markdowns/VIEW_MARKETS_V2_CHECKLIST.md`, `Markdowns/VIEW_MARKETS_{PLAN,STRATEGY_DESIGN,TESTING_AND_SCORING,VIEW_TAXONOMY}.md`.
- Strategies research scripts: `pivot/scripts/strategy_research/` (+ `_out/*.json`).
- Auto-memory: `~/.claude/projects/-Users-karanveersingh-Downloads-Second-Star/memory/project_view_markets_v2.md` (+ `MEMORY.md` index).
- Root `CLAUDE.md` = auto-loaded project context (§9 = V2 direction).
