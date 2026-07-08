FROM python:3.11-slim

WORKDIR /app

# build-essential: several requirements (numba/llvmlite for py_vollib_vectorized,
# etc.) need a C compiler to build from source on slim images; the app already
# degrades gracefully if numba itself isn't usable, but pip install must not fail.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY alembic.ini .
COPY migrations ./migrations

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
