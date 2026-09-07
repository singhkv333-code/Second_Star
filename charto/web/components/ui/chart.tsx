"use client";

/**
 * chart.tsx — Pivot's thin Recharts wrapper, adapted from shadcn/ui charts.
 *
 * Deviations from stock shadcn (intentional, see Views redesign spec §1):
 *  - We do NOT use shadcn's --chart-1..5 palette. Series colors bind to our
 *    live theme tokens (var(--color-profit) / loss / warn / pivot-blue /
 *    text-*). A ChartConfig entry's `color` is expected to be a CSS var()
 *    string (e.g. "var(--color-profit)"). ChartStyle emits `--color-<key>`
 *    custom props so Recharts series can reference `var(--color-<key>)`.
 *  - ChartContainer sets fontFamily: var(--font-display) (Inter) and
 *    fontVariantNumeric: tabular-nums at the root, so every axis tick / label /
 *    tooltip number inherits Inter-tabular — this kills mono numerals in charts
 *    at the root. NEVER var(--font-numeric) here.
 *  - SSR-safe: no window access at module/render time.
 */

import * as React from "react";
import * as RechartsPrimitive from "recharts";

import { cn } from "@/lib/utils";

export type ChartConfig = Record<
  string,
  {
    label?: React.ReactNode;
    icon?: React.ComponentType;
    /** A CSS var() string or concrete color, emitted as --color-<key>. */
    color?: string;
  }
>;

type ChartContextValue = { config: ChartConfig };

const ChartContext = React.createContext<ChartContextValue | null>(null);

function useChart(): ChartContextValue {
  const ctx = React.useContext(ChartContext);
  if (!ctx) {
    throw new Error("useChart must be used within a <ChartContainer />");
  }
  return ctx;
}

/* ────────────────────────────────────────────────────────────────────
 * ChartContainer — root: ResponsiveContainer + token style + Inter-tabular.
 * ──────────────────────────────────────────────────────────────────── */

function ChartContainer({
  id,
  className,
  children,
  config,
  style,
  ...props
}: React.ComponentProps<"div"> & {
  config: ChartConfig;
  children: React.ComponentProps<
    typeof RechartsPrimitive.ResponsiveContainer
  >["children"];
}): React.ReactElement {
  const uniqueId = React.useId();
  const chartId = `chart-${(id || uniqueId).replace(/:/g, "")}`;

  return (
    <ChartContext.Provider value={{ config }}>
      <div
        data-chart={chartId}
        className={cn("w-full", className)}
        style={{
          fontFamily: "var(--font-display)",
          fontVariantNumeric: "tabular-nums",
          ...style,
        }}
        {...props}
      >
        <ChartStyle id={chartId} config={config} />
        <RechartsPrimitive.ResponsiveContainer width="100%" height="100%">
          {children}
        </RechartsPrimitive.ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * ChartStyle — injects --color-<key> custom props scoped to this chart.
 * Values are CSS var() strings, so they re-theme on the .dark flip for free.
 * ──────────────────────────────────────────────────────────────────── */

function ChartStyle({
  id,
  config,
}: {
  id: string;
  config: ChartConfig;
}): React.ReactElement | null {
  const colorEntries = Object.entries(config).filter(
    ([, c]) => c.color != null,
  );
  if (colorEntries.length === 0) return null;

  const body = colorEntries
    .map(([key, c]) => `  --color-${key}: ${c.color};`)
    .join("\n");

  return (
    <style
      dangerouslySetInnerHTML={{
        __html: `[data-chart=${id}] {\n${body}\n}`,
      }}
    />
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Tooltip
 * ──────────────────────────────────────────────────────────────────── */

const ChartTooltip = RechartsPrimitive.Tooltip;

type TooltipPayloadItem = {
  value?: number | string;
  name?: string;
  dataKey?: string | number;
  color?: string;
  payload?: Record<string, unknown>;
};

function ChartTooltipContent({
  active,
  payload,
  label,
  labelFormatter,
  formatter,
  hideLabel = false,
  hideIndicator = false,
  className,
}: {
  active?: boolean;
  payload?: TooltipPayloadItem[];
  label?: React.ReactNode;
  labelFormatter?: (label: React.ReactNode) => React.ReactNode;
  formatter?: (
    value: number | string,
    name: string,
    item: TooltipPayloadItem,
  ) => React.ReactNode;
  hideLabel?: boolean;
  hideIndicator?: boolean;
  className?: string;
}): React.ReactElement | null {
  const { config } = useChart();

  if (!active || !payload || payload.length === 0) return null;

  const resolvedLabel = labelFormatter ? labelFormatter(label) : label;

  return (
    <div
      className={cn("min-w-[8rem] px-2.5 py-1.5", className)}
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-sm)",
        fontFamily: "var(--font-display)",
        fontVariantNumeric: "tabular-nums",
        boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
      }}
    >
      {!hideLabel && resolvedLabel != null && (
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: "var(--text-tertiary)",
            marginBottom: 4,
          }}
        >
          {resolvedLabel}
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {payload.map((item, i) => {
          const key = String(item.dataKey ?? item.name ?? i);
          const indicatorColor = item.color || `var(--color-${key})`;
          const cfg = config[key];
          const name = cfg?.label ?? item.name ?? key;
          const value = item.value ?? "";
          return (
            <div
              key={key + i}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 11.5,
                color: "var(--text-primary)",
              }}
            >
              {!hideIndicator && (
                <span
                  aria-hidden
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: 2,
                    background: indicatorColor,
                    flexShrink: 0,
                  }}
                />
              )}
              {formatter ? (
                formatter(value, String(item.name ?? key), item)
              ) : (
                <>
                  <span style={{ color: "var(--text-tertiary)" }}>{name}</span>
                  <span
                    style={{
                      marginLeft: "auto",
                      fontWeight: 600,
                      letterSpacing: "-0.01em",
                    }}
                  >
                    {value}
                  </span>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Legend
 * ──────────────────────────────────────────────────────────────────── */

const ChartLegend = RechartsPrimitive.Legend;

type LegendPayloadItem = {
  value?: string;
  dataKey?: string | number;
  color?: string;
};

function ChartLegendContent({
  payload,
  className,
}: {
  payload?: LegendPayloadItem[];
  className?: string;
}): React.ReactElement | null {
  const { config } = useChart();
  if (!payload || payload.length === 0) return null;

  return (
    <div
      className={cn("flex flex-wrap items-center gap-x-4 gap-y-1.5", className)}
      style={{
        fontFamily: "var(--font-display)",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {payload.map((item, i) => {
        const key = String(item.dataKey ?? item.value ?? i);
        const cfg = config[key];
        return (
          <div
            key={key + i}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 11.5,
              color: "var(--text-secondary)",
            }}
          >
            <span
              aria-hidden
              style={{
                width: 8,
                height: 8,
                borderRadius: 2,
                background: item.color || `var(--color-${key})`,
              }}
            />
            {cfg?.label ?? item.value}
          </div>
        );
      })}
    </div>
  );
}

export {
  ChartContainer,
  ChartStyle,
  ChartTooltip,
  ChartTooltipContent,
  ChartLegend,
  ChartLegendContent,
  useChart,
};
