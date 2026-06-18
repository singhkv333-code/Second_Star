"use client";

/**
 * OptionStrategyCard — editable option strategy widget.
 * State machine: idle → saving → registered → withdrawn/error.
 *
 * Sections:
 *  - Header: template label, underlying + expiry, risk chip, book toggle
 *  - Legs editor: side chip, option_type, strike input, qty_lots input
 *  - Payoff chart (recharts LineChart with zero line + breakevens)
 *  - Decision quad: max_loss / max_profit / pop / capital_required
 *  - Greeks row (collapsible)
 *  - Critique block
 *  - Candidates strip
 *  - Disclosure checkbox (when required)
 *  - Register CTA
 *
 * Structural precedent: IpoApplicationCard.tsx.
 */

import { useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Info,
  Loader2,
  ShieldAlert,
  Undo2,
} from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { cn } from "@/lib/utils";
import {
  registerOptionStrategy,
  withdrawOptionStrategy,
} from "@/lib/api";
import { isError } from "@/lib/types";
import type {
  CritiqueFlag,
  OptionStrategyPayload,
  StrategyCandidate,
  StrategyLeg,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type OptionStrategyCardProps = {
  payload: OptionStrategyPayload;
  /** Called when user clicks a candidate pill. Parent may ignore. */
  onSelectCandidate?: (template: string) => void;
};

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

type CardState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "registered"; strategy: NonNullable<import("@/lib/types").OptionStrategyRegisterResponse["strategy"]> }
  | { kind: "withdrawing" }
  | { kind: "withdrawn" }
  | { kind: "error"; message: string };

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

const SANS_FONT =
  "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Inter, Roboto, sans-serif";

function fmtInr(n: number, maximumFractionDigits = 0): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits,
  }).format(n);
}

function fmtExpiry(expiry: string): string {
  try {
    const d = new Date(expiry + "T00:00:00");
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "2-digit" });
  } catch {
    return expiry;
  }
}

/** Convert snake_case to human-readable title: "bull_call_spread" → "Bull Call Spread" */
function humanizeTemplate(t: string): string {
  return t
    .split("_")
    .map((w) => (w.length > 0 ? w[0]!.toUpperCase() + w.slice(1) : w))
    .join(" ");
}

// ---------------------------------------------------------------------------
// Risk chip
// ---------------------------------------------------------------------------

function RiskChip({
  verdict,
}: {
  verdict: "ok" | "caution" | "risky";
}): React.ReactElement {
  const map = {
    ok: { label: "Balanced", cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300" },
    caution: { label: "Caution", cls: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300" },
    risky: { label: "Risky", cls: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300" },
  };
  const { label, cls } = map[verdict];
  return (
    <span className={cn("inline-flex items-center rounded-md px-2 py-0.5 text-[10.5px] font-medium", cls)}>
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Payoff chart
// ---------------------------------------------------------------------------

function PayoffChart({
  data,
  breakevens,
  forward,
}: {
  data: { s: number; pnl: number }[];
  breakevens: number[];
  forward: number;
}): React.ReactElement {
  if (data.length === 0) {
    return (
      <div className="flex h-[140px] items-center justify-center text-[11.5px] text-muted-foreground">
        No payoff data
      </div>
    );
  }

  const chartData = data.map((p) => ({ s: p.s, pnl: p.pnl }));
  const maxPnl = Math.max(...data.map((p) => p.pnl));
  const positive = maxPnl >= 0;

  return (
    <div className="h-[140px]" data-testid="option-payoff-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="2 4" opacity={0.15} vertical={false} />
          <XAxis
            dataKey="s"
            tick={{ fontSize: 9, fill: "rgb(107 114 128)" }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => v.toLocaleString("en-IN")}
            minTickGap={40}
          />
          <YAxis
            tick={{ fontSize: 9, fill: "rgb(107 114 128)" }}
            axisLine={false}
            tickLine={false}
            width={52}
            tickFormatter={(v: number) =>
              Math.abs(v) >= 1000
                ? `${v >= 0 ? "" : "-"}₹${(Math.abs(v) / 1000).toFixed(0)}K`
                : `₹${v.toFixed(0)}`
            }
          />
          <Tooltip
            contentStyle={{
              fontSize: "11px",
              borderRadius: "8px",
              padding: "5px 10px",
              border: "1px solid rgba(0,0,0,0.08)",
              fontFamily: SANS_FONT,
            }}
            formatter={(value: number) => [fmtInr(value), "P&L"]}
            labelFormatter={(label: number) => `Underlying: ${fmtInr(label)}`}
          />
          {/* Zero line */}
          <ReferenceLine y={0} stroke="rgb(107 114 128)" strokeDasharray="3 3" strokeOpacity={0.5} />
          {/* Current forward */}
          <ReferenceLine
            x={forward}
            stroke="#6366f1"
            strokeWidth={1.5}
            strokeDasharray="4 2"
            label={{ value: "Fwd", position: "top", fontSize: 9, fill: "#6366f1" }}
          />
          {/* Breakevens */}
          {breakevens.map((be) => (
            <ReferenceLine
              key={be}
              x={be}
              stroke="#f59e0b"
              strokeWidth={1}
              strokeDasharray="3 3"
              label={{ value: "BE", position: "top", fontSize: 9, fill: "#f59e0b" }}
            />
          ))}
          <Line
            type="monotone"
            dataKey="pnl"
            stroke={positive ? "#10b981" : "#ef4444"}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 3 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Decision quad
// ---------------------------------------------------------------------------

function DecisionQuad({
  maxLoss,
  maxProfit,
  pop,
  capitalRequired,
  netPremium,
  marginEstimate,
  marginNote,
}: {
  maxLoss: number | null;
  maxProfit: number | null;
  pop: number | null;
  capitalRequired: number;
  netPremium: number;
  marginEstimate: number;
  marginNote: string;
}): React.ReactElement {
  return (
    <div className="rounded-xl border border-border/40 bg-muted/30 px-4 py-3">
      <div className="grid grid-cols-2 gap-x-4 gap-y-3">
        <div>
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground/70">Max loss</p>
          <p className="mt-0.5 text-[14px] font-semibold tabular-nums text-rose-600 dark:text-rose-400">
            {maxLoss === null ? "Unlimited" : fmtInr(maxLoss)}
          </p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground/70">Max profit</p>
          <p className="mt-0.5 text-[14px] font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
            {maxProfit === null ? "Unlimited" : fmtInr(maxProfit)}
          </p>
        </div>
        {pop !== null && (
          <div>
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground/70">POP</p>
            <p className="mt-0.5 text-[14px] font-semibold tabular-nums text-foreground">
              {(pop * 100).toFixed(0)}%
            </p>
          </div>
        )}
        <div>
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground/70">Capital required</p>
          <p className="mt-0.5 text-[14px] font-semibold tabular-nums text-foreground">
            {fmtInr(capitalRequired)}
          </p>
        </div>
      </div>
      {/* Net premium */}
      <div className="mt-2.5 border-t border-border/30 pt-2">
        <p className="text-[11px] text-muted-foreground">
          {netPremium <= 0
            ? `Net debit ${fmtInr(Math.abs(netPremium))}`
            : `Net credit ${fmtInr(netPremium)}`}
        </p>
        <p className="mt-0.5 text-[10.5px] text-muted-foreground/70">
          Margin est. {fmtInr(marginEstimate)}
          {marginNote && <span className="ml-1">({marginNote})</span>}
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Critique block
// ---------------------------------------------------------------------------

function CritiqueBlock({ flags, summary }: { flags: CritiqueFlag[]; summary: string }): React.ReactElement {
  return (
    <div className="rounded-xl border border-border/40 bg-muted/20 px-4 py-3">
      <p className="text-[11.5px] text-foreground">{summary}</p>
      {flags.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1.5">
          {flags.map((f, i) => (
            <li key={i} className="flex items-start gap-1.5">
              {f.severity === "info" && (
                <Info className="mt-px h-3 w-3 shrink-0 text-sky-500" aria-hidden="true" />
              )}
              {f.severity === "warn" && (
                <AlertCircle className="mt-px h-3 w-3 shrink-0 text-amber-500" aria-hidden="true" />
              )}
              {f.severity === "risk" && (
                <AlertCircle className="mt-px h-3 w-3 shrink-0 text-rose-500" aria-hidden="true" />
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

// ---------------------------------------------------------------------------
// Candidate pill
// ---------------------------------------------------------------------------

const RISK_TAG_STYLES: Record<StrategyCandidate["risk_tag"], string> = {
  conservative: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  moderate: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
  aggressive: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
};

function CandidatePill({
  candidate,
  onSelect,
}: {
  candidate: StrategyCandidate;
  onSelect?: (template: string) => void;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={() => onSelect?.(candidate.template)}
      className={cn(
        "flex flex-col gap-1 rounded-xl border border-border/50 bg-background px-3 py-2.5 text-left",
        "transition-colors hover:bg-muted hover:border-border",
        "focus:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        "min-w-[140px]",
      )}
    >
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[12px] font-semibold tracking-tight text-foreground">
          {humanizeTemplate(candidate.label)}
        </span>
        <span className={cn("inline-flex items-center rounded-md px-1.5 py-px text-[9.5px] font-medium", RISK_TAG_STYLES[candidate.risk_tag])}>
          {candidate.risk_tag}
        </span>
      </div>
      <p className="text-[10.5px] leading-snug text-muted-foreground">{candidate.one_liner}</p>
      <div className="flex gap-2 text-[10px] tabular-nums text-muted-foreground/70">
        {candidate.max_loss !== null && (
          <span className="text-rose-600 dark:text-rose-400">
            max loss {fmtInr(candidate.max_loss)}
          </span>
        )}
        {candidate.pop !== null && (
          <span>POP {(candidate.pop * 100).toFixed(0)}%</span>
        )}
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Leg row
// ---------------------------------------------------------------------------

function LegRow({
  leg,
  index,
  disabled,
  onStrikeChange,
  onQtyChange,
  qtyLots,
  minLots,
  maxLots,
  showQtyOnFirst,
}: {
  leg: StrategyLeg;
  index: number;
  disabled: boolean;
  onStrikeChange: (idx: number, val: number) => void;
  onQtyChange: (val: number) => void;
  qtyLots: number;
  minLots: number;
  maxLots: number;
  /** Only the first leg row shows the qty editor (shared across all legs). */
  showQtyOnFirst: boolean;
}): React.ReactElement {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {/* Side chip */}
      <span
        className={cn(
          "inline-flex items-center rounded-md px-2 py-0.5 text-[10.5px] font-semibold",
          leg.side === "BUY"
            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
            : "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
        )}
      >
        {leg.side}
      </span>
      {/* Option type */}
      <span className="text-[11.5px] font-medium text-foreground">{leg.option_type}</span>
      {/* Strike input */}
      <div className="flex flex-col gap-0.5">
        <label className="sr-only">Strike for leg {index + 1}</label>
        <input
          type="number"
          value={leg.strike}
          disabled={disabled}
          onChange={(e) => {
            const v = parseFloat(e.target.value);
            if (!isNaN(v)) onStrikeChange(index, v);
          }}
          className={cn(
            "w-24 rounded-lg border border-border/60 bg-background px-2 py-1 text-center text-[12px] tabular-nums text-foreground",
            "focus:outline-none focus:ring-1 focus:ring-ring",
            "disabled:cursor-not-allowed disabled:opacity-60",
          )}
          aria-label={`Strike for ${leg.side} ${leg.option_type} (leg ${index + 1})`}
        />
      </div>
      {/* Mid premium (read-only) */}
      {leg.mid !== undefined && (
        <span className="text-[11px] tabular-nums text-muted-foreground">
          Mid {fmtInr(leg.mid, 2)}
        </span>
      )}
      {/* Qty — only on first leg */}
      {showQtyOnFirst && index === 0 && (
        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground/70">Lots</span>
          <button
            type="button"
            aria-label="Decrease lots"
            disabled={disabled || qtyLots <= minLots}
            onClick={() => onQtyChange(Math.max(minLots, qtyLots - 1))}
            className={cn(
              "flex h-6 w-6 items-center justify-center rounded-md border border-border/60 text-[12px] text-muted-foreground",
              "hover:bg-muted hover:text-foreground transition-colors",
              "disabled:cursor-not-allowed disabled:opacity-40",
            )}
          >
            −
          </button>
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
            className={cn(
              "w-12 rounded-lg border border-border/60 bg-background px-1 py-1 text-center text-[12px] tabular-nums text-foreground",
              "focus:outline-none focus:ring-1 focus:ring-ring",
              "disabled:cursor-not-allowed disabled:opacity-60",
            )}
            aria-label="Number of lots"
          />
          <button
            type="button"
            aria-label="Increase lots"
            disabled={disabled || qtyLots >= maxLots}
            onClick={() => onQtyChange(Math.min(maxLots, qtyLots + 1))}
            className={cn(
              "flex h-6 w-6 items-center justify-center rounded-md border border-border/60 text-[12px] text-muted-foreground",
              "hover:bg-muted hover:text-foreground transition-colors",
              "disabled:cursor-not-allowed disabled:opacity-40",
            )}
          >
            +
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main card
// ---------------------------------------------------------------------------

export function OptionStrategyCard({
  payload,
  onSelectCandidate,
}: OptionStrategyCardProps): React.ReactElement {
  const { locked, computed, validation, critique, candidates } = payload;

  // Editable state — initialised from payload.editable
  const [book, setBook] = useState<"paper" | "live">(payload.editable.book);
  const [qtyLots, setQtyLots] = useState<number>(payload.editable.qty_lots);
  const [legs, setLegs] = useState<StrategyLeg[]>(payload.editable.legs);
  const [greeksOpen, setGreeksOpen] = useState(false);
  const [disclosureChecked, setDisclosureChecked] = useState(false);

  const [cardState, setCardState] = useState<CardState>({ kind: "idle" });

  // Dirty tracking: if user edits strikes or qty away from the computed
  // snapshot, show a subtle hint that values will be recomputed on register.
  const isDirty = useMemo(() => {
    if (qtyLots !== payload.editable.qty_lots) return true;
    return legs.some((l, i) => {
      const orig = payload.editable.legs[i];
      return orig === undefined || l.strike !== orig.strike;
    });
  }, [qtyLots, legs, payload.editable]);

  function handleStrikeChange(idx: number, val: number): void {
    setLegs((prev) => prev.map((l, i) => (i === idx ? { ...l, strike: val } : l)));
  }

  const isBusy = cardState.kind === "saving" || cardState.kind === "withdrawing";
  const isRegistered = cardState.kind === "registered";
  const isWithdrawn = cardState.kind === "withdrawn";

  // Register disabled conditions
  const blockReasons: string[] = [];
  if (!validation.lot_multiple_ok) blockReasons.push("Lot count is not a valid multiple.");
  if (validation.mcx_execution_blocked) blockReasons.push("MCX execution blocked — research only.");
  if (validation.requires_disclosure && !disclosureChecked) blockReasons.push("Acknowledge the disclosure first.");
  const canRegister = !isBusy && !isRegistered && !isWithdrawn && blockReasons.length === 0;

  async function handleRegister(): Promise<void> {
    if (!canRegister) return;
    setCardState({ kind: "saving" });

    const result = await registerOptionStrategy({
      underlying: locked.underlying,
      expiry: locked.expiry,
      template: payload.editable.template,
      book,
      qty_lots: qtyLots,
      legs: legs.map((l) => ({
        option_type: l.option_type,
        side: l.side,
        strike: l.strike,
      })),
      acknowledge_disclosure: disclosureChecked,
      conversation_id: payload.conversation_id ?? undefined,
    });

    if (isError(result)) {
      setCardState({ kind: "error", message: result.error.message ?? "Registration failed — try again." });
      return;
    }
    if (!result.data.success || !result.data.strategy) {
      setCardState({
        kind: "error",
        message: result.data.error ?? "Registration failed — try again.",
      });
      return;
    }
    setCardState({ kind: "registered", strategy: result.data.strategy });
  }

  async function handleWithdraw(): Promise<void> {
    if (cardState.kind !== "registered") return;
    const stratId = cardState.strategy.id;
    setCardState({ kind: "withdrawing" });

    const result = await withdrawOptionStrategy(stratId);
    if (isError(result)) {
      setCardState({
        kind: "error",
        message: result.error.message ?? "Withdrawal failed — try again.",
      });
      return;
    }
    setCardState({ kind: "withdrawn" });
  }

  return (
    <div
      data-testid="option-strategy-card"
      role="region"
      aria-label={`Option strategy: ${humanizeTemplate(payload.editable.template)} on ${locked.underlying}`}
      className={cn(
        "my-2 w-full max-w-[480px] overflow-hidden rounded-3xl border border-border/50 bg-card",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
      )}
    >
      {/* Header */}
      <div className="flex flex-col gap-2 px-5 pt-4 pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="inline-flex items-center rounded-md bg-purple-100 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-purple-700 dark:bg-purple-500/15 dark:text-purple-300">
              F&amp;O Strategy
            </span>
            <span className="text-[10.5px] font-medium uppercase tracking-widest text-muted-foreground">
              {locked.exchange} · {locked.segment}
            </span>
          </div>
          <RiskChip verdict={critique.verdict} />
        </div>
        <div className="flex flex-wrap items-baseline gap-2">
          <h3 className="text-[16px] font-semibold tracking-tight text-foreground">
            {humanizeTemplate(payload.editable.template)}
          </h3>
          <span className="text-[12px] text-muted-foreground">
            {locked.underlying} · {fmtExpiry(locked.expiry)} ({locked.expiry_kind})
          </span>
        </div>
        {locked.research_only && (
          <span className="inline-flex items-center gap-1 self-start rounded-md bg-amber-100 px-2 py-0.5 text-[10.5px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
            <AlertCircle className="h-3 w-3 shrink-0" aria-hidden="true" />
            MCX — research only, no execution
          </span>
        )}
      </div>

      {/* Book toggle */}
      <div className="flex items-center gap-3 border-t border-border/30 px-5 py-2.5">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground/70">Book</span>
        {(["paper", "live"] as const).map((b) => (
          <button
            key={b}
            type="button"
            onClick={() => setBook(b)}
            disabled={isBusy || isRegistered || isWithdrawn}
            className={cn(
              "rounded-lg px-3 py-1 text-[11.5px] font-medium transition-colors border",
              book === b
                ? "border-primary bg-primary/10 text-primary"
                : "border-border/60 text-muted-foreground hover:bg-muted hover:text-foreground",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {b === "paper" ? "Paper" : "Live"}
          </button>
        ))}
        {book === "live" && (
          <span className="text-[10.5px] text-amber-600 dark:text-amber-400">
            register only — you confirm in broker
          </span>
        )}
      </div>

      {/* Legs editor */}
      {!isRegistered && !isWithdrawn && (
        <div className="flex flex-col gap-2.5 border-t border-border/30 px-5 py-3">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground/70">Legs</p>
          {legs.map((leg, idx) => (
            <LegRow
              key={idx}
              leg={leg}
              index={idx}
              disabled={isBusy}
              onStrikeChange={handleStrikeChange}
              onQtyChange={setQtyLots}
              qtyLots={qtyLots}
              minLots={validation.min_lots}
              maxLots={validation.max_lots}
              showQtyOnFirst
            />
          ))}
          {isDirty && (
            <p className="text-[10px] text-muted-foreground/60 italic">
              Values edited — payoff + greeks recomputed on register.
            </p>
          )}
        </div>
      )}

      {/* Payoff chart */}
      <div className="border-t border-border/30 px-5 py-3">
        <p className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground/70">
          Expiry P&amp;L
        </p>
        <PayoffChart
          data={computed.payoff}
          breakevens={computed.breakevens}
          forward={locked.forward}
        />
        {computed.breakevens.length > 0 && (
          <p className="mt-1 text-[10px] tabular-nums text-muted-foreground/70">
            BE: {computed.breakevens.map((b) => b.toLocaleString("en-IN")).join(", ")}
          </p>
        )}
      </div>

      {/* Decision quad */}
      <div className="border-t border-border/30 px-5 py-3">
        <DecisionQuad
          maxLoss={computed.max_loss}
          maxProfit={computed.max_profit}
          pop={computed.pop}
          capitalRequired={computed.capital_required}
          netPremium={computed.net_premium}
          marginEstimate={computed.margin_estimate}
          marginNote={computed.margin_note}
        />
      </div>

      {/* Greeks row (collapsible) */}
      <div className="border-t border-border/30 px-5 py-2">
        <button
          type="button"
          onClick={() => setGreeksOpen((v) => !v)}
          className="flex w-full items-center justify-between text-[10.5px] font-medium text-muted-foreground hover:text-foreground transition-colors"
          aria-expanded={greeksOpen}
        >
          <span className="uppercase tracking-wider text-[10px] text-muted-foreground/70">Net Greeks</span>
          {greeksOpen ? (
            <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
          )}
        </button>
        {greeksOpen && (
          <div className="mt-2 grid grid-cols-4 gap-3">
            {(["delta", "gamma", "theta", "vega"] as const).map((g) => (
              <div key={g}>
                <p className="text-[10px] uppercase tracking-wider text-muted-foreground/70">{g}</p>
                <p className="mt-0.5 text-[12.5px] font-medium tabular-nums text-foreground">
                  {computed.net_greeks[g].toFixed(3)}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Critique block */}
      <div className="border-t border-border/30 px-5 py-3">
        <CritiqueBlock flags={critique.flags} summary={critique.summary} />
      </div>

      {/* Candidates strip */}
      {candidates.length > 0 && (
        <div className="border-t border-border/30 px-5 py-3">
          <p className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground/70">
            Alternatives
          </p>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {candidates.map((c) => (
              <CandidatePill key={c.template} candidate={c} onSelect={onSelectCandidate} />
            ))}
          </div>
        </div>
      )}

      {/* Expiry gamma warning */}
      {validation.expiry_gamma_warn && (
        <div className="mx-5 mb-1 flex items-start gap-1.5 rounded-lg bg-amber-50 px-3 py-2 dark:bg-amber-500/10">
          <AlertCircle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-600" aria-hidden="true" />
          <p className="text-[11px] text-amber-700 dark:text-amber-300">
            Expiry-day gamma risk — option premium can move sharply in the final hours.
          </p>
        </div>
      )}

      {/* Liquidity flags */}
      {!validation.liquidity_ok && validation.liquidity_flags.length > 0 && (
        <div className="mx-5 mb-1 flex items-start gap-1.5 rounded-lg bg-rose-50 px-3 py-2 dark:bg-rose-500/10">
          <AlertCircle className="mt-px h-3.5 w-3.5 shrink-0 text-rose-600" aria-hidden="true" />
          <p className="text-[11px] text-rose-700 dark:text-rose-300">
            {validation.liquidity_flags.join(" · ")}
          </p>
        </div>
      )}

      {/* Registered confirmation */}
      {isRegistered && (
        <div
          className="flex flex-col gap-3 border-t border-border/30 px-5 py-4"
          data-testid="option-strategy-registered"
        >
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-emerald-500" strokeWidth={2.25} aria-hidden="true" />
            <div>
              <p className="text-[13px] font-semibold tracking-tight text-foreground">
                Strategy registered
              </p>
              <p className="text-[11.5px] text-muted-foreground">
                {cardState.strategy.underlying} · {humanizeTemplate(cardState.strategy.template)} ·
                {" "}{cardState.strategy.qty_lots} lots · {cardState.strategy.book} ·{" "}
                <span className="capitalize">{cardState.strategy.status}</span>
              </p>
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">
            ID: <span className="font-mono text-[10px]">{cardState.strategy.id}</span>
          </p>
          <button
            type="button"
            onClick={() => void handleWithdraw()}
            disabled={isBusy}
            data-testid="option-strategy-withdraw-button"
            className={cn(
              "inline-flex items-center gap-1.5 self-start rounded-md px-3 py-1.5 text-[11.5px] font-medium text-muted-foreground",
              "border border-border/60 transition-colors hover:bg-muted hover:text-foreground",
              "disabled:cursor-not-allowed disabled:opacity-60",
            )}
          >
            <Undo2 className="h-3 w-3" aria-hidden="true" />
            Withdraw
          </button>
        </div>
      )}

      {/* Withdrawing */}
      {cardState.kind === "withdrawing" && (
        <div className="flex items-center gap-2 border-t border-border/30 px-5 py-4 text-[12.5px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Withdrawing…
        </div>
      )}

      {/* Withdrawn */}
      {isWithdrawn && (
        <div className="border-t border-border/30 px-5 py-4 text-[12.5px] text-muted-foreground">
          Strategy withdrawn.
        </div>
      )}

      {/* Form controls (hidden once registered/withdrawn) */}
      {!isRegistered && cardState.kind !== "withdrawing" && !isWithdrawn && (
        <div className="flex flex-col gap-3 border-t border-border/30 px-5 py-4">
          {/* Disclosure checkbox */}
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

          {/* Block reasons */}
          {blockReasons.length > 0 && (
            <div
              role="alert"
              className="flex items-start gap-1.5 rounded-lg bg-destructive/10 px-3 py-2 text-[11.5px] text-destructive"
            >
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>{blockReasons[0]}</span>
            </div>
          )}

          {/* Register CTA */}
          <button
            type="button"
            onClick={() => void handleRegister()}
            disabled={!canRegister}
            data-testid="option-strategy-register-button"
            className={cn(
              "inline-flex h-9 w-full items-center justify-center gap-1.5 rounded-full bg-primary text-[12.5px] font-medium tracking-tight text-primary-foreground transition-all",
              "hover:bg-primary/90 active:scale-[0.98]",
              "disabled:cursor-not-allowed disabled:opacity-70",
            )}
          >
            {cardState.kind === "saving" ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                <span>Registering…</span>
              </>
            ) : (
              <>
                <span>Register strategy</span>
                <ArrowUpRight className="h-4 w-4 shrink-0" aria-hidden="true" />
              </>
            )}
          </button>

          {/* Error */}
          {cardState.kind === "error" && (
            <p
              role="alert"
              data-testid="option-strategy-error"
              className="rounded-lg bg-destructive/10 px-3 py-2 text-[11.5px] text-destructive"
            >
              {cardState.message}
            </p>
          )}
        </div>
      )}

      {/* Disclosure footer */}
      <div className="flex items-start gap-1.5 border-t border-border/40 bg-amber-50/40 px-5 py-2 dark:bg-amber-500/[0.04]">
        <ShieldAlert
          className="mt-px h-3 w-3 shrink-0 text-amber-600/80 dark:text-amber-400/80"
          aria-hidden="true"
        />
        <p className="text-[10.5px] leading-snug text-amber-700/90 dark:text-amber-300/90">
          {locked.disclosure}
        </p>
      </div>
    </div>
  );
}
