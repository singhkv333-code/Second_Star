# charto/ — the charting engine. **This is production.**

Charto is Pivot's charting engine and, as of 2026-08, the surface most users
actually touch. It is not a sandbox and has not been one since 2026-07-29.

> **History note.** This folder began as an exploratory workspace, and both this
> file and `CHARTO.md` used to say "nothing in here is wired into the running
> application." That stopped being true weeks before anyone corrected it, which
> is how a stale status line becomes the most misleading sentence in a repo.
> Corrected 2026-09-05.

## What ships from here

| Path | Runs as | Serves |
|---|---|---|
| `preview/` | static, served off disk by nginx at `/` | The chart app — 26k lines of vanilla JS over a vendored Lightweight Charts v5 |
| `data/dataserver.py` | `charto.service` on `:5174` | Bars, indicators, patterns, scene, drawings, alerts, live ticks, paper book, strategies, auth |
| `web/` | `charto-web.service` on `:5175` | The company page `/stock/[symbol]`, `/paper`, `/strategies` |
| `deploy/` | — | systemd units, nginx conf, `apply_nginx.sh`, `check_routes.sh`, provisioning |

## Read first

- **`CHARTO.md`** — the constitution: the LLM-vs-backend boundary, the evidence
  hierarchy, the feature inventory, the design lens. Still the governing
  document for anything drawn on the chart.
- **`../docs/DATA_MAP.md`** — before touching any store. `charto_users.db` is
  not an auth database; it holds the entire live user-state plane.
- **`preview/VENDOR_PATCHES.md`** — six patches to the vendored charting
  bundle. The bundle is a *different minification* from upstream, so a patch
  written against upstream source will silently not apply.
- **`EXECUTION_STATE_AND_PLAN_2026_09_02.md`** and the two
  `RESEARCH_MODE_GRADE_*` files — the current honest quality bar.

## Ground rules

1. **Bump `?v=N`** on every edited `preview/*.js` — `index.html` cache-stamps
   every script, and a missed bump ships an old file to every browser.
2. **Importing `pivot/` is expected, not forbidden.** `data/execution_bridge.py`
   borrows Pivot's strategy engine deliberately: one derivation, one set of
   numbers. The old "keep it isolated" rule is retired.
3. **Every fraction of time is measured in BARS, not seconds.** A ratio, a
   projection or a drag that reasons in clock time is wrong at session edges.
4. **nginx has a route allowlist** — unlisted routes return 200 with
   `index.html` and never 404. Gate every new route with
   `deploy/check_routes.sh`.
5. **Evidence or nothing.** A rate without a control is decoration.
