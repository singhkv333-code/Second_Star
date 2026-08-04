# charto/web — the company page

This is **Pivot's stock page, copied file-for-file**, not a re-implementation:
`components/StockDetailPage.tsx`, its component tree, `lib/`, `hooks/` and the
design tokens all come straight from `pivot-next/`. It runs against **charto's**
data server instead of Pivot's backend, so a company page and the chart quote
the same bars.

## Running it

```sh
cd charto/web && npx next dev -p 5175        # node_modules symlinks to pivot-next
```

Then `http://localhost:5175/stock/RELIANCE`. The chart links here from the ↗ on
a search-dropdown row, and `Open chart →` links back. Chat replies are not
linked — a reply's tables carry the company's logo next to its name, nothing
more.

`.env.local` points the copied `lib/api.ts` at charto:

```
NEXT_PUBLIC_PIVOT_API_BASE=http://127.0.0.1:5174/api
```

`charto/data/dataserver.py` answers those Pivot routes (`api_route`) out of its
own SQLite store: `/api/markets/quote|sparkline|ohlc`, `/api/financials/{sym}`,
`/api/companies/search|logos`. Prices come from the same bars the chart draws;
profile/CEO/logo/P-B/EV come from the synced `company_profile` table
(`charto/data/sync_company_profile.py`, Moneycontrol + enrichment, joined on
`sc_id` and never on the corrupted `ticker`).

## What differs from Pivot's copy, and why

Four edits, all marked. Three hang off a single `CHARTO` flag in
`StockDetailPage.tsx` — charto is a chart, not a broker or an account system:

| Suppressed | Reason |
| --- | --- |
| Buy / Sell CTAs | charto has no order path; a button that cannot register an order is a fake control |
| Watchlist bookmark | charto has no accounts |

Key Metrics and the Financial Performance panel are live: `sync_financials.py`
runs **Pivot's own** `backend.market.financials_db` against the same
Moneycontrol database and stores the assembled payloads, so the numbers are
Pivot's, not a second derivation. Where Moneycontrol has no ratio row (D/E,
current ratio, P/B, EV multiples for much of the universe) Pivot calls
yfinance live; charto fills the same gaps from the enrichment snapshot instead,
tagged `yfinance` — so those few can sit a scrape behind Pivot's live call.

The fourth: `app/stock/[symbol]/view.tsx` swaps Pivot's `AppShell` (its sidebar
of tabs charto doesn't have) for a charto bar with a link back to the chart,
and `/stock` is ungated in `AppBootstrap` so the page opens without a sign-in.

A field neither source publishes for a company renders as the page's own
em-dash, never as a filler number. `Year Founded` comes from the same curated
map Pivot ships.
