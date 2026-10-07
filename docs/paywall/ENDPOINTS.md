# Paywall endpoint inventory — charto dataserver (:5174) + AI consumers

Verifier, 2026-09-28. Read from `charto/data/dataserver.py` (`Handler.do_GET` L15896,
`do_DELETE` L16508, `do_POST` L16544, `_account_post` L15693) and `charto/data/alerts.py`.
Line numbers are as of this read; the code wins.

Tags: **GATE** (must check an entitlement) · **METER** (consumes a quota) · **OPEN** · **ADMIN** (should not be user-reachable).

## 0. How a request is authenticated — the enforcement hook

- `_auth_user(headers)` (dataserver.py L14235) → `(user_id, email, name)` or `None`.
  Reads `Authorization: Bearer <token>`, joins `sessions`→`users` in `charto_users.db`
  (under `_users_lock`), 30-day TTL, touches `last_seen`.
- Every authed branch calls it inline: `me = _auth_user(self.headers); if not me: 401`.
  There is no middleware. Enforcement = one call after `me` is resolved, e.g.
  `entitlements.check(me[0], "alerts.price", ...)`.
- `/chat` does NOT require auth: it sets `_req.user = _auth_user(...)` (L16854) and
  proceeds with `None`. Tools read `_req.user` (e.g. `tool_set_alert(user_id=…)`).
- `/stream` uses `EventSource` (main.js L400), which **cannot send a Bearer header** —
  anything per-user on `/stream` needs a query token or a switch to fetch-SSE
  (alerts.js already made that switch for `/alerts/stream`).
- `/api/auth/*` is rewritten to `/auth/*` at the door (`_strip_api_auth`).
- Plan state does not exist yet: `users` has no plan column; `charto_users.db` is the
  whole user-state plane (DATA_MAP) so any plan/usage table goes there, same lock.

## 1. GET routes

| Path | Auth | What it does | Spec row | Tag |
|---|---|---|---|---|
| `/health` (`?deep=1`) | no | health report | — | OPEN |
| `/api/workflows[/…]`, `/api/strategies`, `/api/portfolio/performance`, `/strategies/baskets`, `/users/option-strategies` | yes | Pivot-shaped reads of paper book / strategies | — | OPEN |
| `/api/*` (else) → `api_route()` L4545: `markets/quote`, `markets/…/sparkline`, `financials/*`, `markets/metric-series`, `stock/<sym>/{flows,deals,sections,patterns,peers,mix,scores,quarters}`, `companies/search`, `companies/logos` | no | company page data, cached | — | OPEN |
| `/symbols` | no | universe + names + logos | — | OPEN |
| `/bars?symbol&interval&to&limit` | **no** | OHLCV; `interval ∈ INTRADAY_MIN(1m,3m,5m,15m,30m,1h) ∪ 1d,1w,1mo`; `limit` default 3000, **hard cap 20000** (L16036); `to` = paging cursor so depth is unbounded by paging | Historical bars (Free 10K) | **GATE** (cap `limit` AND total depth reachable via `to`; needs auth or anon default) |
| `/quotes?symbols=` | no | watchlist prices, ≤120 symbols | — | OPEN |
| `/screen/features` | no | daily feature matrix | — | OPEN |
| `/screen/run?spec=` | no | market-wide technical scan (same engine as `screen_universe`) | Screener usage (not a spec row) | OPEN (note: stateless, no save) |
| `/indicators` | no | indicator catalogue | — | OPEN |
| `/indicator?name&interval&limit&…` | **no** | computes ONE indicator server-side; `limit` cap 20000 | Indicators per chart — server sees one at a time, **cannot count per chart here** | OPEN (cap `limit` same as bars) |
| `/volume_profile` | no | VP scene | counts as an indicator? | OPEN |
| `/patterns/draw` | no | draw a detected pattern | — | OPEN |
| `/live?venue&symbols&stop&status` | **no** | **starts/stops venue WS drivers in-process** | — | **ADMIN — unauthenticated today (security finding)** |
| `/replay?date&speed&stop` | no | bar replay driver | (TV: Bar Replay is intraday-paid) | OPEN (optionally GATE intraday replay) |
| `/stream?symbol` | no (EventSource) | live forming-bar SSE, one per open chart | **Parallel chart connections** | **GATE** (count concurrent streams per user; needs token in query) |
| `/company?symbol&range` | no | company page payload | — | OPEN |
| `/shared?token` | no (link) | read-only shared layout | — | OPEN |
| `/alerts` | yes | list alerts + log + vocab | — | OPEN (return usage vs limit here for UX) |
| `/alerts/stream` | yes | alert fire SSE | — | OPEN |
| `/paper/{summary,holdings,orders,fills,nav,account/mode}` | yes | paper book | — | OPEN |
| `/strategies[/<id>]` | yes | strategies | — | OPEN |
| `/brokers/{list,orders,<b>/callback}` | yes | broker catalog / OAuth return | — | OPEN (dormant rails) |
| `/plans[/<id>]` | yes | registered plans | — | OPEN |
| `/journal[/bootstrap|/trades/<id>]` | yes | journal | — | OPEN |
| `/auth/me` | optional | current user (+ should carry plan/entitlements) | all (client mirror) | OPEN — **add `plan` + `entitlements` + `usage` here** |
| `/workspace?symbol` | yes | per-symbol workspace state | — | OPEN |
| `/layouts[?id|?name|?thumbs]` | yes | list / get layout | — | OPEN |
| `/meta?symbol` | no | bar count/min/max + model name | — | OPEN |

## 2. POST routes

| Path | Auth | What it does | Spec row | Tag |
|---|---|---|---|---|
| `/audio/transcribe` | yes | raw audio → transcript (paid API) | not in sheet | METER candidate (count as AI credit or its own cap) |
| `/brokers/<b>/{connect,reconnect,disconnect,live,login_url}` | yes | broker rails (dormant) | — | OPEN (live stays off) |
| `/paper/orders/<id>/cancel`, `/paper/snapshot` | yes | paper book | — | OPEN |
| `/strategies`, `/api/strategies` (create), `/strategies/<id>` (patch), `/strategies/<id>/delete` | yes | save/arm/patch/retire strategy | not in sheet | OPEN (a strategy-count cap would go here) |
| `/plans`, `/plans/<id>/{activate,retire,delete}` | yes | plans | — | OPEN |
| `/journal/{trades,trades/<id>,playbooks,playbooks/<id>}` | yes | journal | — | OPEN |
| `/alerts` (create) → `alerts.api_create` L1628 | yes | arms an alert | **Alerts P / Alerts T / Multi-condition / Alert expiry** | **GATE + METER (count LIVE rows)** |
| `/alerts/check` → `api_check` | yes | dry-run resolve, writes nothing | — | OPEN |
| `/alerts/seen` | yes | mark log seen | — | OPEN |
| `/alerts/<id>` → `api_patch` L1671 (`{delete}`, `state:"armed"` re-arm, `when/interval/all` edit, `expires`) | yes | edit / pause / re-arm / delete | **Alerts P/T (edit can turn price→technical; re-arm of a paused/fired row re-enters the live count), Alert expiry (patch can set `expires:null`)** | **GATE** |
| `/auth/{signup,login,google,logout}` | no | auth | — | OPEN |
| `/workspace` | yes | upsert per-symbol state (drawings, indicators list lives in client Store key `indicators`) | Indicators per chart (only server-visible place, if the FE syncs that key) | GATE (optional; primary enforcement is client + `/chat` tool) |
| `/conversations` | yes | mirror chat archive | — | OPEN |
| `/layouts` (save/copy/share/thumb/autosave/delete) → `_layout_save` L14572 | yes | save a layout; spec = `{v, grid, charts:[{symbol,interval}…], workspace}` (layouts.js `snapshot()` L51) | **Charts per tab** (`len(spec.charts)`, grid ids go up to 8 panes: `g24,g42,c8,r8` in panes.js) | **GATE** on save/copy (count `spec.charts`); never on delete |
| `/suggest` | **no** | LEGACY follow-up LLM stream | AI credits | **METER or delete** (unauthenticated LLM spend) |
| `/execution/backtest` | **no** | runs `backtest_workflow` via pivot engine | not in sheet | OPEN (should at least require auth — heavy CPU) |
| `/chat` (`stream:true` → `_send_stream`, else `llm_chat`) | **optional** | the chart chat, all ~50 tools | **AI credits** (+ tools inside: `set_alert`/`update_alert` → alerts gate, `get_indicator(draw=True)`/`read_indicators` → indicators, `open_chart(layout=)` → charts per tab, `get_bars` → bars depth, `explain_move` → AI summary candidate) | **METER** (one debit per user turn, keyed by an idempotency key; decide anonymous policy) |

## 3. DELETE

| Path | Auth | What | Tag |
|---|---|---|---|
| `/api/workflows/<id>`, `/strategies/<id>` | yes | retire strategy | OPEN |

## 4. How alert kinds are (not) distinguished — the core problem for Alerts P vs T

- `alerts` table has **no `kind` column, by design** (alerts.py header; CLAUDE.md §7).
  A rule is `{symbol, interval, when:[{left, op, right, right2, x, plus_pct, within}], all, freq, expires}`,
  ≤ `MAX_CONDITIONS = 4`, global `MAX_PER_USER = 200` live (state != 'fired') per user.
- So "price" vs "technical" must be **derived from the spec**, deterministically, at create
  AND at patch time. Proposed classifier, matching TradingView's own definition (RESEARCH.md §5:
  "technical when it is set for an indicator, drawing, or strategy"; channel and move-% ops are
  "always technical"):
  - **price** iff EVERY condition has `op ∈ {cross, cross_up, cross_down, above, below}`,
    a price-field left (`close open high low hl2 hlc3 ohlc4`) and a plain-number right
    (optionally with `plus_pct`/`x`; a fixed session/window level such as `pday.high`,
    `52w.high` is a judgement call — recommend price, since it resolves to one number).
  - **technical** otherwise: any indicator call `name(...)`, `avg(...)`, `volume`, `poc/vah/val`,
    a drawing `draw:Dn…` (TV counts drawing alerts as technical), a detector
    (`pattern()`, `divergence()`, `results()`), or any op in `rises_pct/falls_pct/changes_pct/
    enters/exits/is_true`.
  - Tie-break: one technical condition makes the whole rule technical.
- Counting: TradingView counts *active* alerts. Recommend counting **armed** rows per class, and checking on create, on a `when` edit that changes class, and on re-arm (`state:"armed"`). That is consistent with the lead's downgrade plan: auto-pausing the over-limit alerts frees slots. Today's `MAX_PER_USER` counts `state != 'fired'` (armed + paused); choose one rule. `api_create` COUNTs and INSERTs under two separate `_users_lock` acquisitions, which is a race at the limit.
- **Watchlist alerts do not exist**: every alert is single-symbol (`symbol` is required).
  Watchlists themselves are **client-only** (`panels.js` `WL_KEY="watchlists"` in Store/
  localStorage, `WL_MAX = 12` lists) — the server has no watchlist table, so a watchlist
  alert needs a new stored list (server-side) before it can exist. Until then: spec row
  "Watchlist alerts" has nothing to gate.
- **Screen (screener) alerts do not exist** and **saved screens do not exist** in charto.
  `/screen/run` is stateless. The only saved-screens store is pivot's
  `/api/screener/screens` (GET/POST/DELETE, `require_pivot_user`, screener.py L1644-1724),
  and pivot API is not deployed. Both rows are "build first, then gate".
- **Alert expiry**: `expires` is an optional epoch on create/patch; `null` = open-ended.
  `tool_set_alert` sets it from `expires_in_days` (0 → open-ended). The gate must
  CLAMP (not refuse) `expires` to `created + plan.max_expiry` and treat `null` as the
  plan maximum on Free/Pro — including on `api_patch` (`expires: null` would otherwise
  make a Free alert eternal). Expired rules are skipped by `_load_index`/`_run_symbol`.
- Chat path: `tool_set_alert` → `api_create`, `update_alert` → `api_patch`. Gating inside
  `api_create/api_patch` (or one helper they both call) covers HTTP and chat in one place.
  The refusal must come back as a tool result with a `_note` so the model states the
  boundary honestly (non-negotiable: never narrate "armed" on a refusal).

## 5. Other spec rows mapped to code

| Spec row | Where it lives today | Server-enforceable? |
|---|---|---|
| AI credits | `/chat`, `/suggest` (legacy), pivotted `/research/chat/stream`, `/audio/transcribe` | yes — debit in `do_POST /chat` before `_send_stream` |
| AI summaries | no dedicated endpoint. Candidates: `explain_move` tool (web + bars "why did it move"), pivotted research answers. **Needs a definition from lead** | only once defined as a tool/route |
| Indicators per chart | client Store key `indicators` (main.js L664/L4265), computed one-by-one by `/indicator`; chat can add via `get_indicator(draw=True)` | partly: server can cap chat `draw=True` count via the envelope and `/workspace` saves; `/indicator` GETs are stateless |
| Charts per tab | `panes.js` LAYOUTS (1–8 panes), `/layouts` spec `charts[]`, `open_chart(layout=)` tool | yes on `/layouts` save + `open_chart`; the live grid is client-side |
| Parallel charts | `/stream` SSE (one per chart pane per tab) | yes, by counting open `/stream` connections per user — requires auth on `/stream` |
| Custom timeframes | **none**: intervals are a fixed whitelist (`INTRADAY_MIN`, `1d/1w/1mo`; `_OPEN_INTERVALS` L9820) | only after custom intervals are built (then gate at `/bars` interval validation) |
| Historical bars | `/bars` `limit` (cap 20000) + `to` paging; `/indicator` limit; chat `get_bars` | yes — clamp in `/bars` and `_rows`; cap depth by `to` ≥ earliest-allowed |
| Ad-free | no ads exist | client flag only |
| Watchlists (unlimited) | client-only, `WL_MAX=12` | nothing to enforce (note: unlimited contradicts WL_MAX=12) |
| Multi-condition alerts (all plans) | `MAX_CONDITIONS=4` | no gate |

## 6. pivot API (`pivot/backend/routers`) — AI-consuming routes only (not deployed)

| Route | Auth | Note |
|---|---|---|
| `POST /chat` (chat.py L814) | `_auth(authorization)`; rate_limit 40/60s | METER |
| `POST /chat/stream` (L985) | same | METER |
| `POST /chat/followups` (followups.py L43) | `get_user_id` | METER (small) or free |
| `POST /audio/transcribe` (audio.py L180) | `require_user`, 20/60s | METER candidate |
| `/api/conversations/*` | `require_user` | storage, not AI spend |
| `/api/screener/screens` GET/POST/DELETE (screener.py L1644+) | `require_pivot_user` | **Saved screens** gate would live here if pivot ships |

pivotted (:5176, `/research/`): `POST /chat/stream` (server.py L449) — **no auth read at all**
("Authorization matters even though nothing here reads it", L391). Unmetered LLM spend.

## 7. Routes that should be gated/authed but are not (for lead)

1. `GET /live` — unauthenticated start/stop of in-process venue drivers. ADMIN; lock down.
2. `POST /chat` — anonymous turns allowed; credits need an identity (or an anon quota by IP).
3. `POST /suggest` — anonymous legacy LLM stream; meter or remove.
4. pivotted `POST /research/chat/stream` — no auth, no meter.
5. `GET /bars`, `GET /indicator` — no auth, `limit` 20000 and unlimited `to` paging; the
   historical-bars row cannot be enforced without either auth or an anonymous default.
6. `GET /stream` — EventSource, no auth; parallel-chart counting is impossible until it
   carries identity.
7. `POST /execution/backtest` — anonymous CPU-heavy backtests.
8. `POST /alerts/<id>` patch — can null out `expires` and can change an alert's class;
   must go through the same gate as create.
