# Execution mode — where it stands, and what it should become
*2026-09-02 · verified against VM HEAD `b9f1e86` · evidence: `data/exec_coverage_24_results.json`*

---

## 1. What execution mode is today

A toggle in the chat panel that swaps the tool surface and the contract:

| | Research mode | Execution mode |
|---|---|---|
| Charto tools | 42 | **21** (`_EXECUTION_CHARTO_TOOLS`) |
| Borrowed Pivot tools | 0 | **9** (`execution_bridge.PIVOT_TOOLS`) |
| System contract | Charto's | Charto's **adapter** (6.6 KB) + 2 sliced `system_core.md` sections + 5 Pivot prompt modules + filtered calibration = **45.6 KB ≈ 11.4 k tok** |
| Context block | full `_render_chart` | `_execution_context` — instrument, interval, last price, and four lines saying the screen is not a constraint |

`execution_bridge.py` lends Pivot's engine without forking it: one daemon event
loop, a cached availability answer, `db=None`, `kite_token=""`, `user_id=0`.
**Nothing on this surface can touch an account, and that is deliberate.**

**It is genuinely live in production.** `/health?deep=1` on the VM today:
`execution: {checked: true, ok: true}`. (A bare `python3` probe on the box says
`No module named 'pydantic'` — that is the system interpreter, not the service's
venv. The service imports the engine fine.)

## 2. What it covers — proven, not asserted

24 prompts / 28 turns through the live `/chat` endpoint, execution mode, today.

**Works, with a card:**

- **Condition entries** — single, compound, indicator-vs-indicator, volume-relative, gap, session-day filter → `propose_dsl_workflow`, English readback on every tile.
- **Exit trees** — `unrealised_pct`, `bars_held`, `drawdown_from_peak_pct`. #2 built a two-clause exit correctly.
- **Schedules** — "every Monday 9:30, ₹5,000 into ITC" → `propose_workflow` with notional sizing. Square-off-at-15:15 too.
- **Amendment** — "make it 60 shares and add a 4% stop" re-emitted **one** draft, not a second strategy (#16).
- **Backtest** — trust verdict, deflated Sharpe, sub-periods, Monte Carlo, benchmark-vs-hold, and the trade ribbon on the chart.
- **Backtest → ink** — "mark where it entered" ran the test then drew its 7 real fills (#8).
- **Baskets** — `build_strategy` returns names + weights + the engine's own reason per name.
- **Pairs / cointegration** — Engle–Granger ADF, hedge ratio, half-life (#6).
- **Screen → build in one turn** — `screen_universe` → 10 × `read_symbol` → `build_strategy` (#20). It works, and it costs **190 s**.
- **Honest boundaries** — options hedge (#18) and RBI-event trigger (#19) were both refused in one line *and* redirected to the real nearest thing. This is the contract working exactly as written.

**Triad — 24 graded turns**

| | |
|---|---|
| Latency | median **21.2 s** · mean 32.7 s · p90 41.4 s · max **190.8 s** |
| Input tokens | median **67,330**/turn · single-round floor **33.4 k** · ≈2.0 M across the run |
| Output tokens | median 259 · 10,502 total |
| Routing | 10/12 hit the expected tool (the 2 misses: #6 defensible, #9 real) |
| Tool calls | 34 issued, **5 failed (15%)** |
| Cards | 13/24 turns produced one |

## 3. What it does not cover

The surface **builds and simulates. It has no memory and no runtime.**

There is no `/strategies`, no `/orders`, no `/positions` — the only execution
endpoint in the whole server is `/execution/backtest`. `cards.js:1209` sets
`activate.disabled = true` unconditionally. **A strategy you build cannot be
saved, armed, watched, filled or reviewed.** It dies with the conversation.

Concretely missing:

1. **A store.** No strategy survives the turn.
2. **A runtime.** Nothing evaluates a built rule against live bars. (Charto's *alert* engine does exactly this — for alerts only.)
3. **A position book.** So every `position` leaf — stop, trailing stop, time stop — is backtest-only. The exits the builder writes have nowhere to run.
4. **The journal seam.** `journal_trades` exists and is well-built; not one journal tool is on the execution surface.
5. **Delivery.** Alerts are in-app only: "with every tab shut nothing is pushed anywhere."
6. **Portfolio-level anything.** No aggregate capital, exposure or worst case across strategies (#15 had to ask the user for the numbers back).

## 4. Findings from the run, ranked

### P0-1 — It says "Saved" when there is no store *(integrity)*
> #12 — "save this and turn it on" → **"Saved the strategy… It's ready to turn on from the strategy card."**

Nothing was saved. The button it points at is permanently disabled. This is
"never narrate done on a failure path" — an identity-level rule — violated on
the most natural follow-up a user has after seeing a draft. **Worst finding in
the run.**

### P0-2 — It says it writes to the journal, and it cannot *(integrity)*
> #21 — "log it in my journal when it fires" → **"…buys 8 SUNPHARMA and logs the trigger in your journal."**

No journal tool is on the execution wire. It emitted a `propose_workflow` draft
and described a journal write that no step performs.

### P0-3 — A dropped upstream connection becomes a 500 with an empty reply
> #10 — HTTP 500, blank answer.

`http.client.RemoteDisconnected` from Azure escapes `_urlopen_with_retry`
(`dataserver.py:12394`), which catches `URLError, TimeoutError, socket.timeout`
— and `RemoteDisconnected` is neither (CPython leaves `getresponse()` outside
`do_open`'s `OSError` wrapper). One tuple entry, plus a graceful-error belt in
`do_POST`. **4% of turns in this run died this way.**

### P1-4 — The model did the arithmetic itself
> #9 — sizing → called `get_bars`, then computed "₹20,000 ÷ ₹2.90 ≈ 6,896 shares" in prose.

`plan_position` was on the wire and is exactly this tool. Constitution §3 —
model owns meaning, code owns math — crossed. (Its *reasoning* about the invalid
stop was sharp; the number should not have been its own.)

### P1-5 — Capabilities promised that do not exist
- #17 walk-forward: *"I'll test it on rolling in-sample and out-of-sample windows."* Not reachable from this surface.
- #22 paper trade: *"What idea should I paper-trade for the month?"* There is no paper book.
- #11 fractional crypto: asked for a rupee amount instead of naming the boundary.

### P1-6 — Cost per turn
**33.4 k input tokens per LLM round**, of which ~21 k is this surface's own
(11.4 k contract + 9.4 k Pivot tool schemas — `build_strategy` and
`propose_workflow` are 2.2–2.3 k each). Every tool round pays it again: #2 spent
119 s and 138 k tokens on three calls to reach one correct card.

### P2-7 — A real DSL gap, honestly reported
> #24 — "no more than 2 trades a week" → `Aggregate op 'count_when' needs a boolean source; got 'AlwaysNode'`, twice, then: *"I can't complete this automation… No order has been created."*

The right behaviour on a capability the DSL lacks. It still burned 3 calls and 41 s.

## 5. The ceiling — how far "execution" is allowed to go

SEBI's Feb-2025 framework (`SEBI/HO/MIRSD/MIRSD-PoD/P/2025/0000013`) became
mandatory for all stockbrokers **1 April 2026**:

- Every algo-placed order carries an exchange-assigned **Algo-ID**.
- **Algo providers must empanel with a registered broker**; they cannot reach the exchange directly.
- **>10 orders/second per exchange** is the algo threshold; below it you are a normal API user.
- **White-box** (logic disclosed and replicable) vs **black-box** (undisclosed → the provider must register as a Research Analyst).
- Self-built algos for personal use — and immediate family — stay permitted.

Two consequences:

**Charto is white-box by construction.** The card prints every step and an
English readback of the tree. That is the lenient category, and it is a property
of the design, not a claim.

**The moment Charto places the order it becomes an algo provider** needing broker
empanelment and Algo-IDs. Constitution §8 already forecloses this: *"The bracket
on the chart stages; the human commits in their own broker app. Never wire
chat/chart to auto-place."* The regulation and the constitution agree, so the
roadmap's terminus is **arm → detect → notify → hand off a filled ticket**, and
auto-placement is not on it. (Dhan and Fyers accept TradingView-style webhooks
today — that is the broker owning the registration, and it is a partnership
conversation, not an engineering one.)

## 6. The architectural discovery that makes this cheap

I expected Phase 2 to be "write a translator from Pivot's DSL tree into Charto's
alert grammar, accept ~70% coverage, then maintain two grammars forever" —
Charto's addresses cannot express nested logic, cross-symbol spreads, math nodes
or position leaves.

**That translator should not be written.** `dsl/evaluator.py` is a pure tree
walker over a five-method Protocol (`dsl/data_accessor.py:83`):

```python
get_price(*, symbol, exchange, basis, offset, timeframe)      -> float | None
get_indicator(*, symbol, indicator, period, component, ...)   -> float | None
get_volume(*, symbol, bars, exchange, offset)                 -> float | None
get_position_field(*, field, basis)                           -> float | None
get_session_day()                                             -> str | None
```

Charto already has bars and 26 indicators. **Implement one `ChartoDataAccessor`
and `evaluate(tree, accessor=...)` runs the exact tree the builder produced, on
Charto's own tick, with no second grammar and no forked semantics.** `None`
propagates as Kleene UNKNOWN, so a gap holds rather than fires — the fail-safe
posture the alert engine already takes.

This is the same reasoning `execution_bridge` was built on: borrow the engine,
own the seam.

---

## 7. The plan

Four phases. Each ships alone and is worth shipping alone.

### Phase 0 — Stop the surface claiming things it cannot do *(~1 day)*
The only phase that is pure correction, and it should land before anything is built on top.

1. **P0-1.** The draft card's primary button is the honest answer, not the prompt. Until Phase 1 lands it reads **"Backtest"** as primary and the disabled control goes away entirely; the contract gains one line: *a strategy is not saved, and you cannot say it is.*
2. **P0-2.** Same line covers the journal: name what the draft does, never what no step performs.
3. **P0-3.** Add `http.client.RemoteDisconnected` / `ConnectionError` to `_urlopen_with_retry`'s retry tuple; make `do_POST` return a spoken error instead of a bare 500.
4. **P1-4.** One clause in the adapter: sizing goes through `plan_position`, never through prose arithmetic.
5. **P1-5.** Three boundaries stated rather than implied: no walk-forward on this surface, no paper book yet, no fractional-quantity instruments.

*Test:* re-run `exec_coverage_24.json`. Success = zero fabricated capabilities, zero 500s.

### Phase 1 — The store *(~2–3 days)*
- `strategies` table in `charto_users.db` (user-scoped, beside `alerts`): the draft JSON, its name, its state (`draft | armed | paused | retired`), its provenance (which chat, which backtest).
- `/strategies` CRUD + a `strategies` panel that reuses the alerts list shell.
- Four tools on the execution surface: `save_strategy`, `list_strategies`, `pause_strategy`, `delete_strategy` — Charto's own, user-scoped, never Pivot's (a Charto id is not a Pivot id; `execution_bridge.dispatch` documents why).
- **"Save & activate" becomes real**, and #12's sentence becomes true.

### Phase 2 — The runtime *(~3–4 days)*
- `ChartoDataAccessor` — the five methods above, over Charto's bars and `indicators.py`.
- Arm a saved strategy on the **existing** tick seam. `alerts.py` already solves every hard part and solves it well: persisted crossing state, boot catch-up with `late=1`, freq buckets, expiry, and a hook that cannot raise into `_live_on_tick`. The strategy watcher is the same worker with a different verdict function — **extend it, do not write a second one.**
- Firing writes an evidence row (what it saw, the level as resolved, the bar's clock) exactly as an alert does.
- Entry trees go live here. Exit trees still cannot — they need Phase 3.

### Phase 3 — The position book, and the loop closes *(~3–4 days)*
- `journal_trades` **already is** a position book: `status IN ('open','closed')`, entry/exit, fees, `initial_risk`, `source`. A fired strategy writes a row with `source='agent'` and the draft attached as its `plan`.
- `get_position_field` reads that row → **`unrealised_pct`, `bars_held`, `peak_unrealised_pct`, `drawdown_from_peak_pct` all resolve live.** Every stop, trailing stop and time stop the builder can already write starts working.
- That is also the paper book (#22), and it is forward-testing for free: the same strategy has a backtested claim and a live record, side by side.
- **build → test → arm → fire → log → review**, in one surface. CHARTO.md calls the journal layer "the biggest retention bet"; this is what connects it to everything else.

### Phase 4 — Delivery and the handoff *(~2–3 days)*
- Push past the open tab (the alert engine's own stated gap). A strategy that fires while you are asleep is worth nothing if the log is the only place it lands.
- A fire renders a **pre-filled order ticket** — symbol, side, quantity, the level it saw — that the user carries to their broker. This is register-not-execute made concrete, and it is where the surface stops by design.

### Deliberately not on this plan
Auto-placement; broker empanelment and Algo-IDs; options on this surface (removed on purpose — "strategy" collapsed to "option strategy"); macro-event triggers; a second strategy builder.

### Worth doing, unscheduled
- **Cost.** 33.4 k input per round is the latency lever. `build_strategy` + `propose_workflow` are 4.5 k of schema alone; a per-intent tool subset (Pivot's own `tool_router` pattern) is the obvious cut.
- **`count_when`** (#24) — a frequency cap is a thing people genuinely ask for.
- **Excluded reads worth restoring:** `get_divergences`, `volume_profile`, `compare_symbols` are legitimate strategy inputs that research mode has and this surface does not.
- **#20's 190 s** — screen-then-build is the right behaviour at the wrong price.
