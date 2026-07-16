"use client";

/**
 * EquityBasketsSection — the user's saved equity / ETF baskets inside the
 * Agents → Strategies tab (the equity half; option strategies sit alongside).
 *
 * Lists baskets from GET /strategies/baskets. "New basket" hands off to chat
 * (baskets are built by describing them, not a form) — POST/PATCH still back
 * the three-dot "Edit" for structural tweaks. Delete squares off every held
 * member and hard-deletes the basket (DELETE /strategies/baskets/{id});
 * "Square off" does the same sell without deleting (POST .../close). Cards
 * mirror the Agents-tab card language (rounded-2xl, border, real values only).
 */

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Layers, MessageSquarePlus, MoreVertical, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
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
  closeEquityBasket,
  deleteEquityBasket,
  listEquityBaskets,
  type BasketCloseResult,
  type EquityBasket,
} from "@/lib/agentsApi";
import { CompanyLogo } from "@/components/CompanyLogo";
import { useCompanyLogos } from "@/hooks/useCompanyLogos";
import { EquityBasketBuilder } from "./EquityBasketBuilder";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: EquityBasket[] };

export function EquityBasketsSection({
  onSendPrompt,
}: {
  /** Seed a prompt into the chat composer and jump there — used by "New
   *  basket" and the per-card "Edit with chat" action. */
  onSendPrompt?: (prompt: string) => void;
}): React.ReactElement {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [builderOpen, setBuilderOpen] = useState(false);
  const [editing, setEditing] = useState<EquityBasket | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [closingId, setClosingId] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<EquityBasket | null>(null);
  const [confirmClose, setConfirmClose] = useState<EquityBasket | null>(null);
  const [closeResult, setCloseResult] = useState<{ id: number; result: BasketCloseResult } | null>(null);

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
    onSendPrompt?.("create a basket");
  };
  const openEdit = (b: EquityBasket): void => {
    setEditing(b);
    setBuilderOpen(true);
  };
  const editWithChat = (b: EquityBasket): void => {
    onSendPrompt?.(`Edit my "${b.name}" basket`);
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

  const handleClose = (b: EquityBasket): void => {
    setClosingId(b.id);
    setCloseResult(null);
    closeEquityBasket(b.id)
      .then((res) => {
        if (isError(res)) return;
        setCloseResult({ id: b.id, result: res.data });
      })
      .catch(() => {})
      .finally(() => setClosingId((cur) => (cur === b.id ? null : cur)));
  };

  return (
    <div className="flex flex-col gap-4" data-testid="equity-baskets-section">
      <div className="flex items-center justify-end gap-3">
        <Button size="sm" onClick={openCreate} className="gap-1.5" data-testid="new-basket-btn">
          <Plus className="h-4 w-4" aria-hidden="true" />
          New basket
        </Button>
      </div>

      {state.kind === "loading" && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-48 w-full rounded-2xl" />
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
            Start a chat — describe what you want and pick the names.
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
                isClosing={closingId === b.id}
                closeResult={closeResult?.id === b.id ? closeResult.result : null}
                onEdit={() => openEdit(b)}
                onDelete={() => setConfirmDelete(b)}
                onEditWithChat={() => editWithChat(b)}
                onSquareOff={() => setConfirmClose(b)}
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

      <AlertDialog
        open={confirmDelete !== null}
        onOpenChange={(o) => !o && setConfirmDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{confirmDelete?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              This squares off (sells) every position currently held in this
              basket, then deletes the strategy for good. It can&apos;t be undone.
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

      <AlertDialog
        open={confirmClose !== null}
        onOpenChange={(o) => !o && setConfirmClose(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Square off “{confirmClose?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Sells every position currently held in this basket at market.
              The basket itself stays — you can trade it again later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (confirmClose) handleClose(confirmClose);
                setConfirmClose(null);
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Square off
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

function squareOffSummary(result: BasketCloseResult): string {
  if (result.count === 0) return "Nothing held — no positions to square off.";
  const names = result.registered.map((r) => r.symbol).join(", ");
  return `Sold ${names}.`;
}

function EquityBasketCard({
  basket,
  isDeleting,
  isClosing,
  closeResult,
  onEdit,
  onDelete,
  onEditWithChat,
  onSquareOff,
}: {
  basket: EquityBasket;
  isDeleting: boolean;
  isClosing: boolean;
  closeResult: BasketCloseResult | null;
  onEdit: () => void;
  onDelete: () => void;
  onEditWithChat: () => void;
  onSquareOff: () => void;
}): React.ReactElement {
  const members = basket.members ?? [];
  const logos = useCompanyLogos(members.map((m) => m.symbol));

  return (
    <div
      data-testid={`basket-card-${basket.id}`}
      className={cn(
        "group flex h-full flex-col gap-3 rounded-2xl border border-border/50 bg-card px-4 py-4",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_8px_20px_-12px_rgba(15,23,42,0.08)]",
        "transition-colors hover:border-border",
        (isDeleting || isClosing) && "opacity-70 pointer-events-none",
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
        <h3 className="m-0 line-clamp-2 text-[17px] leading-[1.2] font-semibold tracking-tight text-foreground">
          {basket.name}
        </h3>
        <span className="text-[12px] text-muted-foreground">
          {members.length} name{members.length === 1 ? "" : "s"}
          {basket.capital_inr != null && <> · {fmtInrCompact(basket.capital_inr)}</>}
        </span>
      </div>

      {/* Holdings — vertical list, logo + real name + weight. Height caps at
          ~5 rows so a big basket doesn't stretch the card (or its grid row);
          past that it scrolls WITHIN the card, scrollbar hidden. */}
      <div className="mt-auto flex flex-col gap-1.5 border-t border-border/40 pt-2.5">
        <span className="text-[10.5px] uppercase tracking-wide text-muted-foreground/70">
          Holdings
        </span>
        <div className="quartr-no-scrollbar flex max-h-[132px] flex-col gap-1 overflow-y-auto">
          {members.length === 0 ? (
            <span className="text-[12px] text-muted-foreground/70">No names</span>
          ) : (
            members.map((m) => (
              <div
                key={m.symbol}
                className="flex items-center gap-1.5"
                title={`${m.symbol} · ${m.weight.toFixed(0)}%`}
              >
                <CompanyLogo
                  logoUrl={logos[m.symbol.toUpperCase()]}
                  name={m.name ?? m.symbol}
                  symbol={m.symbol}
                  size={20}
                />
                <span className="flex-1 truncate text-[12px] font-medium text-foreground">
                  {m.name ?? m.symbol}
                </span>
                <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                  {m.weight.toFixed(0)}%
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      {closeResult && (
        <p className="text-[11px] text-muted-foreground" data-testid={`basket-squareoff-result-${basket.id}`}>
          {squareOffSummary(closeResult)}
        </p>
      )}

      {/* Two stacked actions — editing goes through chat; squaring off sells
          everything currently held without touching the saved basket. */}
      <div className="flex flex-col gap-2">
        <Button
          size="sm"
          onClick={onEditWithChat}
          className="w-full gap-1.5"
          data-testid={`basket-edit-chat-${basket.id}`}
        >
          <MessageSquarePlus className="h-3.5 w-3.5" aria-hidden="true" />
          Edit with chat
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={isClosing}
          onClick={onSquareOff}
          className="w-full"
          data-testid={`basket-squareoff-${basket.id}`}
        >
          {isClosing ? "Squaring off…" : "Square off"}
        </Button>
      </div>
    </div>
  );
}
