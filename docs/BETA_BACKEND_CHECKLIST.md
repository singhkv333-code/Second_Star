# Pivot — Beta backend readiness checklist

What every beta user needs server-side, what shipped this round, and what's
still open before/at launch. Status as of 2026-06-21.

## ✅ Done this round

### Auth & accounts
- `User` model (bcrypt) + JWT (python-jose HS256, 12h access / 7d refresh).
- Endpoints: `POST /auth/register`, `POST /auth/login`, `GET /auth/me`,
  `POST /auth/refresh` (rotates the refresh token), `POST /auth/logout`
  (revokes the current token), `POST /auth/request-verify`,
  `POST /auth/verify-email`, `POST /auth/forgot-password`,
  `POST /auth/reset-password`, `GET|PATCH /auth/settings`.
- **Credential verification:** password policy (≥8 chars + a letter + a digit),
  email normalised (trim+lowercase); uniform `401 invalid email or password`
  (no account enumeration); `is_active` enforced.
- **Brute-force protection:** Redis per-(email+ip) failure counter — 5 fails /
  15 min → 15-min lockout, `429` + `Retry-After`.
- **Logout / revocation:** access tokens carry a `jti`; `POST /auth/logout`
  adds it to a Redis revocation list (TTL = token lifetime) checked in
  `require_user`. Legacy tokens (no jti) still validate.
- **Email verify + password reset:** full flow built; **send is deferred**
  (`EMAIL_ENABLED=false` logs the link). Token *hashes* only are stored
  (`email_verification_tokens`, `password_reset_tokens`); single-use + expiry.
- **Audit:** `auth_audit` row on signup / login / login_failed / refresh /
  logout (best-effort).

### Multi-tenant isolation (User A cannot reach User B)
- **Chat-store leak fixed:** a client-supplied `conversation_id` is now
  namespaced under the authed user (`u{id}::{conv}`) in `chat.py::_conv_id`, so
  a forged id can't address another user's history / pending tool calls /
  drafts. Verified: A forging `u2` → `u1::u2`.
- **Dev auth bypass closed:** the old `/chat` "no token ⇒ user 1" fallback is
  gated behind `dev_auth_bypass` (default **false**). Beta must keep it false.
- **Router scoping audited:** conversations, orders, portfolio, paper,
  workflows, runs, approvals, SIP, options, IPO all filter by `user_id` and
  return 404 (not 403) on cross-user ids. Verified live: Alice→Bob's
  conversation / messages / summary all 404.

### Per-user data (migration `0022_user_auth_beta`, applied to Postgres)
- `conversation_summaries` — rolling LLM chat-history summary
  (`services/conversation_summary.py`; `GET /api/conversations/{id}/summary`),
  ownership-gated.
- `user_settings` — per-user preference JSON (`/auth/settings`).
- `auth_audit`, `email_verification_tokens`, `password_reset_tokens`.
- Existing per-user data already persisted: chat (`conversations` +
  `conversation_messages`), live trades (`trade_logs`), full paper book
  (`paper_accounts/orders/fills/positions/ledger/nav`), workflows, SIPs, option
  strategies, IPO applications, broker sessions (encrypted), LLM usage.

### Paper-mode trade → portfolio reflection
- Verified end-to-end: a paper BUY → `paper_fills` → `paper_positions` upsert →
  `paper_ledger` debit → `portfolio.holdings()` + `account_summary()` reflect
  it (cash decremented, position shown). Source of truth = `paper_fills`.

### Bonus hardening
- `RequestValidationError` handler now `jsonable_encoder`s errors → a
  body-validator `ValueError` returns **422, not 500** (was app-wide latent).

## ⚙️ Required configuration before launch (env / secrets)

| Setting | Why | Beta value |
|---|---|---|
| `JWT_SECRET_KEY` | signs all tokens — **must be a strong random secret**, never the dev blank | set per-env secret |
| `DEV_AUTH_BYPASS` | the `/chat` no-token fallback | **false** |
| `KITE_TOKEN_ENC_KEY` / broker token enc key | Fernet-encrypt broker creds at rest (else plaintext + warning) | set a real key |
| `ALLOWED_ORIGINS` | CORS — currently dev URLs (`localhost:3000/5173`) | set the real beta web origin(s) |
| `EMAIL_ENABLED` + provider creds | flip verify/reset emails on | false until a provider is wired |
| `REDIS_URL` | rate-limit + revocation + chat store rely on Redis (MockRedis fallback is per-process only) | point at a real Redis |
| `DATABASE_URL` | primary Postgres (migrations through `0022`) | beta Postgres |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 720 (12h) is long for prod | consider 30–60 + refresh |

## 🔲 Open before / at beta (recommended)

1. **Email provider** — wire SMTP/Resend/SendGrid and set `EMAIL_ENABLED=true`
   to turn on verify + password-reset (tables + flow already exist).
2. **HTTPS + secure token storage** — serve over TLS; consider httpOnly cookies
   for the refresh token instead of `localStorage` (XSS exposure). FE currently
   stores `pivot_jwt` + `pivot_refresh` in localStorage.
3. **Redis in prod** — rate-limit/revocation/chat-store degrade to per-process
   MockRedis without it; a multi-worker deploy needs a shared Redis.
4. **Verified-email gate (optional)** — `is_verified` is tracked but not
   enforced at login; decide if beta requires it.
5. **Rotate-on-refresh persistence** — FE must save the new `refresh_token`
   returned by `/auth/refresh` (it rotates) — already wired; keep in mind.
6. **Observability** — add login/lockout dashboards off `auth_audit`; Sentry
   DSN is supported (`sentry_dsn`) but unset.
7. **Known product gap (not auth):** the chat `build_strategy` path still
   doesn't force the indicator-timeframe ask on every route (documented
   separately) — orthogonal to beta auth.
8. **Account management** — no self-serve account deactivation / data export
   endpoint yet (GDPR-style); add if needed for beta.

## Verification performed (this round)
- HTTP integration on `:8000`: weak-pw→422; signup→201; wrong-pw→401;
  rate-limit 5→429; `/auth/me`; refresh (rotated); logout→revoked token 401;
  `/chat` no-auth→401; **isolation** (A→B's data 404); settings GET/PATCH.
- Paper reflection (fills/positions/ledger/holdings) on a fresh user.
- Chat-summary generated + persisted + isolation-gated.
- Login + signup pages render (HTTP 200, branded) on `:3000`; `tsc` clean.
- Backend suite: 649 passed in the auth/chat/conversation/workflow slice
  (failures limited to pre-existing date/network-mock tests).
