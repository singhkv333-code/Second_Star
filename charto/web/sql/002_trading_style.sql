-- The optional last question: how a person actually trades, in their own words.
--
-- Nullable and unconstrained on purpose. It is the only field on the form that
-- is not required, and it is free text because the four experience bands above
-- it already carry the part that can be enumerated — this one exists precisely
-- for what those bands cannot hold ("mostly Bank Nifty expiry days", "I only
-- swing trade smallcaps"). A CHECK constraint on prose would be a constraint on
-- the answer we asked for.
--
-- 2,000 characters is a paragraph or two, matched by the API's own cap. The
-- column is TEXT rather than VARCHAR(2000) so raising that cap is a change in
-- one place instead of two.

BEGIN;

ALTER TABLE charto_landing.waitlist_registrations
  ADD COLUMN IF NOT EXISTS trading_style TEXT;

COMMENT ON COLUMN charto_landing.waitlist_registrations.trading_style IS
  'Optional free-text answer: how the person trades, in their own words. NULL when skipped.';

COMMIT;
