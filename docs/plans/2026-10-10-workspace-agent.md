# One chat that runs the whole workspace — plan

2026-10-10 · charto (`charto/preview`, `charto/data/dataserver.py`)

## What we are building

The chat becomes the operator of the user's workspace, not a sidebar beside it.
It can spawn any widget, place and size it, set every setting, put content
inside it (a screen into the Screener, code into the Code editor, a table into
a Sheet, symbols into the Watchlist, a draft into Strategy), read what each
widget is showing, and do all of it in one turn while the user watches the desk
assemble. At the same time the tool surface stops being "every tool on every
turn": groups load when the model asks for them.

Two constraints from the user hold throughout:

1. **No deterministic routing.** The model decides what to open, load, write.
   Code only *verifies* (types exist, settings and values are legal, a screen
   feature is real) and *executes*. No keyword tables, no regex intent.
2. **Measure.** Tool loading is shipped with before/after tokens, latency and
   quality on the same live prompts.

## What the research says (and what we take from it)

| Source | Idea | What we do with it |
|---|---|---|
| Anthropic, *Advanced tool use* | Tool search cut definitions 77k→8.7k tokens and *raised* accuracy (Opus 4.5 79.5%→88.1%); keep the few most-used tools loaded, defer the rest | Core chart tools stay loaded; 8 purpose groups deferred |
| OpenAI / Azure, *Tool search* | `namespace` + `defer_loading` + hosted `tool_search`; works with item replay (no `previous_response_id`); loaded tools go at the END of context so the cache survives; <10 functions per namespace | **Probed on our deployment (gpt-6-luna): works, statelessly, and loads+calls in the SAME response (2.9 s, no extra round).** We replay `tool_search_call/_output` items within a turn |
| OpenAI Apps SDK, *state management* | Three kinds of state: business data (server, authoritative), UI state (widget, ephemeral), and what the model sees (`modelContent`) — share only what the model needs | Every widget declares a short **model view** (what it shows, in words and numbers); raw DOM/config never goes to the model |
| Anthropic, *context engineering* | Just-in-time context: lightweight identifiers up front, load detail with tools | The turn carries a **manifest** (ids, types, one line each). `workspace.read(id)` / `describe(type)` load detail only when needed |
| CopilotKit (`useCopilotReadable` / `useCopilotAction`), AG-UI | App state published to the agent; frontend actions the agent can call; state deltas streamed both ways; user can see and undo | Widget ops stream to the page **as the tool runs** (the desk builds live), every op is acknowledged back, one **Undo** reverts the whole turn |
| Cloudflare Code Mode, Anthropic programmatic tool calling | Batch many operations into one call instead of N round trips | `workspace` takes a **list of ops** in one call ("build a bank desk" = one call, six ops) |
| AInvest Trading Studio, TakeProfit, Newsquawk | Users describe a goal → components assembled and *linked around one symbol* | Use the dock's existing **link groups**: widgets the agent opens for one idea share a group, so a click in the screener moves all of them |

## Scale (measured)

- **17 widget types**: chart (main), extra chart, screener, watchlist, alerts,
  journal, notes, news, calendar, live TV, financials, portfolio, depth, sheet,
  code, documents, browser, strategy. ≈90 settings in total.
- Content today: 5 widgets accept content (`receive`: notes, sheet, browser,
  docs, strategy); 15 can describe themselves (`ask`).
- Chat tools: **53 tools, 100,215 characters (~25k tokens) on every round.**
  Biggest: get_patterns 6.4k, get_indicator 5.1k, mark 4.9k, draw_shape 3.7k.
- The dock already has the right primitives (`open/close/moveTo/setCfg/send`,
  link groups, a persisted state tree) — this is an agent API over them, not a
  rewrite.

## Architecture

### 1. The widget contract (front end)

Each `Dock.register` spec gains an `agent` block, next to the settings it
already declares:

```js
agent: {
  model(api, cfg) → string      // ≤ 400 chars: what it shows now ("Screen 'RSI < 30', 11 of 500 as of 22 Jul: SBIN, …")
  writes: { …schema-ish doc… }  // what `write` accepts, with enumerations
  write(api, payload) → { ok, error?, did }   // runs inside the widget
}
```

`model()` defaults to the widget's existing `ask().sub`; `read` returns the
fuller `ask().context` (≤ 3k chars). Settings are already declarative, so the
catalog (keys, kinds, options, ranges) is generated from them — nothing is
written twice.

### 2. The turn envelope

`context.workspace` (sent every turn, small):

```json
{ "widgets": [{ "id": "screener:k2f9", "type": "screener", "title": "Screener",
                "symbol": "HDFCBANK", "link": "2", "visible": true,
                "model": "Screen 'Oversold banks' · 6 of 500 · as of 22 Jul" }],
  "layout": "chart | screener (left) | notes+news (right, tabs)",
  "ack": [{ "op": 3, "ok": false, "error": "unknown setting 'colour'" }] }
```

plus `context.widget_catalog` (types + settings + writes), which the server
keeps and serves only through `workspace(action:"describe")` — it costs
request bytes, not prompt tokens. The manifest goes at the END of the prompt
(after history), so the cached prefix is not broken by a desk that changed.

### 3. One tool, a list of ops

```
workspace(ops: [
  {op:"open", type, id?, where?, settings?, symbol?, link?, content?},
  {op:"configure", id, settings?, symbol?, link?},
  {op:"write", id, content},
  {op:"move", id, where},            // left|right|bottom|top|float|tab:<id>|split:<id>:<side>
  {op:"focus"|"close", id},
  {op:"read", id}, {op:"describe", type}
])
```

- The server **verifies** each op against the catalog (type exists, setting key
  and value legal, link group 1-4, target id exists) and returns an
  instructive error per op — wrong ops never reach the page.
- **Business data is computed on the server, the page only presents it.** A
  screen written into the Screener is run through the same screen engine in the
  tool, so the model gets the matches back in the same call (and the page draws
  the same spec). Code written into the Code widget is validated by the same
  sandbox validator before it is shown as saved. One derivation, never two.
- Valid ops stream to the page immediately as `view_op` events (the desk builds
  while the model is still talking) and are applied through `Dock.agent.*`.
- The page records each op's outcome; the next turn's envelope carries `ack`.
- Before the first op of a turn the page snapshots the dock state;
  **Undo** in the reply's footer restores it.

### 4. Tool loading (namespaces + hosted tool search)

Always loaded (core, chart-shaped, used on most turns): get_bars, get_patterns,
get_indicator, read_indicators, mark, draw_shape, get_levels, get_trend,
get_trendlines, get_anchors, get_gaps, get_divergences, multi_timeframe,
volume_profile, open_chart, read_symbol, compare_symbols, screen_universe,
scan_setup, explain_move, paper_portfolio, **workspace**.

Deferred namespaces (each <10 functions, one-line purpose):

| namespace | tools |
|---|---|
| alerts | set_alert, update_alert, check_alert, list_alerts, cancel_alert |
| journal | log_trade, update_trade, list_trades, update_journal_trade |
| paper_strategies | save_strategy, list_strategies, pause_strategy, delete_strategy |
| indicator_builder | custom_indicator |
| drawing_checks | evaluate_drawing, evaluate_fib, evaluate_pattern, evaluate_line, read_drawing, evaluate_results |
| fundamentals_flows | get_results, get_flows, get_deals, get_peers, search_news |
| position_planning | plan_position, confirm_reversal |
| memory | recall_conversations |

Execution mode keeps its own surface unchanged. A user's own model (BYOK) may
not support hosted search: those turns get a client-side `load_tools(groups)`
function with the same groups (Claude Code's ToolSearch shape) — the model
still chooses; code only adds the definitions for the next round.

### 5. What this unlocks (the prompts it must win)

- "Set me up for banks today" → chart + screener (banks, sorted by 1D) + news
  muted to banking + calendar on results + watchlist of the top names, all in
  link group 1.
- "Screen oversold IT stocks and put them in a sheet" → screener written, the
  matches returned to the model, a sheet with the table.
- "Open the code for my volume indicator and make the length 30" → Code widget
  opened on that study, edited, validated, saved — or the failing check quoted.
- "Clean this up" → reads the manifest, closes duplicates, tabs the research
  widgets together.
- "What is my screener showing?" → reads the widget, answers from its model view.

## Phases

| # | Work | Done when |
|---|---|---|
| 0 | **Baseline**: 8 live prompts (core + deferred-tool + workspace-free) → tokens, TTFT, total, quality | Numbers recorded |
| 1 | `Dock.agent` API (open with id, write, read, setLinkOf, where→place, snapshot/restore) + `agent` blocks on all 17 widgets | Every widget opens/configures/reads; 8 widgets accept content |
| 2 | `workspace` tool: verify → stream `view_op` → apply → ack; manifest in context; Undo | Multi-op desk builds live in one call |
| 3 | Server-side business writes: screener via screen engine, code via validator, strategy via lab, sheet tables | Model gets results back in the same call |
| 4 | Namespaces + hosted tool search (+ client `load_tools` for BYOK); replay search items in-turn | Deferred tools load and run; no extra round |
| 5 | Live multi-turn eval (same 8 + 6 workspace prompts), fix, retest once; report the triad and the before/after | Report delivered |

## Risks and how they are handled

- **Agent changes persist** (the dock saves to the account store) → turn-level
  Undo; the reply names what changed.
- **Page fails to apply an op** after the tool said ok → the `ack` loop tells
  the model next turn; the reply never claims a widget content the page did
  not accept (writes are verified server-side first).
- **Cache**: the volatile manifest goes after the history; loaded tools are
  appended at the end by the API.
- **Phone (compact) layout**: ops still apply; placement collapses to the sheet.
- **Untrusted content into widgets**: notes HTML goes through the widget's own
  `clean()`; code only ever runs in the sandbox; URLs only into the browser
  widget's existing navigation.
