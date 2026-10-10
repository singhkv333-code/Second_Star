"use client";

/**
 * The billing routes on charto's dataserver.
 *
 * Plans and subscriptions belong to the CHART's account (charto_users.db),
 * so every call carries charto's session token, the same one the chart and
 * the broker page use. Same-origin paths: next.config.ts proxies `/billing/*`
 * to the dataserver in development and nginx falls through to it in
 * production.
 */

import { readChartoToken } from "@/lib/charto-auth";
import type { BillingMe, Cycle, InvoiceList, PaidPlanId, PublicCatalog, Refusal } from "./types";

export type BillingResult<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; error: string; code?: string; body?: unknown };

const BASE = (typeof process !== "undefined" && process.env.NEXT_PUBLIC_BILLING_BASE) || "";

function headers(json = false): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  const t = readChartoToken();
  if (t) h.Authorization = `Bearer ${t}`;
  return h;
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<BillingResult<T>> {
  let r: Response;
  try {
    r = await fetch(`${BASE}${path}`, {
      method,
      headers: headers(body !== undefined),
      body: body !== undefined ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch {
    return { ok: false, status: 0, error: "Could not reach the server. Check your connection and try again.", code: "network" };
  }
  let data: unknown = null;
  try {
    data = await r.json();
  } catch {
    /* an empty or non-JSON body; the status still says what happened */
  }
  if (r.ok) return { ok: true, status: r.status, data: data as T };
  const d = (data ?? {}) as { error?: string; code?: string };
  return {
    ok: false,
    status: r.status,
    error: d.error || `The server answered ${r.status}.`,
    code: d.code,
    body: data,
  };
}

export const hasChartoSession = (): boolean => !!readChartoToken();

export const billingApi = {
  plans: () => call<PublicCatalog>("GET", "/billing/plans"),
  me: () => call<BillingMe>("GET", "/billing/me"),
  invoices: () => call<InvoiceList>("GET", "/billing/invoices"),
  checkout: (plan: PaidPlanId, cycle: Cycle) =>
    call<{ subscription_id: string; key_id: string; short_url?: string; prefill?: { email?: string } }>(
      "POST",
      "/billing/checkout",
      { plan, cycle },
    ),
  verify: (p: { razorpay_payment_id: string; razorpay_subscription_id: string; razorpay_signature: string }) =>
    call<{ ok?: true; pending?: true; note?: string; billing?: BillingMe }>("POST", "/billing/verify", p),
  change: (plan: PaidPlanId, cycle: Cycle) =>
    call<{ effective: "now" | "cycle_end"; at?: number; billing: BillingMe }>("POST", "/billing/change", { plan, cycle }),
  undoChange: () => call<{ billing: BillingMe }>("POST", "/billing/change", { undo: true }),
  cancel: () => call<{ active_until: number; billing: BillingMe }>("POST", "/billing/cancel"),
};

/** A 402 from any service, as the paywall reads it. */
export function refusalOf(res: BillingResult<unknown>): Refusal | null {
  if (res.ok || res.status !== 402) return null;
  return (res.body as Refusal) ?? null;
}
