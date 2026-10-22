# The execution layer: how plays work

> Status (2026-10-22): **local only, paper only.** Nothing here places a broker
> order, and none of it is deployed: the layer runs only when the dataserver is
> started with `CHARTO_PLAYS=1`, which production does not set. The design and
> its reasons are in `docs/plans/2026-10-22-execution-layer-architecture.md`;
> this file describes what the code does today.

## The one idea

A trader says what they want in their own words. The model writes it once as a
**play**: a typed object that names what to watch, when to act, what to do and
how far it may go on its own. Code checks the play, replays it over stored
history against a control, arms it, and from then on runs it on every bar
close or event, filling into the simulated paper book.

The rule the whole layer lives under: **the model decides what things mean;
code decides when they happen and what is allowed.** Nothing in the run loop
calls a model except the event judge, and the judge only answers a yes/no
question it must back with a quote.

## Where it is used

| Surface | What it does |
|---|---|
| Plays page (`charto/preview/plays.html`) | Chat that builds and edits plays, a live card per play, Alerts, Portfolio, Review tabs, the halt bar |
| Chat tool `play` | `compile` (build, replay, arm), `edit`, `list`, `pause`, `resume`, `delete`, `explain`, `review` |
| Chat tool `measure` | test or save a measure, make a custom security (basket, ratio, spread), `scan` a universe |
| Chat tool `search`, source `social` | read the latest posts at a social address |
| HTTP | `GET/POST /plays/...` (cards, menus, edits, events, positions, halts, chats) |

## A play

```
{title, ask, why, symbol, interval, measures, when, on, do, manage,
 contract, mandate, security, universe, policy}
```

- **measures**: any series the rule needs, written by the model as an
  expression or a `compute(data, params)` function that runs in the indicator
  sandbox. Inputs: other symbols' bars, a read-only SQL query over the market
  database (F&O history included), Hyperliquid perp funding, premium and open
  interest, the trader's own day book (`day_pnl`, `realized_today`,
  `trades_today`, `loss_streak`), and posts per bar at a social address. The
  same measure code runs for the replay and for the live bar.
- **when**: `{all|any: [{left, op, right, ...}]}` with `above`, `below`,
  `cross_up`, `cross_down`, `between`, `outside`, `rises_pct`, `falls_pct`,
  `is_true`; operands may be arithmetic over names (`or_high + 0.5 * atr14`);
  `{time: {after, before} | {at}}` reads the bar close in IST.
- **on**: events clock the play instead of bars (below).
- **do**: `notify`; `buy | sell | short | cover` sized in rupees, quantity or
  all; `options` legs by ATM offset or strike and expiry; `future`; perps on
  Hyperliquid; `rebalance` to target weights; `halt` (a guard). Code works out
  quantities, lots and strikes; the model never types a price.
- **manage**: stop, target, trail, time exit, leg rules, loss and profit in
  rupees, and `exit_when`, a condition that may read the position itself
  (`entry`, `bars_in`, `hi_since`, `lo_since`, `pnl_pct`).
- **universe**: up to 60 names, each checked on its own bars and traded as
  itself, at most `mandate.max_open` at once.
- **security**: a basket, ratio or spread that does not exist yet, made in the
  same call and usable as a symbol.

## The dials

| Dial | Values | Default |
|---|---|---|
| lifespan | `now`, `once_when`, `standing` (every time), `policy` (keep it) | from the play's shape: a rule fires once, a guard or a universe stands, a rebalance runs now |
| authority | `notify`, `propose` (you confirm), `act`, `manage` | tells you; an order proposes |
| now | act once immediately, then the lifespan takes over; `{size}` for a different first order | off |

**Build what was asked.** A dial above its default must quote the trader's own
words in `contract.inferred_from`; code checks the quote against what they
actually said and refuses the compile otherwise, so the model either quotes
them or lowers the dial itself. A default fills only what an action needs; it
never adds an action (a kept rebalance runs on drift alone unless a calendar
was named). Every default code chose comes back in `assumed`, so the reply can
say it, and every menu on the card reaches its smallest setting (once, no
calendar, drift off).

## How a play runs

1. **Compile.** `normalise` checks every field and fills defaults; social
   sources are read once (a name with no public posts is refused); the play is
   **replayed** over stored history.
2. **Replay against a control.** For a play that opens positions, every entry
   is walked through its exits, and compared with the same exits taken from
   entries anywhere in the same hours. Costs come from the day book's charge
   schedule. The verdict is `too_few` (under 20 trips), `ahead`, `behind` or
   `mixed`, with the edge and the sample size. A play with no exit gets a
   forward view at two horizons against any bar. "Has this worked?" is
   answered by compiling.
3. **Arm.** On each bar close of its interval (or each new event) the play's
   measures and conditions are computed by the same code as the replay. A fire
   records an event, then notifies, proposes or places on paper according to
   its authority.
4. **Manage.** What it opened is held per play (per name in a universe) and
   exits by its own rules; intraday positions square off.

### Safety rules in code

- The first unattended fire of an `act` play still asks once; an edited play
  asks again.
- A headline or a social post alone never places an order: a filing or a price
  condition has to back it, otherwise it proposes.
- A fire found late (Pivot was offline) only notifies.
- Guards (`do: halt`) stop new intraday, F&O and perp entries for the rest of
  the day; exits, delivery buys and rebalances still run. They are checked
  after every fill and exit and on their clock, may be triggered by an event,
  and are lifted from the halt bar.
- Rebalances trade whole shares within `mandate.max_order_inr`; the planner,
  when used, stays within `max_weight` (40%); weights the trader stated are
  kept; buys are cut to what the sells fund when rounding leaves a gap.
- Option fills cross the spread (ask to buy, bid to sell) when live depth is
  there.

## How correctness is checked (`effects.py`)

A play can be built exactly as written and still not do what the trader
meant: "sell half my HAL" built as a sell of what the play itself bought
refuses, and "rebalance to 60 HAL 40 BEL" built over the whole portfolio sells
names nobody named. No rule about sells or rebalances catches the next bug of
this kind, so the checks do not know what kind of play they look at.

1. **The effect, read by the model (intent).** Compile works out the order
   the play would place on the latest bar against the trader's book as it
   stands, places nothing, and returns it as `if_it_fired_now`: the orders,
   each holding before → after, what stays untouched, what refused and why,
   and any field of `do` the order never read (an instruction silently
   dropped). The tool description asks the model to read it against the
   trader's words and compile again when they differ. Code cannot tell what
   "my HAL" meant; the model can, once it sees the consequence. This is a
   round trip: the plan becomes a concrete effect, and the effect is compared
   with the request.
2. **The laws, enforced by code (safety).** Every ticket, before it is
   proposed or placed, is reduced to plain orders and checked: it trades only
   names the play names (its symbol, universe, targets, a security's members,
   what it holds), never sells more than the paper portfolio holds, whole
   shares and whole lots, no buy above the order limit, and a rebalance moves
   each name toward its target. A broken law is a bug in the code that built
   the ticket: the ticket is refused with the reason and logged at ERROR.
   Proposals are re-sized at confirm, so a sell confirmed after the book
   moved sells only what is still held.
3. **Properties, not examples (`test_play_laws.py`).** Hypothesis generates
   books, prices, targets and sizes; every ticket must obey the laws, a
   rebalance must not leave the book further from its targets, must be paid
   for by its sells and the money it was given, and run again on its own
   result must have nothing left to do. 400 cases per law on every run;
   `HYPOTHESIS_PROFILE=deep` tries 10,000. The deep run found three faults no
   hand-written case had: money left by cutting buys to whole shares was not
   reinvested, a name half a share from its target was sold and would be
   bought back next run, and totals rounded to the paisa made an exactly
   funded rebalance look short.
4. **End state, live (`plays_eval.py`, cases in `plays_eval.json`).** Each case
   seeds a paper book, sends one message through the live chat, fires what
   the turn built and confirms its proposals, then compares the books with
   tolerances (which names change and by how much, which stay put, target
   weights, day positions and their stops). The reply's wording and the route
   the model took are not graded; the books are, which leaves the model free
   in how it builds. `--k` repeats each case and reports pass^k.

## Events: filings, news and social posts (`play_events.py`, `social.py`)

- **Sources**: NSE corporate filings (every 30 s), market news RSS (every
  2 min), and social addresses read only while an armed play names them:
  `reddit:r/<sub>`, `reddit:u/<user>`, `reddit:search:<words>`,
  `telegram:<channel>`, `youtube:@<handle>`, `bsky:<handle>`,
  `mastodon:<user>@<host>`, `gnews:<words>`, `rss:<url>`, or a pasted link.
  X, StockTwits, Truth Social, Instagram, Facebook, LinkedIn and WhatsApp
  cannot be read; compile says why and names the nearest readable source.
- **Pipeline**: store once (deduplicated) → match the play's words → if it has
  a judge, a tool-less model call answers the question and must return the
  exact words it relied on (no quote in the item, no fire; loud cue words are
  stripped first) → the play's own `when` is checked on the latest bar → fire.
- The first read of any source is its baseline: nothing already published
  fires. A feed URL must resolve to the public internet.
- Social reads run on their own thread with per-host pacing and back-off, so a
  slow site never delays a filing.

## The paper books

- **Paper portfolio** (`paper.py`): delivery holdings, the book rebalances act on.
- **Day book** (`daybook.py`): intraday longs and shorts, NSE option and future
  legs, perps; stops, targets, trails, leg rules, square-off, charges.
- **Review**: closed trades cut by hour, weekday, play, exit, instrument, day,
  after a loss, minutes held, drawdown and curve.

## Research beside the plays

- **Scan** (`scan.py`): the model's measures and condition run across a
  universe (all stocks, F&O names, perps, a sector, a basket, a list or a
  SELECT) in one sandbox pass, ranked; it returns the spread across every
  name, per-clause pass counts and near misses so a broken measure shows.
- **F&O history** (`fo_store.py`): NSE bhavcopy end-of-day rows and live chain
  snapshots, attached read-only to `query` and to measure SQL.

## Storage

`charto_users.db`: `plays`, `play_events`, `measures`, `play_halts`,
`play_chats`, `day_positions`, `custom_securities`. `charto_events.db`:
`items`, `judgments`. `fo_hist.db`: `fo_eod`, `fo_snap`, `fo_days`.

## Running it locally

```
cd charto/data
CHARTO_PORT=5184 CHARTO_USERS_DB=<scratch>/users.db CHARTO_EVENTS_DB=<scratch>/events.db \
CHARTO_CHAT_V2=1 CHARTO_PLAYS=1 python3.11 -u dataserver.py
python3.11 -m pytest -q test_plays.py test_play_laws.py test_chat_v2.py test_paper.py
HYPOTHESIS_PROFILE=deep python3.11 -m pytest -q test_play_laws.py      # 10,000 books per law
CHARTO_USERS_DB=... CHARTO_EVENTS_DB=... python3.11 plays_eval.py --token <file> --uid <id> [--k 2]
```

The tests need `hypothesis` (`pip install hypothesis`).

`CHARTO_LOG_TOOL_ERRORS=1` prints each chat tool error to stderr.

## Known gaps

- Builds take 15 to 80 s when the model corrects itself across several
  compiles; compile should return every problem at once.
- The replay verdict is not yet the backtester's full trust ladder (no
  deflation for the variants tried, no held-out period), and a `no_edge` play
  can still be armed.
- Event-only plays still need bars for their clock symbol.
- Social history starts when Pivot first reads a source, so spike rules have
  little to compare against at first.
- Paper only: a broker route needs a registered partner under SEBI's April
  2026 framework.
