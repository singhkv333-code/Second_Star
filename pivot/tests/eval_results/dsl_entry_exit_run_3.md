# Entry+Exit chat-and-backtest eval — dsl_entry_exit_run_3

- recorded_at: 2026-05-25T13:08:36.223709+00:00 → 2026-05-25T13:15:07.044863+00:00
- prompts: 25
- backend: http://127.0.0.1:8000

## Triad summary

**Quality** — verdict distribution (chat AND backtest):
  - PASS: 21 / 25 (84%)
  - PARTIAL: 0 / 25 (0%)
  - FAIL: 4 / 25 (16%)

**Backtest acceptance** — 21/25 drafts eligible, 0 rejected, 0 errored.

**Latency (ms)** — chat / backtest:
  - chat mean: 10579, p50: 12608, p95: 14408
  - backtest mean: 6016, p50: 804, p95: 29851

**Tokens** — input 841,995 / output 7,491 / total 849,486 (84 calls). cost $0.1314

## Per-prompt detail

| id | verdict | tool | steps | bt eligible | trades | ret % | bench % | reason |
|---|---|---|---|---|---|---|---|---|
| ee_reliance_dual_gap_rsi | PASS | propose_workflow | 2 | ✓ | 20 | 0.61 | -6.08 | chat OK + bt_ok: trades=20 ret=0.61% |
| ee_tcs_rsi_macd_dd_exit | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_infy_golden_cross_trail | PASS | propose_dsl_workflow | 5 | ✓ | 2 | 0.07 | -15.65 | chat OK + bt_ok: trades=2 ret=0.07% |
| ee_hdfcbank_bollinger_meanrev | PASS | propose_dsl_workflow | 5 | ✓ | 15 | -1.22 | 4.4 | chat OK + bt_ok: trades=15 ret=-1.22% |
| ee_sbin_macd_pair | PASS | propose_dsl_workflow | 5 | ✓ | 23 | 1.44 | 20.77 | chat OK + bt_ok: trades=23 ret=1.44% |
| ee_icicibank_pullback_break | PASS | propose_dsl_workflow | 5 | ✓ | 142 | -0.18 | 16.21 | chat OK + bt_ok: trades=142 ret=-0.18% |
| ee_wipro_volume_breakout | PASS | propose_workflow | 2 | ✓ | 158 | -18.29 | -2.46 | chat OK + bt_ok: trades=158 ret=-18.29% |
| ee_kotakbank_pair_zscore | PASS | propose_dsl_workflow | 5 | ✓ | 31 | 0.18 | 15.06 | chat OK + bt_ok: trades=31 ret=0.18% |
| ee_lt_compound_entry_pct_exit | PASS | propose_dsl_workflow | 5 | ✓ | 0 | 0.0 | 13.45 | chat OK + bt_ok: trades=0 ret=0.0% |
| ee_axisbank_ema_crossover_pair | PASS | propose_dsl_workflow | 5 | ✓ | 7 | 0.05 | 10.64 | chat OK + bt_ok: trades=7 ret=0.05% |
| ee_maruti_bb_pct_exit | PASS | propose_dsl_workflow | 5 | ✓ | 19 | -1.12 | 4.12 | chat OK + bt_ok: trades=19 ret=-1.12% |
| ee_itc_percentrank_exit | PASS | propose_dsl_workflow | 5 | ✓ | 62 | -1.35 | -21.31 | chat OK + bt_ok: trades=62 ret=-1.35% |
| ee_eternal_gap_recovery | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_niftybees_session_filtered | PASS | propose_dsl_workflow | 5 | ✓ | 0 | 0.0 | 6.78 | chat OK + bt_ok: trades=0 ret=0.0% |
| ee_bajajhfl_breakout_trail | PASS | propose_workflow | 2 | ✓ | 406 | -17.12 | -49.52 | chat OK + bt_ok: trades=406 ret=-17.12% |
| ee_reliance_pct_change_pair | PASS | propose_dsl_workflow | 5 | ✓ | 9 | -0.11 | -6.08 | chat OK + bt_ok: trades=9 ret=-0.11% |
| ee_hdfclife_atr_breakout | PASS | propose_dsl_workflow | 5 | ✓ | 28 | -0.77 | 10.56 | chat OK + bt_ok: trades=28 ret=-0.77% |
| ee_hyundai_double_entry_double_exit | PASS | propose_dsl_workflow | 5 | ✓ | 0 | 0.0 | 4.37 | chat OK + bt_ok: trades=0 ret=0.0% |
| ee_wipro_adx_trend | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
| ee_tcs_supertrend_session | PASS | propose_workflow | 2 | ✓ | 51 | -40.69 | -35.62 | chat OK + bt_ok: trades=51 ret=-40.69% |
| ee_eternal_macd_line_signal | PASS | propose_dsl_workflow | 5 | ✓ | 14 | -0.05 | 34.86 | chat OK + bt_ok: trades=14 ret=-0.05% |
| ee_bajajhfl_stoch_oversold | PASS | propose_dsl_workflow | 5 | ✓ | 4 | -0.01 | -49.52 | chat OK + bt_ok: trades=4 ret=-0.01% |
| ee_sbin_three_conditions_three_exits | PASS | propose_dsl_workflow | 5 | ✓ | 0 | 0.0 | 20.77 | chat OK + bt_ok: trades=0 ret=0.0% |
| ee_hdfcbank_volatility_regime | PASS | propose_dsl_workflow | 5 | ✓ | 50 | -0.09 | 4.4 | chat OK + bt_ok: trades=50 ret=-0.09% |
| ee_niftybees_252_breakout_trail | FAIL | propose_dsl_workflow | 0 | — | — | — | — | want_hint=workflow_draft_card got=ask_user |
