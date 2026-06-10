# GOLD — quality-stress (GAN round 3)

Category = analysis/compare/screen/valuation/income asks where **output quality is the bar**: the right
tool calls almost always happen now; what separates 6.5 from 9 is structure, vs-own-history context,
coherent rank→pick logic, trajectory (not snapshot) answers, and dated source tags.

External bar (web-researched): the best Indian retail research surfaces — Tijori ("vs own history"
multiples, peer comparisons), Trendlyne (DVM composite scores, dividend history pages), Tickertape
(investment scorecard), Screener.in (ranked query tables) — all share four things Pivot's current
answers still partially lack: (a) **valuation framed against the stock's own 5y history**, not just an
absolute multiple; (b) **a composite score that makes the rank and the pick the same thing**;
(c) **dividend/earnings trajectory as a time series**, not a point-in-time read; (d) **every number
carries a dated as-of**. Sources: [Tijori review](https://wealthwiseguide.com/tijori-finance-review/),
[Winvesta 2026 tools guide](https://www.winvesta.in/blog/investors/fundamental-analysis-tools-and-screeners-2026-guide),
[Trendlyne ITC dividend history](https://trendlyne.com/equity/Dividend/ITC/647/itc-ltd-dividend/),
[Simply Wall St ITC dividend](https://simplywall.st/stocks/in/food-beverage-tobacco/nse-itc/itc-shares/dividend).

R3 snapshot note: all six quality-stress sessions now ship sectioned, tabled answers with defended
views (`analyse_hdfcbank_full`, `is_reliance_expensive`, `infy_vs_tcs_which_better` are near-bar).
Residuals this gold targets: screen rank-vs-pick incoherence (`screen_cheap_high_roe_banks` ranks
P/B-ascending then crowns rank-4 ICICIBANK), `itc_dividend_story` answering a trajectory ask with a
static snapshot, undated `(yfinance, EOD)` tags, the NIFTY answer's filler "Fundamentals: not the
right lens" section, and zero vs-own-history context anywhere.

All numbers below marked `<live>` come from real tool calls at answer time (Kite primary, yfinance
fallback with a dated tag). Nothing is fabricated; where a series Pivot cannot fetch, the gold answer
says so explicitly.

---

## Ideal answers

### 1. `infy_vs_tcs_which_better`
**User:** "INFY vs TCS for a 1 year hold - which is better? give me the real comparison, not a both-are-good answer"

**Widget:** none required (comparison is the text itself); if a compare card exists, it carries the
same per-metric rows as the tables below. Data: `compare_performance` + `fetch_fundamentals` (both
names, same as-of).

**Ideal reply:**

> ## Verdict
>
> **INFY — and not by a small margin on the dimension that matters for a 1-year hold.** Over the
> last year INFY lost <live −24.96%> vs TCS's <live −37.14%>, with a better Sharpe
> (<live −1.18> vs <live −2.05>) and a shallower max drawdown (<live 35.2%> vs <live 38.8%>).
> TCS is the better *business* (ROE <live 48.4%> vs <live 31.4%>); INFY has been the better
> *stock*, and a 1-year horizon is short enough that tape behaviour and entry multiple dominate.
>
> ## Head-to-head — price behaviour (1Y)
>
> | Metric | INFY | TCS | Edge |
> |---|---:|---:|---|
> | 1Y total return | <live −24.96%> | <live −37.14%> | **INFY** |
> | Annualised volatility | <live 26.7%> | <live 24.3%> | TCS (slightly calmer) |
> | Sharpe (1Y) | <live −1.18> | <live −2.05> | **INFY** |
> | Max drawdown | <live −35.2%> | <live −38.8%> | **INFY** |
> | vs SMA200 | <live −x.x%> | <live −y.y%> | <live whichever is closer to repair> |
>
> ## Head-to-head — business quality & valuation
>
> | Metric | INFY | TCS | Edge |
> |---|---:|---:|---|
> | P/E | <live 15.41> | <live 15.82> | INFY (marginal) |
> | P/B | <live 5.08> | <live 6.86> | **INFY** |
> | ROE | <live 31.44%> | <live 48.40%> | **TCS** |
> | Net margin | <live 16.4%> | <live 18.4%> | TCS |
> | Dividend yield | <live 4.21%> | <live 5.76%> | TCS |
> | D/E | <live 0.10> | <live 0.10> | tie |
>
> ## Why INFY wins the 1-year frame
>
> The two trade at almost the same earnings multiple (<live ~15.4x> vs <live ~15.8x>), so you are
> **not being paid a discount to own the weaker tape**. TCS's superior ROE and margins are real, but
> at par P/E that quality is free either way — and the market has been punishing TCS harder for
> <live n> straight quarters. For a 12-month hold, the better entry is the name with the smaller
> drawdown, better risk-adjusted return, and cheaper book multiple: INFY.
>
> **The honest case for TCS instead:** if the IT down-cycle turns, the highest-ROE franchise usually
> re-rates fastest, and TCS pays you <live 5.76%> in dividends to wait. That is a recovery bet, not
> a 1-year base case.
>
> **What would change my mind:** TCS closing the 1Y performance gap to within ~5 points while
> holding its ROE edge, or INFY's P/E premium to TCS opening beyond ~2 turns.
>
> *Data: Kite (live) / <yfinance, EOD as of <live 2026-06-09>> where tagged. This is analysis, not
> financial advice.*

**Why this is the bar:** verdict box first; two tables with an explicit per-row **Edge** column; the
"same multiple, different tape → quality is free" argument is the kind of synthesis a research note
gives and a metric dump doesn't; the counter-case is named and bounded; the as-of is dated.

---

### 2. `screen_cheap_high_roe_banks`
**User:** "screen me the cheap high-ROE banks - rank ICICIBANK, KOTAKBANK, SBIN and AXISBANK"

**Widget:** none required; screen table is the payload. Data: `fetch_fundamentals` × 4.

**Ideal reply (fixes the rank-vs-pick incoherence with a composite Score the rank actually uses):**

> ## Screen: cheap + high-ROE, 4 private/PSU banks
>
> Banks are judged on **P/B (cheapness)** and **ROE (quality)** — P/E is secondary for lenders. To
> make the ranking honest I score each name on both halves of your ask: **Score = ROE ÷ P/B**
> (return on book per unit of price paid for that book). The rank below *is* the recommendation
> order — no separate "but actually" pick.
>
> | Rank | Bank | P/B | ROE | ROE÷P/B Score | P/E | Read |
> |---|---|---:|---:|---:|---:|---|
> | 1 | **SBIN** | <live 1.56> | <live 15.48%> | <live 9.9> | <live 10.99> | Cheapest book AND near-top ROE — the screen's true winner |
> | 2 | ICICIBANK | <live 2.52> | <live 16.36%> | <live 6.5> | <live 17.06> | Best raw quality, but you pay <live 62%> more per unit of book than SBIN |
> | 3 | AXISBANK | <live 1.87> | <live 12.27%> | <live 6.6> | <live 14.29> | Cheap-ish, middling ROE — the in-betweener |
> | 4 | KOTAKBANK | <live 2.10> | <live 11.39%> | <live 5.4> | <live 19.70> | Lowest ROE at the second-richest multiple — fails both halves |
>
> *(AXISBANK and ICICIBANK swap on rounding — <live 6.6> vs <live 6.5>; ICICIBANK ranks ahead on
> the ROE tiebreak.)*
>
> ## The call
>
> **SBIN is the cheap high-ROE bank in this set, and it isn't close on the composite**: you get
> <live 15.5%> ROE at <live 1.56x> book, i.e. the most earning power per rupee of book value. The
> usual objection — PSU discount, cyclical credit costs — is exactly *why* it screens cheap; the
> screen can't tell you whether that discount closes.
>
> - **If you want quality first, cheapness second:** ICICIBANK — highest ROE (<live 16.36%>),
>   reasonable multiple, the "sleep well" pick.
> - **Avoid on this screen:** KOTAKBANK — <live 2.10x> book for the lowest ROE in the set is the
>   wrong side of both filters today (its historical premium was earned at ~13-14% ROE, which it
>   currently isn't printing).
>
> **What would change the ranking:** SBIN's ROE slipping under ~13% (credit-cycle turn) or
> KOTAKBANK's ROE rebuilding above 14% — re-run this screen after results season.
>
> *Fundamentals: Moneycontrol DB / <yfinance fallback, as of <live 2026-06-09>>. This is analysis,
> not financial advice.*

**Why this is the bar:** the composite Score column makes rank #1 and "the pick" the same row — the
exact incoherence flagged in R3 (`SBIN ranked 1, ICICIBANK crowned`) is structurally impossible
here; the per-row Read explains the rank; the screen's epistemic limit ("can't tell you whether the
discount closes") is stated instead of overclaimed.

---

### 3. `itc_dividend_story`
**User:** "is ITC still a solid dividend play after the demerger, what's the yield actually doing"

**Widget:** none required. Data: `fetch_fundamentals` + `get_live_price` + `get_price_history`
(1y, to compute then-price) + `get_symbol_news`. "What's the yield *actually doing*" is a
**trajectory** ask — the answer must show ≥2 timepoints, not a snapshot.

**Ideal reply:**

> ## Verdict
>
> **Yes — ITC is still a real dividend play, and the yield has actually *risen* since the demerger,
> but for the unflattering reason: the price fell faster than the payout.** Current yield is
> <live 5.73%> on a ₹<live 23.00>/share trailing dividend at ₹<live 280.00>.
>
> ## The yield trajectory — then vs now
>
> | | ~1 year ago | Today (<live 2026-06-09>) | Direction |
> |---|---:|---:|---|
> | Price | ₹<live 412> | ₹<live 280.00> | <live −32%> |
> | Trailing DPS | ₹<live 13.75 + special> | ₹<live 23.00> | <live up/flat> |
> | Implied yield | <live ~3.3%> | <live 5.73%> | **up <live ~2.4pp>** |
> | Payout ratio | <live ~84%> | <live 86.92%> | drifting higher |
>
> *(Then-price from the 1y price series; trailing DPS from the fundamentals feed. I don't have a
> per-payment dividend calendar in-session — the DPS split between ordinary and special/demerger-
> linked payouts is the one thing to verify on the company's investor page before treating the full
> ₹<live 23.00> as repeatable.)*
>
> So the yield is "doing" two things at once: **expanding optically** because the stock de-rated
> ~<live 32%>, and **standing on a payout ratio of <live 86.92%>** — ITC is already distributing
> almost everything it earns. That means future yield growth must come from **earnings growth, not
> payout expansion**. There is no headroom lever left.
>
> ## Can the dividend hold?
>
> | Support | Value | Read |
> |---|---:|---|
> | ROE | <live 29.34%> | Elite — the cash engine is intact post-demerger |
> | D/E | <live 0.03> | Effectively unlevered; no balance-sheet threat to the dividend |
> | P/E | <live 16.95> | You're not overpaying for the income stream |
> | Payout | <live 86.92%> | The constraint — a soft earnings year flows straight into the dividend |
>
> The demerger itself **helped** the yield math: hotels was the capex-hungry, low-ROCE segment;
> what remains (cigarettes + FMCG + agri/paper) is the cash-generative core. Recent headline —
> <live BAT trimming its ITC Hotels stake> — is an overhang for the *hotels* entity, not for ITC's
> distributable cash.
>
> ## View
>
> **Solid yield-and-stability play; weak dividend-growth play.** Bull: <live 5.7%> yield, ~0 debt,
> <live 29%> ROE, demerger removed the capital sink. Bear: 87% payout means DPS growth is capped at
> EPS growth (mid-single-digit lately), and the high yield partly reflects a de-rated, weak tape.
> If you need the income to *grow* faster than ~<live 5%>/yr, this isn't the instrument; if you
> want a high, well-covered current yield, it still qualifies.
>
> **What would change my mind:** payout pushed above ~95% or cigarette-volume-led EPS decline —
> either turns the yield from "high" to "at risk".
>
> *Data: Kite (live) / Moneycontrol / <yfinance, EOD as of <live 2026-06-09>>. This is analysis,
> not financial advice.*

**Why this is the bar:** the trajectory table answers the literal question ("what's the yield
*doing*") with then-vs-now math derived from data Pivot genuinely has (1y price series + current
DPS/yield); the de-rating-vs-payout decomposition is the insight a static snapshot can't give; the
one series Pivot lacks (per-payment dividend calendar) is named honestly instead of papered over.

---

## Rubric

Score each quality-stress answer 0-10; weights sum to 100. A judge can check every criterion
mechanically against the response text + tools_called + card_digest.

| # | Criterion | Weight | PASS check |
|---|---|---:|---|
| 1 | **Real, dated numbers** | 20 | Every quantitative claim traces to a tool value in the turn payload; the source tag includes an explicit as-of date when on fallback ("yfinance, EOD as of YYYY-MM-DD" — undated "(yfinance, EOD)" loses half marks); zero fabricated multiples/targets/DPS. |
| 2 | **Verdict-first, defended, unhedged** | 20 | First section states the pick/call in one bolded sentence with ≥2 supporting numbers; a bull AND bear case both appear; a falsifiable "what would change my mind" line is present; "both are good"/"depends on your view" anywhere = FAIL this row. |
| 3 | **Rank↔pick coherence (screens/compares)** | 15 | If the answer ranks N names, the stated rank key (or an explicit composite Score column) makes row #1 = the recommended name; recommending a row other than #1 without a printed composite column = FAIL. N/A for single-name asks (reweight to #2). |
| 4 | **Question-shape fidelity** | 15 | Trajectory/"actually doing"/"after X" asks show ≥2 timepoints in a then-vs-now table; "is X expensive" asks frame the multiple against own history or an explicit peer/anchor — or state plainly that the historical-multiple series isn't available; a static snapshot relabelled as a trajectory = FAIL. |
| 5 | **Mandatory tables + structure** | 15 | ≥1 markdown table for any analysis; compares get per-metric side-by-side with an Edge/winner column; screens get a ranked table with a per-row read; sections (Snapshot/Verdict, evidence, What to watch, View) present; no filler section ("Fundamentals: not applicable" padding loses 5). |
| 6 | **Depth floor with density** | 10 | ≥350 words for a full single-name analysis / ≥250 for a screen; every table row carries an interpretation, not just a value; at least one *synthesis* claim that combines two metrics (e.g. "par P/E means the quality is free", "yield rose because price fell"). |
| 7 | **Honest bounds, no drift** | 5 | Missing fields named ("ROCE unavailable"), capability limits stated in-answer; ends "analysis, not financial advice"; never morphs into an unrequested order/automation or execution-theatre phrasing. |

---

## Probes

### probe_pe_vs_own_history
A great copilot frames "expensive?" against the stock's **own 5-year average multiple** (the Tijori
/Trendlyne standard). Pivot's `fetch_fundamentals` returns only a current-snapshot P/E — there is no
historical-multiples series — and the R3 `is_reliance_expensive` answer never references own-history.
Suspected failure mode: either fabricates/eyeballs a "historical average P/E" (criterion-1 FAIL) or
silently answers a different question. The gold behaviour is the honest bound + nearest real thing:
current P/E, price-vs-SMA200 as the available own-history proxy, and a plain "I don't have a
historical P/E series in-session" line.

Turns:
1. "is TCS trading above or below its own 5-year average P/E right now? how big is the gap"
2. "fine, then use whatever history you DO have to tell me if it's cheaper than usual"

### probe_itc_yield_then_vs_now
Directly probes the open `itc_dividend_story` residual (static snapshot for a trajectory ask) with
the trajectory demand made explicit and in Hinglish, plus a forward-risk follow-up that requires
reasoning from the payout ratio rather than re-quoting it. Suspected failure: turn 1 re-renders the
same point-in-time table without a then-price fetch from the 1y series; turn 2 re-states "payout is
86.92%" without converting it into the EPS-sensitivity argument.

Turns:
1. "ITC ka dividend yield ek saal pehle ke comparison mein improve hua hai ya nahi? then vs now table bana ke dikhao"
2. "ok and agle saal yield drop hone ka realistic risk kya hai - kis number pe nazar rakhun?"
