"use client";

/**
 * /brokers — the third face of the book: where the orders actually go.
 *
 * Portfolio is what you hold, Strategies are the rules that trade it, and this
 * is the account those trades reach. It sits in the same shell for that
 * reason: connecting a broker is part of running a strategy, not a settings
 * chore buried somewhere else.
 */

import { useState } from "react";

import { BrokerGrid } from "@/components/brokers/BrokerGrid";
import { BookShell } from "@/components/paper/BookShell";

export default function BrokersPage(): React.ReactElement {
  // The shell's default caption says no order reaches a broker. On this page
  // that can stop being true, so the grid reports the real state up.
  const [status, setStatus] = useState<string>(
    "Simulated · no order reaches a broker",
  );
  return (
    <BookShell active="/brokers" status={status}>
      <BrokerGrid onStatus={setStatus} />
    </BookShell>
  );
}
