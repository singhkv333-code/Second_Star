# Entry+Exit chat-and-backtest eval — dsl_entry_exit_run_2

- recorded_at: 2026-05-25T13:00:30.891159+00:00 → 2026-05-25T13:05:05.641791+00:00
- prompts: 25
- backend: http://127.0.0.1:8000

## Triad summary

**Quality** — verdict distribution (chat AND backtest):
  - PASS: 4 / 25 (16%)
  - PARTIAL: 12 / 25 (48%)
  - FAIL: 9 / 25 (36%)

**Backtest acceptance** — 4/25 drafts eligible, 12 rejected, 0 errored.

**Latency (ms)** — chat / backtest:
  - chat mean: 10378, p50: 11587, p95: 15507
  - backtest mean: 956, p50: 906, p95: 1857

**Tokens** — input 736,594 / output 7,536 / total 744,130 (84 calls). cost $0.1126

## Per-prompt detail

| id | verdict | tool | steps | bt eligible | trades | ret % | bench % | reason |
|---|---|---|---|---|---|---|---|---|
| ee_reliance_dual_gap_rsi | PASS | propose_workflow | 2 | ✓ | 20 | 0.61 | -6.08 | chat OK + bt_ok: trades=20 ret=0.61% |
| ee_tcs_rsi_macd_dd_exit | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_infy_golden_cross_trail | PARTIAL | propose_dsl_workflow | 5 | ✗ | — | — | — | chat OK; bt_rejected: insufficient data for NSE over 2y (got 0 bars) |
| ee_hdfcbank_bollinger_meanrev | PARTIAL | propose_dsl_workflow | 5 | ✗ | — | — | — | chat OK; bt_rejected: insufficient data for NSE over 2y (got 0 bars) |
| ee_sbin_macd_pair | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_icicibank_pullback_break | PARTIAL | propose_dsl_workflow | 5 | ✗ | — | — | — | chat OK; bt_rejected: insufficient data for NSE over 2y (got 0 bars) |
| ee_wipro_volume_breakout | PASS | propose_workflow | 2 | ✓ | 158 | -18.29 | -2.46 | chat OK + bt_ok: trades=158 ret=-18.29% |
| ee_kotakbank_pair_zscore | PARTIAL | propose_dsl_workflow | 5 | ✗ | — | — | — | chat OK; bt_rejected: insufficient data for NSE over 2y (got 0 bars) |
| ee_lt_compound_entry_pct_exit | PARTIAL | propose_dsl_workflow | 5 | ✗ | — | — | — | chat OK; bt_rejected: insufficient data for NSE over 2y (got 0 bars) |
| ee_axisbank_ema_crossover_pair | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_maruti_bb_pct_exit | PARTIAL | propose_dsl_workflow | 5 | ✗ | — | — | — | chat OK; bt_rejected: insufficient data for NIFTY over 2y (got 0 bars) |
| ee_itc_percentrank_exit | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_eternal_gap_recovery | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_niftybees_session_filtered | PARTIAL | propose_dsl_workflow | 5 | ✗ | — | — | — | chat OK; bt_rejected: insufficient data for NSE over 2y (got 0 bars) |
| ee_bajajhfl_breakout_trail | PASS | propose_workflow | 2 | ✓ | 406 | -17.12 | -49.52 | chat OK + bt_ok: trades=406 ret=-17.12% |
| ee_reliance_pct_change_pair | PARTIAL | propose_dsl_workflow | 5 | ✗ | — | — | — | chat OK; bt_rejected: insufficient data for NSE over 2y (got 0 bars) |
| ee_hdfclife_atr_breakout | PARTIAL | propose_dsl_workflow | 5 | ✗ | — | — | — | chat OK; bt_rejected: insufficient data for NSE over 2y (got 0 bars) |
| ee_hyundai_double_entry_double_exit | PARTIAL | propose_dsl_workflow | 5 | ✗ | — | — | — | chat OK; bt_rejected: insufficient data for NSE over 2y (got 0 bars) |
| ee_wipro_adx_trend | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_tcs_supertrend_session | PASS | propose_workflow | 2 | ✓ | 51 | -40.69 | -35.62 | chat OK + bt_ok: trades=51 ret=-40.69% |
| ee_eternal_macd_line_signal | PARTIAL | propose_dsl_workflow | 5 | ✗ | — | — | — | chat OK; bt_rejected: insufficient data for NSE over 2y (got 0 bars) |
| ee_bajajhfl_stoch_oversold | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_sbin_three_conditions_three_exits | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_hdfcbank_volatility_regime | PARTIAL | propose_dsl_workflow | 5 | ✗ | — | — | — | chat OK; bt_rejected: insufficient data for NIFTY over 2y (got 0 bars) |
| ee_niftybees_252_breakout_trail | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
