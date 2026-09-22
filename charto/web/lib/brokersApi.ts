"use client";

/**
 * brokersApi — the charto dataserver's `/brokers` surface.
 *
 * Same base + token as `charto-auth`: the broker connection belongs to the
 * CHART's account, because the chart is what places the orders. Pointing this
 * at Pivot's `/api` would connect a broker to a different person's account —
 * the two user tables are disjoint.
 */

import { readChartoToken } from "@/lib/charto-auth";

export type BrokerField = {
  name: string;
  label: string;
  type: "text" | "password";
};

export type BrokerLinks = {
  app_create: string | null;
  api_key_page: string | null;
  totp_setup: string | null;
  docs: string | null;
};

export type BrokerEntry = {
  id: string;
  name: string;
  logo: string;
  accent: string;
  blurb: string;
  tags: string[];
  needs_api_key: boolean;
  supports_oauth: boolean;
  supports_unattended: boolean;
  persistence_kind: string;
  connected: boolean;
  /** Seconds until the broker kills the token, or null when it has no expiry. */
  expires_in: number | null;
  /** We hold a session we can no longer use — the reconnect prompt keys on this. */
  needs_reconnect: boolean;
  live_enabled: boolean;
  broker_user_id: string | null;
  last_error: string;
  links: BrokerLinks;
  fields: BrokerField[];
};

export type BrokerCatalog = {
  brokers: BrokerEntry[];
  /** Server-side kill switch. False ⇒ no order can reach a broker, whatever
   *  any per-connection toggle says, and the UI must not imply otherwise. */
  live_armed: boolean;
  encrypted: boolean;
};

export type BrokerOrder = {
  broker: string;
  strategy_id: number | null;
  symbol: string;
  side: string;
  quantity: number;
  order_type: string;
  price: number | null;
  status: string;
  order_id: string;
  message: string;
  created: number;
};

function apiBase(): string {
  if (typeof window === "undefined") return "";
  return ["localhost", "127.0.0.1"].includes(window.location.hostname)
    ? "http://127.0.0.1:5174"
    : "";
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const token = readChartoToken();
  const response = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  const data: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message =
      (data as { error?: string; reason?: string }).error ??
      (data as { reason?: string }).reason ??
      `Request failed (${response.status})`;
    throw new Error(message);
  }
  return data as T;
}

export function getBrokers(): Promise<BrokerCatalog> {
  return call<BrokerCatalog>("/brokers");
}

export function getBrokerOrders(): Promise<{ orders: BrokerOrder[] }> {
  return call<{ orders: BrokerOrder[] }>("/brokers/orders");
}

export function connectBroker(
  broker: string,
  payload: Record<string, string>,
): Promise<{ connected: boolean; expires_in: number | null }> {
  return call(`/brokers/${broker}/connect`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** The one-tap daily reconnect. Resolves silently where the broker allows an
 *  unattended mint; rejects with the reason when a browser login is required. */
export function reconnectBroker(
  broker: string,
): Promise<{ ok: boolean; expires_in: number | null }> {
  return call(`/brokers/${broker}/reconnect`, { method: "POST", body: "{}" });
}

export function disconnectBroker(broker: string): Promise<{ ok: boolean }> {
  return call(`/brokers/${broker}/disconnect`, { method: "POST", body: "{}" });
}

export function setBrokerLive(
  broker: string,
  enabled: boolean,
): Promise<{ live_enabled: boolean }> {
  return call(`/brokers/${broker}/live`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export function getBrokerLoginUrl(
  broker: string,
): Promise<{ login_url: string | null }> {
  return call(`/brokers/${broker}/login_url`, { method: "POST", body: "{}" });
}

/** "4h 20m" / "18m" / "expired" — the only place this phrasing is decided. */
export function formatExpiry(seconds: number | null): string {
  if (seconds === null) return "No expiry";
  if (seconds <= 0) return "Expired";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h >= 1) return `${h}h ${m}m left`;
  return `${m}m left`;
}
