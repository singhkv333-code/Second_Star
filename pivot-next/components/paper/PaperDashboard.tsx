"use client";

/**
 * PaperDashboard — the top-level container for the Paper Trading surface.
 *
 * A thin composition layer: it owns no data fetching of its own. Each child
 * (KpiStatCards, EquityCurveChart, AllocationDonut, HoldingsTable,
 * OpenOrdersBlotter, TradeJournal) fetches and renders its own loading /
 * error / empty states. This file only arranges them into four Quartr-styled
 * sub-views via the shadcn Tabs primitive: Overview / Positions / Orders /
 * Journal.
 */

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AllocationDonut } from "@/components/paper/AllocationDonut";
import { EquityCurveChart } from "@/components/paper/EquityCurveChart";
import { HoldingsTable } from "@/components/paper/HoldingsTable";
import { KpiStatCards } from "@/components/paper/KpiStatCards";
import { OpenOrdersBlotter } from "@/components/paper/OpenOrdersBlotter";
import { TradeJournal } from "@/components/paper/TradeJournal";

const TABS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "overview", label: "Overview" },
  { value: "positions", label: "Positions" },
  { value: "orders", label: "Orders" },
  { value: "journal", label: "Journal" },
];

export function PaperDashboard(): React.ReactElement {
  return (
    <div
      className="flex min-h-full flex-col"
      style={{
        gap: 20,
        padding: "24px 24px 32px",
        background: "var(--bg-base)",
      }}
    >
      {/* Header */}
      <header className="flex flex-col" style={{ gap: 4 }}>
        <h1
          className="q-serif"
          style={{
            margin: 0,
            fontSize: 26,
            lineHeight: 1.1,
            color: "var(--text-primary)",
          }}
        >
          Paper Trading
        </h1>
        <p
          style={{
            margin: 0,
            fontFamily: "var(--font-ui)",
            fontSize: 13,
            color: "var(--text-secondary)",
          }}
        >
          Simulated portfolio — forward-test your triggered ideas.
        </p>
      </header>

      <Tabs
        defaultValue="overview"
        className="flex w-full flex-1 flex-col"
        style={{ gap: 16 }}
      >
        <TabsList
          className="h-auto flex-wrap justify-start gap-1 self-start rounded-[var(--radius-pill)] p-1"
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--glass-border)",
          }}
        >
          {TABS.map((t) => (
            <TabsTrigger
              key={t.value}
              value={t.value}
              className="rounded-[var(--radius-pill)] px-4 py-1.5 text-[13px] q-display transition-colors text-[var(--text-secondary)] data-[state=active]:text-[var(--text-primary)] data-[state=active]:shadow-none"
            >
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>

        {/* Overview */}
        <TabsContent value="overview" className="mt-0 flex flex-col" style={{ gap: 20 }}>
          <KpiStatCards />
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <EquityCurveChart />
            </div>
            <div className="lg:col-span-1">
              <AllocationDonut />
            </div>
          </div>
          <HoldingsTable />
        </TabsContent>

        {/* Positions */}
        <TabsContent value="positions" className="mt-0">
          <HoldingsTable />
        </TabsContent>

        {/* Orders */}
        <TabsContent value="orders" className="mt-0">
          <OpenOrdersBlotter />
        </TabsContent>

        {/* Journal */}
        <TabsContent value="journal" className="mt-0">
          <TradeJournal />
        </TabsContent>
      </Tabs>
    </div>
  );
}
