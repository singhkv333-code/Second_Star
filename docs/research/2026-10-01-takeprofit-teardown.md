# TakeProfit.com — competitor teardown (2026-10-01)

Explored live on 2026-10-01 through the user's own (Freeroll) account in
Chrome: the marketing site, the `/platform` workspace, every top-bar and
widget control, the alert, settings, share, theme, hotkey, symbol-search and
pricing dialogs, the AI assistant, the community pages, and the network
traffic behind the chart. Screenshots are in
`docs/research/assets/takeprofit-2026-10-01/`. Prices and counts are as
shown on that date.

One prompt was sent to their AI assistant to see how it answers. It was
refused on the Free plan, which is how the upsell was found. Nothing was
bought, connected or published. The workspace was put back as it was found
(BTC/USDT, 1h, candles, heatmap off).

---

## 1. What they are, in one paragraph

A browser trading terminal for **crypto first, US equities and CFDs
second**. It is built on their own WebGL2 chart engine, a drag-and-drop
widget workspace, and a Python-subset scripting language (**Indie®**). The
business is three revenue lines layered on one terminal:

- a $20/month subscription;
- an **exchange fee rebate** ("up to 50% cashback" on Bybit fees, paid in
  USDC), which is really a broker-partner revenue share passed back;
- a **creator economy**: a paid-indicator marketplace, paid posts, paid
  widgets and 25% lifetime referrals.

The AI assistant (Claude Sonnet 4.6 / Haiku, or bring-your-own-key) sits on
top of the terminal as a side panel. It is not the centre of the product.

Positioning line: "Trading Platform with Rewards — Charting + AI Tools +
Bybit Cashback". The manifesto frames it as a rebellion against "legacy
platforms" that "extract your edge". TradingView is the explicit foil: the
FAQ answers "own charts or TradingView?" and "how does it differ from
TradingView?".

---

## 2. Navigation and information architecture

### Marketing site (logged in or out)

- **Top bar.**
  - Left: the logo plus a purple **Go to workspace** CTA.
  - Centre: a pill-shaped mega-menu with five items.
  - Right: alerts bell, theme toggle, help, **Get Paid** (the rewards CTA)
    and the avatar.
- **Mega-menus.** Each has a grouped column layout with thumbnail cards and
  `NEW`/`SOON` badges. The shipped-versus-roadmap state is public.

  | Menu | Contents |
  |---|---|
  | **Features** | Technical Analysis (Charting, Indicators, Cloud Alerts); Trading (Trading Terminal, Chart Trading `SOON`, Algo Trading `SOON`, DOM Trading `SOON`); AI Tools (AI Assistant `NEW`, TakeProfit MCP `NEW`); a promo card ("Bybit 50% Cashback") and a "Go to Trading Platform" button |
  | **Rewards** | Trade & Earn (Cashback); Build & Sell (Build Indicators, Craft Widgets); Share & Earn (Invite Traders, Write Posts); "Go to Rewards Hub" |
  | **Community** | Large image cards (Indicators, Stock Screeners, Feed); social column (Discord, Reddit, X, Instagram), each with a one-line purpose ("Reddit — feature requests & platform updates") |
  | **Resources** | Blog, Guide, Indie® Language; Roadmap (Request a Feature, Changelog) |
  | **Company** | About `SOON`, Manifesto, Reviews, Support Center |

- **The hero is the AI prompt box.**
  - Above it: a serif headline and a one-line subhead.
  - The box itself: a large text area whose placeholder **types itself out**,
    cycling through example prompts ("Build an indicator that detects RSI
    divergence", "Why is my Indie script repainting").
  - Below it: four chips (Indicators · Technical analysis · Alerts · Platform
    docs).

  The landing page is effectively a chat entry point into the product.
- **Feature pages** (`/features/charting|trading|indicators|alerts`) share
  one template: hero, sticky in-page tab strip (e.g. Trading · Indie® ·
  Alerts · Drawings · Workspaces · Forks · Cash Rewards · Engine · Traders
  Say · FAQ), horizontally scrolling feature cards, big stat numbers ("<100ms
  exchange to trigger", "60fps", "WebGL2"), testimonials, then an FAQ.
  - The FAQ is long and written to answer comparison searches ("Do you use
    your own charts or TradingView?", "Can I convert Pine Script?").
  - That is an SEO play, though there is **no sitemap.xml and no llms.txt**
    (both 404). `robots.txt` is `Allow: /` only.
- **404 page.** A full collage: torn-paper photo cut-outs, a 1980s trader on
  a phone, hand-drawn doodles ("bla bla", "Buy"), a VHS timecode. See
  `404-collage.jpg`. The brand voice is zine/punk ("Own every decision you
  make", an anarchy-A on the All-In plan name), set against a very sober
  terminal.

### The workspace (`/platform`)

There is **no left nav, no page switching and no tabs**. Everything is a
widget on one canvas (`workspace.jpg`).

- **Top-left.** Logo, then a **workspace switcher** ("My Workspace ▾" with a
  list of workspaces and *Create Workspace*). Multiple named workspaces
  replace pages.
- **Top-right cluster** (a single rounded pill):
  - **Add Widgets**;
  - a keyboard-shortcuts button showing `⌘ /`;
  - Settings (`⌘ I`);
  - Fullscreen (`Shift F`);
  - Alerts;
  - Theme;
  - Support;
  - **Get Paid**;
  - the avatar menu: username, a **plan badge ("Freeroll")**, *New Post*,
    *Rewards Hub*, *Logout*.
- **Floating AI launcher.** A gradient sparkle button, bottom-right, always
  present.
- **Widgets.** Rounded dark cards with 8px gutters, tiled edge to edge. The
  default layout is Watchlist | Chart | Indie code editor.

---

## 3. The widget system (their real differentiator in UX)

### Widget chrome (identical on every widget)

| Element | Behaviour |
|---|---|
| **Six-dot handle** (⠿) at the far left of the header | Tooltip "Drag to Move". Drag to re-dock anywhere; clicking it makes the widget *active* |
| **Link-group chip** (a coloured square with a number, e.g. pink "1") | "Widget Linking: create linked groups to keep data in sync across widgets". Per-group toggles: **Symbol, Label, Crosshair, Timeframe, Date Range, Indicators, Chart Type** |
| Content controls | Specific to the widget type |
| **⋯ menu** | Duplicate · Share · Delete · More Settings |
| **Maximise** (↗ corner arrows) | Expands the widget to fill the workspace |
| **Active widget** | Gets a magenta 1px border. `Tab` cycles active widgets; `Shift Tab` cycles "navigation" widgets |

Some header controls (⋯, settings, share, maximise) **appear only on hover**,
which keeps the resting header quiet.

### Widget Hub (`widget-hub.jpg`)

*Add Widgets* slides a full-height drawer over the right of the workspace.

- **Cards.** Each widget is a card with a live mini-preview (a chart with two
  MAs, a 3-row watchlist, a gauge reading "2.03" for backtesting, a
  depth-ladder sketch, a screener histogram) and a blue **+**. You can also
  **drag a card straight onto the canvas**. A hand-drawn monster doodle
  points at the canvas with the words "DRAG & DROP".
- **The 13 widgets:** Chart · Watchlist · Account · Order Entry · Indie® Code
  Editor · Strategy/Backtesting · Market Depth · Screener · Financials · Feed
  · Notes · Lime/Portfolio · Lime/Order. Lime is a US broker, so its widgets
  are partner-branded with a corner badge.
- **More Widgets.** A dashed card that opens a roadmap page ("TakeProfit
  Widget Hub"). It shows collage art, "Delivered Q1/Q2 2026" cards, a
  **third-party widget marketplace "coming later"**, "Submit your idea" and a
  developer program.
- **Third-party widgets** are served as iframes from their own subdomains
  (`*.widget.takeprofit.com` in the CSP: `lime.`, `ilotcos.`, `j2t.`). The
  sandboxing seam for outside developers is already in place.

### Persistence

- Every UI change autosaves. Switching the timeframe fired
  `PUT /api/settings/<id>`; there is also `/api/settings/root-settings`.
- The theme is cached in `localStorage` (`takeprofit.theme.css-variables`)
  and applied in an inline `<head>` script before first paint, so there is
  no flash.

---

## 4. The chart widget, control by control

### Header (left to right)

1. Drag handle, then the link-group chip.
2. **Symbol pill.**
   - Contents: two overlapping coin logos (base + quote), the ticker, the
     exchange, and a green "live" radio icon.
   - Clicking it opens symbol search (§6).
3. **Timeframe** (shown as "1h ▾"). The menu has collapsible sections:
   - **Ticks:** 1, 5, 10, 25, 50, 100, 500, 1000;
   - **Seconds:** 1, 3, 5, 10, 15, 30, 45;
   - **Minutes:** 1, 3, 5, 15, 30, 45;
   - **Hours:** 1, 2, 3, 4;
   - **Days:** 1D, 1W, 1M;
   - **Custom timeframe:** a number input plus a unit and **+**, so 25s, 12m
     or 2d are allowed; favourites can be saved.
4. **Chart type:** Candles · Line · Bars · **TPO** (market profile) ·
   **Footprint**.
5. **Indicators (ƒ+):** a popover picker (§5).
6. **VS:** compare/overlay another symbol. This opens the same search with
   asset tabs.
7. **Layers:** the object tree for panes, indicators and drawings ("group,
   lock, hide, reorder").
8. **Create Alert (⌥A):** an anchored dialog (§7).
9. **Heatmap:** an **order-book heatmap** overlay on crypto. It has an
   on-chart legend chip with a two-handle range slider (0–652) that sets the
   liquidity colour scale (`heatmap-context-menu.jpg`).
10. Undo / redo.
11. Shown on hover: ⋯ (Duplicate/Delete/More Settings), **Chart Settings**,
    **Share**, Maximise.

### On the canvas

- **Top-left.**
  - A change chip ("+200.0 · 0.24%").
  - An OHLC legend on hover.
  - **Buy / Sell / Connect Exchange** buttons. Trading is one click away on
    every chart.
- **Crosshair price label.** When the crosshair is near the price axis, a
  **"Sell"/"Buy" pill** appears next to it. This is click-to-place at that
  price.
- **Drawing toolbar.** A **floating dock at the bottom centre** that appears
  on hover. There is no TradingView-style left rail. Its groups, left to
  right:
  - collapse;
  - measure;
  - line tools, patterns and shapes;
  - brush tools, fib/channels and text;
  - then a separate cluster: magnet, show/hide, drawing sync scope, lock all,
    delete all.
- **Bottom status bar.**
  - Live clock with timezone ("16:25 UTC");
  - `»` jump to realtime;
  - **session selector "Reg ▾"** (regular vs extended hours);
  - **"Auto"** price scale.
- **Right-click menu.**
  - Reset Chart View;
  - **Sell/Buy @ price Limit / Stop / Stop Limit**: orders anchored at the
    clicked price;
  - Copy Price;
  - Draw Line ▸;
  - **Create Alert on <symbol>**;
  - Layers;
  - Remove Drawings;
  - Remove Indicators;
  - Chart Type ▸;
  - Settings.

### Drawing tools (from the hotkey map; 40+ hotkeys, all rebindable)

| Group | Tools |
|---|---|
| Main | Measure, copy/paste object, undo/redo, hide-all (⌘H), clone (⌘-drag), magnet (hold ⌘), lock (⌘L), bar zoom (⌘-scroll), snap 45°/90° (hold Shift) |
| Measurements | Long/Short position tool (⌥L), Date Range, Price Range |
| Lines | Trendline, Horizontal Line, Horizontal Ray, Vertical, Cross Line, Channel |
| Figures | Rectangle, Circle, Ellipse, Triangle, Curve, Arc |
| Fibonacci | Retracement, Extension (plus time zones per the marketing page) |
| Patterns | XABCD, Cypher, Head & Shoulders, ABCD, Triangle Pattern, 3 Drives, Elliott Impulse, Elliott Correction |
| Draw | Pen, Highlighter, Path, Polygon |
| Text | Text, Callout, Price Tag |

- **Drawing scope** is selectable per drawing: Global, Workspace, Channel
  (link group) or Local. A level drawn once can follow the symbol to every
  chart.
- The **Long/Short tool** computes R:R, position size and P&L from account
  size and risk %. It is a planner only and places no order.

### Chart Settings drawer (`chart-settings-footprint.jpg`)

The drawer slides in from the right with four tabs. Its footer has
**Templates ▾ · Apply to All · Reset to Default**.

| Tab | Settings |
|---|---|
| **Style** | 5 chart-type tiles. **Every row** has two colour swatches (rise/fall), a reset icon and an **eye** toggle (Body, Border, Wicks, Last Visible Price, Last Price, Volume Bars) |
| **Footprint config** | Type (Buy and Sell…), Display (Cluster…), cluster row size (Auto), colour normalisation, POC, labels, infobox, value area, ratio %, **imbalance threshold**, filters |
| **Canvas & Scales** | Background, text, grid, margins (in **bars**); security label (logo / full name / exchange); market-status dot; bar-change / OHLC / volume values; scale type; price labels; **time to close**; overlapping labels; timezone (exchange time) |
| **Trading** | Trades on chart; P&L value in **money (USD) or ticks**; order control; label location (L/C/R); positions; TP/SL control; inactive TP/SL |
| **Alerts** | Show alert lines; inactive alerts; ticker labels |

### Share (`Share Chart` dialog)

- A live preview tile showing exchange, interval and a UTC timestamp.
- **Allow to copy** ("viewers can copy chart to their workspace").
- **Create a link** or **Save as image**. Images go to
  `snapshots.takeprofit.com`.
- **Share & Earn** (the referral hook).

This is the same idea as our shared setups ("Make it mine"). They call it a
**fork**.

---

## 5. Indicators, Indie® and the marketplace

- **Indicator picker** (popover).
  - Search: "Find scripts…".
  - Source dropdown: Recent / Favorites / Subscribed / **Built-In** / My
    Scripts.
  - Grid/list toggle.
  - Each card shows the name, type ("Built-in Indicator") and **last-updated
    date**.
  - Footer: **Explore** (marketplace) and **+** (new script).
- **Built-in library: 85 entries.** It covers all the usual suspects plus
  CVD, Delta Volume, Trading Sessions, Pivot Points (High/Low and Standard),
  Chande Kroll Stop, Connors RSI, McGinley, ZigZag, Williams Alligator and
  others. **Every built-in is open Indie source and can be forked.** At least
  three **built-in strategies** exist: ADX Breakout, Consecutive Up/Down,
  Inside Bar.
- **Indie® language.**
  - A Python subset with `@indicator` / `@algorithm` decorators. Files start
    `# indie:lang_version = 5`.
  - Imports from `indie`, `indie.algorithms` (Atr, PivotHighLow…) and
    `indie.drawings` (LabelAbs, LineSegment, Rectangle).
  - Multi-symbol and multi-timeframe; session-aware; user inputs map to UI
    controls.
  - Scripts **run server-side**, so alerts on custom indicators run with the
    browser closed.
  - Pine Script / MQL conversion is done with AI.
- **Code editor widget.**
  - Header: script picker (All Scripts / Built-in), ⋯ (Edit / **Fork** /
    Delete Script), **Docs** button.
  - Toolbar: undo/redo, **version selector "v1 ▾" with a DRAFT badge**,
    search, **Add to Chart `⌘D`**.
  - New scripts open with an auto-inserted MIT licence header carrying the
    author's handle.
  - Scripts are stored versioned: `/api/repo/indicators/<id>/versions/<n>`.
- **Marketplace** (`/indicators`, `indicator-page.jpg`).
  - About 50 pages × 12, so roughly 600 community indicators, filtered All /
    Free / Paid / Recent. Prices run **$1 to $100 per month**.
  - The detail page has: breadcrumb; huge serif title; tabs **Chart** /
    **Source code**; **Add to Chart** (purple); **Add to Favorites** with a
    count; a **live embedded chart** of the indicator; author with *Follow*;
    comments; share; licence badge (MIT).
  - Publishing goes through moderation (2–4 business days).
  - Creator split: **80%**, or 100% on buyers you referred. Payout at $200.

---

## 6. Symbol search and the data they serve

### Search modal

- A large overlay with "Search symbol or company…".
- A **Formula** toggle for spread or ratio expressions.
- **Sources** filter chip with a count.
- Asset tabs: **All · Funds · Equities · Crypto · Commodities · Forex · Bonds
  · Indices**.
- **Rows:**
  - base/quote logos, ticker, exchange, full name;
  - a blue **check** (instrument is in a watchlist);
  - live price in the quote currency;
  - coloured change and %.
- **Footer** of keyboard hints: `Shift ↵` add to *[chosen watchlist ▾]*,
  `← →` filters, `↑↓` navigate, `↵` apply.

### Sources picker

A grid of exchange cards with checkboxes, grouped by region and class, each
group with "Select All" (`⌘A` for everything):

| Group | Venues |
|---|---|
| 🇺🇸 United States | AMEX, CBOE, NASDAQ, NYSE |
| Forex & CFD | Exness, Pepperstone |
| Crypto (about 55) | Aster, BigONE, Binance, Binance US, BingX, Bitbank, Bitbns, Bitfinex, Bitget, Bithumb, BitMart, AscendEX, BitMEX, Bitrue, Bitso, Bitstamp, Bittrex, Bitvavo, BTSE, Bybit, CEX.IO, Coinbase, Coincheck, CoinEx, Coinone, Crypto.com, Cryptology, Delta Exchange, Dex-Trade, Gate.io, Gemini, GOPAX, HitBTC, HTX, Huobi JP, **Hyperliquid**, IDAX, Independent Reserve, Indodax, IndoEx, itBit, Kraken (+futures), KuCoin, LATOKEN, Luno, MEXC, OKX, Paribu, Phemex, Poloniex (+futures), tothemoon, Upbit, VinDAX, YoBit |

**There is no Indian exchange: no NSE, BSE, MCX or NFO.** Their marketing
claims "5,000+ instruments" and "70+ crypto exchanges". Equities are US
only; forex/CFD (gold, indices such as "CA60") comes from two CFD brokers.

### What the data supports

- **Tick charts and second charts.** These are built from raw trades on
  Binance, Bybit, Exness and Pepperstone.
- **Arbitrary custom intervals.** The bars endpoint is literally called
  `ExtrapolationApi/ListBars`: bars are folded server-side from raw data to
  whatever interval is asked for.
- **Order flow: footprint, TPO, order-book heatmap, market depth (DOM).**
  These need trade-side and book data, which crypto venues publish.
  *Indian retail feeds do not provide aggressor side* (see
  `project_charto_volume_profile_2026_07_31`), so this column is not
  reproducible for NSE equities. For F&O it would need a depth feed.
- **Fundamentals** ("10 years of financial data" on the paid plan) and a
  **stock screener** with categories and community-published screens (US
  small-caps, "undervalued stocks").
- **History depth is a plan lever:** "20,000 historical candles" on All-In.
  This is the same lever as our `chart.history_bars`.

### How it is served (observed on the wire)

- **Frontend.**
  - Stack: SvelteKit (Svelte 5), Vite, Tailwind with a `tw-` prefix.
  - Fonts: IBM Plex Sans and IBM Plex Mono, with a serif display face for
    headlines.
  - Delivery: CloudFront CDN; PWA manifest (`display: standalone`).
  - Size: about 246 immutable JS chunks on workspace load.
  - Stale deploys: a `vite:preloadError` handler reloads the page, and a
    "New version available — Reload" toast appears.
- **Backend API: gRPC-web over Connect** (`application/grpc-web+proto`) at
  `backend.takeprofit.com`, with versioned protobuf services:
  - `marketdata.external.candle.v1.ExtrapolationApi/ListBars`
  - `marketdata.external.quote.v1.QuoteApi/ListQuotes`
  - `marketdata.external.price_charts.v1.VolumeFootprintHistoryApi/QueryVolumeFootprintRowSizeScale`
  - `marketdata.external.ratio.v2.CatalogueApi/ListModes`
  - `marketdata.external.fundamental.v2.FundamentalApi/ListModes`
  - `screener.external.v2.CategoryApi/ListCategories`

  Most first calls answered in about 280–350 ms from India. Category listing
  took 1.4 s.
- **Auth.**
  - **Stytch**: the login form posts to `api.stytch.takeprofit.com`.
  - A JWT from `/api/auth/token` is sent as an `accesstoken` header on every
    RPC.
- **Live ticks.** The watchlist and last price update continuously. The
  stream was opened at boot, before any hook could see it, and no new fetch
  or WebSocket was created on symbol or interval change. That points to one
  multiplexed stream opened at boot (re-subscribed in place) or a worker.
  This is consistent with the "one stream per browser" recommendation in our
  own research doc.
- **Other hosts in the CSP:**
  - `trading.` (order routing);
  - `snapshots.` (shared images);
  - `media-files.` and `cdn.`;
  - `*.widget.` (third-party iframes);
  - `uniswap.`;
  - `affiliates.` (Rewardful);
  - `a.` (a **PostHog** reverse proxy);
  - Stripe;
  - Termly (consent);
  - GTM/GA4, Google Ads and the Reddit pixel.
- **CSP posture.** The CSP is in **report-only** mode, with `unsafe-eval`
  allowed in script-src.

---

## 7. Alerts

- **Entry points:** the header button, ⌥A, the right-click menu, or the
  watchlist header.
- **The dialog** is anchored under the header button:
  - a symbol row with **+** for **multi-symbol alerts**, and a timeframe;
  - **Conditions** with **Add Condition** (AND of several);
  - **Source:** Price, or any indicator *currently on the chart*. Empty-state
    copy: "add them to the active chart first";
  - **Criteria (13):** Crossing, Crossing Up, Crossing Down, Greater Than,
    Lower Than, Entering Channel, Exiting Channel, Inside Channel, Outside
    Channel, Moving Up, Moving Down, Moving Up %, Moving Down %;
  - **Target:** a value with a stepper, or another series;
  - **Expand:** frequency (once / once per bar close / every trigger),
    **expiry date** (defaults to one month), delivery channels, message;
  - **Add More** stacks more alerts in the same dialog. This is "bulk
    alerts".
- **Delivery:** in-app pop-up, email, Telegram, webhooks (Discord, Slack,
  Zapier, IFTTT, own server).
  - Webhook messages are JSON templates with variables: trigger type,
    exchange/session/ticker/timeframe, OHLCV, time, series 1–3 values,
    exchange code.
- **Claims:** under 100 ms from exchange to trigger, server-side, 99.9%
  uptime, five statuses (Active, Fired, Expired, Stopped, Error), history and
  logs.
- **Plan limits:** Free gets **1 alert with a 3-month expiry**; All-In gets
  **400 non-expiring** alerts.
- **Webhook automation is the "algo trading" today.** You paste bot
  credentials into the alert message and route it to an external bot.

---

## 8. Trading and execution

- **Execution venue.** **Bybit** (spot and perpetuals) is live through OAuth
  "trade-only" broker access. Binance and Exness are "coming soon". **Lime**
  (US broker) is available as widgets.
- **Order entry.**
  - Order types: Market and Limit, with optional trigger price (last, mark
    or index).
  - Order options: TP/SL, R:R, Reduce-Only; **risk-based sizing** (stop by
    cash or % sizes the position); Cross/Isolated margin and per-position
    leverage.
  - Safety: a **confirmation modal** with fee estimate. *One-click trading*
    is the **only** item in the Settings dialog, and it skips that modal.
- **Chart trading** is drag-to-modify orders on the chart; marked `SOON` in
  the menu but partly live. **DOM trading** and **algo trading** are marked
  `SOON`.
- **Synced account.** Open orders, equity, available balance, uPnL and
  margin mode, including orders placed on the exchange directly.

---

## 9. The AI layer

- **Launcher and empty state.**
  - Launcher: bottom-right; it opens a floating card.
  - Empty state: "Analyze charts, build scripts, automate alerts", with
    chips Indicators · Technical analysis · Alerts · Platform docs.
- **Composer.**
  - An **Add context** row of removable chips: the current chart (symbol ·
    exchange · interval) and the open script.
  - *New Chat*, a settings gear, a mode selector ("Auto ▾"), and a **model
    picker**: "TAKEPROFIT MODELS — Claude Sonnet 4.6, Claude Haiku" plus
    **Add Models**.
- **Paywall.** On Free, sending a prompt returns an upsell card instead of an
  answer. It promises:
  - "Spot levels, patterns and trends, drawn right on your chart";
  - "Describe an indicator or strategy, get validated Indie® code";
  - "Bring your PineScript or MQL scripts over to Indie®";
  - "Create alerts and watchlists in bulk with a single message".

  It offers two exits: **Upgrade to All-In** or **Connect API Key**. The
  second runs 400+ models through OpenRouter, Anthropic or OpenAI, "billed by
  your provider, never through us".
- **TakeProfit MCP.** An MCP server that lets Claude or other agents write,
  convert and audit Indie code and **deploy alerts** from outside the
  browser. Their testimonials single this out ("The MCP server is a game
  changer").

---

## 10. Watchlist, notes, feed, screeners

- **Watchlist widget.**
  - Header: an emoji/icon per list (😊), list name ▾ (Recents / My
    watchlists / **Built-in** lists / Create Watchlist), **+ Add Symbol**,
    **Add Alert**, ⋯ (Duplicate/Share/Delete), maximise.
  - Toolbar: view mode, sort, column set.
  - Rows have two lines: a ticker chip with logos, exchange and live icon on
    top; a description ("Bitcoin / Tether • Spot") below. The right side
    shows the quote currency, last price, change and %. Rows **flash-tint**
    on ticks.
  - Row right-click: Copy to / Move to / Delete from watchlist.
- **Notes widget.** Rich text (H1, text size, lists, images).
- **Feed.** Posts with *Following / Recent* filters, **subscriber-only
  posts** (creators keep 90%, or 100% from their own invites), and the
  company changelog posted as a feed item ("What's New in August 26':
  Footprint & Heatmap Charts, Indie Live Streams & TPO, Bulk Alerts, and AI
  Upgrades").
- **Community screeners.** Shared stock screens with like and fork counts.

---

## 11. Pricing and the paywall

| | **Freeroll** (free forever) | **All-In** |
|---|---|---|
| Price | $0 | **$20/month**, or **$120/year** (shown as "~~$240~~ $120, 50% OFF") |
| Trial | — | **30 days free, card required** (Stripe), "Skip Trial And Pay Now", promo-code field |
| Alerts | 1, expires within 3 months | **400 cloud alerts, non-expiring** |
| Workspaces / indicators / widgets | Core charting | **Unlimited** workspaces, indicators, and widgets per workspace |
| History | (lower) | **20,000 historical candles** |
| Fundamentals | — | **10 years of financial data** |
| Screener | Manual refresh | **Auto-refresh** |
| AI assistant | Not included (or bring your own key) | Included (Claude Sonnet 4.6 / Haiku) |

- **One paid tier only.** There is no pricing page (`/pricing` is a 404).
  Pricing appears only as an **in-context modal at the moment of need**: the
  AI paywall, an alert limit or a feature gate.
- **The modal** (`pricing-modal.jpg`) has:
  - a "Start Free Trial / To Push The Limit" title;
  - the plan name in a large serif with an orange anarchy-A;
  - a plain feature list;
  - a Monthly/Annually toggle with a purple "50% OFF" pill;
  - the trial line "$0 — All-In Trial (30 Days)" next to the first charge
    date;
  - "Cancel the Offer · ESC".

**Rewards economy** (from the Rewards and Cashback pages):

- **Cashback.**
  - Rate: 50% of Bybit fees (10% if the account is already affiliated with
    another partner); VIP 0–99 and PRO 1–3 qualify, PRO 4–6 are excluded.
  - Payment: accrues daily in a USD balance in the **Rewards Hub** and is
    paid in **USDC from $200, on request to support**.
  - Pitch: "official broker rebate on one account, not self-referral".
  - The page has a slider calculator (rate × monthly volume → monthly
    cashback).
- **Referrals:** 25% of revenue from each invited user, for life.
- **Marketplace:** 80% to the creator, 100% for referred buyers. **Posts:**
  90% / 100%. **Widgets:** paid subscriptions.
- **Ambassador** program with an application form.

---

## 12. Visual design language

- **Palette.** A near-black canvas (about #0b0c0e); widget cards one step
  lighter (about #1b1d21). There is **one saturated brand accent, purple**,
  used for the CTA, "Add to Chart" and the 50%-off pill. Buy and Sell use
  pastel lime and salmon *buttons*, distinct from the green/orange candles.
  The active widget gets a magenta outline.
- **Type.**
  - IBM Plex Sans for the UI and **IBM Plex Mono for numbers and code**.
  - A **light, high-contrast serif** for marketing and modal headlines:
    "Trading Platform with Rewards", "All-In Plan", indicator titles.
  - The serif-in-a-terminal contrast is their signature.
- **Density.** Compact: 12–13 px UI text, two-line watchlist rows,
  icon-only toolbars with tooltips that include the hotkey ("Create Alert
  ⌥ A", "Settings ⌘ I").
- **Motion.** Popovers fade and slide; the hero placeholder types itself out;
  the hub drawer slides in.
- **Theming.**
  - **16 named presets:** System, System Dark, System Light, Arctic, Astro
    Trader, Dawn, Day One, Gold Rush, Platinum, Profit Cowboy, Red Dragon,
    Sahara, Smoothie, VHS Noir, Wen Moon, Wonderland.
  - **Add Theme**, built from just **five tokens**: Background, Text, Rise,
    Fall, Accent.
- **Hotkeys modal.**
  - Categories: Essential, Chart, Trading, Watchlist, IDE, Financials, Notes,
    Stock Screener, Alerts, plus a joke tab, "Take it easy!".
  - Every binding is click-to-rebind, with *Restore Default Settings*.
  - Hotkeys are first-class: the shortcuts button sits in the top bar,
    labelled `⌘ /`.
- **Personality.** Doodles in the drawers, a collage 404 and "Profit Cowboy"
  theme names. The terminal stays sober; the personality lives in the empty
  states and edges.

---

## 13. How they compare with Pivot

| Dimension | TakeProfit | Pivot (charto) |
|---|---|---|
| Market | Crypto (about 55 venues) + US equities + 2 CFD brokers. **No India** | **India-first**: NSE/BSE equities, indices, NFO, MCX, CDS, plus crypto |
| Core promise | Terminal + fee cashback + creator economy | A chart that explains itself + a strategy builder that refuses to flatter |
| AI | Side-panel assistant (Claude Sonnet/Haiku), **paywalled**, BYOK, MCP server | Chat-native, the centre of the product: drawings with evidence, `mark` addresses, move attribution |
| Honesty layer | Backtests show win rate, max DD and equity curve. **No multiple-testing control, no base rates** | Trust ladder (Deflated Sharpe, MinTRL, permutation test, trial counter); a pattern rate always ships with its base rate |
| Scripting | Indie® (Python subset), server-side, versioned, forkable built-ins, 600-script marketplace with 80% creator share | AI custom-indicator builder (sandboxed, native chart path); no public marketplace |
| Workspace | Modular widgets, link groups on 7 dimensions, multi-workspace, unlimited charts | Single chart + chat; layouts, shared setups with "Make it mine" |
| Order flow | Footprint, TPO, order-book heatmap, DOM (crypto data permits it) | Volume profile; aggressor-side order flow is not available on Indian retail feeds |
| Execution | **Live** Bybit (spot + perps), confirmation modal, one-click option | **Simulated paper book by design**; broker rails dormant (SEBI framework) |
| Alerts | 13 criteria, multi-condition, multi-symbol, bulk, webhooks with JSON variables, 400 per user | Composed-expression rules, crossing side persisted, boot catch-up |
| Pricing | One paid tier, $20/mo or $120/yr, 30-day card trial, in-context paywall | Free / Pro / Pro+ catalog with a 402 contract (Razorpay inert) |
| Stack | SvelteKit + Tailwind + WebGL2 engine, gRPC-web/protobuf, Stytch, Stripe, PostHog | Vanilla JS + vendored Lightweight Charts v5, stdlib Python dataserver, Next.js pages |

---

## 14. What is worth taking, in order

1. **Link groups on named dimensions.** One coloured chip per widget, with
   toggles for symbol / timeframe / crosshair / date range / indicators /
   chart type. It is the cheapest high-value piece of their workspace, and it
   maps onto our multi-pane plans without a full widget system.
2. **Drawing scope (Global / Workspace / Group / Local).** Ours are per chart.
   "Draw a level once and it follows the symbol everywhere" pairs naturally
   with the D-ref and `mark` addressing we already have.
3. **Paywall at the moment of need, with a BYOK escape hatch.** One modal,
   shown when a limit is hit, listing exactly what the plan unlocks, with
   annual pricing as the default and visible savings. The BYOK option keeps
   price-sensitive power users inside the product.
4. **Alert dialog details we lack:** expiry date, frequency (once / per bar /
   every), multi-symbol, Add More (bulk), channel-type criteria (entering,
   exiting, inside, outside), and webhook JSON templates with variables. Our
   engine already composes expressions; this is mostly UI and delivery.
5. **Hotkey map as a product surface.** A `⌘/` button in the top bar, a
   categorised modal, click-to-rebind, and the hotkey shown in every tooltip.
6. **Fork everything.** Built-ins open as source, public charts copyable, a
   versioned script repo with a DRAFT state. Our custom indicators already
   live on the native path; adding *fork a built-in* and *v1/v2 + draft*
   would close most of the gap.
7. **Theme presets from five tokens.** Background / Text / Rise / Fall /
   Accent is all their custom themes need. Our CSS tokens can do the same,
   with the theme cached pre-paint to avoid a flash.
8. **Quiet chrome.** Header actions appear only on hover; the drawing dock is
   a bottom floating bar that shows on hover; there is a session selector and
   a jump-to-realtime control in the status bar.

**What not to copy:**

- **Live order buttons on every chart and the "one-click trading" setting.**
  This conflicts with *simulate, don't execute*. Our equivalent is
  "Paper Buy/Sell" into the simulated book.
- **Fee-cashback economics.** These rely on a crypto exchange broker program;
  there is no Indian equivalent we can or should run.
- **Paid "SMC signal" indicators and subscriber-only posts.** These are
  personalised-advice territory under SEBI and conflict with *data and
  frameworks, never advice*. If a marketplace is ever built, it should sell
  tools with base-rate evidence, never signals.
- **Footprint / DOM for NSE equities.** The data does not exist on retail
  feeds. Spend the effort on TPO / volume profile from minute bars, which we
  can compute honestly.

**Where we are ahead and should say so:** India coverage they do not have at
all; AI at the centre rather than behind a paywall; evidence per annotation;
and the trust ladder. Their backtests report win rate and drawdown with no
multiple-testing control. That is exactly the gap our verdicts are built to
close.
