# The tool surface — what is shared, what is duplicated, and what must not be merged

> Written 2026-09-05 while merging Charto and Pivot into one platform. The
> merge plan called for collapsing the two tool registries into one. **That was
> based on a wrong premise**, and this file records the measurement so nobody
> re-derives it — or "fixes" an arrangement that is deliberate.

## The measurement

| | count |
|---|---:|
| Charto tools (`dataserver.py`) | 47 |
| Pivot tools (`agents/tools.py`) | 90 |
| **Exact name collisions** | **4** |
| Charto tools with no Pivot equivalent | 43 |

The four names on both sides: `get_indicator`, `list_strategies`,
`pause_strategy`, `delete_strategy`.

## Why the registries are not merged

**They are never in the same process.** Charto's dataserver runs its own LLM
loop over its 47 tools plus 9 borrowed from Pivot through
`execution_bridge.PIVOT_TOOLS`. Pivot's API runs a separate loop over its 90.
Two services, two prompt contracts, two tool tables. "One registry" would mean
one of those loops absorbing the other — a rewrite of the chat brain, not a
dedupe.

**43 of Charto's 47 tools have no counterpart at all.** They are the chart:
`draw_shape`, `mark`, `evaluate_fib`, `volume_profile`, `get_trendlines`,
`explain_move`, `screen_universe`, the alert verbs, the journal verbs. There is
nothing to merge them with.

**The 9 borrowed tools are already the merge.** `execution_bridge.py` lends
Pivot's strategy engine to Charto's chat by SUBTRACTION — it names the tools it
wants, runs them on one daemon event loop, and reports honest unavailability
when Pivot cannot be imported. That is a working seam, and it is smaller and
more legible than a combined registry would be.

## The one real hazard, and the guard for it

`dataserver.py` builds the merged dispatch table with

    for _n in execution_bridge.PIVOT_TOOLS:
        _DISPATCH[_n] = _pivot_tool(_n)

which **assigns**. All four colliding names are in `_EXECUTION_CHARTO_TOOLS`,
so adding any of them to `PIVOT_TOOLS` would silently replace Charto's
implementation with Pivot's — and execution mode would answer `get_indicator`
from Pivot's data path instead of the 497M-row bar store the chart is drawn
from. Same tool name, same schema, different numbers, no error anywhere.

A collision now raises at import with a message naming the tools. The rule it
enforces:

> **Charto owns anything read off the bar store. Pivot owns the workflow,
> backtest and fundamentals surface.**

## The indicator engines are two on purpose

`charto/data/indicators.py` (1,234 lines, ~26 indicators) and
`pivot/backend/core/indicators/` (2,498 lines across five modules) both compute
RSI, SMA, MACD and the rest. This looks like the most obvious duplication in
the repo and it is not duplication.

They meet at a Protocol. `workflows/dsl/data_accessor.py:83` declares a
five-method `DataAccessor`; `LiveDataAccessor` answers it from Pivot's data
path and `ChartoDataAccessor` (`charto/data/strategies.py:299`) answers it from
Charto's bars. **One DSL, two data planes, one seam** — which is why an armed
rule in Charto IS the card's tree, evaluated by Pivot's evaluator over Charto's
minute store.

Deleting either engine means one data plane wins and the other surface loses
its numbers. That is a product decision, not a cleanup.

## Execution: already register-not-execute, verified

The merge plan's Phase 6 ("route order verbs to the paper book, leave the
broker rails dormant") required no code. It is the shipped state, and both
paths fail closed:

| Path | Gate | Default | Result when off |
|---|---|---|---|
| User-confirmed (chat, `/orders`) | `live_execution_enabled()` | **False** | `_registered_only()` — registered, never sent |
| Unattended (workflow triggers) | `is_auto_exec_allowed(uid)` | **False**, and `broker_auto_exec_user_ids` is empty | audited as `REGISTERED`, order armed not placed |

Verified 2026-09-05 against the local `.env` and the VM's: neither sets any of
the three variables, so both defaults apply. **No code path can place a live
broker order.** The unattended path is double-gated — flipping
`auto_execute_enabled` alone still places nothing, because the allow-list is
empty.

`should_use_paper()` routes to the non-paper branch if its account lookup
raises, which reads alarming. It is not: the non-paper branch is exactly the
one the two gates above close, so a database blip registers an order rather
than placing one.
