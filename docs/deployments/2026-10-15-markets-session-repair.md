# Markets session repair — 15 October 2026

## Cause and production change

Sign-in and `/auth/me` succeeded, but `/pv/api/markets/explore` returned
503 `auth_unavailable`. The account bridge's `_bind` replaces stale account
links with a transactional DELETE/INSERT. The serving role had SELECT and
INSERT on `public.charto_account_links`, but no DELETE. PostgreSQL checks
permission even when that DELETE matches zero rows.

Applied `api/migrations/security/20261015_account_link_replacement_permission.sql`:
DELETE on that single mapping table for `pivotserve_20261008`. No account rows
were manually edited; session credentials, authentication checks and other
table privileges were unchanged.
No service restart was needed.

The owner approved a temporary firewall exception for this operator's current
public IP. The single-IP rule `temporary-markets-repair-20261015` was removed
after applying the grant, and its absence was independently checked. Existing
firewall rules were preserved.

## Verification

- Real dedicated test-account login: HTTP 200; `/auth/me`: HTTP 200.
- Authenticated `/pv/api/markets/explore`: HTTP 200 in 0.24s, 500 stocks and
  21 sectors. Data explicitly reports EOD 2026-10-15, not live prices.
- Browser: Markets brief, breadth, sectors and lists render instead of the
  session-check error.
- Account tests: 9 passed. Markets calculation/navigation tests: 12 passed.
  Frontend TypeScript check passed.

## Local UI change, not deployed

Asset-class selection remains segmented. The lower section navigation uses
plain text, bold active state, a 24px gap and `aria-current="location"`.
The browser design preview used temporary CSS against the working Markets
page; that preview is not a production frontend deployment. Source changes
remain local pending push/deployment authorization.

## Rollback

If required, revoke only the added grant:

```sql
REVOKE DELETE ON public.charto_account_links FROM pivotserve_20261008;
```

This restores the previous privilege set but reintroduces the account-mapping
failure for accounts needing a mapping. Do not restore an old database or
remove account links. The separate local laptop connection issue was an
outdated firewall IP; its access was not left permanently expanded.
