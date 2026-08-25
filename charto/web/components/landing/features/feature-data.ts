/**
 * feature-data — everything the features section SAYS, with no DOM and no
 * layout. Copy, prompts, and the deterministic product fragments each feature
 * quotes.
 *
 * Same status as `film-script`: this is illustrative landing copy shaped to
 * the real tool contracts in `charto/data/dataserver.py`, not live output from
 * the agent. Every figure is a literal — nothing is generated at render, so
 * server and client agree and a screenshot taken today matches one taken next
 * month.
 *
 * The shapes below are the tools' own vocabulary, deliberately:
 *   levels      → get_levels    (pivot clusters, touch record, median reaction)
 *   detections  → get_levels + get_patterns + volume_profile
 *   workspace   → panes.js SPECS, layouts.js, indicators.js legends
 *   rungs       → multi_timeframe (four identical readings per interval)
 *   screen      → screen_universe (end-of-day features across the universe)
 *   alert       → set_alert      (a composed expression, notify-only)
 *   plan/journal→ plan_position + list_trades
 *   financials  → the company page's KeyMetricsStrip + FinancialsPanel
 *   chart ops   → open_chart / get_results(draw) view operations
 * Keeping the vocabulary honest is what stops the page claiming a capability
 * the product does not have — there is no order routing in any of it.
 *
 * `DEMO_MOVE` and its `MovePanel` are kept though no slot currently quotes
 * them: `explain_move` is still a shipped tool, and the transcription is the
 * expensive half. Delete them only when the tool goes.
 */

/** The instrument the whole page demonstrates on, matching `film-script`. */
export const DEMO_SYMBOL = "RELIANCE";
export const DEMO_EXCHANGE = "NSE";

/* ── 01 · levels that carry their own record ─────────────────────────────── */

export type DemoLevel = {
  id: string;
  /** Price band as the level list prints it. */
  price: string;
  /** Where the detector found it — the provenance line. */
  from: string;
  role: "resistance" | "support" | "neutral";
  touches: string;
  /** The record. `tone` is what the record says, not what we hope. */
  record: string;
  tone: "good" | "bad" | "flat";
  /** Median reaction after a touch, as get_levels reports it. */
  reaction: string;
  /** Price band in DATA space, for the chart overlay. Omitted = not drawn. */
  band?: { from: number; to: number };
};

export const DEMO_LEVELS: readonly DemoLevel[] = [
  {
    id: "res",
    price: "₹2,486 – 2,496",
    from: "pivot cluster · 1D · since 04 May",
    role: "resistance",
    touches: "4 touches",
    record: "Held 3 of 4",
    tone: "good",
    reaction: "median −1.8% after",
    band: { from: 2486, to: 2496 },
  },
  {
    id: "mid",
    price: "₹2,461",
    from: "pivot cluster · 1D · mid-range",
    role: "neutral",
    touches: "27 touches",
    record: "Broke 14 of 26",
    tone: "bad",
    reaction: "median +0.2% after",
  },
  {
    id: "sup",
    price: "₹2,404 – 2,428",
    from: "pivot cluster · 1D · since 27 May",
    role: "support",
    touches: "6 touches",
    record: "Held 5 of 6",
    tone: "good",
    reaction: "median +1.4% after",
    band: { from: 2404, to: 2428 },
  },
];

/* ── 02 · why it moved, or that nobody knows ─────────────────────────────── */

export const DEMO_MOVE = {
  prompt: "Why is it down 4.1%?",
  window: "12 Aug 2026 → 19 Aug 2026 · 5 sessions",
  stats: [
    { k: "Move", v: "−4.1%", q: "close to close", tone: "down" as const },
    { k: "vs own history", v: "0.4σ", q: "inside the ordinary range" },
    { k: "Delivery", v: "41.8%", q: "72nd percentile" },
    { k: "Futures OI", v: "Short buildup", q: "OI +6.2%, price −4.1%" },
  ],
  /** The gate: what the index already accounts for, before any cause is named. */
  attribution: [
    { label: "NIFTY ENERGY moved", value: "−3.8%", width: 93, fill: "s2" as const },
    { label: "Beta 1.02 would explain", value: "−3.9%", width: 95, fill: "s1" as const },
    { label: "Residual — the part it does not", value: "−0.2%", width: 5, fill: "flat" as const },
  ],
  verdict: "No clear catalyst.",
  verdictNote:
    "The sector moved with it and the part left over is smaller than an ordinary session's noise. Nothing here is specific to this company.",
  context: [
    "Q1 results · 12 Aug · three sessions before the window opened",
    "No bulk or block deals printed in the window",
    "₹2,404 is the level that decides pullback from trend change",
  ],
} as const;

/* ── 03 · every timeframe, measured the same way ─────────────────────────── */

export type DemoRung = {
  label: string;
  reads: string;
  stance: "Bullish" | "Bearish";
};

/* Six rungs, because six is what the tool actually climbs: `_MTF_LADDER` is
   ("5m","15m","30m","1h","1d","1w") and a call with no `intervals` argument
   walks all of it. The page used to show four, which quietly re-cut the
   product's own default to fit a card. */
export const DEMO_RUNGS: readonly DemoRung[] = [
  { label: "5m", reads: "RSI 41.30 · MACD hist −0.52 · ADX 24.6 · −DI>+DI · below EMA 50", stance: "Bearish" },
  { label: "15m", reads: "RSI 44.10 · MACD hist −0.34 · ADX 21.7 · −DI>+DI · below EMA 50", stance: "Bearish" },
  { label: "30m", reads: "RSI 45.80 · MACD hist −0.22 · ADX 19.4 · −DI>+DI · below EMA 50", stance: "Bearish" },
  { label: "1h", reads: "RSI 47.90 · MACD hist −0.11 · ADX 18.2 · −DI>+DI · below EMA 50", stance: "Bearish" },
  { label: "1D", reads: "RSI 51.60 · MACD hist +0.12 · ADX 16.4 · +DI>−DI · above EMA 50", stance: "Bullish" },
  { label: "1W", reads: "RSI 58.20 · MACD hist +1.94 · ADX 27.9 · +DI>−DI · above EMA 50", stance: "Bullish" },
];

/* ── 04 · one question, the whole universe ───────────────────────────────── */

export const DEMO_SCREEN = {
  prompt: "Which names crossed their 50-day this week and are still above the 200?",
  rows: [
    { sym: "TITAN", note: "crossed 2 sessions ago", num: "RSI 61.4" },
    { sym: "CIPLA", note: "crossed 3 sessions ago", num: "RSI 58.9" },
    { sym: "BHARTIARTL", note: "crossed 4 sessions ago", num: "RSI 57.2" },
    { sym: "DIVISLAB", note: "crossed 4 sessions ago", num: "RSI 55.8" },
    { sym: "DRREDDY", note: "crossed 5 sessions ago", num: "RSI 54.6" },
  ],
  /* The head's right half, so it has to survive a phone: the screen's scope
     and the day it was scored, short enough to sit beside the title at 358px
     rather than be hidden there. */
  asOf: "14 of 500 · EOD 21 Aug",
} as const;

/* ── 05 · say it once, and it watches ────────────────────────────────────── */

export const DEMO_ALERT = {
  prompt: "Tell me if it actually breaks out — I'm away all afternoon",
  reading:
    "“Breaks out” is read against the chart, not invented: the level is the ₹2,496 resistance the detector already found, and the volume leg is what makes it a break rather than a wick.",
  conditions: [
    { left: "close", op: "above", right: "₹2,496.00" },
    { left: "volume", op: "above", right: "2 × avg(volume, 20)" },
  ],
  meta: "1D · all conditions · once per bar close",
  state: "Armed",
  note: "It notifies. It never places an order.",
} as const;

/* ── 06 · it remembers what you did ──────────────────────────────────────── */

export const DEMO_PLAN = {
  basis: "support 2,404–2,428 · 6 touches",
  rows: [
    { k: "Entry", v: "₹2,430.00", q: "last close" },
    { k: "Stop", v: "₹2,398.00", q: "1.4 × ATR(14)" },
    { k: "Target", v: "₹2,496.00", q: "resistance band" },
    { k: "Size", v: "781", q: "from ₹25,000 at risk" },
  ],
  rr: "2.06 R",
  breakeven: "needs 32.7% to break even",
} as const;

export const DEMO_JOURNAL = {
  stats: [
    { k: "Closed trades", v: "34" },
    { k: "Win rate", v: "47.1%" },
    { k: "Profit factor", v: "1.62" },
    { k: "Expectancy", v: "+0.38 R" },
  ],
  adherence: "Plan adherence 79% · 27 of 34 reviewed",
  /** `list_trades` returns the rows as well as the statistics over them. */
  last: { what: "Last closed · RELIANCE long", when: "08 Aug", r: "+1.4 R" },
} as const;

export const DEMO_RECALL = [
  {
    when: "14 Aug",
    line: "“wait for a daily close above 2,496 before adding” — your words, on this chart, ten sessions ago.",
  },
  {
    when: "29 Jul",
    line: "You asked whether the June gap had filled. It had — in four sessions, which is faster than this stock’s median.",
  },
] as const;

/* ── 01 · what the structure pass found ──────────────────────────────────── */

/** The three detectors the structure feature quotes, each named by its tool. */
export const DEMO_DETECTIONS = [
  { k: "Levels", v: "3 zones · ₹2,404 – 2,496", tone: "sup" as const },
  { k: "Pattern", v: "Descending triangle · 1D", tone: "res" as const },
  { k: "Value area", v: "₹2,436 – 2,472 · POC 2,458", tone: "flat" as const },
] as const;

/* ── 02 · the workspace ──────────────────────────────────────────────────── */

/** Four of `panes.js`'s SPECS, spelled the way that file spells them: an id,
 *  the label the layout menu shows, and the grid-area rows the glyph draws. */
export const DEMO_LAYOUTS = [
  { id: "s1", label: "Single chart", spec: ["a"] },
  { id: "c2", label: "Two columns", spec: ["ab"] },
  { id: "l1r2", label: "Left · two right", spec: ["ab", "ac"] },
  { id: "g22", label: "2 × 2", spec: ["ab", "cd"], on: true },
] as const;

/** The panes the active 2 × 2 holds. Only the MAIN pane carries drawings —
 *  `open_chart` says so in as many words, so the panel does too. */
export const DEMO_PANES = [
  {
    sym: "RELIANCE", interval: "1D", note: "main · drawings live here",
    main: true, tone: "up" as const,
    spark: [38, 34, 41, 37, 45, 43, 52, 48, 57, 55, 63, 68],
  },
  {
    sym: "NIFTY 50", interval: "1D", note: "benchmark",
    tone: "up" as const,
    spark: [30, 33, 31, 38, 36, 42, 47, 44, 50, 54, 52, 59],
  },
  {
    sym: "TCS", interval: "15m", note: "reference",
    tone: "down" as const,
    spark: [64, 61, 66, 58, 54, 57, 49, 45, 47, 39, 36, 31],
  },
  {
    sym: "USDINR", interval: "1h", note: "reference",
    tone: "up" as const,
    spark: [44, 46, 43, 48, 51, 49, 54, 56, 53, 58, 61, 60],
  },
] as const;

/** Indicator legends as `indicators.js` prints them — name plus its periods,
 *  because an indicator without its settings is not a reading. */
export const DEMO_INDICATORS = [
  "EMA 20", "EMA 50", "RSI 14", "MACD 12/26/9",
  "Bollinger 20 · 2σ", "Supertrend 10 · 3", "VWAP", "ADX 14",
] as const;

/* ── 06 · the company page's numbers ─────────────────────────────────────── */

/** `KeyMetricsStrip`'s non-bank tiles, in that component's own order. */
export const DEMO_FUNDAMENTALS = {
  period: "As of FY25",
  tiles: [
    { k: "ROE", v: "9.24%" },
    { k: "ROCE", v: "9.81%" },
    { k: "D/E", v: "0.42x" },
    { k: "P/B", v: "1.71x" },
    { k: "EV/EBITDA", v: "12.40x" },
    { k: "Net Margin", v: "8.10%" },
  ],
  years: ["FY23", "FY24", "FY25"],
  rows: [
    { k: "Revenue", vals: ["8,76,396", "9,01,064", "9,64,693"] },
    { k: "Operating profit", vals: ["1,28,942", "1,42,171", "1,50,010"] },
    { k: "Net profit", vals: ["66,702", "69,621", "69,648"] },
    { k: "EPS (basic)", vals: ["98.60", "102.90", "102.90"] },
  ],
  unit: "₹ crore, consolidated",
} as const;

/* ── 08 · the chart doing what the sentence said ─────────────────────────── */

export const DEMO_CHART_OPS = [
  {
    said: "Open TCS beside this one",
    tool: "open_chart",
    did: "reference pane added · layout grew to Two columns",
  },
  {
    said: "Make it weekly",
    tool: "open_chart",
    did: "main pane switched to 1W",
  },
  {
    said: "Mark the last four results",
    tool: "get_results",
    did: "4 markers placed on the sessions that could react",
  },
  {
    said: "Clear those markers",
    tool: "get_results",
    did: "removed · scope: markers, owner get_results",
  },
] as const;


/* ── 01 · the session the demo replays ───────────────────────────────────── */

/**
 * One turn of the real thing, transcribed rather than imagined.
 *
 * `steps` are `chat.js`'s own wait rows — its opening STEP_SCRIPT line
 * followed by the line each tool that actually ran contributes (`toolStep`),
 * lead word plus supporting words. `reply` is what an assistant turn looks
 * like in the app: bare prose, no bubble, quoting the numbers the card
 * carries rather than restating the question.
 */
export const DEMO_SESSION = {
  ask: "Mark the key support and resistance levels",
  steps: [
    { word: "Reading", detail: "the chart" },
    { word: "Scanning", detail: "price history" },
    { word: "Measuring", detail: "levels" },
  ],
  elapsed: "3s",
  reply:
    "Two zones carry a record worth the ink. 2,486–2,496 has held 3 of its 4 tests; 2,404–2,428 has held 5 of 6, and price is sitting on it now.",
  drawn: "2 drawn",
} as const;

/**
 * The band labels, in `get_levels`'s own format — role letter, the cluster
 * price, and the touch record (`dataserver.py`: `f"{R|S} {price:,.2f} · held
 * {held}/{graded}"`). The landing page used to print RESISTANCE and SUPPORT in
 * filled badges, which is a legend for a diagram; the product prints the
 * finding.
 */
export const DEMO_ZONE_LABELS = {
  res: "R 2,491.00 · held 3/4",
  sup: "S 2,416.00 · held 5/6",
} as const;

/* ── 02 + 05 · the two claims told as figures ────────────────────────────── */

/**
 * Two features cannot be shown as one pane. The workspace claim is about how
 * much fits at once — four panes shrunk to fit a landing card prove the
 * opposite — and the journal claim is that three separate things live in one
 * place. Both are told as a divided row of drawings instead: a tile, a name,
 * and one line.
 *
 * The names are the nouns from each feature's own sentence, not new claims.
 * "Indicators, layouts, fundamentals, and multi-chart views" becomes three
 * columns because layouts and multi-chart views are one thing in the product
 * (`panes.js` SPECS), and "trades, notes, and charts" is already three.
 *
 * Each line names a capability that exists: pane specs and per-pane symbols,
 * the indicator legends, the company page beside the chart, `plan_position`,
 * the note kept against a trade, and the chart image stored with it.
 */
export const DEMO_WORKSPACE_FIGURES = [
  {
    tile: "layouts",
    name: "Multi-chart layouts",
    line: "Split the workspace into panes and give each one its own instrument and interval.",
  },
  {
    tile: "indicators",
    name: "Indicators",
    line: "Overlays on the price pane, oscillators in their own, each with its legend.",
  },
  {
    tile: "fundamentals",
    name: "Fundamentals",
    line: "Statements, key metrics and the earnings print, beside the chart rather than elsewhere.",
  },
] as const;

export const DEMO_JOURNAL_FIGURES = [
  {
    tile: "planTrade",
    name: "Trades",
    line: "Entry, stop and target kept as you planned them, with what the position did next.",
  },
  {
    tile: "notes",
    name: "Notes",
    line: "What you were thinking, written against the bar you were thinking about it on.",
  },
  {
    tile: "screenshot",
    name: "Charts",
    line: "The chart as it looked when you took the trade, kept with the trade.",
  },
] as const;
