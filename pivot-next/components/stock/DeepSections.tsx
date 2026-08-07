"use client";

/**
 * The deep sections, below the existing overview / metrics / financial summary.
 *
 * Everything above this component is untouched. This is purely additive: the
 * chart, the overview card, the key-metrics strip and the financials panel
 * keep their layout and their data path.
 *
 * The organising rule is that COVERAGE DECIDES THE TABS. These assets range
 * from ~99% of the universe (ownership) down to 12% (market share), so the
 * page asks `/sections` what this company actually has and renders only those.
 * HDFCBANK, for instance, genuinely has no quarterly metrics — so it gets no
 * Quarters tab rather than an empty one. An empty panel reads as a broken
 * product; a missing tab reads as a page that knows what it holds.
 *
 * Panels load LAZILY, one fetch per tab on first open. Fetching all five up
 * front would cost five cross-database round trips for a reader who wanted one.
 */

import * as React from "react";

import {
  getStockMix, getStockQuarters, getStockSections,
  type MixResponse, type QuartersResponse, type StockSections,
} from "@/lib/api";
import { isError } from "@/lib/types";
import { MixPanel } from "./MixPanel";
import { QuartersPanel } from "./QuartersPanel";
import { EmptyNote, PanelSkeleton } from "./chrome";

type TabId = "quarters" | "revenue_mix";

const TAB_LABEL: Record<TabId, string> = {
  quarters: "Quarters",
  revenue_mix: "Segments",
};

// Numbers first, then what produced them.
//
// The annual-report, ownership and documents panels were cut from the page
// (see git history — the API endpoints and typed client for all three are
// still in place, so restoring one is a tab entry and an import, not a
// rebuild).
const TAB_ORDER: TabId[] = ["quarters", "revenue_mix"];

export function DeepSections({ symbol }: { symbol: string }): React.ReactElement | null {
  const [sections, setSections] = React.useState<StockSections | null>(null);
  const [failed, setFailed] = React.useState(false);
  const [tab, setTab] = React.useState<TabId | null>(null);

  const [basis, setBasis] = React.useState<"consolidated" | "standalone">("consolidated");

  const [quarters, setQuarters] = React.useState<QuartersResponse | null>(null);
  const [mix, setMix] = React.useState<MixResponse | null>(null);

  // ── coverage ─────────────────────────────────────────────────────────────
  React.useEffect(() => {
    let dead = false;
    setSections(null); setFailed(false); setTab(null);
    setQuarters(null); setMix(null);
    getStockSections(symbol)
      .then((r) => {
        if (dead) return;
        if (isError(r)) { setFailed(true); return; }
        setSections(r.data);
      })
      .catch(() => { if (!dead) setFailed(true); });
    return () => { dead = true; };
  }, [symbol]);

  const available = React.useMemo<TabId[]>(() => {
    if (!sections) return [];
    const c = sections.coverage;
    return TAB_ORDER.filter((t) => (c[t]?.count ?? 0) > 0);
  }, [sections]);

  // Open the first available tab once coverage lands. Deliberately not a
  // fixed default: a company with no quarters would otherwise open on a tab
  // that is not there.
  React.useEffect(() => {
    const first = available[0];
    if (tab === null && first) setTab(first);
  }, [available, tab]);

  // ── lazy panel loads ─────────────────────────────────────────────────────
  React.useEffect(() => {
    if (tab !== "quarters") return;
    let dead = false;
    setQuarters(null);
    getStockQuarters(symbol, basis, 24)
      .then((r) => { if (!dead && !isError(r)) setQuarters(r.data); })
      .catch(() => {});
    return () => { dead = true; };
  }, [tab, symbol, basis]);


  React.useEffect(() => {
    if (tab !== "revenue_mix" || mix) return;
    let dead = false;
    getStockMix(symbol)
      .then((r) => { if (!dead && !isError(r)) setMix(r.data); })
      .catch(() => {});
    return () => { dead = true; };
  }, [tab, symbol, mix]);



  // A company with none of this data gets nothing rather than an empty shell —
  // the page above still stands on its own.
  if (failed || (sections && available.length === 0)) return null;

  return (
    <section
      aria-label="Company detail"
      style={{ marginTop: 26, display: "flex", flexDirection: "column", gap: 16 }}
    >
      <div
        role="tablist"
        aria-label="Detail sections"
        style={{
          display: "flex",
          gap: 2,
          borderBottom: "1px solid var(--glass-border)",
          overflowX: "auto",
        }}
      >
        {sections === null
          ? <div style={{ height: 38 }} />
          : available.map((t) => {
            const on = t === tab;
            const count = sections.coverage[t]?.count ?? 0;
            return (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={on}
                onClick={() => setTab(t)}
                style={{
                  position: "relative",
                  padding: "9px 13px 10px",
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  fontFamily: "var(--font-ui)",
                  fontSize: 13,
                  fontWeight: on ? 600 : 500,
                  color: on ? "var(--text-primary)" : "var(--text-tertiary)",
                  whiteSpace: "nowrap",
                  transition: "color 120ms ease",
                }}
              >
                {TAB_LABEL[t]}
                <span
                  style={{
                    marginLeft: 6,
                    fontSize: 11,
                    opacity: 0.62,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {count.toLocaleString("en-IN")}
                </span>
                {on ? (
                  <span
                    style={{
                      position: "absolute",
                      left: 0, right: 0, bottom: -1,
                      height: 2,
                      borderRadius: 2,
                      background: "var(--text-primary)",
                    }}
                  />
                ) : null}
              </button>
            );
          })}
      </div>

      <div role="tabpanel">
        {sections === null ? <PanelSkeleton rows={7} /> : null}

        {tab === "quarters" ? (
          quarters
            ? <QuartersPanel data={quarters} basis={basis} onBasisChange={setBasis} />
            : <PanelSkeleton rows={8} />
        ) : null}


        {tab === "revenue_mix" ? (
          mix ? <MixPanel data={mix} /> : <PanelSkeleton rows={6} />
        ) : null}



        {sections && tab === null && available.length === 0 ? (
          <EmptyNote>Nothing further on file for this company yet.</EmptyNote>
        ) : null}
      </div>
    </section>
  );
}
