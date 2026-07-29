FROM python:3.11-slim

WORKDIR /app

# build-essential: several requirements (numba/llvmlite for py_vollib_vectorized,
# etc.) need a C compiler to build from source on slim images; the app already
# degrades gracefully if numba itself isn't usable, but pip install must not fail.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Build context is the REPO ROOT (see deploy-backend.yml / az acr build) so we
# can also install the sibling pivot-backtester package. Paths below are
# therefore repo-root-relative (pivot/…, pivot-backtester/…).

# Python deps first for layer caching.
COPY pivot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# pivot-backtester (expr factor engine "Engine 1") — imported as `backtester`
# by the backtest paths (routers/chat.py, routers/expr_backtest.py). Without it
# those features fail at runtime.
COPY pivot-backtester ./pivot-backtester
RUN pip install --no-cache-dir ./pivot-backtester

COPY pivot/backend ./backend
COPY pivot/alembic.ini .
COPY pivot/migrations ./migrations

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
