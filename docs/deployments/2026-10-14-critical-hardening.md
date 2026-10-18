# Critical launch hardening — 14 October 2026

## Production result

Applied to `pivot-clean-stage`, serving `app.pivotnow.in` and the existing Azure hostname. Changes were limited to the shared control handlers, identity bridge/auth router, nginx waitlist route and PostgreSQL schema/firewall. Existing unrelated workspace changes were not deployed.

### Shared feeds and replay

- `GET /live?status=1` stays read-only/public.
- Control GETs return 405. Control POSTs require `X-Pivot-Feed-Key` matching server-only `CHARTO_FEED_CONTROL_KEY`; ordinary sessions/admin email matching cannot authorize them. Missing configuration denies everyone.
- `/replay` GET and POST return 403. Shared replay remains disabled until isolated from production data. Normal candle reads and chart streams remain available.
- An operator key was generated on the VM in the protected runtime environment. It was not printed or added to Git. Google sign-in remains disabled by default; re-enabling it requires explicit `CHARTO_GOOGLE_SIGNIN_ENABLED` configuration and a separate safe identity-linking design.

### Account identity

New `public.charto_account_links` binds immutable Charto user IDs to distinct Pivot IDs, with unique constraints and a foreign key. The bridge creates a new password-disabled Pivot identity only if no independently existing email account collides, and never marks password signup as email-verified. Existing bindings require an active Pivot account. Missing schema/configuration fails closed.

`POST /pv/auth/link-charto` requires a genuine, non-revoked Pivot JWT in `Authorization: Bearer …` and a valid Charto session in `X-Charto-Token`. Both accounts must have matching emails, and conflicting bindings are rejected. The server validates both credentials; an email alone never authorizes linking.

**User-visible migration boundary:** seven current Charto accounts have an independently existing Pivot account with the same email. They are not automatically linked; owners need one-time proof of both logins. Their Charto accounts, sessions and workspace state remain intact. No existing account was auto-approved or reassigned. This endpoint is currently an API flow, not a new frontend wizard. Ten current identities had no Pivot email collision and can create their own binding on first use. No matching password-disabled legacy bridge record was found to migrate.

### PostgreSQL network

Removed `allow-all-dev` (0.0.0.0–255.255.255.255). Remaining rules permit seven individual IPv4 addresses:

| Purpose | Address |
|---|---|
| Serving VM | 20.204.109.233 |
| Kite ingestion VM | 20.197.56.208 |
| Sentinel ingestion VM | 4.240.1.19 |
| Current operator egress | 14.139.45.241 |
| Observed operator connections | 14.139.45.243 |
| Previously allowed operator address | 183.83.156.96 |
| Previously allowed operator address | 183.83.152.89 |

The owner identified localhost as the only other known direct client. The two pre-existing narrow operator rules were preserved. New addresses require a deliberate firewall update; there is no catch-all or Azure-wide exception. PostgreSQL still uses a public endpoint restricted by firewall; this is not a private-endpoint migration.

Vercel project `pivotnow` now has a project-level rewrite for exactly `/api/waitlist` to `https://app.pivotnow.in/api/landing/waitlist`. This required no landing-page redeployment. Route ID: `5d17ed5f-55f8-4cdf-9f3f-da8495991116`; published route version: `8351ac4c-3e8c-4d5d-96a4-a74ec45aeccc`.

The Azure route proxies the existing Next waitlist handler on :5175. It accepts POST only, limits request size to 12 KiB and uses the authentication rate-limit zone. `pivotserve_20261008` received scoped access to the landing schema/table/sequences, so the request uses the existing serving credential. Vercel no longer needs direct database connectivity for this path. Existing Vercel database credentials were not removed/rotated in this change.

**Still open:** coordinated rotation/revocation of older shared PostgreSQL credentials. Network restriction does not prove an old credential is revoked. This change did not rotate the shared admin password or disable unidentified consumers. Model/browser/Razorpay credential rotation is also outside these three fixes.

## Verification

- Feed-control GET → 405; anonymous control POST → 403; replay GET/POST → 403; live status → 200.
- Linking without a genuine Pivot JWT → 401; local tests cover email collision, active/inactive binding, missing schema, unverified new identities and possession of both accounts.
- Security tests: 3 shared-control tests and 6 identity tests passed. Including related billing/entitlement regression coverage: 53 tests passed (47 + 6). The Unicode operator-header rejection was additionally rerun in the 3 security tests.
- nginx configuration and changed Python files compiled successfully; Charto and Pivot API restarted and passed health checks; backup remained fresh.
- A fresh PostgreSQL connection from the serving VM succeeded after hardening.
- Synthetic waitlist submissions through both Azure and Vercel returned 200 and were confirmed in PostgreSQL. Every synthetic row was removed. The Vercel test was repeated after removal of the broad firewall rule and still passed.
- Firewall readback confirmed only the seven single-address rules above.

## Release persistence and rollback

Current release directory: `/opt/pivot/releases/8ab4474f3ae7b932`. Original files/config are backed up under `/var/lib/pivot-release/security-20261014/`; `paths.json` records the mapping. The operator-header comparison was subsequently amended to compare bytes and checked again. The original backups still cover the complete pre-change state.

`pivot-release.timer` is temporarily stopped to keep an unreviewed release from overwriting the security patch. Running application services continue normally. Integrate the reviewed security commit into `production-live`, verify the release plan does not deploy unrelated changes, then resume the timer. Repository instructions require approval before any Git push; no push was performed.

Rollback selectively: restore affected application/nginx files from the backup and reload/restart only the relevant services. Retain the additive identity table and existing customer data; do not restore an older whole database. Revert the Vercel route through route disable/publish if needed. If a legitimate database client was omitted, add its specific address after verification rather than recreating the all-IP rule. Never re-open dangerous public feed/replay controls as a routine rollback.

## Files

- `charto/data/dataserver.py` (only security hunks)
- `charto/data/test_feed_security.py`
- `charto/deploy/nginx-charto.conf`
- `charto/deploy/harden_launch_controls.py`
- `pivot/backend/auth/charto_session.py`
- `pivot/backend/auth/router.py`
- `pivot/backend/tests/test_charto_identity_binding.py`
- `pivot/migrations/security/20261014_charto_account_links.sql`
- this deployment record

## Reviewed automated-release candidate

An isolated worktree is based on `origin/production-live` (`ea8fa934`), with only the security commit cherry-picked. SHA-256 comparison confirmed all three candidate runtime files exactly match the hardened VM files:

- `charto/data/dataserver.py`: `1ec1daaf67b99d15ee8718158d81b20a7d55f1c165e02d39e675d2cf3c2acb4f`
- `pivot/backend/auth/charto_session.py`: `41a24e8d0e9f90b08695647b289f89d7a2f3b00d508eb14b534e304315c6071b`
- `pivot/backend/auth/router.py`: `f46e680fc807a92a8b4d3ff358fad3850c7d3fca9c20e354e052a9266014dac9`

The isolated candidate's three shared-control tests passed as well. A private compressed source capture is retained at `pivotmarketdata / pivot-clean-backups / release/security-20261014-source.json.gz`; no credentials were included. The candidate can fast-forward `production-live` after approval, then the automatic release timer can resume. No branch was pushed.
