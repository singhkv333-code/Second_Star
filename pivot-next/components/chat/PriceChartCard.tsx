"use client";

/**
 * PriceChartCard — a price chart of one or two companies under a chat reply,
 * shown when the model calls `show_price_chart`.
 *
 * The tool only names the symbols; this card loads their bars from charto's
 * own store (`/bars`), after the reply has rendered. So the chart costs the
 * turn no latency, and no price ever passes through the model. The plot is
 * the stock page's StockPriceChart: an area for one company, two lines as %
 * change from the window's start for a pair.
 */

import React, { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { CandlestickChart, Building2 } from "lucide-react";
import { CompanyLogo } from "@/components/CompanyLogo";
import {
  StockPriceChart,
  type PricePoint,
  type VolumePoint,
} from "@/components/chart/StockPriceChart";
import { useCompanyLogos } from "@/hooks/useCompanyLogos";

export type PriceChartPayload = {
  symbols: { symbol: string; name?: string }[];
  range?: Range;
};

const RANGES = ["1D", "1W", "1M", "3M", "6M", "1Y", "5Y"] as const;
type Range = (typeof RANGES)[number];

// interval + how many bars cover the window (sessions of 75 five-minute bars).
const SPEC: Record<Range, { interval: string; limit: number; sessions?: number }> = {
  "1D": { interval: "5m", limit: 160, sessions: 1 },
  "1W": { interval: "15m", limit: 160, sessions: 5 },
  "1M": { interval: "1d", limit: 22 },
  "3M": { interval: "1d", limit: 63 },
  "6M": { interval: "1d", limit: 126 },
  "1Y": { interval: "1d", limit: 250 },
  "5Y": { interval: "1w", limit: 260 },
};
const COLORS = ["#2962FF", "#fb8500"];
const IST = 19_800; // charto's bars are true UTC; the axis reads IST

type Bar = { t: number; o: number; h: number; l: number; c: number; v: number };
type Series = { points: PricePoint[]; volume: VolumePoint[]; base: number; last: Bar };

function dataBase(): string {
  if (typeof window === "undefined") return "";
  return ["localhost", "127.0.0.1"].includes(window.location.hostname)
    ? "http://127.0.0.1:5174"
    : "";
}

const cache = new Map<string, Promise<Series | null>>();

function loadSeries(symbol: string, range: Range): Promise<Series | null> {
  const key = `${symbol}:${range}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const { interval, limit, sessions } = SPEC[range];
  const p = fetch(
    `${dataBase()}/bars?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=${limit}`,
  )
    .then((r) => (r.ok ? r.json() : null))
    .then((d: { bars?: Bar[] } | null): Series | null => {
      let bars = d?.bars ?? [];
      if (!bars.length) return null;
      // Intraday windows keep whole sessions; the close before the first
      // kept session is the base the day's change is measured from.
      let base = bars[0]!.o;
      if (sessions) {
        const day = (b: Bar): string => new Date((b.t + IST) * 1000).toISOString().slice(0, 10);
        const days = [...new Set(bars.map(day))].slice(-sessions);
        const first = bars.findIndex((b) => day(b) === days[0]);
        base = first > 0 ? bars[first - 1]!.c : bars[first]!.o;
        bars = bars.slice(first);
      }
      const iso = (t: number): string => new Date((t + IST) * 1000).toISOString();
      return {
        points: bars.map((b) => ({ t: iso(b.t), v: b.c })),
        volume: bars.map((b) => ({ t: iso(b.t), v: b.v, up: b.c >= b.o })),
        base,
        last: bars[bars.length - 1]!,
      };
    })
    .catch(() => null);
  cache.set(key, p);
  p.then((s) => {
    if (!s) cache.delete(key); // a failure is retried on the next visit
  });
  return p;
}

// Quiet text actions: they read as part of the card until hovered.
const LINK =
  "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-muted-foreground " +
  "transition-colors hover:bg-muted/70 hover:text-foreground";

/** The card's frame, drawn the moment the model starts the chart. */
export function PriceChartSkeleton(): React.ReactElement {
  return (
    <div
      className="w-full animate-pulse overflow-hidden rounded-xl border border-border bg-card px-4 pb-3 pt-3.5"
      data-testid="price-chart-skeleton"
      aria-label="Loading chart"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="h-8 w-8 rounded-lg bg-muted" />
          <div className="space-y-1.5">
            <div className="h-3 w-28 rounded bg-muted" />
            <div className="h-2.5 w-16 rounded bg-muted/70" />
          </div>
        </div>
        <div className="h-5 w-20 rounded bg-muted" />
      </div>
      <div className="mt-4 h-[240px] rounded-lg bg-muted/40" />
    </div>
  );
}

const inr = (v: number): string =>
  `₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (v: number): string => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;

export function PriceChartCard({ payload }: { payload: PriceChartPayload }): React.ReactElement {
  const router = useRouter();
  const syms = payload.symbols.slice(0, 2);
  const [range, setRange] = useState<Range>(
    RANGES.includes(payload.range as Range) ? (payload.range as Range) : "1Y",
  );
  const [data, setData] = useState<(Series | null)[] | null>(null);
  const key = syms.map((s) => s.symbol).join(",");
  const logos = useCompanyLogos(useMemo(() => key.split(","), [key]));

  useEffect(() => {
    let live = true;
    setData(null);
    void Promise.all(syms.map((s) => loadSeries(s.symbol, range))).then((d) => {
      if (live) setData(d);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, range]);

  const ok = data?.every(Boolean) ? (data as Series[]) : null;
  const failed = data !== null && !ok;
  const change = (s: Series): number => ((s.last.c - s.base) / s.base) * 100;
  const intraday = range === "1D" || range === "1W";
  const asOf = ok
    ? new Date((ok[0]!.last.t + IST) * 1000).toLocaleString("en-IN", {
        timeZone: "UTC",
        day: "numeric",
        month: "short",
        ...(intraday ? { hour: "2-digit", minute: "2-digit", hour12: false } : { year: "numeric" }),
      })
    : "";

  const openChart = (symbol: string): void => {
    window.dispatchEvent(new CustomEvent("pivot:open-chart", { detail: { symbol } }));
  };

  return (
    <div className="w-full overflow-hidden rounded-xl border border-border bg-card" data-testid="price-chart-card">
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-3.5">
        {syms.length === 1 ? (
          <>
            <div className="flex min-w-0 items-center gap-2.5">
              <CompanyLogo
                logoUrl={logos[syms[0]!.symbol] ?? null}
                name={syms[0]!.name || syms[0]!.symbol}
                symbol={syms[0]!.symbol}
                size={32}
              />
              <div className="min-w-0">
                <div className="truncate text-[15px] font-semibold text-foreground">
                  {syms[0]!.name || syms[0]!.symbol}
                </div>
                <div className="text-[11.5px] text-muted-foreground">{syms[0]!.symbol} · NSE</div>
              </div>
            </div>
            {ok && (
              <div className="text-right">
                <div className="text-[18px] font-semibold tabular-nums text-foreground">
                  {inr(ok[0]!.last.c)}
                </div>
                <div
                  className="text-[12.5px] font-medium tabular-nums"
                  style={{ color: change(ok[0]!) >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}
                >
                  {`${change(ok[0]!) >= 0 ? "+" : "−"}${inr(Math.abs(ok[0]!.last.c - ok[0]!.base)).slice(1)} (${pct(change(ok[0]!))}) · ${range}`}
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="flex flex-wrap gap-x-5 gap-y-1.5">
            {syms.map((s, i) => (
              <div key={s.symbol} className="flex items-center gap-2 text-[13px]">
                <span className="h-2 w-2 rounded-full" style={{ background: COLORS[i] }} />
                <span className="font-semibold text-foreground">{s.name || s.symbol}</span>
                {ok && (
                  <span
                    className="tabular-nums"
                    style={{ color: change(ok[i]!) >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}
                  >
                    {pct(change(ok[i]!))}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between px-3 pt-2.5">
        <div className="flex gap-0.5" role="tablist" aria-label="Range">
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              role="tab"
              aria-selected={r === range}
              onClick={() => setRange(r)}
              className={`rounded-md px-2 py-1 text-[12px] font-medium transition-colors ${
                r === range
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {r}
            </button>
          ))}
        </div>
        {asOf && <span className="pr-1 text-[11px] text-muted-foreground">As of {asOf}</span>}
      </div>

      <div className="px-1 pt-1" style={{ height: 240 }}>
        {ok ? (
          <StockPriceChart
            height={240}
            intraday={intraday}
            seriesDefs={syms.map((s, i) => ({
              symbol: s.symbol,
              color: COLORS[i]!,
              points: ok[i]!.points,
            }))}
            volume={syms.length === 1 ? ok[0]!.volume : null}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-[12.5px] text-muted-foreground">
            {failed ? "Price history is unavailable for this range." : (
              <div className="h-full w-full animate-pulse rounded-lg bg-muted/40" />
            )}
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-0.5 px-2 pb-2 pt-1">
        {syms.map((s) => (
          <button key={s.symbol} type="button" className={LINK} onClick={() => openChart(s.symbol)}>
            <CandlestickChart size={13} strokeWidth={1.75} aria-hidden />
            {syms.length === 1 ? "Detailed chart" : `${s.symbol} chart`}
          </button>
        ))}
        {syms.length === 1 && (
          <button
            type="button"
            className={LINK}
            onClick={() => router.push(`/stock/${encodeURIComponent(syms[0]!.symbol)}`)}
          >
            <Building2 size={13} strokeWidth={1.75} aria-hidden />
            Company page
          </button>
        )}
      </div>
    </div>
  );
}
