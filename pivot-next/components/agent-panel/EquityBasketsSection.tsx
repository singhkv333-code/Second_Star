"use client";

/**
 * EquityBasketsSection — the user's saved equity / ETF baskets inside the
 * Agents → Strategies tab (the equity half; option strategies sit alongside).
 *
 * Lists baskets from GET /strategies/baskets, opens the EquityBasketBuilder to
 * create or edit, and soft-deletes via DELETE /strategies/baskets/{id}. Cards
 * mirror the Agents-tab card language (rounded-2xl, border, real values only).
 */

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Layers, LineChart, MoreVertical, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { isError } from "@/lib/types";
import {
  deleteEquityBasket,
  listEquityBaskets,
  type EquityBasket,
} from "@/lib/agentsApi";
import { EquityBasketBuilder } from "./EquityBasketBuilder";
import { BasketTradeModal } from "./BasketTradeModal";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: EquityBasket[] };

export function EquityBasketsSection(): React.ReactElement {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [builderOpen, setBuilderOpen] = useState(false);
  const [editing, setEditing] = useState<EquityBasket | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<EquityBasket | null>(null);
  const [tradingBasket, setTradingBasket] = useState<EquityBasket | null>(null);

  const load = useCallback((): void => {
    setState({ kind: "loading" });
    listEquityBaskets()
      .then((res) => {
        if (isError(res)) {
          setState({ kind: "error", message: res.error.message });
          return;
        }
        setState({ kind: "ok", items: res.data.baskets });
      })
      .catch((err: unknown) => {
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Network error",
        });
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = (): void => {
    setEditing(null);
    setBuilderOpen(true);
  };
  const openEdit = (b: EquityBasket): void => {
    setEditing(b);
    setBuilderOpen(true);
  };

  const handleSaved = (saved: EquityBasket): void => {
    setState((prev) => {
      if (prev.kind !== "ok") return { kind: "ok", items: [saved] };
      const exists = prev.items.some((b) => b.id === saved.id);
      return {
        kind: "ok",
        items: exists
          ? prev.items.map((b) => (b.id === saved.id ? saved : b))
          : [saved, ...prev.items],
      };
    });
  };

  const handleDelete = (b: EquityBasket): void => {
    setDeletingId(b.id);
    deleteEquityBasket(b.id)
      .then((res) => {
        if (isError(res)) return;
        setState((prev) =>
          prev.kind === "ok"
            ? { kind: "ok", items: prev.items.filter((x) => x.id !== b.id) }
            : prev,
        );
      })
      .catch(() => {})
      .finally(() => setDeletingId((cur) => (cur === b.id ? null : cur)));
  };

  return (
    <div className="flex flex-col gap-4" data-testid="equity-baskets-section">
      <div className="flex items-center justify-between gap-3">
        <h2
          className="q-serif m-0"
          style={{ fontSize: 16, letterSpacing: "-0.02em", color: "var(--text-primary)" }}
        >
          Equity baskets
        </h2>
        <Button size="sm" onClick={openCreate} className="gap-1.5" data-testid="new-basket-btn">
          <Plus className="h-4 w-4" aria-hidden="true" />
          New basket
        </Button>
      </div>

      {state.kind === "loading" && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-56 w-full rounded-2xl" />
          ))}
        </div>
      )}

      {state.kind === "error" && (
        <div
          role="alert"
          className="flex flex-col items-center justify-center py-10 text-center"
          data-testid="baskets-error"
        >
          <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
          <p className="text-sm font-medium">Couldn&apos;t load baskets</p>
          <p className="mt-1 text-xs text-muted-foreground">{state.message}</p>
          <Button variant="outline" size="sm" className="mt-4" onClick={load}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
            Retry
          </Button>
        </div>
      )}

      {state.kind === "ok" && state.items.length === 0 && (
        <button
          type="button"
          onClick={openCreate}
          className="flex flex-col items-center justify-center gap-1 rounded-2xl border border-dashed py-12 text-center transition-colors hover:border-border hover:bg-muted/40"
          data-testid="baskets-empty"
        >
          <Layers className="mb-2 h-8 w-8 text-muted-foreground" aria-hidden="true" />
          <span className="text-sm font-medium">No equity baskets yet</span>
          <span className="text-xs text-muted-foreground">
            Build one — pick securities and set weights.
          </span>
        </button>
      )}

      {state.kind === "ok" && state.items.length > 0 && (
        <div
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
          data-testid="baskets-list"
          role="list"
        >
          {state.items.map((b) => (
            <div key={b.id} role="listitem" className="h-full">
              <EquityBasketCard
                basket={b}
                isDeleting={deletingId === b.id}
                onEdit={() => openEdit(b)}
                onDelete={() => setConfirmDelete(b)}
                onTrade={() => setTradingBasket(b)}
              />
            </div>
          ))}
        </div>
      )}

      <EquityBasketBuilder
        open={builderOpen}
        onOpenChange={setBuilderOpen}
        basket={editing}
        onSaved={handleSaved}
      />

      {tradingBasket && (
        <BasketTradeModal
          open={tradingBasket !== null}
          onOpenChange={(o) => !o && setTradingBasket(null)}
          basket={tradingBasket}
        />
      )}

      <AlertDialog
        open={confirmDelete !== null}
        onOpenChange={(o) => !o && setConfirmDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{confirmDelete?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes the basket from your Strategies. It can&apos;t be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (confirmDelete) handleDelete(confirmDelete);
                setConfirmDelete(null);
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete basket
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function fmtInrCompact(amount: number | null): string {
  if (amount === null || amount === undefined) return "—";
  return `₹${Math.round(amount).toLocaleString("en-IN")}`;
}

function EquityBasketCard({
  basket,
  isDeleting,
  onEdit,
  onDelete,
  onTrade,
}: {
  basket: EquityBasket;
  isDeleting: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onTrade: () => void;
}): React.ReactElement {
  const members = basket.members ?? [];
  return (
    <div
      data-testid={`basket-card-${basket.id}`}
      className={cn(
        "group flex h-full flex-col gap-4 rounded-2xl border border-border/50 bg-card px-5 py-5",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_8px_20px_-12px_rgba(15,23,42,0.08)]",
        "transition-colors hover:border-border",
        isDeleting && "opacity-70 pointer-events-none",
      )}
    >
      {/* Header: type chip + weighting + kebab */}
      <div className="flex items-center justify-between gap-3">
        <span className="inline-flex items-center rounded-md bg-emerald-100 px-2.5 py-0.5 text-[11px] font-medium tracking-tight text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300">
          Equity basket
        </span>
        <div className="flex items-center gap-1.5">
          <span className="inline-flex items-center rounded-full bg-muted px-2.5 py-1 text-[11px] font-medium capitalize text-muted-foreground">
            {basket.weighting}-weight
          </span>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label={`Basket ${basket.name} actions`}
                disabled={isDeleting}
                className={cn(
                  "inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground",
                  "transition-colors hover:bg-muted hover:text-foreground",
                  "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isDeleting && "opacity-50",
                )}
              >
                <MoreVertical className="h-4 w-4" aria-hidden="true" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem className="gap-2" onSelect={() => onEdit()}>
                <Pencil className="h-4 w-4" aria-hidden="true" />
                Edit
              </DropdownMenuItem>
              <DropdownMenuItem
                className="gap-2 text-destructive focus:text-destructive"
                onSelect={(e) => {
                  e.preventDefault();
                  onDelete();
                }}
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* Title */}
      <div className="flex flex-col gap-0.5">
        <h3 className="m-0 line-clamp-2 text-[20px] leading-[1.2] font-semibold tracking-tight text-foreground">
          {basket.name}
        </h3>
        <span className="text-[12px] text-muted-foreground">
          {members.length} name{members.length === 1 ? "" : "s"}
          {basket.capital_inr != null && <> · {fmtInrCompact(basket.capital_inr)}</>}
        </span>
      </div>

      {/* Members as weighted chips */}
      <div className="mt-auto flex flex-col gap-2 border-t border-border/40 pt-3">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground/70">
          Holdings
        </span>
        <div className="flex flex-wrap gap-1.5">
          {members.length === 0 ? (
            <span className="text-[12px] text-muted-foreground/70">No names</span>
          ) : (
            members.map((m) => (
              <span
                key={m.symbol}
                className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-[11px] font-medium tabular-nums text-foreground/80"
                title={`${m.symbol} · ${m.weight}%`}
              >
                <span className="font-mono">{m.symbol}</span>
                <span className="text-muted-foreground">{m.weight.toFixed(0)}%</span>
              </span>
            ))
          )}
        </div>
      </div>

      {/* Trade — size to shares at live prices and place through the broker. */}
      <button
        type="button"
        onClick={onTrade}
        data-testid={`basket-trade-${basket.id}`}
        className={cn(
          "inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-[13px] font-semibold",
          "bg-foreground text-background transition-opacity hover:opacity-90",
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
        )}
      >
        <LineChart className="h-3.5 w-3.5" aria-hidden="true" />
        Trade
      </button>
    </div>
  );
}
