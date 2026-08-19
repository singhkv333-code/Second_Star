"use client";

import * as React from "react";
import { getOhlc, type OhlcBar, type StockQuote } from "@/lib/api";
import { isError } from "@/lib/types";
import { PanelSkeleton } from "./chrome";
import { PatternEdge } from "./PatternEdge";

type Reading = { label: string; value: string; signal: "Bullish" | "Neutral" | "Bearish"; detail: string };

export function TechnicalPanel({ quote }: { quote: StockQuote }): React.ReactElement {
  const [bars, setBars] = React.useState<OhlcBar[] | null>(null);
  const [source, setSource] = React.useState<string>("");

  React.useEffect(() => {
    let dead = false;
    setBars(null);
    getOhlc(quote.symbol, "1Y", quote.exchange === "BSE" ? "BSE" : "NSE")
      .then((result) => {
        if (dead || isError(result)) return;
        setBars(result.data.bars);
        setSource(result.data.source);
      })
      .catch(() => {});
    return () => { dead = true; };
  }, [quote.symbol, quote.exchange]);

  const analysis = React.useMemo(() => bars && bars.length >= 30 ? analyse(bars, quote.ltp || quote.prev_close) : null, [bars, quote.ltp, quote.prev_close]);

  return (
    <section aria-label="Technical analysis" style={{ marginTop: 32, padding: "0 20px" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12, marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontFamily: "var(--font-ui)", fontSize: 21, fontWeight: 600, letterSpacing: "-0.022em", color: "var(--text-primary)" }}>Technical analysis</h2>
          <div style={{ marginTop: 3, fontSize: 11.5, color: "var(--text-tertiary)" }}>Daily timeframe · indicators calculated from the latest one-year OHLC history</div>
        </div>
        {source ? <span style={{ fontSize: 10.5, color: "var(--text-tertiary)", whiteSpace: "nowrap" }}>{source === "kite" ? "Kite" : "yfinance, EOD"}</span> : null}
      </div>

      {!analysis ? <PanelSkeleton rows={6} /> : (
        <div className="technical-layout" style={{ display: "grid", gridTemplateColumns: "210px minmax(0, 1fr)", borderTop: "1px solid var(--glass-border)", borderBottom: "1px solid var(--glass-border)" }}>
          <Summary score={analysis.score} label={analysis.label} price={analysis.price} />
          <div className="technical-tables" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", borderLeft: "1px solid var(--glass-border)" }}>
            <ReadingTable title="Moving averages" rows={analysis.averages} />
            <ReadingTable title="Momentum & volatility" rows={analysis.momentum} divided />
          </div>
        </div>
      )}
      <div style={{ marginTop: 8, fontSize: 10.5, lineHeight: 1.45, color: "var(--text-tertiary)" }}>Signals describe indicator direction only. They are not price targets, support/resistance levels, or investment advice.</div>

      {/* Measured behaviour under the read of today. The indicators above say
          where price sits; this says whether the shapes they form have ever
          meant anything. */}
      <PatternEdge symbol={quote.symbol} />
      <style>{`
        @media (max-width: 820px) {
          .technical-layout { grid-template-columns: 1fr !important; }
          .technical-tables { border-left: 0 !important; border-top: 1px solid var(--glass-border); }
        }
        @media (max-width: 560px) { .technical-tables { grid-template-columns: 1fr !important; } }
      `}</style>
    </section>
  );
}

function Summary({ score, label, price }: { score: number; label: string; price: number }): React.ReactElement {
  const position = ((score + 5) / 10) * 100;
  return <div style={{ padding: "18px 20px 18px 0" }}><div style={{ fontSize: 10.5, fontWeight: 650, letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--text-tertiary)" }}>Daily summary</div><div style={{ marginTop: 7, fontSize: 21, fontWeight: 600, letterSpacing: "-0.02em", color: "var(--text-primary)" }}>{label}</div><div style={{ marginTop: 3, fontSize: 11.5, color: "var(--text-tertiary)", fontVariantNumeric: "tabular-nums" }}>Reference ₹{price.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</div><div style={{ position: "relative", height: 3, marginTop: 20, background: "linear-gradient(90deg, var(--color-loss), var(--glass-border) 50%, var(--color-profit))" }}><span style={{ position: "absolute", left: `calc(${position}% - 4px)`, top: -3, width: 9, height: 9, borderRadius: "50%", border: "2px solid var(--bg-primary)", background: "var(--text-primary)" }} /></div><div style={{ display: "flex", justifyContent: "space-between", marginTop: 7, fontSize: 9.5, color: "var(--text-tertiary)" }}><span>Bearish</span><span>Neutral</span><span>Bullish</span></div></div>;
}

function ReadingTable({ title, rows, divided = false }: { title: string; rows: Reading[]; divided?: boolean }): React.ReactElement {
  return <div style={{ padding: "14px 18px", borderLeft: divided ? "1px solid var(--glass-border)" : undefined }}><div style={{ marginBottom: 7, fontSize: 10.5, fontWeight: 650, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-tertiary)" }}>{title}</div>{rows.map((row) => <div key={row.label} title={row.detail} style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 12, alignItems: "center", minHeight: 31, borderTop: "1px solid var(--glass-border)" }}><span style={{ fontSize: 11.5, color: "var(--text-secondary)" }}>{row.label}</span><span style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>{row.value}</span><Signal value={row.signal} /></div>)}</div>;
}

function Signal({ value }: { value: Reading["signal"] }): React.ReactElement {
  return <span style={{ width: 48, textAlign: "right", fontSize: 10, fontWeight: 600, color: value === "Bullish" ? "var(--color-profit)" : value === "Bearish" ? "var(--color-loss)" : "var(--text-tertiary)" }}>{value}</span>;
}

function analyse(bars: OhlcBar[], livePrice: number): { price: number; score: number; label: string; averages: Reading[]; momentum: Reading[] } {
  const closes = bars.map((b) => b.c).filter(Number.isFinite);
  const price = livePrice || closes[closes.length - 1]!;
  const ma20 = sma(closes, 20), ma50 = sma(closes, 50), ma200 = sma(closes, 200);
  const rsi14 = rsi(closes, 14);
  const macd = macdReading(closes);
  const atr14 = atr(bars, 14);
  const signalForMA = (v: number | null): Reading["signal"] => v === null ? "Neutral" : price > v ? "Bullish" : price < v ? "Bearish" : "Neutral";
  const averages: Reading[] = [["SMA 20", ma20], ["SMA 50", ma50], ["SMA 200", ma200]].map(([label, raw]) => { const value = raw as number | null; return { label: label as string, value: value === null ? "—" : `₹${value.toFixed(2)}`, signal: signalForMA(value), detail: "Price compared with the simple moving average" }; });
  const momentum: Reading[] = [
    { label: "RSI (14)", value: rsi14 === null ? "—" : rsi14.toFixed(1), signal: rsi14 === null ? "Neutral" : rsi14 > 55 ? "Bullish" : rsi14 < 45 ? "Bearish" : "Neutral", detail: "Relative Strength Index; 45–55 treated as neutral" },
    { label: "MACD (12,26,9)", value: macd ? macd.macd.toFixed(2) : "—", signal: !macd ? "Neutral" : macd.macd > macd.signal ? "Bullish" : "Bearish", detail: macd ? `Signal line ${macd.signal.toFixed(2)}` : "Insufficient history" },
    { label: "ATR (14)", value: atr14 === null ? "—" : `₹${atr14.toFixed(2)}`, signal: "Neutral", detail: atr14 === null ? "Insufficient history" : `${((atr14 / price) * 100).toFixed(2)}% of price; volatility measure, not directional` },
  ];
  const directional = [...averages, ...momentum].filter((r) => r.label !== "ATR (14)");
  const score = directional.reduce((n, row) => n + (row.signal === "Bullish" ? 1 : row.signal === "Bearish" ? -1 : 0), 0);
  return { price, score, label: score >= 3 ? "Bullish" : score <= -3 ? "Bearish" : "Neutral", averages, momentum };
}

function sma(values: number[], period: number): number | null { if (values.length < period) return null; const slice = values.slice(-period); return slice.reduce((a, b) => a + b, 0) / period; }
function ema(values: number[], period: number): number[] { if (!values.length) return []; const k = 2 / (period + 1); const out = [values[0]!]; for (let i = 1; i < values.length; i += 1) out.push(values[i]! * k + out[i - 1]! * (1 - k)); return out; }
function rsi(values: number[], period: number): number | null { if (values.length <= period) return null; const changes = values.slice(-(period + 1)).slice(1).map((v, i) => v - values.slice(-(period + 1))[i]!); const gain = changes.reduce((n, v) => n + Math.max(v, 0), 0) / period; const loss = changes.reduce((n, v) => n + Math.max(-v, 0), 0) / period; return loss === 0 ? 100 : 100 - (100 / (1 + gain / loss)); }
function macdReading(values: number[]): { macd: number; signal: number } | null { if (values.length < 35) return null; const a = ema(values, 12), b = ema(values, 26); const line = a.map((v, i) => v - b[i]!); const signals = ema(line, 9); return { macd: line[line.length - 1]!, signal: signals[signals.length - 1]! }; }
function atr(bars: OhlcBar[], period: number): number | null { if (bars.length <= period) return null; const recent = bars.slice(-(period + 1)); const ranges = recent.slice(1).map((bar, i) => Math.max(bar.h - bar.l, Math.abs(bar.h - recent[i]!.c), Math.abs(bar.l - recent[i]!.c))); return ranges.reduce((a, b) => a + b, 0) / period; }
