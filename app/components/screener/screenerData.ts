// Mock universe used ONLY by the screener's ETF / Index / Mutual-Fund tabs
// (the Stocks tab is now backed by the live /api/screener endpoints). Numbers
// are illustrative, not real market data. market_cap is in ₹ Cr.
//
// STOCKS / SECTORS remain exported for backward-compat with any other importer
// but are no longer consumed by ScreenerPage (the live grid replaced them).

export const SECTORS = [
  'Banking', 'IT Services', 'Energy', 'FMCG', 'Auto', 'Pharma',
  'Metals', 'NBFC', 'Cement', 'Telecom', 'Consumer', 'Index ETF',
];

// ── ETFs ─────────────────────────────────────────────────
// AUM in ₹ Cr. expense_ratio is annual %.
export const ETF_CATEGORIES = [
  'Equity — Broad', 'Equity — Sector', 'Debt', 'Gold', 'International',
];

// `logoDomain` is the issuing fund-house (AMC) brand domain — the ETF
// tickers aren't in the equity company DB, so the screener builds a
// logo.dev URL from this directly (mirrors Groww's AMC logo column).
export const ETFS = [
  { ticker: 'NIFTYBEES',   exch: 'NSE', category: 'Equity — Broad',   last:  248.90, day_change_pct:  0.61, aum: 28400, expense_ratio: 0.04, one_year_pct: 10.4, three_year_pct: 14.8, tracking_error: 0.04, logoDomain: 'nipponindiaim.com' },
  { ticker: 'BANKBEES',    exch: 'NSE', category: 'Equity — Sector',  last:  484.20, day_change_pct:  0.42, aum:  9200, expense_ratio: 0.18, one_year_pct:  9.8, three_year_pct: 12.4, tracking_error: 0.06, logoDomain: 'nipponindiaim.com' },
  { ticker: 'JUNIORBEES',  exch: 'NSE', category: 'Equity — Broad',   last:  712.40, day_change_pct:  1.12, aum:  4800, expense_ratio: 0.15, one_year_pct: 24.5, three_year_pct: 22.1, tracking_error: 0.08, logoDomain: 'nipponindiaim.com' },
  { ticker: 'ITBEES',      exch: 'NSE', category: 'Equity — Sector',  last:   42.18, day_change_pct: -0.84, aum:  1800, expense_ratio: 0.20, one_year_pct: 14.2, three_year_pct:  9.6, tracking_error: 0.07, logoDomain: 'nipponindiaim.com' },
  { ticker: 'PHARMABEES',  exch: 'NSE', category: 'Equity — Sector',  last:   24.60, day_change_pct:  0.32, aum:   980, expense_ratio: 0.20, one_year_pct: 26.4, three_year_pct: 18.2, tracking_error: 0.09, logoDomain: 'nipponindiaim.com' },
  { ticker: 'GOLDBEES',    exch: 'NSE', category: 'Gold',             last:   62.40, day_change_pct:  0.28, aum:  9800, expense_ratio: 0.55, one_year_pct: 14.6, three_year_pct: 13.2, tracking_error: 0.12, logoDomain: 'nipponindiaim.com' },
  { ticker: 'LIQUIDBEES',  exch: 'NSE', category: 'Debt',             last: 1000.00, day_change_pct:  0.02, aum:  6200, expense_ratio: 0.65, one_year_pct:  6.8, three_year_pct:  5.9, tracking_error: 0.05, logoDomain: 'nipponindiaim.com' },
  { ticker: 'MAFANG',      exch: 'NSE', category: 'International',    last:   84.20, day_change_pct:  1.84, aum:  2100, expense_ratio: 0.50, one_year_pct: 32.8, three_year_pct: 18.5, tracking_error: 0.18, logoDomain: 'miraeassetmf.co.in' },
  { ticker: 'MON100',      exch: 'NSE', category: 'International',    last:  168.50, day_change_pct:  1.42, aum:  9400, expense_ratio: 0.50, one_year_pct: 28.6, three_year_pct: 16.8, tracking_error: 0.15, logoDomain: 'motilaloswalmf.com' },
  { ticker: 'CPSEETF',     exch: 'NSE', category: 'Equity — Broad',   last:   84.10, day_change_pct:  0.94, aum: 36000, expense_ratio: 0.05, one_year_pct: 38.4, three_year_pct: 24.6, tracking_error: 0.06, logoDomain: 'nipponindiaim.com' },
  { ticker: 'BHARATBOND',  exch: 'NSE', category: 'Debt',             last: 1245.30, day_change_pct: -0.05, aum: 18400, expense_ratio: 0.0005, one_year_pct: 7.2, three_year_pct:  6.4, tracking_error: 0.02, logoDomain: 'edelweissmf.com' },
  { ticker: 'NEXT50',      exch: 'NSE', category: 'Equity — Broad',   last:   71.40, day_change_pct:  1.04, aum:  1600, expense_ratio: 0.10, one_year_pct: 22.8, three_year_pct: 18.4, tracking_error: 0.08, logoDomain: 'nipponindiaim.com' },
  { ticker: 'PSUBANKBEES', exch: 'NSE', category: 'Equity — Sector',  last:   84.60, day_change_pct:  1.28, aum:  3200, expense_ratio: 0.45, one_year_pct: 41.2, three_year_pct: 28.6, tracking_error: 0.14, logoDomain: 'nipponindiaim.com' },
];

// ── Indices ──────────────────────────────────────────────
export const INDEX_CATEGORIES = [
  'Broad', 'Sector', 'Strategy', 'Volatility',
];

// `logoDomain` is the exchange that publishes the index — NSE for the
// NIFTY family + India VIX, BSE for the SENSEX — so the screener shows the
// bourse mark beside each index instead of a bare monogram.
export const INDICES = [
  { ticker: 'NIFTY 50',           category: 'Broad',      last: 24142.10, day_change_pct: -0.16, one_year_pct: 12.8, ytd_pct:  6.4, pe: 22.4, dy: 1.30, members: 50, logoDomain: 'nseindia.com' },
  { ticker: 'SENSEX',             category: 'Broad',      last: 79486.32, day_change_pct: -0.15, one_year_pct: 13.1, ytd_pct:  6.2, pe: 23.1, dy: 1.20, members: 30, logoDomain: 'bseindia.com' },
  { ticker: 'BANK NIFTY',         category: 'Sector',     last: 52317.85, day_change_pct:  0.28, one_year_pct: 14.6, ytd_pct:  9.2, pe: 17.4, dy: 0.90, members: 12, logoDomain: 'nseindia.com' },
  { ticker: 'NIFTY MIDCAP 100',   category: 'Broad',      last: 57902.04, day_change_pct:  0.71, one_year_pct: 32.4, ytd_pct: 18.6, pe: 36.2, dy: 0.80, members: 100, logoDomain: 'nseindia.com' },
  { ticker: 'NIFTY SMALLCAP 100', category: 'Broad',      last: 18420.50, day_change_pct:  1.24, one_year_pct: 41.8, ytd_pct: 22.4, pe: 28.6, dy: 0.60, members: 100, logoDomain: 'nseindia.com' },
  { ticker: 'NIFTY IT',           category: 'Sector',     last: 34128.20, day_change_pct: -1.04, one_year_pct:  6.8, ytd_pct: -3.4, pe: 28.4, dy: 2.20, members: 10, logoDomain: 'nseindia.com' },
  { ticker: 'NIFTY PHARMA',       category: 'Sector',     last: 21845.10, day_change_pct:  0.38, one_year_pct: 24.6, ytd_pct: 11.2, pe: 32.8, dy: 0.85, members: 20, logoDomain: 'nseindia.com' },
  { ticker: 'NIFTY AUTO',         category: 'Sector',     last: 22640.40, day_change_pct:  0.94, one_year_pct: 21.4, ytd_pct: 10.8, pe: 27.2, dy: 0.95, members: 15, logoDomain: 'nseindia.com' },
  { ticker: 'NIFTY FMCG',         category: 'Sector',     last: 56120.80, day_change_pct: -0.42, one_year_pct:  4.2, ytd_pct: -1.8, pe: 42.4, dy: 1.85, members: 15, logoDomain: 'nseindia.com' },
  { ticker: 'NIFTY ENERGY',       category: 'Sector',     last: 41280.60, day_change_pct:  1.22, one_year_pct: 18.6, ytd_pct:  8.4, pe: 14.6, dy: 2.40, members: 10, logoDomain: 'nseindia.com' },
  { ticker: 'NIFTY 500',          category: 'Broad',      last: 22340.10, day_change_pct:  0.18, one_year_pct: 18.2, ytd_pct: 10.4, pe: 24.8, dy: 1.10, members: 500, logoDomain: 'nseindia.com' },
  { ticker: 'NIFTY NEXT 50',      category: 'Broad',      last: 71428.30, day_change_pct:  1.04, one_year_pct: 22.8, ytd_pct: 13.2, pe: 28.6, dy: 1.40, members: 50, logoDomain: 'nseindia.com' },
  { ticker: 'NIFTY ALPHA 50',     category: 'Strategy',   last: 48420.20, day_change_pct:  0.84, one_year_pct: 36.4, ytd_pct: 18.6, pe: 32.4, dy: 0.70, members: 50, logoDomain: 'nseindia.com' },
  { ticker: 'NIFTY LOWVOL 50',    category: 'Strategy',   last: 19840.40, day_change_pct:  0.22, one_year_pct: 14.8, ytd_pct:  7.4, pe: 26.2, dy: 1.55, members: 50, logoDomain: 'nseindia.com' },
  { ticker: 'INDIA VIX',          category: 'Volatility', last:    13.82, day_change_pct:  4.20, one_year_pct: -8.4, ytd_pct: -12.6, pe: null, dy: null, members: null, logoDomain: 'nseindia.com' },
];

// ── Mutual Funds ─────────────────────────────────────────
// AUM in ₹ Cr. expense_ratio is annual %. 3y_cagr / 5y_cagr in %.
export const FUND_CATEGORIES = [
  'Equity — Largecap', 'Equity — Flexicap', 'Equity — Midcap', 'Equity — Smallcap',
  'Hybrid', 'Debt — Liquid', 'Debt — Short Duration', 'ELSS',
];

// `logoDomain` is the AMC (fund-house) brand domain — funds carry no ticker
// in the equity company DB, so the screener builds the logo.dev URL from
// this so each fund shows its house mark, exactly like Groww's MF list.
export const MUTUAL_FUNDS = [
  { ticker: 'PARAGFLEXI',    name: 'Parag Parikh Flexi Cap',          category: 'Equity — Flexicap',      nav:  72.84, aum:  64200, expense_ratio: 0.62, three_year_pct: 22.4, five_year_pct: 25.8, one_year_pct: 28.6, logoDomain: 'ppfas.com' },
  { ticker: 'AXISBLUECHIP',  name: 'Axis Bluechip',                   category: 'Equity — Largecap',      nav:  62.40, aum:  31800, expense_ratio: 1.62, three_year_pct: 12.6, five_year_pct: 14.8, one_year_pct: 18.2, logoDomain: 'axismf.com' },
  { ticker: 'MIRAEELSS',     name: 'Mirae Asset ELSS Tax Saver',      category: 'ELSS',                   nav:  48.20, aum:  22400, expense_ratio: 1.65, three_year_pct: 18.6, five_year_pct: 19.4, one_year_pct: 24.5, logoDomain: 'miraeassetmf.co.in' },
  { ticker: 'CANARARNBL',    name: 'Canara Robeco Bluechip Equity',   category: 'Equity — Largecap',      nav:  58.40, aum:  14200, expense_ratio: 1.65, three_year_pct: 14.2, five_year_pct: 17.6, one_year_pct: 21.4, logoDomain: 'canararobeco.com' },
  { ticker: 'NIPPSMALL',     name: 'Nippon India Small Cap',          category: 'Equity — Smallcap',      nav: 184.20, aum:  56800, expense_ratio: 1.40, three_year_pct: 32.4, five_year_pct: 31.6, one_year_pct: 41.8, logoDomain: 'nipponindiaim.com' },
  { ticker: 'KOTAKEMERG',    name: 'Kotak Emerging Equity',           category: 'Equity — Midcap',        nav: 134.60, aum:  48400, expense_ratio: 1.46, three_year_pct: 24.5, five_year_pct: 25.2, one_year_pct: 32.4, logoDomain: 'kotakmf.com' },
  { ticker: 'HDFCBALANCED',  name: 'HDFC Balanced Advantage',         category: 'Hybrid',                 nav: 348.20, aum:  86400, expense_ratio: 1.30, three_year_pct: 16.4, five_year_pct: 14.8, one_year_pct: 19.6, logoDomain: 'hdfcfund.com' },
  { ticker: 'ICICILIQ',      name: 'ICICI Pru Liquid',                category: 'Debt — Liquid',          nav: 384.20, aum:  56000, expense_ratio: 0.20, three_year_pct:  6.4, five_year_pct:  5.6, one_year_pct:  7.2, logoDomain: 'icicipruamc.com' },
  { ticker: 'AXISTREASURY',  name: 'Axis Treasury Advantage',         category: 'Debt — Short Duration',  nav:  31.80, aum:   8400, expense_ratio: 0.34, three_year_pct:  6.8, five_year_pct:  6.2, one_year_pct:  7.4, logoDomain: 'axismf.com' },
  { ticker: 'SBISMALLCAP',   name: 'SBI Small Cap',                   category: 'Equity — Smallcap',      nav: 162.40, aum:  31600, expense_ratio: 1.62, three_year_pct: 26.4, five_year_pct: 27.8, one_year_pct: 36.2, logoDomain: 'sbimf.com' },
  { ticker: 'MIRAEEMRG',     name: 'Mirae Asset Emerging Bluechip',   category: 'Equity — Flexicap',      nav: 138.40, aum:  34200, expense_ratio: 1.62, three_year_pct: 22.8, five_year_pct: 23.4, one_year_pct: 31.4, logoDomain: 'miraeassetmf.co.in' },
  { ticker: 'QUANTSML',      name: 'Quant Small Cap',                 category: 'Equity — Smallcap',      nav: 286.40, aum:  21800, expense_ratio: 1.45, three_year_pct: 38.6, five_year_pct: 36.4, one_year_pct: 48.6, logoDomain: 'quantmutual.com' },
];

export const MARKET_CAP_TIERS = [
  ['large', 'Large Cap', 50000,  Infinity],
  ['mid',   'Mid Cap',   10000,  50000],
  ['small', 'Small Cap', 0,      10000],
];

export const STOCKS = [
  // Banking
  { ticker: 'HDFCBANK',   exch: 'NSE', sector: 'Banking',     last: 1718.30, day_change_pct:  0.84, market_cap: 1305000, pe: 19.4, roe: 17.2, div_yield: 1.10, one_year_pct: 14.2 },
  { ticker: 'ICICIBANK',  exch: 'NSE', sector: 'Banking',     last: 1142.50, day_change_pct:  0.62, market_cap:  802000, pe: 17.8, roe: 16.8, div_yield: 0.85, one_year_pct: 22.4 },
  { ticker: 'AXISBANK',   exch: 'NSE', sector: 'Banking',     last: 1142.85, day_change_pct:  1.16, market_cap:  351000, pe: 14.6, roe: 15.4, div_yield: 0.10, one_year_pct: 10.7 },
  { ticker: 'KOTAKBANK',  exch: 'NSE', sector: 'Banking',     last: 1812.40, day_change_pct: -0.31, market_cap:  360000, pe: 22.1, roe: 13.8, div_yield: 0.10, one_year_pct: -2.4 },
  { ticker: 'SBIN',       exch: 'NSE', sector: 'Banking',     last:  812.30, day_change_pct:  0.94, market_cap:  725000, pe:  9.6, roe: 17.1, div_yield: 1.70, one_year_pct: 33.1 },
  { ticker: 'INDUSINDBK', exch: 'NSE', sector: 'Banking',     last: 1456.10, day_change_pct: -0.84, market_cap:  113000, pe: 12.5, roe: 14.5, div_yield: 1.15, one_year_pct: -8.2 },

  // IT Services
  { ticker: 'TCS',        exch: 'NSE', sector: 'IT Services', last: 3895.10, day_change_pct: -0.12, market_cap: 1410000, pe: 30.2, roe: 49.5, div_yield: 1.20, one_year_pct:  6.1 },
  { ticker: 'INFY',       exch: 'NSE', sector: 'IT Services', last: 1523.45, day_change_pct:  1.32, market_cap:  632000, pe: 24.8, roe: 28.4, div_yield: 2.20, one_year_pct: 12.3 },
  { ticker: 'WIPRO',      exch: 'NSE', sector: 'IT Services', last:  482.10, day_change_pct:  0.42, market_cap:  251000, pe: 21.6, roe: 17.0, div_yield: 0.21, one_year_pct: 18.8 },
  { ticker: 'HCLTECH',    exch: 'NSE', sector: 'IT Services', last: 1655.20, day_change_pct:  0.88, market_cap:  449000, pe: 26.4, roe: 22.6, div_yield: 3.10, one_year_pct: 25.1 },
  { ticker: 'TECHM',      exch: 'NSE', sector: 'IT Services', last: 1690.60, day_change_pct: -1.04, market_cap:  165000, pe: 39.8, roe: 11.2, div_yield: 1.20, one_year_pct: 32.5 },

  // Energy
  { ticker: 'RELIANCE',   exch: 'NSE', sector: 'Energy',      last: 2812.65, day_change_pct:  2.05, market_cap: 1900000, pe: 24.5, roe: 10.4, div_yield: 0.36, one_year_pct: -1.4 },
  { ticker: 'ONGC',       exch: 'NSE', sector: 'Energy',      last:  274.50, day_change_pct:  1.78, market_cap:  345000, pe:  6.1, roe: 16.1, div_yield: 4.45, one_year_pct: 24.6 },
  { ticker: 'NTPC',       exch: 'NSE', sector: 'Energy',      last:  368.40, day_change_pct: -0.46, market_cap:  357000, pe: 16.8, roe: 13.9, div_yield: 2.10, one_year_pct: 18.5 },
  { ticker: 'POWERGRID',  exch: 'NSE', sector: 'Energy',      last:  302.80, day_change_pct:  0.35, market_cap:  281000, pe: 18.9, roe: 17.4, div_yield: 3.85, one_year_pct: 11.2 },

  // FMCG
  { ticker: 'HINDUNILVR', exch: 'NSE', sector: 'FMCG',        last: 2398.40, day_change_pct: -0.65, market_cap:  563000, pe: 54.6, roe: 20.2, div_yield: 1.75, one_year_pct: -4.6 },
  { ticker: 'ITC',        exch: 'NSE', sector: 'FMCG',        last:  443.80, day_change_pct:  0.35, market_cap:  554000, pe: 26.2, roe: 28.9, div_yield: 3.00, one_year_pct: 11.8 },
  { ticker: 'NESTLEIND',  exch: 'NSE', sector: 'FMCG',        last: 2475.10, day_change_pct:  0.18, market_cap:  239000, pe: 70.4, roe: 99.5, div_yield: 0.95, one_year_pct:  4.5 },
  { ticker: 'BRITANNIA',  exch: 'NSE', sector: 'FMCG',        last: 4894.20, day_change_pct: -0.92, market_cap:  118000, pe: 53.8, roe: 49.6, div_yield: 1.35, one_year_pct:  9.8 },
  { ticker: 'DABUR',      exch: 'NSE', sector: 'FMCG',        last:  528.40, day_change_pct:  0.74, market_cap:   93600, pe: 48.2, roe: 19.5, div_yield: 1.10, one_year_pct: -3.2 },

  // Auto
  { ticker: 'MARUTI',     exch: 'NSE', sector: 'Auto',        last: 12940.0, day_change_pct:  1.45, market_cap:  407000, pe: 26.4, roe: 16.1, div_yield: 0.95, one_year_pct: 12.6 },
  { ticker: 'TATAMOTORS', exch: 'NSE', sector: 'Auto',        last:  794.20, day_change_pct:  2.15, market_cap:  290000, pe: 11.4, roe: 25.5, div_yield: 0.42, one_year_pct: -8.4 },
  { ticker: 'M&M',        exch: 'NSE', sector: 'Auto',        last: 2840.60, day_change_pct:  0.92, market_cap:  346000, pe: 30.6, roe: 17.8, div_yield: 0.65, one_year_pct: 32.8 },
  { ticker: 'EICHERMOT',  exch: 'NSE', sector: 'Auto',        last: 4625.10, day_change_pct: -0.18, market_cap:  126000, pe: 31.2, roe: 25.4, div_yield: 0.85, one_year_pct: 16.4 },

  // Pharma
  { ticker: 'SUNPHARMA',  exch: 'NSE', sector: 'Pharma',      last: 1812.50, day_change_pct:  0.34, market_cap:  434000, pe: 38.4, roe: 17.6, div_yield: 0.85, one_year_pct: 24.5 },
  { ticker: 'CIPLA',      exch: 'NSE', sector: 'Pharma',      last: 1502.10, day_change_pct: -0.42, market_cap:  121000, pe: 26.4, roe: 14.6, div_yield: 0.85, one_year_pct: 18.7 },
  { ticker: 'DRREDDY',    exch: 'NSE', sector: 'Pharma',      last: 1218.90, day_change_pct:  0.12, market_cap:  102000, pe: 19.8, roe: 19.2, div_yield: 0.65, one_year_pct: -2.5 },
  { ticker: 'DIVISLAB',   exch: 'NSE', sector: 'Pharma',      last: 5928.20, day_change_pct:  1.04, market_cap:  157000, pe: 64.5, roe: 14.8, div_yield: 0.50, one_year_pct: 41.2 },

  // Metals
  { ticker: 'TATASTEEL',  exch: 'NSE', sector: 'Metals',      last:  142.55, day_change_pct:  2.78, market_cap:  178000, pe: 64.2, roe:  3.4, div_yield: 2.45, one_year_pct: -4.2 },
  { ticker: 'JSWSTEEL',   exch: 'NSE', sector: 'Metals',      last:  984.30, day_change_pct:  1.65, market_cap:  240000, pe: 28.4, roe: 13.6, div_yield: 0.75, one_year_pct: 14.8 },
  { ticker: 'HINDALCO',   exch: 'NSE', sector: 'Metals',      last:  642.10, day_change_pct:  0.98, market_cap:  144000, pe: 12.6, roe: 13.4, div_yield: 0.55, one_year_pct: 20.5 },
  { ticker: 'COALINDIA',  exch: 'NSE', sector: 'Metals',      last:  482.40, day_change_pct: -0.28, market_cap:  297000, pe:  8.4, roe: 47.8, div_yield: 5.40, one_year_pct: 39.6 },

  // NBFC
  { ticker: 'BAJFINANCE', exch: 'NSE', sector: 'NBFC',        last: 6712.55, day_change_pct: -1.84, market_cap:  416000, pe: 27.8, roe: 22.3, div_yield: 0.45, one_year_pct: -2.1 },
  { ticker: 'BAJAJFINSV', exch: 'NSE', sector: 'NBFC',        last: 1612.10, day_change_pct: -0.94, market_cap:  257000, pe: 32.4, roe: 14.1, div_yield: 0.10, one_year_pct: -4.5 },

  // Cement
  { ticker: 'ULTRACEMCO', exch: 'NSE', sector: 'Cement',      last: 11240.0, day_change_pct:  0.42, market_cap:  324000, pe: 48.6, roe: 11.4, div_yield: 0.65, one_year_pct: 15.2 },
  { ticker: 'GRASIM',     exch: 'NSE', sector: 'Cement',      last: 2412.50, day_change_pct:  0.18, market_cap:  164000, pe: 25.8, roe:  9.8, div_yield: 0.45, one_year_pct:  8.4 },

  // Telecom
  { ticker: 'BHARTIARTL', exch: 'NSE', sector: 'Telecom',     last: 1648.40, day_change_pct:  1.22, market_cap:  942000, pe: 78.4, roe: 11.2, div_yield: 0.50, one_year_pct: 51.8 },

  // Consumer
  { ticker: 'ASIANPAINT', exch: 'NSE', sector: 'Consumer',    last: 2964.10, day_change_pct: -0.92, market_cap:  284000, pe: 56.4, roe: 24.8, div_yield: 1.05, one_year_pct: -2.4 },
  { ticker: 'TITAN',      exch: 'NSE', sector: 'Consumer',    last: 3340.60, day_change_pct:  0.84, market_cap:  297000, pe: 92.4, roe: 31.5, div_yield: 0.30, one_year_pct: -1.2 },

  // Index ETF
  { ticker: 'NIFTYBEES',  exch: 'NSE', sector: 'Index ETF',   last:  248.90, day_change_pct:  0.61, market_cap:   28400, pe: 22.6, roe: 14.4, div_yield: 1.20, one_year_pct: 10.4 },
  { ticker: 'BANKBEES',   exch: 'NSE', sector: 'Index ETF',   last:  484.20, day_change_pct:  0.42, market_cap:    9200, pe: 16.8, roe: 16.4, div_yield: 0.90, one_year_pct:  9.8 },
];
