"use client";

/**
 * PortfolioGreeksPanel — Paper Trading dashboard section for F&O Greeks.
 *
 * Fetches GET /paper/greeks on mount and on manual refresh. Renders the
 * PortfolioGreeksCard from the chat surface (shared component). Shows a
 * skeleton while loading and an error message on failure.
 *
 * Wire in: PaperDashboard.tsx (below holdings in the Overview tab, or as
 * a dedicated "Greeks" tab).
 */

import { useEffect, useState, useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { fetchPaperGreeks } from "@/lib/api";
import { isError } from "@/lib/types";
import type { PortfolioGreeksPayload } from "@/lib/types";
import { Skeleton } from "@/components/ui/skeleton";
import { PortfolioGreeksCard } from "@/components/chat/PortfolioGreeksCard";

// ---------------------------------------------------------------------------
// State discriminant
// ---------------------------------------------------------------------------

type S =
  | { k: "loading" }
  | { k: "ok"; d: PortfolioGreeksPayload }
  | { k: "err"; msg: string }
  | { k: "empty" };

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function LoadingSkeleton(): React.ReactElement {
  return (
    <div
      className="space-y-4"
      style={{
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        overflow: "hidden",
        padding: "20px 16px",
      }}
    >
      <div style={{ display: "flex", gap: 10, marginBottom: 4, alignItems: "center" }}>
        <Skeleton style={{ height: 16, width: 124 }} />
        <Skeleton style={{ height: 24, width: 72, borderRadius: 999 }} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginBottom: 16 }}>
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            style={{
              background: "color-mix(in srgb, var(--bg-secondary) 82%, transparent)",
              border: "1px solid color-mix(in srgb, var(--glass-border) 78%, transparent)",
              borderRadius: "calc(var(--radius-md) - 4px)",
              padding: "10px 12px",
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            <Skeleton style={{ height: 10, width: "44%" }} />
            <Skeleton style={{ height: 22, width: "74%" }} />
            <Skeleton style={{ height: 10, width: "56%" }} />
          </div>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "2fr repeat(5, 1fr)", gap: 12, paddingBottom: 8 }}>
        <Skeleton style={{ height: 10, width: "28%" }} />
        {Array.from({ length: 5 }).map((__, j) => (
          <div key={j} style={{ display: "flex", justifyContent: "flex-end" }}>
            <Skeleton style={{ height: 10, width: "52%" }} />
          </div>
        ))}
      </div>
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          style={{
            display: "grid",
            gridTemplateColumns: "2fr repeat(5, 1fr)",
            gap: 12,
            padding: "12px 0",
            borderTop: "1px solid var(--glass-border)",
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <Skeleton style={{ height: 12, width: "44%" }} />
            <Skeleton style={{ height: 10, width: "24%" }} />
          </div>
          {Array.from({ length: 5 }).map((__, j) => (
            <div key={j} style={{ display: "flex", justifyContent: "flex-end" }}>
              <Skeleton style={{ height: 12, width: "62%" }} />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

function ErrorState({ message }: { message: string }): React.ReactElement {
  return (
    <div
      style={{
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        padding: "28px 16px",
        textAlign: "center",
        fontFamily: "var(--font-ui)",
        fontSize: 13,
        color: "var(--text-tertiary)",
      }}
    >
      {message || "Couldn't load portfolio Greeks. Try again in a moment."}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export function PortfolioGreeksPanel(): React.ReactElement {
  const [s, setS] = useState<S>({ k: "loading" });
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (isRefresh = false): Promise<void> => {
    if (isRefresh) setRefreshing(true);
    else setS({ k: "loading" });

    try {
      const result = await fetchPaperGreeks();
      if (isError(result)) {
        setS({ k: "err", msg: result.error.message });
        return;
      }
      const d = result.data;
      setS(d.position_count === 0 ? { k: "empty" } : { k: "ok", d });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Network error";
      setS({ k: "err", msg });
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Section header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <h2
          className="q-display"
          style={{
            margin: 0,
            fontSize: 15,
            fontWeight: 600,
            color: "var(--text-primary)",
          }}
        >
          Greeks
        </h2>
        <button
          type="button"
          onClick={() => void load(true)}
          disabled={refreshing || s.k === "loading"}
          aria-label="Refresh Greeks"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            padding: "4px 10px",
            borderRadius: "var(--radius-pill)",
            border: "1px solid var(--glass-border)",
            background: "var(--bg-secondary)",
            fontFamily: "var(--font-ui)",
            fontSize: 11.5,
            fontWeight: 500,
            color: "var(--text-secondary)",
            cursor: refreshing || s.k === "loading" ? "not-allowed" : "pointer",
            opacity: refreshing || s.k === "loading" ? 0.6 : 1,
            transition: "opacity 0.2s ease",
          }}
        >
          <RefreshCw
            size={11}
            style={{
              animation: refreshing ? "spin 1s linear infinite" : undefined,
            }}
            aria-hidden
          />
          Refresh
        </button>
      </div>

      {/* Content */}
      {s.k === "loading" && <LoadingSkeleton />}
      {s.k === "err" && <ErrorState message={s.msg} />}
      {(s.k === "ok" || s.k === "empty") && (
        <PortfolioGreeksCard
          payload={
            s.k === "ok"
              ? s.d
              : {
                  _render_hint: "portfolio_greeks_card",
                  net: { delta: 0, gamma: 0, theta: 0, vega: 0 },
                  delta_notional: 0,
                  by_underlying: {},
                  by_expiry: {},
                  position_count: 0,
                  unmarked: [],
                  note: "No F&O positions with Greek data in your paper book.",
                }
          }
        />
      )}
    </div>
  );
}
