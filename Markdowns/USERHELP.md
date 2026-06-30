# PIVOT — Developer Help Guide
## What Has Been Built, How It Works, and What You Can Do With It

---

## What Pivot Is

Pivot is an AI-powered investing assistant for Indian retail investors.
Users chat with an AI that understands plain English and Hinglish, and
the AI executes investment strategies through their Zerodha account.

---

## Current State (After Days 1–3 + Backend Build)

### What Works Right Now (Zero Configuration Needed)

Everything below works immediately with no API keys and no Docker:

- User registration and login (JWT authentication)
- Health check at `/health` showing all system status + mock-mode flags
- Chat endpoint with mock AI responses
- SafeGrow capital guarantee note builder (calculates legs, payoff table)
- EarnMore covered call income engine preview
- StormShield bear protection note preview
- Product catalogue (7 product types listed)
- Portfolio dashboard (mock holdings: INFY, TCS, HDFCBANK, NIFTYBEES, GOLDBEES)
- Sector breakdown of portfolio
- Yield comparison table (savings / liquid / arbitrage / FD)
- SIP creation, listing, pause, resume, delete
- Strategy creation, listing, pause, resume, delete
- Backtest engine — runs on real yfinance historical data (no API key needed)
- Order preview (shows what will happen before executing)
- GTT order creation (Good Till Triggered)
- Intent classification (PROTECTION, INCOME, GROWTH, YIELD_OPT, …)
- Automated scheduler (SIPs execute, strategies monitored during market hours)
- Database operations with SQLite (no Docker required)
- Full test suite — `python -m pytest tests/ -v`

---

## Technical Stack

| Component | Technology | Why |
|---|---|---|
| Backend API | FastAPI (Python 3.11) | Async, auto-docs, Pydantic validation |
| Database | PostgreSQL (prod) / SQLite (dev/test) | ACID, financial data |
| Cache | Redis (prod) / MockRedis (dev) | Session storage, price caching |
| Scheduler | APScheduler | SIP execution, strategy monitoring |
| HTTP Client | httpx (async) | Sarvam AI, mfapi.in |
| Auth | JWT (python-jose) + bcrypt | Stateless, secure |
| Market Data | yfinance | Free historical OHLCV |

### AI Models Used

| Task | Model | Cost |
|---|---|---|
| General chat | Sarvam `sarvam-m` | FREE |
| Intent classification | Sarvam `sarvam-m` | FREE |
| Strategy explanation | Sarvam `sarvam-m` | FREE |
| Complex maths / guaranteed JSON | GPT-4o mini | ~₹0.04/call |
| Fallback if Sarvam down | GPT-4o mini | ~₹0.04/call |

---

## File Structure

```
pivot/
├── backend/
│   ├── main.py              FastAPI app, all routers, startup events
│   ├── config.py            env vars (API keys, DB URLs, JWT)
│   ├── database.py          SQLAlchemy engine (SQLite when APP_ENV=test)
│   ├── models.py            Tables: User, KiteSession, Strategy, SIPSchedule, ProductPosition, ProductLeg, TradeLog
│   ├── schemas.py           Request/response shapes
│   ├── cache.py             Redis client with MockRedis fallback
│   ├── safety.py            Non-negotiable order limits
│   ├── scheduler.py         APScheduler jobs for SIPs + strategies
│   ├── auth/
│   │   ├── jwt_handler.py   Create/verify JWT, hash passwords
│   │   └── router.py        POST /auth/register, POST /auth/login
│   ├── kite/
│   │   ├── auth.py          Kite login, TOTP, token exchange (mock if no key)
│   │   ├── orders.py        Place/cancel/GTT orders
│   │   ├── portfolio.py     Holdings, P&L, margins
│   │   ├── market_data.py   Live quotes (Kite) + historical OHLCV (yfinance)
│   │   └── mock_data.py     Realistic fake data for development
│   ├── agents/
│   │   ├── sarvam_client.py Sarvam AI API wrapper, retry, context trim
│   │   ├── openai_client.py GPT-4o mini wrapper, JSON mode
│   │   ├── router.py        Routes tasks to Sarvam vs OpenAI
│   │   ├── parser.py        Intent classification + entity extraction
│   │   ├── explainer.py     Plain-language strategy explanations
│   │   ├── sizer.py         Leg sizing, payoff tables, yield fetching
│   │   ├── structured_builder.py  SafeGrow / EarnMore / StormShield builders
│   │   ├── yield_scanner.py Fetches yields from mfapi.in
│   │   └── yield_optimizer.py     placeholder (future switch logic)
│   └── routers/
│       ├── orders.py        POST /orders/preview, /confirm, /gtt, GET /history
│       ├── chat.py          POST /chat, POST /chat/stream (SSE)
│       ├── sip.py           SIP CRUD
│       ├── strategy.py      Strategy CRUD
│       ├── products.py      POST /products/preview, GET /products/catalogue
│       ├── portfolio.py     summary / holdings / sector / products / yields
│       └── backtest.py      POST /backtest/run (yfinance, no API key)
├── tests/                   conftest + 9 test modules (SQLite, offline-capable)
├── migrations/              Alembic migrations
├── USERHELP.md              (this file lives at project root)
├── requirements.txt
├── docker-compose.yml
└── Makefile
```

---

## API Endpoints

Start server: `uvicorn backend.main:app --reload`  
Swagger UI: http://localhost:8000/docs

| Method | Endpoint | What it does |
|---|---|---|
| GET | `/` | API info |
| GET | `/health` | System status + mock mode flags |
| POST | `/auth/register` | Create account → JWT tokens |
| POST | `/auth/login` | Login → JWT tokens |
| GET | `/kite/callback` | Kite OAuth callback |
| POST | `/chat` | Message → AI response + intent |
| POST | `/chat/stream` | SSE chat (word-by-word) |
| POST | `/orders/preview` | Preview order before executing |
| POST | `/orders/confirm` | Execute a previewed order |
| GET | `/orders/history` | Last 20 orders |
| POST | `/orders/gtt` | Create GTT (price-triggered) order |
| POST | `/products/preview` | Build synthetic product |
| GET | `/products/catalogue` | All available products |
| GET | `/portfolio/summary` | Total value, P&L |
| GET | `/portfolio/holdings` | All holdings + sector mapping |
| GET | `/portfolio/sector` | Sector breakdown |
| GET | `/portfolio/products` | Active synthetic products |
| GET | `/portfolio/yields` | Live yield comparison |
| POST | `/sip` | Create a SIP schedule |
| GET | `/sip` | List all SIPs |
| PATCH | `/sip/{id}/pause` | Pause a SIP |
| PATCH | `/sip/{id}/resume` | Resume a SIP |
| DELETE | `/sip/{id}` | Delete a SIP |
| POST | `/strategies` | Create automation strategy |
| GET | `/strategies` | List strategies |
| PATCH | `/strategies/{id}/pause` | Pause |
| PATCH | `/strategies/{id}/resume` | Resume |
| DELETE | `/strategies/{id}` | Delete |
| POST | `/backtest/run` | Strategy backtest on historical data |

---

## Activating Real Connections

### Sarvam AI (FREE)
1. https://api.sarvam.ai → create account, get API key.
2. Set `SARVAM_API_KEY=...` in `.env`.
3. Restart — real AI replaces mocks.

### Zerodha Kite
1. https://developers.kite.trade → Personal App (free).
2. Set `KITE_API_KEY=...` and `KITE_API_SECRET=...` in `.env`.
3. After user Kite login, browser hits `/kite/callback?request_token=xxx`.
4. All portfolio + orders now route through real Zerodha.

### PostgreSQL (when Docker is available)
1. `docker-compose up -d`.
2. `DATABASE_URL=postgresql://pivot_user:pivot_password@localhost:5432/pivot_db` in `.env`.
3. `alembic upgrade head`.
4. Restart — Postgres takes over from SQLite.

### OpenAI (for complex reasoning)
1. Get key from platform.openai.com.
2. Set `OPENAI_API_KEY=...` in `.env`.
3. Router now uses GPT-4o mini for leg sizing and guaranteed JSON.

---

## Safety Limits (Non-Negotiable, see `backend/safety.py`)

| Limit | Value | Why |
|---|---|---|
| Max single order | ₹5,00,000 | Prevents misclicks |
| Max orders/day | 20 | Caps runaway automation |
| Max daily spend | ₹10,00,000 | Total spend cap |
| Max strategy budget | ₹2,00,000 | Per automated strategy |
| Min capital (structured products) | ₹10,000 | Makes options sizing viable |
| Confirmation required | Always | LogicCard must be confirmed |

---

## Running Tests

```bash
cd pivot
source .venv/bin/activate
python -m pytest tests/ -v
```

Tests run against SQLite with MockRedis and stubbed AI clients — no external dependencies required.

---

## Next Steps

1. Activate Sarvam (free) → real AI responses.
2. Activate Kite → real portfolio + orders.
3. Install Docker → PostgreSQL for production persistence.
4. Hand the API off to the frontend team.
5. Phase 7: yield optimiser daily job, portfolio biodiversity score, RBI rate-bet product.

---

*Pivot Backend — FastAPI + SQLAlchemy + Sarvam AI + Kite Connect*
