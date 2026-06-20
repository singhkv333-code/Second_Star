"use client";

/**
 * BrokerConnectPanelBody — the per-broker connect/manage flow.
 *
 * Rendered inside the dialog that BrokerOnboarding already owns (it is NOT a
 * dialog itself — nesting a second one would be wrong). Three connect shapes
 * resolved from the broker's capability flags (see broker-ui.ts::connectKind):
 *
 *   • OAuth (Kite)   — primary CTA does GET /brokers/{id}/login_url then
 *                      window.location → the hosted login (zero typed input).
 *                      A collapsible "Advanced: stay connected without daily
 *                      login" posts credentials + auto_login_opt_in=true with
 *                      an honest encrypted-credentials warning + deep-links to
 *                      create the API app and enable TOTP.
 *   • API key (Dhan) — a key/secret form (+ optional client_id/pin/totp_secret)
 *                      led by a prominent "Open Dhan → API Access" deep-link, an
 *                      automation toggle framed as a positive, and a docs link.
 *   • Mock           — a single "Connect (mock)" button (dev, when mock_mode).
 *
 * When the broker is already connected the body switches to
 * BrokerConnectedState (holdings + automation + disconnect).
 *
 * Honest by construction: nothing claims success on a failure path, and every
 * displayed value comes off the wire (no fabricated tokens/ids).
 */

import { useCallback, useState } from "react";
import {
  ChevronDown,
  Eye,
  EyeOff,
  Loader2,
  Lock,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import { DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  connectBrokerMock,
  getBrokerLoginUrl,
  setBrokerCredentials,
} from "@/lib/api";
import { isError } from "@/lib/types";
import type { Broker, BrokerCredentialsRequest, BrokerStatus } from "@/lib/types";
import { BrokerLogo } from "./BrokerLogo";
import { BrokerConnectedState } from "./BrokerConnectedState";
import { DeepLinkButton } from "./DeepLinkButton";
import { connectKind, persistenceBlurb } from "./broker-ui";

export type BrokerOAuthResult =
  | { kind: "connected" }
  | { kind: "error"; reason: string };

/**
 * BrokerConnectPanelBody — the full connect/manage flow for one broker, WITHOUT
 * a dialog wrapper. Embedded by BrokerOnboarding, which already owns the
 * surrounding dialog (nesting a second one would be wrong). Mount with
 * `key={broker.id}` to keep form state clean per broker.
 */
export function BrokerConnectPanelBody({
  broker,
  onStatusChange,
  oauthResult,
  onClose,
}: {
  broker: Broker;
  onStatusChange: (brokerId: string, status: BrokerStatus) => void;
  oauthResult?: BrokerOAuthResult | null;
  /** Close the surrounding surface after a fully-live connection. */
  onClose: () => void;
}): React.ReactElement {
  const kind = connectKind(broker);
  const connected = broker.status.connected;

  return (
    <div className="flex flex-col gap-4">
      <DialogHeader className="gap-0 space-y-0 text-left">
        <div className="flex items-center gap-3">
          <BrokerLogo
            brokerId={broker.id}
            logo={broker.logo}
            name={broker.name}
            accent={broker.accent}
            size={40}
          />
          <div className="min-w-0">
            <DialogTitle
              style={{ fontSize: 16, letterSpacing: "-0.01em" }}
              className="truncate"
            >
              {connected ? `${broker.name} connected` : `Connect ${broker.name}`}
            </DialogTitle>
            <p
              style={{
                margin: "2px 0 0",
                fontSize: 12,
                color: "var(--text-tertiary)",
              }}
            >
              {connected
                ? "Manage this connection and review holdings."
                : broker.blurb}
            </p>
          </div>
        </div>
      </DialogHeader>

      {/* OAuth round-trip banner */}
      {oauthResult?.kind === "connected" && !connected && (
        <Banner tone="success">
          Connected to {broker.name}. Your session is live.
        </Banner>
      )}
      {oauthResult?.kind === "error" && (
        <Banner tone="error">Couldn&apos;t connect: {oauthResult.reason}</Banner>
      )}

      {connected ? (
        <BrokerConnectedState
          broker={broker}
          onStatusChange={(s) => onStatusChange(broker.id, s)}
          onDisconnected={() =>
            onStatusChange(broker.id, {
              ...broker.status,
              connected: false,
              broker_user_id: null,
              expires_at: null,
            })
          }
        />
      ) : kind === "oauth" ? (
        <OAuthFlow broker={broker} onStatusChange={onStatusChange} onClose={onClose} />
      ) : kind === "api_key" ? (
        <ApiKeyFlow broker={broker} onStatusChange={onStatusChange} onClose={onClose} />
      ) : (
        <MockFlow broker={broker} onStatusChange={onStatusChange} onClose={onClose} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// OAuth flow (Kite) — primary redirect + collapsible advanced auto-login.
// ---------------------------------------------------------------------------

function OAuthFlow({
  broker,
  onStatusChange,
  onClose,
}: {
  broker: Broker;
  onStatusChange: (brokerId: string, status: BrokerStatus) => void;
  onClose: () => void;
}): React.ReactElement {
  const [redirecting, setRedirecting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const connect = useCallback(async (): Promise<void> => {
    setRedirecting(true);
    setErr(null);
    const res = await getBrokerLoginUrl(broker.id);
    if (isError(res)) {
      setErr(res.error.message || "Couldn't start the login.");
      setRedirecting(false);
      return;
    }
    const url = res.data.login_url;
    if (!url) {
      setErr(
        res.data.mock_mode
          ? "Backend is in demo mode — no real login to redirect to."
          : "No login URL returned by the broker.",
      );
      setRedirecting(false);
      return;
    }
    // Hard nav to the broker. After login the backend bounces back with
    // ?broker=connected (or ?broker=error&reason=…), handled in AppShell.
    window.location.href = url;
  }, [broker.id]);

  return (
    <div className="flex flex-col gap-4">
      {err && <Banner tone="error">{err}</Banner>}

      {/* Primary CTA — one click, zero typed input. */}
      <PrimaryButton
        onClick={() => void connect()}
        busy={redirecting}
        busyLabel="Redirecting…"
      >
        {`Connect ${broker.name}`}
      </PrimaryButton>
      <p style={subtleNote}>
        You&apos;ll sign in on {broker.name}&apos;s secure page and approve
        access — Pivot never sees your password.
      </p>

      {/* Advanced auto-login — collapsible, honest warning. */}
      <div
        style={{
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-md)",
          overflow: "hidden",
        }}
      >
        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          data-testid={`broker-advanced-toggle-${broker.id}`}
          aria-expanded={advancedOpen}
          className="flex w-full items-center justify-between gap-2"
          style={{
            padding: "11px 14px",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            textAlign: "left",
          }}
        >
          <span className="flex items-center gap-2">
            <Lock size={13} strokeWidth={2} style={{ color: "var(--text-tertiary)" }} aria-hidden />
            <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-primary)" }}>
              Advanced: stay connected without daily login
            </span>
          </span>
          <ChevronDown
            size={15}
            strokeWidth={2}
            aria-hidden
            style={{
              color: "var(--text-tertiary)",
              transform: advancedOpen ? "rotate(180deg)" : "rotate(0deg)",
              transition: "transform 0.18s var(--ease-quartr)",
            }}
          />
        </button>

        {advancedOpen && (
          <div
            style={{ borderTop: "1px solid var(--glass-border)", padding: 14 }}
            className="flex flex-col gap-3"
          >
            <p style={{ ...subtleNote, margin: 0 }}>
              {broker.name}&apos;s tokens expire daily. To keep your automations
              running unattended, store your API credentials so Pivot can refresh
              the token for you.
            </p>

            {/* Deep-links straight to the broker's setup pages. */}
            <div className="flex flex-wrap gap-2">
              <DeepLinkButton
                href={broker.deep_links.app_create}
                label={`Open ${broker.name} → create API app`}
                accent={broker.accent}
              />
              <DeepLinkButton
                href={broker.deep_links.totp_setup}
                label={`Open ${broker.name} → enable TOTP`}
                accent={broker.accent}
              />
            </div>

            <CredentialsForm
              broker={broker}
              autoLoginOptIn
              submitLabel="Save & enable auto-login"
              onStatusChange={onStatusChange}
              onClose={onClose}
              showTotp
            />

            <EncryptedWarning />
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// API-key flow (Dhan) — deep-link-first, key/secret form, automation toggle.
// ---------------------------------------------------------------------------

function ApiKeyFlow({
  broker,
  onStatusChange,
  onClose,
}: {
  broker: Broker;
  onStatusChange: (brokerId: string, status: BrokerStatus) => void;
  onClose: () => void;
}): React.ReactElement {
  return (
    <div className="flex flex-col gap-4">
      {/* Lead with the deep-link so they grab keys in one click. */}
      {broker.deep_links.api_key_page && (
        <div
          className="flex flex-col gap-2.5 p-3.5"
          style={{
            border: `1px solid color-mix(in srgb, ${broker.accent} 26%, transparent)`,
            background: `color-mix(in srgb, ${broker.accent} 7%, transparent)`,
            borderRadius: "var(--radius-md)",
          }}
        >
          <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-primary)" }}>
            Step 1 — get your API keys
          </span>
          <p style={{ ...subtleNote, margin: 0 }}>
            Open {broker.name}, go to <strong>Profile → Access {broker.name}HQ
            APIs</strong>, switch to API-Key mode, and copy your key + secret.
          </p>
          <DeepLinkButton
            href={broker.deep_links.api_key_page}
            label={`Open ${broker.name} → API Access`}
            accent={broker.accent}
            variant="accent"
          />
        </div>
      )}

      <div className="flex flex-col gap-2.5">
        <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-primary)" }}>
          Step 2 — paste them here
        </span>
        <CredentialsForm
          broker={broker}
          autoLoginOptIn={false}
          submitLabel={`Connect ${broker.name}`}
          onStatusChange={onStatusChange}
          onClose={onClose}
          showAutomationToggle
          showOptionalIdentity
        />
      </div>

      <div className="flex items-center justify-between gap-2">
        <EncryptedWarning compact />
        <DeepLinkButton href={broker.deep_links.docs} label="API docs" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mock flow (dev) — single stub-connect button.
// ---------------------------------------------------------------------------

function MockFlow({
  broker,
  onStatusChange,
  onClose,
}: {
  broker: Broker;
  onStatusChange: (brokerId: string, status: BrokerStatus) => void;
  onClose: () => void;
}): React.ReactElement {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const connect = useCallback(async (): Promise<void> => {
    setBusy(true);
    setErr(null);
    const res = await connectBrokerMock(broker.id);
    setBusy(false);
    if (isError(res)) {
      setErr(res.error.message || "Mock connect failed.");
      return;
    }
    onStatusChange(broker.id, res.data);
    onClose();
  }, [broker.id, onStatusChange, onClose]);

  return (
    <div className="flex flex-col gap-3">
      {err && <Banner tone="error">{err}</Banner>}
      <p style={{ ...subtleNote, margin: 0 }}>
        The backend has no real {broker.name} credentials configured, so this
        connects a <strong>demo</strong> session serving stub data — handy for
        trying the flow without live keys.
      </p>
      <PrimaryButton onClick={() => void connect()} busy={busy} busyLabel="Connecting…">
        Connect (mock)
      </PrimaryButton>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared credentials form — used by both the Kite advanced path and Dhan.
// ---------------------------------------------------------------------------

function CredentialsForm({
  broker,
  autoLoginOptIn,
  submitLabel,
  onStatusChange,
  onClose,
  showTotp = false,
  showOptionalIdentity = false,
  showAutomationToggle = false,
}: {
  broker: Broker;
  /** Fixed opt-in (Kite advanced path posts true). When showAutomationToggle is
   *  set the user controls it instead and this is the initial value. */
  autoLoginOptIn: boolean;
  submitLabel: string;
  onStatusChange: (brokerId: string, status: BrokerStatus) => void;
  onClose: () => void;
  showTotp?: boolean;
  showOptionalIdentity?: boolean;
  showAutomationToggle?: boolean;
}): React.ReactElement {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [clientId, setClientId] = useState("");
  const [pin, setPin] = useState("");
  const [totpSecret, setTotpSecret] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [autoOn, setAutoOn] = useState(autoLoginOptIn);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const canSubmit = apiKey.trim().length > 0 && apiSecret.trim().length > 0 && !busy;

  const submit = useCallback(async (): Promise<void> => {
    if (!apiKey.trim() || !apiSecret.trim()) {
      setErr("API key and secret are both required.");
      return;
    }
    setBusy(true);
    setErr(null);
    const body: BrokerCredentialsRequest = {
      api_key: apiKey.trim(),
      api_secret: apiSecret.trim(),
      auto_login_opt_in: showAutomationToggle ? autoOn : autoLoginOptIn,
    };
    if (showOptionalIdentity) {
      if (clientId.trim()) body.client_id = clientId.trim();
      if (pin.trim()) body.pin = pin.trim();
    }
    if (showTotp && totpSecret.trim()) body.totp_secret = totpSecret.trim();

    const res = await setBrokerCredentials(broker.id, body);
    setBusy(false);
    if (isError(res)) {
      setErr(res.error.message || "Couldn't save credentials.");
      return;
    }
    onStatusChange(broker.id, res.data);
    // Only auto-close when the connection actually went live; otherwise keep
    // the form open so the user can fix what's missing.
    if (res.data.connected) onClose();
  }, [
    apiKey,
    apiSecret,
    clientId,
    pin,
    totpSecret,
    autoOn,
    autoLoginOptIn,
    showAutomationToggle,
    showOptionalIdentity,
    showTotp,
    broker.id,
    onStatusChange,
    onClose,
  ]);

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <Field label="API key">
        <Input
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={`Your ${broker.name} API key`}
          autoComplete="off"
          spellCheck={false}
          disabled={busy}
          data-testid={`broker-api-key-${broker.id}`}
        />
      </Field>

      <Field label="API secret">
        <div className="relative">
          <Input
            type={showSecret ? "text" : "password"}
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            placeholder="Paste your API secret"
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
            className="pr-9"
            data-testid={`broker-api-secret-${broker.id}`}
          />
          <button
            type="button"
            tabIndex={-1}
            aria-label={showSecret ? "Hide secret" : "Show secret"}
            onClick={() => setShowSecret((v) => !v)}
            className="absolute inset-y-0 right-0 flex items-center px-2"
            style={{ color: "var(--text-tertiary)" }}
          >
            {showSecret ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>
      </Field>

      {showOptionalIdentity && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="Client ID" optional>
            <Input
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="Optional"
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
            />
          </Field>
          <Field label="PIN" optional>
            <Input
              type="password"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              placeholder="Optional"
              autoComplete="off"
              disabled={busy}
            />
          </Field>
        </div>
      )}

      {(showTotp || showOptionalIdentity) && (
        <Field label="TOTP secret" optional>
          <Input
            value={totpSecret}
            onChange={(e) => setTotpSecret(e.target.value)}
            placeholder="For unattended re-login (optional)"
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
          />
        </Field>
      )}

      {showAutomationToggle && broker.supports_unattended && (
        <div
          className="flex items-start gap-3 p-3"
          style={{
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-sm)",
            background: "var(--bg-base)",
          }}
        >
          <div className="min-w-0 flex-1">
            <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-primary)" }}>
              Stay connected — no daily login
            </span>
            <p style={{ ...subtleNote, margin: "2px 0 0" }}>
              {persistenceBlurb(broker.persistence_kind)}
            </p>
          </div>
          <Switch
            checked={autoOn}
            disabled={busy}
            onCheckedChange={setAutoOn}
            aria-label="Stay connected with no daily login"
            data-testid={`broker-credform-automation-${broker.id}`}
          />
        </div>
      )}

      {err && <Banner tone="error">{err}</Banner>}

      <PrimaryButton type="submit" busy={busy} busyLabel="Connecting…" disabled={!canSubmit}>
        {submitLabel}
      </PrimaryButton>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Small shared presentational atoms
// ---------------------------------------------------------------------------

const subtleNote: React.CSSProperties = {
  fontSize: 11.5,
  lineHeight: 1.55,
  color: "var(--text-tertiary)",
  margin: 0,
};

function Field({
  label,
  optional,
  children,
}: {
  label: string;
  optional?: boolean;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="flex flex-col gap-1.5">
      <Label
        className="flex items-center gap-1.5"
        style={{ fontSize: 12, color: "var(--text-secondary)" }}
      >
        {label}
        {optional && (
          <span style={{ fontSize: 10.5, fontWeight: 400, color: "var(--text-tertiary)" }}>
            optional
          </span>
        )}
      </Label>
      {children}
    </div>
  );
}

function PrimaryButton({
  children,
  onClick,
  busy = false,
  busyLabel,
  type = "button",
  disabled = false,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  busy?: boolean;
  busyLabel?: string;
  type?: "button" | "submit";
  disabled?: boolean;
}): React.ReactElement {
  const isDisabled = busy || disabled;
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={isDisabled}
      className="inline-flex w-full items-center justify-center gap-2"
      style={{
        height: 42,
        borderRadius: "var(--radius-sm)",
        background: "var(--text-primary)",
        color: "var(--bg-base)",
        fontFamily: "var(--font-ui)",
        fontSize: 13.5,
        fontWeight: 600,
        letterSpacing: "-0.01em",
        border: "none",
        cursor: isDisabled ? "default" : "pointer",
        opacity: isDisabled && !busy ? 0.45 : 1,
        boxShadow: "var(--shadow-cta)",
        transition: "opacity 0.2s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        if (!isDisabled) e.currentTarget.style.opacity = "0.9";
      }}
      onMouseLeave={(e) => {
        if (!isDisabled) e.currentTarget.style.opacity = "1";
      }}
    >
      {busy && <Loader2 size={15} className="animate-spin" aria-hidden />}
      {busy && busyLabel ? busyLabel : children}
    </button>
  );
}

function EncryptedWarning({ compact = false }: { compact?: boolean }): React.ReactElement {
  return (
    <div
      className="flex items-start gap-2"
      style={{
        fontSize: 11,
        lineHeight: 1.5,
        color: "var(--text-tertiary)",
      }}
    >
      <Lock
        size={12}
        strokeWidth={2}
        style={{ marginTop: 2, flexShrink: 0, color: "var(--text-tertiary)" }}
        aria-hidden
      />
      <span>
        {compact ? (
          <>Stored encrypted. Revoke anytime.</>
        ) : (
          <>
            Your credentials are stored <strong>encrypted</strong> and used only
            to refresh your broker token. We never place orders without your
            confirmation, and you can revoke access anytime by disconnecting.
          </>
        )}
      </span>
    </div>
  );
}

export function Banner({
  tone,
  children,
}: {
  tone: "success" | "error";
  children: React.ReactNode;
}): React.ReactElement {
  const success = tone === "success";
  const color = success ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <div
      role={success ? "status" : "alert"}
      className="flex items-start gap-2"
      style={{
        padding: "9px 11px",
        borderRadius: "var(--radius-sm)",
        border: `1px solid color-mix(in srgb, ${color} 35%, transparent)`,
        background: `color-mix(in srgb, ${color} 10%, transparent)`,
        fontSize: 12,
        lineHeight: 1.45,
        color,
      }}
    >
      {success ? (
        <CheckCircle2 size={14} style={{ marginTop: 1, flexShrink: 0 }} aria-hidden />
      ) : (
        <XCircle size={14} style={{ marginTop: 1, flexShrink: 0 }} aria-hidden />
      )}
      <span>{children}</span>
    </div>
  );
}
