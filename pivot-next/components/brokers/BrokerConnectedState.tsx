"use client";

/**
 * BrokerConnectedState — the post-connection panel body for one broker.
 *
 * Shows the connected identity (broker_user_id), persistence mode, expiry, a
 * compact holdings preview (GET /brokers/{id}/holdings), an automation toggle
 * (POST /brokers/{id}/automation), and a Disconnect control
 * (DELETE /brokers/{id}/session). Honest, dense, calm — no celebration
 * confetti, no fabricated numbers (every value comes straight off the wire).
 */

import { useCallback, useEffect, useState } from "react";
import { Loader2, LogOut, ShieldCheck, Wallet } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import {
  disconnectBroker,
  getBrokerHoldings,
  setBrokerAutomation,
} from "@/lib/api";
import { isError } from "@/lib/types";
import type { Broker, BrokerHolding, BrokerStatus } from "@/lib/types";
import {
  effectivePersistence,
  fmtInr,
  formatExpiry,
  isUnattendedKind,
  persistenceBlurb,
  persistenceLabel,
} from "./broker-ui";

type HoldingsState =
  | { kind: "loading" }
  | { kind: "ok"; rows: BrokerHolding[] }
  | { kind: "empty" }
  | { kind: "error"; message: string };

export function BrokerConnectedState({
  broker,
  onStatusChange,
  onDisconnected,
}: {
  broker: Broker;
  /** Bubble a fresh status up so the picker/panel re-render. */
  onStatusChange: (status: BrokerStatus) => void;
  /** Called after a successful disconnect so the parent can return to connect. */
  onDisconnected: () => void;
}): React.ReactElement {
  const [holdings, setHoldings] = useState<HoldingsState>({ kind: "loading" });
  const [autoBusy, setAutoBusy] = useState(false);
  const [autoError, setAutoError] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);

  const status = broker.status;
  const persistence = effectivePersistence(broker);
  const expiry = formatExpiry(status.expires_at);
  const autoOn = status.auto_login_opt_in ?? isUnattendedKind(persistence);

  // Holdings preview — read-only, one fetch on mount. Never blocks the rest of
  // the panel; an error here degrades to a quiet inline message.
  useEffect(() => {
    let alive = true;
    setHoldings({ kind: "loading" });
    void getBrokerHoldings(broker.id).then((res) => {
      if (!alive) return;
      if (isError(res)) {
        setHoldings({ kind: "error", message: res.error.message });
        return;
      }
      const rows = res.data.holdings ?? [];
      setHoldings(rows.length === 0 ? { kind: "empty" } : { kind: "ok", rows });
    });
    return () => {
      alive = false;
    };
  }, [broker.id]);

  const toggleAutomation = useCallback(
    async (next: boolean): Promise<void> => {
      setAutoBusy(true);
      setAutoError(null);
      const res = await setBrokerAutomation(broker.id, next);
      setAutoBusy(false);
      if (isError(res)) {
        setAutoError(res.error.message || "Couldn't update automation.");
        return;
      }
      onStatusChange(res.data);
    },
    [broker.id, onStatusChange],
  );

  const handleDisconnect = useCallback(async (): Promise<void> => {
    setDisconnecting(true);
    const res = await disconnectBroker(broker.id);
    setDisconnecting(false);
    if (isError(res)) {
      setAutoError(res.error.message || "Couldn't disconnect.");
      return;
    }
    onDisconnected();
  }, [broker.id, onDisconnected]);

  return (
    <div className="flex flex-col gap-4">
      {/* Identity + persistence + expiry — a tidy 2-up fact grid. */}
      <div
        className="grid grid-cols-2 gap-px overflow-hidden"
        style={{
          background: "var(--glass-border)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-md)",
        }}
      >
        <Fact label="Account" value={status.broker_user_id || broker.name} />
        <Fact label="Persistence" value={persistenceLabel(persistence)} />
        <Fact
          label="Session"
          value={expiry ? `Renews ${expiry}` : autoOn ? "No daily login" : "Active"}
        />
        <Fact label="Mode" value={status.mock_mode ? "Demo data" : "Live"} />
      </div>

      {/* Automation toggle — explained as a positive. */}
      {broker.supports_unattended && (
        <div
          className="flex items-start gap-3 p-3.5"
          style={{
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
            background: "var(--bg-base)",
          }}
        >
          <span
            className="mt-0.5 inline-flex shrink-0 items-center justify-center"
            style={{
              width: 30,
              height: 30,
              borderRadius: "var(--radius-sm)",
              background: `color-mix(in srgb, ${broker.accent} 14%, transparent)`,
              color: broker.accent,
            }}
          >
            <ShieldCheck size={16} strokeWidth={2} aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-3">
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: "var(--text-primary)",
                }}
              >
                Stay connected
              </span>
              <Switch
                checked={autoOn}
                disabled={autoBusy}
                onCheckedChange={(c) => void toggleAutomation(c)}
                aria-label="Toggle stay-connected automation"
                data-testid={`broker-automation-${broker.id}`}
              />
            </div>
            <p
              style={{
                margin: "3px 0 0",
                fontSize: 11.5,
                lineHeight: 1.5,
                color: "var(--text-tertiary)",
              }}
            >
              {autoOn
                ? `${persistenceBlurb(persistence)} Your automations keep running without a daily login.`
                : "Turn this on so your automations keep running without a daily login."}
            </p>
            {autoError && (
              <p
                role="alert"
                style={{
                  margin: "6px 0 0",
                  fontSize: 11,
                  color: "var(--color-loss)",
                }}
              >
                {autoError}
              </p>
            )}
          </div>
        </div>
      )}

      {/* Holdings preview */}
      <div>
        <div
          className="mb-2 flex items-center gap-1.5"
          style={{ color: "var(--text-tertiary)" }}
        >
          <Wallet size={13} strokeWidth={2} aria-hidden />
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
            }}
          >
            Holdings
          </span>
        </div>
        <HoldingsPreview state={holdings} accent={broker.accent} />
      </div>

      {/* Disconnect */}
      <button
        type="button"
        onClick={() => void handleDisconnect()}
        disabled={disconnecting}
        data-testid={`broker-disconnect-${broker.id}`}
        className="inline-flex items-center justify-center gap-1.5 self-start"
        style={{
          height: 32,
          padding: "0 12px",
          borderRadius: "var(--radius-sm)",
          border: "1px solid var(--glass-border)",
          background: "transparent",
          color: "var(--text-secondary)",
          fontSize: 12.5,
          fontWeight: 500,
          cursor: disconnecting ? "default" : "pointer",
          transition: "color 0.2s var(--ease-quartr), border-color 0.2s var(--ease-quartr)",
        }}
        onMouseEnter={(e) => {
          if (disconnecting) return;
          e.currentTarget.style.color = "var(--color-loss)";
          e.currentTarget.style.borderColor =
            "color-mix(in srgb, var(--color-loss) 40%, var(--glass-border))";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.color = "var(--text-secondary)";
          e.currentTarget.style.borderColor = "var(--glass-border)";
        }}
      >
        {disconnecting ? (
          <Loader2 size={13} className="animate-spin" aria-hidden />
        ) : (
          <LogOut size={13} strokeWidth={2} aria-hidden />
        )}
        Disconnect {broker.name}
      </button>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div style={{ background: "var(--bg-primary)", padding: "10px 12px" }}>
      <div
        style={{
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: "0.05em",
          textTransform: "uppercase",
          color: "var(--text-tertiary)",
        }}
      >
        {label}
      </div>
      <div
        className="truncate"
        style={{
          marginTop: 2,
          fontSize: 13,
          fontWeight: 500,
          color: "var(--text-primary)",
        }}
        title={value}
      >
        {value}
      </div>
    </div>
  );
}

function HoldingsPreview({
  state,
  accent,
}: {
  state: HoldingsState;
  accent: string;
}): React.ReactElement {
  if (state.kind === "loading") {
    return (
      <div className="flex flex-col gap-1.5">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            style={{
              height: 38,
              borderRadius: "var(--radius-sm)",
              background: "var(--surface-active)",
              opacity: 1 - i * 0.18,
            }}
          />
        ))}
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <p
        style={{
          fontSize: 11.5,
          lineHeight: 1.5,
          color: "var(--text-tertiary)",
          margin: 0,
        }}
      >
        Couldn&apos;t load holdings right now. They&apos;ll appear here once the
        broker responds.
      </p>
    );
  }

  if (state.kind === "empty") {
    return (
      <div
        className="flex items-center justify-center text-center"
        style={{
          minHeight: 56,
          border: "1px dashed var(--glass-border)",
          borderRadius: "var(--radius-md)",
          fontSize: 12,
          color: "var(--text-tertiary)",
          padding: "10px 14px",
        }}
      >
        No holdings in this account yet.
      </div>
    );
  }

  const rows = state.rows.slice(0, 4);
  const extra = state.rows.length - rows.length;

  return (
    <div
      className="overflow-hidden"
      style={{
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
      }}
    >
      {rows.map((h, i) => {
        const pnl = h.pnl;
        const pnlColor =
          pnl == null
            ? "var(--text-tertiary)"
            : pnl >= 0
              ? "var(--color-profit)"
              : "var(--color-loss)";
        return (
          <div
            key={`${h.tradingsymbol}-${i}`}
            className="flex items-center justify-between gap-3"
            style={{
              padding: "9px 12px",
              borderTop: i === 0 ? "none" : "1px solid var(--glass-border)",
              background: "var(--bg-primary)",
            }}
          >
            <div className="flex min-w-0 items-center gap-2">
              <span
                aria-hidden
                style={{
                  width: 3,
                  height: 18,
                  borderRadius: 2,
                  background: accent,
                  opacity: 0.7,
                  flexShrink: 0,
                }}
              />
              <div className="flex min-w-0 flex-col">
                <span
                  className="truncate"
                  style={{
                    fontSize: 12.5,
                    fontWeight: 600,
                    color: "var(--text-primary)",
                    lineHeight: 1.2,
                  }}
                >
                  {h.tradingsymbol}
                </span>
                <span
                  style={{ fontSize: 10.5, color: "var(--text-tertiary)" }}
                >
                  {h.quantity} qty
                  {h.average_price != null && ` · avg ${fmtInr(h.average_price)}`}
                </span>
              </div>
            </div>
            <div className="flex flex-col items-end" style={{ flexShrink: 0 }}>
              <span
                className="tabular-nums"
                style={{
                  fontSize: 12.5,
                  fontWeight: 600,
                  fontFamily: "var(--font-numeric)",
                  color: "var(--text-primary)",
                }}
              >
                {h.last_price != null ? fmtInr(h.last_price * h.quantity) : "—"}
              </span>
              {pnl != null && (
                <span
                  className="tabular-nums"
                  style={{
                    fontSize: 10.5,
                    fontFamily: "var(--font-numeric)",
                    color: pnlColor,
                  }}
                >
                  {pnl >= 0 ? "+" : "−"}
                  {fmtInr(Math.abs(pnl)).replace(/^[-−]/, "")}
                </span>
              )}
            </div>
          </div>
        );
      })}
      {extra > 0 && (
        <div
          style={{
            padding: "7px 12px",
            borderTop: "1px solid var(--glass-border)",
            background: "var(--bg-primary)",
            fontSize: 11,
            color: "var(--text-tertiary)",
            textAlign: "center",
          }}
        >
          +{extra} more {extra === 1 ? "holding" : "holdings"}
        </div>
      )}
    </div>
  );
}
