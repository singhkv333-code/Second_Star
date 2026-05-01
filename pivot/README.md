# Pivot

AI-powered investing platform for Indian retail investors — places trades on
Zerodha, runs structured products (capital protection, covered-call income,
bear-spread hedge), automates SIPs and conditional orders, runs fundamentals
screens and backtests over a Moneycontrol-sourced financials database. FastAPI
+ PostgreSQL + Redis, Sarvam-m as the chat LLM.

## Architecture (chat path)

```
User → /chat → ChatService.handle()
                ├── ConversationStore (Redis, 24h TTL)
                ├── system prompt (versioned in backend/prompts/system.md)
                ├── ToolRegistry — full tool schema, LLM picks
                ├── Sarvam call (1-2 hops; second hop after a tool result)
                └── post_process (strips any leaked <FOO>, <TOOL_CALL>)
```

The chatbot does not use an intent classifier. The LLM sees every available
tool on every turn and picks. Slash commands `/screen` and `/expr-backtest`
are kept as deterministic shortcuts the user types explicitly.

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
