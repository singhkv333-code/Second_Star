"use client";

/**
 * /paper — Charto's paper trading book.
 *
 * The composition is Pivot's `PaperDashboard`, and six of its seven panels are
 * that file's components used unchanged: the KPI strip, the equity curve, the
 * allocation donut, the holdings table, the resting-order blotter and the trade
 * journal. They needed no edit because Charto's backend answers the same paths
 * with the same shapes — `/paper/summary`, `/paper/holdings`, `/paper/orders`,
 * `/paper/fills`, `/paper/nav` — which was the point of matching them.
 *
 * Three of Pivot's tabs are deliberately absent. **Greeks** and **IPOs** have
 * no data here: options left the execution surface on purpose and Charto has no
 * IPO feed, so those tabs would render an empty state that reads as a fault
 * rather than a boundary. Pivot's **Ideas** tab is replaced by **Strategies**,
 * which is the same question asked about the thing Charto actually has — the
 * saved rules that produced these fills.
 *
 * Sign-in is Charto's, and it happens on the chart. A signed-out visitor is
 * told that rather than bounced into this app's own login form, which belongs
 * to a different account system.
 */

import { useEffect, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AllocationDonut } from "@/components/paper/AllocationDonut";
import { EquityCurveChart } from "@/components/paper/EquityCurveChart";
import { HoldingsTable } from "@/components/paper/HoldingsTable";
import { KpiStatCards } from "@/components/paper/KpiStatCards";
import { OpenOrdersBlotter } from "@/components/paper/OpenOrdersBlotter";
import { StrategiesPanel } from "@/components/paper/StrategiesPanel";
import { TradeJournal } from "@/components/paper/TradeJournal";
import { getStoredToken } from "@/components/AppBootstrap";

const TABS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "overview", label: "Overview" },
  { value: "strategies", label: "Strategies" },
  { value: "positions", label: "Positions" },
  { value: "orders", label: "Orders" },
  { value: "journal", label: "Journal" },
];

export default function PaperPage(): React.ReactElement {
  // Read on the client only: `getStoredToken` touches localStorage, and
  // rendering the signed-out state during SSR would flash it at every signed-in
  // visitor for one frame.
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  useEffect(() => setSignedIn(Boolean(getStoredToken())), []);

  return (
    <div
      className="flex min-h-full flex-col"
      style={{ gap: 20, padding: "24px 24px 32px", background: "var(--bg-base)" }}
    >
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
          A simulated book. Strategies you arm on the chart fill in here — the
          shares, the cash and the charges are all real records of a simulation,
          and no order ever reaches a broker.
        </p>
      </header>

      {signedIn === false ? (
        <div
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-sm)",
            padding: "20px 22px",
            maxWidth: "62ch",
          }}
        >
          <p
            style={{
              margin: 0,
              fontFamily: "var(--font-ui)",
              fontSize: 13.5,
              color: "var(--text-secondary)",
            }}
          >
            A paper book belongs to an account, not to a browser tab. Sign in on
            the chart and this page will find it.{" "}
            <a href="/" style={{ color: "var(--text-primary)" }}>
              Open the chart
            </a>
            .
          </p>
        </div>
      ) : signedIn === null ? null : (
        <Tabs
          defaultValue="overview"
          className="flex w-full flex-1 flex-col"
          style={{ gap: 16 }}
        >
          <TabsList
            className="h-auto flex-wrap justify-start gap-1 self-start rounded-[var(--radius-sm)] p-1"
            style={{
              background: "var(--bg-secondary)",
              border: "1px solid var(--glass-border)",
            }}
          >
            {TABS.map((t) => (
              <TabsTrigger
                key={t.value}
                value={t.value}
                className="rounded-[var(--radius-xs)] px-4 py-1.5 text-[13px] q-display transition-colors text-[var(--text-secondary)] data-[state=active]:text-[var(--text-primary)] data-[state=active]:shadow-none"
              >
                {t.label}
              </TabsTrigger>
            ))}
          </TabsList>

          <TabsContent
            value="overview"
            className="mt-0 flex flex-col"
            style={{ gap: 20 }}
          >
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

          <TabsContent value="strategies" className="mt-0">
            <StrategiesPanel />
          </TabsContent>

          <TabsContent value="positions" className="mt-0">
            <HoldingsTable />
          </TabsContent>

          <TabsContent value="orders" className="mt-0">
            <OpenOrdersBlotter />
          </TabsContent>

          <TabsContent value="journal" className="mt-0">
            <TradeJournal />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
