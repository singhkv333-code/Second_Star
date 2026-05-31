# Pivot

A chat-first investing assistant for Indian retail investors. You talk to it in
plain language — check prices and fundamentals, screen stocks, run backtests,
place simulated (paper) trades, and build **agents** (conditional workflows that
fire on a price / indicator / schedule / news trigger). Everything routes
through a single chat surface backed by a paper-trading book and a forward-test
scorecard.

Stack: **FastAPI + PostgreSQL + Redis** (backend) · **Next.js 15 + shadcn/ui +
Tailwind** (frontend) · a standalone **expression backtester** over a
Moneycontrol-sourced financials DB.

---

## Repository layout

| Path | What it is |
|---|---|
| **`pivot/`** | The backend. FastAPI app under `pivot/backend/` (package `backend.*`), Alembic migrations, tests, infra. This is where chat, orders, paper trading, the workflow/agent engine, and market data live. |
| **`pivot-next/`** | The frontend (the one we use). Next.js app — chat, Portfolio, **Paper** (positions / orders / journal / **Ideas** forward-test scorecards), Agents, Calendar, Screener. |
| **`pivot-backtester/`** | The expression/indicator backtest engine. Installed editable into the backend venv (`import backtester`) and mounted as the `/api/.../expr_backtest` router — powers the indicator-backtest cards in chat. **Not optional.** |
| **`docs/`** | System documentation (architecture, API contract, DSL grammar, paper-trading plan, walkthrough). |
| `STATUS.md`, `BACKLOG.md`, `USERHELP.md` | Running status, backlog, and end-user help. |

> The backend Python package (`pivot/backend/`) is organised by concern:
> `routers/` (HTTP) · `services/` · `core/` · `paper/` (paper-trading book) ·
> `workflows/` (agent/DSL engine + backtest) · `agents/` (chat tools) ·
> `market/` · `kite/` (broker) · `news_events/` · `llm/` · `triggers/`.

---

## Quickstart

Prerequisites: Python 3.11 (a `.venv` lives in `pivot/`), Node, and a local
Postgres + Redis (a `docker-compose.yml` in `pivot/` brings them up).

```bash
# 1. Infra (Postgres + Redis)
cd pivot && make up           # docker compose up -d

# 2. Database schema
make migrate                  # alembic upgrade head

# 3. Backend  →  http://127.0.0.1:8000
PYTHONPATH=. .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 4. Frontend →  http://localhost:3000   (reads NEXT_PUBLIC_PIVOT_API_BASE from .env.local)
cd ../pivot-next && npm run dev
```

Health check: `curl http://127.0.0.1:8000/health` → `{"status":"ok", ...}`.

The frontend talks to the backend via `NEXT_PUBLIC_PIVOT_API_BASE`
(`http://127.0.0.1:8000/api`); legacy surfaces (`/chat`, `/paper`, `/orders`,
`/auth`) are served at the root and proxied by `next.config.ts` rewrites.

---

## Where to read next

| You want… | Read |
|---|---|
| The end-to-end chat → engine → DB flow with real output | [`docs/SYSTEM_WALKTHROUGH.md`](docs/SYSTEM_WALKTHROUGH.md) |
| Architecture (data model, engine invariants, scheduler) | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Every endpoint with curl examples | [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) |
| The agent/workflow DSL grammar | [`docs/dsl_grammar.md`](docs/dsl_grammar.md) |
| The paper-trading + forward-test design (P0–P6) | [`docs/PAPER_TRADING_PLAN.md`](docs/PAPER_TRADING_PLAN.md) |
| Backend specifics | [`pivot/README.md`](pivot/README.md) |
