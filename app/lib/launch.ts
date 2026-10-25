/**
 * What the launch build switches off. The code behind each stays in place;
 * these flags only decide whether the shell shows it and where its links go.
 *
 *   mainChat    the full Chat tab, its history sidebar and "New chat". With it
 *               off there is one chat everywhere: the ask bar opens the page
 *               assistant on every page and the workspace chat on the Chart,
 *               and "go deeper" continues in the workspace chat.
 *   strategies  the Strategy tab (agents and workflows), the /strategies page
 *               and Home's strategy launcher.
 *   execution   switching to live trading. The account stays on the paper
 *               book; the chart chat's Execution mode is off separately, by
 *               the chart's own PIVOT_CHART_EXECUTION.
 *   plays       the Execution tab: plays built in chat, run on the paper books
 *               (chart/plays.html, framed). Local only for now; it
 *               also needs the data server started with PIVOT_PLAYS=1.
 *
 * Each turns back on with its NEXT_PUBLIC_PIVOT_* variable set to "1" at
 * build time.
 */

const on = (v: string | undefined): boolean => v === "1" || v === "true";

export const LAUNCH = {
  mainChat: on(process.env.NEXT_PUBLIC_PIVOT_MAIN_CHAT),
  strategies: on(process.env.NEXT_PUBLIC_PIVOT_STRATEGIES),
  execution: on(process.env.NEXT_PUBLIC_PIVOT_EXECUTION),
  plays: on(process.env.NEXT_PUBLIC_PIVOT_PLAYS),
} as const;

/** Whether a shell tab is reachable in this build. */
export function tabEnabled(tab: string): boolean {
  if (tab === "chat") return LAUNCH.mainChat;
  if (tab === "agents") return LAUNCH.strategies;
  if (tab === "brokers") return LAUNCH.execution;   // linking a broker is for live orders
  if (tab === "plays") return LAUNCH.plays;
  return true;
}

/** Ask Pivot from anywhere: the shell opens the page assistant (or the
 *  workspace chat on the Chart) with this question. */
export const ASK_EVENT = "pivot:ask";

export function askPivot(text: string): void {
  window.dispatchEvent(new CustomEvent(ASK_EVENT, { detail: { text } }));
}
