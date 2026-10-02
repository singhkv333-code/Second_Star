"use client";

/**
 * BrokerSidebarButton — the broker connection, docked at the bottom-left of
 * the sidebar.
 *
 * It replaces the broker pill that used to ride the top header. Whether a
 * broker is linked is a piece of state you glance at, so it now lives pinned to
 * the foot of the sidebar nav rail, reading as a peer to the nav icons above it:
 *   - nothing connected → two greyed marks + the word "Connect broker"
 *   - one or more connected → their marks in full colour + a live dot + count
 *
 * In the collapsed 48px rail it is a single centred glyph; in the expanded
 * drawer it becomes a full label row (layout in globals.css).
 *
 * Clicking it opens the SAME <BrokerConnectDialog/> the pill opened — the grid
 * inside is the whole connect surface; this row owns no connect logic of its own.
 */

import { useCallback, useEffect, useState } from "react";

import { BrokerLogo } from "@/components/brokers/BrokerLogo";
import { BrokerConnectDialog } from "@/components/brokers/BrokerConnectDialog";
import { getBrokers, type BrokerEntry } from "@/lib/brokersApi";

// Purely decorative hint marks for the empty state (greyed out) — the two most
// common Indian retail brokers. Choice/order here is cosmetic, not a nudge.
const HINT_BROKERS: { id: string; name: string; accent: string }[] = [
  { id: "zerodha", name: "Zerodha", accent: "#387ed1" },
  { id: "upstox", name: "Upstox", accent: "#5a3bde" },
];

const MAX_SHOWN = 3;

export function BrokerSidebarButton(): React.ReactElement {
  const [brokers, setBrokers] = useState<BrokerEntry[] | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback((): void => {
    getBrokers()
      .then((data) => setBrokers(data.brokers))
      // Degrade to the "connect" hint on any failure — this row must never
      // block the shell or throw a broken state into the sidebar.
      .catch(() => setBrokers([]));
  }, []);

  // Load once, then refresh on focus and after the grid reports a change, so the
  // row never lies about state.
  useEffect(() => {
    load();
    window.addEventListener("pivot:brokers-changed", load);
    window.addEventListener("focus", load);
    return () => {
      window.removeEventListener("pivot:brokers-changed", load);
      window.removeEventListener("focus", load);
    };
  }, [load]);

  // Re-read whenever the dialog closes — a connect/disconnect inside it should
  // be reflected immediately.
  useEffect(() => {
    if (!open) load();
  }, [open, load]);

  // Connected OR stale-but-remembered — either way there's an account behind it
  // worth surfacing (a stale one gets the amber dot so it reads as "attention").
  const linked = (brokers ?? []).filter((b) => b.connected || b.needs_reconnect);
  const anyStale = linked.some((b) => b.needs_reconnect || !b.connected);
  const connected = linked.length > 0;

  const shown = connected ? linked.slice(0, MAX_SHOWN) : HINT_BROKERS;
  const overflow = connected ? Math.max(0, linked.length - MAX_SHOWN) : 0;

  const label = connected
    ? linked.length === 1
      ? linked[0].name
      : `${linked.length} brokers`
    : "Connect broker";

  return (
    <>
      {/* Layout is CSS-driven (globals.css .broker-sidebar-button) so the row
          matches the sidebar nav items: a single centred 34px glyph in the
          collapsed 48px rail, and a full label row in the expanded drawer. No
          hardcoded width/padding here — those overflowed the narrow rail. */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={connected ? `Brokers — ${linked.length} connected` : "Connect a broker"}
        title={
          connected
            ? `${linked.map((b) => b.name).join(", ")} connected`
            : "Connect a broker"
        }
        data-testid="broker-sidebar-button"
        className="broker-sidebar-button"
      >
        {/* Stacked marks — an overlapping cluster; connected in colour, the hint
            set desaturated. In the collapsed rail only the first mark shows (CSS
            hides the rest) so a single coin sits centred without overflow. */}
        <span className="broker-mark-cluster" aria-hidden="true">
          {shown.map((b, i) => (
            <span
              key={b.id}
              className="broker-mark"
              style={{
                marginLeft: i === 0 ? 0 : -7,
                // Ring each mark with the sidebar surface so an overlap still
                // reads as separate coins cut from the bar.
                boxShadow: "0 0 0 1.5px var(--bg-base)",
                borderRadius: 5,
                display: "inline-flex",
                filter: connected ? "none" : "grayscale(1)",
                opacity: connected ? 1 : 0.45,
              }}
            >
              <BrokerLogo brokerId={b.id} name={b.name} accent={b.accent} size={18} />
            </span>
          ))}
          {overflow > 0 ? (
            <span
              className="broker-mark broker-mark--overflow"
              style={{
                marginLeft: -7,
                width: 18,
                height: 18,
                borderRadius: 5,
                boxShadow: "0 0 0 1.5px var(--bg-base)",
                background: "var(--bg-elevated, #ececee)",
                color: "var(--text-secondary)",
                fontSize: 9,
                fontWeight: 600,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              +{overflow}
            </span>
          ) : null}
          {/* Status dot — pinned to the cluster's top-right. Green when
              connected, amber when a session went stale. Absent when nothing
              is connected and on first load. */}
          {brokers !== null && connected ? (
            <span
              aria-hidden="true"
              style={{
                position: "absolute",
                top: -2,
                right: -3,
                width: 7,
                height: 7,
                borderRadius: 999,
                background: anyStale ? "#f0b429" : "var(--color-profit, #16a34a)",
                boxShadow: "0 0 0 1.5px var(--bg-base)",
              }}
            />
          ) : null}
        </span>

        <span className="sidebar-nav-label truncate">{label}</span>
      </button>

      {/* The exact connect surface — same grid, same per-broker forms. */}
      <BrokerConnectDialog open={open} onOpenChange={setOpen} />
    </>
  );
}
