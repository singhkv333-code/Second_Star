# Slice-4 chat eval — multi_entry_exit_run_1

- recorded_at: 2026-05-25T11:53:20.252719+00:00 → 2026-05-25T12:01:24.097250+00:00
- prompts: 50
- backend: http://127.0.0.1:8000

## Triad summary

**Quality** — verdict distribution:
  - PASS: 36 / 50 (72%)
  - PARTIAL: 2 / 50 (4%)
  - FAIL: 12 / 50 (24%)

**Latency** (wall-clock per prompt, ms):
  - mean: 9676
  - p50:  9777
  - p95:  16538

**Tokens** (sum across all LLM calls in window):
  - input:  1,782,731
  - output: 14,542
  - total:  1,797,273
  - calls:  118
  - cost_usd (Azure-recorded): $0.2888

## By category (verdict counts)

| category | PASS | PARTIAL | FAIL |
|---|---|---|---|
| edge | 9 | 0 | 1 |
| entry_exit | 9 | 1 | 5 |
| mixed | 6 | 1 | 3 |
| multi_entry | 12 | 0 | 3 |

## Per-prompt detail

| id | verdict | tools | hint | tok(in/out) | wall(ms) | reason |
|---|---|---|---|---|---|---|
| me_rsi_ema_basic | PASS | propose_dsl_workflow | workflow_draft_card | 41,537/526 | 10,053 | 2/2 checks |
| me_rsi_macd_ema_triple | PASS | propose_dsl_workflow | workflow_draft_card | 40,935/301 | 10,504 | 2/2 checks |
| me_rsi_or_macd_cross | PASS | propose_dsl_workflow | workflow_draft_card | 40,818/242 | 11,967 | 2/2 checks |
| me_golden_cross | PASS | propose_workflow | workflow_draft_card | 41,040/292 | 8,697 | 2/2 checks |
| me_20_50_sma_cross | FAIL | ASK_USER | ask_user | 19,074/44 | 4,083 | 0/2; first_fail: want_any_of=['propose_dsl_workflow', 'propose_workflow'] got=[' |
| me_bollinger_lower_break | PASS | propose_dsl_workflow | workflow_draft_card | 43,261/173 | 10,623 | 2/2 checks |
| me_rsi_volume_confirm | PASS | propose_workflow | workflow_draft_card | 39,011/406 | 10,138 | 2/2 checks |
| me_macd_zero_cross_price_above_sma | PASS | propose_workflow | workflow_draft_card | 38,918/374 | 9,044 | 2/2 checks |
| me_rsi_pct_dip | PASS | propose_workflow | workflow_draft_card | 0/0 | 21 | 2/2 checks |
| me_rsi_ema_volume_quadruple | PASS | propose_dsl_workflow | workflow_draft_card | 41,101/373 | 10,694 | 2/2 checks |
| me_breakout_20day_high | FAIL | ASK_USER | ask_user | 21,352/260 | 7,281 | 0/2; first_fail: want_any_of=['propose_dsl_workflow', 'propose_workflow'] got=[' |
| me_bollinger_squeeze | FAIL | ASK_USER | ask_user | 39,143/85 | 8,591 | 0/2; first_fail: want_any_of=['propose_dsl_workflow', 'propose_workflow'] got=[' |
| me_adx_trending_with_rsi | PASS | propose_dsl_workflow | workflow_draft_card | 40,845/290 | 13,568 | 2/2 checks |
| me_pairs_spread | PASS | propose_dsl_workflow | workflow_draft_card | 41,253/170 | 9,569 | 2/2 checks |
| me_higher_high_higher_low | PASS | propose_dsl_workflow | workflow_draft_card | 34,292/245 | 9,936 | 2/2 checks |
| ee_rsi_entry_exit_pair | PASS | propose_dsl_workflow | workflow_draft_card | 40,664/170 | 9,831 | 2/2 checks |
| ee_rsi_entry_multi_exit | PARTIAL | propose_workflow | ask_user | 39,064/786 | 14,974 | 1/2; first_fail: want=workflow_draft_card got=ask_user |
| ee_macd_entry_drawdown_exit | PASS | propose_workflow | workflow_draft_card | 38,946/388 | 9,389 | 2/2 checks |
| ee_bollinger_pair | FAIL | get_live_price,get_indicator | ask_user | 20,734/144 | 7,327 | 0/2; first_fail: want_any_of=['propose_dsl_workflow', 'propose_workflow'] got=[' |
| ee_open_close_intraday | PASS | propose_workflow | workflow_draft_card | 36,987/242 | 7,056 | 2/2 checks |
| ee_dca_n_day_hold | FAIL | ASK_USER | ask_user | 19,368/50 | 3,595 | 0/2; first_fail: want_any_of=['propose_scheduled_order', 'propose_workflow'] got |
| ee_golden_cross_trail_5pct | FAIL | — | — | 20,228/66 | 6,818 | 0/2; first_fail: want_any_of=['propose_dsl_workflow', 'propose_workflow'] got=[] |
| ee_rsi_entry_time_or_indicator_exit | PASS | propose_dsl_workflow | workflow_draft_card | 40,660/167 | 12,067 | 2/2 checks |
| ee_sma_cross_sl_tp | PASS | propose_workflow | workflow_draft_card | 0/0 | 30 | 2/2 checks |
| ee_breakout_with_stops | PASS | propose_dsl_workflow | workflow_draft_card | 41,939/184 | 11,648 | 2/2 checks |
| ee_macd_entry_two_exits | PASS | propose_workflow | workflow_draft_card | 39,774/450 | 10,134 | 2/2 checks |
| ee_ema_pullback_exit_on_break | FAIL | ASK_USER | ask_user | 19,088/471 | 17,358 | 0/2; first_fail: want_any_of=['propose_dsl_workflow', 'propose_workflow'] got=[' |
| ee_bollinger_mean_reversion | PASS | propose_dsl_workflow | workflow_draft_card | 41,119/271 | 12,647 | 2/2 checks |
| ee_volume_breakout_trail | PASS | propose_workflow | workflow_draft_card | 0/0 | 34 | 2/2 checks |
| ee_buy_dip_sell_recovery | FAIL | ASK_USER | ask_user | 19,372/117 | 3,348 | 0/2; first_fail: want_any_of=['propose_dsl_workflow', 'propose_workflow'] got=[' |
| mx_double_filter_double_exit | PASS | propose_workflow | workflow_draft_card | 60,837/1,135 | 16,918 | 2/2 checks |
| mx_triple_entry_triple_exit | PASS | propose_dsl_workflow | workflow_draft_card | 41,084/362 | 13,179 | 2/2 checks |
| mx_pairs_entry_zscore_exit | FAIL | find_tool,backtest_dsl_tree | indicator_backtest_chart | 66,701/323 | 19,301 | 0/2; first_fail: want_any_of=['propose_dsl_workflow', 'propose_workflow'] got=[' |
| mx_or_entry_compound_exit | PASS | propose_dsl_workflow | workflow_draft_card | 41,108/218 | 9,347 | 2/2 checks |
| mx_macd_signal_cross_bb_exit | PASS | propose_dsl_workflow | workflow_draft_card | 41,181/281 | 9,629 | 2/2 checks |
| mx_supertrend_with_trail | PASS | propose_workflow | workflow_draft_card | 39,091/424 | 11,361 | 2/2 checks |
| mx_2y_window_strategy | PARTIAL | find_tool,backtest_dsl_tree | ask_user | 45,169/436 | 16,073 | 1/2; first_fail: want=workflow_draft_card got=ask_user |
| mx_aggregator_entry_pct_exit | FAIL | find_tool,backtest_dsl_tree | ask_user | 45,179/363 | 14,645 | 0/2; first_fail: want_any_of=['propose_dsl_workflow', 'propose_workflow'] got=[' |
| mx_session_filtered_entry | FAIL | ASK_USER | ask_user | 19,080/35 | 4,603 | 0/2; first_fail: want_any_of=['propose_dsl_workflow', 'propose_workflow'] got=[' |
| mx_gap_down_buy_recovery_exit | PASS | propose_workflow | workflow_draft_card | 39,123/453 | 13,639 | 2/2 checks |
| edge_macd_line_vs_signal | PASS | propose_workflow | workflow_draft_card | 38,611/244 | 8,793 | 2/2 checks |
| edge_holding_dd_sell | PASS | propose_holding_action | workflow_draft_card | 40,684/67 | 8,190 | 2/2 checks |
| edge_pct_change_multi_window | PASS | propose_dsl_workflow | workflow_draft_card | 41,339/203 | 9,721 | 2/2 checks |
| edge_atr_break_with_session | PASS | propose_workflow | workflow_draft_card | 41,378/422 | 9,017 | 2/2 checks |
| edge_252day_breakout | PASS | propose_dsl_workflow | workflow_draft_card | 41,286/163 | 9,831 | 2/2 checks |
| edge_two_indicator_crossover_exit_on_reverse | FAIL | ASK_USER | ask_user | 19,085/436 | 5,849 | 0/2; first_fail: want_any_of=['propose_dsl_workflow', 'propose_workflow'] got=[' |
| edge_volatility_regime_filter | PASS | propose_dsl_workflow | workflow_draft_card | 40,839/224 | 10,631 | 2/2 checks |
| edge_compound_or_pattern | PASS | propose_dsl_workflow | workflow_draft_card | 41,319/327 | 8,910 | 2/2 checks |
| edge_bars_held_pure_exit | PASS | propose_holding_action | workflow_draft_card | 38,971/92 | 9,620 | 2/2 checks |
| edge_compound_entry_and_sl_tp | PASS | propose_workflow | workflow_draft_card | 60,143/1,047 | 13,492 | 2/2 checks |
