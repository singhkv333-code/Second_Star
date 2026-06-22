"use client";

/**
 * BrokerPicker — the broker selection grid.
 *
 * A calm, dense card grid (1 col on phones, 2 at sm+) listing every broker
 * from GET /brokers. Each card carries a logo, name, blurb, capability tags,
 * a connection status badge, and one primary action. The header reads as a
 * settings section, not a marketing hero (no gradient, no emoji, no
 * "Welcome! Let's get you set up" filler).
 *
 * Connected brokers float to the top so the user sees their live links first.
 */

import { useMemo } from "react";
import { BrokerCard } from "./BrokerCard";
import { connectKind } from "./broker-ui";
import type { Broker } from "@/lib/types";

export function BrokerPicker({
  brokers,
  onSelect,
}: {
  brokers: Broker[];
  /** Open the connect/manage panel for this broker. */
  onSelect: (broker: Broker) => void;
}): React.ReactElement {
  const ordered = useMemo(() => {
    return [...brokers].sort((a, b) => {
      // Connected first, then live (non-mock), then alphabetical.
      const ac = a.status.connected ? 0 : 1;
      const bc = b.status.connected ? 0 : 1;
      if (ac !== bc) return ac - bc;
      const am = a.status.mock_mode ? 1 : 0;
      const bm = b.status.mock_mode ? 1 : 0;
      if (am !== bm) return am - bm;
      return a.name.localeCompare(b.name);
    });
  }, [brokers]);

  return (
    <section aria-label="Connect a broker" className="flex flex-col gap-4">
      {/* Section header — quiet, informative. Right padding keeps the heading
          clear of the dialog's absolute close button. */}
      <div className="flex items-start justify-between gap-4 pe-9">
        <div>
          <h2
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 17,
              fontWeight: 600,
              letterSpacing: "-0.015em",
              color: "var(--text-primary)",
              margin: 0,
            }}
          >
            Connect your broker
          </h2>
          <p
            style={{
              margin: "4px 0 0",
              fontSize: 12.5,
              lineHeight: 1.5,
              color: "var(--text-tertiary)",
              maxWidth: 420,
            }}
          >
            Link a broker to pull live holdings and arm your automations. Pivot
            registers orders for you to confirm — it never trades on its own.
          </p>
        </div>
      </div>

      {/* Card grid */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {ordered.map((broker) => (
          <BrokerCard
            key={broker.id}
            broker={broker}
            primaryLabel={primaryLabel(broker)}
            onPrimary={() => onSelect(broker)}
          />
        ))}
      </div>
    </section>
  );
}

/** Resolve the single CTA label per broker + state. */
function primaryLabel(broker: Broker): string {
  if (broker.status.connected) return "Manage";
  switch (connectKind(broker)) {
    case "oauth":
      return `Connect ${broker.name}`;
    case "api_key":
      return `Connect with API key`;
    case "mock":
    default:
      return "Connect (mock)";
  }
}
