"use client";

/**
 * KpiStatCards — the headline strip of the Paper Trading dashboard.
 *
 * Reads getPaperSummary() once on mount and renders a responsive grid of
 * Quartr stat cards: Portfolio Value, Total P&L, Day P&L, Buying Power,
 * Invested and Realized P&L. Money + P&L formatting and the profit/loss
 * colors come entirely from "@/components/paper/format" so this strip reads
 * identically to the rest of the dashboard.
 */

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { inr, pct, pnlColor, signedInr } from "@/components/paper/format";
import { getPaperSummary, type PaperSummaryData } from "@/lib/api";
import { isError } from "@/lib/types";

type S =
  | { k: "loading" }
  | { k: "ok"; d: PaperSummaryData }
  | { k: "err" }
  | { k: "empty" };

const GRID_STYLE: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
  gap: 12,
};

/** One Quartr stat card: uppercase label, big value, optional sub-line. */
function StatCard({
  label,
  value,
  valueColor,
  sub,
  badge,
}: {
  label: string;
  value: string;
  valueColor?: string;
  sub?: React.ReactNode;
  badge?: React.ReactNode;
}): React.ReactElement {
  return (
    <div
      className="flex flex-col"
      style={{
        gap: 8,
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
      <div className="flex items-center justify-between" style={{ gap: 8 }}>
        <span className="q-uppercase-label">{label}</span>
        {badge}
      </div>
      <span
        className="q-display tabular-nums"
        style={{
          fontSize: 22,
          lineHeight: 1.1,
          color: valueColor ?? "var(--text-primary)",
        }}
      >
        {value}
      </span>
      {sub ? <div style={{ marginTop: -2 }}>{sub}</div> : null}
    </div>
  );
}

/** A coloured pill for a percentage change (Total P&L sub-line). */
function PnlPill({ value }: { value: number }): React.ReactElement {
  const color = pnlColor(value);
  return (
    <Badge
      variant="secondary"
      className="tabular-nums"
      style={{
        background: "var(--bg-secondary)",
        color,
        fontFamily: "var(--font-mono)",
        fontSize: 11,
      }}
    >
      {pct(value)}
    </Badge>
  );
}

export function KpiStatCards(): React.ReactElement {
  const [s, setS] = useState<S>({ k: "loading" });

  useEffect(() => {
    let on = true;
    getPaperSummary()
      .then((r) => {
        if (!on) return;
        if (isError(r)) {
          setS({ k: "err" });
          return;
        }
        const d = r.data;
        if (!d.exists) {
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
      <div style={GRID_STYLE}>
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton
            key={i}
            style={{ height: 84, borderRadius: "var(--radius-md)" }}
          />
        ))}
      </div>
    );
  }

  if (s.k === "err") {
    return (
      <div
        className="flex items-center"
        style={{
          padding: "14px 16px",
          background: "var(--bg-primary)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-md)",
          color: "var(--text-tertiary)",
          fontFamily: "var(--font-ui)",
          fontSize: 13,
        }}
      >
        Couldn&apos;t load your paper portfolio.
      </div>
    );
  }

  if (s.k === "empty") {
    return (
      <div
        className="flex flex-col items-center justify-center text-center"
        style={{
          gap: 6,
          padding: "32px 16px",
          background: "var(--bg-primary)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-md)",
        }}
      >
        <span
          className="q-display"
          style={{ fontSize: 15, color: "var(--text-secondary)" }}
        >
          No paper activity yet
        </span>
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 13,
            color: "var(--text-tertiary)",
          }}
        >
          Your triggered orders will show up here.
        </span>
      </div>
    );
  }

  const d = s.d;

  return (
    <div style={GRID_STYLE}>
      <StatCard
        label="Portfolio Value"
        value={inr(d.nav)}
        badge={
          d.is_stale ? (
            <Badge
              variant="secondary"
              style={{
                background: "var(--bg-secondary)",
                color: "var(--text-tertiary)",
                fontFamily: "var(--font-ui)",
                fontSize: 10,
              }}
            >
              stale
            </Badge>
          ) : null
        }
      />

      <StatCard
        label="Total P&amp;L"
        value={signedInr(d.total_pnl)}
        valueColor={pnlColor(d.total_pnl)}
        sub={<PnlPill value={d.total_pnl_pct} />}
      />

      <StatCard
        label="Day P&amp;L"
        value={signedInr(d.day_pnl)}
        valueColor={pnlColor(d.day_pnl)}
      />

      <StatCard label="Buying Power" value={inr(d.buying_power)} />

      <StatCard label="Invested" value={inr(d.invested)} />

      <StatCard
        label="Realized P&amp;L"
        value={signedInr(d.realized_pnl_cum)}
        valueColor={pnlColor(d.realized_pnl_cum)}
      />
    </div>
  );
}
