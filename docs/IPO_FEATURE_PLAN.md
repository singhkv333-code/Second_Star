# IPO Feature Implementation Plan — Chat-Native IPO Widget + Event-Triggered Application Tracking

Status: DRAFT PLAN — for review · Date: 2026-06-02 · Target branch: `Eventtriggers` · Author: Pivot lead architect

## 1. Executive summary

We are building a chat-native IPO experience in two layers. **Layer A** is a rich, partially-editable IPO widget that renders inline in chat (via the existing `_render_hint` → `raw_data` → `ChatDemo` dispatch contract), letting a user browse an open/upcoming IPO, edit the four fields that are actually user-controllable on every Indian broker (category, lots, cut-off/price, UPI ID), see an **estimated** amount-to-keep-available recompute live, and **register their application intent** — register-not-execute, exactly like `POST /orders/register`. **Layer B** is "set up the IPO for me," which we resolve honestly: because Pivot is **not** a SEBI-registered intermediary and never calls a broker, this means a workflow on the existing event-trigger engine that watches an IPO's open date, fires once on open day, arms a **handoff-and-reminder** sequence (open-day "you must apply yourself" nudge → close-day mandate-cutoff reminder → allotment-day registrar deep-link) — plus an optional **paper-mode simulation** that fakes allotment so users can forward-test an IPO strategy.

The verb **"apply" is never used for an action Pivot performs.** Pivot's own steps are named `arm`/`register intent`/`remind`; only the user's act in their own broker/UPI app is ever called "apply." The one thing v1 will **never** do is submit an ASBA bid or block funds; that requires a broker/sponsor-bank/RTA + UPI-PSP partnership, which we scope as Phase N and label explicitly. The autonomous open-day artifact is **not** a row called an "application" with a "blocked" amount — it is an `intent_armed`/`reminder_fired` record carrying an `amount_estimate` (never `amount_blocked`), and every autonomous notification leads with "Pivot has NOT applied — you must place and approve the bid yourself."

The data layer keeps `ipo_feed.py`'s honest-on-failure contract (`count:0` + `note`, never fabricate) and extends it additively for subscription %, listing date, RHP link, and a **deep-link-not-fetch** pattern for GMP and allotment status. A load-bearing new piece is `parse_price_band()`, which turns NSE's inconsistent `priceRange` **string** into numeric `{min, max, is_fixed}` (or `null` → hide amount math); the editable card's entire amount/validation layer rests on this. GMP, if shown at all, is fail-closed behind an OFF-by-default flag and rendered as a clearly-labelled "unofficial, unregulated, not exchange data" secondary chip that never drives a recommendation or pre-fills a bid. We reuse named existing patterns throughout: `WorkflowDraftCard.tsx` for the editable card, the polymarket trigger picker for ambiguous-vs-exact IPO selection, a shared `persist_ipo_application()` helper (the `_persist_leg` analog) for register+paper routing, and the `@register_step` registry for the two new step types. This is the smallest-honest-thing-first plan: P0 ships a demoable read-only-plus-register card in days, and broker-real-apply stays gated behind a partnership.

## 2. The "set up the IPO for me" question (resolved honestly)

### The hard regulatory wall (from research `hard_constraints`)

- **Only a SEBI-registered intermediary** (Syndicate Member / broker-Trading Member / RTA / DP) may upload an IPO bid to the exchange ASBA platform.
- **Funds are blocked, not debited**, at an SCSB via Sponsor-Bank + NPCI UPI mandate — which needs SCSB/Sponsor-Bank + UPI-PSP + SEBI registration.
- **The investor must personally approve the UPI mandate** with their UPI PIN in their own UPI app. No platform can approve it for them. Deadline: **5 PM on close day** or the bid is rejected.
- **One application per PAN; duplicates are rejected; up to 3 bids per application.** Pivot cannot enforce exchange-level PAN dedup, but it sets the expectation and soft-warns on duplicate intents.
- **Allotment timeline runs on TRADING days**, not calendar days: T = close day, **T+1 (trading day) allotment, T+2 unblock, T+3 listing.** All offsets use `is_trading_day()` / NSE holiday calendar, never raw `+1d`.

Pivot is none of these entities and (per v1 philosophy) makes **no broker call**. Therefore automating "the IPO application" **cannot** mean "Pivot silently submits and funds your bid." Any plan that implies otherwise is dishonest and exposes us legally. What we *can* legitimately do is everything **up to and around** the regulated bid: intent capture, timing, reminders, status-tracking handoff, and simulation — and we name every Pivot-side action accordingly (never "apply").

### What automation precisely means in v1

A v1 IPO automation is a **Workflow** (existing engine) shaped like this. Note every step type and template name — none uses "apply" as Pivot's verb:

```
[0] trigger.ipo_open       { symbol: "TIKONA" }                                  # watcher fires ONCE on upcoming→open edge
[1] action.arm_ipo_intent  { ipo_symbol: "TIKONA", quantity_lots: 2,             # writes intent_armed record (NOT an "application")
                             category: "retail", bid_price_mode: "cutoff" }      #   amount_estimate only; no broker call
[2] notify.message         { template: "ipo_open_handoff" }                      # "TIKONA is open. Pivot has NOT applied — open your broker/UPI app, place the bid, and approve the mandate by 5 PM yourself."
```

The close-day cutoff reminder and the allotment-day reminder **cannot self-schedule from inside this run** (notify steps fire inline; there is no `at:` deferred-send — see §5). They are modeled as **separate `trigger.schedule` workflows** anchored to trading-day-computed dates:

```
# Close-day mandate-cutoff reminder
[0] trigger.schedule  { next_run_at: <close_date 14:00 IST> }
[1] notify.message    { template: "ipo_mandate_cutoff" }   # "TIKONA closes today. Approve your UPI mandate in your UPI app by 5 PM or your bid is rejected. Pivot cannot do this for you."

# Allotment-day reminder (T+1 trading day)
[0] trigger.schedule  { next_run_at: <T+1 trading day, or announced RTA date from feed> }
[1] notify.message    { template: "ipo_check_allotment", vars: { registrar_deeplink: <RTA url> } }
```

So the lifecycle Pivot owns is: **Draft (editable card) → Register intent → (open day) auto-arm intent + "you must apply yourself" handoff → close-day 5 PM cutoff reminder → (T+1 trading day) allotment-day registrar deep-link.** The blocking/bidding/approval steps are explicitly the user's, in their broker/UPI app — the card and notifications *guide and remind*, they never execute, and every autonomous message says so.

> **Honesty note on notifications:** `notify.message` has **no real delivery channel wired** in the codebase today — it renders `template.format(**vars)` and delegates to an optional `backend/services/notify.send` that currently only logs. v1 reminders therefore land as run-step log lines, not push notifications, until a channel is built. We state this plainly rather than implying live push.

### Three automation models

| Model | What it does | Legal? in v1 | Effort | Recommendation |
|---|---|---|---|---|
| **A. Handoff + Reminders** (register-not-execute) | Card registers intent; workflow watches open date, fires **once** on the upcoming→open edge, writes an `intent_armed` record (amount_estimate only), then sends a "you must apply yourself" handoff; separate `trigger.schedule` workflows send the close-day 5 PM cutoff reminder and the T+1 allotment-day registrar deep-link. **No fund movement, no broker call, no row that reads as a bid.** | ✅ Fully within register-not-execute | Low–Med (reuses `persist_ipo_application`, `@register_step`, `notify.message`, `trigger.schedule`) | **PRIMARY — build this.** |
| **B. Paper / Forward-test simulation** | In PAPER mode the same `action.arm_ipo_intent` writes to a simulated `paper_ipo_allocation` ledger and fakes an allotment outcome (lottery: ~5% mainboard min-lot, ~50% SME min-lot), so users can forward-test "track every SME this quarter" without real money. | ✅ Clearly labelled simulation | Med (mirrors paper-broker routing) | **BUILD as P3 add-on to A.** Compelling demo, zero compliance risk. |
| **C. Real-broker partnership** | Pivot integrates a SEBI-registered broker's IPO API or deep-links into their app with the bid pre-filled; the broker uploads the bid and the user approves the UPI mandate in their UPI app. | ⚠️ Only via a registered partner; out of v1 | High (legal + integration + KYC pass-through) | **Phase N, GATED.** Design the card CTA and `ipo_applications` schema today so this slots in later with a status transition, not a rewrite. |

**Why A is the recommendation:** it is the *only* model that is both immediately shippable and unambiguously compliant. It delivers the core value ("I told Pivot I want this IPO and it made sure I didn't miss the window") without ever pretending to do something we legally cannot. B is a low-risk, high-delight extension that fits the existing paper book. C is the eventual monetizable endgame but must wait for a partner; we de-risk it by making the schema and CTA forward-compatible now.

### "What would need a broker/sponsor-bank/RTA + UPI-PSP partnership" — OUT OF V1

Explicitly out of scope until a partnership exists:
- Uploading an actual ASBA bid to NSE/BSE.
- Creating/blocking funds via a UPI mandate or net-banking ASBA.
- Approving a mandate on the user's behalf (impossible by regulation — always the user's PIN).
- Real allotment-status lookup keyed by the user's PAN (CAPTCHA-walled RTAs; PII risk).
- Bid modification/cancellation against a live exchange book.

The card states this in plain language (see §10). The `ipo_applications.status` enum reserves `applied`/`blocked`/`allotted`/`rejected` for the partnership phase; v1 only ever writes `registered`/`withdrawn` (and, in paper mode, simulated `allotted`/`not_allotted`). The word "applied" exists **only** inside the reserved status enum, never as a Pivot-facing verb, CTA, step type, or template.

## 3. The chat IPO widget — design + editable fields

### Field spec

Render-hint constant (locked, byte-identical across tool return, payload, and FE dispatch): **`ipo_application_card`**. The card consumes a payload (§5) with `locked{}`, `editable{}` defaults, and `validation{}` metadata. Only **four** controls are editable — matching every Indian broker's actual field set; identity/KYC fields are read-only confirmation.

| Field | Source | Editable? | Validation / behaviour |
|---|---|---|---|
| `name` / `symbol` | NSE feed (`ipo_feed`) | No | Header; from `get_ipo_details`. |
| `type` (mainboard/sme) | NSE feed | No | Branches all validation constants (SME = min 2 lots / typically > ₹2L, no cut-off, no downward-revision/cancellation). |
| `price_band` (`min`,`max`,`is_fixed`) | NSE feed → `parse_price_band()` | No | Parsed from string; custom bid must lie within. If `null` (unparseable/fixed with no max), amount math + custom-price field are hidden and CTAs disabled. |
| `lot_size` | NSE feed (coerced to int) | No | Quantity must be exact multiple. If non-numeric, treated like unparseable band (hide math, disable CTAs). |
| `open_date` / `close_date` | NSE feed | No | Drives status + 5 PM cutoff (trading-day arithmetic). |
| **`category`** | User (default `retail`) | **Yes** (dropdown) | `retail \| snii \| bnii \| shareholder \| employee`. `retail` total ≤ ₹2,00,000 (mainboard). Cut-off allowed only for `retail`/`employee`. On category change, **recompute all gates and force-clear now-invalid selections** (e.g. switching to sNII clears cut-off and requires an explicit price). |
| **`quantity_lots`** | User (default 1; SME default 2) | **Yes** (stepper) | Integer ≥ min lots; snapped to lot multiples. Recompute `amount_estimate`. |
| **`bid_price_mode`** + **`bid_price`** | User (default `cutoff`) | **Yes** (toggle → price field) | Toggle ON = cut-off (retail/employee only; estimate uses `price_band.max`). Toggle OFF reveals price field constrained to `[min,max]`; required for snii/bnii/qib. |
| **`upi_id`** | User | **Yes** (text) | Regex `^[\w.\-]{2,256}@[a-zA-Z]{2,64}$`; success state reads **"format looks valid — must be your own PAN-linked account (we can't verify this)"**, never a bare green check. Hidden/greyed if `amount_estimate > ₹5,00,000` (UPI cap) with "use bank-ASBA via your broker" note. |
| `applicant_name`, `pan`, `demat_dp_id`, `depository` | User profile (read-only) | No | Shown **only if a real profile exists**, each value source-tagged. If absent, the block is **omitted entirely** and replaced with one line: "KYC details come from your broker — Pivot does not store your PAN/demat." Never render a fake masked PAN or defaulted depository. |
| `amount_estimate` (display preview) | Derived | No | `= lots × lot_size × (cutoff ? price_band.max : bid_price)`. Labelled **"Estimated ₹X you'll need available — it will be blocked (not charged) when you apply in your broker app."** Never "blocked" in past/present tense. |
| `subscription` (per-category ×) | Feed (live, open window) | No | Per-category (QIB/NII/RII/Emp). `null` → "subscription not available." Carries "as of HH:MM" + manual refresh. When the user's category is >1× oversubscribed, a contextual note appears **at the lots stepper**: "RII is 2.1× oversubscribed — allotment is a lottery; extra lots don't improve your odds." |
| `gmp` | Unofficial / deep-link | No | **OFF by default (fail-closed).** Absent from payload entirely when off. If vendor-gated on: subordinate chip, **"Unofficial grey-market estimate — not exchange/SEBI data, informational only,"** with `source`+`as_of`. Never drives anything. |
| `rhp_url` | Feed (SEBI/BSE link) | No | "View prospectus (RHP)" deep-link; hidden if `null`. |
| `registrar` + `allotment_deeplink` | Feed | No | "Check allotment on <RTA>" deep-link (post-allotment); hidden if `null`. |

### Cross-field validation matrix (explicit)

- **SME bypasses the ₹2,00,000 retail ceiling** — SME min 2 lots is intentionally > ₹2L, so the retail ≤ ₹2L guard applies to **mainboard retail only**.
- **Retail cut-off ceiling is evaluated at `price_band.max`** (the cap = the amount that would be blocked), never at mid/min. A lot count whose cap-amount exceeds ₹2L is rejected even if its mid-amount would pass.
- **UPI ₹5,00,000 cap is a hard submit-block** with a bank-ASBA redirect for the **entire sNII range that exceeds it** (sNII spans ₹2–10L; the portion above ₹5L must use bank-ASBA), independent of the category label. bNII (> ₹10L) always disables the UPI path.
- **Cut-off is retail/employee only.** Switching category away from these force-clears the cut-off toggle and requires an explicit in-band price.
- **SME:** no cut-off, no downward revision, no cancellation — surfaced as disabled affordances with one-line reasons.

### Card state machine (v1)

The card is a state machine, not a one-shot form. Transitions past `registered` are **user-reported / reminder-driven** (Pivot has no real-time broker visibility — stated plainly on the card):

```
idle → saving → registered(application_id)
registered → reminded_open      (open-day handoff fired)
registered → reminded_cutoff    (close-day 5 PM reminder fired)
registered → check_allotment    (T+1 trading-day reminder fired)
registered → withdrawn          (user taps Withdraw; allowed only while issue still open/upcoming)
closed/listed: read-only variant — register/automate disabled, only RHP + allotment-deeplink + listing tracking surfaced
```

Each read-only live field (subscription) shows "last updated HH:MM" + manual refresh. Pivot **cannot** observe mandate status; the card never implies it can — the post-register state shows "Action needed: approve the mandate yourself in your broker/UPI app by 5 PM" with a re-check affordance and recoverable guidance copy (verify the UPI is your PAN-linked account, check balance), never a "submitted"/"blocked" claim.

### Visual structure (extends `WorkflowDraftCard.tsx` pattern)

```
┌─ IpoApplicationCard ──────────────────────────────────────┐
│ HEADER   TIKONA · Mainboard · OPEN   [closes in 1d 4h]     │
│ FACTS    Price band ₹125–₹132 · Lot 110 · Issue ₹1,200cr   │
│ OFFICIAL Subscription: RII 2.1× · NII 0.8× · QIB 1.4×      │  ← read-only, "as of HH:MM" + refresh
│ (GMP chip only if vendor flag ON + disclaimer attached)    │  ← else absent entirely
│ ── EDITABLE ──────────────────────────────────────────────│
│ Category [ Retail ▾ ]                                      │
│ Lots     [ – ] 2 [ + ]   (= 220 shares)                    │
│   ⚠ RII 2.1× oversubscribed — lottery; extra lots ≠ odds   │  ← contextual, data-driven
│ ☑ Apply at cut-off (recommended)    [ price field hidden ] │
│ UPI ID   [ name@bank        ]  format looks valid          │
│          ↳ must be YOUR PAN-linked account (we can't check)│
│ ── PREVIEW ───────────────────────────────────────────────│
│ Est. ₹29,040 you'll need — blocked (not charged) when YOU  │
│   apply in your broker app.   UPI cap ₹5,00,000 ✓          │
│ ⏱ You must approve the UPI mandate by 5 PM on close day.   │
│ KYC: (shown only if real profile; else "comes from broker")│
│ ── CTAs ──────────────────────────────────────────────────│
│ [ Register intent ]  [ Set up reminders for open day ]     │
│ [ Withdraw ] (only while open/upcoming)                    │
│ ↳ status: idle → saving → registered (application_id shown)│
│ Pivot can't submit or fund the bid — YOU apply & approve   │
│ in your broker/UPI app. This registers your plan only.     │
└────────────────────────────────────────────────────────────┘
```

**CTAs:**
- **Primary — "Register intent"** → `POST /ipo-applications` (register-not-execute), card → `registered` with `application_id`. Soft duplicate-intent check (see §5).
- **Secondary — "Set up reminders for open day"** (never "Automate (apply...)") → proposes/creates the `trigger.ipo_open` workflow + the two `trigger.schedule` reminder workflows (reuses `createWorkflow` → `activateWorkflow`). Disabled if IPO already `open` (offers "Register now" instead).
- **Tertiary** — "Withdraw" (registered + issue still open/upcoming), "View RHP", "Check allotment" deep-links.

For ambiguous selection ("set up the upcoming IPO") we reuse the **polymarket trigger picker** two-mode pattern: exact symbol match → auto-pick the card; ambiguous → render a picker of candidate IPOs first.

## 4. Data layer plan — extend `ipo_feed.py` (+ new helpers)

Preserve the existing contract everywhere: browser-UA + cookie-warm, `_read_cache`/`_write_cache` (Redis) writing both `_CACHE_KEY` and `_CACHE_KEY+':raw'`, **honest-on-failure** (`count:0` + `note`, `source` records the endpoint or `"unreachable"`, never fabricate). The `_raw` JSONB already absorbs new fields with **no migration**. Strategic note (research): the NSE scrape is a **ToS violation and fragility risk** — flag to product/legal, and design the new fetchers so the Upstox IPO API (official, token-authed) can replace NSE later behind the same normalized shape.

**Load-bearing new helper — `parse_price_band(raw: str) -> {min: float, max: float, is_fixed: bool} | None`** (in `ipo_feed.py`): NSE delivers `price_band` as an inconsistent **string** (`"125-132"`, `"₹125 – ₹132"`, en-dash variants, fixed-price single values, or missing). This parser is the foundation for the card's amount math and band validation. On any unparseable input it returns `None`; the normalized record then sets `price_band: null`, and the card hides the amount preview + custom-price field, disables both CTAs, and shows "price details unavailable for this issue" — **never** defaulting to 0 or fabricating a max. Fixed-price issues set `is_fixed: true` with `min==max`. Unit tests cover all known NSE string variants.

| New field | Source | Access | Cache key / TTL | Failure behaviour |
|---|---|---|---|---|
| **Subscription %** (per-category) | NSE EIPO query server (`nseindiaipo.com/eipo/mktdata/v1/demand/{sym}`, `/catwise/{sym}`) **or** Upstox `/v2/ipos/{id}` | New `fetch_subscription(symbol)` in `ipo_feed.py`, **own cache key** | `ipo_feed:sub:{sym}` / **15 min** (open window only), independent of the 45-min list cache that backs `get_ipo_details` | `subscription: null` + per-call `note`; card shows "subscription not available." Verify EIPO is public before relying (open question #5). |
| **GMP** | Unofficial aggregators (block 403) | **DO NOT scrape.** Optional licensed vendor behind a **fail-closed OFF-by-default flag**; else **omit** | `ipo_feed:gmp:{sym}` / 30 min if vendor | Default **field absent entirely** (not null-with-shape). If vendor flag ON: startup assertion that the disclaimer string is attached or render is refused; `gmp:{value, source, as_of, unofficial:true}`. Never fabricate. |
| **RHP / financials link** | SEBI Filings → Public Issues; BSE DRHP; company/BRLM | `rhp_url` resolved from `_raw` or a small `resolve_rhp(symbol)` lookup | part of list cache / 45 min | `rhp_url: null` → hide the link. Never extract/estimate financials; only link the dated PDF. |
| **Listing date / gain** | Pre-listing: `open/close` + announced listing date from `_raw`. Post-listing: existing **yfinance** path | `listing_date` from feed (T+3 trading day if only computed); `listing_gain` from live symbol | n/a (live) | Pre: show announced date or "TBD." Post: `(listing_price − issue_price)/issue_price`; if symbol not yet queryable, "listing data pending." |
| **Allotment status** | RTA portals (KFintech/MUFG/Bigshare/Cameo) — CAPTCHA + PAN | **DO NOT fetch server-side.** Detect `registrar` from feed → build `allotment_deeplink` | `registrar` from list cache | `registrar: null` → generic "check with your broker/registrar." Never store/transmit user PAN for this. |

New normalized record additions (all optional, `_raw`-sourced, never invented):
```python
{ ...existing,
  "price_band": {"min": 125, "max": 132, "is_fixed": False} | None,   # via parse_price_band(); None hides amount math
  "subscription": {"rii": 2.1, "nii": 0.8, "qib": 1.4, "emp": None, "as_of": "2026-06-02T14:00:00+05:30"} | None,
  "gmp": <ABSENT unless vendor flag ON> ,    # never null-with-shape when off
  "rhp_url": "https://..." | None,
  "registrar": "KFintech" | None,
  "allotment_deeplink": "https://ris.kfintech.com/ipostatus/" | None,
  "listing_date": "2026-06-09" | None,
}
```

## 5. Backend contract

### New chat tool: `propose_ipo_application`

Added to `agents/tools.py` (IPO_QUERY group, tool def near the existing `list_upcoming_ipos`/`get_ipo_details` defs ~line 710), handler in `agents/tool_executor.py` handlers dict (~line 83, entries 49–83), registered in `services/tool_registry.py` IPO group (~line 62), **and added to the `tool_router.py` IPO regex target list (~line 149)** — the regex matches "apply for the X ipo" but currently surfaces only the read tools, so the new tool must be added to the surfaced set or it will never be offered. System prompt (`prompts/system.md` lines 120–125) extended with: *"When the user wants to apply to a specific open IPO now → call `propose_ipo_application` (this registers their intent; Pivot never submits the bid). When they want it set up automatically for open day, or 'track every SME this quarter' → propose a `trigger.ipo_open` workflow via `propose_workflow`."*

Handler `async def _propose_ipo_application(a, kt, db, uid)` (signature verified against `_propose_workflow`/`_list_upcoming_ipos`):
1. `get_ipo_details(symbol)` — if `not found` or feed `count:0`, return honest "couldn't find that IPO" (no card). Distinguish empty (`"NSE reports no open/upcoming IPOs"`) from unreachable (error `note`).
2. Build the payload below. Pre-flight: validate `symbol` exists (not hallucinated); run `parse_price_band` and `lot_size` coercion — if either fails, emit the card with `price_band:null` and CTAs disabled (mirrors `propose_workflow`'s `check_draft` gate — hide CTAs that need missing data). If `status=="closed"`, emit the closed-issue read-only variant.
3. Return `{"success": True, "data": {<payload>, "_render_hint": "ipo_application_card"}, "logiccard": null}`. Router (`routers/chat.py` ~418–431 and streaming ~555–564) lifts `_render_hint` to top-level `raw_data` automatically — works on both paths.

**Card payload schema (`raw_data`):**
```jsonc
{
  "_render_hint": "ipo_application_card",
  "symbol": "TIKONA", "name": "Tikona Infinet",
  "type": "mainboard", "status": "open",
  "locked": {
    "price_band": {"min": 125, "max": 132, "is_fixed": false},   // null → hide amount math, disable CTAs
    "lot_size": 110,
    "open_date": "2026-06-03", "close_date": "2026-06-05",
    "issue_size": "₹1,200 cr", "rhp_url": "https://...",
    "registrar": "KFintech", "allotment_deeplink": "https://ris.kfintech.com/ipostatus/",
    "subscription": {"rii": 2.1, "nii": 0.8, "qib": 1.4, "as_of": "..."}
    // gmp omitted entirely unless vendor flag ON
  },
  "editable": {
    "category": "retail",
    "quantity_lots": 1,
    "bid_price_mode": "cutoff",      // "cutoff" | "fixed"
    "bid_price": null,
    "upi_id": ""
  },
  "kyc": null,   // null when no real profile → card shows "comes from your broker" line, NOT placeholders
  "validation": {
    "min_lots": 1, "lot_size": 110,
    "amount_estimate_at_cutoff": 14520,        // lots × lot_size × price_band.max (the cap)
    "retail_max_amount": 200000,               // mainboard retail only
    "sme_bypasses_retail_cap": true,
    "upi_cap": 500000,
    "cutoff_allowed": true,
    "price_band": {"min": 125, "max": 132, "is_fixed": false},
    "category_options": ["retail","snii","bnii","shareholder","employee"]
  },
  "automatable": true,
  "conversation_id": "s_...",
  "disclaimer": "Pivot can't submit or fund this bid. This registers your intent only; YOU place and approve the mandate in your broker/UPI app by 5 PM on close day."
}
```

### Shared persistence helper + register/automate endpoints

The IPOApplication write is factored into a shared **`persist_ipo_application(db, user_id, ...)`** helper (the `_persist_leg` analog), imported by **both** the router handler and the workflow step executor — the executor runs inside the engine with a `ctx` object (not an HTTP request), so it must call the function, never the endpoint. Verify `ctx` accessors for `db`/`user_id` against `execute_action_place_order` (`actions.py:259`) before writing.

**Mount decision (resolves the api.ts helper ambiguity):** the new router is mounted **bare** like `/orders` (no `/api` prefix), so **all** three FE fetchers use `requestLegacy` (matching how `/orders/register` is called at `api.ts:613`). All endpoints below are therefore non-`/api`.

**`POST /ipo-applications`** (new `routers/ipo_applications.py`) — register-not-execute, calls `persist_ipo_application`:
```jsonc
// body
{ "ipo_symbol": "TIKONA", "category": "retail", "quantity_lots": 2,
  "bid_price_mode": "cutoff", "bid_price": null, "upi_id_masked": "•••@bank",
  "conversation_id": "s_..." }
// → writes ipo_applications row status="registered", source="chat-confirm",
//   amount_estimate (NOT amount_blocked), conversation_id threaded.
//   Re-validates the IPO still exists & is not closed:
//     - feed unreachable → register anyway (it's just intent) with stale=true + "couldn't reconfirm live details" note
//     - status=="closed" → reject with honest message
//   Soft duplicate check: existing non-withdrawn row for same (user_id, ipo_symbol) → warn + offer replace
//   If should_use_paper(db, uid): ALSO write paper_ipo_allocation (simulated). [P3]
//   NO ASBA call, ever.
```

**`POST /ipo-applications/{id}/withdraw`** — sets `status="withdrawn"`; allowed only while the issue is still open/upcoming. Gives the `withdrawn` enum value a reachable path (closes the dead-state gap).

**`GET /users/ipo-applications`** — list the user's registered applications (FE dashboard/history). Surfaces `amount_estimate` labelled "estimated amount you'll need," never "blocked."

**`GET /ipo-calendar?from=&to=`** — **its own route in `ipo_applications.py`** (NOT under `/api/events`; the events router is `prefix="/api/events"` and its `EventCalendarItem` has no IPO fields). Backed by `ipo_feed.list_upcoming_ipos()`; returns a new `IpoCalendarItem[]` shape `[{ipo_symbol, name, open_date, close_date, price_band, status}]` for the Calendar tab and `trigger.ipo_open` surfacing.

### Mapping onto the event-trigger engine — new step types + registry

Two new `@register_step` entries (decorator shape per `workflows/registry.py:96` and trigger examples in `workflows/steps/triggers.py`). Add `TriggerIpoOpenConfig` and `ActionArmIpoIntentConfig` to `workflows/schemas.py`.

**`trigger.ipo_open`** (in `workflows/steps/triggers.py`, `trigger_only=True`, `max_retries=0`):
```python
@register_step(step_type="trigger.ipo_open", category="trigger",
  label="On IPO open", description="Fire when an IPO's subscription window opens",
  icon="rocket", max_retries=0, trigger_only=True, config_model=TriggerIpoOpenConfig)
async def execute_trigger_ipo_open(ctx) -> None:
    return None  # no-op: a dedicated watcher fires the run, like trigger.price/event
```
Config: `{symbol: str}`. **(Dropped `min/max_price_band` — they were dead config; the feed's `price_band` is a string the trigger can't numerically compare, and firing is on status transition, not price. Any "only arm if band within X" guard belongs in `action.arm_ipo_intent`, not the trigger.)**

**Watcher — a SEPARATE poll path, not a reuse of `_poll_watch_triggers`.** Verified: `_poll_watch_triggers`/`_scan_active_watch_triggers` (`scheduler.py:467–573`) is hard-gated behind `is_trading_day() and is_market_open()`, scans only a fixed step-type allowlist (`trigger.price/indicator/compound/exit_compound/event`), and batches a price-quote fetch — none of which fits a daily, calendar/feed-driven IPO-open check. We add a dedicated low-frequency `_poll_ipo_open_triggers` (every ~15–30 min, **not** gated to intraday market hours, since open-status is readable any time; this cadence also avoids amplifying the NSE-ToS/fragility risk that a 60s tick would create):
- Scans `Workflow.status=="active" AND step_type=="trigger.ipo_open"`.
- Calls `ipo_feed.list_upcoming_ipos()` once per poll (cache-backed), matches `config.symbol`.
- **Edge detection / fire-once latch:** `_persist_last_value` only accepts float/dict-of-float and silently drops strings, so it **cannot** hold the `"upcoming"/"open"` status. Instead we persist a fired-guid via a dedicated `_persist_ipo_fired` helper **modeled on the trigger.event `_persist_event_guid` pattern** (the string-dedup channel the price watcher does *not* use). Fire at most **once** per (workflow, IPO) on the upcoming→open edge — never re-fire on subsequent ticks while status stays `open`.
- **Feed-unreachable around the open window:** on repeated unreachable polls spanning the expected open date, send a `notify.message` "we couldn't confirm TIKONA opened — check your broker app" rather than silently never firing.
- Set `Workflow.expires_at = close_date + 1 trading day` (trading-day arithmetic, not `+1d`) so it auto-pauses after close (reuses existing `expires_at`, checked at `scheduler.py:299` and `_fire_watch_run` ~1146).

**`action.arm_ipo_intent`** (in `workflows/steps/actions.py`, `trigger_only=False`) — note the name: **`arm_ipo_intent`, never `apply_ipo`**:
```python
@register_step(step_type="action.arm_ipo_intent", category="action",
  label="Arm IPO intent + reminder", description="Record an IPO intent and hand off (no broker call, never submits a bid)",
  icon="file-check", max_retries=2, trigger_only=False, config_model=ActionArmIpoIntentConfig)
async def execute_action_arm_ipo_intent(ctx) -> dict:
    # resolves db/user_id from ctx (verify against actions.py:259), then calls the shared
    # persist_ipo_application(...) with autonomous=True → writes status="intent_armed",
    # amount_estimate only (NEVER amount_blocked). In PAPER mode writes paper_ipo_allocation.
    ...
```
Config: `{ipo_symbol: str, quantity_lots: int, category: str, bid_price_mode: str, bid_price?: float}`. The autonomously-created row is `status="intent_armed"` (reserved-but-distinct from user-initiated `registered`), carries `amount_estimate` only, and **every** notification it triggers leads with "Pivot has NOT applied — you must apply and approve the bid yourself."

**Close-day cutoff + allotment-day reminders — `trigger.schedule`, NOT `trigger.event`.** Verified: `trigger.event` is a **news-article classifier** (delegates to `execute_fetch_news`, fires on keyword/confidence match) with **no `event_type` field and no date-based firing**; `events_calendar.py` only buckets `rbi_rate_decision/company_results/fii_flow` by keyword. A fixed-date reminder cannot be a news-classification trigger. We use **`trigger.schedule`** workflows with `next_run_at` set to the trading-day-computed close-day 14:00 IST and T+1 allotment date (preferring an announced RTA date from `_raw` when present), each feeding a `notify.message`. No new trigger type needed for the reminders; the only new trigger is `trigger.ipo_open`.

**Migration for steps:** the registry is in-process (no DB rows), so adding step types needs only code + a bumped `CATALOG_VERSION` in `registry.py` (so the FE invalidates its catalog cache). The only DB migration is the new `ipo_applications` table (§7). New step types must also pass `routers/workflows.py` `_validate_steps` (rejects unknown `step_type` with 422; step 0 must be `trigger.*`).

## 6. Frontend contract

Files to touch (all under `pivot-next/`):

- **`components/chat/ChatDemo.tsx`**
  - Add Message kind (~line 624–637 union): `| { kind: "ipo_application"; payload: IpoApplicationPayload; intro: string }`.
  - Add dispatch case in `resolveStreamingMessage` (~line 858–912), matching the **exact** hint constant: `if (hint === "ipo_application_card" && rawData) finalMessage = { kind: "ipo_application", payload: rawData as IpoApplicationPayload, intro: data.response ?? "" }`. (Must byte-match the tool's `_render_hint` or it falls through to the plain-text `else`.)
  - Render the new card in the message map.
- **`components/chat/IpoApplicationCard.tsx`** (NEW) — extends the `WorkflowDraftCard.tsx` template (props struct → editable controls → state machine → CTAs). Implements: category dropdown (with on-change gate recompute + force-clear of invalid selections), lots stepper (snap to lot multiples; contextual oversubscription note), cut-off toggle revealing a band-constrained price field, UPI-ID input with regex validation + the "must be your own PAN-linked account (we can't verify)" copy, **live `amount_estimate` recompute** (`lots × lot_size × (cutoff ? band.max : bid_price)`; hidden when `price_band` is null), UPI-cap hard-block (> ₹5L disables UPI + bank-ASBA note, applied across the sNII range), mainboard-retail ₹2L guard at cap (SME bypasses), KYC block **only if real profile else omitted**, full state machine (`idle→saving→registered→reminded_*/check_allotment/withdrawn`; closed/listed read-only variant), disclaimer line. Primary CTA → `registerIpoApplication()`; Withdraw CTA → `withdrawIpoApplication(id)`; secondary CTA → `propose/createWorkflow()`+`activateWorkflow()` for the reminder workflows. Uses `ApiResult<T>`/`isError()` (`lib/api.ts`).
- **`components/chat/IpoListCard.tsx`** (NEW, P0.5) — browse open/upcoming IPOs (from `list_upcoming_ipos`), each row → "Apply"/"Open" loads `IpoApplicationCard`. Mirrors the polymarket **picker** for the ambiguous case. Distinguishes **empty** ("NSE reports no open/upcoming IPOs") from **unreachable** (feed-error note).
- **`components/chat/steps/IpoStepRow.tsx`** (NEW) — renders `trigger.ipo_open` and `action.arm_ipo_intent` step rows in `WorkflowDraftCard` (open/close dates, lots, category) — mirrors the existing `NewsStepRow`/event-step rendering.
- **`lib/api.ts`** — add (all via **`requestLegacy`**, since the router is bare/non-`/api`): `registerIpoApplication(body)` → `POST /ipo-applications`, `withdrawIpoApplication(id)` → `POST /ipo-applications/{id}/withdraw`, `listMyIpoApplications()` → `GET /users/ipo-applications`, `getIpoCalendar(from,to)` → `GET /ipo-calendar`. Same `ApiResult` shape as `createWorkflow`/`activateWorkflow`.
- **`lib/types.ts`** — `IpoApplicationPayload`, `IpoApplication`, `IpoCalendarEntry`.
- **Calendar tab** (component that consumes `events_calendar`) — add IPO open/close + allotment entries from `getIpoCalendar` (its own endpoint, not the events router).

## 7. Data model / migrations

One new table (P0); a second for paper (P3); everything else reuses existing tables/`_raw`. Add to `pivot/backend/models.py`.

**`ipo_applications`** (mirrors `TradeLog` shape + `conversation_id` attribution):
```python
class IPOApplication(Base):
    __tablename__ = "ipo_applications"
    id: int (pk)
    user_id: int (fk users.id, indexed)
    ipo_symbol: str (indexed)
    ipo_name: str | None
    ipo_type: str           # "mainboard" | "sme"
    category: str           # "retail" | "snii" | "bnii" | "shareholder" | "employee"
    quantity_lots: int
    lot_size: int
    bid_price_mode: str     # "cutoff" | "fixed"
    bid_price: float | None
    amount_estimate: float  # display/audit ONLY — the amount the user WOULD need available; NOT a block that occurred
    upi_id_masked: str | None   # masked/last-segment only; NEVER used to block funds; minimal-PII retention
    status: str             # v1 user: "registered" | "withdrawn"; v1 autonomous: "intent_armed";
                            #   reserved for Phase N: "applied"|"blocked"|"allotted"|"not_allotted"|"rejected"
    autonomous: bool        # True if written by action.arm_ipo_intent (user absent) — drives "Pivot has NOT applied" copy
    paper_mode: bool        # default False
    stale: bool             # default False — set True if feed unreachable at register time
    conversation_id: str | None   # soft ref, forward-test attribution
    workflow_id: int | None       # soft ref if created via automation
    created_at / updated_at: datetime (IST)
```

> **Naming honesty:** the column is **`amount_estimate`, not `amount_blocked`** — nothing is ever blocked in v1, so a persisted `amount_blocked` would be a misstatement that leaks into history/dashboard views. Everywhere it surfaces (payload, FE history) it is labelled "estimated amount you'll need (blocked when YOU apply in your broker app)."

> **Data-retention / consent (one-liner):** an `ipo_applications` row is financial-intent PII (quantity, category, masked UPI, conversation_id) that is **never executed** in v1. Purpose: reminders + (Phase N) optional hand-off to a registered broker the user explicitly chooses. We store masked UPI only, retain for the issue lifecycle + a short audit window, and surface a withdraw path; full lawful-basis copy to be finalized with legal before commercial launch.

**`paper_ipo_allocation`** (P3, simulation ledger; soft-refs paper account; clearly-labelled simulated):
```python
id, paper_account_id, ipo_application_id, ipo_symbol,
quantity_applied, quantity_allotted, allotment_date,
fill_price, fill_date, simulated: bool = True   # "simulated" vocabulary fine here because clearly labelled
```

**Alembic note:** migrations live in **`pivot/migrations/versions/`** (NOT `pivot/backend/alembic/versions/`). New revision **`0014_ipo_applications.py`**, `down_revision="0013_paper_trading"` (or the exact revision id inside `0013`). `op.create_table("ipo_applications", ...)` (+ `paper_ipo_allocation` in P3). Indexes on `(user_id, ipo_symbol)` and `(user_id, status)`. Project GOTCHA: SQLite `create_all` masks PG uuid-FK / missing-table divergences — keep `workflow_id`/`conversation_id` as **soft refs** (no hard FK), matching the paper-trading precedent. Apply to real Postgres + restart `:8000` (same flow as `0013`).

## 8. Phased build plan

**P0 — Editable read-only-plus-register card (smallest honest demoable thing).**
Ship `propose_ipo_application` tool + `ipo_application_card` render-hint + `IpoApplicationCard.tsx` + `parse_price_band()` + `POST /ipo-applications` (+ withdraw) + `persist_ipo_application()` + `ipo_applications` table. Editable lots/price/category/UPI with live `amount_estimate` preview and the full cross-field validation matrix; primary CTA registers intent. **Resolve open question #4 (KYC presence) before P0** since it changes whether the KYC block ships.
*Acceptance:*
- "apply for the TIKONA IPO" renders the card; editing lots updates "Est. ₹X you'll need."
- **Fixed-price SME issue** (unparseable/no-max band) → amount preview + custom-price hidden, CTAs disabled, "price details unavailable" — never 0/fabricated.
- Mainboard-retail cap-amount > ₹2L is rejected even when mid-amount passes; **SME bypasses the ₹2L cap**; UPI > ₹5L hard-blocks with bank-ASBA note across the sNII range; switching category off retail/employee force-clears cut-off.
- "Register intent" writes `ipo_applications` row `status="registered"`, `amount_estimate` (no `amount_blocked` column exists), `conversation_id` threaded; duplicate non-withdrawn intent for same (user, symbol) warns + offers replace; **Withdraw** transitions to `withdrawn` only while open/upcoming.
- Honest "not found" when the symbol isn't in the feed; **empty vs unreachable** copy differ; **closed-issue** card is read-only (register/automate disabled, allotment-deeplink only).
- KYC block absent (no placeholder PAN) when no real profile; present + source-tagged when profile exists.
- **CI regulatory-boundary test:** assert NO code path (chat register, executor, paper) calls any broker/ASBA/UPI-mandate API.

**P0.5 — Browse/list card + picker.** `IpoListCard.tsx` from `list_upcoming_ipos`; ambiguous "set up the upcoming IPO" → picker (polymarket pattern); exact symbol → straight to the card.
*Acceptance:* "any IPOs open right now?" lists open IPOs; empty-vs-unreachable states differ; "set up the upcoming IPO" shows a picker when >1 candidate.

**P1 — Data-layer enrichment (honest).** Extend `ipo_feed.py` with `fetch_subscription` (own 15-min cache key), `resolve_rhp`, `registrar`/`allotment_deeplink`, `listing_date`; render read-only with "as of HH:MM" + refresh + contextual oversubscription note; GMP **field-absent by default**, fail-closed flag with mandatory-disclaimer startup assertion.
*Acceptance:* subscription shows per-category × or "not available" (never blended/fabricated); RHP/allotment deep-links resolve or hide; **GMP off → field absent from payload entirely** (not null-with-shape); oversubscription note appears at the stepper when the user's category is >1×; failures show a `note`.

**P2 — Event-triggered reminders.** `trigger.ipo_open` + `action.arm_ipo_intent` step types + dedicated `_poll_ipo_open_triggers` watcher (fire-once latch via `_persist_ipo_fired`) + `IpoStepRow.tsx` + secondary "Set up reminders for open day" CTA proposing the `trigger.ipo_open` workflow **and** the two `trigger.schedule` reminder workflows; `expires_at = close_date + 1 trading day`; open-day handoff `notify.message`; close-day 5 PM + T+1 allotment `trigger.schedule` reminders; `GET /ipo-calendar` (own route) + Calendar-tab entries.
*Acceptance:* "Set up TIKONA for open day" creates active workflows; the watcher fires **exactly once** on a simulated upcoming→open flip → writes `intent_armed` (amount_estimate only) → sends a handoff whose copy leads with "Pivot has NOT applied"; **no re-fire** on subsequent ticks while open; feed-unreachable around open → "couldn't confirm it opened" message (not silent); workflow auto-pauses after close (trading-day expiry); allotment-day `trigger.schedule` fires on the T+1 trading day (or announced RTA date) with the registrar deep-link. **Honesty caveat asserted:** notifications land as run-step log lines (no real push channel wired) — test verifies the log content, not a delivered push.

**P3 — Paper-mode simulation.** PAPER-mode `action.arm_ipo_intent` writes `paper_ipo_allocation` with a simulated lottery outcome (~5% mainboard min-lot, ~50% SME); surface in the Paper dashboard, clearly labelled "simulated."
*Acceptance:* a paper-mode user tracking a quarter of SME IPOs sees simulated intents and fake allotments attributed to the originating `conversation_id`; clearly labelled "simulated"; no real fund movement.

**P4 — Listing-day tracking.** Post-listing, compute `listing_gain` from the now-live symbol (existing yfinance path); card flips to a "listed" read-only state.
*Acceptance:* for a just-listed symbol, the card shows listing gain vs issue price, or "listing data pending" if the scrip isn't queryable yet.

**PN (GATED) — Real-broker partnership.** With a registered partner, the secondary CTA deep-links into the broker app with the bid pre-filled (or calls the partner's IPO API); `ipo_applications.status` transitions `registered → applied → blocked → allotted/...` from partner webhooks. **Blocked on legal + partnership.**
*Acceptance:* only with a signed partner: real bid handed off; Pivot still never blocks funds or approves the mandate; "apply" appears as a user-side/partner-side verb only.

## 9. File-by-file change list

**Backend (new):**
- `pivot/backend/routers/ipo_applications.py` — `POST /ipo-applications`, `POST /ipo-applications/{id}/withdraw`, `GET /users/ipo-applications`, `GET /ipo-calendar` (bare/non-`/api` mount; register-not-execute; calls `persist_ipo_application`).
- `pivot/migrations/versions/0014_ipo_applications.py` — create `ipo_applications` (+ `paper_ipo_allocation` in P3); `down_revision="0013_paper_trading"`.

**Backend (edit):**
- `services/ipo_feed.py` — add **`parse_price_band`** (load-bearing, unit-tested), `fetch_subscription` (own cache key/TTL), `resolve_rhp`, `detect_registrar`, `listing_date`; extend normalized record; same cache/honest-on-failure pattern; GMP field-absent-when-off.
- `services/ipo_applications_service.py` (NEW or inside the router module) — **`persist_ipo_application(db, user_id, ...)`** shared helper (the `_persist_leg` analog) used by both the router and the executor.
- `agents/tool_executor.py` — add `_propose_ipo_application` handler + register in handlers dict (~line 83).
- `agents/tools.py` — add `propose_ipo_application` tool def to IPO_QUERY group (def near ~line 710).
- `services/tool_registry.py` — register the new tool in the IPO group (~line 62).
- `services/tool_router.py` — add `propose_ipo_application` to the IPO regex **target list** (~line 149) so it's actually surfaced.
- `prompts/system.md` (lines 120–125) — guidance: register-intent vs set-up-reminders; never imply Pivot submits.
- `workflows/schemas.py` — `TriggerIpoOpenConfig` (`{symbol}` only), `ActionArmIpoIntentConfig`.
- `workflows/steps/triggers.py` — `@register_step trigger.ipo_open`.
- `workflows/steps/actions.py` — `@register_step action.arm_ipo_intent`.
- `workflows/scheduler.py` — NEW `_poll_ipo_open_triggers` poll path (not a `_poll_watch_triggers` edit) + `_persist_ipo_fired` fire-once helper; trading-day `expires_at`.
- `workflows/registry.py` — bump `CATALOG_VERSION`.
- `models.py` — `IPOApplication` (+ `PaperIpoAllocation` in P3).
- `routers/chat.py` — confirm `_render_hint` lifting covers `ipo_application_card` (no change expected; verify on both paths).
- `tests/` — `test_ipo_applications.py`, `test_ipo_feed_parse_price_band.py`, `test_ipo_feed_enrichment.py`, `test_workflow_ipo_steps.py` (incl. fire-once edge + no-broker-call boundary assertion).

**Frontend (new):** `components/chat/IpoApplicationCard.tsx`, `components/chat/IpoListCard.tsx`, `components/chat/steps/IpoStepRow.tsx`.
**Frontend (edit):** `components/chat/ChatDemo.tsx` (Message kind + exact-hint dispatch), `lib/api.ts` (4 `requestLegacy` fetchers), `lib/types.ts` (types), Calendar-tab component (IPO entries via `getIpoCalendar`).

## 10. Risks, compliance, and honesty guardrails

- **Naming never implies Pivot applies.** Step type `action.arm_ipo_intent` (not `apply_ipo`); CTA "Set up reminders for open day" (not "Automate (apply...)"); template `ipo_open_handoff` (not `ipo_open_apply_now`); autonomous row `status="intent_armed"` with `amount_estimate` (not an "application" with `amount_blocked`). "Apply" is only ever the user's act in their own broker, or a reserved Phase-N status value.
- **Autonomous-path disclaimer is wired to the notification, not just the card.** Every message from `action.arm_ipo_intent` and the `trigger.schedule` reminders leads with "Pivot has NOT applied — you must place and approve the bid yourself by 5 PM." The disclaimer lives where the user actually sees it during the absent-user flow.
- **`amount_estimate`, never `amount_blocked`.** Nothing is blocked in v1; the persisted column and every surface use future-conditional language ("you'll need available — blocked when YOU apply").
- **GMP fail-closed.** Flag defaults OFF; field absent (not null-with-shape) when off; startup assertion refuses to render GMP without the mandatory disclaimer attached. Legal sign-off is a **release gate**, not a launch-time TODO.
- **Never fabricate.** Every new field inherits `ipo_feed.py`'s honest-on-failure: on source failure, `null`/`count:0` + `note`, never an invented name/date/band/subscription/GMP/listing gain. Subscription is always per-category, never blended. `parse_price_band` returns `null` rather than guessing a max.
- **Pivot cannot see mandate status.** The card states plainly there is no real-time broker visibility; it shows guided, recoverable "approve the mandate yourself" copy and a re-check affordance — never a "submitted"/"blocked" claim, never a failure state Pivot cannot actually detect.
- **No allotment promises.** Allotment is a computerized lottery; extra lots do **not** improve per-application odds (surfaced contextually at the lots stepper when oversubscribed, plus the static line). Refund/unblock timing on non-allotment runs T+2 (trading day); listing T+3.
- **One application per PAN.** Pivot can't enforce exchange-level dedup; it soft-warns on duplicate intents for the same (user, symbol) and shows "only one application per PAN is valid at your broker."
- **No real fund movement in v1.** No ASBA upload, no UPI mandate, no fund block — enforced by a CI test asserting no broker/ASBA/UPI API is ever called. Disclaimer: *"Pivot can't submit or fund your IPO bid — only a SEBI-registered broker can. This registers your plan and reminds you; YOU approve the actual bid in your broker/UPI app."*
- **Allotment PII.** Never fetch RTA status server-side (CAPTCHA + PAN). Deep-link only; do not store/transmit user PAN.
- **NSE ToS exposure — data layer AND polling.** The cookie-warm scrape is a ToS/fragility risk; the IPO-open watcher runs at ~15–30 min (not 60 s) to avoid amplifying it, and we plan the Upstox-IPO-API migration behind the same normalized shape before commercial launch.
- **UPI ID handling.** Store **masked/last-segment only** (matching `upi_id_masked` in the POST body and schema); never used to block funds; minimal-PII retention with a documented lawful basis before launch.

*Deferred minor (intentional):* multiple-bids-per-application (up to 3) is **named but deferred** to a later phase — v1 models a single bid; the schema can grow a child `bids` table without a rewrite.

## 11. Open product questions (decisions the user must make)

1. **Automation model commitment** (the big one): confirm **A (Handoff + Reminders) for v1, B (paper sim) as P3, C (broker partnership) gated to PN** — or change the priority. Everything downstream depends on this.
2. **GMP:** keep field-absent/OFF for v1, or integrate a licensed vendor behind the fail-closed flag + disclaimer + legal sign-off? (Legal call, not just engineering.)
3. **Data-source migration timing:** keep the NSE scrape for v1 and accept the ToS risk, or prioritize the **Upstox IPO API** migration (needs an Upstox developer OAuth token) before launch?
4. **KYC fields source (must resolve before P0):** do we have applicant name / PAN / demat / depository in the user profile for the read-only confirmation block, or do we ship v1 with the block **omitted** + "comes from your broker"? This changes the P0 card surface.
5. **Subscription source:** confirm whether the **NSE EIPO query server is public** or restricted to members — picks EIPO vs Upstox vs omit for live subscription %.
6. **Recurring IPO automation** ("track every SME this quarter"): is the per-quarter recurring case in-scope for v1 (would add an `IPOApplicationSchedule` table mirroring `SIPSchedule`), or single-IPO automations only?
7. **Broker-partnership target** for PN: which registered broker (Upstox, Zerodha, Angel One) do we design the hand-off CTA and status webhooks against?
8. **Notification channel:** is wiring a real `backend/services/notify.send` delivery channel in scope for P2, or is v1 explicitly "reminders land as log lines" until a channel ships?

## What the reviews changed

- **Verb/naming overhaul (regulatory MAJOR):** `action.apply_ipo` → `action.arm_ipo_intent`; CTA "Automate (apply on open day)" → "Set up reminders for open day"; template `ipo_open_apply_now` → `ipo_open_handoff`; autonomous row `status="registered"` → `intent_armed`. "Apply" is now only the user's verb / a reserved Phase-N status.
- **`amount_blocked` → `amount_estimate` (regulatory MAJOR):** renamed the persisted column and all surfaces to future-conditional language; nothing is ever "blocked" in v1.
- **Autonomous-path disclaimer (regulatory MAJOR + missing):** the "Pivot has NOT applied" disclaimer is now wired onto the notifications themselves, not only the card.
- **Watcher rebuilt (regulatory + code-fit + UX BLOCKERS):** dropped the false "mirrors `_poll_watch_triggers`" claim; specified a dedicated `_poll_ipo_open_triggers` poll path, a fire-once latch via a `_persist_ipo_fired` helper (since `_persist_last_value` can't hold a string status), exactly-once edge semantics, and feed-unreachable handling.
- **Reminders re-architected (code-fit + UX BLOCKERS):** `notify.push` doesn't exist → use `notify.message`; the `at:` deferred-send doesn't exist → close-day and allotment reminders are separate `trigger.schedule` workflows; dropped the broken `trigger.event(event_type="ipo_allotment")` reuse (it's a news classifier with no date-fire).
- **Calendar endpoint fixed (code-fit BLOCKER):** `/api/ipo-calendar` was wrong (events router is `prefix="/api/events"`, no IPO fields) → mounted as its own bare `/ipo-calendar` route with a new `IpoCalendarItem` model.
- **`parse_price_band` added (code-fit + UX BLOCKERS):** the feed's `price_band` is a **string**; added a load-bearing parser → `{min,max,is_fixed}|null`, with null-handling (hide math, disable CTAs) and a fixed-price acceptance test.
- **Migration path fixed (code-fit MAJOR):** `pivot/backend/alembic/versions/00XX` → `pivot/migrations/versions/0014_ipo_applications.py`, chained off `0013_paper_trading`.
- **api.ts mount resolved (code-fit MAJOR):** router mounts **bare** like `/orders`; all FE fetchers use `requestLegacy`, with consistent non-`/api` endpoint paths.
- **Render-hint locked (code-fit MAJOR):** single constant `ipo_application_card` used identically in tool return, payload, and the exact-match `ChatDemo` dispatch (the `_draft` vs `_card` ambiguity is resolved).
- **Tool-router surfacing (code-fit minor):** added `propose_ipo_application` to the IPO regex target list (the regex matched but pointed only at read tools).
- **Shared persistence helper (code-fit minor):** `persist_ipo_application()` factored out so the executor calls the function, not the endpoint.
- **Lifecycle/state machine + withdraw (UX MAJORs + missing):** added the explicit status vocabulary, card state machine, a reachable `withdraw` endpoint/CTA, mandate-guidance state, "last updated"+refresh, and the closed-issue read-only variant.
- **Cross-field validation matrix (UX MAJOR):** SME bypasses the ₹2L cap; retail cap evaluated at band.max; UPI ₹5L cap as a hard block across the sNII range; category-change force-clears invalid cut-off/price.
- **Resilience (UX MAJOR):** feed-unreachable/stale-card/closed-IPO handling at register time and in the watcher; empty-vs-unreachable copy differentiation.
- **Honesty minors applied:** KYC block omitted (no fake PAN) when no profile; UPI stored masked-only with a retention/consent line; UPI success copy de-confidenced; oversubscription note wired to live data; one-application-per-PAN soft-warn; trading-day (not calendar) arithmetic for allotment/expiry; CI no-broker-call boundary test; notify-channel honesty caveat. **Deferred minor:** multiple-bids-per-application (named, scoped to a later phase).
