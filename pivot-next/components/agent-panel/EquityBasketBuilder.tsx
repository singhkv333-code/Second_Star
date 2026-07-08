"use client";

/**
 * EquityBasketBuilder — the minimal builder/editor for an equity (or ETF)
 * basket, shown in the Agents → Strategies tab.
 *
 * A basket is a set of securities with weights. Weighting is either:
 *   • Equal   — every name gets 100/n automatically.
 *   • Custom  — per-name weight inputs; the running total shows how close to
 *               100% you are, and the server normalises on save.
 *
 * Create (no `basket` prop) or edit (pass an existing `basket`). On save it
 * calls create/update and hands the fresh basket back via `onSaved`.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Plus, Search, X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { isError } from "@/lib/types";
import { searchCompanies, type CompanySearchResult } from "@/lib/api";
import {
  createEquityBasket,
  updateEquityBasket,
  type BasketWeighting,
  type EquityBasket,
} from "@/lib/agentsApi";

type DraftMember = { symbol: string; weight: number };

export function EquityBasketBuilder({
  open,
  onOpenChange,
  basket,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Present → edit mode; absent → create mode. */
  basket?: EquityBasket | null;
  onSaved: (b: EquityBasket) => void;
}): React.ReactElement {
  const editing = !!basket;

  const [name, setName] = useState(basket?.name ?? "");
  const [weighting, setWeighting] = useState<BasketWeighting>(
    basket?.weighting ?? "equal",
  );
  const [members, setMembers] = useState<DraftMember[]>(
    basket?.members?.map((m) => ({ symbol: m.symbol, weight: m.weight })) ?? [],
  );
  const [capital, setCapital] = useState<string>(
    basket?.capital_inr != null ? String(basket.capital_inr) : "",
  );
  const [symbolInput, setSymbolInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The Dialog keeps this component mounted across open/close, so useState
  // initializers only run once. Re-sync every time the modal opens so it always
  // reflects the basket being edited (or a clean slate for a new one) instead
  // of leaking state from the previous session.
  useEffect(() => {
    if (!open) return;
    setName(basket?.name ?? "");
    setWeighting(basket?.weighting ?? "equal");
    setMembers(
      basket?.members?.map((m) => ({ symbol: m.symbol, weight: m.weight })) ?? [],
    );
    setCapital(basket?.capital_inr != null ? String(basket.capital_inr) : "");
    setSymbolInput("");
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, basket?.id]);

  const equalWeight = members.length > 0 ? 100 / members.length : 0;
  const customTotal = useMemo(
    () => members.reduce((s, m) => s + (Number.isFinite(m.weight) ? m.weight : 0), 0),
    [members],
  );

  function addSymbol(raw: string): void {
    const sym = raw.replace(/\.NS$/i, "").trim().toUpperCase();
    if (!sym) return;
    setSymbolInput("");
    if (members.some((m) => m.symbol === sym)) return;
    setMembers((prev) => [...prev, { symbol: sym, weight: 0 }]);
    setError(null);
  }

  function removeSymbol(sym: string): void {
    setMembers((prev) => prev.filter((m) => m.symbol !== sym));
  }

  function setWeight(sym: string, w: number): void {
    setMembers((prev) =>
      prev.map((m) => (m.symbol === sym ? { ...m, weight: Math.max(0, w) } : m)),
    );
  }

  async function handleSave(): Promise<void> {
    if (saving) return;
    setError(null);
    if (!name.trim()) {
      setError("Give the basket a name.");
      return;
    }
    if (members.length === 0) {
      setError("Add at least one security.");
      return;
    }
    const capNum = capital.trim() ? Number(capital) : undefined;
    if (capNum !== undefined && (!Number.isFinite(capNum) || capNum <= 0)) {
      setError("Capital must be a positive number, or leave it blank.");
      return;
    }
    const body = {
      name: name.trim(),
      weighting,
      members: members.map((m) => ({ symbol: m.symbol, weight: m.weight })),
      capital_inr: capNum ?? null,
    };
    setSaving(true);
    const res = editing
      ? await updateEquityBasket(basket!.id, body)
      : await createEquityBasket(body);
    setSaving(false);
    if (isError(res)) {
      setError(res.error.message);
      return;
    }
    onSaved(res.data);
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg gap-0 p-0 overflow-hidden">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle className="text-lg">
            {editing ? "Edit basket" : "New equity basket"}
          </DialogTitle>
          <DialogDescription>
            A set of equities or ETFs with weights — saved to your Strategies.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 px-6 py-5 max-h-[60vh] overflow-y-auto">
          {/* Name */}
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Name
            </span>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. My IT bundle"
              maxLength={120}
            />
          </label>

          {/* Weighting toggle */}
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Weighting
            </span>
            <div className="inline-flex w-fit items-center rounded-full border p-1">
              {(["equal", "custom"] as BasketWeighting[]).map((w) => (
                <button
                  key={w}
                  type="button"
                  onClick={() => setWeighting(w)}
                  aria-pressed={weighting === w}
                  className={cn(
                    "rounded-full px-3.5 py-1 text-[12.5px] font-semibold capitalize transition-colors",
                    weighting === w
                      ? "bg-foreground text-background"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {w}
                </button>
              ))}
            </div>
          </div>

          {/* Add security — DB-backed autocomplete */}
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Securities
            </span>
            <SymbolTypeahead
              value={symbolInput}
              onChange={setSymbolInput}
              onPick={addSymbol}
              alreadyAdded={members.map((m) => m.symbol)}
            />
          </div>

          {/* Member rows */}
          {members.length === 0 ? (
            <p className="rounded-lg border border-dashed py-6 text-center text-sm text-muted-foreground">
              No securities yet — add a few above.
            </p>
          ) : (
            <div className="flex flex-col divide-y rounded-lg border">
              {members.map((m) => (
                <div key={m.symbol} className="flex items-center gap-3 px-3 py-2.5">
                  <span className="min-w-0 flex-1 truncate font-mono text-sm font-semibold">
                    {m.symbol}
                  </span>
                  {weighting === "equal" ? (
                    <span className="tabular-nums text-sm text-muted-foreground">
                      {equalWeight.toFixed(1)}%
                    </span>
                  ) : (
                    <div className="flex items-center gap-1">
                      <Input
                        type="number"
                        inputMode="decimal"
                        min={0}
                        max={100}
                        value={m.weight === 0 ? "" : m.weight}
                        onChange={(e) =>
                          setWeight(m.symbol, Number(e.target.value) || 0)
                        }
                        placeholder="0"
                        className="h-8 w-20 text-right tabular-nums"
                        aria-label={`Weight for ${m.symbol}`}
                      />
                      <span className="text-sm text-muted-foreground">%</span>
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={() => removeSymbol(m.symbol)}
                    aria-label={`Remove ${m.symbol}`}
                    className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    <X className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Custom-weight running total */}
          {weighting === "custom" && members.length > 0 && (
            <p
              className={cn(
                "text-xs",
                Math.abs(customTotal - 100) < 0.5
                  ? "text-muted-foreground"
                  : "text-amber-600 dark:text-amber-400",
              )}
            >
              Total: <span className="tabular-nums font-semibold">{customTotal.toFixed(1)}%</span>
              {Math.abs(customTotal - 100) >= 0.5 &&
                " — will be scaled to 100% on save."}
            </p>
          )}

          {/* Optional capital */}
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Capital <span className="font-normal normal-case">(optional)</span>
            </span>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">₹</span>
              <Input
                type="number"
                inputMode="numeric"
                min={0}
                value={capital}
                onChange={(e) => setCapital(e.target.value)}
                placeholder="e.g. 50000"
                className="tabular-nums"
              />
            </div>
          </label>

          {error && (
            <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </p>
          )}
        </div>

        <DialogFooterRow
          onCancel={() => onOpenChange(false)}
          onSave={handleSave}
          saving={saving}
          saveLabel={editing ? "Save changes" : "Create basket"}
        />
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// SymbolTypeahead — debounced autocomplete over the company DB
// (GET /api/companies/search). Pick a suggestion or type a raw symbol.
// ---------------------------------------------------------------------------

function SymbolTypeahead({
  value,
  onChange,
  onPick,
  alreadyAdded,
}: {
  value: string;
  onChange: (v: string) => void;
  onPick: (symbol: string) => void;
  alreadyAdded: string[];
}): React.ReactElement {
  const [results, setResults] = useState<CompanySearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const seq = useRef(0);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const q = value.trim();
    if (q.length < 1) {
      setResults([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    const mine = ++seq.current;
    const t = setTimeout(() => {
      searchCompanies(q, 8)
        .then((res) => {
          if (mine !== seq.current) return; // a newer keystroke won
          setResults(isError(res) ? [] : res.data.results);
          setActive(0);
        })
        .catch(() => mine === seq.current && setResults([]))
        .finally(() => mine === seq.current && setLoading(false));
    }, 180);
    return () => clearTimeout(t);
  }, [value]);

  const added = new Set(alreadyAdded);
  const pick = (sym: string): void => {
    onPick(sym);
    setResults([]);
    setOpen(false);
  };

  return (
    <div className="relative">
      <div className="flex items-center gap-2 rounded-md border bg-transparent px-3 focus-within:ring-2 focus-within:ring-ring">
        <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <input
          value={value}
          onChange={(e) => {
            onChange(e.target.value);
            setOpen(true);
          }}
          onFocus={() => value.trim() && setOpen(true)}
          onBlur={() => {
            blurTimer.current = setTimeout(() => setOpen(false), 120);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(a + 1, results.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              const chosen = results[active];
              pick(chosen ? chosen.symbol : value);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          placeholder="Search a company or symbol — e.g. TCS, Reliance"
          aria-label="Search securities"
          className="h-10 w-full bg-transparent py-2 font-mono text-sm uppercase outline-none placeholder:font-sans placeholder:normal-case placeholder:text-muted-foreground"
        />
        {loading && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" aria-hidden="true" />}
      </div>

      {open && value.trim().length >= 1 && (results.length > 0 || !loading) && (
        <div
          className="absolute z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border bg-popover p-1 shadow-lg"
          role="listbox"
          onMouseDown={(e) => {
            // Keep the input focused so onBlur doesn't fire before the click.
            e.preventDefault();
            if (blurTimer.current) clearTimeout(blurTimer.current);
          }}
        >
          {results.length === 0 ? (
            <div className="px-3 py-3 text-sm text-muted-foreground">
              No matches. Press Enter to add “{value.trim().toUpperCase()}” anyway.
            </div>
          ) : (
            results.map((r, i) => {
              const isAdded = added.has(r.symbol.toUpperCase());
              return (
                <button
                  key={r.symbol}
                  type="button"
                  role="option"
                  aria-selected={i === active}
                  disabled={isAdded}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => pick(r.symbol)}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left",
                    i === active && !isAdded && "bg-accent",
                    isAdded && "opacity-50",
                  )}
                >
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-[11px] font-bold text-muted-foreground">
                    {r.symbol.charAt(0)}
                  </span>
                  <span className="flex min-w-0 flex-col">
                    <span className="font-mono text-[13px] font-semibold leading-tight">
                      {r.symbol}
                    </span>
                    <span className="truncate text-[12px] leading-tight text-muted-foreground">
                      {r.name}
                      {r.sector ? ` · ${r.sector}` : ""}
                    </span>
                  </span>
                  {isAdded ? (
                    <span className="ml-auto text-[11px] font-medium text-muted-foreground">
                      Added
                    </span>
                  ) : (
                    <Plus className="ml-auto h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  )}
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

function DialogFooterRow({
  onCancel,
  onSave,
  saving,
  saveLabel,
}: {
  onCancel: () => void;
  onSave: () => void;
  saving: boolean;
  saveLabel: string;
}): React.ReactElement {
  return (
    <div className="flex items-center justify-end gap-2 border-t px-6 py-4">
      <Button type="button" variant="ghost" onClick={onCancel} disabled={saving}>
        Cancel
      </Button>
      <Button type="button" onClick={onSave} disabled={saving} className="gap-1.5">
        {saving && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
        {saveLabel}
      </Button>
    </div>
  );
}
