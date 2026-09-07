"use client";

/**
 * EquityCurveChart — the Paper Trading equity (NAV) curve.
 *
 * A Quartr card with a recharts AreaChart of NAV over the end-of-day snapshot
 * dates, with an optional rebased NIFTY benchmark overlay. The header shows the
 * latest NAV and the total return from the first to the last snapshot.
 */

import { useEffect, useId, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getPaperNavCurve, type PaperNavPoint } from "@/lib/api";
import { isError } from "@/lib/types";
import { Skeleton } from "@/components/ui/skeleton";
import { dateShort, inr, inrCompact, pct, pnlColor } from "@/components/paper/format";

type S<T> =
  | { k: "loading" }
  | { k: "ok"; d: T }
  | { k: "err" }
  | { k: "empty" };

/** A chart-ready point: NAV always present, benchmark rebased to NAV[0]. */
type ChartPoint = {
  as_of_date: string | null;
  nav: number;
  benchmark: number | null;
};

type ChartData = {
  points: ChartPoint[];
  latestNav: number;
  totalReturnPct: number;
  /** True only when every raw point carries a non-null nifty_close. */
  showBenchmark: boolean;
};

const CHART_HEIGHT = 260;

function buildChartData(raw: PaperNavPoint[]): ChartData | null {
  const first = raw[0];
  const last = raw[raw.length - 1];
  // Caller guarantees raw.length >= 2; guard anyway for the type-checker.
  if (!first || !last) return null;

  // Benchmark is only meaningful when every point has a NIFTY close AND the
  // base close is non-zero (avoid divide-by-zero / Infinity).
  const baseNifty = first.nifty_close;
  const showBenchmark =
    baseNifty !== null &&
    baseNifty !== 0 &&
    raw.every((p) => p.nifty_close !== null);

  const points: ChartPoint[] = raw.map((p) => {
    let benchmark: number | null = null;
    if (showBenchmark && p.nifty_close !== null && baseNifty) {
      benchmark = first.nav * (p.nifty_close / baseNifty);
    }
    return { as_of_date: p.as_of_date, nav: p.nav, benchmark };
  });

  const totalReturnPct =
    first.nav !== 0 ? ((last.nav - first.nav) / first.nav) * 100 : 0;

  return {
    points,
    latestNav: last.nav,
    totalReturnPct,
    showBenchmark,
  };
}

type TipPayload = {
  payload?: ChartPoint;
};

function ChartTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: TipPayload[];
}): React.ReactElement | null {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0]?.payload;
  if (!p) return null;
  return (
    <div
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-sm)",
        padding: "8px 10px",
      }}
    >
      <div
        className="q-uppercase-label"
        style={{ marginBottom: 4 }}
      >
        {dateShort(p.as_of_date)}
      </div>
      <div
        className="tabular-nums q-mono"
        style={{ color: "var(--text-primary)", fontSize: 13 }}
      >
        {inr(p.nav)}
      </div>
      {p.benchmark !== null ? (
        <div
          className="tabular-nums q-mono"
          style={{ color: "var(--text-tertiary)", fontSize: 12, marginTop: 2 }}
        >
          NIFTY {inr(p.benchmark)}
        </div>
      ) : null}
    </div>
  );
}

function CardShell({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div
      className="flex flex-col"
      style={{
        gap: 14,
        padding: "14px 16px",
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        transition: "border-color 0.35s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "var(--glass-border-hover)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--glass-border)";
      }}
    >
      {children}
    </div>
  );
}

export function EquityCurveChart(): React.ReactElement {
  const [s, setS] = useState<S<ChartData>>({ k: "loading" });

  useEffect(() => {
    let on = true;
    getPaperNavCurve()
      .then((r) => {
        if (!on) return;
        if (isError(r)) {
          setS({ k: "err" });
          return;
        }
        const raw = r.data;
        if (!Array.isArray(raw) || raw.length < 2) {
          setS({ k: "empty" });
          return;
        }
        const d = buildChartData(raw);
        if (!d) {
          setS({ k: "empty" });
          return;
        }
        setS({ k: "ok", d });
      })
      .catch(() => {
        if (on) setS({ k: "err" });
      });
    return () => {
      on = false;
    };
  }, []);

  if (s.k === "loading") {
    return (
      <CardShell>
        <div className="flex items-end justify-between" style={{ gap: 16 }}>
          <div className="flex flex-col" style={{ gap: 6 }}>
            <div className="q-uppercase-label">Equity curve</div>
            <Skeleton style={{ height: 26, width: 140 }} />
          </div>
          <Skeleton style={{ height: 14, width: 52 }} />
        </div>
        <Skeleton style={{ height: CHART_HEIGHT, width: "100%" }} />
      </CardShell>
    );
  }

  if (s.k === "err") {
    return (
      <CardShell>
        <div className="q-uppercase-label">Equity curve</div>
        <div
          style={{
            color: "var(--text-tertiary)",
            fontSize: 13,
            padding: "32px 0",
            textAlign: "center",
          }}
        >
          Couldn&rsquo;t load the equity curve.
        </div>
      </CardShell>
    );
  }

  if (s.k === "empty") {
    return (
      <CardShell>
        <div className="q-uppercase-label">Equity curve</div>
        <div
          style={{
            color: "var(--text-tertiary)",
            fontSize: 13,
            padding: "40px 16px",
            textAlign: "center",
            maxWidth: 360,
            marginInline: "auto",
          }}
        >
          Your equity curve appears after the first end-of-day snapshot.
        </div>
      </CardShell>
    );
  }

  return <EquityCurveBody d={s.d} />;
}

function EquityCurveBody({ d }: { d: ChartData }): React.ReactElement {
  // Deterministic, collision-free gradient id (colons stripped so it is a
  // valid CSS url(#id) reference) — avoids the Math.random() anti-pattern.
  const gradientId = `nav-fill-${useId().replace(/:/g, "")}`;

  return (
    <CardShell>
      <div className="flex items-end justify-between" style={{ gap: 16 }}>
        <div className="flex flex-col" style={{ gap: 4 }}>
          <div className="q-uppercase-label">Equity curve</div>
          <div
            className="q-display tabular-nums"
            style={{ color: "var(--text-primary)", fontSize: 26, lineHeight: 1 }}
          >
            {inr(d.latestNav)}
          </div>
        </div>
        <div
          className="tabular-nums q-mono"
          style={{ color: pnlColor(d.totalReturnPct), fontSize: 14 }}
        >
          {pct(d.totalReturnPct)}
        </div>
      </div>

      <div
        role="img"
        aria-label={`Equity curve. Latest NAV ${inr(d.latestNav)}, total return ${pct(d.totalReturnPct)}${d.showBenchmark ? ", with NIFTY benchmark overlay" : ""}.`}
        style={{ width: "100%", height: CHART_HEIGHT }}
      >
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={d.points}
            margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
          >
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop
                  offset="0%"
                  stopColor="var(--color-profit)"
                  stopOpacity={0.28}
                />
                <stop
                  offset="100%"
                  stopColor="var(--color-profit)"
                  stopOpacity={0}
                />
              </linearGradient>
            </defs>
            <CartesianGrid
              stroke="var(--glass-border)"
              strokeOpacity={0.6}
              vertical={false}
            />
            <XAxis
              dataKey="as_of_date"
              tickFormatter={(v: string | null) => dateShort(v)}
              tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
              axisLine={{ stroke: "var(--glass-border)" }}
              tickLine={false}
              minTickGap={28}
            />
            <YAxis
              tickFormatter={(v: number) => inrCompact(v)}
              tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={56}
              domain={["auto", "auto"]}
            />
            <RTooltip
              content={<ChartTooltip />}
              cursor={{ stroke: "var(--glass-border-hover)", strokeWidth: 1 }}
            />
            <Area
              type="monotone"
              dataKey="nav"
              stroke="var(--price-line)"
              strokeWidth={2}
              fill={`url(#${gradientId})`}
              dot={false}
              activeDot={{ r: 3, fill: "var(--price-line)", strokeWidth: 0 }}
              isAnimationActive={false}
            />
            {d.showBenchmark ? (
              <Line
                type="monotone"
                dataKey="benchmark"
                stroke="var(--text-tertiary)"
                strokeWidth={1.5}
                strokeDasharray="4 3"
                dot={false}
                activeDot={false}
                isAnimationActive={false}
              />
            ) : null}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </CardShell>
  );
}
