"use client";

/**
 * The statements page, under the same chrome as the company overview.
 *
 * `StatementsPage` has existed and rendered all four filed statements for as
 * long as the "See detail" link has pointed at this URL — there was simply no
 * route mounting it, so the one way out of the summary panel 404'd. This is
 * that route.
 *
 * The tab arrives in the query string because the link carries the tab the
 * reader was already on: a person who clicks through from the Ratios tab is
 * asking for the ratio sheet, not for the first statement in the list.
 */

import * as React from "react";
import { useSearchParams } from "next/navigation";

import { StatementsPage } from "@/components/stock/StatementsPage";
import type { StatementType } from "@/lib/api";
import { CompanyChrome } from "../view";

const TABS: StatementType[] = [
  "profit_loss", "balance_sheet", "cash_flow", "ratios",
];

function Body({ symbol }: { symbol: string }): React.ReactElement {
  // Same nullability as above: null while prerendering, so read it defensively
  // rather than asserting a value the type no longer guarantees.
  const asked = (useSearchParams()?.get("tab") ?? null) as StatementType | null;
  const tab = asked && TABS.includes(asked) ? asked : "profit_loss";
  // Keyed on the tab so a reader who lands here from a different summary tab
  // opens on the statement they asked for rather than on whichever one the
  // component mounted with first.
  return <StatementsPage key={tab} symbol={symbol} initialTab={tab} />;
}

export function StatementsView({ symbol }: { symbol: string }): React.ReactElement {
  return (
    <CompanyChrome symbol={symbol}>
      {/* useSearchParams needs a Suspense boundary to prerender; without one
          the whole route opts into client rendering and `next build` says so. */}
      <React.Suspense fallback={null}>
        <Body symbol={symbol} />
      </React.Suspense>
    </CompanyChrome>
  );
}
