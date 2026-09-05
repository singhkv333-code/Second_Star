# Pivot

The engine half of Pivot, the holistic trading platform. Builds and backtests
strategies, runs structured products (capital protection, covered-call income,
bear-spread hedge), automates SIPs and conditional orders, and runs fundamentals
screens over a Moneycontrol-sourced financials database. FastAPI + PostgreSQL +
Redis; **Azure GPT-5.x** as the chat LLM (`config.llm_provider="azure"`).

> **Pivot simulates; it does not place live broker orders.** Strategies fill
> into a simulated paper book. The Zerodha order rails exist but are dormant —
> see `../CLAUDE.md` section 4. An earlier version of this paragraph said
> "places trades on Zerodha"; that has not been true since the paper-only
> decision, and Sarvam-m was replaced by Azure well before that.

> **In production this package is a library, not a service.** `:8000` is not
> deployed yet; `charto/data/execution_bridge.py` and `pivotted/fundamentals.py`
> import it directly. Before touching any database, read `../docs/DATA_MAP.md`.

## Agent System (Workflows v1) — new in May 2026

The user describes a strategy in chat, the bot proposes a structured workflow,
the user reviews/edits/activates in a side panel, and the workflow runs
autonomously on its triggers (cron / price / indicator / manual / webhook).
22 of 24 step types real, end-to-end demo path works.

**Where to start:**

| Audience | Read |
|---|---|
| Frontend dev wiring `pivot-next/` to the backend | [docs/HANDOFF.md](../docs/HANDOFF.md) |
| Anyone wanting to see the chat → DB → engine flow with real captured output | [docs/SYSTEM_WALKTHROUGH.md](../docs/SYSTEM_WALKTHROUGH.md) |
| Architecture spec (data model, engine invariants, scheduler, build sequence) | [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) |
| Wire format (every endpoint with curl examples) | [docs/API_CONTRACT.md](../docs/API_CONTRACT.md) |
| Daily progress | [STATUS.md](../STATUS.md) |

**Quickstart:**

```bash
# Backend up (sqlite, no docker needed)
cd pivot
APP_ENV=test JWT_SECRET_KEY="dev-secret-key-minimum-32-characters-long" \
  uvicorn backend.main:app --reload --port 8000

# Verify every Agent System endpoint
bash pivot/scripts/smoke_test_api.sh   # → 41 / 41 checks passed

# Try the chat-to-workflow translation directly (no LLM key needed)
python3 -c "
import asyncio, json
from backend.workflows.propose import propose_workflow_async
async def main():
    draft = await propose_workflow_async(
        'Every weekday at 3:55 PM IST if buying power > 50000 buy 10 RELIANCE and email me'
    )
    print(json.dumps(draft.model_dump(), indent=2, default=str))
asyncio.run(main())
"
```

## Chatbot architecture (existing — pre-Agent-System)

```
User → /chat → ChatService.handle()
                ├── ConversationStore (Redis, 24h TTL)
                ├── system prompt (versioned in backend/prompts/system.md)
                ├── ToolRegistry — full tool schema, LLM picks
                │      (now includes propose_workflow → opens the Agent panel)
                ├── Azure GPT-5.x call (1-2 hops; second after a tool result)
                └── post_process (strips any leaked <FOO>, <TOOL_CALL>)
```

The chatbot does not use an intent classifier. The LLM sees every available
tool on every turn and picks. Slash commands `/screen` and `/expr-backtest`
are kept as deterministic shortcuts the user types explicitly. The Agent
System adds `propose_workflow` as one more tool — when the LLM picks it, the
chat surfaces an "Open in editor →" card instead of plain text.

## Quick start

```bash
# 1. Start infrastructure (postgres + redis)
docker-compose up -d

# 2. Create the test database (one-time)
docker exec -it pivot_postgres psql -U pivot_user -c "CREATE DATABASE pivot_test_db;"

# 3. Install Python dependencies
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Copy env template and fill in secrets
cp .env.example .env
# edit .env — set JWT_SECRET_KEY, KITE_API_KEY, etc.

# 5. Run migrations
alembic revision --autogenerate -m "initial_schema"
alembic upgrade head

# 6. Start the API
uvicorn backend.main:app --reload --port 8000

# 7. Verify health
curl http://localhost:8000/health

# 8. Run tests
pytest tests/ -v
```

## Layout

- `backend/` — FastAPI app (config, database, models, schemas, auth)
- `tests/` — pytest suite (uses `pivot_test_db`)
- `migrations/` — Alembic migrations (env.py loads DATABASE_URL from `.env`)
- `docker-compose.yml` — Postgres 16 + Redis 7

## Running the chatbot eval

The 200-case eval suite lives in the sibling `pivot-eval/` repo. It tests
the *real* chatbot — never mocks. After any change to the chat path:

```bash
cd ../pivot-eval
uv run pivot-eval run                                         # ~12-20 min for 200 cases
uv run pivot-eval suggest                                     # pattern brief, no patches
open runs/<latest>/conversations.md                           # full transcripts, fails first
```

Targets we hold ourselves to:

| Category | Pass-rate gate |
|---|---|
| CASUAL    | ≥ 80% |
| FINANCIAL | ≥ 65% |
| AMBIGUOUS | ≥ 70% |
| MULTITURN | ≥ 60% |
| Overall   | ≥ 65% |

A re-run is part of the definition-of-done for any chat-path change.

## Make targets

```
make up        # docker-compose up -d
make dev       # up + uvicorn --reload
make test      # pytest
make migrate   # alembic upgrade head
make migration name="add_foo"  # new autogen revision
make clean     # down -v + clear pycache
```
