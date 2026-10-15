"use client";

/**
 * The top of the Markets tab: the brief, the market's breadth, and a strip of
 * the instruments everything else is measured against.
 */

import { useEffect, useMemo, useState } from "react";
import { ChatCircle, Pulse, Sparkle } from "./icons";
import { Skeleton } from "@/components/ui/skeleton";
import { askPivot } from "@/lib/launch";
import { isError } from "@/lib/types";
import {
  fetchAssetQuotes,
  fetchDailyCloses,
  fmtDay,
  fmtPrice,
  getMarketBrief,
  type AssetQuote,
  type MarketBreadth,
  type MarketBrief,
  type MarketExplore,
} from "@/lib/markets";
import { CardHead, Delta, Hint, Spark, css as s, cx, openChart } from "./parts";

// ── brief ────────────────────────────────────────────────────────────────

export function BriefCard({ data }: { data: MarketExplore | null }): React.ReactElement {
  const [brief, setBrief] = useState<MarketBrief | null>(null);
  const [failed, setFailed] = useState(false);
  const generated = data?.as_of.generated_at;

  useEffect(() => {
    if (!generated) return;
    let alive = true;
    getMarketBrief()
      .then((r) => {
        if (!alive) return;
        if (isError(r)) setFailed(true);
        else { setBrief(r.data); setFailed(false); }
      })
      .catch(() => alive && setFailed(true));
    return () => { alive = false; };
  }, [generated]);

  const basis = data
    ? data.as_of.prices === "live" ? "today's session" : `the ${fmtDay(data.as_of.eod_day)} close`
    : "";

  return (
    <section className={cx(s.card, s.cardPad, "flex h-full flex-col")} aria-label="Market brief">
      <CardHead Icon={Sparkle} title="Brief" />
      <div className="flex-1">
        {brief ? (
          <p className={s.brief}>{brief.text}</p>
        ) : failed ? (
          <p className={s.sectionCaption}>The brief is unavailable right now. The figures below are unaffected.</p>
        ) : (
          <div className="flex flex-col gap-2.5 pt-1">
            <Skeleton style={{ height: 16, width: "96%" }} />
            <Skeleton style={{ height: 16, width: "90%" }} />
            <Skeleton style={{ height: 16, width: "62%" }} />
          </div>
        )}
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <span className={s.stamp}>
          {data ? `${data.universe.size} largest companies · ${basis}` : " "}
        </span>
        <button
          type="button"
          className={s.ghostBtn}
          disabled={!brief}
          onClick={() => askPivot(`The market brief says: "${brief?.text ?? ""}" What is driving this, and which sectors should I look at more closely?`)}
        >
          <ChatCircle size={14} /> Ask a follow-up
        </button>
      </div>
    </section>
  );
}

// ── breadth ──────────────────────────────────────────────────────────────

export function BreadthCard({ breadth }: { breadth: MarketBreadth | null }): React.ReactElement {
  return (
    <section className={cx(s.card, s.cardPad, "flex h-full flex-col")} aria-label="Market breadth">
      <CardHead Icon={Pulse} title="Breadth" />
      {!breadth ? (
        <div className="flex flex-col gap-3">
          <Skeleton style={{ height: 28, width: "70%" }} />
          <Skeleton style={{ height: 8, width: "100%" }} />
          <Skeleton style={{ height: 64, width: "100%" }} />
        </div>
      ) : (
        <BreadthBody b={breadth} />
      )}
    </section>
  );
}

function BreadthBody({ b }: { b: MarketBreadth }): React.ReactElement {
  const total = Math.max(1, b.advancers + b.decliners + b.unchanged);
  const pct = (n: number, d: number): string => (d ? `${Math.round((n / d) * 100)}%` : "—");
  return (
    <div className="flex flex-1 flex-col gap-4">
      <div>
        <div className="flex items-baseline justify-between">
          <span className={cx(s.num, "text-[22px] font-semibold tracking-tight")}>
            {b.advancers}
            <span className={cx(s.muted, "ml-1.5 text-[12px] font-medium")}>up</span>
          </span>
          <span className={cx(s.num, "text-[22px] font-semibold tracking-tight")}>
            {b.decliners}
            <span className={cx(s.muted, "ml-1.5 text-[12px] font-medium")}>down</span>
          </span>
        </div>
        <div className="mt-2 flex h-[6px] w-full overflow-hidden rounded-full" style={{ background: "var(--surface-track)" }}>
          <span style={{ width: `${(b.advancers / total) * 100}%`, background: "var(--color-profit)", opacity: 0.75 }} />
          <span style={{ width: `${(b.unchanged / total) * 100}%` }} />
          <span style={{ width: `${(b.decliners / total) * 100}%`, background: "var(--color-loss)", opacity: 0.75, marginLeft: "auto" }} />
        </div>
      </div>

      <Histogram edges={b.histogram.edges} counts={b.histogram.counts} />

      <div className="grid grid-cols-2 gap-x-4 gap-y-3">
        <Meter label="Above 50-day" value={b.above_sma50} total={b.sma50_total} />
        <Meter label="Above 200-day" value={b.above_sma200} total={b.sma200_total} />
        <Stat label="Near 52W high" value={String(b.near_high)} sub={pct(b.near_high, b.extremes_total)} />
        <Stat label="Near 52W low" value={String(b.near_low)} sub={pct(b.near_low, b.extremes_total)} />
      </div>
    </div>
  );
}

function Histogram({ edges, counts }: { edges: number[]; counts: number[] }): React.ReactElement {
  const max = Math.max(1, ...counts);
  const label = (i: number): string => {
    if (i === 0) return `${edges[0] ?? 0}% or worse`;
    if (i === counts.length - 1) return `Above +${edges[edges.length - 1] ?? 0}%`;
    const lo = edges[i - 1] ?? 0;
    const hi = edges[i] ?? 0;
    const f = (v: number): string => `${v > 0 ? "+" : ""}${v}%`;
    return `${f(lo)} to ${f(hi)}`;
  };
  return (
    <div>
      <div className="flex h-[64px] items-end gap-[3px]" role="img" aria-label="Distribution of the session's moves">
        {counts.map((c, i) => {
          const up = i >= edges.indexOf(0) + 1;
          return (
            <Hint key={i} text={`${label(i)}: ${c} stocks`}>
              <span
                className="flex-1 rounded-[3px] transition-opacity hover:opacity-100"
                style={{
                  height: `${Math.max(3, (c / max) * 100)}%`,
                  background: up ? "var(--color-profit)" : "var(--color-loss)",
                  opacity: 0.18 + 0.5 * (c / max),
                }}
              />
            </Hint>
          );
        })}
      </div>
      <div className={cx(s.label, "mt-1.5 flex justify-between")}>
        <span>−5%</span>
        <span>0</span>
        <span>+5%</span>
      </div>
    </div>
  );
}

function Meter({ label, value, total }: { label: string; value: number; total: number }): React.ReactElement {
  const p = total ? value / total : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className={s.label}>{label}</span>
        <span className={cx(s.num, "text-[13px] font-semibold")}>{Math.round(p * 100)}%</span>
      </div>
      <div className="mt-1.5 h-[4px] w-full overflow-hidden rounded-full" style={{ background: "var(--surface-track)" }}>
        <div className="h-full rounded-full" style={{ width: `${p * 100}%`, background: "var(--text-primary)" }} />
      </div>
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub: string }): React.ReactElement {
  return (
    <div>
      <div className={s.label}>{label}</div>
      <div className="mt-0.5 flex items-baseline gap-1.5">
        <span className={cx(s.num, "text-[15px] font-semibold")}>{value}</span>
        <span className={cx(s.num, s.muted, "text-[11.5px]")}>{sub}</span>
      </div>
    </div>
  );
}

// ── the strip ────────────────────────────────────────────────────────────

const STRIP: { symbol: string; label: string }[] = [
  { symbol: "NIFTY 50", label: "Nifty 50" },
  { symbol: "SENSEX", label: "Sensex" },
  { symbol: "NIFTY BANK", label: "Bank Nifty" },
  { symbol: "NIFTY MIDCAP 100", label: "Midcap 100" },
  { symbol: "INDIA VIX", label: "India VIX" },
  { symbol: "USDINR", label: "USD/INR" },
  { symbol: "GOLD", label: "Gold" },
  { symbol: "BTCUSDT", label: "Bitcoin" },
];

export function MarketStrip(): React.ReactElement {
  const [quotes, setQuotes] = useState<Map<string, AssetQuote> | null>(null);
  const [sparks, setSparks] = useState<Record<string, number[]>>({});

  useEffect(() => {
    let alive = true;
    const load = (): void => {
      void fetchAssetQuotes(STRIP.map((x) => x.symbol)).then((q) => alive && setQuotes(q));
    };
    load();
    STRIP.forEach((x) => {
      void fetchDailyCloses(x.symbol, 30).then((b) => {
        if (alive && b.length > 1) setSparks((m) => ({ ...m, [x.symbol]: b.map((p) => p.c) }));
      });
    });
    const id = window.setInterval(load, 60_000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  const stale = useMemo(() => staleDay(quotes ? [...quotes.values()] : []), [quotes]);

  return (
    <div>
      <div className={s.strip} aria-label="Benchmarks">
        {STRIP.map((x) => {
          const q = quotes?.get(x.symbol);
          return (
            <button key={x.symbol} type="button" className={s.tile} onClick={() => openChart(x.symbol)}>
              <div className="flex items-start justify-between gap-2">
                <span className="truncate text-[12px] font-medium" style={{ color: "var(--text-primary)" }}>{x.label}</span>
                <Delta v={q?.change_pct ?? null} className="text-[11.5px] font-medium" />
              </div>
              {quotes ? (
                <div className={cx(s.num, "mt-1 text-[15px] font-semibold")}>{q ? fmtPrice(q.last) : "—"}</div>
              ) : (
                <Skeleton style={{ height: 18, width: "70%", marginTop: 6 }} />
              )}
              <div className="mt-2">
                <Spark values={sparks[x.symbol]} width={140} height={24} fluid />
              </div>
            </button>
          );
        })}
      </div>
      {stale && <p className={cx(s.stamp, "mt-2")}>Prices as of {stale}</p>}
    </div>
  );
}

/** The day a set of quotes is from, when it is not today. */
export function staleDay(qs: AssetQuote[]): string | null {
  const ts = qs.map((q) => q.as_of ?? 0).filter((t) => t > 0);
  if (!ts.length) return null;
  const newest = Math.max(...ts);
  if (Date.now() / 1000 - newest < 36 * 3600) return null;
  return new Date(newest * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
}
