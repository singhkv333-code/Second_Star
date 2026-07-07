# PostHog post-wizard report

The wizard has completed a deep integration of PostHog analytics into the Pivot FastAPI backend. A singleton `Posthog` client is initialized at startup (via `posthog_client.py`), flushed on shutdown, and consumed by 7 route files covering every critical user-facing action: authentication, chat, order registration, workflow CRUD, backtesting, Opinion Markets, and broker connection. Users are identified at login/signup so sessions correlate across devices.

| Event | Description | File |
|---|---|---|
| `user_signed_up` | A new user registered with email and password. | `pivot/backend/auth/router.py` |
| `user_logged_in` | An existing user logged in with email and password. | `pivot/backend/auth/router.py` |
| `user_signed_up_google` | A new user registered via Google Sign-In. | `pivot/backend/auth/router.py` |
| `user_logged_in_google` | An existing user logged in via Google Sign-In. | `pivot/backend/auth/router.py` |
| `chat_message_sent` | A user sent a chat message to the Pivot AI assistant. | `pivot/backend/routers/chat.py` |
| `order_confirmed` | A user confirmed and registered a buy or sell order. | `pivot/backend/routers/orders.py` |
| `workflow_created` | A user created a new automation workflow (agent). | `pivot/backend/routers/workflows.py` |
| `workflow_activated` | A user activated an automation workflow to run on schedule. | `pivot/backend/routers/workflows.py` |
| `backtest_run` | A user ran a backtest on a trading strategy. | `pivot/backend/routers/backtest.py` |
| `opinion_followed` | A user followed an Opinion Markets view. | `pivot/backend/routers/views.py` |
| `opinion_expression_deployed` | A user deployed an expression from an Opinion Markets view. | `pivot/backend/routers/views.py` |
| `broker_connected` | A user successfully connected a broker account (Kite, Dhan, or Fyers). | `pivot/backend/routers/brokers.py` |

## Next steps

We've built a dashboard and 5 insights to track core user behavior:

- **Dashboard:** https://us.posthog.com/project/498532/dashboard/1801033
- New User Signups (email + Google): https://us.posthog.com/project/498532/insights/OqVsndE3
- Daily Active Chat Users: https://us.posthog.com/project/498532/insights/8NSeYvKF
- Signup → First Chat Funnel: https://us.posthog.com/project/498532/insights/PmKJCTP1
- Workflow Created vs Activated: https://us.posthog.com/project/498532/insights/8vJF4nBs
- Order Registrations: https://us.posthog.com/project/498532/insights/v7aEodfC

## Verify before merging

- [ ] Run a full production build (the wizard only verified the files it touched) and fix any lint or type errors introduced by the generated code.
- [ ] Run the test suite — call sites that were rewritten or instrumented may need updated mocks or fixtures.
- [ ] Install the `posthog` package: `pip install posthog` (the sandbox could not reach PyPI during this run). Add it to your virtual environment and confirm `posthog>=3.0.0` appears in `requirements.txt` (already added).
- [ ] Add `POSTHOG_PROJECT_TOKEN` and `POSTHOG_HOST` to `.env.example` and any onboarding scripts so collaborators know what to set.
- [ ] Confirm the returning-visitor path also calls `identify` — sessions that begin with a token refresh (`/auth/refresh`) rather than a fresh login will not re-identify. Consider calling `identify` in a middleware or the `/auth/me` endpoint for complete coverage.

### Agent skill

We've left an agent skill folder in your project at `.claude/skills/integration-fastapi/`. You can use this context for further agent development when using Claude Code. This will help ensure the model provides the most up-to-date approaches for integrating PostHog.
