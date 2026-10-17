BEGIN;
CREATE TABLE IF NOT EXISTS public.charto_account_links (
    charto_user_id BIGINT PRIMARY KEY CHECK (charto_user_id > 0),
    pivot_user_id BIGINT NOT NULL UNIQUE REFERENCES public.users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pivotserve_20261008') THEN
        GRANT SELECT, INSERT ON public.charto_account_links TO pivotserve_20261008;
    END IF;
END $$;
COMMIT;
