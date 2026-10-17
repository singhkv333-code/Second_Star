"use client";

import { readChartoToken } from "@/lib/charto-auth";

/**
 * The referral programme as the shell sees it. Charto owns the ledger
 * (dataserver/referrals.py); `GET /referrals/me` returns the account's
 * summary and mints its link on first call, so "signing up" for the
 * programme is simply the first read.
 */

/** The published rate, used until /referrals/terms answers (and if it cannot). */
export const REFERRAL_RATE_PERCENT = 15;

/** Where every "Sign up" on /affiliate lands: the product, which opens the join popup. */
export const AFFILIATE_JOIN_HREF = "/?affiliate=join";

/** Settings → Affiliate in the product (also where /affiliate/referrals now redirects). */
export const AFFILIATE_SETTINGS_HREF = "/?settings=affiliate";

/** Local development only: sample referral data, so the popup and
 *  Settings → Affiliate can be previewed without the referral ledger. */
export const REFERRALS_DEMO = process.env.NODE_ENV === "development" && process.env.NEXT_PUBLIC_PIVOT_REFERRALS_DEMO === "1";

export type ReferralSummary = {
  code: string;
  signups: number;
  paid_customers: number;
  totals_paise: Record<"pending" | "approved" | "paid" | "exception", number>;
  payouts: { amount_paise: number; reference: string; created: number }[];
};

const DEMO_SUMMARY: ReferralSummary = {
  code: "demo-link-inactive", signups: 12, paid_customers: 4,
  totals_paise: { pending: 24950, approved: 12475, paid: 49900, exception: 0 },
  payouts: [{ amount_paise: 49900, reference: "DEMO-TRANSFER", created: 1792022400 }],
};

export async function fetchReferralSummary(): Promise<ReferralSummary> {
  if (REFERRALS_DEMO) return DEMO_SUMMARY;
  const token = readChartoToken();
  if (!token) throw new Error("Sign in to get your affiliate link.");
  let r: Response;
  try {
    r = await fetch("/referrals/me", { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
  } catch {
    throw new Error("Pivot could not be reached. Check your connection and try again.");
  }
  if (!r.ok) {
    // 404: the dataserver predates the referral routes (needs a restart on
    // current code). 503: the routes exist but the programme's settings are
    // not configured on the server (see docs/REFERRALS.md).
    throw new Error(r.status === 401 ? "Sign in again to get your affiliate link."
      : r.status === 404 ? "Affiliate links are not available on this server yet. The server needs updating."
      : r.status === 503 ? "The affiliate programme is not set up on this server yet."
      : "Your affiliate link could not be created. Try again shortly.");
  }
  return r.json() as Promise<ReferralSummary>;
}

export function referralLink(code: string): string {
  return typeof window === "undefined" ? `/r/${code}` : `${window.location.origin}/r/${code}`;
}

export const inr = (n: number): string => "₹" + Math.round(n).toLocaleString("en-IN");
