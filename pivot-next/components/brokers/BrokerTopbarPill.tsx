"use client";

/**
 * BrokerTopbarPill — the broker connection, folded into the topbar.
 *
 * Brokers stopped being their own nav item: whether a broker is linked is a
 * piece of state you want to *see*, not a page you sit on. So this is a small
 * pill that rides the header and tells the truth at a glance —
 *   - nothing connected → two greyed marks + the word "Connect"
 *   - one or more connected → their marks in full colour with a live dot
 *
 * Clicking it opens a dialog that hosts the SAME <BrokerGrid/> the old Brokers
 * tab used — so every broker keeps its exact per-broker connect form (Client ID
 * / PIN / TOTP secret, "Get my keys", OAuth-only one-tap, etc.). The pill owns
 * no connect logic of its own; the grid is the whole connect surface.
 */

import { useCallback, useEffect, useState } from "react";

import { BrokerLogo } from "@/components/brokers/BrokerLogo";
import { BrokerGrid } from "@/components/brokers/BrokerGrid";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { getBrokers, type BrokerEntry } from "@/lib/brokersApi";

// Purely decorative hint marks for the empty state (greyed out) — the two most
// common Indian retail brokers. Choice/order here is cosmetic, not a nudge.
const HINT_BROKERS: { id: string; name: string; accent: string }[] = [
  { id: "zerodha", name: "Zerodha", accent: "#387ed1" },
  { id: "upstox", name: "Upstox", accent: "#5a3bde" },
];

const MAX_SHOWN = 3;

export function BrokerTopbarPill(): React.ReactElement {
  const [brokers, setBrokers] = useState<BrokerEntry[] | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback((): void => {
    getBrokers()
      .then((data) => setBrokers(data.brokers))
      // Degrade to the "connect" hint on any failure — the pill must never
      // block the shell or throw a broken state into the header.
      .catch(() => setBrokers([]));
  }, []);

  // Load once, then refresh on focus and after the grid reports a change, so the
  // pill never lies about state.
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
  // be reflected in the pill immediately.
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

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={connected ? `Brokers — ${linked.length} connected` : "Connect a broker"}
        title={
          connected
            ? `${linked.map((b) => b.name).join(", ")} connected`
            : "Connect a broker"
        }
        data-testid="broker-topbar-pill"
        className="broker-pill inline-flex shrink-0 items-center justify-center"
        style={{
          // Borderless, minimal-width: just the logo cluster, no chrome and no
          // caption. Status is a tiny dot on the cluster, not a word.
          position: "relative",
          padding: "4px 6px",
          height: 32,
          borderRadius: "var(--radius-sm)",
          border: "none",
          background: "transparent",
          color: "var(--text-secondary)",
          lineHeight: 1,
          cursor: "pointer",
          transition: "background-color 0.2s",
        }}
      >
        {/* Stacked marks — an overlapping cluster; connected in colour, the hint
            set desaturated. */}
        <span className="inline-flex items-center" aria-hidden="true">
          {shown.map((b, i) => (
            <span
              key={b.id}
              style={{
                marginLeft: i === 0 ? 0 : -7,
                // Ring each mark with the header surface so an overlap still
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
        </span>

        {/* Status dot — a small badge pinned to the cluster's top-right. Green
            when connected, amber when a session went stale. Absent entirely
            when nothing is connected (the greyed logos already say "not yet")
            and on first load. */}
        {brokers !== null && connected ? (
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              top: 3,
              right: 3,
              width: 7,
              height: 7,
              borderRadius: 999,
              background: anyStale ? "#f0b429" : "var(--color-profit, #16a34a)",
              boxShadow: "0 0 0 1.5px var(--bg-base)",
            }}
          />
        ) : null}
      </button>

      {/* The exact connect surface the old Brokers tab used — same grid, same
          per-broker forms. Hosted in a dialog now that it has no tab. */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          className="sm:max-w-[680px] sm:max-h-[calc(100dvh_-_2rem)] sm:overflow-y-auto"
          data-testid="broker-grid-dialog"
        >
          <DialogHeader>
            <DialogTitle>Brokers</DialogTitle>
            <DialogDescription>
              Connect a broker to pull live holdings and arm automations.
            </DialogDescription>
          </DialogHeader>
          <BrokerGrid />
        </DialogContent>
      </Dialog>
    </>
  );
}
