"use client";

/**
 * StrategyCalculator — the "put in ₹X, see what actually happened" widget.
 *
 * Steals the delightful Polymarket/Kalshi mechanic (one amount input +
 * quick-add chips → an instantly-recomputing outcome) but keeps Pivot's ONE
 * honest difference: a prediction market shows a single fixed payout; we invest
 * in real securities, so the outcome is a RANGE across real past occurrences.
 *
 *  - basket / pair / hedge → a per-occurrence outcome dot-plot: each real past
 *    episode = one dot at its ₹ profit/loss (baseline 0 = breakeven), median
 *    marked. Typing an amount rescales the ₹ axis + every number live.
 *  - option tier → the REAL modelled payoff curve (max loss = the premium, max
 *    profit, breakeven, POP), scaled to ₹. This is the one place a fixed payoff
 *    shape is genuinely accurate.
 *
 * NEVER a single fabricated "you'll win ₹X" number and NEVER a fabricated
 * probability — the hero readout is the triplet typical · range · hit-rate.
 *
 * DESIGN LAW: rounded, border-only, no fills except tinted zones, every label
 * ≥13px, tabular numerals, colored solid controls, light + dark via tokens.
 */

import * as React from "react";
import {
  Area,
  AreaChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ExpressionDetail } from "@/lib/types";
import { Num, Stat } from "./Stat";
import { useTokenColors } from "./use-token-color";

const QUICK_ADDS = [10_000, 25_000, 50_000, 100_000] as const;
const MIN_AMT = 10_000;
const MAX_AMT = 2_000_000;
const DEFAULT_AMT = 100_000;

function inr(v: number): string {
  const r = Math.round(v);
  const sign = r < 0 ? "−" : "";
  return `${sign}₹${Math.abs(r).toLocaleString("en-IN")}`;
}
function inrCompact(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(a >= 1e8 ? 0 : 1)}Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(a >= 1e6 ? 0 : 1)}L`;
  if (a >= 1e3) return `${sign}₹${Math.round(a / 1e3)}k`;
  return `${sign}₹${Math.round(a)}`;
}
function pct(v: number, dp = 1): string {
  const s = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${s}${Math.abs(v).toFixed(dp)}%`;
}
function median(xs: number[]): number {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
}

export function StrategyCalculator({
  expression,
  horizonLabel,
}: {
  expression: ExpressionDetail;
  horizonLabel?: string | null;
}): React.ReactElement {
  const [amount, setAmount] = React.useState<number>(DEFAULT_AMT);
  const c = useTokenColors({
    blue: "--pivot-blue",
    profit: "--color-profit",
    loss: "--color-loss",
    warn: "--color-warn",
    ink: "--text-primary",
    secondary: "--text-secondary",
    tertiary: "--text-tertiary",
    border: "--glass-border",
    borderFocus: "--glass-border-focus",
    bgBase: "--bg-base",
  });

  const om = expression.option_model ?? null;
  const isOption = !!om;

  const rets = (expression.episodes ?? [])
    .map((e) => e.return_pct)
    .filter((r): r is number => typeof r === "number");
  const hasEpisodes = rets.length >= 2;
  const n = expression.n_episodes ?? rets.length;
  const nPos =
    expression.n_positive ?? rets.filter((r) => r > 0).length;
  const horizon =
    horizonLabel ?? expression.exit_period ?? expression.time_horizon ?? "the horizon";

  const clamp = (v: number) => Math.max(MIN_AMT, Math.min(MAX_AMT, Math.round(v)));
  const sliderPct = Math.round(((amount - MIN_AMT) / (MAX_AMT - MIN_AMT)) * 100);

  return (
    <div
      style={{
        border: `1px solid ${c.border}`,
        borderRadius: "var(--radius-lg)",
        background: "var(--bg-base)",
        padding: 18,
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      {/* header */}
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 15,
            fontWeight: 700,
            color: "var(--text-primary)",
          }}
        >
          If you put in this much
        </span>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 13,
            color: "var(--text-tertiary)",
            lineHeight: 1.45,
          }}
        >
          {isOption
            ? "The modelled payoff of the options structure, scaled to your amount."
            : `What this strategy did to your money across ${n} past ${
                n === 1 ? "occurrence" : "occurrences"
              }.`}
        </span>
      </div>

      {/* amount control */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 22,
              fontWeight: 700,
              color: "var(--text-tertiary)",
            }}
          >
            ₹
          </span>
          <input
            aria-label="Amount to invest"
            value={amount.toLocaleString("en-IN")}
            onChange={(e) => {
              const raw = Number(e.target.value.replace(/[^0-9]/g, ""));
              if (Number.isFinite(raw)) setAmount(clamp(raw || MIN_AMT));
            }}
            inputMode="numeric"
            style={{
              fontFamily: "var(--font-display)",
              fontVariantNumeric: "tabular-nums",
              fontSize: 28,
              fontWeight: 700,
              letterSpacing: "-0.02em",
              color: "var(--text-primary)",
              background: "transparent",
              border: "none",
              outline: "none",
              width: "100%",
              padding: 0,
            }}
          />
        </div>

        {/* quick-add chips — solid colored */}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {QUICK_ADDS.map((q) => (
            <button
              key={q}
              onClick={() => setAmount((a) => clamp(a + q))}
              style={{
                fontFamily: "var(--font-display)",
                fontVariantNumeric: "tabular-nums",
                fontSize: 13,
                fontWeight: 600,
                color: c.blue,
                background: `color-mix(in srgb, ${c.blue} 12%, var(--bg-base))`,
                border: `1px solid color-mix(in srgb, ${c.blue} 32%, transparent)`,
                borderRadius: "var(--radius-pill, 999px)",
                padding: "6px 12px",
                cursor: "pointer",
              }}
            >
              +{inrCompact(q)}
            </button>
          ))}
          <button
            onClick={() => setAmount(DEFAULT_AMT)}
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 13,
              fontWeight: 500,
              color: "var(--text-tertiary)",
              background: "transparent",
              border: "none",
              padding: "6px 6px",
              cursor: "pointer",
            }}
          >
            Reset
          </button>
        </div>

        {/* slider — gradient track (fill + calm remainder) + styled thumb, so it
            stays light in both themes instead of a heavy black default track. */}
        <input
          type="range"
          className="vm-calc-slider"
          min={MIN_AMT}
          max={MAX_AMT}
          step={5000}
          value={amount}
          onChange={(e) => setAmount(clamp(Number(e.target.value)))}
          aria-label="Amount slider"
          style={{
            width: "100%",
            height: 6,
            WebkitAppearance: "none",
            appearance: "none",
            borderRadius: 999,
            cursor: "pointer",
            background: `linear-gradient(to right, ${c.blue} ${sliderPct}%, color-mix(in srgb, var(--text-tertiary) 18%, transparent) ${sliderPct}%)`,
          }}
        />
        <style>{`
          .vm-calc-slider::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:16px;height:16px;border-radius:999px;background:var(--pivot-blue);cursor:pointer;border:2px solid var(--bg-base);}
          .vm-calc-slider::-moz-range-thumb{width:16px;height:16px;border:2px solid var(--bg-base);border-radius:999px;background:var(--pivot-blue);cursor:pointer;}
        `}</style>
      </div>

      {/* the chart */}
      {isOption ? (
        <OptionPayoffChart om={om!} amount={amount} c={c} />
      ) : hasEpisodes ? (
        <EpisodeOutcomeChart rets={rets} amount={amount} c={c} />
      ) : null}

      {/* the numbers — the honest triplet, live-rescaled */}
      {isOption ? (
        <OptionNumbers om={om!} amount={amount} horizon={horizon} />
      ) : hasEpisodes ? (
        <BasketNumbers
          rets={rets}
          amount={amount}
          n={n}
          nPos={nPos}
          horizon={horizon}
        />
      ) : (
        <p
          style={{
            margin: 0,
            fontFamily: "var(--font-display)",
            fontSize: 13,
            color: "var(--text-tertiary)",
            lineHeight: 1.5,
          }}
        >
          No finished per-occurrence history yet for this strategy — there&rsquo;s
          nothing to size here.
        </p>
      )}

      {/* the honest line */}
      <p
        style={{
          margin: 0,
          fontFamily: "var(--font-display)",
          fontSize: 13,
          lineHeight: 1.5,
          color: "var(--text-tertiary)",
        }}
      >
        {isOption
          ? "Modelled at the underlying's realised volatility; final strikes and premium are set at deploy. This is analysis, not financial advice."
          : "A prediction market shows one fixed payout. This shows the full range of what actually happened across the real past occurrences — no single guaranteed number. This is analysis, not financial advice."}
      </p>
    </div>
  );
}

/** Per-occurrence outcome dots: each real episode's ₹ P&L, baseline 0. */
function EpisodeOutcomeChart({
  rets,
  amount,
  c,
}: {
  rets: number[];
  amount: number;
  c: Record<string, string>;
}): React.ReactElement | null {
  if (rets.length < 2) return null;
  const data = rets.map((r, i) => ({ i: i + 1, pnl: (amount * r) / 100, ret: r }));
  const med = (amount * median(rets)) / 100;

  return (
    <div style={{ height: 168 }}>
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 14, right: 10, bottom: 4, left: 6 }}>
          <XAxis type="number" dataKey="i" hide domain={[0, rets.length + 1]} />
          <YAxis
            type="number"
            dataKey="pnl"
            tickFormatter={(v: number) => inrCompact(v)}
            tick={{ fontFamily: "var(--font-display)", fontSize: 13, fill: c.tertiary }}
            axisLine={false}
            tickLine={false}
            width={54}
          />
          <ReferenceLine y={0} stroke={c.borderFocus} strokeDasharray="3 3" />
          <ReferenceLine
            y={med}
            stroke={c.ink}
            strokeWidth={1.5}
            label={{
              value: `typical ${inrCompact(med)}`,
              position: "right",
              fill: c.secondary,
              fontFamily: "var(--font-display)",
              fontSize: 13,
              fontWeight: 600,
            }}
          />
          <Tooltip
            cursor={{ stroke: c.border }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0]!.payload as { pnl: number; ret: number };
              return (
                <div
                  style={{
                    background: "var(--bg-base)",
                    border: `1px solid ${c.border}`,
                    borderRadius: 8,
                    padding: "6px 10px",
                    fontFamily: "var(--font-display)",
                    fontVariantNumeric: "tabular-nums",
                    fontSize: 13,
                    color: "var(--text-primary)",
                  }}
                >
                  <div style={{ fontWeight: 700 }}>
                    {inr(d.pnl)}{" "}
                    <span style={{ color: d.ret >= 0 ? c.profit : c.loss }}>
                      ({pct(d.ret)})
                    </span>
                  </div>
                  <div style={{ color: "var(--text-tertiary)" }}>one past occurrence</div>
                </div>
              );
            }}
          />
          <Scatter data={data} isAnimationActive={false}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.pnl >= 0 ? c.profit : c.loss} fillOpacity={0.8} />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

/** The priced option payoff curve, ₹ P&L vs terminal underlying move. */
function OptionPayoffChart({
  om,
  amount,
  c,
}: {
  om: NonNullable<ExpressionDetail["option_model"]>;
  amount: number;
  c: Record<string, string>;
}): React.ReactElement {
  const id = React.useId().replace(/[^a-zA-Z0-9]/g, "");
  const data = om.payoff.map((p) => ({ move: p.move_pct, pnl: (amount * p.pnl_pct) / 100 }));
  const pnls = data.map((d) => d.pnl);
  const maxP = Math.max(...pnls, 0);
  const minP = Math.min(...pnls, 0);
  const zero = maxP - minP === 0 ? 0.5 : maxP / (maxP - minP);

  return (
    <div style={{ height: 168 }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 14, right: 12, bottom: 4, left: 6 }}>
          <defs>
            <linearGradient id={`pf-${id}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset={0} stopColor={c.profit} stopOpacity={0.3} />
              <stop offset={zero} stopColor={c.profit} stopOpacity={0.04} />
              <stop offset={zero} stopColor={c.loss} stopOpacity={0.04} />
              <stop offset={1} stopColor={c.loss} stopOpacity={0.3} />
            </linearGradient>
            <linearGradient id={`ps-${id}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset={0} stopColor={c.profit} />
              <stop offset={zero} stopColor={c.profit} />
              <stop offset={zero} stopColor={c.loss} />
              <stop offset={1} stopColor={c.loss} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="move"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v: number) => `${v > 0 ? "+" : ""}${v.toFixed(0)}%`}
            tick={{ fontFamily: "var(--font-display)", fontSize: 13, fill: c.tertiary }}
            axisLine={false}
            tickLine={false}
            minTickGap={40}
          />
          <YAxis
            tickFormatter={(v: number) => inrCompact(v)}
            tick={{ fontFamily: "var(--font-display)", fontSize: 13, fill: c.tertiary }}
            axisLine={false}
            tickLine={false}
            width={54}
          />
          <ReferenceLine y={0} stroke={c.borderFocus} strokeDasharray="3 3" />
          <ReferenceLine
            x={om.breakeven_move_pct}
            stroke={c.warn}
            strokeDasharray="3 3"
            label={{
              value: "breakeven",
              position: "insideTopRight",
              fill: c.warn,
              fontFamily: "var(--font-display)",
              fontSize: 13,
            }}
          />
          <Area
            type="linear"
            dataKey="pnl"
            stroke={`url(#ps-${id})`}
            strokeWidth={2}
            fill={`url(#pf-${id})`}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function BasketNumbers({
  rets,
  amount,
  n,
  nPos,
  horizon,
}: {
  rets: number[];
  amount: number;
  n: number;
  nPos: number;
  horizon: string;
}): React.ReactElement {
  const med = median(rets);
  const worst = rets.length ? Math.min(...rets) : 0;
  const best = rets.length ? Math.max(...rets) : 0;
  const typical = (amount * med) / 100;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
        gap: "14px 18px",
      }}
    >
      <Stat label="You put in" value={inr(amount)} valueSize="lg" />
      <Stat
        label="Typical outcome"
        value={
          <span style={{ color: typical >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}>
            {typical >= 0 ? "+" : "−"}
            {inr(Math.abs(typical))}
          </span>
        }
        valueSize="lg"
        sub={`median · ${pct(med)}`}
      />
      <Stat
        label="Range across occurrences"
        value={
          <span style={{ fontSize: 15 }}>
            <span style={{ color: "var(--color-loss)" }}>{inrCompact((amount * worst) / 100)}</span>
            <span style={{ color: "var(--text-tertiary)" }}> → </span>
            <span style={{ color: "var(--color-profit)" }}>{inrCompact((amount * best) / 100)}</span>
          </span>
        }
        valueSize="md"
        sub={`worst → best (${pct(worst)} to ${pct(best)})`}
      />
      <Stat
        label="Ended positive"
        value={
          <>
            <Num size="lg">{nPos}</Num>
            <span style={{ color: "var(--text-tertiary)", fontSize: 15 }}> of {n}</span>
          </>
        }
        sub={n ? `${Math.round((nPos / n) * 100)}% of the time` : undefined}
      />
      <Stat label="Held for" value={<span style={{ fontSize: 15 }}>{horizon}</span>} valueSize="md" />
    </div>
  );
}

function OptionNumbers({
  om,
  amount,
  horizon,
}: {
  om: NonNullable<ExpressionDetail["option_model"]>;
  amount: number;
  horizon: string;
}): React.ReactElement {
  const maxLoss = (amount * om.max_loss_pct) / 100; // negative
  const maxProfit = (amount * om.max_profit_pct) / 100;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
        gap: "14px 18px",
      }}
    >
      <Stat label="You put in" value={inr(amount)} valueSize="lg" sub="the premium (capital at risk)" />
      <Stat
        label="Most you can lose"
        value={<span style={{ color: "var(--color-loss)" }}>{inr(maxLoss)}</span>}
        valueSize="lg"
        sub="if it expires out of the money"
      />
      <Stat
        label="Most you can make"
        value={<span style={{ color: "var(--color-profit)" }}>+{inr(maxProfit)}</span>}
        valueSize="lg"
        sub={`capped · ${pct(om.max_profit_pct, 0)} of capital`}
      />
      <Stat
        label="Chance of profit"
        value={<Num size="lg">{om.pop_pct.toFixed(0)}%</Num>}
        sub={`breakeven ${pct(om.breakeven_move_pct)} move`}
      />
      <Stat label="By" value={<span style={{ fontSize: 15 }}>{horizon}</span>} valueSize="md" />
    </div>
  );
}

export default StrategyCalculator;
