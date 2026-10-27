/**
 * The chat loader's words. Pure, so the rule that every phrase is TRUE can
 * be tested: nothing here is on a timer except "thinking", and a tool's
 * subject only ever comes from the backend's `hint` (the model's own args).
 */

export type StatusTool = { name: string; hint?: string; ok: boolean | undefined };

type Phrase = { key: string; say: (subject: string) => string; bare: string };

const PHRASES: ReadonlyArray<[RegExp, Phrase]> = [
  [/^get_index_level$/, { key: "index", say: (s) => `Checking ${s}`, bare: "Checking the indices" }],
  [/^get_market_status$/, { key: "status", say: () => "Checking market hours", bare: "Checking market hours" }],
  [/^get_top_movers$/, { key: "movers", say: () => "Scanning the day's movers", bare: "Scanning the day's movers" }],
  [/^get_market_data$/, { key: "prices", say: (s) => `Pulling prices for ${s}`, bare: "Pulling prices" }],
  [/^get_indicators$/, { key: "ind", say: (s) => `Computing indicators on ${s}`, bare: "Computing indicators" }],
  [/^(fetch_fundamentals|query_financials|get_company_research)$/, { key: "fin", say: (s) => `Reading ${s} financials`, bare: "Reading the financials" }],
  [/^show_price_chart$/, { key: "chart", say: (s) => `Charting ${s}`, bare: "Preparing the chart" }],
  [/^scan_technicals$/, { key: "scan", say: () => "Scanning the charts market-wide", bare: "Scanning the charts market-wide" }],
  [/^screen_fundamentals$/, { key: "screen", say: (s) => `Screening ${s}`, bare: "Screening the market" }],
  [/^get_symbol_news$/, { key: "news", say: (s) => `Reading news on ${s}`, bare: "Reading the news" }],
  [/^web_search/, { key: "web", say: (s) => `Searching the web for ${s}`, bare: "Searching the web" }],
  [/^(compare_performance|get_correlation_matrix|regime_compare_metrics)$/, { key: "cmp", say: (s) => `Comparing ${s}`, bare: "Comparing performance" }],
  [/^(scan_pairs|test_cointegration)$/, { key: "pairs", say: (s) => `Testing ${s}`, bare: "Testing pairs" }],
  [/backtest/, { key: "bt", say: (s) => `Backtesting ${s}`, bare: "Running the backtest" }],
  [/^get_portfolio/, { key: "pf", say: (s) => `Reading your ${s} position`, bare: "Reading your portfolio" }],
  [/^get_option_chain$|^roll_option/, { key: "chain", say: (s) => `Reading the ${s} option chain`, bare: "Reading the option chain" }],
  [/option_strategy$/, { key: "opt", say: (s) => `Building a ${s} options strategy`, bare: "Building an options strategy" }],
  [/^(propose_|build_strategy|create_strategy|register_workflow|compose_multistep)/, { key: "draft", say: (s) => `Drafting a strategy for ${s}`, bare: "Drafting the strategy" }],
  [/order|^squareoff|^create_(dip_buy|sip)|^cancel_gtt/, { key: "order", say: (s) => `Preparing the ${s} order`, bare: "Preparing the order" }],
  [/^(calculate|compute)$/, { key: "calc", say: () => "Running the numbers", bare: "Running the numbers" }],
  [/ipo/, { key: "ipo", say: (s) => `Checking the ${s} IPO`, bare: "Checking IPOs" }],
  [/yield/, { key: "yield", say: () => "Comparing yields", bare: "Comparing yields" }],
  [/automation|workflow_status|scheduler|upcoming_jobs|pending_orders|gtt_orders/, { key: "auto", say: () => "Checking your automations", bare: "Checking your automations" }],
];
const WORKING: Phrase = { key: "work", say: () => "Working on it", bare: "Working on it" };

function phraseFor(name: string): Phrase {
  for (const [re, p] of PHRASES) if (re.test(name)) return p;
  return WORKING;
}

/** "A", "A and B", "A, B and 2 more" — subjects of same-kind parallel calls. */
function joinSubjects(subjects: string[]): string {
  const u = [...new Set(subjects)];
  if (u.length <= 2) return u.join(" and ");
  return `${u[0]}, ${u[1]} and ${u.length - 2} more`;
}

/** What is true right now, as one phrase. Pure, so it is testable. */
export function statusPhrase(tools: StatusTool[], hasText: boolean, elapsedMs: number): string {
  if (hasText) return "Writing the answer";
  const running = tools.filter((t) => t.ok === undefined);
  if (running.length > 0) {
    // Speak for the newest call; parallel calls of the same kind merge.
    const p = phraseFor(running[running.length - 1]!.name);
    const subjects = running
      .filter((t) => phraseFor(t.name).key === p.key)
      .map((t) => t.hint?.trim())
      .filter((h): h is string => !!h);
    return subjects.length ? p.say(joinSubjects(subjects)) : p.bare;
  }
  if (tools.length > 0) return "Reading the results";
  if (elapsedMs < 4000) return "Thinking";
  if (elapsedMs < 12000) return "Thinking it through";
  return "Still working on it";
}
