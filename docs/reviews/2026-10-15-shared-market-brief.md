# Shared market brief — local implementation and verification

Not deployed. The production brief remains unchanged.

## Design

- Dedicated system prompt: `api/backend/prompts/modules/market_brief.md`.
- Five or six short labelled bullets covering India, Global, Crypto,
  Commodities, Currencies & rates; optional Watch next. One label per topic.
- JSON output validated for category coverage, length, source IDs and numbers.
  No model-generated links or HTML. Sources remain available in an expandable
  list; missing evidence must be stated rather than invented.
- Fixed news feeds: Economic Times, CNBC-TV18, BBC Business, CoinDesk. At most
  six timestamped headlines per publisher, at most 24 hours old. Cross-asset
  quotes use existing dataserver quotes, retaining their individual timestamps.
- Redis shared snapshot `markets:brief:shared:v3`, retained for 48 hours;
  stale snapshots are marked, never passed off as a fresh generation.
- GET `/api/markets/brief` reads only. It never builds facts or calls a model.
- A startup-owned async worker checks freshness every minute, generating once
  per hour, independently of the disabled broker/SIP scheduler. A separate
  hourly-attempt key prevents failure-driven model retries every minute.
- POST `/api/markets/brief/refresh` requires the normal authenticated session;
  a cross-worker lease and global five-minute cooldown bound manual refreshes.
- Mock/unavailable Redis disables model generation instead of allowing each
  API worker/user to generate independently. Failed generation keeps the old
  snapshot. No user identity, portfolio or conversation is sent to the model.
- `MARKET_BRIEF_ENABLED=0` disables the hourly worker. No broker/order rails
  are enabled or changed by this feature.

## Frontend

The brief uses a real list with bold topic labels, the chat's UI font at 15px,
an icon-only refresh button, explicit update time in IST, stale/error feedback,
and the existing follow-up button. Clients poll the cheap shared GET once a
minute, not a model endpoint. Refresh keeps the old text visible while pending.

## Live evaluation (two calls only)

1. 4,224 input / 954 output tokens (389 reasoning); provider latency 9.713s,
   approximately 19.9s wall time including usage persistence. Four publishers,
   24 recent headlines, seven timestamped quotes. Numeric/source checks passed
   and all markets were covered, but the result was too long and repeated
   India. Quality verdict: reject for concision. Prompt and length/topic gates
   were tightened afterward.
2. 4,317 input / 585 output tokens (287 reasoning); provider latency 7.473s.
   The shorter response was rejected by the grounding/link gate. It was NOT
   published. Quality verdict: not accepted; live end-to-end generation with
   the final guards is not confirmed.

Afterward, a separately identified grounding bug was fixed: comma-grouped
figures in headline evidence now use the same number tokenizer as output.
Regression coverage accepts `8,000` / `1,23,456.78` when present in evidence
and still rejects invented numbers. No third live call was made; do not claim
that this proves the cause of the second rejection or a successful live retest.

The local machine could not persist usage to Postgres after the approved
temporary firewall rule had been removed. Provider usage remained observable.
The existing price registry reports `unknown_model` for this deployment:
its logged $0 cost is NOT proof that these requests are free.

## Deployment boundary

Backend router, worker, prompt and shared-cache module must deploy together
with the frontend; the dataserver must restart to load the two added feed IDs. Normal
source deployment/push approval is still required. Do not enable only the new
frontend against the legacy per-facts generation endpoint, and do not claim
the revised brief is live until one accepted shared snapshot is verified.
