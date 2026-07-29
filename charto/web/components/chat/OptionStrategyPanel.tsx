"use client";

/**
 * OptionStrategyPanel — full interactive F&O strategy builder, opened from the
 * compact OptionStrategyCard. A right-anchored sidebar (same shell as the
 * Agent editor / IPO drawers) where the user can:
 *   - add / remove legs, flip side (BUY/SELL) and type (CE/PE)
 *   - pick strikes from the live chain, change expiry, change lots
 *   - watch payoff / Greeks / P&L recompute live (POST /option-strategies/compute)
 *   - register the strategy (paper fills / live intent — register-not-execute)
 *
 * Tabs: Builder · Payoff · Greeks · P&L Table. The intent is "simple and
 * complex at once" — a newcomer reads the Payoff tab's plain stats, a pro
 * lives in Builder + Greeks.
 *
 * Shell precedent: IpoDetailPanel.tsx / AgentPanel.tsx (.agent-panel-shell).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  CheckCircle2,
  Info,
  Loader2,
  Plus,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useTradingMode } from "@/lib/trading-mode";
import {
  computeOptionStrategy,
  getOptionChainSlice,
  registerOptionStrategy,
  withdrawOptionStrategy,
} from "@/lib/api";
import { isError } from "@/lib/types";
import type {
  CritiqueFlag,
  OptionChainSlice,
  OptionStrategyPayload,
  OptionStrategyRegisterResponse,
  StrategyLeg,
} from "@/lib/types";
import {
  RiskChip,
  fmtInr,
  fmtInrCompact,
  humanizeTemplate,
} from "@/components/chat/OptionStrategyCard";
import { PayoffChart } from "@/components/chat/option-payoff-chart";
import { ContentOverlay } from "@/components/chat/ContentOverlay";

type Tab = "payoff" | "table" | "greeks";

type RegState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "registered"; strategy: NonNullable<OptionStrategyRegisterResponse["strategy"]> }
  | { kind: "withdrawing" }
  | { kind: "withdrawn" }
  | { kind: "error"; message: string };

export type OptionStrategyPanelProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  payload: OptionStrategyPayload;
};

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/** type/side/strike triplet — the structural identity of the basket. */
function specKeyOf(legs: StrategyLeg[], qtyLots: number, expiry: string): string {
  return JSON.stringify({
    e: expiry,
    q: qtyLots,
    l: legs.map((l) => [l.option_type, l.side, l.strike]),
  });
}

/** Strikes on this chain where the given side is quotable. */
function strikesFor(chain: OptionChainSlice, type: "CE" | "PE"): number[] {
  return chain.rows
    .filter((r) => (type === "CE" ? r.ce : r.pe) != null)
    .map((r) => r.strike);
}

function nearest(target: number, options: number[]): number | null {
  if (options.length === 0) return null;
  return options.reduce((best, s) =>
    Math.abs(s - target) < Math.abs(best - target) ? s : best,
  );
}

/** Snap each leg's strike to the nearest quotable strike on this chain. */
function remapLegs(legs: StrategyLeg[], chain: OptionChainSlice): StrategyLeg[] {
  return legs.map((l) => {
    const avail = strikesFor(chain, l.option_type);
    if (avail.includes(l.strike)) return l;
    const snapped = nearest(l.strike, avail);
    return snapped == null ? l : { ...l, strike: snapped };
  });
}

/** Linear interpolation of the payoff curve at an arbitrary underlying price. */
function payoffAt(payoff: { s: number; pnl: number }[], s: number): number | null {
  if (payoff.length === 0) return null;
  if (s <= payoff[0]!.s) return payoff[0]!.pnl;
  if (s >= payoff[payoff.length - 1]!.s) return payoff[payoff.length - 1]!.pnl;
  for (let i = 1; i < payoff.length; i++) {
    const a = payoff[i - 1]!;
    const b = payoff[i]!;
    if (s <= b.s) {
      const t = (s - a.s) / (b.s - a.s || 1);
      return a.pnl + t * (b.pnl - a.pnl);
    }
  }
  return payoff[payoff.length - 1]!.pnl;
}

// ---------------------------------------------------------------------------
// Small UI atoms
// ---------------------------------------------------------------------------

function StatBox({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string;
  tone?: "loss" | "profit" | "neutral";
  hint?: string;
}): React.ReactElement {
  return (
    <div className="px-1 py-0.5">
      <p className="text-[9.5px] uppercase tracking-wider text-muted-foreground/70">{label}</p>
      <p
        className={cn(
          "mt-0.5 text-[15px] font-semibold tabular-nums",
          tone === "loss" && "text-rose-600 dark:text-rose-400",
          tone === "profit" && "text-emerald-600 dark:text-emerald-400",
          (!tone || tone === "neutral") && "text-foreground",
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-0.5 text-[10px] text-muted-foreground/70">{hint}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

export function OptionStrategyPanel({
  open,
  onOpenChange,
  payload,
}: OptionStrategyPanelProps): React.ReactElement | null {
  const { locked } = payload;

  const tradingMode = useTradingMode();
  const book: "paper" | "live" = tradingMode === "paper" ? "paper" : "live";

  // ── Editable structure ──
  const [legs, setLegs] = useState<StrategyLeg[]>(payload.editable.legs);
  const [qtyLots, setQtyLots] = useState<number>(payload.editable.qty_lots);
  const [expiry, setExpiry] = useState<string>(locked.expiry);

  // ── Live-computed view (starts from the card payload) ──
  const [current, setCurrent] = useState<OptionStrategyPayload>(payload);
  const [computing, setComputing] = useState(false);
  const [computeError, setComputeError] = useState<string | null>(null);

  // ── Chain for the strike/expiry pickers ──
  const [chain, setChain] = useState<OptionChainSlice | null>(null);
  const [chainLoading, setChainLoading] = useState(false);

  const [tab, setTab] = useState<Tab>("payoff");
  const [tInterval, setTInterval] = useState<number>(50);
  const [showPct, setShowPct] = useState(false);
  const [disclosureChecked, setDisclosureChecked] = useState(false);
  const [regState, setRegState] = useState<RegState>({ kind: "idle" });
  const [target, setTarget] = useState<number>(Math.round(locked.forward));
  // Phone gets a shorter payoff chart so the slider + stats below stay within
  // a thumb's reach without a long scroll (it also spans wider on phone).
  const [isPhone, setIsPhone] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 639.98px)");
    const sync = (): void => setIsPhone(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const lastSpec = useRef<string>(specKeyOf(payload.editable.legs, payload.editable.qty_lots, locked.expiry));

  const isStructurallyOriginal = useMemo(() => {
    const a = payload.editable.legs;
    if (a.length !== legs.length) return false;
    return legs.every(
      (l, i) =>
        a[i] != null &&
        a[i]!.option_type === l.option_type &&
        a[i]!.side === l.side &&
        a[i]!.strike === l.strike,
    );
  }, [legs, payload.editable.legs]);

  const liveTemplate = isStructurallyOriginal ? payload.editable.template : "custom";
  const specKey = specKeyOf(legs, qtyLots, expiry);

  // ── Esc to close ──
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onOpenChange(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  // ── Fetch the chain for the active expiry; remap leg strikes onto it ──
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setChainLoading(true);
    void getOptionChainSlice({ underlying: locked.underlying, expiry, width: 14 }).then((res) => {
      if (cancelled) return;
      setChainLoading(false);
      if (isError(res) || !res.data.success || !res.data.chain) {
        setChain(null);
        return;
      }
      const ch = res.data.chain;
      setChain(ch);
      setLegs((prev) => remapLegs(prev, ch));
    });
    return () => {
      cancelled = true;
    };
  }, [open, locked.underlying, expiry]);

  // ── Debounced live recompute (only once the chain matches the expiry so
  //    leg strikes are guaranteed valid) ──
  useEffect(() => {
    if (!open) return;
    if (!chain || chain.expiry !== expiry) return;
    if (specKey === lastSpec.current) return;
    let cancelled = false;
    setComputing(true);
    const t = setTimeout(async () => {
      const res = await computeOptionStrategy({
        underlying: locked.underlying,
        expiry,
        template: liveTemplate,
        qty_lots: qtyLots,
        legs: legs.map((l) => ({
          option_type: l.option_type,
          side: l.side,
          strike: l.strike,
        })),
      });
      if (cancelled) return;
      setComputing(false);
      if (isError(res)) {
        setComputeError(res.error.message ?? "Recompute failed — try again.");
        return;
      }
      if (!res.data.success || !res.data.payload) {
        setComputeError(res.data.error ?? "Couldn't build this structure on the live chain.");
        return;
      }
      setComputeError(null);
      lastSpec.current = specKey;
      setCurrent(res.data.payload);
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [open, chain, expiry, specKey, qtyLots, legs, liveTemplate, locked.underlying]);

  if (!open) return null;

  const computed = current.computed;
  const validation = current.validation;
  const critique = current.critique;
  const isRegistered = regState.kind === "registered";
  const isWithdrawn = regState.kind === "withdrawn";
  const isBusy = regState.kind === "saving" || regState.kind === "withdrawing";
  const locked2 = current.locked;

  // ── Leg editing ──
  function updateLeg(idx: number, patch: Partial<StrategyLeg>): void {
    setLegs((prev) => prev.map((l, i) => (i === idx ? { ...l, ...patch } : l)));
  }
  function removeLeg(idx: number): void {
    setLegs((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== idx)));
  }
  function addLeg(): void {
    const atm = chain?.atm_strike ?? legs[0]?.strike ?? locked.forward;
    const avail = chain ? strikesFor(chain, "CE") : [];
    const strike = chain ? nearest(atm, avail) ?? atm : atm;
    setLegs((prev) => [...prev, { option_type: "CE", side: "BUY", strike }]);
  }

  // ── Register / withdraw ──
  const blockReasons: string[] = [];
  if (computeError) blockReasons.push(computeError);
  if (computing) blockReasons.push("Recomputing…");
  if (!validation.lot_multiple_ok) blockReasons.push("Lot count is not a valid multiple.");
  if (validation.mcx_execution_blocked) blockReasons.push("This structure cannot be registered right now.");
  if (validation.requires_disclosure && !disclosureChecked)
    blockReasons.push("Acknowledge the disclosure first.");
  const canRegister = !isBusy && !isRegistered && !isWithdrawn && blockReasons.length === 0;

  async function handleRegister(): Promise<void> {
    if (!canRegister) return;
    setRegState({ kind: "saving" });
    const result = await registerOptionStrategy({
      underlying: locked.underlying,
      expiry,
      template: liveTemplate,
      book,
      qty_lots: qtyLots,
      legs: legs.map((l) => ({ option_type: l.option_type, side: l.side, strike: l.strike })),
      acknowledge_disclosure: disclosureChecked,
      conversation_id: payload.conversation_id ?? undefined,
    });
    if (isError(result)) {
      setRegState({ kind: "error", message: result.error.message ?? "Registration failed — try again." });
      return;
    }
    if (!result.data.success || !result.data.strategy) {
      setRegState({ kind: "error", message: result.data.error ?? "Registration failed — try again." });
      return;
    }
    setRegState({ kind: "registered", strategy: result.data.strategy });
  }

  async function handleWithdraw(): Promise<void> {
    if (regState.kind !== "registered") return;
    const id = regState.strategy.id;
    setRegState({ kind: "withdrawing" });
    const result = await withdrawOptionStrategy(id);
    if (isError(result)) {
      setRegState({ kind: "error", message: result.error.message ?? "Withdrawal failed — try again." });
      return;
    }
    setRegState({ kind: "withdrawn" });
  }

  // ── Payoff target ──
  const payoffData = computed.payoff;
  const sMin = payoffData.length > 0 ? payoffData[0]!.s : locked.forward * 0.9;
  const sMax = payoffData.length > 0 ? payoffData[payoffData.length - 1]!.s : locked.forward * 1.1;
  const projected = payoffAt(payoffData, target);

  const TABS: { id: Tab; label: string }[] = [
    { id: "payoff", label: "Payoff Graph" },
    { id: "table", label: "P&L Table" },
    { id: "greeks", label: "Greeks" },
  ];

  const title = humanizeTemplate(liveTemplate === "custom" ? "Custom strategy" : liveTemplate);

  return (
    <ContentOverlay open={open} onClose={() => onOpenChange(false)} label="Option strategy builder">
      <div className="flex h-full w-full flex-col" data-testid="option-strategy-panel">
        {/* Top bar */}
        <div className="flex shrink-0 items-center justify-between gap-3 px-5 py-3 lg:px-7">
          <div className="flex min-w-0 items-center gap-3">
            <span className="inline-flex items-center rounded-md bg-orange-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-orange-700 dark:bg-orange-500/15 dark:text-orange-300">
              F&amp;O
            </span>
            <h2 className="truncate text-[17px] font-semibold tracking-tight text-foreground">{title}</h2>
            <RiskChip verdict={critique.verdict} />
            {computing && (
              <span className="inline-flex items-center gap-1 text-[10.5px] text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" /> recomputing
              </span>
            )}
          </div>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close builder"
            onClick={() => onOpenChange(false)}
            className="rounded-full"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>

        {/* Decision quad strip */}
        <div className="grid shrink-0 grid-cols-2 gap-2 border-b border-border/40 px-5 py-3 sm:grid-cols-3 lg:grid-cols-6 lg:px-7">
          <StatBox
            label="Max loss"
            tone="loss"
            value={computed.max_loss === null ? "Unlimited" : fmtInrCompact(computed.max_loss)}
          />
          <StatBox
            label="Max profit"
            tone="profit"
            value={computed.max_profit === null ? "Unlimited" : fmtInrCompact(computed.max_profit)}
          />
          <StatBox label="POP" value={computed.pop === null ? "—" : `${(computed.pop * 100).toFixed(0)}%`} />
          <StatBox
            label={computed.net_premium <= 0 ? "Net debit" : "Net credit"}
            value={fmtInrCompact(Math.abs(computed.net_premium))}
          />
          <StatBox label="Capital" value={fmtInrCompact(computed.capital_required)} />
          <StatBox
            label="Breakeven"
            value={
              computed.breakevens.length > 0
                ? computed.breakevens.map((b) => b.toLocaleString("en-IN")).join(", ")
                : "—"
            }
          />
        </div>

        {/* Two-column workspace: builder (left) · analytics (right).
            On phone the two panes collapse into ONE natural scroll (the
            workspace itself scrolls) so the full-height payoff chart is
            reachable; on lg+ each pane scrolls independently. */}
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
          {/* LEFT — builder + register */}
          <div className="flex shrink-0 flex-col border-b border-border/40 lg:min-h-0 lg:w-[480px] lg:border-b-0 lg:border-r">
            <div className="px-5 py-4 lg:min-h-0 lg:flex-1 lg:overflow-y-auto lg:px-6">
              <BuilderTab
                legs={legs}
                serverLegs={current.editable.legs}
                chain={chain}
                chainLoading={chainLoading}
                qtyLots={qtyLots}
                expiry={expiry}
                lotSize={locked.lot_size}
                disabled={isBusy || isRegistered}
                onUpdateLeg={updateLeg}
                onRemoveLeg={removeLeg}
                onAddLeg={addLeg}
                onQtyChange={setQtyLots}
                onExpiryChange={setExpiry}
                minLots={validation.min_lots}
                maxLots={validation.max_lots}
              />
            </div>

            {/* Register footer (sticky bottom of the left column) */}
            <div className="shrink-0 border-t border-border/40 px-5 py-3 lg:px-6">
              {isRegistered ? (
                <div className="flex flex-col gap-2" data-testid="option-strategy-registered">
                  <div className="flex items-center gap-2">
                    <CheckCircle2 className="h-5 w-5 text-emerald-500" strokeWidth={2.25} aria-hidden="true" />
                    <p className="text-[12.5px] text-foreground">
                      Registered · {regState.strategy.qty_lots} lots · {regState.strategy.book} ·{" "}
                      <span className="capitalize">{regState.strategy.status}</span>
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleWithdraw()}
                    disabled={isBusy}
                    data-testid="option-strategy-withdraw-button"
                    className={cn(
                      "inline-flex items-center gap-1.5 self-start rounded-md border border-border/60 px-3 py-1.5 text-[11.5px] font-medium text-muted-foreground",
                      "transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60",
                    )}
                  >
                    <Undo2 className="h-3 w-3" aria-hidden="true" />
                    Withdraw
                  </button>
                </div>
              ) : isWithdrawn ? (
                <p className="text-[12.5px] text-muted-foreground">Strategy withdrawn.</p>
              ) : (
                <div className="flex flex-col gap-2.5">
                  {validation.requires_disclosure && (
                    <label className="flex cursor-pointer items-start gap-2">
                      <input
                        type="checkbox"
                        checked={disclosureChecked}
                        onChange={(e) => setDisclosureChecked(e.target.checked)}
                        disabled={isBusy}
                        className="mt-0.5 h-3.5 w-3.5 cursor-pointer rounded border-border/60"
                      />
                      <span className="text-[11px] leading-snug text-muted-foreground">
                        I understand 9 of 10 individual F&amp;O traders lose money (SEBI).{" "}
                        <span className="text-foreground">{locked.disclosure}</span>
                      </span>
                    </label>
                  )}
                  {blockReasons.length > 0 && !computing && (
                    <div role="alert" className="flex items-start gap-1.5 px-1 text-[11.5px] text-destructive">
                      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                      <span>{blockReasons[0]}</span>
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={() => void handleRegister()}
                    disabled={!canRegister}
                    data-testid="option-strategy-register-button"
                    className={cn(
                      "inline-flex h-8 w-full items-center justify-center gap-2 rounded-full bg-primary text-[12px] font-medium tracking-tight text-primary-foreground transition-all",
                      "hover:bg-primary/90 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60",
                    )}
                  >
                    {regState.kind === "saving" ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                        <span>Registering…</span>
                      </>
                    ) : (
                      <>
                        <span>Register {book === "paper" ? "paper" : "live"} strategy</span>
                        <ArrowUpRight className="h-4 w-4 shrink-0" aria-hidden="true" />
                      </>
                    )}
                  </button>
                  {regState.kind === "error" && (
                    <p
                      role="alert"
                      data-testid="option-strategy-error"
                      className="rounded-lg bg-destructive/10 px-3 py-2 text-[11.5px] text-destructive"
                    >
                      {regState.message}
                    </p>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* RIGHT — analytics tabs */}
          <div className="flex shrink-0 flex-col lg:min-h-0 lg:flex-1">
            <div className="flex shrink-0 gap-6 border-b border-border/40 px-5 lg:px-7">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTab(t.id)}
                  className={cn(
                    "relative px-1 py-2.5 text-[13px] font-medium transition-colors",
                    tab === t.id ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                  )}
                  aria-current={tab === t.id}
                >
                  {t.label}
                  {tab === t.id && (
                    <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-primary" />
                  )}
                </button>
              ))}
            </div>

            <div className="px-3 py-4 sm:px-5 lg:min-h-0 lg:flex-1 lg:overflow-y-auto lg:px-7">
            {tab === "payoff" && (
              <div className="flex flex-col gap-4">
                <div className="-mx-3 sm:mx-0">
                  <PayoffChart
                    data={payoffData}
                    now={computed.payoff_now}
                    breakevens={computed.breakevens}
                    forward={locked2.forward}
                    target={target}
                    sd={chain?.expected_move?.abs ?? null}
                    chain={chain}
                    height={isPhone ? 268 : 360}
                  />
                  <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-[10px] text-muted-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="h-0.5 w-4 rounded bg-[#2563eb]" /> Today (T+0)
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="h-0.5 w-4 rounded bg-emerald-500" /> At expiry
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="h-2 w-2 rounded-sm bg-emerald-500/30" /> Call OI
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="h-2 w-2 rounded-sm bg-rose-500/30" /> Put OI
                    </span>
                  </div>
                </div>

                {/* Target slider */}
                <div className="px-1 py-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] uppercase tracking-wider text-muted-foreground/70">
                      Target on expiry
                    </span>
                    <span className="text-[13px] font-semibold tabular-nums text-foreground">
                      {target.toLocaleString("en-IN")}
                    </span>
                  </div>
                  <input
                    type="range"
                    min={Math.floor(sMin)}
                    max={Math.ceil(sMax)}
                    step={1}
                    value={target}
                    onChange={(e) => setTarget(Number(e.target.value))}
                    className="payoff-slider mt-2 w-full"
                    aria-label="Target underlying price"
                  />
                  <div className="mt-2 flex items-center justify-between">
                    <span className="text-[11px] text-muted-foreground">Projected P&amp;L</span>
                    <span
                      className={cn(
                        "text-[14px] font-semibold tabular-nums",
                        (projected ?? 0) >= 0
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-rose-600 dark:text-rose-400",
                      )}
                    >
                      {projected == null ? "—" : fmtInr(projected)}
                    </span>
                  </div>
                </div>

                {/* Stat grid — capital/breakeven live in the top strip */}
                <div className="grid grid-cols-2 gap-2">
                  <StatBox
                    label="Margin estimate"
                    value={fmtInr(computed.margin_estimate)}
                    hint={computed.margin_note || undefined}
                  />
                  <StatBox
                    label="Lot size · lots"
                    value={`${locked.lot_size} × ${qtyLots} = ${locked.lot_size * qtyLots}`}
                  />
                </div>

                <CritiqueBlock flags={critique.flags} summary={critique.summary} />
              </div>
            )}

            {tab === "table" && (
              <div className="flex flex-col gap-3">
                {/* Table controls */}
                <div className="flex flex-wrap items-center justify-end gap-3">
                  <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    Target interval
                    <select
                      value={tInterval}
                      onChange={(e) => setTInterval(Number(e.target.value))}
                      className="rounded-md border border-border/60 bg-background px-1.5 py-0.5 text-[11.5px] tabular-nums text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                      aria-label="Target interval"
                    >
                      {[25, 50, 100, 250].map((n) => (
                        <option key={n} value={n}>
                          {n}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    onClick={() => setShowPct((v) => !v)}
                    aria-pressed={showPct}
                    className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground"
                  >
                    Show %
                    <span
                      className={cn(
                        "inline-flex h-4 w-7 items-center rounded-full p-0.5 transition-colors",
                        showPct ? "bg-primary" : "bg-muted-foreground/30",
                      )}
                      aria-hidden="true"
                    >
                      <span
                        className={cn(
                          "h-3 w-3 rounded-full bg-background transition-transform",
                          showPct && "translate-x-3",
                        )}
                      />
                    </span>
                  </button>
                </div>
                <PayoffTable
                  payoff={payoffData}
                  payoffNow={computed.payoff_now ?? []}
                  forward={locked2.forward}
                  capital={computed.capital_required}
                  interval={tInterval}
                  showPct={showPct}
                  expiry={expiry}
                />
              </div>
            )}

            {tab === "greeks" && (
              <div className="flex flex-col gap-4">
                <div className="grid grid-cols-4 gap-2">
                  {(["delta", "gamma", "theta", "vega"] as const).map((g) => (
                    <StatBox key={g} label={`Net ${g}`} value={computed.net_greeks[g].toFixed(3)} />
                  ))}
                </div>
                <div className="overflow-x-auto rounded-[var(--radius-md)] border border-border/40">
                  <table className="w-full border-collapse">
                    <thead>
                      <tr
                        className="border-b-[1.5px] border-border/70"
                        style={{ background: "var(--bg-secondary)" }}
                      >
                        {(["Leg", "Side", "Mid", "IV", "Delta"] as const).map((h, hi) => (
                          <th
                            key={h}
                            className={cn(
                              "px-4 py-3 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground/80",
                              hi === 0 ? "text-left" : "text-right",
                            )}
                          >
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {current.editable.legs.map((l, i) => (
                        <tr
                          key={i}
                          className="border-b border-border/40 transition-colors last:border-0 hover:bg-[var(--bg-secondary)]"
                        >
                          <td className="px-4 py-3 text-left text-[12.5px] font-medium tabular-nums text-foreground">
                            {l.strike.toLocaleString("en-IN")} {l.option_type}
                          </td>
                          <td className="px-4 py-3 text-right">
                            <span
                              className="inline-flex items-center rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold"
                              style={{
                                color: l.side === "BUY" ? "var(--color-profit)" : "var(--color-loss)",
                                background: `color-mix(in srgb, ${l.side === "BUY" ? "var(--color-profit)" : "var(--color-loss)"} 10%, transparent)`,
                              }}
                            >
                              {l.side}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-right text-[12.5px] tabular-nums text-foreground">
                            {l.mid != null ? fmtInr(l.mid, 2) : "—"}
                          </td>
                          <td className="px-4 py-3 text-right text-[12.5px] tabular-nums text-muted-foreground">
                            {l.iv != null ? `${(l.iv * 100).toFixed(1)}%` : "—"}
                          </td>
                          <td className="px-4 py-3 text-right text-[12.5px] tabular-nums text-muted-foreground">
                            {l.delta != null ? l.delta.toFixed(2) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-[10.5px] leading-snug text-muted-foreground/70">
                  Net Greeks are scaled by lot size × lots ({locked.lot_size * qtyLots}). A negative
                  net theta means the position loses value each day from time decay; positive net
                  delta means it gains as the underlying rises.
                </p>
              </div>
            )}

          </div>
          </div>
        </div>
      </div>
    </ContentOverlay>
  );
}

// ---------------------------------------------------------------------------
// Builder tab
// ---------------------------------------------------------------------------

function BuilderTab({
  legs,
  serverLegs,
  chain,
  chainLoading,
  qtyLots,
  expiry,
  lotSize,
  disabled,
  onUpdateLeg,
  onRemoveLeg,
  onAddLeg,
  onQtyChange,
  onExpiryChange,
  minLots,
  maxLots,
}: {
  legs: StrategyLeg[];
  serverLegs: StrategyLeg[];
  chain: OptionChainSlice | null;
  chainLoading: boolean;
  qtyLots: number;
  expiry: string;
  lotSize: number;
  disabled: boolean;
  onUpdateLeg: (idx: number, patch: Partial<StrategyLeg>) => void;
  onRemoveLeg: (idx: number) => void;
  onAddLeg: () => void;
  onQtyChange: (v: number) => void;
  onExpiryChange: (v: string) => void;
  minLots: number;
  maxLots: number;
}): React.ReactElement {
  const expiries = chain?.expiries ?? [{ expiry, kind: "weekly" as const }];
  // Shared grid template so the header labels line up over every leg row. All
  // columns are fixed-or-fluid (no `auto`/large mins) so the row always fits
  // the left pane width with NO horizontal scroll.
  const GRID = "grid items-center gap-x-2 grid-cols-[28px_minmax(56px,1fr)_104px_34px_48px_minmax(46px,0.7fr)_24px]";
  const fmtExpiryShort = (iso: string): string => {
    try {
      return new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" });
    } catch {
      return iso;
    }
  };

  return (
    <div className="flex flex-col gap-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground/70">Legs ({legs.length})</span>
        <div className="flex items-center gap-2">
          {chainLoading && (
            <span className="inline-flex items-center gap-1 text-[10.5px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" /> loading chain
            </span>
          )}
          <button
            type="button"
            onClick={onAddLeg}
            disabled={disabled || legs.length >= 6 || !chain}
            className="inline-flex items-center gap-1 rounded-md border border-border/60 px-2 py-1 text-[11px] font-medium text-foreground hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Plus className="h-3 w-3" aria-hidden="true" /> Add leg
          </button>
        </div>
      </div>

      {/* Column labels */}
      <div className={cn(GRID, "px-1 text-[9.5px] font-medium uppercase tracking-wider text-muted-foreground/60")}>
        <span>B/S</span>
        <span>Expiry</span>
        <span className="text-center">Strike</span>
        <span>Type</span>
        <span className="text-center">Lots</span>
        <span className="text-right">Price</span>
        <span />
      </div>

      {/* Leg rows */}
      <div className="flex flex-col gap-2">
        {legs.map((leg, idx) => {
          const avail = chain ? strikesFor(chain, leg.option_type) : [];
          const mid = serverLegs[idx]?.mid ?? leg.mid;
          const sIdx = avail.indexOf(leg.strike);
          const stepStrike = (dir: number): void => {
            if (sIdx < 0 || avail.length === 0) return;
            const ni = Math.min(avail.length - 1, Math.max(0, sIdx + dir));
            const next = avail[ni];
            if (next != null) onUpdateLeg(idx, { strike: next });
          };
          return (
            <div key={idx} className={cn(GRID, "rounded-lg px-1 py-1.5 transition-colors hover:bg-muted/30")}>
              {/* B/S */}
              <button
                type="button"
                disabled={disabled}
                aria-label={`Toggle side for leg ${idx + 1} (currently ${leg.side})`}
                onClick={() => onUpdateLeg(idx, { side: leg.side === "BUY" ? "SELL" : "BUY" })}
                className="flex h-7 w-7 items-center justify-center rounded-md text-[12px] font-bold text-white transition-transform active:scale-95 disabled:opacity-50"
                style={{ background: leg.side === "BUY" ? "var(--color-profit)" : "var(--color-loss)" }}
              >
                {leg.side === "BUY" ? "B" : "S"}
              </button>

              {/* Expiry (shared across legs) */}
              <select
                value={expiry}
                disabled={disabled || !chain}
                onChange={(e) => onExpiryChange(e.target.value)}
                className="w-full min-w-0 rounded-md border border-border/50 bg-background px-1 py-1 text-[11px] text-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-60"
                aria-label="Expiry"
              >
                {expiries.map((e) => (
                  <option key={e.expiry} value={e.expiry}>
                    {fmtExpiryShort(e.expiry)}
                  </option>
                ))}
              </select>

              {/* Strike − value + */}
              <div className="flex w-full items-center gap-1">
                <button
                  type="button"
                  aria-label="Lower strike"
                  disabled={disabled || sIdx <= 0}
                  onClick={() => stepStrike(-1)}
                  className="flex h-6 w-5 shrink-0 items-center justify-center rounded border border-border/50 text-[13px] text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30"
                >
                  −
                </button>
                <select
                  value={leg.strike}
                  disabled={disabled || avail.length === 0}
                  onChange={(e) => onUpdateLeg(idx, { strike: Number(e.target.value) })}
                  className="min-w-0 flex-1 appearance-none rounded-md border border-border/50 bg-background px-1 py-1 text-center text-[12px] font-medium tabular-nums text-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-60"
                  aria-label={`Strike for leg ${idx + 1}`}
                >
                  {avail.length === 0 && <option value={leg.strike}>{leg.strike}</option>}
                  {avail.map((s) => (
                    <option key={s} value={s}>
                      {s.toLocaleString("en-IN")}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  aria-label="Higher strike"
                  disabled={disabled || sIdx < 0 || sIdx >= avail.length - 1}
                  onClick={() => stepStrike(1)}
                  className="flex h-6 w-5 shrink-0 items-center justify-center rounded border border-border/50 text-[13px] text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30"
                >
                  +
                </button>
              </div>

              {/* Type CE/PE */}
              <button
                type="button"
                disabled={disabled}
                aria-label={`Toggle option type for leg ${idx + 1} (currently ${leg.option_type})`}
                onClick={() => onUpdateLeg(idx, { option_type: leg.option_type === "CE" ? "PE" : "CE" })}
                className="rounded-md border border-border/50 px-1 py-1 text-[11px] font-semibold text-foreground transition-colors hover:bg-muted disabled:opacity-50"
              >
                {leg.option_type}
              </button>

              {/* Lots (shared) — number field with native stepper */}
              <input
                type="number"
                min={minLots}
                max={maxLots}
                value={qtyLots}
                disabled={disabled}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10);
                  if (!isNaN(v)) onQtyChange(Math.min(maxLots, Math.max(minLots, v)));
                }}
                className="w-full min-w-0 rounded-md border border-border/50 bg-background px-1 py-1 text-center text-[12px] tabular-nums text-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-60"
                aria-label="Number of lots (whole strategy)"
              />

              {/* Price (mid, read-only) */}
              <span className="truncate text-right text-[11.5px] font-medium tabular-nums text-foreground">
                {mid != null ? mid.toFixed(2) : "—"}
              </span>

              {/* Delete */}
              <button
                type="button"
                aria-label={`Remove leg ${idx + 1}`}
                onClick={() => onRemoveLeg(idx)}
                disabled={disabled || legs.length <= 1}
                className="flex h-6 w-5 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-30"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>

      <p className="text-[10px] text-muted-foreground/60">
        Lot size {lotSize}. Expiry &amp; lots apply to the whole strategy. Edits recompute payoff, Greeks &amp; margin live.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Payoff table — the same payoff data as the chart, read as a grid of target
// prices with the pre-expiry (T+0) and at-expiry P&L side by side. The row at
// the current spot is highlighted; values can be shown in ₹ or as % of capital.
// ---------------------------------------------------------------------------

function fmtDayLabel(d: Date): string {
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
}

function PayoffTable({
  payoff,
  payoffNow,
  forward,
  capital,
  interval,
  showPct,
  expiry,
}: {
  payoff: { s: number; pnl: number }[];
  payoffNow: { s: number; pnl: number }[];
  forward: number;
  capital: number;
  interval: number;
  showPct: boolean;
  expiry: string;
}): React.ReactElement {
  if (payoff.length === 0) {
    return (
      <div className="py-8 text-center text-[11.5px] text-muted-foreground">No payoff data</div>
    );
  }

  const sMin = payoff[0]!.s;
  const sMax = payoff[payoff.length - 1]!.s;
  const base = Math.round(forward / interval) * interval;
  const N = 6;

  // Grid rows stepping by `interval`; the centre row is replaced by the exact
  // current spot so the live underlying always appears.
  const rows: { s: number; current: boolean }[] = [];
  for (let i = -N; i <= N; i++) {
    if (i === 0) {
      rows.push({ s: forward, current: true });
      continue;
    }
    const s = base + i * interval;
    if (s < sMin - 0.5 || s > sMax + 0.5) continue;
    rows.push({ s, current: false });
  }

  const hasNow = payoffNow.length > 0;
  const today = fmtDayLabel(new Date());
  let expiryLabel = expiry;
  try {
    expiryLabel = fmtDayLabel(new Date(expiry + "T00:00:00"));
  } catch {
    /* keep raw */
  }

  const cell = (v: number | null): string => {
    if (v == null) return "—";
    if (showPct) return capital > 0 ? `${((v / capital) * 100).toFixed(1)}%` : "—";
    return Math.round(v).toLocaleString("en-IN");
  };
  const toneCls = (v: number | null): string =>
    v == null
      ? "text-muted-foreground"
      : v >= 0
        ? "text-emerald-600 dark:text-emerald-400"
        : "text-rose-600 dark:text-rose-400";

  return (
    <div>
      <table className="w-full text-[12px] tabular-nums">
        <thead>
          <tr className="border-b border-border/40 text-left align-bottom">
            <th className="px-3 py-2 font-medium text-muted-foreground">Target</th>
            <th className="px-3 py-2 text-right font-medium text-muted-foreground">
              On target date
              <span className="block text-[10px] font-normal text-muted-foreground/60">{today}</span>
            </th>
            <th className="px-3 py-2 text-right font-medium text-muted-foreground">
              On expiry
              <span className="block text-[10px] font-normal text-muted-foreground/60">
                {expiryLabel}
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const nowPnl = hasNow ? payoffAt(payoffNow, r.s) : null;
            const expPnl = payoffAt(payoff, r.s);
            return (
              <tr
                key={i}
                className={cn(
                  "border-t border-border/20",
                  r.current
                    ? "bg-amber-50/60 dark:bg-amber-400/[0.06]"
                    : i % 2 === 1 && "bg-muted/15",
                )}
              >
                <td
                  className={cn(
                    "px-3 py-2 text-left text-foreground",
                    r.current && "font-semibold",
                  )}
                >
                  {r.current
                    ? forward.toLocaleString("en-IN", { maximumFractionDigits: 2 })
                    : r.s.toLocaleString("en-IN")}
                </td>
                <td className={cn("px-3 py-2 text-right font-medium", toneCls(nowPnl))}>
                  {cell(nowPnl)}
                </td>
                <td className={cn("px-3 py-2 text-right font-medium", toneCls(expPnl))}>
                  {cell(expPnl)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Critique block (shared shape with the legacy card)
// ---------------------------------------------------------------------------

function CritiqueBlock({
  flags,
  summary,
}: {
  flags: CritiqueFlag[];
  summary: string;
}): React.ReactElement {
  return (
    <div className="px-1 py-1">
      <p className="text-[11.5px] text-foreground">{summary}</p>
      {flags.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1.5">
          {flags.map((f, i) => (
            <li key={i} className="flex items-start gap-1.5">
              {f.severity === "info" ? (
                <Info className="mt-px h-3 w-3 shrink-0 text-sky-500" aria-hidden="true" />
              ) : (
                <AlertCircle
                  className={cn(
                    "mt-px h-3 w-3 shrink-0",
                    f.severity === "warn" ? "text-amber-500" : "text-rose-500",
                  )}
                  aria-hidden="true"
                />
              )}
              <span
                className={cn(
                  "text-[11px] leading-snug",
                  f.severity === "info" && "text-sky-700 dark:text-sky-300",
                  f.severity === "warn" && "text-amber-700 dark:text-amber-300",
                  f.severity === "risk" && "text-rose-700 dark:text-rose-300",
                )}
              >
                {f.text}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
