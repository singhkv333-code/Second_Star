"use client";

/**
 * OptionChainLauncherCard - a tiny entry point that opens the full-screen
 * OptionChainFullScreen overlay. Self-contained (owns its own open state), so
 * it can be dropped anywhere: as a chat widget (`variant="card"`) or as a
 * compact pill in the composer row (`variant="pill"`).
 */

import { useState } from "react";
import { ArrowUpRight, LayoutGrid } from "lucide-react";
import { cn } from "@/lib/utils";
import { useExclusiveSidePanel } from "@/lib/sidePanels";
import { OptionChainFullScreen } from "@/components/chat/OptionChainFullScreen";
import { OptionStrategyPanel } from "@/components/chat/OptionStrategyPanel";
import { computeOptionStrategy } from "@/lib/api";
import { isError } from "@/lib/types";
import type { OptionStrategyPayload, StrategyLeg } from "@/lib/types";

type StrategyDraft = {
  underlying: string;
  expiry: string;
  qtyLots: number;
  legs: Pick<StrategyLeg, "option_type" | "side" | "strike">[];
};

export function OptionChainLauncherCard({
  variant = "card",
  underlying = "NIFTY",
}: {
  variant?: "card" | "pill";
  underlying?: string;
}): React.ReactElement {
  const [open, setOpen] = useState(false);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [builderPayload, setBuilderPayload] = useState<OptionStrategyPayload | null>(null);
  const [buildPending, setBuildPending] = useState(false);
  const [buildError, setBuildError] = useState<string | null>(null);

  useExclusiveSidePanel("option-chain", open, () => setOpen(false));
  useExclusiveSidePanel("option-strategy", builderOpen, () => setBuilderOpen(false));

  async function handleBuildStrategy(draft: StrategyDraft): Promise<void> {
    setBuildPending(true);
    setBuildError(null);
    const result = await computeOptionStrategy({
      underlying: draft.underlying,
      expiry: draft.expiry,
      template: "custom",
      qty_lots: draft.qtyLots,
      legs: draft.legs,
    });
    setBuildPending(false);

    if (isError(result)) {
      setBuildError(result.error.message ?? "Couldn't open the builder right now.");
      return;
    }
    if (!result.data.success || !result.data.payload) {
      setBuildError(result.data.error ?? "Couldn't build this structure on the current chain.");
      return;
    }

    setBuilderPayload(result.data.payload);
    setOpen(false);
    setBuilderOpen(true);
  }

  return (
    <>
      {variant === "pill" ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          data-testid="option-chain-launcher-pill"
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] px-3 py-1.5 text-[11.5px] font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          <LayoutGrid className="h-3 w-3" aria-hidden="true" />
          Option chain
        </button>
      ) : (
        <div
          data-testid="option-chain-launcher-card"
          className={cn(
            "my-2 w-full max-w-[388px] overflow-hidden rounded-3xl border border-border/50 bg-card",
            "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
          )}
        >
          <div className="flex flex-col gap-2.5 px-5 py-4">
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center rounded-md bg-orange-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-orange-700 dark:bg-orange-500/15 dark:text-orange-300">
                F&amp;O
              </span>
              <span className="text-[10.5px] font-medium uppercase tracking-widest text-muted-foreground">
                Option chain
              </span>
            </div>
            <div className="flex items-baseline gap-2">
              <h3 className="text-[16px] font-semibold tracking-tight text-foreground">{underlying} options</h3>
              <span className="text-[12px] text-muted-foreground">strikes - OI - IV - greeks</span>
            </div>
            <p className="text-[11.5px] leading-snug text-muted-foreground">
              Open the live chain to browse strikes and build a basket - buy or sell any strike in one tap.
            </p>
            <button
              type="button"
              onClick={() => setOpen(true)}
              data-testid="option-chain-launcher-open"
              className={cn(
                "inline-flex h-8 w-full items-center justify-center gap-2 rounded-full bg-primary text-[12px] font-medium tracking-tight text-primary-foreground transition-all",
                "hover:bg-primary/90 active:scale-[0.98]",
              )}
            >
              <LayoutGrid className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>Open option chain</span>
              <ArrowUpRight className="h-4 w-4 shrink-0" aria-hidden="true" />
            </button>
          </div>
        </div>
      )}

      <OptionChainFullScreen
        open={open}
        onClose={() => setOpen(false)}
        underlying={underlying}
        onBuildStrategy={handleBuildStrategy}
        buildPending={buildPending}
        buildError={buildError}
      />
      {builderPayload && (
        <OptionStrategyPanel
          open={builderOpen}
          onOpenChange={setBuilderOpen}
          payload={builderPayload}
        />
      )}
    </>
  );
}
