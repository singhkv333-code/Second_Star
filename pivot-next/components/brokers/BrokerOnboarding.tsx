"use client";

/**
 * BrokerOnboarding — the multi-broker connect surface, mounted by AppShell in
 * place of the old Kite-only KiteCredentialsPanel.
 *
 * It is a dialog (same mount contract as the panel it replaces — open/onOpenChange
 * driven by AppShell + the account menu) that hosts a two-level flow:
 *
 *   1. Picker  — the broker grid (BrokerPicker). The default view.
 *   2. Connect — a per-broker connect/manage panel (BrokerConnectPanel),
 *                reached by selecting a card; a back affordance returns to (1).
 *
 * Data: GET /brokers on open. The OAuth round-trip result (?broker=connected
 * / ?broker=error&reason=…), read by AppShell, is threaded in so the relevant
 * broker opens straight onto its connect panel with a success/error banner.
 *
 * First-run: AppShell can also surface a lightweight inline entry point
 * (BrokerOnboardingBanner, below) when no broker is connected — a single calm
 * row inside the existing shell, NOT a full-page gradient hero.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Loader2, Plug, RefreshCw } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { listBrokers } from "@/lib/api";
import { isError } from "@/lib/types";
import type { Broker, BrokerStatus } from "@/lib/types";
import { BrokerPicker } from "./BrokerPicker";
import { brokerHasOauth } from "./broker-ui";
import {
  Banner,
  BrokerConnectPanelBody,
  type BrokerOAuthResult,
} from "./BrokerConnectPanel";

export type { BrokerOAuthResult };

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; brokers: Broker[] }
  | { kind: "error"; message: string };

export function BrokerOnboarding({
  open,
  onOpenChange,
  /** OAuth round-trip outcome read by AppShell from the ?broker= query param.
   *  When `{ broker, result }` is present we deep-open that broker's panel. */
  oauth,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  oauth?: { broker: string | null; result: BrokerOAuthResult } | null;
}): React.ReactElement {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setState({ kind: "loading" });
    const res = await listBrokers();
    if (isError(res)) {
      setState({ kind: "error", message: res.error.message });
      return;
    }
    setState({ kind: "ok", brokers: res.data.brokers });
  }, []);

  // Fetch on open; reset the inner view each time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setSelectedId(null);
    void load();
  }, [open, load]);

  // When AppShell hands us an OAuth outcome, jump straight to that broker's
  // panel (so the user lands on the success/error banner in context). The
  // backend's success redirect currently carries no broker id, so when it's
  // absent we fall back to the single OAuth-capable broker (Kite/Zerodha) once
  // the catalog has loaded — otherwise a `?broker=connected` return would dump
  // the user on the bare picker with no feedback. Only auto-select when the
  // OAuth broker is unambiguous; with >1 we leave the picker up.
  useEffect(() => {
    if (!open || !oauth) return;
    if (oauth.broker) {
      setSelectedId(oauth.broker);
      return;
    }
    if (state.kind !== "ok") return;
    const oauthBrokers = state.brokers.filter(brokerHasOauth);
    const only = oauthBrokers.length === 1 ? oauthBrokers[0] : null;
    if (only) setSelectedId(only.id);
  }, [open, oauth, state]);

  // Optimistically patch one broker's status after a connect/automation/
  // disconnect so the picker + panel re-render without a full refetch.
  const patchStatus = useCallback(
    (brokerId: string, status: BrokerStatus): void => {
      setState((prev) =>
        prev.kind === "ok"
          ? {
              kind: "ok",
              brokers: prev.brokers.map((b) =>
                b.id === brokerId ? { ...b, status } : b,
              ),
            }
          : prev,
      );
    },
    [],
  );

  // Derive the broker list + the selected broker from `state` directly, so the
  // memo keys on the stable state object (not a freshly-allocated `[]`).
  const brokers = useMemo(
    () => (state.kind === "ok" ? state.brokers : []),
    [state],
  );
  const selected = useMemo(
    () => brokers.find((b) => b.id === selectedId) ?? null,
    [brokers, selectedId],
  );

  // The OAuth banner belongs to the broker the user is looking at. When the
  // backend supplied a broker id it must match the selected one; when it
  // didn't (id null), the banner rides along with whichever OAuth broker we
  // auto-selected above (so a `?broker=connected` return still shows feedback).
  const oauthForSelected =
    oauth && selected && (oauth.broker == null || oauth.broker === selected.id)
      ? oauth.result
      : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={
          // Desktop: centered floating card.
          "sm:flex sm:max-h-[calc(100dvh_-_2rem)] sm:max-w-[640px] sm:flex-col sm:rounded-2xl " +
          // Mobile: slide up as a bottom sheet instead of taking the whole
          // screen (anchored bottom, rounded top, capped height, internal scroll).
          // `max-sm:!` overrides the shared dialog's full-screen `inset-0`/`border-0`.
          "max-sm:!inset-x-0 max-sm:!top-auto max-sm:!bottom-0 " +
          // Size to content so the broker list shows without a scrollbar; the
          // ceiling only kicks in on very short screens. Trim padding to fit.
          "max-sm:h-auto max-sm:max-h-[calc(100svh-1rem)] max-sm:!p-5 " +
          "max-sm:rounded-t-2xl max-sm:!border-t " +
          "max-sm:data-[state=open]:animate-in max-sm:data-[state=open]:slide-in-from-bottom-4"
        }
        data-testid="broker-onboarding"
      >
        {selected ? (
          // ── Connect / manage one broker ──────────────────────────────────
          <div className="flex min-h-0 flex-1 flex-col gap-3">
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className="inline-flex w-fit shrink-0 items-center gap-1.5"
              style={{
                fontSize: 12,
                fontWeight: 500,
                color: "var(--text-tertiary)",
                background: "transparent",
                border: "none",
                cursor: "pointer",
                padding: 0,
                transition: "color 0.2s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.color = "var(--text-primary)")}
              onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-tertiary)")}
              data-testid="broker-back"
            >
              <ArrowLeft size={14} strokeWidth={2} aria-hidden />
              All brokers
            </button>
            <BrokerConnectPanelBody
              key={selected.id}
              broker={selected}
              onStatusChange={patchStatus}
              oauthResult={oauthForSelected}
              onClose={() => onOpenChange(false)}
            />
          </div>
        ) : (
          // ── Picker ───────────────────────────────────────────────────────
          <div className="quartr-no-scrollbar flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto">
            <DialogHeader className="gap-0 space-y-0 text-left">
              <DialogTitle className="sr-only">Connect a broker</DialogTitle>
            </DialogHeader>

            {/* OAuth outcome with no resolvable panel (no broker id + an
                ambiguous OAuth set) — surface the result here so the return
                trip is never silent. */}
            {oauth?.result.kind === "connected" && (
              <Banner tone="success">
                Broker connected. Your session is live.
              </Banner>
            )}
            {oauth?.result.kind === "error" && (
              <Banner tone="error">
                Couldn&apos;t connect: {oauth.result.reason}
              </Banner>
            )}

            {state.kind === "loading" && <PickerSkeleton />}

            {state.kind === "error" && (
              <ErrorState message={state.message} onRetry={() => void load()} />
            )}

            {state.kind === "ok" &&
              (brokers.length === 0 ? (
                <EmptyState />
              ) : (
                <BrokerPicker brokers={brokers} onSelect={(b) => setSelectedId(b.id)} />
              ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// First-run entry point — a calm inline banner for AppShell to drop into the
// existing shell when no broker is connected. NOT a full-page hero.
// ---------------------------------------------------------------------------

export function BrokerOnboardingBanner({
  onOpen,
  brokerCount,
}: {
  onOpen: () => void;
  /** How many brokers are available, for the copy ("2 brokers supported"). */
  brokerCount?: number;
}): React.ReactElement {
  return (
    <div
      data-testid="broker-onboarding-banner"
      className="flex items-center gap-3"
      style={{
        padding: "12px 14px",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        background: "var(--bg-primary)",
      }}
    >
      <span
        className="inline-flex shrink-0 items-center justify-center"
        style={{
          width: 34,
          height: 34,
          borderRadius: "var(--radius-sm)",
          background: "var(--surface-active)",
          color: "var(--text-secondary)",
        }}
      >
        <Plug size={16} strokeWidth={2} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
          Connect a broker
        </div>
        <div style={{ fontSize: 11.5, color: "var(--text-tertiary)" }}>
          Pull live holdings and arm automations
          {brokerCount ? ` — ${brokerCount} brokers supported` : ""}.
        </div>
      </div>
      <button
        type="button"
        onClick={onOpen}
        data-testid="broker-onboarding-banner-cta"
        className="inline-flex shrink-0 items-center"
        style={{
          height: 32,
          padding: "0 14px",
          borderRadius: "var(--radius-sm)",
          background: "var(--text-primary)",
          color: "var(--bg-base)",
          fontSize: 12.5,
          fontWeight: 600,
          border: "none",
          cursor: "pointer",
          transition: "opacity 0.2s var(--ease-quartr)",
        }}
        onMouseEnter={(e) => (e.currentTarget.style.opacity = "0.9")}
        onMouseLeave={(e) => (e.currentTarget.style.opacity = "1")}
      >
        Connect
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

function PickerSkeleton(): React.ReactElement {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading brokers">
      <div className="flex flex-col gap-2">
        <div
          style={{
            height: 18,
            width: 180,
            borderRadius: 6,
            background: "var(--surface-active)",
          }}
        />
        <div
          style={{
            height: 13,
            width: 320,
            borderRadius: 5,
            background: "var(--surface-active)",
            opacity: 0.7,
          }}
        />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {[0, 1].map((i) => (
          <div
            key={i}
            style={{
              height: 188,
              borderRadius: "var(--radius-lg)",
              border: "1px solid var(--glass-border)",
              background: "var(--bg-primary)",
            }}
          />
        ))}
      </div>
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}): React.ReactElement {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 text-center"
      style={{ padding: "28px 16px" }}
    >
      <p style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>
        Couldn&apos;t load brokers
      </p>
      <p style={{ fontSize: 12, color: "var(--text-tertiary)", margin: 0, maxWidth: 320 }}>
        {message}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex items-center gap-1.5"
        style={{
          height: 34,
          padding: "0 14px",
          borderRadius: "var(--radius-sm)",
          border: "1px solid var(--glass-border)",
          background: "transparent",
          color: "var(--text-primary)",
          fontSize: 12.5,
          fontWeight: 500,
          cursor: "pointer",
        }}
      >
        <RefreshCw size={13} strokeWidth={2} aria-hidden />
        Try again
      </button>
    </div>
  );
}

function EmptyState(): React.ReactElement {
  return (
    <div
      className="flex flex-col items-center gap-2 text-center"
      style={{ padding: "28px 16px" }}
    >
      <Loader2 size={18} className="animate-spin" style={{ color: "var(--text-tertiary)" }} aria-hidden />
      <p style={{ fontSize: 12.5, color: "var(--text-tertiary)", margin: 0 }}>
        No brokers are configured on this server yet.
      </p>
    </div>
  );
}
