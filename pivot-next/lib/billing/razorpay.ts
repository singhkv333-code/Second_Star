"use client";

/**
 * Razorpay Checkout.js, for a subscription the server already created.
 *
 * The browser never decides that a payment happened: the handler's three ids
 * go to POST /billing/verify, which checks the HMAC and FETCHES the
 * subscription from Razorpay before granting anything, and the webhook is the
 * source of truth after that (charto/data/billing.py).
 */

const SRC = "https://checkout.razorpay.com/v1/checkout.js";

type RzpSuccess = {
  razorpay_payment_id: string;
  razorpay_subscription_id: string;
  razorpay_signature: string;
};

type RzpInstance = {
  open: () => void;
  on: (ev: "payment.failed", cb: (r: { error?: { description?: string; reason?: string } }) => void) => void;
};

declare global {
  interface Window {
    Razorpay?: new (opts: Record<string, unknown>) => RzpInstance;
  }
}

let loading: Promise<void> | null = null;

export function loadCheckout(): Promise<void> {
  if (typeof window === "undefined") return Promise.reject(new Error("no window"));
  if (window.Razorpay) return Promise.resolve();
  if (loading) return loading;
  loading = new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SRC;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => {
      loading = null;
      reject(new Error("The payment window could not load. Check your connection or any content blocker, then try again."));
    };
    document.head.appendChild(s);
  });
  return loading;
}

export type CheckoutOutcome =
  | { kind: "paid"; ids: RzpSuccess }
  | { kind: "dismissed" }
  | { kind: "failed"; reason: string };

export async function openCheckout(opts: {
  keyId: string;
  subscriptionId: string;
  description: string;
  email?: string;
}): Promise<CheckoutOutcome> {
  await loadCheckout();
  const Rzp = window.Razorpay;
  if (!Rzp) throw new Error("The payment window could not load.");
  return new Promise<CheckoutOutcome>((resolve) => {
    let failed: string | null = null;
    const rzp = new Rzp({
      key: opts.keyId,
      subscription_id: opts.subscriptionId,
      name: "Pivot",
      description: opts.description,
      prefill: opts.email ? { email: opts.email } : undefined,
      theme: { color: "#0d0d0e" },
      handler: (ids: RzpSuccess) => resolve({ kind: "paid", ids }),
      modal: {
        // Razorpay keeps its window open after a failed attempt so the user
        // can retry another method; closing it after a failure is a failure.
        ondismiss: () => resolve(failed ? { kind: "failed", reason: failed } : { kind: "dismissed" }),
      },
    });
    rzp.on("payment.failed", (r) => {
      failed = r.error?.description || r.error?.reason || "The payment was declined.";
    });
    rzp.open();
  });
}
