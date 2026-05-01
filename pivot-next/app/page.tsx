"use client";

import { useState } from "react";
import { Activity, Bot } from "lucide-react";
import { AgentPanel } from "@/components/agent-panel/AgentPanel";
import { RunView } from "@/components/agent-panel/RunView";
import { useStepCatalog } from "@/components/agent-panel/use-step-catalog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

export default function HomePage(): React.ReactElement {
  const [open, setOpen] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);

  return (
    <main className="flex min-h-screen flex-col bg-background">
      <header className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <h1 className="text-base font-semibold tracking-tight">
            Pivot — Agent System
          </h1>
          <p className="text-xs text-muted-foreground">
            Day 2 mock shell. Chat ports later this sprint.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setRunId(`mock-run-${Date.now().toString(36)}`)}
          >
            <Activity className="h-4 w-4" aria-hidden="true" />
            View run
          </Button>
          <Button onClick={() => setOpen(true)} size="sm">
            <Bot className="h-4 w-4" aria-hidden="true" />
            Open agent panel
          </Button>
        </div>
      </header>

      <section className="flex flex-1 items-stretch justify-center p-12">
        {runId ? (
          <RunFrame runId={runId} onClose={() => setRunId(null)} />
        ) : (
          <div className="max-w-md self-center text-center">
            <p className="text-sm text-muted-foreground">
              Click <span className="font-medium text-foreground">Open agent panel</span> to
              review the 5-step demo workflow, or
              <span className="font-medium text-foreground"> View run </span>
              to watch a simulated execution end-to-end.
            </p>
          </div>
        )}
      </section>

      <AgentPanel open={open} onOpenChange={setOpen} />
    </main>
  );
}

function RunFrame({
  runId,
  onClose,
}: {
  runId: string;
  onClose: () => void;
}): React.ReactElement {
  const state = useStepCatalog();
  if (state.status === "loading") {
    return (
      <div className="w-full max-w-2xl space-y-3">
        <Skeleton className="h-6 w-1/3" />
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full rounded-xl" />
        ))}
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div role="alert" className="max-w-md self-center text-center">
        <p className="text-sm font-medium">Couldn&apos;t load step catalog</p>
        <p className="mt-1 text-xs text-muted-foreground">{state.error.message}</p>
      </div>
    );
  }
  return (
    <div className="w-full max-w-2xl rounded-xl border bg-card shadow-sm">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <p className="text-xs text-muted-foreground">Live run preview</p>
        <Button size="sm" variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
      <div className="h-[640px]">
        <RunView runId={runId} catalog={state.catalog} onClose={onClose} />
      </div>
    </div>
  );
}
