# Slice-4 chat eval — slice4_run_1

- recorded_at: 2026-05-25T07:22:41.690107+00:00 → 2026-05-25T07:31:18.895206+00:00
- prompts: 50
- backend: http://127.0.0.1:8000

## Triad summary

**Quality** — verdict distribution:
  - PASS: 44 / 50 (88%)
  - PARTIAL: 3 / 50 (6%)
  - FAIL: 3 / 50 (6%)

**Latency** (wall-clock per prompt, ms):
  - mean: 10343
  - p50:  10111
  - p95:  15964

**Tokens** (sum across all LLM calls in window):
  - input:  1,978,105
  - output: 7,839
  - total:  1,985,944
  - calls:  128
  - cost_usd (Azure-recorded): $0.3110

## By category (verdict counts)

| category | PASS | PARTIAL | FAIL |
|---|---|---|---|
| baseline | 9 | 0 | 0 |
| polymarket | 35 | 3 | 3 |

## Per-prompt detail

| id | verdict | tools | hint | tok(in/out) | wall(ms) | reason |
|---|---|---|---|---|---|---|
| pm_thr_btc_150k_above_30 | PASS | propose_polymarket_trigger | polymarket_trigger_draft | 38,744/137 | 10,229 | 4/4 checks |
| pm_thr_trump_2028_above_25 | PARTIAL | propose_polymarket_trigger | polymarket_trigger_picker | 39,631/153 | 11,367 | 2/3; first_fail: want=polymarket_trigger_draft got=polymarket_trigger_picker |
| pm_thr_fed_june_above_60 | PASS | propose_polymarket_trigger | polymarket_trigger_draft | 39,753/145 | 11,302 | 4/4 checks |
| pm_thr_iran_ceasefire_below_20 | PASS | propose_polymarket_trigger | polymarket_trigger_draft | 39,724/134 | 11,833 | 4/4 checks |
| pm_thr_btc_above_100k_above_70 | PARTIAL | propose_polymarket_trigger | polymarket_trigger_picker | 37,993/91 | 8,444 | 1/2; first_fail: want=polymarket_trigger_draft got=polymarket_trigger_picker |
| pm_thr_eth_4k_above_50 | PARTIAL | propose_polymarket_trigger | polymarket_trigger_picker | 39,582/206 | 10,815 | 1/2; first_fail: want=polymarket_trigger_draft got=polymarket_trigger_picker |
| pm_thr_modi_2029_above_80 | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 39,687/143 | 13,516 | 1/1 checks |
| pm_thr_world_cup_brazil_above_30 | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 39,595/140 | 12,286 | 1/1 checks |
| pm_thr_nba_finals_above_25 | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 38,030/117 | 9,501 | 1/1 checks |
| pm_thr_xi_summit_above_40 | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 38,605/148 | 12,516 | 1/1 checks |
| pm_thr_nobel_above_15 | PASS | propose_polymarket_trigger | polymarket_trigger_draft | 39,752/144 | 11,938 | 1/1 checks |
| pm_thr_oil_100_above_55 | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 39,793/165 | 10,478 | 1/1 checks |
| pm_smart_no_threshold_iran | PASS | propose_polymarket_trigger | polymarket_trigger_draft | 39,716/150 | 9,777 | 3/3 checks |
| pm_smart_no_threshold_btc | FAIL | — | — | 19,325/77 | 3,236 | 0/2; first_fail: want=propose_polymarket_trigger got=[] |
| pm_smart_no_threshold_modi | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 40,748/138 | 7,808 | 1/1 checks |
| pm_smart_no_threshold_fed | FAIL | — | — | 18,866/49 | 4,572 | 0/2; first_fail: want=propose_polymarket_trigger got=[] |
| pm_smart_no_threshold_election | PASS | find_tool,propose_polymarket_trigger | polymarket_trigger_picker | 65,136/154 | 13,764 | 1/1 checks |
| pm_smart_no_threshold_sports | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 39,620/144 | 11,573 | 1/1 checks |
| pm_res_trump_2028_yes | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 39,613/150 | 12,670 | 3/3 checks |
| pm_res_fed_no | PASS | propose_polymarket_trigger | polymarket_trigger_draft | 43,060/124 | 11,750 | 3/3 checks |
| pm_res_iran_actually_breaks | PASS | propose_polymarket_trigger | polymarket_trigger_draft | 39,690/161 | 19,553 | 2/2 checks |
| pm_res_world_cup_resolves | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 39,588/143 | 16,082 | 2/2 checks |
| pm_res_btc_150k_decided | PASS | find_tool,propose_polymarket_trigger | polymarket_trigger_draft | 61,904/168 | 15,818 | 2/2 checks |
| pm_res_either_outcome | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 41,936/151 | 14,378 | 2/2 checks |
| pm_browse_default | PASS | browse_polymarket_markets | polymarket_market_browse_card | 39,849/164 | 8,917 | 3/3 checks |
| pm_browse_bitcoin | PASS | browse_polymarket_markets | polymarket_market_browse_card | 39,425/408 | 11,161 | 3/3 checks |
| pm_browse_politics | PASS | browse_polymarket_markets | polymarket_market_browse_card | 39,810/281 | 9,545 | 2/2 checks |
| pm_browse_sports | PASS | browse_polymarket_markets | polymarket_market_browse_card | 38,677/275 | 9,741 | 2/2 checks |
| pm_browse_geopolitics | PASS | browse_polymarket_markets | polymarket_market_browse_card | 39,818/178 | 9,992 | 2/2 checks |
| pm_browse_what_can_i_bet | PASS | browse_polymarket_markets | polymarket_market_browse_card | 39,770/191 | 9,522 | 1/1 checks |
| pm_neg_trump_no_win | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 39,654/151 | 12,694 | 1/1 checks |
| pm_neg_fed_no_cut | PASS | propose_polymarket_trigger,propose_workf | polymarket_trigger_draft | 60,421/299 | 14,746 | 1/1 checks |
| pm_neg_modi_loses | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 39,702/145 | 11,261 | 1/1 checks |
| pm_neg_brazil_doesnt_win | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 39,581/131 | 9,909 | 1/1 checks |
| pm_compound_buy_reliance_sell_on_poly | PASS | propose_polymarket_trigger,propose_workf | polymarket_trigger_draft | 62,328/423 | 13,950 | 1/1 checks |
| pm_compound_buy_oil_etf_on_resolution | PASS | propose_polymarket_trigger,propose_workf | polymarket_trigger_draft | 88,099/579 | 24,407 | 1/1 checks |
| pm_compound_hedge_sell_trump | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 42,960/144 | 10,816 | 2/2 checks |
| pm_compound_buy_btc_etf_on_threshold | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 41,343/117 | 9,381 | 1/1 checks |
| pm_no_match_aliens | PASS | propose_polymarket_trigger | polymarket_trigger_picker | 38,008/92 | 9,629 | 2/2 checks |
| pm_no_match_vague_crypto | PASS | ASK_USER | ask_user | 18,865/42 | 3,822 | 1/1 checks |
| pm_no_match_vague_election | FAIL | propose_workflow | workflow_draft_card | 39,942/125 | 6,654 | 0/1; first_fail: want_any_of=['ASK_USER', 'propose_polymarket_trigger'] got=['pr |
| base_buy_infy | PASS | place_market_order | logic_card | 31,834/74 | 6,141 | 2/2 checks |
| base_portfolio | PASS | get_holdings | — | 36,337/214 | 6,964 | 1/1 checks |
| base_rsi_reliance | PASS | get_indicator | — | 38,209/65 | 7,987 | 1/1 checks |
| base_sl_holding | PASS | create_sl_order | logic_card | 32,401/69 | 6,260 | 1/1 checks |
| base_market_status | PASS | get_market_status | — | 38,758/47 | 6,232 | 1/1 checks |
| base_backtest_rsi | PASS | backtest_workflow | indicator_backtest_chart | 44,082/140 | 7,403 | 1/1 checks |
| base_yield_recommendation | PASS | get_yield_recommendation | — | 28,254/100 | 8,440 | 1/1 checks |
| base_agent_build | PASS | propose_workflow | workflow_draft_card | 0/0 | 19 | 1/1 checks |
| base_cancel_order | PASS | list_pending_orders | ask_user | 19,887/53 | 6,337 | 1/1 checks |
