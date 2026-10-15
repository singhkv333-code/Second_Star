-- Account mapping replaces stale links in one transaction (accounts._bind).
-- PostgreSQL requires DELETE permission even when the statement matches zero
-- rows. SELECT/INSERT alone made genuine signed-in sessions return HTTP 503.
-- Narrow fix: no new access to users, sessions, schemas or other tables.
BEGIN;
GRANT DELETE ON public.charto_account_links TO pivotserve_20261008;
COMMIT;

-- Rollback (reintroduces the mapping failure):
-- REVOKE DELETE ON public.charto_account_links FROM pivotserve_20261008;
