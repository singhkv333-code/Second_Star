# Pivot launch-readiness review — 14 October 2026

**Decision: NO-GO for an unrestricted public or paid launch today.** Keep the landing page/waitlist available. A limited, supervised beta is a separate decision, after the critical security and data-use gates below are closed. Calling something a beta does not waive data licences or account security.

This is an engineering and operational review, not a legal opinion or a completed penetration test. It combines live HTTP checks of app.pivotnow.in and www.pivotnow.in, read-only Azure infrastructure inspection, current source review, targeted local tests, and official provider/regulatory references. No payments, production signups, market-stream controls, deployments, credential changes or customer-data edits were performed. Local baseline: commit `9dff2f2f` plus existing uncommitted changes; passing local tests do not establish the deployed build is identical.

Evidence labels: **LIVE** = observed during this review; **CODE** = current source; **DOCUMENTED** = prior deployment record, not independently re-proven; **UNVERIFIED** = clearance evidence is still required. Absence of evidence is not a claim that a feature or control does not exist.

## 1. Release-blocking findings

### B1. Public control of shared market streams and replay — critical security gate

**CODE:** `charto/data/dataserver.py:19546` dispatches `/live` to `_live_stream()` without an authentication/admin check except for the separate read-only status branch. `_live_stream()` can start or stop a process-wide driver. `/replay` at line 19557 invokes shared symbol replay state without a user/admin gate. Replay changes the shared forming bar and horizon. These are state-changing GET operations.

**LIVE:** Anonymous `/live?status=1` is reachable. Live nginx inspection shows rate limits but no `auth_request` protection in the inspected configuration. I deliberately did not issue start/stop/replay requests against production. This is a source-confirmed unsafe control path, not a claim that I disrupted a production stream.

**Impact:** A visitor could disrupt a venue feed or change shared chart state if these branches are served unchanged. Rate limiting does not provide authorization.

**Clearance:** Make shared ingestion controls operator-only and use authenticated POSTs with explicit authorization. Isolate user replay from production/live state, or remove public replay until isolated. Test anonymous and ordinary-user rejection, privileged operation, cross-user replay isolation, and no effects on alerts/paper execution. Owner: backend/security. Applies before any external beta.

### B2. Unverified email is trusted across account stores — critical identity gate

**CODE:** Charto signup (`dataserver.py:16946`) accepts an email/password and immediately creates a session without email ownership verification. The bridge `pivot/backend/auth/charto_session.py:143` maps that email to an existing Pivot account; for a new linked row it sets `is_verified=True` with the incorrect assumption that Charto verified the email. This fixes integer-ID collisions but leaves email ownership unproven.

**Impact:** Where a Pivot-side account email is not already reserved in Charto, registering that email can link to the existing Pivot identity. An admin identity can be implicated if configured that way. This is conditional on the account population; I did not attempt to impersonate a real user. Charto's separate email-based admin allowlist and future Google account linking also need the same ownership proof.

**Clearance:** Require verified email before linking existing accounts or granting privileged access. Use explicit, durable identity links; never mark an account verified solely because password signup succeeded. Audit existing links and reserve/verify administrator identities. Add two-account tests including colliding numeric IDs, matching unverified emails, inactive accounts and Google/password linking. Owner: backend/security. Google sign-in can remain disabled; that does not solve the existing bridge.

### B3. PostgreSQL is exposed to all public IP addresses

**LIVE:** Azure firewall rule `allow-all-dev` on `pivot-db-india` still allows `0.0.0.0` through `255.255.255.255`. This is network exposure, not proof that the database is unauthenticated or breached.

**DOCUMENTED:** The October cutover recorded old shared PostgreSQL admin credentials, an older model key and a browser signing key awaiting revocation. I have not proved those old credentials were revoked. A Razorpay secret was also previously pasted into chat; treat it as exposed even though it was a test credential.

**Clearance:** Inventory legitimate VM, Vercel, ingestion and operator clients before narrowing access. Use private networking or controlled egress/allowlists, least-privilege per-service roles and TLS. Rotate exposed/shared credentials and revoke old versions only after updating known dependants; verify old credentials fail. Review access logs and cloud/operator MFA. Do not close the firewall blindly and break the Vercel waitlist or ingestion. Owner: infrastructure/security.

### B4. Current market data is not ready for a live-market launch

**LIVE:** `/live?status=1` returned `streams: {}`. The last RELIANCE one-minute candle returned by the public API is **4 September 2026, 15:28 IST**. The daily result also ends on 4 September. This is roughly 40 calendar days behind the review date. This is a sample finding, not a measurement of every instrument.

The same deployment returned `/health?deep=1` as `ready: true`. Its market DB check establishes availability, not exchange-session freshness. One armed paper strategy was reported, with `watching: 0`; that needs an execution/watchdog check rather than an assumption that the strategy is running.

**Clearance:** First settle B5's data rights. Then restore an authorized feed, backfill gaps, verify session calendars/timezones, corporate actions, indices and supported asset families, and demonstrate an end-to-end market-session tick → candle → chart → alert/paper event. Add freshness/source fields and visible stale/delayed/offline labels. Alerts and strategies must fail safely on stale prices. Monitor token expiry, reconnects, gap fill and end-of-day sync separately from uptime. Owner: data/backend.

### B5. Data licensing and product scope require written clearance

**CODE/DOCUMENTED:** Kite is the primary feed, fundamentals come from Moneycontrol, and Pivot includes simulated trading and downloadable financial reports. No commercial redistribution agreement was established by this review.

The official [Kite terms](https://kite.trade/terms/) prohibit public-at-large display of live Kite data and also restrict use of the APIs/live data for virtual/mock trading apps. That is directly relevant to Pivot's proposed public charts and paper book. A paid API subscription or a disclaimer is not evidence of redistribution or simulation rights.

**Clearance:** Obtain written rights covering the actual product: public/authenticated display, historical storage, derived analytics, AI processing, paper simulation and CSV/Excel exports, for each provider/exchange as applicable. Alternatively use a suitably licensed feed and remove unsupported uses. Review Moneycontrol, fallback providers, news, logos and vendored chart-library attribution too. Current financial-export wording correctly acknowledges third-party rights; retain that distinction. Do not claim all underlying data belongs to Pivot.

Separately, have Indian securities counsel classify the actual AI research, strategy and portfolio outputs and marketing against IA/RA requirements. A “not financial advice” footer does not determine regulatory classification. [SEBI's investor guidance](https://investor.sebi.gov.in/investment_advisor.html) is a starting point, not a legal clearance. Owner: founder/legal/data partnerships. This gate applies to free public distribution too.

### B6. Paid plans are not operational on the live platform

**LIVE:** `/billing/plans` advertises Free, Pro and Pro+ but returns `checkout: false`.

**CODE/DOCUMENTED:** The Vercel Standard Checkout is a separate one-time payment flow; it does not grant a plan or durably fulfil a purchase. The Charto subscription engine requires its own keys, plan IDs and webhook configuration. Earlier checkout verification only created a test order/opened the modal; no complete paid lifecycle was established.

**Clearance:** Choose one clear purchase journey tied to an authenticated account and a server-side product/price. Configure the intended test and live environments, provider plans, capture/renewal handling and webhooks. Prove successful payment → correct entitlement → invoice/confirmation; also cancellation, failed/authorized-only payment, retry, duplicate click, closed browser, renewal, downgrade, refund/dispute and provider outage. Reconcile provider charges against local access. Remove or clearly label the unrelated arbitrary-amount checkout. Verify tax treatment/business details and explicit refund/cancellation terms before charging. Owner: payments/product. Blocks paid launch; a genuinely free beta can disable purchase surfaces.

### B7. Billing webhook recovery can lose a valid event

**CODE:** `entitlements.record_event()` at line 615 catches **all** insert exceptions and returns `False`. `billing.api_webhook()` at line 402 treats `False` as a duplicate and returns success. A database error can therefore be acknowledged as if already processed.

The event is committed before `_apply()`. If the process fails after recording but before applying, redelivery is ignored. A caught application error also returns 200. `_apply()` rejects older billing periods but does not by itself resolve all conflicting events within the same period. Checkout also needs a concurrent/repeated-subscription creation test.

**Clearance:** Distinguish duplicate-key errors from storage failures. Persist a recoverable pending/processed/failed state, make application transactional or safely retryable, reconcile provider state, and acknowledge only a durably accepted/recoverable event. Test crash boundaries, DB failure, out-of-order same-cycle events and reconciliation. Razorpay documents [at-least-once delivery, retries and non-guaranteed ordering](https://razorpay.com/docs/webhooks/best-practices/); deduplication alone is insufficient. Owner: payments/backend. Blocks paid launch.

### B8. Account recovery, session revocation and authentication abuse need closure

**CODE:** The Next client calls `/auth/forgot-password`; I did not find its Charto implementation. An anonymous GET returns 404, which alone does not test the POST contract, so complete the actual user journey in staging before declaring it supported. Current password hashing is scrypt N=2^14/r=8/p=1. This is below the current [OWASP recommendation](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html); benchmark a stronger configuration outside the global database lock.

Sessions are 30-day bearer tokens stored in localStorage and plaintext session rows. LocalStorage is not by itself proof of a vulnerability, but it increases the consequences of XSS. `jwt_handler.get_user_id_from_token()` accepts JWTs without checking revocation; the central `require_user` dependency does check it. Audit legacy routers that use the helper directly so logout has consistent meaning.

**LIVE/CODE:** nginx gives `/auth/login|signup|reset` the tight authentication limit. Charto also aliases `/api/auth/*`; parity of the tighter limit is not shown in the inspected configuration. Avoid bypasses through aliases or alternate API prefixes. Account creation without email verification also makes account farming/AI-credit abuse cheaper.

**Clearance:** Working recovery with short-lived single-use tokens, neutral responses and session invalidation; logout/revocation across all services; verified email before privileged/linking actions; consistent per-IP and per-account throttles for every alias; password length limits and a safe hash migration. Document an identity-checked manual recovery process if self-service is deliberately deferred for a small beta. Owner: backend/security.

## 2. Other essential launch gates

| Area | Current evidence / gap | What must be demonstrated |
|---|---|---|
| Dependency security | Production dependency audit of local pivot-next reports 15 high, 9 moderate, 2 low, 0 critical findings. This is an advisory/path count, not 26 proven exploits. Includes ECharts XSS and several transitive build/parser issues. | Triage reachability against the deployed lockfile, patch or document justified acceptance. Scan charto/web, Python, OS and vendored code as well; those inventories were not fully audited here. |
| Browser security | Main shell and landing return anti-framing, nosniff and other headers. Their CSP lacks script-src/default-src restrictions. `/chart-app/` did not return CSP in the sample. Chat markdown escapes source, and iframe messages check origin/source—positive controls. | Review all rendered model/third-party content and URL schemes for XSS; protect chart document too, with frame-ancestors allowing the same-origin shell. Roll out an appropriate tested CSP. Do not apply DENY blindly to the chart iframe. |
| Privacy operations | PostHog config is live, points to US ingestion; client identify includes email/name. Code uses defaults without explicit replay masking settings. Policy discusses analytics, storage, providers and requests via email. | Verify dashboard replay/autocapture settings, exclude sensitive prompts/tokens/payment fields, define purposes/retention and provider contracts, test deletion/export across SQLite, PG, analytics, logs and backups, name a responsible contact. A mailbox alone is not a completed deletion process. |
| Regulatory incident response | No exercised incident playbook or contact ownership was established. | Counsel should map applicable CERT-In reporting/log obligations; prepare a contact, evidence preservation and response process. [Official directions](https://www.cert-in.org.in/PDF/CERT-In_Directions_70B_28.04.2022.pdf) include reporting and logging requirements for covered entities. Do not log secrets to satisfy retention. |
| DPDP readiness | Published policy is a useful starting point, but operational proof is missing. | Map phased commencement and applicable obligations with counsel. The [official 2025 Rules, rule 1](https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf) stagger commencement; do not incorrectly claim every provision is already effective in October 2026. Build notices, consent/withdrawal where applicable, retention, rights handling and breach response now. |
| Backups and recovery | LIVE backup was fresh, ~34 minutes old at first check; hourly backup and certificate timers are active. Historical cutover restore exists. Restore script checks a subset of durable tables. | Restore the full current user state—including billing, usage, paper orders/fills, strategies and alerts—on an isolated host. Verify PG PITR and market-data recovery too. Set explicit RPO/RTO, protected backup access/retention and deletion handling. A fresh blob is not a full recovery drill. |
| Capacity | Data volume is 86% used, ~34 GiB available; health threshold is 10% free. Services listen on loopback behind nginx; current serving NSG restricts Internet access to HTTP/HTTPS. | Forecast ingestion/index/temp/backup growth and alert before the threshold. Load-test staging with charts + SSE + chat + SQLite writes at the intended launch cohort, including slow clients and reconnects. Define admission caps and latency/error targets. Single-VM operation can suit a bounded beta if recovery is rehearsed; multi-region is not required on day one. |
| Deployment integrity | Vercel and Azure are separate release surfaces; app nginx is a generated copy of another vhost. Existing local changes are uncommitted. CI inspected runs backend test collection, not execution, and frontend typecheck/lint. | Pin immutable release manifests and matching assets/config; execute security/billing/data tests, build both Next apps, check routes on both domains, migrate safely, and rehearse rollback without reverting customer data. Prevent stale vhost copies and old-VM duplicate writers. |
| Monitoring | Health checks, concurrency counters, backup checks and PostHog hooks exist. | Prove alerts reach a named operator for stale feed, dead stream, backup failure, disk pressure, DB locks, auth spikes, model failures/spend, webhook backlog and certificate expiry. Product analytics is not an on-call system. |
| Support and mail | info@pivotnow.in is published; delivery and support workflow were not verified. | Test receiving/responding, signup/recovery/payment/alert mail, SPF/DKIM/DMARC and bounces. Draft templates do not establish delivery. Publish realistic support, refund and cancellation commitments. |

## 3. Product correctness and AI safety release checklist

These are required acceptance checks, not claims that every item currently fails:

- **Source truth:** prices carry source, venue, currency, timezone and as-of timestamp. Null/unsupported data stays unavailable; no fallback masquerading as Kite. Index aliases and symbol search resolve consistently. Sample NSE/BSE/MCX/crypto coverage separately; do not advertise an instrument count as equivalent live coverage.
- **Charts:** resizing, maximize/restore, hidden tab return, multiple widgets, mobile rotation and high-DPI rendering. Comparisons preserve chart type, normalize from a common valid baseline, label percentage versus raw price, and handle mismatched calendars without invented bars. Current unit regressions pass; a browser/device acceptance run is still needed.
- **Financials/exports:** all stored periods, explicit consolidated/standalone basis, stable units, numeric cells, missing values distinct from zero, correct statement dates and restatements; preserve provenance internally and third-party rights. Test spreadsheet formula injection protection and downloaded workbook readability. Last local export changes are not deployment proof.
- **Patterns/backtests:** stock-specific populations, maximum valid available history, no look-ahead, next-bar fills, splits/dividends, delisted/survivorship effects, realistic fees/slippage, sample sizes and control rates. Verification-ladder results must constrain claims and arming. Do not present an attractive win rate as validated edge.
- **Paper execution:** both Charto and FastAPI global live switches remain off; normal users cannot override them. Verify idempotent fills, restart recovery, paused/cancelled strategies, stale-data refusal, positions/P&L arithmetic and alert deduplication. User-facing broker “live” controls should be hidden/disabled for this paper-only release even if the global switch blocks execution.
- **Tenant isolation:** exercise two users against conversations, shared links, layouts, drawings, exports, alerts, broker connections, journal, strategies, billing and API/websocket channels. Guessing another user's ID must not expose or mutate state. Shared-link revocation and logout must work across shell and chart.
- **Model authority:** retrieval content is untrusted; prompts cannot grant a plan, change identity, reveal secrets or bypass simulation. Mutating tools enforce ownership and approvals in code. Limit tool loops, tokens, uploads, response size and costs; meter every model route and service-to-service path. Voice transcription also needs a cost/abuse budget.
- **Output quality:** run one instrumented multi-turn staging acceptance session covering stale quotes, ambiguous instrument, unavailable data, drawing, fundamentals, comparison, strategy/backtest, paper action and adversarial instructions. Record tokens, latency and correctness/quality per item. This review did not run paid model calls and does not certify current answer quality.
- **Claims and pricing:** every sold feature must exist on the advertised plan. Live catalog marks several features pending (including some alert types, custom timeframes/history/ad-free metadata); reconcile that catalog with actual implementation and pricing copy. Explain monthly credit resets and limits. Do not sell a pending feature as delivered.
- **Accessibility and essentials:** keyboard navigation/focus, contrast across custom themes, screen-reader labels, error states, cancellation/loading/offline states, empty portfolio, one-asset allocation and mobile usability. More themes and decorative polish are not blockers once the basic experience works.

## 4. What is already in place

- Valid HTTPS and landing/app routing worked in sampled requests; HTTP/certificate renewal was documented on October 13 and the certbot timer is active.
- Main services answer health checks; PG and Redis checks pass. FastAPI's `mock_mode.openai=true` flag is based on absence of an OpenAI key; Azure is configured, so this flag alone does **not** establish fake AI responses.
- Serving application ports are bound to loopback. The public PG firewall is the separate exception identified above. A separate successful process-environment check confirmed `CHARTO_LIVE_ORDERS` is unset (code defaults off) and `LIVE_EXECUTION_ENABLED=false` for Charto and Pivot API; both run with `APP_ENV=production`.
- Fresh online SQLite backups exist; the state plane is understood and a prior cutover preserved it.
- Payment HMAC validation, ownership checks, server-side quotas, failure refunds and several deduplication paths exist. They are useful foundations, not a substitute for payment recovery.
- Legal pages acknowledge simulation, AI/data limits, third-party rights and a support contact. The export does not claim blanket ownership of third-party data.
- Same-origin shell/chart session sharing and origin-checked iframe messages exist. No need to rewrite the whole platform to fix these gates.

## 5. Checks actually run

| Check | Result | Limit |
|---|---|---|
| Public health, billing catalog, auth metadata, analytics config, shell/landing/chart headers, live status, small bars samples | Completed | Read-only; no authenticated customer actions or stream changes |
| Azure PG firewall, serving NSG, listener/disk inspection | Completed | Not a full cloud IAM/secret-rotation audit |
| `test_entitlements_api.py` + `test_billing_manage.py` | **44 passed**, 25.66s | Temp user DB, fake payment/model wire; not provider end-to-end |
| `test_production_readiness.py` | **25 passed**, 0.78s | Explicit disposable user DB; no full production restore/load test |
| Chart size/comparison/symbol restore/widget migration Node tests | **37 passed**, ~0.11s | Unit tests; no new browser acceptance run |
| `pnpm audit --prod --json`, pivot-next | **15 high / 9 moderate / 2 low / 0 critical** | Local dependency tree, reachability unproven |

Total: **106 targeted tests passed**. No claim of full-suite, penetration-test, end-to-end billing, fresh-market-session or legal clearance.

Artifacts: `2026-10-14-dependency-audit.json`, `2026-10-14-chart-tests.txt` beside this report. One broad VM inspection exceeded Azure's output window and ended with a read-only script terminator error; incomplete environment output was discarded; a subsequent smaller read-only check successfully verified the live-order flags listed above. No production application configuration was changed.

## 6. Order of work and release decision

1. **Contain and prove security:** B1 shared controls, B2 identity trust, B3 network/credential closure, B8 auth consistency; triage exploitable dependency findings. Preserve rollback and customer data.
2. **Settle rights and recover trustworthy data:** B5 written permissions/product classification, then B4 feed restoration and visible stale-state handling. These can proceed alongside security work.
3. **Make the paid lifecycle reliable:** B6 configuration/product mapping and B7 durable event processing; complete sandbox lifecycle tests and then an explicitly authorized controlled live payment/refund check.
4. **Exercise operations:** full restore, staging load, operator alerts, support/mail, privacy requests, deployment/rollback and the two-user security suite.
5. **Run a capped pilot:** invite-only, clear scope and coverage, no live orders, no unproven paid features. Use actual latency/error/freshness/cost evidence to decide expansion.

**Free beta:** can defer paid features by disabling them, and can defer Google sign-in and cosmetic additions. Cannot defer account isolation, dangerous public controls, data-use permissions or honest freshness labels.

**Paid public launch:** requires all B1–B8 cleared or a specific feature removed, plus recovery, monitoring, privacy/support and paid lifecycle evidence. Attach an owner, evidence link and review date to each clearance. Do not use a percentage-ready score: a single unresolved critical gate is enough to stop launch.
