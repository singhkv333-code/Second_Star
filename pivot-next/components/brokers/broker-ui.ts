/**
 * Shared presentational helpers for the broker onboarding UI. Pure functions —
 * no React — so the picker, connect panel, and connected state derive labels,
 * badges, and copy from the same place.
 */

import type {
  Broker,
  BrokerPersistenceKind,
  BrokerStatus,
} from "@/lib/types";

export type ConnectionBadge = {
  label: string;
  /** Drives the dot + text color. */
  tone: "connected" | "mock" | "idle";
};

/** Derive the small status pill shown on each broker card. */
export function connectionBadge(status: BrokerStatus): ConnectionBadge {
  if (status.connected) return { label: "Connected", tone: "connected" };
  if (status.mock_mode) return { label: "Demo data", tone: "mock" };
  return { label: "Not connected", tone: "idle" };
}

/** True when this persistence kind keeps the broker connected with NO daily
 *  human re-login (rolling/minted/refresh/opt-in-replay). Only `daily_oauth`
 *  (and unknown future kinds, conservatively) require a daily login. */
export function isUnattendedKind(kind: BrokerPersistenceKind): boolean {
  return (
    kind === "rolling_renew" ||
    kind === "api_key_mint" ||
    kind === "refresh_token" ||
    kind === "totp_login"
  );
}

/** Human label for a persistence mode — used in the connected summary. Keyed
 *  on the backend `PersistenceKind` *values* (see BrokerPersistenceKind). */
export function persistenceLabel(kind: BrokerPersistenceKind): string {
  switch (kind) {
    case "rolling_renew":
      return "Stays connected";
    case "api_key_mint":
    case "refresh_token":
    case "totp_login":
      return "Auto-login on";
    case "daily_oauth":
    default:
      return "Daily login";
  }
}

/** Longer, sentence-form explanation of a persistence mode. */
export function persistenceBlurb(kind: BrokerPersistenceKind): string {
  switch (kind) {
    case "rolling_renew":
      return "Rolling token — no daily login. Stays connected until you disconnect.";
    case "api_key_mint":
      return "Your API key mints a fresh token each day automatically. No daily login.";
    case "refresh_token":
      return "A refresh token renews access silently. No daily login.";
    case "totp_login":
      return "Encrypted credentials let Pivot refresh the token for you. No daily login.";
    case "daily_oauth":
    default:
      return "Token expires overnight (broker policy) — reconnect once a day.";
  }
}

/** The effective persistence kind for a connection — honours the per-session
 *  override (`status.persistence_mode`) over the catalog default. */
export function effectivePersistence(broker: Broker): BrokerPersistenceKind {
  return broker.status.persistence_mode ?? broker.persistence_kind;
}

/** Connect flow this broker uses, derived from its capability flags.
 *   - "oauth"     — hosted login redirect (Kite).
 *   - "api_key"   — typed key/secret form (Dhan).
 *   - "mock"      — dev stub connect (mock mode, nothing real to connect to).
 */
export function connectKind(broker: Broker): "oauth" | "api_key" | "mock" {
  if (broker.status.mock_mode && !broker.status.connected) return "mock";
  // A broker can be both (Kite: OAuth primary + api-key advanced). The PRIMARY
  // flow is OAuth unless the broker exclusively needs an api key.
  if (!broker.needs_api_key) return "oauth";
  // needs_api_key === true. If it also exposes an OAuth login the primary is
  // still OAuth (Kite); otherwise it's a pure api-key broker (Dhan).
  return broker.deep_links.login || brokerHasOauth(broker) ? "oauth" : "api_key";
}

/** Heuristic: a broker offers OAuth when its id is a known hosted-login broker
 *  OR it advertises a login deep-link. Kite is OAuth-primary even though it
 *  also accepts API keys for the advanced auto-login path. */
export function brokerHasOauth(broker: Broker): boolean {
  if (broker.id === "kite" || broker.id === "zerodha") return true;
  return Boolean(broker.deep_links.login);
}

/** Format an ISO expiry into a short, friendly "Today 3:30 PM" / "20 Jun" form. */
export function formatExpiry(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const time = d.toLocaleTimeString("en-IN", {
    hour: "numeric",
    minute: "2-digit",
  });
  if (sameDay) return `Today ${time}`;
  const date = d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  return `${date}, ${time}`;
}

/** INR currency formatter shared by the holdings preview. */
const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

export function fmtInr(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return INR.format(n);
}
