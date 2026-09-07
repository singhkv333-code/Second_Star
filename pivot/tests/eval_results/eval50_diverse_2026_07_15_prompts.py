"""A second, independently-authored 50-prompt eval set — distinct from
eval50_capability_2026_07_14_prompts.py (different tickers/sectors/
condition shapes throughout) — run against the ALREADY-FIXED ChatService
(post eval50_post_refactor) to check the fixes generalize beyond the
exact prompts that drove them, and to re-probe known deferred weak spots
with fresh, non-memorized phrasing:

  - notification-channel honesty (email/SMS/WhatsApp -> push disclosure)
  - MCX commodity workflows
  - backtest level-condition (staying-true, not crossover) triggers —
    probes the known backtest/live latch divergence
  - short/bearish and recurring-contribution backtests
  - build_strategy theme/constraint fidelity + reliability
  - F&O critique-vs-build routing (explicit "critique X" / "what could
    go wrong" asks, not "build me X")
  - sector-wide qualitative exposure asks (different sectors/criteria
    than the original industry_twist prompts) + a different single-
    company volatile-fact question (not ZEEL)
  - screening criteria the DB likely can't screen directly (multi-year
    consistency) to check for an honest substitution disclosure
"""

PROMPTS = [
    # ── Automation / workflows (9) ────────────────────────────────
    ("automation", "Alert me by SMS if NIFTY's dividend yield crosses above 1.5%."),
    ("automation", "Build an alert for SILVER on MCX — notify me on WhatsApp when it drops 3% in a day."),
    ("automation", "Set up an automation: sell my HDFCBANK position if it's held for more than 20 trading days, no matter what the price does."),
    ("automation", "I want to buy 15 shares of TATAPOWER whenever it closes above its 20-day high OR its RSI crosses above 70, whichever happens first."),
    ("automation", "Automate booking profits on my WIPRO holding at +10%, and separately cut losses at -6% — two independent exits."),
    ("automation", "Set up something for my portfolio so I don't lose too much if the market crashes."),
    ("automation", "Email me every Friday at 3:00 PM with NIFTY BANK's closing level."),
    ("automation", "Create a workflow that buys 25 shares of IRCTC if it drops 5% AND volume is more than twice the 20-day average, then auto-sells after 15 trading days."),
    ("automation", "Alert me on WhatsApp when CRUDEOIL on MCX crosses below its 50-day moving average."),

    # ── Backtesting (9) ────────────────────────────────────────────
    ("backtest", "Backtest staying long on NIFTYBEES only while it's trading above its 200-day moving average, over the last 5 years — how many separate holding periods were there?"),
    ("backtest", "What would shorting IDEA (Vodafone Idea) have returned if I shorted every time it rallied more than 8% in a week, over the last 3 years?"),
    ("backtest", "Simulate investing ₹10,000 every month into NIFTYBEES for the last 4 years and tell me my XIRR."),
    ("backtest", "Backtest a 3-stock rotation: hold whichever of RELIANCE, TCS, or HDFCBANK had the best 20-day return, rebalancing weekly, over 2 years."),
    ("backtest", "Test buying LT whenever its MACD line crosses above the signal line, and selling when it crosses back below, over the last 3 years."),
    ("backtest", "I have ₹1.5 lakh — backtest putting it all into BAJFINANCE and selling half whenever it's up 15%, over the last 3 years."),
    ("backtest", "Backtest a strategy: on the 15-minute chart, buy TATASTEEL when RSI(14) is below 30, but only take the trade if the daily trend (price above 50-day SMA) is also bullish. Last 6 months."),
    ("backtest", "Backtest buying ONGC whenever its volume is more than 3x the 20-day average volume and the price is up on the day, holding for 5 days, over the last 2 years."),
    ("backtest", "What if I'd bought GOLDBEES every time gold fell 4% from its recent high, over the last 5 years — how does that compare to just buying and holding?"),

    # ── Agent basket (8) ───────────────────────────────────────────
    ("basket", "Build me a basket of exactly 5 companies benefiting from India's data-center and cloud infrastructure buildout, with 4 lakh rupees."),
    ("basket", "Construct a basket for the PLI scheme in electronics manufacturing, excluding any company with debt-to-equity above 1, with 6 lakh rupees."),
    ("basket", "I want a basket of companies with dividend yield above 3% and ROE above 15%, no PSU banks, with 3 lakh rupees."),
    ("basket", "Build a rural-consumption recovery basket — no urban-focused retail names — with 5 lakh rupees."),
    ("basket", "Make me a basket that's 70% large-cap equity and 30% gold, for someone nearing retirement, with 8 lakh rupees."),
    ("basket", "Build a basket of exactly 4 private-sector banks, weighted by ROE, with 2 lakh rupees."),
    ("basket", "Create a monsoon/agri-input basket — fertilizers and agrochemicals only, no FMCG — with 3.5 lakh rupees."),
    ("basket", "I want a basket that avoids anything with promoter pledge above 20%, focused on mid-cap manufacturing, with 5 lakh rupees."),

    # ── F&O (8) ────────────────────────────────────────────────────
    ("fno", "What could go wrong with selling a naked put on ADANIENT right before its quarterly results?"),
    ("fno", "Critique a calendar spread on BANKNIFTY right now — what's the biggest risk I'm not thinking about?"),
    ("fno", "Write a covered call on my 500 shares of ITC — show me strikes and expected premium for the monthly expiry."),
    ("fno", "Set up a protective put on 300 shares of MARUTI at the nearest OTM strike."),
    ("fno", "Build an iron condor on NIFTY for the current weekly expiry."),
    ("fno", "I think SBIN will stay flat for the next month — what option strategy should I use?"),
    ("fno", "Show me the option chain for IDEA and suggest a strategy — I'm not sure it even has decent options liquidity."),
    ("fno", "Why might a bear put spread on ZOMATO be a worse idea than just shorting the stock directly?"),

    # ── Analysis / fundamental research (8) ────────────────────────
    ("analysis", "Has PAYTM's promoter/founder shareholding changed in the last few quarters?"),
    ("analysis", "Which IT services companies are most exposed to US H-1B visa policy changes given their onsite-heavy delivery models?"),
    ("analysis", "Which paint companies are most exposed to crude-oil-linked raw material cost swings?"),
    ("analysis", "Which private banks have the highest exposure to unsecured retail lending right now?"),
    ("analysis", "Give me a full fundamental breakdown of COFORGE — margins, growth, valuation, balance sheet health."),
    ("analysis", "Compare TRENT and DMART on same-store sales growth and margin trajectory."),
    ("analysis", "Explain what EV/EBITDA tells you that P/E doesn't, using APOLLOHOSP as a live example."),
    ("analysis", "Has SUZLON had any recent insider buying or selling I should know about?"),

    # ── Screening (8) ──────────────────────────────────────────────
    ("screening", "Screen for companies with debt-to-EBITDA below 1.5 and net profit margin above 15%."),
    ("screening", "Find companies that have grown revenue every single year for the last 3 years, with current ROE above 18%."),
    ("screening", "Screen for small-cap companies with price-to-sales below 1 and positive free cash flow."),
    ("screening", "Top 10 companies by ROCE in the auto ancillary sector, excluding anything with D/E above 0.8."),
    ("screening", "Screen for FMCG companies excluding cigarette/tobacco makers, sorted by revenue growth."),
    ("screening", "Find cheap, high-quality mid-cap industrials — nothing fancy, just solid fundamentals."),
    ("screening", "Screen for companies with interest coverage above 8 and cash-to-debt above 0.5."),
    ("screening", "Which infrastructure companies have order books that have grown for 3 consecutive years?"),
]

assert len(PROMPTS) == 50, len(PROMPTS)
