# Pivot Chatbot — Architecture Audit (Step 0)

Read-only forensic map produced from the live codebase before any fix lands.
Every claim cites a file:line.

---

## 1. The canned 4-line "marketing pitch" — origin

**It is not a hardcoded fallback in code.** It is *instructed* by the system prompt.

- **`backend/routers/chat.py:88-89`** (inside `PIVOT_SYSTEM_PROMPT`):
  ```
  WHEN ASKED WHAT YOU CAN DO, OR WHEN THE USER SENDS A SHORT GREETING (yo, hi,
  hey, sup, hello, yo there, hii, heyy — any greeting under ~10 characters) —
  reply with EXACTLY these 4 lines, plain prose, verbatim, no preamble, no
  "How can I assist?", nothing else:
  Execute orders on Zerodha. Build capital protection and income products.
  Automate SIP and strategy rules. Analyse your portfolio.
  ```

- The model is following the rule literally. "thanks" (6 chars), "lol", "what is pivot",
  "what can you do", "help" all qualify under "any greeting under ~10 characters" or
  "ASKED WHAT YOU CAN DO".
- Repo-wide grep for the verbatim string returns **only one hit**: that line.
  There is no second code path printing this; the LLM is the sole emitter.

**Implication:** the fix is *prompt-only*. No code path needs to be deleted —
the instruction needs to be deleted.

A *separate* hardcoded instance lives in **`backend/agents/sarvam_client.py:58`**
inside `MOCK_RESPONSES` (`"default": "I understand your query…"`) — this only
fires when `SARVAM_API_KEY` is unset. With our key set it never executes.

---

## 2. Intent classifier — current shape

- **File:** `backend/agents/classifier.py`
- **Function:** `classify_intent(user_message, history)`
- **How it routes:** the chat handler at `backend/routers/chat.py:220` calls
  `classify_intent`, takes the returned `intent`, then `tools = get_tools_for_subset(intent)`
  (`tools.py:534-537`) — meaning the LLM only sees the subset of tools the
  classifier picked. If the classifier picks the wrong subset, the right tool
  is not even in the prompt.
- **Two stages:**
  1. **Sarvam JSON call** with the system prompt at `classifier.py:18-50` listing 16 intents.
  2. **Heuristic fallback** at `classifier.py:53-80` — a list of 15 pre-compiled regex rules.
- **Failure modes confirmed by the eval:**
  - `"explain RSI"` → matches no rule → falls through to `"GENERAL"` *or* Sarvam picks `BACKTEST` because `RSI` appears in `BACKTEST` examples; either way the tool subset is wrong.
  - `"what is RSI"` → same.
  - `"is the market open"` → regex `r"\b(... |market open|market closed)\b"` at line 60 → `MARKET_QUERY` (correct family but no `get_market_status` tool in that subset).
  - `"tax saving stocks"` → `\btax\b` regex at line 59 → `PORTFOLIO_QUERY` (pulls user's STCG/LTCG, irrelevant to query).
- **Subset map:** `backend/agents/tools.py:12-29`. `BACKTEST` exposes only `run_backtest`. `GENERAL` exposes **zero tools**.

---

## 3. Tools — registered vs implemented vs stubs

**Schema source:** `backend/agents/tools.py:31-532`. Roughly 50 tool definitions.

**Dispatcher:** `backend/agents/tool_executor.py:18-76` maps tool name → handler.

| Tool | Handler | Status |
|---|---|---|
| `place_market_order` | `_place_market_order` | real (Kite) |
| `place_limit_order` | `_place_limit_order` | real |
| `create_gtt_order` | `_create_gtt_order` | real |
| `create_sl_order` / `create_oco_order` / `create_dip_buy` | dedicated | real |
| `place_basket_order` | `_place_basket_order` | real |
| `cancel_order` / `cancel_gtt` / `list_pending_orders` / `list_gtt_orders` | dedicated | real |
| `modify_order` | **`_generic_confirm`** | **STUB** (returns `{"message":"Created"}`) |
| `squareoff_*` | dedicated | real |
| `place_futures_order` / `place_options_order` / `place_multileg_options` | **`_generic_confirm`** | **STUB** |
| `roll_futures_position` | **`_generic_confirm`** | **STUB** |
| `get_option_chain` | **`_generic_confirm`** | **STUB** (returns `"Created"`) |
| `get_option_greeks` | **`_generic_confirm`** | **STUB** |
| `get_margin_required` | **`_generic_confirm`** | **STUB** |
| `create_sip` / `list_sips` / `pause_sip` / `resume_sip` / `delete_sip` / `pause_all_sips` | dedicated | real (DB) |
| `create_strategy` / `list_strategies` / `pause_strategy` / `resume_strategy` / `delete_strategy` | dedicated | real (DB) |
| `create_cash_sweep` / `create_rebalancing_rule` / `create_drawdown_protection` | **`_generic_confirm`** | **STUB** |
| `get_portfolio_summary` / `get_holdings` / `get_sector_breakdown` / `get_holding_detail` / `get_tax_summary` / `get_active_products` | dedicated | real |
| `get_live_price` | `_get_live_price` (`tool_executor.py:448`) | real (Kite cache) |
| `get_index_level` | dedicated | real |
| `get_ohlc` | `_get_ohlc` | real (Kite historical) |
| `get_52wk_range` | **`_generic_confirm`** | **STUB** |
| `get_market_status` | dedicated | real |
| `get_upcoming_events` | `_get_upcoming_events` | **STUB** — returns `{"message":"Connect TrueData for live event calendar"}` (`tool_executor.py:484-487`) |
| `compare_yields` / `get_yield_recommendation` | real | real |
| `calculate_*` | real | real |
| `run_backtest` | `_run_backtest` | real (legacy backtester) |
| `get_scheduler_status` / `list_upcoming_jobs` | real | real |

**`run_compare`** (chart short-circuit) — note: this is **not** a registered LLM tool. It's a regex shortcut in the chat router (`chat.py:181-203`) that intercepts requests like "show me reliance" and calls `backend/routers/compare.py::run_compare`. It returns a chart payload to the frontend; the chat reply is the static line `"Here is {period} of {syms}. Past performance does not guarantee future results."` (`chat.py:188-191`). That line is what the eval saw 15+ times — it's not a stub return, it's an *intentional* template that says "go look at the chart". The bug is that the bot uses it for revenue/quarterly/perf questions where the user expected numeric data.

---

## 4. Conversation history

**Storage model: server-stateless. The client carries history in the request body.**

- `chat.py:101-103` — `ChatRequest{ messages: list, include_portfolio_context: bool }`. The full history is in `messages`.
- `chat.py:240-249` — every chat turn passes `messages=user_messages` straight to Sarvam.
- The frontend (`frontend/src/components/chat/ChatPane.jsx:42-44`) sends the last 12 messages on every turn. So **history *is* reaching Sarvam**.
- **Redis** (`backend/cache.py:55`) is initialised but **not used for chat history** — only for price/yield/sector caching.

**So the multi-turn breakage is *not* "history isn't reaching the model".** Two real causes:

1. **System prompt has no instruction to use prior turns.** `PIVOT_SYSTEM_PROMPT` (`chat.py:23-98`) never tells the model "resolve 'and X' / 'what about X' against prior context". Sarvam at temp=0.2 with no instruction tends to treat each turn standalone.
2. **`<TOOL_CALL>` leaks** (see §6) make follow-up turns echo a previous tool-call payload as visible text, which then re-enters the user-visible transcript.

---

## 5. The LLM call site

**Production model:** Sarvam's `sarvam-m` via `https://api.sarvam.ai/v1/chat/completions`.

- Library: `backend/agents/sarvam_client.py:126-252` (`call_sarvam`).
- Defaults: `temperature=0.2` for chat, `max_tokens=900` (with tools) / `500` (without).
- **No native tool-calling.** Sarvam returns 400 when OpenAI-shaped `tools`/`tool_choice` are sent (`sarvam_client.py:140-144,182-184`). The code emulates by *injecting tool descriptions into the system prompt* (`_build_tool_instruction`, line 75-103) and *parsing a `<TOOL_CALL>{...}</TOOL_CALL>` block out of the prose response* (line 72, 106-123).

**This is the proximate cause of every leak issue:**

- The model is *prompted* to emit `<TOOL_CALL>{...}</TOOL_CALL>` literally.
- A regex (`_TOOL_CALL_RE`, line 72) tries to extract it.
- If the regex fails to match (model truncated, JSON malformed, model wrapped in markdown fences) the raw text passes through to the user.
- The regex has `re.DOTALL` and is non-greedy on `\{.*?\}`, but `<think>` interplay and truncation (line 220-229 explicitly handles it) sometimes produce a half-matched payload.

---

## 6. Sources of `<LTP>`, `<STRIKE>`, `<TOOL_CALL>` template strings

| Marker | Source | Path |
|---|---|---|
| `<LTP>` | system prompt instruction at `chat.py:47` ("write the literal token `<LTP>` ALONE…") and `chat.py:72` worked example | system prompt |
| `<LTP_PREMIUM>` | system prompt `chat.py:55` (EarnMore spec) | system prompt |
| `<STRIKE>` / `<STRIKE_LONG>` / `<STRIKE_SHORT>` | system prompt `chat.py:55,58` | system prompt |
| `<PREMIUM>` | system prompt `chat.py:55,58` | system prompt |
| `<TOOL_CALL>` | system prompt injected at `sarvam_client.py:99` plus extraction regex `sarvam_client.py:72` | LLM-emulated tool calling |
| `<LOGICCARD>` | system prompt `chat.py:60-69` requires inline JSON with this tag | parsed at `frontend/src/components/chat/ChatPane.jsx:5-16` |

**Pattern is identical for all of them: the model is *instructed to emit a placeholder*, then a parser (or human-eye in the LTP case) is expected to substitute the real value. It works when there's a parser (`<LOGICCARD>`, `<TOOL_CALL>`). It catastrophically fails when there isn't (`<LTP>`, `<STRIKE>`) — the placeholder reaches the user.**

---

## 7. The `92.764%` SafeGrow ratio

Hardcoded in **two** places:

- `chat.py:52` (system prompt) — full prose specification with three repetitions of "0.92764".
- `sarvam_client.py:58` (`MOCK_RESPONSES["safegrow"]`) — only fires in mock mode.

There is **no products config file** anywhere. The economics live in the prompt
that is sent on every chat turn. Step 6's mandate ("don't hardcode product
economics in the system prompt") is correct.

---

## 8. Frontend coupling

- The frontend expects `response`, `intent`, `chart_data`, `backtest_data`, `screen_data`, `expr_backtest_data`, `tool_call`, `logiccard`, `requires_clarification`, `missing_params` keys (`ChatPane.jsx:46-62`). When we delete the `intent` field server-side, the frontend won't break — it ignores nulls — but several inline UI pieces key off `intent === 'BACKTEST'` etc. (e.g. `MessageBubble.jsx:298`). Worth a sweep.
- `LOGICCARD` parsing happens client-side (`ChatPane.jsx:5-16`). Server returns the `logiccard` dict separately *and* embeds it in the response text per the system prompt's mandate. We can keep the dict-only path and drop the embedded copy.

---

## 9. Summary of root causes (mapped to the user's plan)

| Symptom in eval | Root cause (this audit) | Step that fixes it |
|---|---|---|
| Canned 4-line greeting on 30+ inputs | System prompt explicitly mandates it (`chat.py:88-89`) | **Step 6** (rewrite prompt) |
| `<LTP>` leaking | System prompt instructs the model to emit `<LTP>` literally (`chat.py:47,72`) | **Step 6** (no placeholders) + **Step 7** (post-process) |
| `<TOOL_CALL>` leaking | Sarvam doesn't have native tool calling; we emulate via text parsing (`sarvam_client.py:72-123`) | **Step 7** (post-process strip) — full fix is an LLM swap, out of scope |
| Wrong intent → wrong tools | Classifier subset routing kills tool availability (`tools.py:12-29`, `chat.py:237`) | **Step 3** (kill classifier, expose all tools) |
| `run_compare` template line on revenue/quarterly questions | Chart shortcut intercepts before Sarvam (`chat.py:181-203`); not a tool the model can decline | **Step 3** (move to a real tool the model selects when appropriate) |
| `get_52wk_range` / `get_option_chain` returning `"Created"` | `_generic_confirm` stub at `tool_executor.py:338-339` | **Step 4** (implement or remove from schema) |
| `get_upcoming_events` returning TrueData placeholder | `tool_executor.py:484-487` | **Step 4** (remove from schema until real) |
| Multi-turn breakage | System prompt has no "use history" rule + `<TOOL_CALL>` leaking into prior assistant messages | **Step 5** (system prompt + parser hardening) |
| Hardcoded 92.764% | `chat.py:52` + `sarvam_client.py:58` | **Step 6** (move to `config/products.yaml`, look up via tool) |

---

## 10. Files I will edit (preview, no edits yet)

```
backend/routers/chat.py                 — gut & rewrite around new ChatService
backend/services/chat_service.py        — NEW (single LLM-decides-tools loop)
backend/services/conversation_store.py  — NEW (Redis-backed, optional Postgres mirror)
backend/services/tool_registry.py       — NEW (consolidates schema + dispatcher)
backend/prompts/system.md               — NEW (versioned system prompt)
backend/prompts/__init__.py             — NEW (loader)
backend/agents/classifier.py            — DELETE (and remove all imports)
backend/agents/tools.py                 — slimmed; keep schemas, drop subsetting
backend/agents/tool_executor.py         — fold into tool_registry; remove _generic_confirm
backend/agents/sarvam_client.py         — keep emulated tool-calling, harden parser
backend/agents/context_injector.py      — keep, but only used by tools that need it
backend/config/products.yaml            — NEW (SafeGrow/EarnMore/StormShield specs)
tests/                                  — new unit tests per Speedrun gate
CHANGELOG.md                            — NEW (one entry per step)
```

No file is touched until Step 1 begins.
