"use client";

/**
 * BasketTradeModal — turn a saved equity basket into real orders.
 *
 * Enter capital → the backend sizes each name to whole shares at live prices
 * (a `dry_run` preview) → "Trade" places the BUYs through the connected broker.
 * Register-not-execute: with no broker connected the backend returns a clear
 * "connect your broker" message; while live execution is off the orders are
 * registered (confirm in your broker app), not auto-placed.
 */

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { isError } from "@/lib/types";
import {
  tradeEquityBasket,
  type BasketTradeDryRun,
  type BasketTradePlaced,
  type EquityBasket,
} from "@/lib/agentsApi";

const inr = (n: number): string => `₹${Math.round(n).toLocaleString("en-IN")}`;

export function BasketTradeModal({
  open,
  onOpenChange,
  basket,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  basket: EquityBasket;
}): React.ReactElement {
  const [capital, setCapital] = useState<string>(
    basket.capital_inr != null ? String(basket.capital_inr) : "",
  );
  const [preview, setPreview] = useState<BasketTradeDryRun | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [placed, setPlaced] = useState<BasketTradePlaced | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runPreview = useCallback(
    async (cap: number): Promise<void> => {
      setPreviewing(true);
      setError(null);
      const res = await tradeEquityBasket(basket.id, {
        capital_inr: cap,
        dry_run: true,
      });
      setPreviewing(false);
      if (isError(res)) {
        setPreview(null);
        setError(res.error.message);
        return;
      }
      setPreview(res.data as BasketTradeDryRun);
    },
    [basket.id],
  );

  // Re-sync + auto-preview each time the modal opens (Dialog stays mounted).
  useEffect(() => {
    if (!open) return;
    setPlaced(null);
    setError(null);
    setPreview(null);
    const cap = basket.capital_inr ?? 0;
    setCapital(cap ? String(cap) : "");
    if (cap > 0) void runPreview(cap);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, basket.id]);

  const capNum = Number(capital);
  const capValid = Number.isFinite(capNum) && capNum > 0;

  async function handleTrade(): Promise<void> {
    if (placing || !capValid) return;
    setPlacing(true);
    setError(null);
    const res = await tradeEquityBasket(basket.id, {
      capital_inr: capNum,
      dry_run: false,
    });
    setPlacing(false);
    if (isError(res)) {
      setError(res.error.message);
      return;
    }
    setPlaced(res.data as BasketTradePlaced);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg gap-0 p-0 overflow-hidden">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle className="text-lg">Trade “{basket.name}”</DialogTitle>
          <DialogDescription>
            Sized to whole shares at live prices, then placed through your broker.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 px-6 py-5 max-h-[60vh] overflow-y-auto">
          {/* Capital */}
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Capital to invest
            </span>
            <div className="flex gap-2">
              <div className="flex flex-1 items-center gap-2">
                <span className="text-sm text-muted-foreground">₹</span>
                <Input
                  type="number"
                  inputMode="numeric"
                  min={0}
                  value={capital}
                  disabled={!!placed}
                  onChange={(e) => setCapital(e.target.value)}
                  placeholder="e.g. 100000"
                  className="tabular-nums"
                />
              </div>
              <Button
                type="button"
                variant="outline"
                disabled={!capValid || previewing || !!placed}
                onClick={() => runPreview(capNum)}
                className="shrink-0"
              >
                {previewing ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  "Preview"
                )}
              </Button>
            </div>
          </label>

          {/* Preview table */}
          {preview && (
            <div className="flex flex-col divide-y rounded-lg border">
              {preview.legs.map((l) => (
                <div key={l.symbol} className="flex items-center justify-between gap-3 px-3 py-2.5">
                  <span className="font-mono text-sm font-semibold">{l.symbol}</span>
                  <span className="text-sm text-muted-foreground tabular-nums">
                    {l.quantity} × {inr(l.est_price)}
                  </span>
                  <span className="text-sm font-medium tabular-nums">{inr(l.est_cost)}</span>
                </div>
              ))}
              <div className="flex items-center justify-between px-3 py-2.5">
                <span className="text-sm text-muted-foreground">
                  {preview.legs.length} order{preview.legs.length === 1 ? "" : "s"}
                </span>
                <span className="text-sm font-semibold tabular-nums">
                  ≈ {inr(preview.est_total)}
                </span>
              </div>
            </div>
          )}

          {/* Skipped names — honest about what didn't fit */}
          {preview && preview.skipped.length > 0 && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-xs text-amber-700 dark:text-amber-300">
              Skipped: {preview.skipped.map((s) => `${s.symbol} (${s.reason})`).join(", ")}
            </div>
          )}

          {/* Placed result */}
          {placed && (
            <div className="flex items-start gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-3 py-3 text-sm">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
              <span>
                {placed.routed_to === "paper"
                  ? `Filled ${placed.count} order${placed.count === 1 ? "" : "s"} in your paper book.`
                  : `Registered ${placed.count} order${placed.count === 1 ? "" : "s"} through your broker — confirm in your broker app to place.`}
              </span>
            </div>
          )}

          {error && (
            <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t px-6 py-4">
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)} disabled={placing}>
            {placed ? "Close" : "Cancel"}
          </Button>
          {!placed && (
            <Button
              type="button"
              onClick={handleTrade}
              disabled={placing || previewing || !capValid || !preview || preview.legs.length === 0}
              className="gap-1.5"
            >
              {placing && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              {preview ? `Trade ${inr(preview.est_total)}` : "Trade"}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
