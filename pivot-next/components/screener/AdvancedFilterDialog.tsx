"use client";

import { useMemo, useState } from "react";
import {
  Activity, BarChart3, Check, ChevronRight, CircleGauge, LineChart,
  Plus, Search, SlidersHorizontal, TrendingUp, X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import type { ScreenerFilterClause } from "@/lib/screenerApi";

type CategoryId = "popular" | "market" | "valuation" | "quality" | "performance" | "technical" | "volume_profile";
type Metric = {
  field: string;
  label: string;
  category: CategoryId;
  unit: string;
  description: string;
  source: string;
  suggested?: Array<{ label: string; op: ScreenerFilterClause["op"]; value: number; value2?: number }>;
};

const CATEGORIES: Array<{ id: CategoryId; label: string; subhead: string; icon: typeof Activity }> = [
  { id: "popular", label: "Popular", subhead: "The filters investors reach for first", icon: SlidersHorizontal },
  { id: "market", label: "Market data", subhead: "Price, size, liquidity and today’s move", icon: BarChart3 },
  { id: "valuation", label: "Valuation", subhead: "How the market prices current earnings", icon: CircleGauge },
  { id: "quality", label: "Quality & balance sheet", subhead: "Returns on capital and financial leverage", icon: TrendingUp },
  { id: "performance", label: "Performance", subhead: "Measured price returns and 52-week position", icon: LineChart },
  { id: "technical", label: "Technical", subhead: "Charto momentum, trend, volatility and participation", icon: Activity },
  { id: "volume_profile", label: "Volume profile", subhead: "Charto’s 20-session accepted-value structure", icon: BarChart3 },
];

const M: Metric[] = [
  { field: "market_cap_cr", label: "Market capitalisation", category: "market", unit: "₹ crore", description: "Current equity value in Indian rupees crore.", source: "Pivot Enrich", suggested: [{ label: "Above ₹50K Cr", op: "gte", value: 50000 }, { label: "₹20K–50K Cr", op: "between", value: 20000, value2: 50000 }] },
  { field: "price", label: "Price", category: "market", unit: "₹", description: "Latest available NSE price; Kite is primary and delayed data is labelled.", source: "Kite / delayed relay" },
  { field: "change_pct", label: "Price change (intraday)", category: "market", unit: "%", description: "Move from the previous close to the latest price.", source: "Kite / delayed relay", suggested: [{ label: "Gainers", op: "gt", value: 0 }, { label: "Above 3%", op: "gte", value: 3 }, { label: "Losers", op: "lt", value: 0 }] },
  { field: "volume", label: "Volume", category: "market", unit: "shares", description: "Shares traded in the current session.", source: "Kite / delayed relay" },
  { field: "turnover_20d_cr", label: "Average daily turnover (20D)", category: "market", unit: "₹ crore", description: "Average close × volume across 20 sessions.", source: "Charto daily bars" },
  { field: "pe", label: "Price / earnings", category: "valuation", unit: "x", description: "Trailing price-to-earnings ratio from the latest usable statement.", source: "Moneycontrol financials", suggested: [{ label: "Below 15x", op: "lt", value: 15 }, { label: "15–25x", op: "between", value: 15, value2: 25 }] },
  { field: "roe", label: "Return on equity", category: "quality", unit: "%", description: "Profit generated as a percentage of shareholder equity.", source: "Moneycontrol financials", suggested: [{ label: "Above 15%", op: "gte", value: 15 }, { label: "Above 20%", op: "gte", value: 20 }] },
  { field: "roce", label: "Return on capital employed", category: "quality", unit: "%", description: "Operating return generated on long-term capital employed.", source: "Moneycontrol financials" },
  { field: "de", label: "Debt / equity", category: "quality", unit: "x", description: "Total debt relative to shareholder equity.", source: "Moneycontrol financials", suggested: [{ label: "Below 0.5x", op: "lt", value: 0.5 }, { label: "Below 1x", op: "lt", value: 1 }] },
  { field: "one_year_pct", label: "One-year return", category: "performance", unit: "%", description: "Price return over approximately 252 trading sessions.", source: "Kite historical / delayed relay" },
  { field: "dist_52w_high", label: "Distance from 52-week high", category: "performance", unit: "%", description: "Signed distance from the high: 0 is at the high; negative is below it.", source: "Charto daily bars", suggested: [{ label: "Within 5%", op: "gte", value: -5 }, { label: "Within 10%", op: "gte", value: -10 }] },
  { field: "dist_52w_low", label: "Distance above 52-week low", category: "performance", unit: "%", description: "Percentage the latest close sits above its 52-week low.", source: "Charto daily bars" },
  { field: "rsi14", label: "RSI (14)", category: "technical", unit: "0–100", description: "Daily relative-strength index over 14 sessions.", source: "Charto indicator engine", suggested: [{ label: "Oversold", op: "lt", value: 30 }, { label: "30–70", op: "between", value: 30, value2: 70 }, { label: "Overbought", op: "gt", value: 70 }] },
  { field: "sma20_rel", label: "Price vs 20-day SMA", category: "technical", unit: "%", description: "Positive means the close is above its 20-day moving average.", source: "Charto indicator engine" },
  { field: "sma50_rel", label: "Price vs 50-day SMA", category: "technical", unit: "%", description: "Positive means the close is above its 50-day moving average.", source: "Charto indicator engine" },
  { field: "sma200_rel", label: "Price vs 200-day SMA", category: "technical", unit: "%", description: "Positive means the close is above its 200-day moving average.", source: "Charto indicator engine", suggested: [{ label: "Above 200D", op: "gt", value: 0 }, { label: "Below 200D", op: "lt", value: 0 }] },
  { field: "atr_pct", label: "ATR (14) / price", category: "technical", unit: "%", description: "Average true range as a percentage of price; comparable across stocks.", source: "Charto indicator engine" },
  { field: "range_20d_pct", label: "20-day range width", category: "technical", unit: "%", description: "High-to-low width over 20 sessions; lower values identify tighter coils.", source: "Charto daily bars" },
  { field: "vol_z20", label: "Relative volume z-score", category: "technical", unit: "σ", description: "Latest volume measured in standard deviations from the prior 20 sessions.", source: "Charto daily bars", suggested: [{ label: "Unusual (>2σ)", op: "gt", value: 2 }, { label: "Above average", op: "gt", value: 0 }] },
  { field: "vp20_pos", label: "Position inside value area", category: "volume_profile", unit: "%", description: "0 is value-area low, 100 is high; above 100 trades above accepted value.", source: "Charto 1-minute volume profile", suggested: [{ label: "Above value", op: "gt", value: 100 }, { label: "Inside value", op: "between", value: 0, value2: 100 }, { label: "Below value", op: "lt", value: 0 }] },
  { field: "vp20_va_width_pct", label: "Value-area width", category: "volume_profile", unit: "% of POC", description: "Width of the 20-session value area relative to its point of control.", source: "Charto 1-minute volume profile" },
  { field: "vp20_poc_dist_pct", label: "Distance from point of control", category: "volume_profile", unit: "%", description: "Signed distance between close and the most-traded price.", source: "Charto 1-minute volume profile" },
  { field: "vp20_poc_shift_pct", label: "Point-of-control migration", category: "volume_profile", unit: "%", description: "Change in accepted price versus the prior 20-session profile.", source: "Charto 1-minute volume profile" },
];

const POPULAR = ["market_cap_cr", "change_pct", "pe", "roe", "de", "one_year_pct", "dist_52w_high", "rsi14", "sma200_rel", "vol_z20"];
const OP_LABEL: Record<ScreenerFilterClause["op"], string> = { gt: "More than", gte: "At least", lt: "Less than", lte: "At most", eq: "Equal to", between: "Between" };

function metricFor(field: string): Metric | undefined { return M.find((m) => m.field === field); }

export function AdvancedFilterDialog({ value, onChange }: { value: ScreenerFilterClause[]; onChange: (filters: ScreenerFilterClause[]) => void }): React.ReactElement {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<ScreenerFilterClause[]>(value);
  const [category, setCategory] = useState<CategoryId>("popular");
  const [selected, setSelected] = useState<string>("market_cap_cr");
  const [search, setSearch] = useState("");

  const metrics = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (q) return M.filter((m) => `${m.label} ${m.description} ${m.source}`.toLowerCase().includes(q));
    if (category === "popular") return POPULAR.map(metricFor).filter(Boolean) as Metric[];
    return M.filter((m) => m.category === category);
  }, [category, search]);
  const metric = metricFor(selected) ?? metrics[0] ?? M[0]!;
  const current = draft.find((f) => f.field === metric.field);
  const clause = current ?? { field: metric.field, op: "gte" as const, value: 0 };
  const upsert = (next: ScreenerFilterClause) => setDraft((all) => [...all.filter((f) => f.field !== next.field), next]);
  const remove = (field: string) => setDraft((all) => all.filter((f) => f.field !== field));

  return (
    <Dialog open={open} onOpenChange={(next) => { setOpen(next); if (next) setDraft(value); }}>
      <DialogTrigger asChild>
        <Button variant="outline" className="h-[34px] rounded-full border-border bg-background px-3 text-xs font-medium shadow-none hover:border-foreground/30 hover:bg-muted/60">
          <Plus className="mr-1.5 h-3.5 w-3.5" /> All filters
          {value.length > 0 && <span className="ml-2 rounded-full bg-foreground px-1.5 py-0.5 text-[10px] text-background">{value.length}</span>}
        </Button>
      </DialogTrigger>
      <DialogContent className="gap-0 overflow-hidden border-border bg-background p-0 font-[var(--font-ui)] shadow-[0_24px_70px_-24px_rgba(0,0,0,0.38)] sm:h-[min(600px,82vh)] sm:max-w-[920px] sm:rounded-xl">
        <div className="border-b border-border px-5 pb-4 pt-4">
          <DialogTitle className="font-[var(--font-display)] text-[19px] font-medium leading-6 tracking-[-0.02em]">Equity filters</DialogTitle>
          <DialogDescription className="mt-0.5 text-[11px] leading-4 text-muted-foreground">Combine market, fundamental and Charto criteria. All conditions must pass.</DialogDescription>
          <div className="mt-3 flex h-10 items-center gap-2.5 rounded-lg border border-border bg-muted/30 px-3 transition-colors focus-within:border-foreground/25 focus-within:bg-background">
            <Search className="h-3.5 w-3.5 text-muted-foreground" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search for filters" className="h-full flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground" />
            {search && <button className="rounded p-1 hover:bg-muted" onClick={() => setSearch("")} aria-label="Clear search"><X className="h-3.5 w-3.5 text-muted-foreground hover:text-foreground" /></button>}
          </div>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[160px_200px_minmax(330px,1fr)] divide-y divide-border sm:grid-cols-[190px_278px_minmax(0,1fr)] sm:grid-rows-1 sm:divide-x sm:divide-y-0">
          <nav className="overflow-y-auto p-2">
            {draft.length > 0 && <button onClick={() => { setSearch(""); setCategory("popular"); setSelected(draft[0]!.field); }} className="mb-1 flex h-9 w-full items-center justify-between rounded-md px-2.5 text-left text-[12px] font-medium transition-colors hover:bg-muted"><span>Active filters</span><span className="min-w-5 rounded-full bg-sky-500/15 px-1.5 py-0.5 text-center text-[10px] text-sky-600 dark:text-sky-300">{draft.length}</span></button>}
            {CATEGORIES.map((c) => { const Icon = c.icon; const active = !search && category === c.id; return <button key={c.id} onClick={() => { setSearch(""); setCategory(c.id); const first = c.id === "popular" ? POPULAR[0] : M.find((m) => m.category === c.id)?.field; if (first) setSelected(first); }} className={`group mb-0.5 flex min-h-10 w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors ${active ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}><Icon className="h-3.5 w-3.5 shrink-0"/><span className="min-w-0 flex-1"><span className="block text-[12px] font-medium leading-4">{c.label}</span><span className={`block truncate text-[9px] leading-3 ${active ? "text-background/55" : "text-muted-foreground"}`}>{c.subhead}</span></span><ChevronRight className="h-3 w-3 opacity-40 transition-transform group-hover:translate-x-0.5"/></button>; })}
          </nav>

          <section className="overflow-y-auto p-2">
            <div className="px-2.5 pb-1.5 pt-1 text-[9px] font-semibold uppercase tracking-[0.13em] text-muted-foreground">{search ? `${metrics.length} matching filters` : CATEGORIES.find((c) => c.id === category)?.label}</div>
            {metrics.map((m) => { const active = selected === m.field; const applied = draft.some((f) => f.field === m.field); return <button key={m.field} onClick={() => setSelected(m.field)} className={`group mb-0.5 flex min-h-[46px] w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-left transition-all ${active ? "border-foreground/20 bg-muted/70" : "border-transparent hover:border-border hover:bg-muted/40"}`}><span className={`flex h-6 w-6 items-center justify-center rounded-full border ${applied ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-600" : "border-border text-muted-foreground"}`}>{applied ? <Check className="h-3 w-3"/> : <Activity className="h-3 w-3"/>}</span><span className="min-w-0 flex-1"><span className="block truncate text-[12px] font-medium leading-4 text-foreground">{m.label}</span><span className="block truncate text-[9.5px] leading-3.5 text-muted-foreground">{m.unit} · {m.source}</span></span><ChevronRight className="h-3 w-3 text-muted-foreground transition-transform group-hover:translate-x-0.5"/></button>; })}
          </section>

          <section className="overflow-y-auto p-5">
            <div className="text-[9px] font-semibold uppercase tracking-[0.13em] text-muted-foreground">{CATEGORIES.find((c) => c.id === metric.category)?.label}</div>
            <h3 className="mt-1.5 font-[var(--font-display)] text-[17px] font-medium leading-5 tracking-[-0.015em]">{metric.label}</h3>
            <p className="mt-1.5 max-w-lg text-[11.5px] leading-[1.55] text-muted-foreground">{metric.description}</p>
            <div className="mt-2 inline-flex rounded-full border border-border bg-muted/35 px-2 py-0.5 text-[9px] leading-4 text-muted-foreground">Source · {metric.source}</div>

            {metric.suggested && <div className="mt-4"><div className="mb-1.5 text-[10px] font-medium text-muted-foreground">Quick ranges</div><div className="flex flex-wrap gap-1.5">{metric.suggested.map((s) => <button key={s.label} onClick={() => upsert({ field: metric.field, op: s.op, value: s.value, value2: s.value2 })} className="h-7 rounded-full border border-border px-2.5 text-[10.5px] transition-all hover:-translate-y-px hover:border-foreground/30 hover:bg-muted">{s.label}</button>)}</div></div>}

            <div className="mt-4 rounded-lg border border-border bg-muted/15 p-3">
              <div className="grid grid-cols-[118px_1fr] gap-2">
                <select aria-label="Filter operator" value={clause.op} onChange={(e) => upsert({ ...clause, op: e.target.value as ScreenerFilterClause["op"] })} className="h-9 rounded-md border border-border bg-background px-2.5 text-[11px] outline-none transition-colors focus:border-foreground/30">{Object.entries(OP_LABEL).map(([op, label]) => <option key={op} value={op}>{label}</option>)}</select>
                <div className="flex h-9 items-center rounded-md border border-border bg-background px-2.5 transition-colors focus-within:border-foreground/30"><input aria-label={`${metric.label} value`} type="number" value={clause.value} onChange={(e) => upsert({ ...clause, value: Number(e.target.value) })} className="min-w-0 flex-1 bg-transparent text-right font-mono text-[12px] tabular-nums outline-none"/><span className="ml-2 text-[10px] text-muted-foreground">{metric.unit}</span></div>
              </div>
              {clause.op === "between" && <div className="mt-2 flex items-center gap-2"><span className="w-[118px] text-right text-[10px] text-muted-foreground">and</span><div className="flex h-9 flex-1 items-center rounded-md border border-border bg-background px-2.5 focus-within:border-foreground/30"><input aria-label={`${metric.label} upper value`} type="number" value={clause.value2 ?? clause.value} onChange={(e) => upsert({ ...clause, value2: Number(e.target.value) })} className="min-w-0 flex-1 bg-transparent text-right font-mono text-[12px] tabular-nums outline-none"/><span className="ml-2 text-[10px] text-muted-foreground">{metric.unit}</span></div></div>}
              <div className="mt-3 flex items-center justify-between gap-3"><span className="text-[9.5px] leading-4 text-muted-foreground">Stocks without this value are excluded.</span>{current && <button onClick={() => remove(metric.field)} className="shrink-0 text-[10px] text-muted-foreground hover:text-destructive">Remove</button>}</div>
            </div>

            {draft.length > 0 && <div className="mt-4"><div className="mb-1.5 text-[10px] font-medium text-muted-foreground">Active filters</div><div className="flex flex-wrap gap-1.5">{draft.map((f) => { const m = metricFor(f.field); return <button key={f.field} onClick={() => { setSelected(f.field); setCategory(m?.category ?? "popular"); }} className="group inline-flex h-7 max-w-full items-center gap-1.5 rounded-full border border-border bg-background px-2.5 text-[10px] hover:border-foreground/30"><span className="truncate">{m?.label} · {OP_LABEL[f.op]} {f.value}{f.op === "between" ? `–${f.value2}` : ""} {m?.unit}</span><X onClick={(e) => { e.stopPropagation(); remove(f.field); }} className="h-2.5 w-2.5 shrink-0 text-muted-foreground group-hover:text-foreground"/></button>; })}</div></div>}
          </section>
        </div>

        <div className="flex min-h-14 items-center justify-between border-t border-border bg-muted/10 px-5 py-2.5">
          <button onClick={() => setDraft([])} className="text-[10.5px] text-muted-foreground transition-colors hover:text-foreground">Clear all</button>
          <div className="flex items-center gap-2.5"><span className="text-[10.5px] text-muted-foreground">{draft.length} {draft.length === 1 ? "filter" : "filters"}</span><Button onClick={() => { onChange(draft); setOpen(false); }} className="h-9 rounded-full px-5 text-[11px] shadow-none">Show results</Button></div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ActiveFilterChips({ value, onChange }: { value: ScreenerFilterClause[]; onChange: (filters: ScreenerFilterClause[]) => void }): React.ReactElement | null {
  if (!value.length) return null;
  return <div className="flex flex-wrap items-center gap-2 px-8 pb-3">{value.map((f) => { const m = metricFor(f.field); return <span key={f.field} className="inline-flex h-7 items-center gap-2 rounded-full border border-border bg-muted/35 pl-3 pr-2 text-[11px] text-foreground"><span>{m?.label} {OP_LABEL[f.op].toLowerCase()} {f.value}{f.op === "between" ? `–${f.value2}` : ""}{m?.unit === "%" ? "%" : ` ${m?.unit ?? ""}`}</span><button aria-label={`Remove ${m?.label} filter`} onClick={() => onChange(value.filter((x) => x.field !== f.field))} className="rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-3 w-3"/></button></span>; })}</div>;
}
