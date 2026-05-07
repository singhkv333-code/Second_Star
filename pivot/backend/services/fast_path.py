"""Pre-LLM fast-path classifier.

Catches conversational starters (greetings, thanks, help asks) that
don't need an LLM at all. Each match returns a canned response in
under a millisecond — that's a ~5000× speedup over the agentic loop
and avoids burning tokens on "hi".

Conservative on purpose: strict equality after normalization, plus
prefix-with-trailing-content so "hello, what's RELIANCE's price"
does NOT match (the model needs to handle it). Better to miss a
fast-path opportunity than mis-route a real query.
"""
from __future__ import annotations

import re
from typing import Optional


# Order matters slightly: longer phrases come before single words so
# "good morning" matches before any subset would.
_GREETINGS = (
    "good morning", "good afternoon", "good evening", "good night",
    "namaste", "namaskar",
    "hello", "hey", "hi", "yo", "sup",
)

_THANKS = (
    "thank you very much", "thanks a lot", "thank you",
    "thanks", "thx", "ty", "appreciate it", "cheers",
)

_HELP_QUERIES = (
    "what can you do",
    "what can you help me with",
    "how do you work",
    "what is pivot",
    "what features do you have",
    "show me what you can do",
    "/help",
    "help",
    "capabilities",
)

# WHY these exist: after an order/workflow card lands, users often type
# a short continuation ("what else", "anything else", "what now") to ask
# what they can do next. Without this fast-path the LLM hop tries to
# amend the prior draft and produces a wrong card or a leaked catalog
# error message. A short canned reply nudges the user toward a fresh
# intent.
_CONTINUATION_QUERIES = (
    "what else",
    "what else can you do",
    "anything else",
    "what now",
    "now what",
    "what next",
    "what's next",
    "whats next",
    "next",
    "and now",
    "ok now what",
)


_GREETING_REPLY = (
    "Hi! Tell me what you'd like to do — check a price, build an agent, "
    "look at your portfolio, or run a backtest."
)

_THANKS_REPLY = "Anytime."

_CONTINUATION_REPLY = (
    "What would you like to do next? You can check a price, build or "
    "amend an agent, look at your portfolio, or run a backtest."
)

_HELP_REPLY = (
    "I can help you with:\n"
    "• Live prices, fundamentals, and screening of Indian stocks\n"
    "• Building automated trading agents in plain English\n"
    "• Backtesting strategies on historical data\n"
    "• Tracking your portfolio and active agents\n\n"
    "Just describe what you want — for example, *\"buy 10 RELIANCE every "
    "weekday at 3:55 PM\"* or *\"show stocks where PE < 15 and ROE > 18\"*."
)


# Strip trailing punctuation that doesn't change meaning ("hi!", "hello?", "thx.")
_TRAILING_PUNCT_RE = re.compile(r"[?!.,;:]+$")


def _normalize(message: str) -> str:
    """Lowercase, strip whitespace + trailing punctuation, collapse
    multiple spaces. Returns "" for empty/whitespace input.
    """
    s = (message or "").strip().lower()
    # Repeatedly strip trailing punctuation + whitespace so "hi ,"
    # → "hi" not "hi " (regex stops at the space the first time).
    prev = None
    while prev != s:
        prev = s
        s = _TRAILING_PUNCT_RE.sub("", s).rstrip()
    s = re.sub(r"\s+", " ", s)
    return s


def _matches_phrase(normalized: str, phrases: tuple[str, ...]) -> bool:
    """Match either equality or prefix-followed-by-end. Crucially does
    NOT match "hello, what's RELIANCE's price" — that has more content
    after the greeting, so it goes to the LLM.

    Match shapes:
      "hello"                           → match
      "hello!"   (after _normalize)     → match
      "hello there"                     → no match (extra content)
      "is hello supported"              → no match (greeting not at start)
    """
    if not normalized:
        return False
    for p in phrases:
        if normalized == p:
            return True
    return False


# ── Educational definitions (curated) ────────────────────────────────
#
# WHY: every "what is RSI" / "explain SIP" / "what is GTT" hits the
# agentic loop today — ~22K input tokens billed (cache hits ~70% of
# that, but the dynamic suffix + output still cost real money) and
# 3-10s of latency for a definition that never changes. A curated
# dict keyed off definition-shaped phrasing returns the answer at
# microsecond latency and zero token cost. Conservative: only
# triggers when the message normalises EXACTLY to one of the
# definition shapes — "what is RSI", "explain RSI", etc. Anything
# with surrounding context ("what is RSI for TCS") falls through
# to the LLM.
#
# Adding a term: pick a stable canonical name (lowercase, no
# punctuation), write a 1-3 sentence definition with Indian-market
# framing, and add it to _DEFINITIONS. Keep tone consistent with
# system.md — professional, concise, no slang.

_DEFINITIONS: dict[str, str] = {
    # Indicators
    "rsi": (
        "**RSI (Relative Strength Index)** is a momentum oscillator that "
        "measures the speed and magnitude of recent price changes on a "
        "0–100 scale. Computed over a lookback window (typically 14 "
        "candles). Common reads: above 70 → overbought, below 30 → "
        "oversold. Best paired with trend context — RSI alone is a "
        "weak signal in strong trends."
    ),
    "sma": (
        "**SMA (Simple Moving Average)** is the unweighted mean of a "
        "stock's closing prices over a fixed window (e.g. SMA-50 = "
        "average of last 50 closes). Used to smooth noise and identify "
        "trend direction. A price above its SMA-200 is a long-term "
        "uptrend signal in classical TA."
    ),
    "ema": (
        "**EMA (Exponential Moving Average)** is a weighted moving "
        "average that gives more weight to recent prices, so it reacts "
        "faster than the SMA. Common periods: 9, 21, 50, 200. Crossover "
        "of a fast EMA above a slow EMA is a classical bullish trigger."
    ),
    "macd": (
        "**MACD (Moving Average Convergence Divergence)** is a trend-"
        "and-momentum indicator built from two EMAs (typically 12 and "
        "26) and a signal line (9-EMA of the difference). A MACD line "
        "crossing above its signal line is a bullish trigger; below is "
        "bearish. Histogram bars show the gap."
    ),
    "bollinger bands": (
        "**Bollinger Bands** plot two standard deviations above and "
        "below a 20-period SMA. Price tagging the upper band signals "
        "stretched conditions; the lower band, compressed conditions. "
        "Band width contracts in low-volatility regimes ('squeeze')."
    ),
    # Order types
    "gtt": (
        "**GTT (Good Till Triggered)** is a Zerodha order type that "
        "stays inactive until a target price is hit, then sends an "
        "actual order. Useful for set-and-forget triggers — e.g. "
        "*buy RELIANCE if it drops to ₹2,500*. GTTs are valid for one "
        "year and are stored at Zerodha, not at Pivot."
    ),
    "oco": (
        "**OCO (One-Cancels-Other)** pairs a profit target and a "
        "stop-loss on a single position; whichever fills first cancels "
        "the other. Standard exit-bracket pattern: enter at market, "
        "set OCO with target above and stop below the entry."
    ),
    "limit order": (
        "A **limit order** executes only at your specified price or "
        "better. *Buy LIMIT INFY at ₹1,420* fills at ≤₹1,420; if the "
        "price never trades there, the order rests until cancelled or "
        "expires at end-of-day."
    ),
    "market order": (
        "A **market order** executes immediately at the best available "
        "price. Fastest fill but no price guarantee — use for high-"
        "liquidity stocks during market hours."
    ),
    "stop loss": (
        "A **stop-loss order** triggers a market or limit order when "
        "the stock crosses your stop price. Used to cap downside. In "
        "Zerodha you set it as SL or SL-M (market on trigger). Pivot "
        "stores the trigger and submits the order when price hits it."
    ),
    "sl-m": (
        "**SL-M (Stop-Loss Market)** is a Zerodha order that triggers "
        "a market order when the stock hits your trigger price. Faster "
        "fill than SL (which requires a limit price too) but no price "
        "guarantee on slippage."
    ),
    # Investment vehicles
    "sip": (
        "**SIP (Systematic Investment Plan)** is a recurring fixed-"
        "amount investment, typically monthly, into a mutual fund or "
        "ETF. Pivot supports SIPs in liquid ETFs (NIFTYBEES, BANKBEES, "
        "LIQUIDBEES) — set the amount, frequency, and Pivot places the "
        "buy on each trigger date."
    ),
    "sgb": (
        "**SGB (Sovereign Gold Bond)** is a government-issued bond "
        "denominated in grams of gold, paying ~2.5% annual interest in "
        "addition to gold-price appreciation. 8-year tenor with a 5-"
        "year exit window. Capital gains on holding to maturity are "
        "tax-exempt — the most tax-efficient way to own gold in India."
    ),
    "fd": (
        "**FD (Fixed Deposit)** is a bank deposit at a guaranteed "
        "interest rate for a fixed tenor. Indian FDs typically pay "
        "5.5–7.5% pre-tax. Interest is fully taxable at slab rate, so "
        "the post-tax yield for higher tax brackets is often below "
        "inflation."
    ),
    "etf": (
        "An **ETF (Exchange-Traded Fund)** holds a basket of "
        "securities and trades on the exchange like a stock. Indian "
        "ETFs include NIFTYBEES (Nifty 50), BANKBEES (Bank Nifty), "
        "LIQUIDBEES (overnight cash), GOLDBEES (gold). Lower expense "
        "ratios than active mutual funds; settle T+1."
    ),
    "mutual fund": (
        "A **mutual fund** pools money from many investors into a "
        "professionally-managed portfolio. Bought/sold at end-of-day "
        "NAV (not intraday). Major categories: equity, debt, hybrid, "
        "liquid. Direct plans cost ~1% less than regular plans annually."
    ),
    # Tax / regulatory
    "stcg": (
        "**STCG (Short-Term Capital Gains)** tax applies to equities "
        "sold within 12 months of purchase. Currently 20% in India "
        "(post Budget 2024). Equity STCG is taxed under section 111A "
        "regardless of your income slab."
    ),
    "ltcg": (
        "**LTCG (Long-Term Capital Gains)** tax applies to equities "
        "sold after holding for more than 12 months. Currently 12.5% "
        "(post Budget 2024) on gains above ₹1.25 lakh per financial "
        "year. Below that exemption, LTCG is tax-free."
    ),
    "stt": (
        "**STT (Securities Transaction Tax)** is a small charge "
        "levied on every equity buy/sell on Indian exchanges. "
        "Currently 0.1% per side on delivery, lower on intraday. STT "
        "is what makes Indian equity LTCG tax-eligible without "
        "indexation benefit."
    ),
    "demat": (
        "A **demat account** holds shares electronically (in "
        "dematerialised form) with a depository (NSDL or CDSL). "
        "Required to trade Indian equities. Pivot routes through "
        "Zerodha, which provides the demat + trading account combo."
    ),
    # Product types
    "cnc": (
        "**CNC (Cash and Carry)** is the Zerodha product code for "
        "delivery-based equity trades — shares settle to your demat "
        "and you can hold indefinitely. No leverage. Default for "
        "*buy/sell* without an explicit intraday flag."
    ),
    "mis": (
        "**MIS (Margin Intraday Square-off)** is the Zerodha product "
        "code for intraday-only trades. You get up to ~5x leverage "
        "but the position MUST be squared off the same day or it gets "
        "auto-squared at 3:20 PM IST. No demat delivery."
    ),
    # Indices
    "nifty": (
        "**NIFTY 50** is the benchmark NSE index of the 50 largest "
        "Indian companies by free-float market cap. It is an INDEX, "
        "not a tradeable instrument — to invest in it use the ETF "
        "**NIFTYBEES** which tracks NIFTY 50. Pivot agents use NIFTY "
        "for index level checks and gap conditions."
    ),
    "sensex": (
        "**SENSEX** is the BSE benchmark index of the 30 largest "
        "Indian companies by free-float market cap. Similar role to "
        "NIFTY 50 but on BSE. Tradeable via ETFs that track it."
    ),
    "banknifty": (
        "**BANKNIFTY** is the NSE index of 12 large Indian banks. "
        "Highly liquid options market in India. Tradeable via the "
        "ETF **BANKBEES**. Used by F&O traders for sector exposure."
    ),
    # Misc
    "pe ratio": (
        "**P/E Ratio (Price to Earnings)** is the stock price divided "
        "by trailing 12-month earnings per share. A measure of how "
        "much investors pay per ₹1 of profit. Indian large-caps "
        "typically range 15–35; broader market median is around 22."
    ),
    "pb ratio": (
        "**P/B Ratio (Price to Book)** is the stock price divided by "
        "book value per share. Often used for banks and asset-heavy "
        "businesses where earnings can be volatile but book value is "
        "more stable."
    ),
    "roe": (
        "**ROE (Return on Equity)** = Net income ÷ shareholders' "
        "equity. Measures how efficiently a business uses its equity "
        "capital to generate profit. Quality Indian businesses "
        "typically sustain ROE above 15%."
    ),
    "dividend yield": (
        "**Dividend yield** = Annual dividend per share ÷ stock "
        "price. The cash return you'd get from holding the stock at "
        "the current price, before capital gains. Indian dividends "
        "are taxed at slab rate."
    ),
    "muhurat trading": (
        "**Muhurat trading** is a one-hour symbolic trading session "
        "held on Diwali evening on NSE/BSE. Considered auspicious "
        "for new investments. Pivot's market-relative-time triggers "
        "automatically handle muhurat days alongside regular sessions."
    ),
    # Performance / risk metrics
    "sharpe ratio": (
        "**Sharpe Ratio** = (portfolio return − risk-free rate) ÷ "
        "standard deviation of returns. Measures excess return per "
        "unit of total risk. Above 1 is decent, above 2 is good, "
        "above 3 is rare."
    ),
    "sortino ratio": (
        "**Sortino Ratio** is like Sharpe but uses only downside "
        "deviation in the denominator — penalises losses, ignores "
        "upside volatility. Often preferred for asymmetric "
        "strategies where upside vol is desirable."
    ),
    "cagr": (
        "**CAGR (Compound Annual Growth Rate)** is the geometric "
        "annualised return that takes a starting value to an ending "
        "value over N years: (End / Start)^(1/N) − 1. Used for "
        "comparing investments across different holding periods."
    ),
    "xirr": (
        "**XIRR (Extended Internal Rate of Return)** is the "
        "annualised return that discounts irregular cash flows back "
        "to a present value. Standard for SIP performance because "
        "it correctly handles uneven contribution dates."
    ),
    "alpha": (
        "**Alpha** is the excess return of a portfolio over its "
        "benchmark after adjusting for market risk (beta). Positive "
        "alpha = manager added value beyond the market move; "
        "negative alpha = underperformed expected risk-adjusted return."
    ),
    "beta": (
        "**Beta** measures a stock's volatility relative to the "
        "broader market. Beta = 1 moves with NIFTY; beta > 1 is "
        "more volatile; beta < 1 less. Indian large-caps typically "
        "0.7–1.3; small-caps often above 1.5."
    ),
    "drawdown": (
        "**Drawdown** is the peak-to-trough decline of a portfolio "
        "before a new high is reached, expressed as a percentage. "
        "Max drawdown is the worst peak-to-trough loss in a given "
        "window — a key risk metric for backtests."
    ),
    "volatility": (
        "**Volatility** is the standard deviation of returns over a "
        "window (typically annualised). NIFTY 50's long-run "
        "annualised vol is ~16–22%; mid-caps sit higher, FMCG names "
        "lower."
    ),
    # F&O concepts
    "call option": (
        "A **call option** gives the buyer the right (not obligation) "
        "to buy the underlying at the strike price before expiry. "
        "Used for bullish bets or hedging short positions. F&O isn't "
        "wired into Pivot v1 — orders go through cash equity only."
    ),
    "put option": (
        "A **put option** gives the buyer the right to sell the "
        "underlying at the strike price before expiry. Used for "
        "bearish bets or hedging long positions. F&O not wired in "
        "Pivot v1."
    ),
    "strike price": (
        "**Strike price** is the fixed price at which the option "
        "buyer can exercise — buy (call) or sell (put). Strikes are "
        "listed at fixed intervals around the spot price; ATM = at "
        "the money, ITM = in the money, OTM = out of the money."
    ),
    "premium": (
        "**Premium** is the price paid by the option buyer to the "
        "seller for the contract. It's the option's market price; "
        "in F&O accounting, the buyer's max loss is the premium paid, "
        "the seller's max gain is the premium received."
    ),
    "moneyness": (
        "**Moneyness** describes where the option's strike sits "
        "relative to spot: ATM (≈ spot), ITM (profitable to exercise), "
        "OTM (not profitable to exercise). Affects delta, premium, "
        "and the probability of ending in-the-money at expiry."
    ),
    "delta": (
        "**Delta** is an option's sensitivity to a ₹1 change in the "
        "underlying. ATM calls have delta ≈ 0.5; deep-ITM calls "
        "approach 1; OTM calls approach 0. Also doubles as the "
        "approximate probability of finishing in-the-money."
    ),
    "gamma": (
        "**Gamma** is the rate of change of delta per ₹1 move in the "
        "underlying. Highest near ATM and near expiry. High gamma "
        "means delta swings fast — risky for short-option positions "
        "in fast-moving markets."
    ),
    "theta": (
        "**Theta** is the rate of premium decay per day, holding "
        "everything else constant. Long-option holders pay theta; "
        "short-option sellers collect it. Accelerates near expiry — "
        "the basis for short-vol expiry-week strategies."
    ),
    "vega": (
        "**Vega** is the change in option premium per 1% change in "
        "implied volatility. Long-vol positions (long straddles, "
        "long strangles) have positive vega; short-vol the opposite. "
        "Vega is highest for ATM options far from expiry."
    ),
    "implied volatility": (
        "**Implied Volatility (IV)** is the volatility number the "
        "market is pricing into option premiums right now (back-"
        "solved from the option price). High IV = expensive premiums; "
        "low IV = cheap. India VIX is the NIFTY 50 implied-vol index."
    ),
    "vix": (
        "**India VIX** is NSE's volatility index, derived from NIFTY "
        "50 option prices. Reads above 25 signal stress; below 12 "
        "signal complacency. Often spikes on event days "
        "(Budget, election, Fed)."
    ),
    # Candlestick patterns
    "doji": (
        "A **doji** is a candle where open and close are nearly "
        "equal — a small or zero body with wicks on both sides. "
        "Signals indecision; appearing after a sustained trend is "
        "often interpreted as a reversal warning, not confirmation."
    ),
    "hammer": (
        "A **hammer** is a single-candle pattern with a small body "
        "near the top and a long lower wick (typically 2× the body). "
        "Forms after a downtrend; classical bullish-reversal signal "
        "when the next candle confirms with a higher close."
    ),
    "engulfing pattern": (
        "An **engulfing pattern** is two candles where the second's "
        "body fully engulfs the first's body. Bullish engulfing "
        "(green engulfs red) at the bottom of a downtrend hints at "
        "reversal; bearish engulfing the inverse."
    ),
    # NSE / BSE / Indian-market specifics
    "ucc": (
        "**UCC (Unique Client Code)** is the broker-issued ID that "
        "identifies a trader to the exchange. Required on every "
        "order. Zerodha auto-generates UCCs for active accounts."
    ),
    "isin": (
        "**ISIN (International Securities Identification Number)** "
        "is the 12-character globally-unique code for a security. "
        "Indian stocks start with `IN`. Demat shares are tracked by "
        "ISIN, not by ticker."
    ),
    "lot size": (
        "**Lot size** is the minimum tradeable quantity for an F&O "
        "contract. Set by the exchange per underlying — NIFTY 50 = "
        "75, BANKNIFTY = 35, RELIANCE futures = 250 (as of 2025). "
        "Cash-equity has no lot size; you can trade 1 share."
    ),
    "circuit limit": (
        "**Circuit limit** is the max % a stock can move in a single "
        "session. Hit the upper circuit (UC) → no more buy orders "
        "can fill; lower circuit (LC) → no more sell orders. Limits "
        "vary by stock category (5%, 10%, 20%)."
    ),
    "asm": (
        "**ASM (Additional Surveillance Measure)** is an NSE/BSE "
        "framework that puts unusually-volatile stocks under tighter "
        "monitoring (margins, price bands). Long-term ASM is more "
        "restrictive; short-term ASM is a temporary watchlist."
    ),
    "gsm": (
        "**GSM (Graded Surveillance Measure)** flags stocks with "
        "weak fundamentals, low liquidity, or unusual price-volume "
        "behaviour. Adds margin, T2T trading, and exit-only "
        "restrictions in escalating stages."
    ),
    "t2t": (
        "**T2T (Trade-to-Trade) segment** removes intraday trading — "
        "every buy MUST settle to demat (T+1), every sell MUST come "
        "from demat. Used for stocks with low free-float or "
        "surveillance flags."
    ),
    # Hindi / Hinglish
    "kharido": (
        "**Kharido** — Hindi/Hinglish for *buy*. Pivot understands "
        "Hinglish phrasing in chat; *\"10 reliance kharido\"* will "
        "be parsed as a buy order."
    ),
    "becho": (
        "**Becho** — Hindi/Hinglish for *sell*. Pivot understands "
        "*\"5 INFY becho\"* as a sell order."
    ),
    "bhav": (
        "**Bhav** — Hindi for *price* / *rate*. *\"TCS ka bhav kya "
        "hai\"* = *\"what's TCS price\"*. Pivot resolves this to a "
        "live-price query."
    ),
    "muhurat": (
        "**Muhurat** — auspicious time for new investments in Indian "
        "tradition. Most commonly refers to muhurat trading on "
        "Diwali evening (NSE/BSE hold a 1-hour symbolic session)."
    ),
    "lakh": (
        "**Lakh** = 100,000 in the Indian numbering system. Written "
        "as `1,00,000` (note Indian comma placement). Pivot accepts "
        "*\"₹1 lakh\"* / *\"100000\"* / *\"1L\"* interchangeably."
    ),
    "crore": (
        "**Crore** = 10 million = 1,00,00,000 in Indian numbering. "
        "Pivot accepts *\"₹1 crore\"* / *\"1cr\"* / *\"10000000\"*."
    ),
    # General investing terms
    "diversification": (
        "**Diversification** spreads capital across uncorrelated "
        "assets so a single bad outcome doesn't sink the portfolio. "
        "In Indian equity, that typically means across sectors "
        "(IT + Banking + FMCG + Energy + Auto), market caps "
        "(large + mid + small), and asset classes (equity + debt + gold)."
    ),
    "rebalancing": (
        "**Rebalancing** is the periodic act of restoring a "
        "portfolio's target weights as market moves drift them. "
        "Quarterly or annual cadence is common. Pivot supports a "
        "rebalancing automation — set target weights and the agent "
        "trims and tops up to match."
    ),
    "asset allocation": (
        "**Asset allocation** is the split of capital across "
        "equity, debt, gold, and cash. The single largest determinant "
        "of long-term portfolio outcome — far more than stock "
        "selection. A 60/40 equity-debt split is a common Indian "
        "balanced allocation."
    ),
    "risk-free rate": (
        "**Risk-free rate** is the return on a riskless instrument — "
        "typically the 10-year Indian G-Sec yield or the 91-day "
        "T-bill. Used as the baseline in Sharpe / alpha "
        "calculations. Currently in the 6–7% range."
    ),
    "yield to maturity": (
        "**YTM (Yield to Maturity)** is the total return a bond "
        "investor earns if held to maturity, accounting for "
        "coupon income + capital gain/loss to par. The standard "
        "way to compare bonds with different prices and coupons."
    ),
    "expense ratio": (
        "**Expense ratio** is the annual fee a mutual fund / ETF "
        "charges as a % of assets. Direct equity MF plans typically "
        "0.3–1.0%; regular plans add ~1% commission. Indian ETFs "
        "are cheapest at 0.05–0.5%."
    ),
    "exit load": (
        "**Exit load** is the fee charged when you redeem mutual "
        "fund units before a holding-period threshold (commonly "
        "1% if redeemed within 1 year for equity funds). Liquid "
        "and overnight funds usually have no exit load."
    ),
}


# Definition-shape regex. The query must normalise to ONE of:
#   what is X         what's X        whats X
#   explain X         define X
#   X meaning         what does X mean
#   meaning of X      what is X?
# Anything else (e.g. "what is RSI for TCS") falls through to the LLM.
_DEFINITION_RE = re.compile(
    r"^(?:"
    r"what(?:'s|\s+is|s)\s+(?:an?\s+|the\s+)?(.+?)"
    r"|explain\s+(?:an?\s+|the\s+)?(.+?)"
    r"|define\s+(?:an?\s+|the\s+)?(.+?)"
    r"|(.+?)\s+(?:meaning|definition)"
    r"|meaning\s+of\s+(.+?)"
    r"|what\s+does\s+(.+?)\s+mean"
    r")\s*\??$",
    re.IGNORECASE,
)


def _try_definition(normalized: str) -> Optional[str]:
    """If the message asks for a definition of a known term, return it.

    Conservative: only the curated terms in `_DEFINITIONS` match. This
    avoids accidentally answering "what is the best stock to buy" with
    a generic stub.
    """
    m = _DEFINITION_RE.match(normalized)
    if not m:
        return None
    term = next((g for g in m.groups() if g), "")
    term = term.strip().lower()
    # Strip a few leading filler words that escape the regex anchor.
    for filler in ("a ", "an ", "the "):
        if term.startswith(filler):
            term = term[len(filler):]
    if not term:
        return None
    # Direct hit
    if term in _DEFINITIONS:
        return _DEFINITIONS[term]
    # Light alias normalisation (handle "rsi indicator" → "rsi", etc.)
    for alias_suffix in (" indicator", " ratio", " order", " bond",
                         " account", " trading", " fund", " plan"):
        if term.endswith(alias_suffix):
            base = term[: -len(alias_suffix)]
            if base in _DEFINITIONS:
                return _DEFINITIONS[base]
    return None


def try_fast_path(message: str) -> Optional[str]:
    """Return a canned response if the message is purely conversational
    or a definition-shape question for a known term.

    None means "send to the LLM". Latency is microseconds; the function
    is safe to call on every chat turn.
    """
    n = _normalize(message)
    if not n:
        return None

    if _matches_phrase(n, _GREETINGS):
        return _GREETING_REPLY
    if _matches_phrase(n, _THANKS):
        return _THANKS_REPLY
    if _matches_phrase(n, _HELP_QUERIES):
        return _HELP_REPLY
    if _matches_phrase(n, _CONTINUATION_QUERIES):
        return _CONTINUATION_REPLY
    edu = _try_definition(n)
    if edu is not None:
        return edu

    return None
