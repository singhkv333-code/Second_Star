# api/

The Pivot API: FastAPI, PostgreSQL, Redis. The Python package is `backend`.
It holds the full chat, the tool registry, workflows and the strategy DSL, the
backtesters and trust ladder, fundamentals, the screener, options, paper
trading, billing, and the public company data behind the SEO pages.

It runs on the VM as `pivot-api` (:8000, `PYTHONPATH=<release>/api`). The
dataserver also imports it as a library for the strategy engine
(`dataserver/execution_bridge.py`). Read `../docs/DATA_MAP.md` before touching
a database.

> **Pivot simulates; it does not place live broker orders.** Strategies fill
> into a simulated paper book. The broker rails exist but are dormant behind
> `live_orders_enabled` (see `../CLAUDE.md` section 4).

## Where things are

| Path | What |
|---|---|
| `backend/main.py` | The app and its routers (`backend/routers/`). |
| `backend/services/chat_service.py`, `flex_chat.py` | The chat turn: pre-LLM layer, tool loop, reply class. |
| `backend/prompts/system_core.md`, `modules/` | The chat contract and per-intent packs, joined by `assembler.py`. |
| `backend/services/tool_registry.py`, `agents/tool_executor.py` | Tool schemas and dispatch. |
| `backend/workflows/` | The workflow DSL, its evaluator and engine. |
| `backend/backtester/`, `services/backtest/` | Backtests and the trust ladder. |
| `backend/market/` | Market data, `financials_db` (Moneycontrol), the dataserver bar reader. |
| `backend/paper/`, `execution/`, `brokers/` | The paper book, the execution layer, dormant broker rails. |
| `migrations/` | Alembic. |
| `scripts/` | Hand-run jobs: universe and logo builds, profile enrichment, probes. |
| `tests/` | pytest. |

## Running it

```bash
cd api
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # if you have no .env yet; set the DSNs and keys
.venv/bin/uvicorn backend.main:app --reload --port 8000
curl http://localhost:8000/health
```

`../dev.sh` starts this together with the dataserver and the app.
`docker-compose.yml` and the `Makefile` give a local Postgres and Redis
(`make up`, `make migrate`, `make test`).

## Tests

```bash
.venv/bin/python -m pytest
```

A change to a tool, its prompt module or its evals is one unit: re-run the
others before merging.
