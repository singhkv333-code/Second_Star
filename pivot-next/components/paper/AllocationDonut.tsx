"use client";

/**
 * AllocationDonut — Quartr card that aggregates paper holdings by sector and
 * renders a recharts donut + a self-built legend. Money via @/components/paper/format.
 * Slice fills use a fixed hex palette (CSS vars per-slice are awkward); every
 * other color comes from a Quartr token.
 */

import { useEffect, useState } from "react";
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Tooltip as RTooltip,
} from "recharts";
import { getPaperHoldings, type PaperHolding } from "@/lib/api";
import { isError } from "@/lib/types";
import { inr, pct } from "@/components/paper/format";
import { Skeleton } from "@/components/ui/skeleton";

// Fixed pleasant palette for slice fills only. Hexes are allowed here because
// per-slice CSS vars are awkward; everything else uses Quartr tokens.
const PALETTE: readonly string[] = [
  "#5B8DEF", // blue
  "#34C77B", // green
  "#E0A458", // amber
  "#A78BFA", // violet
  "#F26D6D", // red
  "#4FD1C5", // teal
  "#F49AC1", // pink
  "#9CA3AF", // slate (overflow / "Other")
];

type SectorSlice = { sector: string; value: number; color: string };

type S =
  | { k: "loading" }
  | { k: "ok"; slices: SectorSlice[]; total: number }
  | { k: "err" }
  | { k: "empty" };

function aggregate(holdings: PaperHolding[]): { slices: SectorSlice[]; total: number } {
  const bySector = new Map<string, number>();
  for (const h of holdings) {
    const mv = h.market_value;
    if (mv === null || mv === undefined || Number.isNaN(mv) || mv <= 0) continue;
    const sector = h.sector && h.sector.trim().length > 0 ? h.sector : "Unclassified";
    bySector.set(sector, (bySector.get(sector) ?? 0) + mv);
  }

  const sorted = Array.from(bySector.entries()).sort((a, b) => b[1] - a[1]);
  const total = sorted.reduce((acc, [, v]) => acc + v, 0);

  // Keep the top 7 sectors; fold the rest into a single "Other" slice so the
  // donut + legend stay readable and the palette never overflows.
  const MAX = PALETTE.length - 1;
  let slices: SectorSlice[];
  if (sorted.length > PALETTE.length) {
    const head = sorted.slice(0, MAX);
    const tailValue = sorted
      .slice(MAX)
      .reduce((acc, [, v]) => acc + v, 0);
    slices = head.map(([sector, value], i) => ({
      sector,
      value,
      color: PALETTE[i] as string,
    }));
    // "Other sectors" (not "Other") so the overflow bucket can never collide
    // with a real backend sector literally named "Other" (duplicate React key).
    slices.push({ sector: "Other sectors", value: tailValue, color: PALETTE[MAX] as string });
  } else {
    slices = sorted.map(([sector, value], i) => ({
      sector,
      value,
      color: PALETTE[i % PALETTE.length] as string,
    }));
  }

  return { slices, total };
}

export function AllocationDonut(): React.ReactElement {
  const [s, setS] = useState<S>({ k: "loading" });

  useEffect(() => {
    let on = true;
    getPaperHoldings()
      .then((r) => {
        if (!on) return;
        if (isError(r)) {
          setS({ k: "err" });
          return;
        }
        const { slices, total } = aggregate(r.data);
        if (slices.length === 0 || total <= 0) {
          setS({ k: "empty" });
          return;
        }
        setS({ k: "ok", slices, total });
      })
      .catch(() => {
        if (on) setS({ k: "err" });
      });
    return () => {
      on = false;
    };
  }, []);

  return (
    <div
      className="flex flex-col"
      style={{
        gap: 12,
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
      <div className="flex items-baseline justify-between" style={{ gap: 8 }}>
        <div className="q-uppercase-label">Allocation by sector</div>
        {s.k === "ok" && (
          <div
            className="q-mono tabular-nums"
            style={{ fontSize: 13, color: "var(--text-secondary)" }}
          >
            {inr(s.total, 0)}
          </div>
        )}
      </div>

      {s.k === "loading" && <LoadingState />}
      {s.k === "err" && <ErrorState />}
      {s.k === "empty" && <EmptyState />}
      {s.k === "ok" && <Chart slices={s.slices} total={s.total} />}
    </div>
  );
}

function Chart({
  slices,
  total,
}: {
  slices: SectorSlice[];
  total: number;
}): React.ReactElement {
  const ariaLabel =
    "Allocation by sector. " +
    slices
      .map((s) => `${s.sector} ${Math.round((s.value / total) * 100)} percent`)
      .join(", ") +
    ".";
  return (
    <div className="flex flex-col" style={{ gap: 14 }}>
      <div role="img" aria-label={ariaLabel} style={{ width: "100%", height: 240 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={slices}
              dataKey="value"
              nameKey="sector"
              cx="50%"
              cy="50%"
              innerRadius={55}
              outerRadius={85}
              paddingAngle={2}
              stroke="var(--bg-primary)"
              strokeWidth={2}
              isAnimationActive={false}
            >
              {slices.map((slice) => (
                <Cell key={slice.sector} fill={slice.color} />
              ))}
            </Pie>
            <RTooltip
              cursor={false}
              content={<SliceTooltip total={total} />}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>

      <Legend slices={slices} total={total} />
    </div>
  );
}

type TooltipPayloadItem = {
  name?: string | number;
  value?: number;
  payload?: SectorSlice;
};

function SliceTooltip({
  active,
  payload,
  total,
}: {
  active?: boolean;
  payload?: TooltipPayloadItem[];
  total: number;
}): React.ReactElement | null {
  if (!active || !payload || payload.length === 0) return null;
  const item = payload[0];
  const slice = item?.payload;
  if (!slice) return null;
  const value = typeof item?.value === "number" ? item.value : slice.value;
  const share = total > 0 ? (value / total) * 100 : 0;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        padding: "8px 10px",
        background: "var(--bg-elevated)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-sm)",
        minWidth: 140,
      }}
    >
      <div
        className="flex items-center"
        style={{ gap: 6 }}
      >
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "var(--radius-xs)",
            background: slice.color,
            flexShrink: 0,
          }}
        />
        <span
          style={{
            fontSize: 12,
            fontWeight: 550,
            color: "var(--text-primary)",
          }}
        >
          {slice.sector}
        </span>
      </div>
      <div
        className="flex items-baseline justify-between"
        style={{ gap: 12 }}
      >
        <span
          className="q-mono tabular-nums"
          style={{ fontSize: 12, color: "var(--text-secondary)" }}
        >
          {inr(value, 0)}
        </span>
        <span
          className="q-mono tabular-nums"
          style={{ fontSize: 12, color: "var(--text-tertiary)" }}
        >
          {pct(share).replace("+", "")}
        </span>
      </div>
    </div>
  );
}

function Legend({
  slices,
  total,
}: {
  slices: SectorSlice[];
  total: number;
}): React.ReactElement {
  return (
    <div
      className="grid"
      style={{
        gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
        gap: "8px 14px",
      }}
    >
      {slices.map((slice) => {
        const share = total > 0 ? (slice.value / total) * 100 : 0;
        return (
          <div
            key={slice.sector}
            className="flex items-center"
            style={{ gap: 8, minWidth: 0 }}
          >
            <span
              style={{
                width: 10,
                height: 10,
                borderRadius: "var(--radius-xs)",
                background: slice.color,
                flexShrink: 0,
              }}
            />
            <span
              style={{
                fontSize: 12,
                color: "var(--text-secondary)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                flex: "1 1 auto",
                minWidth: 0,
              }}
              title={slice.sector}
            >
              {slice.sector}
            </span>
            <span
              className="q-mono tabular-nums"
              style={{
                fontSize: 12,
                color: "var(--text-tertiary)",
                flexShrink: 0,
              }}
            >
              {pct(share).replace("+", "")}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function LoadingState(): React.ReactElement {
  return (
    <div className="flex flex-col" style={{ gap: 14 }}>
      <div
        className="flex items-center justify-center"
        style={{ height: 240 }}
      >
        <Skeleton
          style={{
            width: 170,
            height: 170,
            borderRadius: "9999px",
          }}
        />
      </div>
      <div
        className="grid"
        style={{
          gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
          gap: "8px 14px",
        }}
      >
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="flex items-center" style={{ gap: 8 }}>
            <Skeleton style={{ width: 10, height: 10, borderRadius: 3 }} />
            <Skeleton style={{ height: 12, flex: "1 1 auto" }} />
          </div>
        ))}
      </div>
    </div>
  );
}

function ErrorState(): React.ReactElement {
  return (
    <div
      className="flex items-center justify-center text-center"
      style={{
        height: 240,
        fontSize: 13,
        color: "var(--text-tertiary)",
        padding: "0 16px",
      }}
    >
      Couldn&rsquo;t load allocation.
    </div>
  );
}

function EmptyState(): React.ReactElement {
  return (
    <div
      className="flex items-center justify-center text-center"
      style={{
        height: 240,
        fontSize: 13,
        color: "var(--text-tertiary)",
        padding: "0 24px",
        lineHeight: 1.5,
      }}
    >
      Allocation appears once you hold positions.
    </div>
  );
}
