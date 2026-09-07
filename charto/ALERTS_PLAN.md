# Charto Alerts — making the widget real

> Plan, 2026-08-10 — **built the same day**. The Alerts widget was drawn at full
> fidelity and deliberately inert; it now runs on a real engine.
> **The engine's own specification is `data/alerts.py`'s module docstring** —
> that file is the authority on the address grammar and the firing rule, the way
> `mark.py`'s header is the authority on addresses. This document is the
> reasoning that led there and the record of what was decided.
>
> One thing changed materially between plan and build, and §5.1/§5.2 below have
> been rewritten to match: **there is no `kind` column and no branch per alert
> type.** The first draft had `kind ∈ {price, pct_move, volume, indicator}`,
> which is the "a tool per sentence, forever" disease `mark.py` names and the
> catalogue-of-named-screens that `screen_universe` refuses. An alert is a
> composed expression instead.

---

## 1. What the frontend has already promised

The fixture is not a placeholder rectangle — it is a **specification**, and it
commits us to more than a price alarm. Read off `MOCK` in `preview/js/panels.js`
and the `.al-*` / `.lg-*` blocks in `preview/index.html`:

| The fixture shows | What the backend therefore owes |
|---|---|
| `Crossing up` · `1,420.00` | price crossings, directional |
| `Crossing down` · `24,800.00` | the mirror, on an index |
| `Volume above` · `2× average` | volume **relative to its own average**, not an absolute |
| `RSI(14) crossing down` · `30` | indicator alerts, with a period |
| `Moving down` · `2% in 1D` | percent move over a window |
| `Once per bar close · 5m` | four trigger frequencies × an interval per alert |
| `armed` / `paused` / `fired` dots | a real state machine, and a rule that **stops itself** on fire |
| the Log tab: `1,656.20` beside `crossed above 1,655.00` | **the value it actually saw at fire time, persisted** |
| bell `has-new` dot, spent on opening the panel | an unseen count per user |
| the disabled bell on every watchlist row | create-from-watchlist, prefilled |
| `.al-side` fixed at 74px so hover controls swap in | pause/resume · edit · delete, per row |
| `Search alerts` · `Sort` · `More` | list controls |

Two things in there are load-bearing and easy to miss:

1. **The log stores what it saw, not what it wanted.** `crossed above 1,655.00`
   with `val: 1,656.20`. That is an evidence record, and it is the whole reason
   an alert is trustworthy after the fact. The engine must persist the observed
   value and the bar it observed it on.
2. **A fired alert is not a deleted alert.** `state: "fired"` sorts second (RANK
   in panels.js), keeps its row, and shows a `Fired` pill where the others show
   a date. So firing is a *transition*, not a removal.

What the frontend does **not** have: any create/edit dialog. That screen is
net-new — nothing about it has been designed yet, and it is where most of the
FE work in this plan actually lives.

---

## 2. What the backend already has (and why the shape is nearly forced)

- **`_live_on_tick(sym, ts, price, vol)` — the one seam.** Every tick source
  calls it: the replay thread, `kite_stream.py`, `crypto_stream.py`. It keeps
  one forming 1-min bar per symbol, writes the minute to SQLite the instant it
  closes, and pushes SSE. An alert engine wants exactly this seam.
- **In-process only.** `_LIVE` and the SSE subscriber lists are module state; a
  driver started as a separate CLI process writes closed minutes correctly and
  *never moves a chart*. Same constraint applies to us: the engine must live in
  the serving process, imported the way `kite_stream.py` is
  (`sys.modules.setdefault("dataserver", …)` at boot makes `import dataserver`
  return the running module).
- **SSE is already solved twice** — `_send_events` and `_send_live`, both with
  the load-bearing `X-Accel-Buffering: no` and the per-subscriber
  `queue.Queue(maxsize=64)` + drop-a-slow-reader rule.
- **Auth is already solved** — bearer token, `charto_users.db`, `_auth_user()`,
  and a `_account_post` dispatcher for the authenticated POST routes.
- **Indicator math is already pure** — `indicators.py` is functions of rows,
  nothing stores a value. An alert can recompute RSI(14) on demand and never
  needs its own indicator cache.
- **`_infer_tick()` already exists** (for the volume profile) — that is the
  "one minimum tick" rule TradingView's *greater than* / *less than* need,
  measured off real prints rather than assumed.

So the design is not open-ended: **storage in `charto_users.db`, evaluation on
the `_live_on_tick` hook, delivery over a third SSE endpoint.**

---

## 3. How the market actually builds these (research)

- **TradingView's price operators are five, not nine**: *crossing*, *crossing
  up*, *crossing down*, *greater than*, *less than* — where the last two require
  clearing the level by **at least one minimum tick**. The percent/absolute
  *moving up ‑ moving down* operators are a separate family
  ([conditions](https://www.tradingview.com/support/solutions/43000763313-how-to-use-price-alerts/),
  [types](https://www.tradingview.com/support/solutions/43000696403-alerts-separation-by-type/)).
- **Frequency is a first-class field with exactly the four our fixture shows**
  ([frequencies](https://www.tradingview.com/support/solutions/43000474415-differences-between-alert-frequencies/)):
  *Only once* (single fire, on the current unclosed candle), *Once per bar*
  (first qualifying tick of each bar — unconfirmed), *Once per bar close*
  (waits for the close, the confirmed value — "the most common setting for
  systematic traders"), *Once per day*.
- **Drawing-anchored alerts are the differentiated ones.** An alert attached to
  a trendline/ray/fib moves when you move the line, and its trigger price
  changes with every new bar — "something a static price alert can't do".
- **Zerodha's Sentinel establishes the Indian retail baseline**: alerts run **on
  the cloud, not in your browser** ("it will alert no matter what"), notify by
  email + in-app, and cover price, day change, volume and OI
  ([Sentinel](https://zerodha.com/z-connect/tradezerodha/introducing-sentinel)).
  Server-side firing is table stakes here, not a nice-to-have.
- **Browser delivery when the tab is shut needs Web Push** — service worker +
  Push API + VAPID keypair, HTTPS-only
  ([Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API),
  [guide](https://blog.codercops.com/blog/web-push-notifications-implementation-guide-2026)).
  Charto is already on HTTPS via Certbot, so this is available — but it is a
  service worker plus a key-management story, which is why it is phased last.
- **Charto's own doctrine argues about the default.** `CHARTO.md` #45:
  *"Setup-completion alerts — fire only on confirmed completion, never approach
  — kills alert fatigue (#1 cross-style complaint)."* And §3 bans the LLM from
  *"watcher evaluation, alert firing"* outright. That points at
  **`per_bar_close` as the default frequency**, not *only once*.

---

## 4. The three things that will actually break this

These are the findings that matter more than the schema. Each is a way the
feature ships looking finished and quietly never fires.

### 4.1 There is no guaranteed tick supply on the VM today

`CHARTO_LIVE_VENUES` autostarts venue drivers at boot, and the comment on it is
already the right argument ("any crash, deploy or reboot silently returns the
box to a state where it serves charts and records NOTHING"). But
`_venue_symbols()` derives its list from `backfill_crypto`'s `COINBASE`/`BYBIT`
tables and `.get(venue, [])` — so **for `kite` it returns `[]`**, `symbols=ALL`
400s, and the NSE feed can only ever be started by hand with an explicit symbol
list. Meanwhile `deploy.sh` restarts `charto.service` on **any** change under
`charto/data/` — every backend deploy therefore drops the NSE stream until
somebody curls `/live` again.

An alert engine on top of that is a promise we can't keep. **Phase 0 fixes the
feed before any alert exists.**

### 4.2 The Kite token expires daily, and it comes from Pivot's Postgres

`kite_stream._kite_access_token()` reaches into `pivot/backend` for the active
Kite session. That token dies ~6 AM IST. A server-side watcher whose feed dies
every morning and says nothing is worse than no watcher. So the engine needs a
**feed-health fact** it can show per alert — *"not being watched right now: no
live feed for NSE since 06:02"* — and the UI has to have somewhere to put it.

### 4.3 A restart must not lose a crossing, or invent one

Two symmetric failures:

- **Inventing one.** A crossing needs the *previous side*. If side lives only in
  memory, a restart re-reads price 1,430 against level 1,420, has no history,
  and either fires a phantom "crossed up" or refuses forever. So the arming side
  is **a persisted column**, seeded from the live price at create time.
- **Losing one.** If the process was down from 10:04 to 10:09, the cross that
  happened at 10:06 is in `bars` and nowhere else. On boot the engine replays
  stored 1-min rows since each alert's `last_eval_ts` and fires with the **bar's
  own timestamp**, marked in the log as detected late. Silently skipping that
  window is the failure that ends trust in the whole widget.

---

## 5. The design

### 5.0 The rule is a composed expression, not a type

This is the load-bearing decision. An alert is:

```
RULE      := {symbol, interval, when: [CONDITION, …], all, freq, expires}
CONDITION := {left, op, right, right2, x, plus_pct, within}
```

…where `left` and `right` are **addresses resolved against the real bars** —
`close`, `high[1]`, `rsi(14)`, `macd().signal`, `avg(volume,20)`, `day.high`,
`52w.low`, `poc`, `draw:D3`, `pattern(bullish_engulfing)`. `x` scales the right
side and `plus_pct` offsets it, which is how *"volume above twice its own
average"* and *"2% below yesterday's close"* are said without either becoming
its own alert type, and without anyone writing an arithmetic parser.

Three consequences worth stating:

- **Every indicator in `indicators.py` is alertable the day it is added there**
  — 26 today — because nothing in the engine holds an indicator list. Same for
  `patterns.py`'s 34 candles and 22 shapes.
- **`all: true` is an AND**, so a breakout condition ANDed with a
  volume-confirmation condition fires once, on confirmation. That is
  `CHARTO.md` #45 ("fire only on confirmed completion, never approach")
  expressible in the data model rather than as a special case in code.
- **A refusal hands back the whole grammar**, exactly as `_screen_vocab` does,
  for the same reason: an error the model cannot act on costs more than the
  vocabulary does.

The firing rule is one sentence covering both events and states:

> fire when every condition is true now, **and** either something flipped on
> this pass **or** the conjunction itself just became true.

That is what lets `close crossed 1420` (an event) and `RSI under 30 and price
above its 200-day` (a conjunction of states) live in one rule with no second
code path.

### 5.1 Storage — `charto_users.db`, two tables

The users DB, not `charto_bars.db`: the existing comment says it exactly — bars
are a 14 GB derived store that gets dropped and rebuilt, and a user's alerts
cannot be regenerated from anything upstream.

The shipped schema is in `data/alerts.py` (`_SCHEMA`). The shape, and why each
column that is not obvious exists:

- `spec` — the composed rule as JSON (`{when: […], all}`). No `kind`, no `op`,
  no `level` column: those would each be a ceiling on what is expressible.
- `cstate` — **per-condition** `{side, ok}`, persisted. §4.3: a crossing needs
  the previous side, and holding it only in memory means a restart either fires
  a phantom or refuses forever. One entry per condition, because each condition
  crosses on its own.
- `all_ok` — was the conjunction true on the last pass. This is what makes a
  rule of pure states fire on its own edge.
- `last_eval_ts` — the catch-up watermark. `last_fired_bkt` — the bar bucket the
  frequency gate counts in (the session, for `per_day`).
- `alert_log.verb/level/value/meta` are exactly the four fields `logRow()`
  already renders, and `late` marks a row the catch-up scan found rather than
  saw live. The engine writes the sentence once, at fire time, from the state it
  had — so the log can never be re-derived wrongly later.

### 5.2 Evaluation — `charto/data/alerts.py`

A new module beside `kite_stream.py`, `import dataserver as ds`, same pattern.
One in-memory index `symbol -> [Rule]`, replaced whole rather than mutated so a
reader never sees a half-built list.

`dataserver._live_on_tick` gains **one call**: a registered `_ON_BAR` hook
receiving `(sym, form, closed)` — the raw forming bar, *not* `_live_snapshot()`.
That matters twice over. The snapshot reads the session's minutes from SQLite
and folds every interval, a cost currently paid only when a browser is watching;
and the hook is routed through `_bar_hook`, which swallows every exception,
because **an exception raised into `_live_on_tick` would abort the tick and lose
the minute it was in the middle of maintaining**. A watcher bug must cost an
alert, never a candle. The hook itself does one bounded `put_nowait` and
returns; all evaluation happens on the module's own worker thread, which drains
and *coalesces* the queue (many ticks on one symbol collapse to its latest, and
a bar close is sticky so a closing minute is never swallowed).

Resolution, not a branch per type: `_resolve(address, ctx, rule)` is one
function, and `Ctx` caches the folded rows once per (symbol, interval) per pass
so a dozen rules on one pair cost one read. Specifics worth recording:

- `above`/`below` require clearing the level by one **measured** minimum tick
  (`ds._infer_tick`, cached hourly) — TradingView's rule, with the instrument's
  own increment rather than an assumed 0.05 that is wrong on a sub-rupee stock
  and on crypto.
- The **side flip is the re-arm rule**, which is what stops a level being
  chopped through from firing twenty times.
- `avg(field,n)` **excludes the current, possibly forming bar**: an average
  containing the value being compared to it drifts toward its own input, and
  "volume above its average" then means less with every tick.
- The detectors (`pattern()`, `divergence()`, `results()`) are evaluated **on
  closed bars only** and drop the forming bar before they run — a pattern on a
  bar that is still moving is a claim the next tick can withdraw. That is
  completion, never approach.
- The **magnitude guard** runs only when exactly one side is a literal, and asks
  the *other* side what units it speaks — the price range on screen, or the
  oscillator's own `bounds` from `indicators.SPECS`. `30` is a slipped decimal
  beside a ₹1,309 close and perfectly ordinary beside RSI, and a comparison of
  two addresses is never second-guessed.
- An address that **stops** resolving pauses the rule and records why, on the
  row. Leaving it armed would be a rule that silently never fires, which is the
  one outcome an alert must never have.

Frequency gates read `last_fired_bkt` against the current bucket, so *once per
bar* and *once per day* need no timers. `per_bar_close` alerts are evaluated
only on the closing edge, using the confirmed value.

**A daily-only fallback, labelled.** 560 symbols have daily bars; ~110 locally
(≈557 on the VM) have the 1-min history a live stream requires. For a
daily-only symbol we still accept a `1d` alert and evaluate it on an EOD pass —
but the row says `1D · end of day`, never something that implies a live watch.

### 5.3 API — GET/POST only

The handler implements `do_GET`/`do_POST`/`do_OPTIONS` and nothing else, and
`layouts` already models delete-by-body-flag. We follow it rather than adding a
verb.

```
GET  /alerts               -> {alerts:[…], log:[…], unseen:n, feed:{…}}
POST /alerts               -> create (validates, seeds `side` off the live price)
POST /alerts/{id}          -> patch state/level/freq/expires, or {delete:true}
POST /alerts/seen          -> spend the bell dot
GET  /alerts/stream        -> SSE, per-user queue: {type:"fired", alert, log}
```

`feed` on the list response is §4.2 made visible: per venue, is a driver
connected and how old is its last tick.

**nginx.** `deploy/nginx-charto.conf` carries the explicit warning that a new
prefix is unreachable in production until it is added to the allowlist — `/auth`,
`/workspace` and `/layouts` shipped without it and silently served
`index.html`. So: `alerts` into the data-route regex, **and** `alerts/stream`
into the SSE location (`^/(stream|chat)$` → `^/(stream|chat|alerts/stream)$`),
because a buffered SSE response is a page that looks hung.

### 5.4 Delivery

1. **In-app, live** — `/alerts/stream`, one queue per subscriber, the
   `_send_live` shape. On an event: `toast()` (already exists in `layouts.js`),
   the bell's `has-new` dot, and a panel repaint if it happens to be open.
2. **Browser notification while a tab is open** — the Notification API, with
   permission requested at the moment the user creates their *first* alert, not
   on page load. Optional sound (a recurring Zerodha ask).
3. **Tab closed** — the log is the durable record and the bell is waiting when
   they return. Real push (§7, Phase 4) is the answer; until then the UI must
   not imply otherwise.
4. **Email** — no SMTP is wired anywhere in charto. Out of scope, and said so
   rather than half-built.

### 5.5 Chart-native creation — the actual differentiator

A form is the commodity version. Charto has a full drawings layer with
never-recycled D-refs (`drawings.js`), `geometry.js` for price↔pixel, and
`mark.py`'s precedent of *the model writes an address, code resolves it against
real bars*. Two entry points worth having:

- **Right-click the chart at a price** → "Add alert at ₹1,420.00", level
  prefilled from the y under the cursor.
- **Alert on a drawing** → attach to a horizontal line, ray or trendline. The
  level tracks the line when the line moves, and for a sloped line it is
  recomputed per bar — precisely the thing research says a static price alert
  can't do, and something we can build cheaply because the drawing is already
  addressable.

### 5.6 The chat surface

`CHARTO.md` §3 bans the LLM from watcher evaluation and alert firing, and gives
it meaning work. That boundary lands cleanly: a `set_alert` tool where the model
translates *"tell me if Reliance breaks 1420 on the 5-min"* into the typed rule,
and the deterministic engine owns every millisecond after. The confirmation
surface is the alert row itself — no new card type.

---

## 6. What shipped, and how it was checked

Phase 0 → 3 as scoped, plus the chat tool that had been parked in Phase 4 (it
cost three schemas once the identity was already on the request).

| Phase | Shipped |
|---|---|
| 0 — the feed | `_venue_symbols()` learns `kite`, so `CHARTO_LIVE_VENUES=kite` can arm the NSE feed at boot; feed health is a first-class field on every alerts response and a line in the panel |
| 1 — the spine | `data/alerts.py`: schema, the composed-expression engine, the guarded `_ON_BAR` hook + worker, `/alerts` CRUD, `/alerts/check`, `/alerts/stream`, boot catch-up, both nginx routes |
| 2 — the widget | `MOCK` deleted; rows/log/counts/bell from the server; the create-edit dialog; pause/resume/delete/search/sort/bulk; watchlist bell awake; toast + Notification API; the signed-out and feed-down sentences |
| 3 — the rest | every condition kind the fixture promised, plus indicator-vs-indicator, bands, windows, volume profile, detectors; right-click-to-alert; drawing-anchored alerts |
| chat | `set_alert` / `list_alerts` / `cancel_alert`, identity from the request's own token (never a model argument) |

Verified, not assumed:

- **The state machine**, on synthetic bars: arm below → cross → no repeat while
  above → re-arm on the way back → cross again. `once` stops itself. `per_bar`
  refuses a second fire in the same bar. `per_bar_close` returns nothing on a
  forming bar. An AND of two states fires on the conjunction's own edge, goes
  quiet, and fires again after it breaks and re-forms.
- **Catch-up**, on real stored bars: watermark set to the middle of the window →
  three genuine crossings found, each stamped with its own bar's time and marked
  `late`; a second pass finds nothing.
- **The full live path**: `ds._bar_hook(...)` → queue → worker → fire → log row →
  SSE event, with the log carrying the value it saw.
- **Two bugs found by testing and fixed**: `once` could fire twice from the same
  `Rule` object in the window between `_fire` flipping the state and the index
  reloading; and the magnitude guard was malformed — it accepted
  `close cross_up 142` on a ₹1,309 instrument. Both have the reasoning in
  comments at the fix.
- **No regression**: `/bars`, `/quotes`, `/meta`, `/company`, `/indicator`,
  `/symbols` byte-identical before and after; the chart, watchlist, drawings and
  chat unchanged in the browser with a clean console.

**Still not built, deliberately:** Web Push (service worker + VAPID) — with
every tab shut nothing is delivered, and the UI says so rather than implying a
push. `CHARTO.md` #46–47 (thesis-invalidation, valuation buy-zones) are now
*expressible* as rules but there is no generator proposing them.

---

## 7. Decisions taken (2026-08-10)

1. **Scope: Phase 0 through Phase 3.** Every condition kind the fixture shows
   ships real. Web Push and the chat tool are out.
2. **An account is required.** Alerts are server-side by definition; signed-out
   reads "Sign in to create alerts", the same boundary layouts already draw.
3. **Default frequency: `Only once`.** All four remain selectable per alert, and
   `Once per bar close` stays one click away in the dialog. The trade is
   accepted knowingly: firing on the unclosed candle means a wick through the
   level counts as a fire, and `CHARTO.md` #45's confirmed-completion argument
   is not the default. Documented here so it reads as a choice, not an
   oversight — and worth revisiting once there is real firing data to look at.
4. **Delivery: in-app SSE + the Notification API.** With every tab shut, nothing
   reaches you until you return; the log and the bell dot are the record, and no
   copy anywhere may imply otherwise.
