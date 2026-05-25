# Entry+Exit chat-and-backtest eval — dsl_entry_exit_run_1

- recorded_at: 2026-05-25T12:54:54.235236+00:00 → 2026-05-25T12:59:15.289302+00:00
- prompts: 25
- backend: http://127.0.0.1:8000

## Triad summary

**Quality** — verdict distribution (chat AND backtest):
  - PASS: 0 / 25 (0%)
  - PARTIAL: 13 / 25 (52%)
  - FAIL: 12 / 25 (48%)

**Backtest acceptance** — 0/25 drafts eligible, 0 rejected, 17 errored.

**Latency (ms)** — chat / backtest:
  - chat mean: 10433, p50: 12214, p95: 14827
  - backtest mean: 13, p50: 14, p95: 20

**Tokens** — input 754,748 / output 7,432 / total 762,180 (83 calls). cost $0.1377

## Per-prompt detail

| id | verdict | tool | steps | bt eligible | trades | ret % | bench % | reason |
|---|---|---|---|---|---|---|---|---|
| ee_reliance_dual_gap_rsi | FAIL | propose_workflow | 2 | — | — | — | — | want_tool=propose_dsl_workflow got=['propose_workflow'] |
| ee_tcs_rsi_macd_dd_exit | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_infy_golden_cross_trail | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_hdfcbank_bollinger_meanrev | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_sbin_macd_pair | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_icicibank_pullback_break | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_wipro_volume_breakout | FAIL | propose_workflow | 2 | — | — | — | — | want_tool=propose_dsl_workflow got=['propose_workflow'] |
| ee_kotakbank_pair_zscore | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_lt_compound_entry_pct_exit | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_axisbank_ema_crossover_pair | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_maruti_bb_pct_exit | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_itc_percentrank_exit | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_eternal_gap_recovery | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_niftybees_session_filtered | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_bajajhfl_breakout_trail | FAIL | propose_workflow | 2 | — | — | — | — | want_tool=propose_dsl_workflow got=['propose_workflow'] |
| ee_reliance_pct_change_pair | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_hdfclife_atr_breakout | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_hyundai_double_entry_double_exit | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_wipro_adx_trend | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_tcs_supertrend_session | FAIL | propose_workflow | 2 | — | — | — | — | want_tool=propose_dsl_workflow got=['propose_workflow'] |
| ee_eternal_macd_line_signal | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_bajajhfl_stoch_oversold | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_sbin_three_conditions_three_exits | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_hdfcbank_volatility_regime | PARTIAL | propose_dsl_workflow | 5 | — | — | — | — | chat OK; bt_error: HTTP 404 |
| ee_niftybees_252_breakout_trail | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
