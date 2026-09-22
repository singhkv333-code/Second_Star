"use client";

/**
 * BrokerGrid — connect a broker in as few moves as the broker allows.
 *
 * The design rule here is that the number of things a person types is the
 * product. So:
 *   - OAuth brokers (Zerodha, Upstox, Fyers) ask for NOTHING — one button.
 *   - Credential brokers show only the fields the BACKEND says are required
 *     (`entry.fields`), never a generic "api key / secret / pin" form, and
 *     every one of them sits under a "Get my keys" button that opens the exact
 *     page on the broker's site where that value is generated.
 *   - A dead token is one tap to fix, and the tap is the SAME control whether
 *     the broker can mint silently or needs a browser round-trip.
 *
 * Copy is deliberately thin. The card says what it is and what state it is in;
 * it does not explain OAuth.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { BrokerLogo } from "@/components/brokers/BrokerLogo";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  type BrokerEntry,
  connectBroker,
  disconnectBroker,
  formatExpiry,
  getBrokerLoginUrl,
  getBrokers,
  reconnectBroker,
  setBrokerLive,
} from "@/lib/brokersApi";
import { cn } from "@/lib/utils";

export function BrokerGrid({
  onStatus,
}: {
  /** Reports the real routing state up to the page shell. */
  onStatus?: (status: string) => void;
} = {}): React.ReactElement {
  const [brokers, setBrokers] = useState<BrokerEntry[] | null>(null);
  const [liveArmed, setLiveArmed] = useState(false);
  const [encrypted, setEncrypted] = useState(true);
  const [error, setError] = useState<string>("");
  const [open, setOpen] = useState<BrokerEntry | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getBrokers();
      setBrokers(data.brokers);
      setLiveArmed(data.live_armed);
      setEncrypted(data.encrypted);
      setError("");
      const live = data.brokers.filter((b) => b.live_enabled && b.connected);
      onStatus?.(
        data.live_armed && live.length > 0
          ? `Live · orders route to ${live.map((b) => b.name).join(", ")}`
          : "Simulated · no order reaches a broker",
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setBrokers([]);
    }
  }, [onStatus]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // A broker that just came back from its OAuth redirect lands here with a
  // query flag; reflect it rather than making the person hit reload.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("connected") || params.get("error")) {
      const reason = params.get("error");
      if (reason) setError(decodeURIComponent(reason));
      window.history.replaceState({}, "", window.location.pathname);
      void refresh();
    }
  }, [refresh]);

  const connected = useMemo(
    () => (brokers ?? []).filter((b) => b.connected || b.needs_reconnect),
    [brokers],
  );
  const rest = useMemo(
    () => (brokers ?? []).filter((b) => !b.connected && !b.needs_reconnect),
    [brokers],
  );

  if (brokers === null) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-[92px] w-full rounded-xl" />
        ))}
      </div>
    );
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex flex-col gap-5">
        {error ? (
          <p className="text-sm" style={{ color: "var(--negative, #dc2626)" }}>
            {error}
          </p>
        ) : null}

        {!encrypted ? (
          <p
            className="text-[11px]"
            style={{ color: "var(--text-secondary)", opacity: 0.75 }}
          >
            Tokens stored unencrypted — set BROKER_TOKEN_ENC_KEY.
          </p>
        ) : null}

        {connected.length > 0 ? (
          <section className="flex flex-col gap-3">
            {connected.map((b) => (
              <ConnectedRow
                key={b.id}
                entry={b}
                liveArmed={liveArmed}
                onChanged={refresh}
              />
            ))}
          </section>
        ) : null}

        {rest.length > 0 ? (
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {rest.map((b) => (
              <button
                key={b.id}
                type="button"
                onClick={() => setOpen(b)}
                className={cn(
                  "group flex items-center gap-3 rounded-xl border p-4 text-left",
                  "transition-colors hover:bg-[var(--bg-subtle,rgba(0,0,0,0.03))]",
                )}
                style={{ borderColor: "var(--border-subtle, rgba(0,0,0,0.1))" }}
              >
                <BrokerLogo
                  brokerId={b.id}
                  logo={b.logo}
                  name={b.name}
                  accent={b.accent}
                  size={40}
                />
                <span className="flex min-w-0 flex-col gap-1">
                  <span className="truncate text-sm font-medium">{b.name}</span>
                  {/* Fixed row height: a Badge is taller than a line of text,
                      and mixing them left the grid visibly ragged. */}
                  <span className="flex h-5 items-center">
                    {b.supports_unattended ? (
                      <Badge variant="secondary" className="text-[11px]">
                        No daily login
                      </Badge>
                    ) : (
                      <span
                        className="text-xs"
                        style={{ color: "var(--text-secondary)" }}
                      >
                        {b.fields.length === 0 ? "One tap" : "Daily login"}
                      </span>
                    )}
                  </span>
                </span>
              </button>
            ))}
          </section>
        ) : null}

        <ConnectDialog
          entry={open}
          onClose={() => setOpen(null)}
          onConnected={refresh}
        />
      </div>
    </TooltipProvider>
  );
}

/* ── a connected (or stale) broker ─────────────────────────────────────── */

function ConnectedRow({
  entry,
  liveArmed,
  onChanged,
}: {
  entry: BrokerEntry;
  liveArmed: boolean;
  onChanged: () => void;
}): React.ReactElement {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const reconnect = async () => {
    setBusy(true);
    setNote("");
    try {
      await reconnectBroker(entry.id);
      onChanged();
    } catch (exc) {
      // A broker with no unattended path (Zerodha, Upstox) lands here by
      // design — fall through to its hosted login rather than showing a error.
      try {
        const { login_url } = await getBrokerLoginUrl(entry.id);
        if (login_url) {
          window.location.href = login_url;
          return;
        }
      } catch {
        /* fall through to the message below */
      }
      setNote(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const stale = entry.needs_reconnect || !entry.connected;

  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-xl border p-4"
      style={{
        borderColor: stale
          ? "var(--warning-border, #f0b429)"
          : "var(--border-subtle, rgba(0,0,0,0.1))",
      }}
    >
      <BrokerLogo
        brokerId={entry.id}
        logo={entry.logo}
        name={entry.name}
        accent={entry.accent}
        size={40}
      />

      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex items-center gap-2 text-sm font-medium">
          {entry.name}
          {entry.broker_user_id ? (
            <span
              className="text-xs font-normal"
              style={{ color: "var(--text-secondary)" }}
            >
              {entry.broker_user_id}
            </span>
          ) : null}
        </span>
        <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
          {stale ? "Session expired" : formatExpiry(entry.expires_in)}
          {note ? ` · ${note}` : ""}
        </span>
      </div>

      {/* Arming real money is its own switch, and it is disabled — with the
          reason on hover — whenever the server-side kill switch is off, so the
          UI can never imply an order will reach an exchange when it cannot. */}
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="flex items-center gap-2">
            <Switch
              checked={entry.live_enabled && liveArmed}
              disabled={!liveArmed || stale || busy}
              onCheckedChange={async (next) => {
                setBusy(true);
                try {
                  await setBrokerLive(entry.id, next);
                  onChanged();
                } finally {
                  setBusy(false);
                }
              }}
              aria-label="Place real orders"
            />
            <Label className="text-xs">Live</Label>
          </span>
        </TooltipTrigger>
        <TooltipContent>
          {liveArmed
            ? "Route strategy fills to this broker"
            : "Live orders are off on this server"}
        </TooltipContent>
      </Tooltip>

      {stale ? (
        <Button size="sm" onClick={reconnect} disabled={busy}>
          {busy ? "Reconnecting…" : "Reconnect"}
        </Button>
      ) : (
        <Button
          size="sm"
          variant="ghost"
          onClick={async () => {
            setBusy(true);
            try {
              await disconnectBroker(entry.id);
              onChanged();
            } finally {
              setBusy(false);
            }
          }}
          disabled={busy}
        >
          Disconnect
        </Button>
      )}
    </div>
  );
}

/* ── the connect sheet ─────────────────────────────────────────────────── */

function ConnectDialog({
  entry,
  onClose,
  onConnected,
}: {
  entry: BrokerEntry | null;
  onClose: () => void;
  onConnected: () => void;
}): React.ReactElement | null {
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setValues({});
    setError("");
  }, [entry?.id]);

  if (!entry) return null;

  const oauth = entry.fields.length === 0;
  const keyPage = entry.links.api_key_page ?? entry.links.app_create;
  const ready = entry.fields.every((f) => (values[f.name] ?? "").trim() !== "");

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      if (oauth) {
        const { login_url } = await getBrokerLoginUrl(entry.id);
        if (!login_url) {
          throw new Error(`${entry.name} is not configured on this server yet.`);
        }
        window.location.href = login_url;
        return;
      }
      await connectBroker(entry.id, values);
      onConnected();
      onClose();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => (o ? null : onClose())}>
      <DialogContent className="sm:max-w-[420px]">
        <DialogHeader>
          <div className="flex items-center gap-3">
            <BrokerLogo
              brokerId={entry.id}
              logo={entry.logo}
              name={entry.name}
              accent={entry.accent}
              size={36}
            />
            <div className="flex flex-col">
              <DialogTitle className="text-base">{entry.name}</DialogTitle>
              <DialogDescription className="text-xs">
                {entry.supports_unattended
                  ? "Stays connected"
                  : "Reconnect each day"}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {oauth ? null : (
          <>
            {keyPage ? (
              <Button
                variant="secondary"
                size="sm"
                className="w-full"
                onClick={() => window.open(keyPage, "_blank", "noopener")}
              >
                Get my keys ↗
              </Button>
            ) : null}
            <Separator />
            <div className="flex flex-col gap-3">
              {entry.fields.map((f) => (
                <div key={f.name} className="flex flex-col gap-1.5">
                  <Label htmlFor={`${entry.id}-${f.name}`} className="text-xs">
                    {f.label}
                  </Label>
                  <Input
                    id={`${entry.id}-${f.name}`}
                    type={f.type}
                    autoComplete="off"
                    spellCheck={false}
                    value={values[f.name] ?? ""}
                    onChange={(e) =>
                      setValues((v) => ({ ...v, [f.name]: e.target.value }))
                    }
                  />
                </div>
              ))}
            </div>
          </>
        )}

        {error ? (
          <p className="text-xs" style={{ color: "var(--negative, #dc2626)" }}>
            {error}
          </p>
        ) : null}

        <Button onClick={submit} disabled={busy || (!oauth && !ready)}>
          {busy ? "Connecting…" : oauth ? `Continue with ${entry.name}` : "Connect"}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
