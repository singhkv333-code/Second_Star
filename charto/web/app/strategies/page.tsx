"use client";

/**
 * /strategies — the saved rules, as Pivot's Agents page.
 *
 * `AgentsTab` unedited but for its three nouns: Charto calls these strategies.
 * It reads the Agent System's workflow contract, and `charto/data/strategies.py`
 * answers in that shape — a Charto strategy IS a workflow, and mapping it on
 * the backend is what keeps a 1,558-line component from being forked.
 *
 * The callbacks all lead back to the chart. There is no workflow editor here
 * on purpose: a strategy is amended in the conversation that built it, where
 * the whole draft is re-emitted and the condition tree stays the object the
 * backtest ran. A form that edited one threshold in isolation would let the
 * card and the armed rule drift apart.
 */

import { useCallback } from "react";

import { AgentsTab } from "@/components/agent-panel/AgentsTab";
import { BookShell, askOnChart } from "@/components/paper/BookShell";
import type { Workflow, WorkflowSummary } from "@/lib/types";

export default function StrategiesPage(): React.ReactElement {
  const editWithChat = useCallback((wf: WorkflowSummary) => {
    askOnChart(
      `Change my saved strategy "${wf.name}" — `,
      deriveSymbol(wf),
    );
  }, []);

  const openWorkflow = useCallback((wf: Workflow) => {
    // No editor to open. Asking about it by name is the nearest real thing,
    // and it is the same route "Edit with chat" takes.
    askOnChart(
      `How is my strategy "${wf.name}" doing, and what is it waiting for?`,
      deriveSymbol(wf),
    );
  }, []);

  return (
    <BookShell active="/strategies">
      <AgentsTab
        onOpenWorkflow={openWorkflow}
        onEditWithChat={editWithChat}
        onSendPrompt={(text: string) => askOnChart(text)}
        onEditBasketWithChat={() => undefined}
      />
    </BookShell>
  );
}

/** The instrument to open the chart on — the first ticker-shaped word in the
 *  strategy's own description, which is where the backend puts it. */
function deriveSymbol(wf: Workflow | WorkflowSummary): string | undefined {
  const m = `${wf.name} ${wf.description ?? ""}`.match(/\b[A-Z]{2,12}\b/);
  return m ? m[0] : undefined;
}
