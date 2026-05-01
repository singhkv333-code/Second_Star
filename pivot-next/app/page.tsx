"use client";

import { useState } from "react";
import { Bot } from "lucide-react";
import { AgentPanel } from "@/components/agent-panel/AgentPanel";
import { Button } from "@/components/ui/button";

export default function HomePage(): React.ReactElement {
  const [open, setOpen] = useState(false);
  return (
    <main className="flex min-h-screen flex-col bg-background">
      <header className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <h1 className="text-base font-semibold tracking-tight">
            Pivot — Agent System
          </h1>
          <p className="text-xs text-muted-foreground">
            Day 1 mock shell. Chat ports later this sprint.
          </p>
        </div>
        <Button onClick={() => setOpen(true)} size="sm">
          <Bot className="h-4 w-4" aria-hidden="true" />
          Open agent panel
        </Button>
      </header>
      <section className="flex flex-1 items-center justify-center p-12">
        <div className="max-w-md text-center">
          <p className="text-sm text-muted-foreground">
            Click <span className="font-medium text-foreground">Open agent panel</span> to
            review the 5-step demo workflow. Drag step rows to reorder. Press
            <kbd className="mx-1 rounded border bg-muted px-1.5 py-0.5 text-[10px] font-medium">Esc</kbd>
            to close.
          </p>
        </div>
      </section>
      <AgentPanel open={open} onOpenChange={setOpen} />
    </main>
  );
}
