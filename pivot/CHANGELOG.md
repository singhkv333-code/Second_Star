# Changelog

All notable changes to the Pivot chatbot.

## [Unreleased] — 2026-05-01 — Hard Reset

Driven by a 200-case eval that exposed an 18% real pass rate. Five
infrastructure issues were strangling the LLM's output. They are gone.

### Added
- `backend/services/chat_service.py` — single canonical request handler.
  Loads conversation history from Redis, makes one or two LLM calls
  (second hop after a tool result), post-processes to strip leaks, persists
  the turn.
- `backend/services/conversation_store.py` — Redis-backed conversation
  history. 24h TTL, last 20 turns. **Stores plain text only**, never
  tool-call payloads — those caused the `<TOOL_CALL>` leakage.
- `backend/services/tool_registry.py` — single source of truth for the
  tool surface the LLM sees. Stub tools are excluded; v2 tools are
  registered in this file.
- `backend/services/_v2_tools.py` — real implementations for
  `get_price_history`, `get_52wk_range`, `get_product_spec`.
- `backend/prompts/system.md` — versioned system prompt v2.0.
- `backend/prompts/__init__.py` — prompt loader with `lru_cache`.
- `backend/config/products.yaml` — single source of truth for SafeGrow /
  EarnMore / StormShield economics. The `92.764%` ratio no longer lives
  in the system prompt.
- `pivot-eval/src/pivot_eval/judge.py` — added the mandatory
  `response_addresses_user_input` 1-3 check, an auto-fail for the canned
  pitch anywhere in a response, and an auto-fail for unsolicited Pivot
  product mentions on CASUAL inputs.

### Changed
- `backend/routers/chat.py` rewritten end-to-end. ~600 lines → ~280
  lines, all routing logic delegated to `ChatService`. `/chat/stream`
  preserved.

### Removed
- The hardcoded canned 4-line greeting from the system prompt
  (was `chat.py:88-89`).
- `<LTP>`, `<STRIKE>`, `<PREMIUM>`, `<LTP_PREMIUM>` placeholder
  instructions from the system prompt (were `chat.py:47,55,58,72`).
- The `92.764%` SafeGrow ratio from the system prompt
  (was `chat.py:52`); moved to `config/products.yaml`.
- The intent classifier as a routing decision (the regex-based
  `_heuristic_intent` and the Sarvam JSON classifier are no longer in
  the chat path; the LLM sees the full tool schema and decides).
- Tool-subset routing in `agents/tools.py::TOOL_SUBSETS` is no longer
  consulted from the chat router.
- The chart short-circuit (regex-based interception of "show me X")
  was deleted; replaced by the `get_price_history` tool the LLM picks
  on its own merits.
- The `parse_strategy` interception path was deleted; `run_backtest`
  is a regular tool now.
- Stub tools removed from the LLM-visible schema:
  `get_option_chain`, `get_option_greeks`, `get_margin_required`,
  `get_upcoming_events`, `modify_order`, `place_futures_order`,
  `place_options_order`, `place_multileg_options`,
  `roll_futures_position`, `create_cash_sweep`,
  `create_rebalancing_rule`, `create_drawdown_protection`. Their
  handlers still exist in `tool_executor.py` for backward compatibility
  but are unreachable from the chat. They will be re-added when their
  implementations are real.

### Safety net
- Post-processor strips any leaked `<UPPERCASE>` placeholders and any
  leaked `<TOOL_CALL>` blocks (closed or unclosed). Logs a warning when
  it has to fire — that's our regression alarm for the upstream fix.
- If the LLM somehow produces the legacy 4-line pitch the post-processor
  replaces it with a graceful generic fallback.
