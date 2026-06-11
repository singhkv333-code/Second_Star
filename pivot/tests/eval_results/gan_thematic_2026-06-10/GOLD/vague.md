# GOLD STANDARD — class: `vague`

Class: open-ended, zero-spec asks — "I want to make money", "what should I buy",
"help me invest", "give me a good strategy", "where do I start".

**The bar:** Pivot must give VALUE FIRST. A vague prompt is not a license to
interrogate. GREAT = a brief honest reframe (no return guarantees, not
personalised advice), then 2–3 CONCRETE starting paths each with a one-line
"what it looks like" using real Indian instruments and real numbers, plus AT
LEAST ONE tappable artifact rendered on turn 1 (a prefilled SIP
`workflow_draft_card` is the default), and only THEN one sharp compound
narrowing question (horizon + risk + capital). FAIL = pure-text interrogation,
`ask_user` punt with no widget, or a generic finance lecture with no instrument
named.

Observed live baseline (2026-06-10): "I want to make money, what should I do"
got competent text (no-guarantees framing, SIP-vs-rules routes, asks
horizon/risk) but **NO widget, no prefilled card, no immediate value**. That is
the residual this gold targets.

---

## Exemplar 1

### Prompt
> I want to make money. What should I do?

### Ideal answer

**Widget:** `workflow_draft_card` — prefilled, editable, NOT armed:
- Trigger: schedule, monthly (1st trading day of month, 09:30 IST)
- Action: buy **NIFTYBEES** worth **₹5,000** (qty computed at fill from live price)
- Mode: paper / register-not-execute, status: draft

**Assistant text** (~250–350 words, headers + one markdown table):

---

Nobody can promise you returns — anyone who does is selling something. What I
*can* do is show you the three honest ways retail investors in India actually
compound money, give you one ready to tap right now, and then tune it to you.

#### Three real starting paths

| Path | What it looks like | Effort | Typical risk |
|---|---|---|---|
| **Index SIP** | ₹5,000/month into NIFTYBEES (NIFTY 50 ETF, ~₹281/unit) — own the top 50 companies, every month, no timing | Zero after setup | Market risk only; NIFTY's long-run CAGR has been ~12–13%, with 30%+ drawdown years on the way |
| **Rules-based entries** | An automation like "buy 10 INFY when RSI(14) < 30, exit at +8% or −4%" — discipline without screen-watching | Low; I build + paper-trade it | Single-stock risk; needs a backtest first |
| **Quality/dividend basket** | A screen for ROE > 15%, dividend yield > 2%, low debt → 4–6 names like ITC, Coal India held long | Quarterly review | Lower churn; sector concentration risk |

#### Start here, right now

I've drafted the simplest one below — a **₹5,000/month NIFTYBEES SIP** — as an
editable card. Change the amount, the instrument (JUNIORBEES for midcap-ish
NIFTY Next 50, GOLDBEES to add gold), or the date. It registers the order each
month for you to confirm in your broker app; nothing executes on its own. You
can also run it in **paper mode** first to watch it work with zero money at risk.

#### To sharpen this for you

One question, three parts: roughly **how much can you invest monthly, for how
many years, and could you stomach seeing it down 30% in a bad year without
selling?** Your answers decide the split between the index SIP and the
higher-risk paths.

*This is analysis and a framework, not personalised financial advice.*

---

**Numbers style:** the NIFTYBEES unit price MUST come from the quote tool (Kite
primary, yfinance fallback). If both fail (token expired), say "around its last
close" or omit the price — never invent one. CAGR/drawdown figures are framed
as historical ranges, not promises.

---

## Exemplar 2

### Prompt
> what should I buy right now

### Ideal answer

**Widget:** screen-results widget (or stock-snapshot list) from a REAL screen
run — e.g. NIFTY 100 filtered on ROE > 15%, PE below sector median, positive
6-month return — showing 4–6 rows with the actual screened values. If the
screen tool degrades, fall back to a prefilled NIFTYBEES `workflow_draft_card`
and say why.

**Assistant text** (~200–300 words):

---

"Buy right now" has no single answer — it depends on whether you're holding for
a decade or a week, and I won't pretend a hot tip is a strategy. But I can give
you a defensible *shortlist* instead of a guess: here's a live quality screen I
just ran on the NIFTY 100.

#### Quality screen — ROE > 15%, PE < sector median, positive 6-mo momentum

| Stock | CMP | PE | ROE | 6-mo return |
|---|---|---|---|---|
| *(4–6 real rows from the screen tool — every number from tool output)* |

What this screen does and doesn't say: these are financially strong companies
not currently expensive — it says nothing about next week's price. Two ways to
act on it:

1. **Tap any row** for a full snapshot (technicals, fundamentals, news) before
   deciding.
2. **Skip stock-picking entirely** — a ₹X/month NIFTYBEES SIP beats most
   ad-hoc buying; say "set up a SIP" and I'll draft the card.

I can also turn any of these into a **dip-buy automation** ("buy ₹10k of
HDFCBANK if it falls 5% from here") so you buy on weakness instead of FOMO.

**One question to narrow it:** is this money you need back within ~2 years, or
can it sit for 5+? Short horizon changes the answer completely (and mostly away
from single stocks).

*Analysis, not financial advice.*

---

**Hard requirements:** every number in the table is from tool output; the
screen criteria are stated explicitly; no "top picks" language; the SIP
alternative is offered, not forced.

---

## Exemplar 3

### Prompt
> I have 50k, where do I start

### Ideal answer

This prompt carries one concrete fact (capital = ₹50,000) — a great answer
USES it instead of re-asking it.

**Widget:** `workflow_draft_card` — prefilled starter plan:
- Trigger: schedule, monthly
- Action: buy NIFTYBEES ₹4,000/month (with a noted optional GOLDBEES ₹1,000 leg
  if multi-leg is supported; otherwise the card carries the NIFTYBEES leg and
  text mentions the gold variant)
- Mode: paper or register-not-execute, editable

**Assistant text** (~250–350 words):

---

₹50,000 is a real start — enough to build a position, not enough to be
diversifying across 15 stocks. The biggest first-timer mistake is deploying it
all on day one into whatever's trending. Here's a structure that avoids that.

#### A starter split for ₹50,000

| Slice | Amount | Where | Why |
|---|---|---|---|
| Core | ₹30,000 | NIFTYBEES, phased ₹5,000/month over 6 months | Rupee-cost averaging — you don't bet on one entry day |
| Diversifier | ₹10,000 | GOLDBEES (gold ETF) | Historically moves differently from equities |
| Learning capital | ₹10,000 | 1–2 quality large-caps you pick after reading my analysis on them | Skin-in-the-game is how you actually learn |

(If part of this is an emergency fund, keep that out of markets entirely — I
deal in market instruments, not bank products.)

I've drafted the core leg below as a monthly SIP card — edit the amount or
instrument, run it in **paper mode** first if you want to watch it for a month
with zero risk. Orders register for you to confirm in your broker; nothing
fires on its own.

For the learning slice: tell me 2–3 companies you know as a customer (HDFC
Bank? Maruti? Asian Paints?) and I'll run a full analysis — price history,
PE/ROE, recent news — so the pick is informed.

**To tune the split:** what's the horizon for this ₹50k — under 2 years, or
5+? And is more money coming monthly, or is this one-time?

*This is a framework, not personalised advice.*

---

**Anti-pattern this exemplar punishes:** asking "how much do you want to
invest?" when the user already said 50k; recommending products Pivot can't
touch (liquid funds, FDs) as if it could act on them — name them honestly as
out-of-scope if mentioned at all.

---

## RUBRIC (weights sum to 100)

| # | Criterion (concretely checkable) | Weight |
|---|---|---|
| 1 | **Tappable artifact on turn 1.** A real widget renders: a prefilled SIP/automation `workflow_draft_card` (trigger + action + amount populated, editable, draft/not-armed) OR a populated screen widget. `render_hint=ask_user` with no widget = 0 on this criterion. | 25 |
| 2 | **2–3 concrete named paths, each with a one-line "what it looks like".** Real Indian instruments (NIFTYBEES/GOLDBEES/specific NSE names) + real numbers (₹/month, RSI threshold, ROE filter). Generic labels alone ("mutual funds", "blue chips") score 0. | 20 |
| 3 | **Value before interrogation; exactly one narrowing question.** The concrete paths + widget come FIRST; then ONE compound question covering horizon/risk/capital. Multiple question rounds, or questions before any value, fail. Re-asking a fact already given (e.g. ₹50k) fails. | 15 |
| 4 | **Honest reframe, no guarantees, no fake advice.** Opens by deflating "make money" without moralising; returns framed as historical ranges; closes with the analysis-not-advice line; register-not-execute stated where a card is drafted. | 12 |
| 5 | **Data grounding / no fabrication.** Every price, PE, ROE, screen value traces to tool output (Kite primary, yfinance fallback). On data failure: degrade honestly ("can't fetch live price right now"), never invent. Historical CAGR framed as approximate history. | 12 |
| 6 | **Structure + depth.** Headers, at least one markdown table (paths comparison or screen/split table), ~200–350 words of text alongside the widget. A 3-line blurb or an unstructured wall both fail. | 10 |
| 7 | **Forward motion / next-step hooks.** Offers concrete continuations Pivot can actually do: paper mode, edit the card, run an analysis on a named stock, build a dip-buy automation, backtest the rule. No invented capabilities (no debt funds, no auto-execution). | 6 |

---

## CAPABILITY PROBES

Fresh prompts a great copilot should handle in this class — suspected current gaps.

### Probe 1 — risk-averse idle cash (out-of-scope honesty + tiered concrete plan)
> I have 2 lakh sitting idle in my savings account. I'm scared of losing money
> but FD returns feel pathetic. Do something.

GREAT: names the real trade-off (equity = drawdown risk, period), states
honestly that Pivot doesn't handle FDs/debt/liquid funds (out of scope — say so
plainly), then offers the nearest real things: a phased NIFTYBEES SIP card for
only the slice the user can risk (e.g. ₹5k/month from the ₹2L, drafted as a
card), GOLDBEES as a lower-correlation leg, plus a **paper-trading** offer so
they can watch with zero risk before committing — and one question on what
fraction they could see down 20% without panic. SUSPECTED FAILURE: either a
text-only lecture with no card, or scope-blind recommendations of debt products
Pivot cannot render or register.

### Probe 2 — unrealistic-return decode
> make me 1% a day

GREAT: refutes the math head-on without mockery (1%/day compounds to >3,600%/yr
— nothing legitimate does that; anyone offering it is a scam), states the
honest realistic band, then converts the ambition into something testable:
offers to **backtest** an aggressive-but-real strategy (e.g. RSI mean-reversion
on a liquid large-cap) and show its actual daily/annual return profile with
drawdowns — rendering a backtest result/chart so the user sees real numbers vs
fantasy — and closes with the SIP fallback. SUSPECTED FAILURE: either a bare
refusal/lecture with no artifact, or worse, treating "1% a day" as a buildable
target and drafting a workflow that pretends to deliver it.
