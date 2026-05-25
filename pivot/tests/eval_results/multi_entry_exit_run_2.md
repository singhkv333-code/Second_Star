# Slice-4 chat eval — multi_entry_exit_run_2

- recorded_at: 2026-05-25T12:32:18.015843+00:00 → 2026-05-25T12:40:13.155955+00:00
- prompts: 50
- backend: http://127.0.0.1:8000

## Triad summary

**Quality** — verdict distribution:
  - PASS: 48 / 50 (96%)
  - PARTIAL: 1 / 50 (2%)
  - FAIL: 1 / 50 (2%)

**Latency** (wall-clock per prompt, ms):
  - mean: 9502
  - p50:  10125
  - p95:  12670

**Tokens** (sum across all LLM calls in window):
  - input:  1,940,727
  - output: 11,431
  - total:  1,952,158
  - calls:  135
  - cost_usd (Azure-recorded): $0.3014

## By category (verdict counts)

| category | PASS | PARTIAL | FAIL |
|---|---|---|---|
| edge | 9 | 0 | 1 |
| entry_exit | 14 | 1 | 0 |
| mixed | 10 | 0 | 0 |
| multi_entry | 15 | 0 | 0 |

## Per-prompt detail

| id | verdict | tools | hint | tok(in/out) | wall(ms) | reason |
|---|---|---|---|---|---|---|
| me_rsi_ema_basic | PASS | propose_dsl_workflow | workflow_draft_card | 42,465/221 | 12,655 | 2/2 checks |
| me_rsi_macd_ema_triple | PASS | propose_dsl_workflow | workflow_draft_card | 42,361/325 | 11,826 | 2/2 checks |
| me_rsi_or_macd_cross | PASS | propose_dsl_workflow | workflow_draft_card | 42,192/252 | 12,506 | 2/2 checks |
| me_golden_cross | PASS | propose_dsl_workflow | workflow_draft_card | 44,368/165 | 8,470 | 2/2 checks |
| me_20_50_sma_cross | PASS | propose_dsl_workflow | workflow_draft_card | 42,073/180 | 10,245 | 2/2 checks |
| me_bollinger_lower_break | PASS | propose_dsl_workflow | workflow_draft_card | 44,643/197 | 10,127 | 2/2 checks |
| me_rsi_volume_confirm | PASS | propose_dsl_workflow | workflow_draft_card | 42,253/253 | 10,809 | 2/2 checks |
| me_macd_zero_cross_price_above_sma | PASS | propose_dsl_workflow | workflow_draft_card | 42,152/234 | 10,564 | 2/2 checks |
| me_rsi_pct_dip | PASS | propose_workflow | workflow_draft_card | 0/0 | 62 | 2/2 checks |
| me_rsi_ema_volume_quadruple | PASS | propose_dsl_workflow | workflow_draft_card | 42,501/396 | 11,050 | 2/2 checks |
| me_breakout_20day_high | PASS | propose_dsl_workflow | workflow_draft_card | 42,505/231 | 9,495 | 2/2 checks |
| me_bollinger_squeeze | PASS | propose_dsl_workflow | workflow_draft_card | 42,192/213 | 13,164 | 2/2 checks |
| me_adx_trending_with_rsi | PASS | propose_dsl_workflow | workflow_draft_card | 42,225/236 | 13,183 | 2/2 checks |
| me_pairs_spread | PASS | propose_dsl_workflow | workflow_draft_card | 42,617/169 | 7,655 | 2/2 checks |
| me_higher_high_higher_low | PASS | propose_dsl_workflow | workflow_draft_card | 35,667/245 | 8,945 | 2/2 checks |
| ee_rsi_entry_exit_pair | PASS | propose_dsl_workflow | workflow_draft_card | 42,032/171 | 10,209 | 2/2 checks |
| ee_rsi_entry_multi_exit | PASS | propose_dsl_workflow | workflow_draft_card | 42,039/166 | 11,158 | 2/2 checks |
| ee_macd_entry_drawdown_exit | PASS | propose_dsl_workflow | workflow_draft_card | 42,117/196 | 9,398 | 2/2 checks |
| ee_bollinger_pair | PASS | propose_dsl_workflow | workflow_draft_card | 44,649/201 | 11,740 | 2/2 checks |
| ee_open_close_intraday | PASS | propose_workflow | workflow_draft_card | 38,352/234 | 7,174 | 2/2 checks |
| ee_dca_n_day_hold | PARTIAL | propose_scheduled_order | ask_user | 20,433/409 | 7,076 | 1/2; first_fail: want=workflow_draft_card got=ask_user |
| ee_golden_cross_trail_5pct | PASS | propose_dsl_workflow | workflow_draft_card | 44,440/212 | 10,078 | 2/2 checks |
| ee_rsi_entry_time_or_indicator_exit | PASS | propose_dsl_workflow | workflow_draft_card | 42,041/180 | 10,413 | 2/2 checks |
| ee_sma_cross_sl_tp | PASS | propose_workflow | workflow_draft_card | 0/0 | 46 | 2/2 checks |
| ee_breakout_with_stops | PASS | propose_dsl_workflow | workflow_draft_card | 43,330/207 | 10,994 | 2/2 checks |
| ee_macd_entry_two_exits | PASS | propose_dsl_workflow,propose_workflow | workflow_draft_card | 43,532/514 | 11,587 | 2/2 checks |
| ee_ema_pullback_exit_on_break | PASS | propose_dsl_workflow | workflow_draft_card | 42,235/245 | 10,121 | 2/2 checks |
| ee_bollinger_mean_reversion | PASS | propose_dsl_workflow | workflow_draft_card | 42,487/245 | 8,117 | 2/2 checks |
| ee_volume_breakout_trail | PASS | propose_workflow | workflow_draft_card | 0/0 | 29 | 2/2 checks |
| ee_buy_dip_sell_recovery | PASS | propose_dsl_workflow | workflow_draft_card | 42,622/177 | 9,162 | 2/2 checks |
| mx_double_filter_double_exit | PASS | propose_dsl_workflow | workflow_draft_card | 42,946/295 | 12,111 | 2/2 checks |
| mx_triple_entry_triple_exit | PASS | propose_dsl_workflow | workflow_draft_card | 42,491/373 | 11,055 | 2/2 checks |
| mx_pairs_entry_zscore_exit | PASS | propose_dsl_workflow | workflow_draft_card | 37,385/278 | 9,454 | 2/2 checks |
| mx_or_entry_compound_exit | PASS | propose_dsl_workflow | workflow_draft_card | 42,485/219 | 8,788 | 2/2 checks |
| mx_macd_signal_cross_bb_exit | PASS | propose_dsl_workflow | workflow_draft_card | 42,562/296 | 10,027 | 2/2 checks |
| mx_supertrend_with_trail | PASS | propose_dsl_workflow | workflow_draft_card | 42,296/277 | 10,032 | 2/2 checks |
| mx_2y_window_strategy | PASS | propose_dsl_workflow | workflow_draft_card | 42,228/275 | 9,822 | 2/2 checks |
| mx_aggregator_entry_pct_exit | PASS | propose_dsl_workflow | workflow_draft_card | 42,162/220 | 10,955 | 2/2 checks |
| mx_session_filtered_entry | PASS | propose_dsl_workflow | workflow_draft_card | 42,219/282 | 10,454 | 2/2 checks |
| mx_gap_down_buy_recovery_exit | PASS | propose_workflow | workflow_draft_card | 40,426/421 | 10,163 | 2/2 checks |
| edge_macd_line_vs_signal | PASS | propose_dsl_workflow | workflow_draft_card | 42,025/148 | 12,098 | 2/2 checks |
| edge_holding_dd_sell | FAIL | — | — | 20,910/44 | 3,394 | 0/2; first_fail: want_any_of=['propose_dsl_workflow', 'propose_holding_action',  |
| edge_pct_change_multi_window | PASS | propose_dsl_workflow | workflow_draft_card | 42,709/203 | 9,014 | 2/2 checks |
| edge_atr_break_with_session | PASS | propose_dsl_workflow | workflow_draft_card | 44,686/300 | 12,682 | 2/2 checks |
| edge_252day_breakout | PASS | propose_dsl_workflow | workflow_draft_card | 42,671/181 | 8,240 | 2/2 checks |
| edge_two_indicator_crossover_exit_on_reverse | PASS | propose_dsl_workflow | workflow_draft_card | 42,114/207 | 10,073 | 2/2 checks |
| edge_volatility_regime_filter | PASS | propose_dsl_workflow | workflow_draft_card | 42,212/224 | 11,016 | 2/2 checks |
| edge_compound_or_pattern | PASS | propose_dsl_workflow | workflow_draft_card | 42,688/332 | 9,308 | 2/2 checks |
| edge_bars_held_pure_exit | PASS | propose_holding_action | workflow_draft_card | 40,347/76 | 7,238 | 2/2 checks |
| edge_compound_entry_and_sl_tp | PASS | propose_dsl_workflow | workflow_draft_card | 42,642/306 | 11,104 | 2/2 checks |
