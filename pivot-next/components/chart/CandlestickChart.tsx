/**
 * CandlestickChart — real OHLC candlesticks (TradingView lightweight-charts)
 * with a volume histogram underlay, a range switcher, a live crosshair
 * readout (O/H/L/C + change), and an honest Kite-vs-yfinance source tag.
 *
 * Fetches `GET /markets/ohlc/{symbol}` on mount + range change. Candlestick is
 * the primary chart traders expect; this is the engaging upgrade over the
 * close-only SVG sparkline.
 */
"use client";

import * as React from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
} from "lightweight-charts";
import { LightweightChart, toTime } from "./LightweightChart";
import { getOhlc, type OhlcBar, type SparklineRange } from "@/lib/api";
import { isError } from "@/lib/types";
import { cn } from "@/lib/utils";

const RANGES: SparklineRange[] = ["1D", "1W", "1M", "6M", "1Y", "5Y"];

const UP = "#16a34a";
const DOWN = "#dc2626";

type Props = {
  symbol: string;
  exchange?: "NSE" | "BSE";
  initialRange?: SparklineRange;
  height?: number;
  className?: string;
};

type Hover = { o: number; h: number; l: number; c: number; chgPct: number } | null;

export function CandlestickChart({
  symbol,
  exchange,
  initialRange = "6M",
  height = 280,
  className,
}: Props) {
  const [range, setRange] = React.useState<SparklineRange>(initialRange);
  const [bars, setBars] = React.useState<OhlcBar[] | null>(null);
  const [source, setSource] = React.useState<string>("");
  const [err, setErr] = React.useState<string | null>(null);
  const [hover, setHover] = React.useState<Hover>(null);

  React.useEffect(() => {
    let alive = true;
    setBars(null);
    setErr(null);
    getOhlc(symbol, range, exchange).then((res) => {
      if (!alive) return;
      if (isError(res)) {
        setErr(res.error.message || "No chart data");
        setBars([]);
        return;
      }
      setSource(res.data.source);
      setBars(res.data.bars);
    });
    return () => {
      alive = false;
    };
  }, [symbol, range, exchange]);

  const last = bars && bars.length ? bars[bars.length - 1] : null;
  const first = bars && bars.length ? bars[0] : null;
  const netPct =
    last && first && first.c ? ((last.c - first.c) / first.c) * 100 : 0;
  const shown = hover ?? (last ? { o: last.o, h: last.h, l: last.l, c: last.c, chgPct: netPct } : null);
  const up = (shown?.chgPct ?? 0) >= 0;

  return (
    <div className={cn("w-full", className)}>
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex items-baseline gap-2 font-mono text-[11px] text-slate-500">
          {shown ? (
            <>
              <span>O <b className="text-slate-700">{shown.o.toFixed(2)}</b></span>
              <span>H <b className="text-slate-700">{shown.h.toFixed(2)}</b></span>
              <span>L <b className="text-slate-700">{shown.l.toFixed(2)}</b></span>
              <span>C <b className="text-slate-900">{shown.c.toFixed(2)}</b></span>
              <span className={up ? "text-green-600" : "text-red-600"}>
                {up ? "▲" : "▼"} {Math.abs(shown.chgPct).toFixed(2)}%
              </span>
            </>
          ) : (
            <span>{symbol}</span>
          )}
        </div>
        <div className="flex gap-1">
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={cn(
                "rounded-md px-1.5 py-0.5 text-[10px] font-semibold transition",
                r === range
                  ? "bg-slate-900 text-white"
                  : "text-slate-400 hover:bg-slate-100 hover:text-slate-600",
              )}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      <div className="relative" style={{ height }}>
        {bars === null && (
          <div className="absolute inset-0 grid place-items-center text-xs text-slate-400">
            Loading {symbol}…
          </div>
        )}
        {bars !== null && bars.length === 0 && (
          <div className="absolute inset-0 grid place-items-center text-xs text-slate-400">
            {err ?? "No chart data"}
          </div>
        )}
        {bars !== null && bars.length > 0 && (
          <LightweightChart
            height={height}
            deps={[bars]}
            onReady={(chart: IChartApi) => {
              const candle = chart.addSeries(CandlestickSeries, {
                upColor: UP,
                downColor: DOWN,
                wickUpColor: UP,
                wickDownColor: DOWN,
                borderVisible: false,
                priceLineVisible: false,
              });
              // dedupe + sort defensively (lightweight-charts requires it)
              const seen = new Set<number>();
              const candleData = bars
                .map((b) => ({
                  time: toTime(b.t),
                  open: b.o,
                  high: b.h,
                  low: b.l,
                  close: b.c,
                }))
                .filter((d) => {
                  const k = d.time as unknown as number;
                  if (seen.has(k)) return false;
                  seen.add(k);
                  return true;
                });
              candle.setData(candleData);

              // Volume histogram pinned to the bottom 22% as an overlay scale.
              const hasVol = bars.some((b) => b.v > 0);
              let vol: ISeriesApi<"Histogram"> | null = null;
              if (hasVol) {
                vol = chart.addSeries(HistogramSeries, {
                  priceFormat: { type: "volume" },
                  priceScaleId: "vol",
                  priceLineVisible: false,
                  lastValueVisible: false,
                });
                vol.priceScale().applyOptions({
                  scaleMargins: { top: 0.8, bottom: 0 },
                });
                vol.setData(
                  bars.map((b) => ({
                    time: toTime(b.t),
                    value: b.v,
                    color:
                      b.c >= b.o ? "rgba(22,163,74,0.30)" : "rgba(220,38,38,0.30)",
                  })),
                );
              }

              // Dashed price line at the latest close.
              if (last) {
                candle.createPriceLine({
                  price: last.c,
                  color: up ? UP : DOWN,
                  lineWidth: 1,
                  lineStyle: LineStyle.Dashed,
                  axisLabelVisible: true,
                });
              }

              const onCross = (param: MouseEventParams) => {
                const d = param.seriesData.get(candle) as
                  | { open: number; high: number; low: number; close: number }
                  | undefined;
                if (!d) {
                  setHover(null);
                  return;
                }
                setHover({
                  o: d.open,
                  h: d.high,
                  l: d.low,
                  c: d.close,
                  chgPct: first && first.c ? ((d.close - first.c) / first.c) * 100 : 0,
                });
              };
              chart.subscribeCrosshairMove(onCross);
              return () => chart.unsubscribeCrosshairMove(onCross);
            }}
          />
        )}
      </div>

      {source && (
        <div className="mt-1 text-right text-[9px] uppercase tracking-wide text-slate-300">
          {source === "kite" ? "live · kite" : "yfinance · eod"}
        </div>
      )}
    </div>
  );
}
