# Handoff — comparisons, one table design, deeper replies, technical scan (2026-09-29)

Branch: `claude-cloud-29sep` (pushed). Two commits on top of `60d282a`:

| Commit | Scope |
|---|---|
| `ddcaa2b` | chat: comparisons on the metrics the question needs, one table design, deeper replies |
| `1a77d68` | scan: opening-range breakouts and 13 more technical features, patterns in words |

Nothing is deployed. `pivot-next` and `pivot` ride the usual path; the charto
change (`charto/data/dataserver.py`) only goes live through `charto-deploy`.

---

## 1. What was asked, and the root cause found for each

| # | Ask | Root cause | Fix (no prompt rules added for the model) |
|---|---|---|---|
| 1 | "compare tcs with other it peers" returned only 1Y return / Sharpe / max drawdown | Three deterministic steers all routed "compare" to `compare_performance` (a price risk/return tool): its own description ("Use for ANY multi-stock comparison"), `system_core.md` (legacy engine), and `_read_intent_gate` in `chat_service.py` forcing it with `tool_choice=required` (legacy). Live A/B confirmed: old code's first call was `compare_performance`; new code's is fundamentals. | Descriptions now say what each tool measures; nothing prescribes a tool. |
| 2 | Better metrics, model's choice | No tool could put a named peer set side by side on arbitrary fundamentals. | `screen_fundamentals` gained `symbols` (scope to named companies) + `metrics` (columns shown without filtering): any screenable field plus 1Y `volatility`/`sharpe`/`max_drawdown`. |
| 3 | One table design (the screener card) | Two renderers: `SmartMarkdownTable` (bordered, glass header) and `ScreenResultsCard`. | Shared primitives exported from `ScreenResultsCard.tsx` (`TABLE_CLS`, `CompanyCell`, `WrapText`); markdown tables render through them (rank #, logo + name + ticker, right-aligned numbers, "Median of N" row). `compare_performance` rows also render as the screen card. Glass-header CSS deleted. |
| 4 | Long text cells stretch the table | Cells were `nowrap`, table-layout auto. | Sentence columns (>28 chars) wrap inside `min-w-[160px] max-w-[280px]`. |
| 5/6 | Longer, structured, ChatGPT-style answers; crisp context, no templates | Flex brief's "Writing the answer" section carried example headings and a checklist. | Replaced with a 96-word section (was 235): depth by default, `###` headings, bullets, tables, short asks stay short. No templates/examples. Cards line now says "build your analysis around it". |
| 7 | "Why web search for financials?" | In the test harness every Pivot tool was stubbed as unreachable, so the model fell back to web (correct). One real miss: it went web-first for *quarterly results*, which the brief didn't name as Pivot-first. | Brief: "Pivot's tools come first for prices, fundamentals, quarterly results, filings and scans." |
| 8 | Scan card showed ", 0d ago" in Pattern | `_scan_technicals` read `p["kind"]`; charto sends `p["pattern"]`. The test fixture had the same wrong key. | Reads `pattern`; cell reads "Bull flag, latest session" / "N sessions ago". Fixture corrected. |
| 9 | Reply leaked `bull_flag`, `ascending_triangle` | The model was handed snake_case ids in the tool description, charto's unknown-pattern list and its notes, and echoed them. | Pattern names go out and come back as plain words (charto normalises "Bull Flag"/"bull-flag" → id). |
| 10 | "ORB not supported"; need more technical screeners | Every screen feature was daily-bar-only. | 15 new charto screen features (below). |

## 2. Files touched

**pivot backend**
- `backend/services/fundamentals_screen.py` — `screen_by_fundamentals(symbols=, metrics=)`. Scoped mode LEFT-joins every metric CTE and turns plausibility bounds into `CASE … END` (implausible → null, row kept); normal screens unchanged (bounds stay in `WHERE`). `metric_fields` now ordered (model's metric order drives columns).
- `backend/agents/tool_executor.py` — `_RISK_COLS`, `_risk_values`, `_risk_for` (reuse `compare_assets` + `get_close_dict`); `_screen_fundamentals` passes `symbols`/`metrics`, merges risk columns (first 30 rows), notes symbols with no fundamentals; `_compare_performance` emits `screen_results_card` (keeps `comparison` for orchestrator plan steps); scan labels/units for new features; pattern cell fix; `minute_bar_coverage` passthrough.
- `backend/agents/tools.py` — schemas: `screen_fundamentals.symbols/metrics`, neutral `compare_performance` description, `_SCAN_FEATURES` entries for the new features, pattern param in words.
- `backend/services/flex_chat.py` — brief (writing section, cards line, quarterly results).
- `backend/services/chat_service.py`, `backend/prompts/system_core.md` — legacy engine no longer forces `compare_performance`.

**charto**
- `data/dataserver.py`
  - removed the dead first copy of `SCREEN_FEATURES`/`_VP_FEATURES`/`_VOLUME_FEATURES`/`SCREEN_FEATURE_HELP`/`SCREEN_OPS` (the second copy always won at import).
  - new features in `_screen_row_features` (daily): `gap_pct`, `ret_open`, `close_pos`, `hi20_break_pct`, `lo20_break_pct`, `vol_ratio20`, `streak`, `adx14`, `macd_hist_pct`, `bb_pct_b`, `bb_width_pct`, `stoch_k`, `supertrend_dir` — via `indicators.compute` (chart's own code). Windows: Bollinger/Stoch last 40 bars (exact), ADX/Supertrend/MACD last 300.
  - `_orb_rows`: `orb15_pos`, `orb30_pos` = last close vs first 15/30 min high-low range of the last `bars_1d` session, % of width (>100 = upside ORB). Reads `bars` (1-minute, PK symbol+ts) with `LIMIT 30`; skips USD-quoted (24h) markets; needs ≥2/3 of the minutes.
  - coverage key renamed `volume_profile_coverage` → `minute_bar_coverage` (covers VP + ORB features; nothing else read the old key).
  - pattern names normalised with `_squash(pattern, "_")`; error list / `res["pattern"]` / notes in words.
- `data/test_screen_features.py` — new, 20 checks on a synthetic store.

**pivot-next**
- `components/chat/ScreenResultsCard.tsx` — exports `TABLE_CLS`, `WrapText`, `CompanyCell`; text columns wrap.
- `components/chat/SmartMarkdownTable.tsx` — render rewritten on those primitives; sticky/glass removed; `formatLike` formats the median in the column's own style.
- `components/chat/AssistantMessage.tsx` (comment), `app/globals.css` (glass header CSS removed).
- `tests/chat/smart-markdown-table.test.tsx` — new.

## 3. Verification done

- pivot: `test_flex_chat`, `test_compare`, `test_fundamentals_screen_*`, `test_screen_results_card`, `test_screener_filters`, `test_scan_technicals`, new `test_fundamentals_screen_compare` — all pass (one assertion in `test_flex_chat` updated: pins "go deep" instead of the old phrase).
- charto: `test_screen_features.py` 20/20, `test_patterns.py` 0 failures, `test_indicators.py` 8 passed, `test_chart_patterns_v2.py` exit 0.
- pivot-next: `tsc --noEmit` clean, eslint clean on changed files, table tests pass. **17 other chat tests fail identically on the untouched base** (chat-demo, chat-editor-sync, indicator-backtest-card, one assistant-message link test) — pre-existing, not from this work.
- Visual: both tables screenshotted at 1100px and 390px via a throwaway `/design/tabletest` page (removed).
- Live model (tools stubbed, see §4): comparison prompt → first calls `fetch_fundamentals` per peer (old code: `compare_performance`); "give me it stocks with orb" → `scan_technicals(industry="IT", filters=[orb15_pos gt 100])`, no snake_case in the reply.
- Perf: screen matrix build for 550 symbols 0.38s → 0.94s (cached per daily import).

## 4. How to reproduce in a cloud session

- Postgres (`pivot-db-india…:5432`) is **blocked by the cloud environment's network policy**; only HTTPS egress works. LLM (Azure) and yfinance are reachable. To run with real data, allow that host under the environment's Network access settings.
- Backend venv: system pip fails building `ta`/`pyaes`; use a venv:
  `python -m venv .venv && .venv/bin/pip install -U pip setuptools wheel && .venv/bin/pip install -r pivot/requirements.txt`
- Tests need `REDIS_URL=redis://127.0.0.1:6399/0 JWT_SECRET_KEY=local-test`.
- Live probe: `pivot/scripts/flex_turn_probe.py` (stubs tools by default; `--real-tools` when the DB is reachable). Set `DATABASE_URL=postgresql://x:x@127.0.0.1:1/x` (and `FINANCIALS_DSN`, `ENRICH_DSN`) when the DB is unreachable, or the LLM-cost logger hangs on connect.
- Frontend: `cd pivot-next && pnpm install --frozen-lockfile && pnpm -s vitest run tests/chat`.
- charto tests: `cd charto/data && python3 test_screen_features.py` (uses a scratch `CHARTO_DB`).

## 5. Open items for the next session

1. **Run with real data** once the DB is reachable: `flex_turn_probe.py --real-tools` on "compare tcs with other it peers", "give me it stocks with orb", and a no-tool explainer; report tokens + latency + quality per item (CLAUDE.md triad).
2. **Tool choice for comparisons is the model's**: in one run it used `fetch_fundamentals` per peer (text table, no card) rather than `screen_fundamentals(symbols=…)` (card). Both are valid; if the card should win more often, improve the `screen_fundamentals` description's discoverability, don't add a rule.
3. **Only one card renders per turn** (`raw_data` hoists the first `_render_hint`). A turn calling both `compare_performance` and `screen_fundamentals` shows one; `screen_fundamentals(metrics=[…, "sharpe"])` puts both in one card.
4. **ORB coverage** equals minute-bar coverage (same subset as volume profile). The result's `minute_bar_coverage` says how many were scored; widen by hydrating more symbols' 1-minute bars.
5. **Industry "IT" in charto's scan** matches by prefix for short words (pre-existing); the tool returns the closest industry names and the model re-calls. Watch whether IT scans land on the right slug in real runs.
6. `pivot-next` `/design/tabletest` was temporary and is gone; `app/design` is the ungated route if a visual harness is needed again.
