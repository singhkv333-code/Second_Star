# Pivot

AI-powered investing platform for Indian retail investors. FastAPI + PostgreSQL + Redis.

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

## Make targets

```
make up        # docker-compose up -d
make dev       # up + uvicorn --reload
make test      # pytest
make migrate   # alembic upgrade head
make migration name="add_foo"  # new autogen revision
make clean     # down -v + clear pycache
```
