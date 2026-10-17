"use client";

/**
 * Indices, commodities, crypto and currencies: the instruments the chart
 * server prices, grouped the way a desk reads them. Each one opens in the
 * chart.
 */

import { useEffect, useMemo, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { loadUniverse, type Instrument } from "@/lib/instruments";
import {
  ASSET_BOARDS,
  fetchAssetQuotes,
  fetchDailyCloses,
  fmtPct,
  fmtPrice,
  type AssetClass,
  type AssetQuote,
} from "@/lib/markets";
import { Delta, Empty, Hint, Mark, RangeBar, Spark, css as s, cx, openChart } from "./parts";
import { staleDay } from "./Overview";

export function AssetBoard({ kind, columns }: { kind: AssetClass; columns: number }): React.ReactElement {
  const groups = ASSET_BOARDS[kind];
  const all = useMemo(() => groups.flatMap((g) => g.symbols), [groups]);
  const [quotes, setQuotes] = useState<Map<string, AssetQuote> | null>(null);
  const [sparks, setSparks] = useState<Record<string, number[]>>({});
  const [names, setNames] = useState<Map<string, Instrument>>(new Map());

  useEffect(() => {
    let alive = true;
    setQuotes(null);
    const load = (): void => { void fetchAssetQuotes(all).then((q) => alive && setQuotes(q)); };
    load();
    void loadUniverse().then((m) => alive && setNames(m));
    for (const sym of all) {
      void fetchDailyCloses(sym, 30).then((b) => {
        if (alive && b.length > 1) setSparks((m) => ({ ...m, [sym]: b.map((p) => p.c) }));
      });
    }
    const id = window.setInterval(load, 60_000);
    return () => { alive = false; window.clearInterval(id); };
  }, [all]);

  const stale = useMemo(() => staleDay(quotes ? [...quotes.values()] : []), [quotes]);

  return (
    <div className="flex flex-col gap-8">
      {groups.map((g) => {
        const rows = quotes ? g.symbols.filter((x) => quotes.has(x)) : g.symbols;
        const best = quotes ? [...rows].sort((a, b) => (quotes.get(b)?.change_pct ?? 0) - (quotes.get(a)?.change_pct ?? 0)) : rows;
        return (
          <section key={g.title} aria-label={g.title}>
            <div className="mb-3 flex items-baseline justify-between gap-3">
              <h2 className={s.sectionTitle}>{g.title}</h2>
            </div>
            {quotes && rows.length === 0 ? (
              <Empty text="No prices for these right now." />
            ) : (
              <div className="grid gap-2.5" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
                {(quotes ? best : g.symbols).map((sym) => (
                  <AssetTile key={sym} sym={sym} q={quotes?.get(sym)} loading={!quotes} spark={sparks[sym]} inst={names.get(sym)} />
                ))}
              </div>
            )}
          </section>
        );
      })}
      <p className={s.stamp}>
        {[stale ? `As of ${stale}` : null,
          kind === "crypto" ? "USD, Bybit spot" : kind === "commodities" ? "MCX, near month" : null,
        ].filter(Boolean).join(" · ")}
      </p>
    </div>
  );
}

function AssetTile({
  sym,
  q,
  loading,
  spark,
  inst,
}: {
  sym: string;
  q?: AssetQuote;
  loading: boolean;
  spark?: number[];
  inst?: Instrument;
}): React.ReactElement {
  const title = inst?.title && inst.title !== sym ? inst.title : prettify(sym);
  const pos = q && q.high != null && q.low != null && q.last != null && q.high > q.low
    ? (q.last - q.low) / (q.high - q.low) : null;
  return (
    <button type="button" className={cx(s.tile, s.assetTile)} onClick={() => openChart(sym)} aria-label={`${title}, open in chart`}>
      <div className={cx(s.assetIdentity, "flex items-center gap-2.5")}>
        <Mark src={inst?.logo} label={title} size={26} />
        <div className="min-w-0 flex-1">
          <div className={cx(s.assetName, "text-[13px] font-semibold")}>{title}</div>
          <div className={cx(s.subname)}>{inst?.sub ?? sym}</div>
        </div>
      </div>
      {loading ? (
        <Skeleton style={{ height: 20, width: "60%", marginTop: 12 }} />
      ) : (
        <div className={cx(s.assetPrice, "mt-3 flex items-baseline justify-between gap-2")}>
          <span className={cx(s.num, "text-[17px] font-semibold tracking-tight")}>{fmtPrice(q?.last)}</span>
          <Delta v={q?.change_pct ?? null} className="text-[12.5px] font-medium" />
        </div>
      )}
      <div className="mt-2"><Spark values={spark} width={200} height={30} fluid /></div>
      {q && (
        <Hint text={`Day range ${fmtPrice(q.low)} – ${fmtPrice(q.high)}`}>
          <div className={cx(s.assetRange, "mt-2 flex items-center gap-2")}>
            <span className={cx(s.label, s.num)}>{fmtPrice(q.low)}</span>
            <span className="flex-1"><RangeBar pos={pos} width="100%" /></span>
            <span className={cx(s.label, s.num)}>{fmtPrice(q.high)}</span>
          </div>
        </Hint>
      )}
      <span className="sr-only">{fmtPct(q?.change_pct)}</span>
    </button>
  );
}

function prettify(sym: string): string {
  const m = sym.match(/^([A-Z0-9]+)USDT$/);
  if (m?.[1]) return m[1];
  return sym.replace(/^NIFTY /, "Nifty ").replace(/\b([A-Z])([A-Z]+)\b/g, (_, a: string, b: string) => a + b.toLowerCase());
}
