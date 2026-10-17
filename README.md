# Pivot

A trading and research platform for Indian markets: a chart that explains
itself, a strategy builder that tests honestly, and the research behind both.
`CLAUDE.md` is the product and engineering contract; this file is the map.

## Where things live

| Folder | What it is | Runs as |
|---|---|---|
| `app/` | The product. Next.js: the workspace shell, chat, stock pages, screener, strategies, paper book, sign-in, the public SEO pages. | `pivot-app` on :3000 |
| `chart/` | The chart engine. Vanilla JS over a patched Lightweight Charts build, mounted in the workspace at `/chart-app`. `patch-vendor.py` rebuilds the chart bundle, which is not in git. | static files, served by nginx |
| `dataserver/` | The dataserver (stdlib Python): bars, indicators, patterns, the chart's chat, drawings, alerts, live ticks, accounts and sessions, billing, the paper book, armed strategies. | `pivot-dataserver` on :5174 |
| `dataserver/jobs/` | Scripts that fill the dataserver's stores: backfills, syncs from Postgres, universe builds. Run by hand. | |
| `api/` | The API (FastAPI, Python package `backend`): the full chat, the tool registry, workflows and the strategy DSL, backtesters, fundamentals, screener, options, public company data. | `pivot-api` on :8000 |
| `research/` | The filings, shareholding and quarterly-metrics pipelines that load Postgres. Run by hand. | |
| `backtester/` | The point-in-time fundamentals backtester, installed into the API's venv (`pip install -e backtester`). | |
| `site/` | www.pivotnow.in (the marketing site, on Vercel), plus the `/paper`, `/strategies` and `/brokers` pages the VM still serves from it. | Vercel, and `pivot-site` on :5175 |
| `browser/` | The remote Chromium and SearXNG behind the Browser widget. | Azure Container Instances |
| `deploy/` | How the VM runs all of this: the release deployer, systemd units, nginx, backups. Start at `deploy/README.md`. | |
| `docs/` | Data map, design notes, handoffs, deployment records, research. Start at `docs/DATA_MAP.md`. | |

Two Python services share code on purpose: the dataserver imports the API's
`backend` package for the strategy engine (`dataserver/execution_bridge.py`),
and both read the same accounts store. `docs/DATA_MAP.md` says which store
belongs to whom.

## Running it locally

```
./dev.sh            # dataserver :5174, api :8000, app :3000 — open localhost:3000
```

Python comes from `api/.venv`; the app from `app/node_modules`. The dataserver
reads `api/.env` for its LLM key. Local stores are `dataserver/bars.db` and
`dataserver/users.db` (set `PIVOT_DB` / `PIVOT_USERS_DB` to use others).

## Tests

```
cd dataserver && ../api/.venv/bin/python -m pytest tests
cd api && .venv/bin/python -m pytest
cd app && npx vitest run && npx tsc --noEmit
node --test chart/tests/*.test.cjs
python -m pytest deploy/test_pivot_release.py
```

## Names that stayed

The chart side of the product used to be called `charto`. The code no longer
uses that name, but a few persisted names keep it because renaming them would
lose data or sign people out:

- browser storage keys (`charto:auth:token`, `charto_theme`, the chart
  store's `charto:` prefix, saved drawings)
- the PostHog identity `charto:<user id>` and the `surface: "charto"` property
- the BYOK key-derivation label `charto-byok-v1`
- Postgres names: `charto_account_links`, the `charto.*` pattern schema,
  `charto_landing.waitlist_registrations`
- the data files on the VM (`/srv/pivot-data/charto_*.db`) and the backup
  blob names, and Azure resource names (`charto-browser`, `pivotchartoacr`)
- `CHARTO_*` environment variables still work: they are read as `PIVOT_*`
