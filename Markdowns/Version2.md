# View Markets V2 - Product Specification

## Context

This document specifies the design and philosophy of **View Markets**, a belief-first investing layer within Pivot.

This is a **V2 initiative**.

The platform already supports:

* Strategy and workflow automation
* Agent-based execution
* Auto-trading capabilities
* Backtesting infrastructure

View Markets sits on top of these capabilities and serves as an intelligent layer for discovering and expressing market beliefs.

---

# Core Philosophy

Most investors think in beliefs and opinions rather than financial instruments.

Examples:

* I think RBI cuts rates.
* I think Gold outperforms equities.
* I think India enters a manufacturing upcycle.
* I think IT beats the market over the next six months.

Most investors do not naturally think:

* Buy futures
* Buy calls
* Build spreads
* Create pair trades

View Markets bridges this gap.

The objective is:

**Belief → Expression → Deployment**

---

# Product Positioning

View Markets is:

* A belief operating system
* A strategy discovery engine
* A capital expression layer

View Markets is NOT:

* A prediction exchange
* A betting platform
* A binary YES/NO market
* A financial advisory product
* A recommendation engine claiming certainty

The product translates beliefs into possible expressions and strategies.

---

# Guiding Principles

Every view should answer:

1. What is being predicted?
2. How is it measured?
3. When does it resolve?
4. What is currently priced in?
5. How does the belief affect markets?
6. What are possible expressions?
7. When should positions be entered?
8. What risks exist?

---

# Belief Classification

Not all beliefs are the same.

Views should be divided into categories.

---

# Type 1: Event Views

Definition:

Specific events that have objective outcomes and clear resolution dates.

Examples:

* RBI cuts rates in next policy meeting
* OPEC cuts production
* TCS beats earnings expectations
* US attacks Iran
* New semiconductor policy announced

Properties:

* Measurable
* Time-bound
* Resolvable
* Event-driven

Structure:

Belief
↓
Resolution Date
↓
Outcome
↓
Possible Expressions

---

# Type 2: Relative Views

Definition:

One asset, sector, theme, or entity outperforming another over a defined horizon.

Examples:

* IT beats Nifty over six months
* Gold outperforms equities this year
* Private Banks outperform PSU Banks
* Reliance outperforms Infosys

Properties:

* Quantifiable
* Benchmark-based
* Time-bound
* Relative performance driven

Structure:

Asset A
vs
Benchmark B
over
Time T

---

# Type 3: Themes

Definition:

Long-duration narratives and structural trends.

Examples:

* India Manufacturing Expansion
* AI Adoption Accelerates
* Renewable Energy Growth
* Defence Spending Supercycle

Properties:

* Long duration
* Narrative driven
* Often difficult to resolve objectively
* Better suited for baskets and themes

Structure:

Theme
↓
Transmission Effects
↓
Beneficiaries
↓
Expressions

Themes should not become binary contracts.

---

# Important Principle

A belief without:

* a measurable outcome
* a defined benchmark
* a time horizon

is not actionable.

Views should therefore always contain sufficient specificity.

Examples:

Bad:

"India will grow."

Better:

"India GDP growth exceeds consensus estimates over the next 12 months."

Bad:

"AI will grow."

Better:

"Indian IT and AI beneficiaries outperform Nifty over the next year."

---

# Market Expectations

Markets react to surprises rather than outcomes.

Every view should therefore contain:

Expected Outcome
User View
Difference (Surprise)

Example:

Expected:
25 bps RBI cut

View:
50 bps cut

Difference:
Positive surprise

This difference is often the actual source of market movement.

---

# Economic Transmission Layer

One of the most differentiated features of View Markets should be visual transmission maps.

Example:

US attacks Iran
↓
Oil prices rise
↓
Inflation expectations rise
↓
Rate expectations increase
↓
Energy companies benefit
↓
Airlines weaken

Views should not simply show assets.

They should explain causal relationships.

---

# Expressions

This is the most important component.

View Markets does not recommend securities with certainty.

Instead it proposes expressions.

Examples:

Conservative
Balanced
Aggressive

Each expression may contain:

* Sector baskets
* Multi-asset allocations
* Options strategies
* Relative trades
* Hedging structures

Expressions should always communicate:

* Why it may work
* Risk profile
* Capital intensity
* Historical relationship strength
* Time horizon

---

# Strategy Timing

This is especially important for Event Views.

Being correct about an event does not guarantee profits.

Positions may be affected by unrelated market risks before the event occurs.

Therefore every event view should support multiple entry modes.

---

## Pre-Position

Enter immediately.

Pros:

* Captures anticipation moves
* First-mover advantage

Cons:

* High idiosyncratic risk
* Event may already be priced
* Unrelated market movements can dominate

---

## Confirmation

Enter after the event occurs.

Pros:

* Reduced uncertainty

Cons:

* Misses initial move
* Lower upside potential

---

## Hybrid

Partial allocation before event.
Additional allocation after confirmation.

Pros:

* Balances risk and opportunity

This will likely become the default approach for many event-based strategies.

---

# Confidence

Confidence should have multiple dimensions.

---

## Outcome Confidence

How likely is the event itself?

Example:

Probability of OPEC cutting production.

---

## Expression Confidence

Assuming the event occurs:

How likely is the proposed expression to benefit?

Example:

Probability that energy stocks outperform.

These are different concepts and should remain separate.

---

# View Lifecycle

Views should move through states.

Open
↓
Developing
↓
Consensus Building
↓
Resolved
↓
Archived

Users should be able to follow views and monitor their progression.

---

# Backend Generation

Views should not be manually written opinions.

Views should be generated after deep analysis and validation.

The backend should use:

* Historical data
* Macroeconomic relationships
* Earnings data
* Sector relationships
* Correlation studies
* Relative performance studies
* Event studies
* Regime analysis
* Consensus estimates
* Economic transmission models

The objective is:

Belief
→ Data Validation
→ Economic Reasoning
→ Expressions

Every view should therefore have evidence supporting it.

---

# Initial Scope

Version 1 of View Markets should focus entirely on:

* Curated views
* Professionally generated expressions
* High-quality visual explanations
* Easy deployment flows
* Beautiful user experience

---

# Explicitly Out of Scope

Do NOT build:

* User-created beliefs
* Custom belief builders
* Prediction exchanges
* Binary YES/NO contracts
* Community voting markets
* Trading on outcome contracts

---

# Future Direction (Not For Initial Release)

Eventually users may create:

* Custom beliefs
* Custom benchmarks
* Custom expressions
* Personal market theses

Examples:

"I think Oil rises while the Rupee weakens."

"I think PSU Banks outperform Private Banks."

Users could eventually create and deploy strategies around their own beliefs.

However:

This is not part of the current implementation.

The immediate objective is validating that investors naturally think in beliefs and enjoy expressing those beliefs through professionally constructed market views.

---

# Design Principles

The interface should feel:

* Visual
* Guided
* Opinion-driven
* Educational
* Professional
* Calm and trustworthy

Avoid:

* Dense tables
* Terminal-style interfaces
* Data overload
* Excessive scrolling
* Overwhelming numerical grids

Prefer:

* Cards
* Timelines
* Confidence indicators
* Small charts
* Transmission diagrams
* Clean typography
* Progressive disclosure

---

# Suggested View Layout

Header
→ View title
→ Category
→ Time horizon

Thesis
→ Short explanation

Market Expectations
→ Consensus
→ Surprise potential

Transmission Map
→ Cause and effect chain

Expressions
→ Conservative
→ Balanced
→ Aggressive

Deployment
→ Backtest
→ Deploy
→ Automate

Related Views
→ Similar themes
→ Follow-up opportunities

---

# Success Metric

Users should feel:

"I may not know which instrument to buy, but I know what I believe."

View Markets succeeds if it can transform beliefs into understandable, evidence-backed, and deployable investment expressions while remaining intuitive and enjoyable to use.
