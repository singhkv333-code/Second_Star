# charto/ — trial-and-error sandbox (NOT production)

This folder is a **separate, exploratory workspace** for Project Charto — a
proposed chat-drives-the-chart product surface for Pivot. It is deliberately
kept out of the production trees (`pivot/`, `pivot-next/`, `Markdowns/`) so that
early ideas, throwaway prototypes, and provisional specs are never mistaken for
the live app or its committed V2 plan.

**Nothing in here is wired into the running application.** As of 2026-07-23 it is
ideation + feasibility only; no Charto code ships.

## Contents
- **`CHARTO.md`** — the full spec: vision, constitution (rules & principles),
  the LLM-vs-backend boundary, the anti-deterministic-layer doctrine, the
  57-feature inventory, the trader's-philosophy design lens, feasibility
  verdicts, and the roadmap. **Start here.**

## Ground rules for this folder
1. **Sandbox, not source of truth.** Everything here is provisional. The Pivot
   repo is authoritative — when a claim here drifts from code, the code wins.
2. **Keep it isolated.** Don't import from or wire into `pivot/` or `pivot-next/`
   from this folder. Charto graduates *out* of here, feature by feature, when it
   becomes a real build (new `_render_hint` + FE card + deploy path, migrations,
   evals — the normal Pivot conventions).
3. **Constitution first.** Before writing any Charto code, ratify `CHARTO.md` §2
   (principles) and §4 (no pre-LLM interception) — those are day-one
   architectural choices that are nearly impossible to retrofit.
