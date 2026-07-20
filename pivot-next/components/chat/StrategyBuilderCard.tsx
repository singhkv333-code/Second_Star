"use client";

/**
 * StrategyBuilderCard — the editable, register-not-execute basket card
 * rendered when the backend's build_strategy tool returns
 * `_render_hint: "strategy_builder_card"` (plan §3b).
 *
 * Shows:
 *   - the NAMED weighting scheme + the NAMED selection gate (anti-bland: the
 *     card "shows its work", never bare equal-weight / top-mcap alone)
 *   - the enforced sector cap
 *   - an interactive allocation donut (hover/tap a slice ↔ holding row) plus a
 *     compact holdings list with per-constituent key fundamentals (gate_metrics)
 *   - the gold sleeve % (SGB / ETF instruments)
 *   - "(assumed …)" lines for every defaulted/skipped slot + honest-boundary
 *     fallbacks (e.g. covariance too thin → equal-weight)
 *   - a one-paragraph rationale tying back to {view × risk × horizon × capital}
 *   - the not-advice disclaimer
 *
 * Editable-card language mirrors WorkflowDraftCard / OptionStrategyCard (DS v2:
 * rounded-3xl surface, sky "Strategy" chip, stat strip, soft amber disclaimer
 * footer). Register-not-execute: the card frames a basket the user confirms in
 * their own broker app; nothing here auto-executes. Edits are surfaced back as a
 * chat amendment (the user types "make it equal-weight", "drop ITC", etc.).
 */

import { useMemo, useState } from "react";
import {
  Check,
  ChevronRight,
  Coins,
  GitBranch,
  History,
  Info,
  Layers,
  Loader2,
  Save,
  ShieldAlert,
  Sparkles,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";
import { cn } from "@/lib/utils";
import { isError } from "@/lib/types";
import { createEquityBasket, type EquityBasket } from "@/lib/agentsApi";
import { BasketTradeModal } from "@/components/agent-panel/BasketTradeModal";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetTitle,
} from "@/components/ui/sheet";
import type {
  GoldInstrument,
  SelectionGate,
  Sleeve,
  StrategyAlternative,
  StrategyBuilderCard as StrategyBuilderCardData,
  StrategyConstituent,
  WeightingScheme,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type StrategyBuilderCardProps = {
  card: StrategyBuilderCardData;
  /**
   * Send a prefilled chat message through the composer (used by the "Backtest"
   * action, which asks the chat pipeline to backtest the just-built basket).
   * Omitted in non-chat render contexts, where the action is hidden.
   */
  onBacktest?: (message: string) => void;
};

// ---------------------------------------------------------------------------
// Label maps — humanize the closed enums (never leave raw snake_case on screen)
// ---------------------------------------------------------------------------

const SCHEME_LABEL: Record<WeightingScheme, string> = {
  equal: "Equal-weight",
  mcap: "Market-cap weighted",
  risk_parity: "Risk-parity (ERC)",
  min_variance: "Minimum-variance",
  black_litterman: "Black-Litterman",
  factor: "Factor-weighted",
  conviction: "Conviction-weighted",
};

const GATE_LABEL: Record<SelectionGate, string> = {
  fscore: "Piotroski F-score",
  magic_formula: "Magic Formula",
  multifactor: "Multi-factor score",
  none: "Price / technical (no fundamental gate)",
};

/**
 * Allocation palette — the Portfolio page's "Vibrant Pivot" palette so the
 * basket reads consistently with the rest of the product: cobalt leads, then a
 * warm/cool rotation of orange · cyan-teal · golden yellow · dark teal · red.
 * Equity constituents cycle through these in order; the gold sleeve is pinned
 * to amber so it always reads as "gold" regardless of position.
 */
const SLICE_PALETTE = [
  "#1b7cc7", // cobalt blue
  "#fb8500", // vivid orange
  "#219ebc", // cyan teal
  "#ffb703", // golden yellow
  "#2c666e", // dark teal
  "#d00000", // red
];
const GOLD_COLOR = "#d97706"; // amber-600 — sleeves

/** Pretty-print a gate_metrics key: "earnings_yield" → "Earnings yield". */
function humanizeMetricKey(k: string): string {
  const upper = new Set(["pe", "pb", "roe", "roce", "de", "roic", "fscore", "pcr", "iv"]);
  if (upper.has(k.toLowerCase())) return k.toUpperCase();
  const words = k.split("_");
  return words
    .map((w, i) =>
      i === 0 ? (w.length > 0 ? w[0]!.toUpperCase() + w.slice(1) : w) : w,
    )
    .join(" ");
}

/** Format a fundamental value compactly — ratios to 1-2dp, yields as %. */
function fmtMetric(key: string, v: number): string {
  const k = key.toLowerCase();
  if (k.includes("yield") || k.includes("payout") || k.includes("margin")) {
    // Heuristic: a fraction < 1.5 reads as a ratio → show as %.
    if (Math.abs(v) <= 1.5) return `${(v * 100).toFixed(1)}%`;
    return `${v.toFixed(1)}%`;
  }
  if (k === "fscore") return v.toFixed(0);
  if (Number.isInteger(v)) return v.toString();
  return v.toFixed(Math.abs(v) >= 100 ? 0 : 1);
}

function fmtPct(v: number): string {
  return `${v.toFixed(v >= 10 || Number.isInteger(v) ? 0 : 1)}%`;
}

// ---------------------------------------------------------------------------
// Gold sleeve instrument kind labels
// ---------------------------------------------------------------------------

function goldKindLabel(k: GoldInstrument["kind"]): string {
  return k === "sgb" ? "SGB" : "Gold ETF";
}

// ---------------------------------------------------------------------------
// Allocation slice — flattened view of every weighted line in the basket
// (equity constituents + sleeves) so the donut sums to the whole basket.
// ---------------------------------------------------------------------------

type Slice = {
  key: string;
  label: string; // short symbol / sleeve name
  sub: string; // company / note
  pct: number;
  color: string;
};

// ---------------------------------------------------------------------------
// Quick stat (mirrors OptionStrategyCard's decision-quad cells)
// ---------------------------------------------------------------------------

function QuickStat({
  label,
  value,
}: {
  label: string;
  value: string;
}): React.ReactElement {
  return (
    <div className="flex min-w-0 flex-col items-center gap-1">
      <p className="truncate text-[19px] font-semibold leading-none tracking-tight tabular-nums text-foreground">
        {value}
      </p>
      <p className="text-[10.5px] font-normal leading-none text-muted-foreground/60">
        {label}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Details-panel primitives — a numeric stat tile and a method key/value row,
// both used inside the "Overview" card at the top of the details sidebar.
// ---------------------------------------------------------------------------

function PanelStat({
  label,
  value,
}: {
  label: string;
  value: string;
}): React.ReactElement {
  return (
    <div className="flex flex-col items-center justify-center gap-1 py-3">
      <p className="text-[18px] font-semibold leading-none tracking-tight tabular-nums text-foreground">
        {value}
      </p>
      <p className="text-[10px] leading-none text-muted-foreground/60">{label}</p>
    </div>
  );
}

function MethodRow({
  icon: Icon,
  label,
  value,
}: {
  icon?: LucideIcon;
  label: string;
  value: string;
}): React.ReactElement {
  return (
    <div className="flex items-center justify-between gap-3 px-3.5 py-2.5">
      <span className="flex items-center gap-2 text-[11.5px] text-muted-foreground">
        {Icon && <Icon className="h-3.5 w-3.5 text-muted-foreground/60" aria-hidden="true" />}
        {label}
      </span>
      <span className="min-w-0 truncate text-right text-[12px] font-semibold text-foreground">
        {value}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Interactive allocation donut
// ---------------------------------------------------------------------------

function AllocationDonut({
  slices,
  active,
  onActiveChange,
}: {
  slices: Slice[];
  active: string | null;
  onActiveChange: (key: string | null) => void;
}): React.ReactElement {
  const activeSlice = active ? slices.find((s) => s.key === active) ?? null : null;

  return (
    <div className="relative mx-auto h-[132px] w-full max-w-[150px]" data-testid="strategy-allocation-donut">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={slices}
            dataKey="pct"
            nameKey="label"
            cx="50%"
            cy="50%"
            innerRadius={42}
            outerRadius={60}
            paddingAngle={slices.length > 1 ? 1.5 : 0}
            stroke="none"
            startAngle={90}
            endAngle={-270}
            isAnimationActive={false}
            onMouseEnter={(_, i) => onActiveChange(slices[i]?.key ?? null)}
            onMouseLeave={() => onActiveChange(null)}
            onClick={(_, i) => {
              const k = slices[i]?.key ?? null;
              onActiveChange(active === k ? null : k);
            }}
          >
            {slices.map((s) => {
              const dim = active !== null && active !== s.key;
              return (
                <Cell
                  key={s.key}
                  fill={s.color}
                  fillOpacity={dim ? 0.28 : 1}
                  stroke={active === s.key ? "var(--background, #fff)" : "none"}
                  strokeWidth={active === s.key ? 2 : 0}
                  style={{ cursor: "pointer", transition: "fill-opacity 140ms ease" }}
                />
              );
            })}
          </Pie>
        </PieChart>
      </ResponsiveContainer>

      {/* Center readout — reflects the hovered/selected slice, else the basket */}
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
        {activeSlice ? (
          <>
            <span
              className="max-w-[76px] truncate text-[10px] font-semibold tracking-tight text-foreground"
              style={{ color: activeSlice.color }}
            >
              {activeSlice.label}
            </span>
            <span className="text-[17px] font-semibold leading-none tabular-nums text-foreground">
              {fmtPct(activeSlice.pct)}
            </span>
          </>
        ) : (
          <>
            <span className="text-[17px] font-semibold leading-none tabular-nums text-foreground">
              {slices.length}
            </span>
            <span className="mt-0.5 text-[10px] text-muted-foreground/70">
              {slices.length === 1 ? "Position" : "Positions"}
            </span>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Constituent row — compact, color-keyed to its donut slice, hover-linked
// ---------------------------------------------------------------------------

function ConstituentRow({
  c,
  index,
  color,
  maxWeight,
  active,
  onActiveChange,
}: {
  c: StrategyConstituent;
  index: number;
  color: string;
  /** Largest weight in the basket — the allocation bar scales relative to it
   *  so the biggest holding fills the track and the rest read proportionally. */
  maxWeight: number;
  active: boolean;
  onActiveChange: (active: boolean) => void;
}): React.ReactElement {
  const metricEntries = Object.entries(c.gate_metrics ?? {});
  const barPct = maxWeight > 0 ? Math.max(6, (c.weight_pct / maxWeight) * 100) : 0;
  return (
    <li
      onMouseEnter={() => onActiveChange(true)}
      onMouseLeave={() => onActiveChange(false)}
      className={cn(
        "rounded-xl px-2.5 py-2 transition-colors",
        active ? "bg-muted/50" : "hover:bg-muted/30",
      )}
      style={{
        animation: `stepIn-quartr 320ms cubic-bezier(0.22, 1, 0.36, 1) both`,
        animationDelay: `${index * 35}ms`,
        listStyle: "none",
      }}
    >
      <div className="flex items-center gap-3">
        <span
          aria-hidden="true"
          className="h-2.5 w-2.5 shrink-0 rounded-full transition-transform"
          style={{ background: color, transform: active ? "scale(1.2)" : undefined }}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate text-[13px] font-semibold tracking-tight text-foreground">
              {c.symbol}
              <span className="ml-2 text-[10px] font-normal uppercase tracking-wide text-muted-foreground/70">
                {c.sector}
              </span>
            </span>
            <span className="shrink-0 text-[12.5px] font-semibold tabular-nums text-foreground">
              {fmtPct(c.weight_pct)}
            </span>
          </div>
          <p className="mt-0.5 truncate text-[10.5px] leading-none text-muted-foreground/80">
            {c.name}
          </p>
          {/* Weight reason — always visible when present; a one-line
              rationale for why this name carries its allocated weight. */}
          {c.weight_reason && (
            <p className="mt-1 text-[10px] leading-snug text-muted-foreground/70">
              {c.weight_reason}
            </p>
          )}
          {/* Allocation bar — proportional, tinted to the slice color */}
          <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-muted/70">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${barPct}%`,
                background: color,
                opacity: active ? 1 : 0.75,
              }}
            />
          </div>
        </div>
      </div>
      {/* Gate metrics — revealed only for the active holding (the fundamentals
          that earned the slot), so the resting list stays clean */}
      {active && metricEntries.length > 0 && (
        <div
          className="mt-2 flex flex-wrap gap-x-3 gap-y-1 pl-[22px]"
          style={{
            animation: "draftCardIn-quartr 200ms cubic-bezier(0.22, 1, 0.36, 1) both",
          }}
        >
          {metricEntries.map(([k, v]) => (
            <span
              key={k}
              className="inline-flex items-center gap-1 rounded-md bg-muted/60 px-1.5 py-0.5 text-[10px] tabular-nums"
            >
              <span className="text-muted-foreground/70">{humanizeMetricKey(k)}</span>
              <span className="font-semibold text-foreground/90">{fmtMetric(k, v)}</span>
            </span>
          ))}
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Compact holding row — one line (dot · symbol · pct) for the right-hand
// column list; company/sector ride on the title attr, gate metrics live in
// the expandable Details section, keeping the resting card ~3in tall.
// ---------------------------------------------------------------------------

function CompactHoldingRow({
  c,
  color,
  active,
  onActiveChange,
}: {
  c: StrategyConstituent;
  color: string;
  active: boolean;
  onActiveChange: (active: boolean) => void;
}): React.ReactElement {
  return (
    <li
      onMouseEnter={() => onActiveChange(true)}
      onMouseLeave={() => onActiveChange(false)}
      title={`${c.name} · ${c.sector}`}
      className={cn(
        "flex items-center gap-2 rounded-md px-1.5 py-[3px] transition-colors",
        active ? "bg-muted/60" : "hover:bg-muted/40",
      )}
      style={{ listStyle: "none" }}
    >
      <span
        aria-hidden="true"
        className="h-2 w-2 shrink-0 rounded-full transition-transform"
        style={{ background: color, transform: active ? "scale(1.25)" : undefined }}
      />
      <span className="min-w-0 flex-1 truncate text-[11.5px] font-semibold tracking-tight text-foreground">
        {c.symbol}
      </span>
      <span className="shrink-0 text-[11px] font-semibold tabular-nums text-foreground">
        {fmtPct(c.weight_pct)}
      </span>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Sleeve row (gold this phase; options/hedge reserved)
// ---------------------------------------------------------------------------

function SleeveBlock({
  sleeve,
  color,
  active,
  onActiveChange,
}: {
  sleeve: Sleeve;
  color: string;
  active: boolean;
  onActiveChange: (active: boolean) => void;
}): React.ReactElement {
  const isGold = sleeve.kind === "gold";
  return (
    <div
      onMouseEnter={() => onActiveChange(true)}
      onMouseLeave={() => onActiveChange(false)}
      className={cn(
        "flex flex-col gap-1.5 rounded-xl border px-3 py-2.5 transition-colors",
        active
          ? "border-amber-300/80 bg-amber-50/70 dark:border-amber-500/30 dark:bg-amber-500/[0.08]"
          : "border-amber-200/60 bg-amber-50/40 dark:border-amber-500/20 dark:bg-amber-500/[0.05]",
      )}
    >
      <div className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="h-2.5 w-2.5 shrink-0 rounded-full transition-transform"
          style={{ background: color, transform: active ? "scale(1.25)" : undefined }}
        />
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
          {isGold ? (
            <Coins className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <Layers className="h-3.5 w-3.5" aria-hidden="true" />
          )}
        </span>
        <span className="flex-1 text-[12px] font-semibold capitalize tracking-tight text-foreground">
          {sleeve.kind} sleeve
        </span>
        <span className="shrink-0 rounded-md bg-amber-100 px-2 py-0.5 text-[12px] font-semibold tabular-nums text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
          {fmtPct(sleeve.pct)}
        </span>
      </div>
      {sleeve.note && (
        <p className="text-[10.5px] leading-snug text-muted-foreground">{sleeve.note}</p>
      )}
      {sleeve.instruments.length > 0 && (
        <ul className="m-0 flex flex-col gap-1 pl-0.5">
          {sleeve.instruments.map((inst) => (
            <li
              key={inst.symbol}
              className="flex items-center gap-2 text-[11px]"
              style={{ listStyle: "none" }}
            >
              <span className="inline-flex items-center rounded bg-amber-100/80 px-1.5 py-px text-[9.5px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
                {goldKindLabel(inst.kind)}
              </span>
              <span className="min-w-0 flex-1 truncate text-foreground/85">
                {inst.symbol} · {inst.name}
              </span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {fmtPct(inst.weight_pct)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Alternatives block — "you might prefer this instead" pivots
// ---------------------------------------------------------------------------

function AlternativeItem({
  alt,
  index,
}: {
  alt: StrategyAlternative;
  index: number;
}): React.ReactElement {
  return (
    <li
      className="flex gap-2.5"
      style={{
        listStyle: "none",
        animation: "stepIn-quartr 320ms cubic-bezier(0.22, 1, 0.36, 1) both",
        animationDelay: `${index * 55}ms`,
      }}
    >
      <span
        aria-hidden="true"
        className="mt-[3px] flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300"
      >
        <GitBranch className="h-2.5 w-2.5" />
      </span>
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-[12px] font-semibold leading-snug tracking-tight text-foreground">
          {alt.title}
        </span>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {alt.detail}
        </p>
      </div>
    </li>
  );
}

function AlternativesBlock({
  alternatives,
}: {
  alternatives: StrategyAlternative[];
}): React.ReactElement {
  return (
    <div className="border-t border-border/30 px-5 py-3" data-testid="strategy-alternatives">
      <p className="mb-2.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/70">
        <GitBranch className="h-3 w-3 text-muted-foreground/60" aria-hidden="true" />
        Alternative strategies
      </p>
      <ul className="m-0 flex flex-col gap-2.5">
        {alternatives.map((alt, i) => (
          <AlternativeItem key={`${alt.title}-${i}`} alt={alt} index={i} />
        ))}
      </ul>
      <p className="mt-2.5 text-[10px] leading-snug text-muted-foreground/70">
        Suggestions only — reply with one (e.g. &ldquo;{alternatives[0]?.title ?? "the value tilt"}
        &rdquo;) to rebuild the basket that way.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// StrategyBuilderCard
// ---------------------------------------------------------------------------

export function StrategyBuilderCard({
  card,
  onBacktest,
}: StrategyBuilderCardProps): React.ReactElement {
  const [detailsOpen, setDetailsOpen] = useState(false);
  // The hovered/selected allocation slice key, shared by the donut and the
  // holdings/sleeve rows so the two stay in sync in both directions.
  const [active, setActive] = useState<string | null>(null);

  // ── Card actions (Save as basket → Deploy / Backtest) ────────────────────
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<EquityBasket | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [tradeOpen, setTradeOpen] = useState(false);

  // B4: on save, a gold sleeve contributes its LISTED proxy (the Gold ETF, e.g.
  // GOLDBEES) as a real basket member; the SGB leg is not exchange-tradeable as
  // a basket order, so it's omitted with a note (never silently included).
  const goldEtfMembers = useMemo(
    () =>
      card.sleeves
        .filter((s) => s.kind === "gold")
        .flatMap((s) => s.instruments)
        .filter((inst) => inst.kind === "etf" && !!inst.symbol)
        .map((inst) => ({ symbol: inst.symbol, weight: inst.weight_pct })),
    [card.sleeves],
  );
  const omittedSleeveLegs = useMemo(
    () =>
      card.sleeves
        .filter((s) => s.kind === "gold")
        .flatMap((s) => s.instruments)
        .filter((inst) => inst.kind !== "etf")
        .map((inst) => inst.symbol),
    [card.sleeves],
  );

  const canSave = card.constituents.length > 0;

  async function handleSave(): Promise<void> {
    if (saving || saved || !canSave) return;
    setSaving(true);
    setSaveError(null);
    const members = [
      ...card.constituents.map((c) => ({ symbol: c.symbol, weight: c.weight_pct })),
      ...goldEtfMembers,
    ];
    const res = await createEquityBasket({
      name: card.title.slice(0, 120) || "Strategy basket",
      members,
      weighting: "custom",
      capital_inr: card.capital_inr ?? undefined,
    });
    setSaving(false);
    if (isError(res)) {
      setSaveError(res.error.message);
      return;
    }
    setSaved(res.data);
  }

  // The Backtest action asks the chat pipeline to backtest this exact basket,
  // buy-and-hold (the Wave-A run_at + allocate_basket / hold-to-end shape). We
  // include the named legs so the pipeline builds THIS basket, not a guess.
  const backtestMessage = useMemo(() => {
    const legs = card.constituents
      .map((c) => `${c.symbol} ${fmtPct(c.weight_pct)}`)
      .join(", ");
    return (
      "Backtest this basket buy-and-hold over the last 5 years — hold to end, " +
      `no rebalancing. Constituents: ${legs}.`
    );
  }, [card.constituents]);

  const schemeLabel = SCHEME_LABEL[card.weighting_scheme] ?? card.weighting_scheme;
  const gateLabel = GATE_LABEL[card.selection_gate] ?? card.selection_gate;

  // Stable per-line colors + the flattened donut slices (equity then sleeves).
  const { slices, constituentColors, sleeveColors } = useMemo(() => {
    const cColors = card.constituents.map(
      (_, i) => SLICE_PALETTE[i % SLICE_PALETTE.length]!,
    );
    const sColors = card.sleeves.map(() => GOLD_COLOR);
    const out: Slice[] = [
      ...card.constituents.map((c, i) => ({
        key: `c:${c.symbol}`,
        label: c.symbol,
        sub: c.name,
        pct: c.weight_pct,
        color: cColors[i]!,
      })),
      ...card.sleeves.map((s, i) => ({
        key: `s:${i}`,
        label: `${s.kind[0]!.toUpperCase()}${s.kind.slice(1)} sleeve`,
        sub: s.note ?? "",
        pct: s.pct,
        color: sColors[i]!,
      })),
    ];
    return { slices: out, constituentColors: cColors, sleeveColors: sColors };
  }, [card.constituents, card.sleeves]);

  const holdings = card.constituents.length;
  const sectorCount = new Set(card.constituents.map((c) => c.sector)).size;
  const topWeight = card.constituents.reduce((m, c) => Math.max(m, c.weight_pct), 0);
  // Right-hand list wraps into a second column once it would outgrow the
  // donut column (~7 compact rows) — per the compact-card design.
  const holdingRows = Math.ceil(holdings / (holdings > 7 ? 2 : 1));

  // Sector composition — aggregate constituent weights by sector, heaviest
  // first, for the "Composition" breakdown in the details panel.
  const sectorBreakdown = useMemo(() => {
    const totals = new Map<string, number>();
    for (const c of card.constituents) {
      totals.set(c.sector, (totals.get(c.sector) ?? 0) + c.weight_pct);
    }
    return [...totals.entries()]
      .map(([sector, pct]) => ({ sector, pct }))
      .sort((a, b) => b.pct - a.pct)
      .map((s, i) => ({ ...s, color: SLICE_PALETTE[i % SLICE_PALETTE.length]! }));
  }, [card.constituents]);
  const sectorTotal = sectorBreakdown.reduce((sum, s) => sum + s.pct, 0);

  return (
    <div
      data-testid="strategy-builder-card"
      role="region"
      aria-label={`Strategy basket: ${card.title}`}
      className={cn(
        "my-2 w-full max-w-[600px] overflow-hidden rounded-3xl border border-border/50 bg-card",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
      )}
      style={{
        animation: "draftCardIn-quartr 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
      }}
    >
      {/* HEADER — title only; everything else lives behind Details */}
      <div className="px-5 pt-4 pb-2.5">
        <h3 className="truncate text-[16px] leading-[1.2] font-semibold tracking-tight text-foreground">
          {card.title}
        </h3>
      </div>

      {/* ALLOCATION — donut + division stats on the LEFT, the holdings list
          vertically on the RIGHT (wrapping into a second column when long) */}
      {slices.length > 0 && (
        <div className="flex flex-col items-stretch gap-4 border-t border-border/30 px-5 py-3 sm:flex-row sm:items-start">
          <div className="w-full shrink-0 sm:w-[168px]">
            <AllocationDonut slices={slices} active={active} onActiveChange={setActive} />
            {holdings > 0 && (
              <div className="mt-3.5 grid grid-cols-3 gap-2">
                <QuickStat label="Holdings" value={String(holdings)} />
                <QuickStat label="Sectors" value={String(sectorCount)} />
                <QuickStat label="Largest" value={fmtPct(topWeight)} />
              </div>
            )}
          </div>
          {card.constituents.length > 0 && (
            // Phone: full-width two-column list under the donut (a single
            // column would run absurdly long, and the old flow-col layout ran
            // off-screen). Desktop (sm+): the original flow-down-then-across
            // list beside the donut, driven by --bs-rows.
            <ol
              className={cn(
                "m-0 grid w-full min-w-0 grid-cols-2 gap-x-3 self-center",
                "sm:w-auto sm:flex-1 sm:grid-flow-col sm:grid-cols-none",
                "sm:[grid-template-rows:repeat(var(--bs-rows),minmax(0,auto))]",
              )}
              style={{ ["--bs-rows" as string]: String(holdingRows) }}
            >
              {card.constituents.map((c, i) => {
                const key = `c:${c.symbol}`;
                return (
                  <CompactHoldingRow
                    key={c.symbol}
                    c={c}
                    color={constituentColors[i]!}
                    active={active === key}
                    onActiveChange={(on) => setActive(on ? key : null)}
                  />
                );
              })}
            </ol>
          )}
        </div>
      )}

      {/* DETAILS — opens a right-side panel (same slide-in sheet pattern as the
          backtest / workflow-editor drawers) holding scheme/gate/cap, rationale,
          per-name fundamentals, sleeves, assumptions and alternatives, so the
          resting card stays compact and premium */}
      <Sheet open={detailsOpen} onOpenChange={setDetailsOpen} modal={false}>
        <SheetContent
          side="right"
          aria-describedby={undefined}
          // Matched to the backtest / agent-editor side panels: a border-l,
          // shadow-xl right surface at a proportional width, non-modal so the
          // chat stays visible; the sheet's default chrome is dropped for a
          // slim custom header.
          style={{ width: "clamp(340px, 25vw, 520px)", maxWidth: "100%" }}
          overlayClassName="basket-sheet-overlay bg-transparent pointer-events-none"
          onInteractOutside={(e) => e.preventDefault()}
          className="basket-sheet-shell flex flex-col gap-0 border-l bg-background p-0 shadow-xl [&>button.opacity-70]:hidden"
        >
          <SheetTitle className="sr-only">{card.title} — details</SheetTitle>

          {/* Header — sticky slim bar with title + ghost close */}
          <div className="sticky top-0 z-10 flex shrink-0 items-start justify-between gap-3 border-b border-border/40 bg-background/95 px-5 py-4 backdrop-blur">
            <div className="min-w-0">
              <p className="text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/55">
                Basket
              </p>
              <h4 className="mt-0.5 truncate text-[16px] font-semibold tracking-tight text-foreground">
                {card.title}
              </h4>
            </div>
            <SheetClose asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Close details"
                className="-mr-1 shrink-0 rounded-full"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </Button>
            </SheetClose>
          </div>

          {/* Body — scrollable, evenly sectioned with a consistent rhythm */}
          <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
            {/* Overview — numeric stat strip + how-it's-built key/values */}
            <section className="flex flex-col gap-3 px-5 py-4">
              {holdings > 0 && (
                <div className="grid grid-cols-3 divide-x divide-border/30">
                  <PanelStat label="Holdings" value={String(holdings)} />
                  <PanelStat label="Sectors" value={String(sectorCount)} />
                  <PanelStat label="Largest" value={fmtPct(topWeight)} />
                </div>
              )}
              <div className="flex flex-col divide-y divide-border/30">
                <div data-testid="strategy-scheme">
                  <MethodRow icon={Layers} label="Weighting" value={schemeLabel} />
                </div>
                <div data-testid="strategy-gate">
                  <MethodRow icon={Sparkles} label="Selection gate" value={gateLabel} />
                </div>
                <MethodRow icon={ShieldAlert} label="Sector cap" value={fmtPct(card.sector_cap)} />
              </div>
            </section>

            {/* Rationale */}
            {card.rationale && (
              <section className="border-t border-border/30 px-5 py-4">
                <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.13em] text-muted-foreground/55">
                  Rationale
                </p>
                <p
                  className="text-[12px] leading-relaxed text-muted-foreground"
                  data-testid="strategy-rationale"
                >
                  {card.rationale}
                </p>
              </section>
            )}

            {/* Composition — one segmented allocation bar by sector + legend */}
            {sectorBreakdown.length > 1 && sectorTotal > 0 && (
              <section className="border-t border-border/30 px-5 py-4">
                <p className="mb-3 text-[10px] font-semibold uppercase tracking-[0.13em] text-muted-foreground/55">
                  Composition
                </p>
                <div className="flex h-2.5 w-full overflow-hidden rounded-full">
                  {sectorBreakdown.map((s) => (
                    <div
                      key={s.sector}
                      className="h-full first:rounded-l-full last:rounded-r-full"
                      style={{
                        width: `${(s.pct / sectorTotal) * 100}%`,
                        background: s.color,
                      }}
                      title={`${s.sector} · ${fmtPct(s.pct)}`}
                    />
                  ))}
                </div>
                <div className="mt-3 grid grid-cols-2 gap-x-5 gap-y-2">
                  {sectorBreakdown.map((s) => (
                    <div key={s.sector} className="flex items-center gap-2 text-[11px]">
                      <span
                        aria-hidden="true"
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{ background: s.color }}
                      />
                      <span className="min-w-0 flex-1 truncate uppercase tracking-wide text-muted-foreground">
                        {s.sector}
                      </span>
                      <span className="shrink-0 font-semibold tabular-nums text-foreground">
                        {fmtPct(s.pct)}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Holdings */}
            {card.constituents.length > 0 && (
              <section className="border-t border-border/30 px-5 py-4">
                <div className="mb-2.5 flex items-baseline justify-between">
                  <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-muted-foreground/55">
                    Holdings
                  </p>
                  <span className="text-[10.5px] font-medium tabular-nums text-muted-foreground/60">
                    {card.constituents.length}
                  </span>
                </div>
                <ol className="m-0 flex flex-col gap-0.5">
                  {card.constituents.map((c, i) => {
                    const key = `c:${c.symbol}`;
                    return (
                      <ConstituentRow
                        key={c.symbol}
                        c={c}
                        index={i}
                        color={constituentColors[i]!}
                        maxWeight={topWeight}
                        active={active === key}
                        onActiveChange={(on) => setActive(on ? key : null)}
                      />
                    );
                  })}
                </ol>
              </section>
            )}

            {/* Sleeves */}
            {card.sleeves.length > 0 && (
              <section className="flex flex-col gap-2.5 border-t border-border/30 px-5 py-4">
                <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-muted-foreground/55">
                  Sleeves
                </p>
                <div className="flex flex-col gap-2">
                  {card.sleeves.map((s, i) => {
                    const key = `s:${i}`;
                    return (
                      <SleeveBlock
                        key={`${s.kind}-${i}`}
                        sleeve={s}
                        color={sleeveColors[i]!}
                        active={active === key}
                        onActiveChange={(on) => setActive(on ? key : null)}
                      />
                    );
                  })}
                </div>
              </section>
            )}

            {/* Assumptions */}
            {card.assumptions.length > 0 && (
              <section className="border-t border-border/30 px-5 py-4">
                <p className="mb-2.5 text-[10px] font-semibold uppercase tracking-[0.13em] text-muted-foreground/55">
                  Assumptions
                </p>
                <ul className="m-0 flex flex-col gap-2" data-testid="strategy-assumptions">
                  {card.assumptions.map((a, i) => (
                    <li key={i} className="flex items-start gap-2" style={{ listStyle: "none" }}>
                      <Info
                        className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground/50"
                        aria-hidden="true"
                      />
                      <span className="block text-[11.5px] leading-snug text-muted-foreground first-letter:uppercase">
                        {a}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Alternatives */}
            {card.alternatives.length > 0 && (
              <AlternativesBlock alternatives={card.alternatives} />
            )}

            {/* Edit hint */}
            <div className="border-t border-border/30 bg-muted/20 px-5 py-4">
              <p className="text-[11px] leading-snug text-muted-foreground/90">
                This is a draft you can edit — reply to re-weight, swap a name, change
                the scheme, or resize the gold sleeve. Pivot registers the idea; you
                confirm and place orders in your own broker app.
              </p>
            </div>
          </div>
        </SheetContent>
      </Sheet>

      {/* ACTIONS — one primary pill (Save → Deploy) with secondary actions as
          ghost links beneath, matching the other draft cards' CTA rail */}
      {canSave && (
        <div className="flex flex-col gap-2.5 border-t border-border/40 px-5 py-3.5">
          {/* PRIMARY CTA — morphs from Save to Deploy once the basket is saved */}
          {saved ? (
            <button
              type="button"
              onClick={() => setTradeOpen(true)}
              data-testid="strategy-deploy"
              title="Deploy this basket"
              className={cn(
                "inline-flex h-8 w-full items-center justify-center gap-2 rounded-full bg-primary text-[12px] font-medium tracking-tight text-primary-foreground transition-all",
                "hover:bg-primary/90 active:scale-[0.98]",
              )}
            >
              <Zap className="h-4 w-4 shrink-0" aria-hidden="true" />
              Deploy basket
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              data-testid="strategy-save"
              className={cn(
                "inline-flex h-8 w-full items-center justify-center gap-2 rounded-full bg-primary text-[12px] font-medium tracking-tight text-primary-foreground transition-all",
                "hover:bg-primary/90 active:scale-[0.98]",
                "disabled:cursor-not-allowed disabled:opacity-70",
              )}
            >
              {saving ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                  Saving…
                </>
              ) : (
                <>
                  <Save className="h-4 w-4 shrink-0" aria-hidden="true" />
                  Save as basket
                </>
              )}
            </button>
          )}

          {/* SECONDARY — ghost links: saved state · backtest · details */}
          <div className="flex items-center justify-center gap-1 text-[11.5px]">
            {saved && (
              <>
                <span
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 font-medium text-emerald-600 dark:text-emerald-400"
                  data-testid="strategy-saved-badge"
                  title="Find it in Agents → Strategies"
                >
                  <Check className="h-3 w-3 shrink-0" aria-hidden="true" />
                  Saved
                </span>
                <span className="text-muted-foreground/40">·</span>
              </>
            )}
            {onBacktest && (
              <>
                <button
                  type="button"
                  onClick={() => onBacktest(backtestMessage)}
                  data-testid="strategy-backtest"
                  className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                >
                  <History className="h-3 w-3 shrink-0" aria-hidden="true" />
                  Backtest
                </button>
                <span className="text-muted-foreground/40">·</span>
              </>
            )}
            <button
              type="button"
              onClick={() => setDetailsOpen(true)}
              data-testid="strategy-why-toggle"
              className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
            >
              Details
              <ChevronRight className="h-3 w-3 shrink-0" aria-hidden="true" />
            </button>
          </div>

          {omittedSleeveLegs.length > 0 && saved && (
            <p className="text-[10px] leading-snug text-muted-foreground/80">
              Saved with {goldEtfMembers.map((m) => m.symbol).join(", ") || "the listed gold ETF"} for
              the gold sleeve. {omittedSleeveLegs.join(", ")} (not exchange-traded as a basket order)
              wasn&apos;t added — buy it directly if you want it.
            </p>
          )}

          {saveError && (
            <p role="alert" className="text-[10.5px] leading-snug text-destructive">
              {saveError}
            </p>
          )}
        </div>
      )}

      {saved && (
        <BasketTradeModal open={tradeOpen} onOpenChange={setTradeOpen} basket={saved} />
      )}

      {/* DISCLAIMER — not-advice footer */}
      <div className="flex items-start gap-1.5 border-t border-border/40 bg-amber-50/40 px-5 py-2 dark:bg-amber-500/[0.04]">
        <ShieldAlert
          className="mt-px h-3 w-3 shrink-0 text-amber-600/80 dark:text-amber-400/80"
          aria-hidden="true"
        />
        <p className="text-[10.5px] leading-snug text-amber-700/90 dark:text-amber-300/90">
          {card.disclaimer}
        </p>
      </div>
    </div>
  );
}
