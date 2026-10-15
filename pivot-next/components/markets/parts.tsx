"use client";

/**
 * Small pieces the Markets tab is built from. Every one reads theme tokens,
 * so light and dark both hold without props.
 */

import { useRouter } from "next/navigation";
import { useId, useMemo, useState } from "react";
import { ChartLineUp, ChatCircle, type IconProps } from "./icons";
import { askPivot } from "@/lib/launch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { fmtPct } from "@/lib/markets";
import s from "./markets.module.css";

export { s as css };

export function cx(...xs: (string | false | null | undefined)[]): string {
  return xs.filter(Boolean).join(" ");
}

// ── headings ─────────────────────────────────────────────────────────────

export function SectionHead({
  title,
  caption,
  right,
}: {
  title: string;
  caption?: string;
  right?: React.ReactNode;
}): React.ReactElement {
  return (
    <div className={s.sectionHead}>
      <div className="min-w-0">
        <h2 className={s.sectionTitle}>{title}</h2>
        {caption && <p className={s.sectionCaption}>{caption}</p>}
      </div>
      {right && <div className="flex min-w-0 max-w-full items-center gap-2">{right}</div>}
    </div>
  );
}

export function CardHead({
  Icon,
  title,
  right,
}: {
  Icon?: React.ComponentType<IconProps>;
  title: string;
  right?: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-2">
        {Icon && <Icon size={15} className={s.muted} />}
        <h3 className={s.cardTitle}>{title}</h3>
      </div>
      {right}
    </div>
  );
}

// ── controls ─────────────────────────────────────────────────────────────

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  size = "md",
  label,
}: {
  options: readonly T[] | { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  size?: "sm" | "md";
  label: string;
}): React.ReactElement {
  const opts = (options as (T | { value: T; label: string })[]).map((o) =>
    typeof o === "string" ? { value: o, label: o } : o,
  );
  return (
    <div role="tablist" aria-label={label} className={cx(s.segmented, size === "sm" && s.segSm)}>
      {opts.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          data-active={o.value === value}
          className={s.segItem}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Hint({ text, children }: { text: string; children: React.ReactElement }): React.ReactElement {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-[260px] text-[12px] leading-snug">
        {text}
      </TooltipContent>
    </Tooltip>
  );
}

// ── figures ──────────────────────────────────────────────────────────────

export function Delta({
  v,
  digits = 2,
  className,
  suffix,
}: {
  v: number | null | undefined;
  digits?: number;
  className?: string;
  suffix?: string;
}): React.ReactElement {
  const tone = typeof v === "number" && v !== 0 ? (v > 0 ? s.up : s.down) : s.muted;
  return (
    <span className={cx(s.num, tone, className)}>
      {fmtPct(v, digits)}
      {suffix}
    </span>
  );
}

/** A line of closes. Neutral ink; the end colour says which way it went. */
export function Spark({
  values,
  width = 96,
  height = 28,
  area = true,
  fluid = false,
}: {
  values: number[] | null | undefined;
  width?: number;
  height?: number;
  area?: boolean;
  /** Stretch to the parent's width; `width` is then only the drawing grid. */
  fluid?: boolean;
}): React.ReactElement {
  const id = useId().replace(/:/g, "");
  const path = useMemo(() => {
    if (!values || values.length < 2) return null;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const line = values
      .map((v, i) => {
        const x = (i / (values.length - 1)) * width;
        const y = height - 2 - ((v - min) / span) * (height - 4);
        return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join("");
    const first = values[0] ?? 0;
    const last = values[values.length - 1] ?? 0;
    return { line, fill: `${line}L${width},${height}L0,${height}Z`, up: last >= first };
  }, [values, width, height]);
  if (!path) return <span style={{ display: "block", width: fluid ? "100%" : width, height }} aria-hidden />;
  const tone = path.up ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <svg
      width={fluid ? "100%" : width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio={fluid ? "none" : undefined}
      aria-hidden
      style={{ display: "block", overflow: "visible" }}
    >
      {area && (
        <>
          <defs>
            <linearGradient id={`g${id}`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={tone} stopOpacity="0.14" />
              <stop offset="100%" stopColor={tone} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={path.fill} fill={`url(#g${id})`} />
        </>
      )}
      <path d={path.line} fill="none" stroke={tone} strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Where a price sits between two bounds: a hairline with a tick. */
export function RangeBar({ pos, width = 64 }: { pos: number | null; width?: number | string }): React.ReactElement {
  return (
    <span
      aria-hidden
      style={{ position: "relative", display: "inline-block", width, height: 10, verticalAlign: "middle" }}
    >
      <span style={{ position: "absolute", left: 0, right: 0, top: 4, height: 2, borderRadius: 2, background: "var(--surface-track)" }} />
      {pos != null && (
        <span
          style={{
            position: "absolute",
            top: 0,
            left: `calc(${(pos * 100).toFixed(1)}% - 1px)`,
            width: 2,
            height: 10,
            borderRadius: 1,
            background: "var(--text-primary)",
          }}
        />
      )}
    </span>
  );
}

// ── logos ────────────────────────────────────────────────────────────────

export function Mark({ src, label, size = 28 }: { src?: string | null; label: string; size?: number }): React.ReactElement {
  const [broken, setBroken] = useState(false);
  const box: React.CSSProperties = {
    width: size,
    height: size,
    flex: "none",
    borderRadius: 8,
    background: "var(--bg-base)",
    overflow: "hidden",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  };
  if (!src || broken) {
    return (
      <span aria-hidden style={{ ...box, fontSize: size * 0.42, fontWeight: 650, color: "var(--text-primary)" }}>
        {label.trim()[0]?.toUpperCase() ?? "·"}
      </span>
    );
  }
  return (
    <span aria-hidden style={box}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt=""
        width={size}
        height={size}
        loading="lazy"
        style={{ width: "100%", height: "100%", objectFit: "contain" }}
        onError={() => setBroken(true)}
      />
    </span>
  );
}

// ── actions ──────────────────────────────────────────────────────────────

export function openChart(symbol: string): void {
  window.dispatchEvent(new CustomEvent("pivot:open-chart", { detail: { symbol } }));
}

export function useOpenStock(): (symbol: string) => void {
  const router = useRouter();
  return (symbol: string) => router.push(`/stock/${encodeURIComponent(symbol)}`);
}

/** Chart and Ask, revealed on row hover. */
export function RowActions({ symbol, name }: { symbol: string; name: string }): React.ReactElement {
  const stop = (e: React.MouseEvent): void => {
    e.preventDefault();
    e.stopPropagation();
  };
  return (
    <span className={cx(s.rowActions, "inline-flex items-center gap-0.5")}>
      <Hint text="Open in chart">
        <button
          type="button"
          aria-label={`Open ${symbol} in the chart`}
          className={s.iconBtn}
          onClick={(e) => { stop(e); openChart(symbol); }}
        >
          <ChartLineUp size={15} />
        </button>
      </Hint>
      <Hint text="Ask Pivot">
        <button
          type="button"
          aria-label={`Ask Pivot about ${symbol}`}
          className={s.iconBtn}
          onClick={(e) => { stop(e); askPivot(`What is moving ${name} (${symbol}) and how does it compare with its sector?`); }}
        >
          <ChatCircle size={15} />
        </button>
      </Hint>
    </span>
  );
}

export function Empty({ text, height = 120 }: { text: string; height?: number }): React.ReactElement {
  return (
    <div className="flex items-center justify-center text-center" style={{ minHeight: height, fontSize: 12.5, color: "var(--text-primary)" }}>
      {text}
    </div>
  );
}
