# Handoff: the page assistant (2026-09-30)

Branch `claude-cloud-29sep`. The prompt bar on Home, Portfolio, Strategy,
Screener, Brokers and the company page now opens a **Pivot Assistant** side
panel, served by a separate lightweight engine. The Chat tab (full chat) and
the Chart tab (Charto's chat) are unchanged.

## Why a second engine

The bar used to hand its question to the full chat: a ~26k-token prefix,
24 always-loaded tools plus deferred groups, hosted web search, and up to 6
rounds. Every question paid that, and still had to call tools for figures the
page was already showing.

## How it works

`pivot/backend/services/assist_chat.py`, served at `POST /chat/assist` (SSE)
and `POST /chat/assist/warm`:

1. **Page data first.** `page_snapshot(page, symbol)` runs each page's sources
   in parallel through the existing tool registry (`_SOURCES`), compacts the
   results (no render payloads or series, lists capped at 12, rounded
   numbers) and caches the block for 60s per (user, page, symbol). A source
   slower than 8s is written as "unavailable", never waited on. Concurrent
   requests share one fetch.
   - Home: NIFTY 50, SENSEX, BANK NIFTY and NIFTY Midcap 100 levels,
     portfolio summary, NIFTY 50 gainers and losers.
   - Portfolio: summary, holdings, sector split.
   - Strategy: strategies and SIPs.
   - Company page: quote, 1y history (returns, SMAs, RSI), fundamentals,
     P/E band, news.
   - Screener and Home also send what only the browser holds: the screen and
     its first 25 rows, and the watchlist. Pages publish these through
     `publishVisible()` in `pivot-next/lib/assist.ts`; the server labels them
     as data, not instructions.
2. **Warm-up.** The shell calls `/chat/assist/warm` 0.8s after the user lands
   on a page, and again when the bar is focused, so the snapshot is ready
   before the question. It is debounced to once per 45s per page and costs
   no credit.
3. **Small prompt.**
   - Brief plus app map: ~420 tokens. The app map answers "where is"
     questions with in-app links (`/#screener`, `/stock/TCS`).
   - Static content comes first and changing data last, so the provider can
     cache the prefix.
   - No hosted web search: it cost ~4.3k tokens and ~0.8s per question. Web
     research is pointed to the full Chat.
4. **Few, read-only tools per page** (`PAGE_TOOLS`), with descriptions cut to
   their opening sentences. The long glossaries appear only on Screener. On
   the company page `screen_fundamentals` exposes only its peer-comparison
   parameters. Tool budget per page: home ~735, portfolio ~607, stock ~1.4k,
   screener ~4k tokens (was ~5.2k plus 4.3k for search on every page).
5. **At most two tool rounds**, calls within a round run in parallel, and the
   tools are withdrawn on round 3 so the model must answer. Tool outputs go
   through the same compaction as the snapshot.
6. **Visible progress.** SSE carries the page read, each lookup with its
   subject ("Reading the news · INFY"), reasoning summaries when the model
   reasons (`stream_openai(reasoning_summary="auto")`), then the answer. The
   panel folds these into "From this page · 2.0s" or "1 lookup · 3.0s", or
   "Thought for Ns" when there was reasoning.
7. **Failures** reach the user as one sentence from `FAILURE`, or the
   front end's `failureText()` if the request itself failed. Status codes,
   traces and provider text stay in the log. Private-use citation tokens are
   stripped (`flex_chat._with_links`).

Front end:
- `components/copilot/AssistantPanel.tsx`: the thread (kept in
  sessionStorage), steps, answer through `AssistantMessage` (tables in the
  screen-card design), `ScreenResultsCard` for screens, suggestions per page,
  Stop, New, and "Open in Chat" (carries the last question to the full chat).
- `lib/assist.ts`: the stream client, warm-up and visible-data registry.
- `AppShell.tsx`: the old side panel no longer mounts the full chat; the
  assistant `<aside>` does.

## Fixes found on the way

- `get_market_data(view=quote)` with an index name looked up `NIFTY.NS` and
  waited out a 5s miss. It now routes to the index path
  (`consolidated_handlers._INDEX_NAMES`).
- `get_index_level`'s enum gained `NIFTYMIDCAP`; the handler already mapped
  it.

## Measured

Live model `gpt-6-luna` on Azure; this cloud box cannot reach Postgres, so
DB-backed sources read "unavailable" in the probes and SQLite test mode was
used end to end.

| Question (page) | Tools | Input tokens | First token | Total | Quality |
|---|---|---|---|---|---|
| How are markets doing today? (Home, probe) | 0 | 2,395 | 1.7s | 2.2s | All four figures from the snapshot, correct |
| Two biggest NIFTY losers vs 52w low (Home, probe) | 4 parallel, 1 round | 6,191 | 4.0s | 5.2s | Table, distances, defended view |
| Same at `ASSIST_EFFORT=medium` | 5, 2 rounds | 6,306 | 14.3s | 14.5s | Re-fetched movers already in the block; slower |
| Watchlist name that fell most, near 52w low? (Home, browser) | 1 | n/a | n/a | 3.0s after Enter | Used the on-screen watchlist plus one lookup |
| How has it done this year? (INFY page, browser) | 0 | 2,752 | n/a | 2.0s after Enter | Returns, SMAs, RSI, 52w-low distance |
| Summarise this screen (Screener, browser) | 0 | n/a | n/a | 2.1s after Enter | Read the 25 rows; P/E honestly unavailable locally |
| Model deployment missing (Portfolio) | n/a | n/a | n/a | n/a | "Pivot couldn't reach its analysis service just now. Please try again in a moment." |

Old path for comparison: the same bar went through the full chat at
~11-22k input tokens per round, often with 2-3 rounds.

## Reproduce

- Probe: `pivot/scripts/assist_probe.py home "how are markets today" --warm`.
  Point `DATABASE_URL`, `FINANCIALS_DSN` and `ENRICH_DSN` at
  `postgresql://x:x@127.0.0.1:1/x` where Postgres is unreachable.
- End to end without Postgres:
  1. Run the API with `APP_ENV=test` from a scratch directory (SQLite
     `pivot_test.db`).
  2. Create the tables with `Base.metadata` (`t.create(engine)` for each
     table).
  3. `POST /auth/register`, then run
     `PIVOT_BACKEND_ORIGIN=http://localhost:8000 pnpm next dev`.
  4. Put the token in `localStorage.pivot_jwt`.
- Tests: `pivot/tests/test_assist_chat.py` (6),
  `pivot-next/tests/chat/assistant-panel.test.tsx` (4). The full `pivot-next`
  suite has 36 failures in 11 files, identical by name on the untouched base.

## Open items

1. `ASSIST_EFFORT` defaults to `low`. On this deployment `low` spends no
   reasoning tokens on page questions, so "Thought for Ns" rarely appears; the
   step list is the visible progress. `medium` streams summaries but was
   3-4x slower.
2. The Home snapshot's slowest source is NIFTY 50 movers (~4s cold through
   yfinance). The warm-up hides it; Home could publish its own mover rows
   instead, like the watchlist.
3. Assistant threads are per browser session and not saved to the
   conversation list; "Open in Chat" is the path to a saved conversation.
4. Re-measure with Postgres reachable (portfolio holdings, fundamentals,
   P/E band) and with Kite quotes.

## References used

- Anthropic, [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents):
  few, well-named tools; high-signal, token-efficient results; description
  wording matters.
- Anthropic, [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents):
  preload what is known and fetch the rest just in time.
- OpenAI, [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
  and [Prompt Caching 201](https://developers.openai.com/cookbook/examples/prompt_caching_201):
  static prefix first, dynamic content last, a stable tool list.
