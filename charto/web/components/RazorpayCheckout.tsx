"use client";

import Script from "next/script";
import Link from "next/link";
import { useRef, useState } from "react";

type PaymentResult = {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
};
type CheckoutOptions = {
  key: string; amount: number; currency: string; order_id: string;
  name: string; description: string;
  handler: (result: PaymentResult) => void;
  modal: { ondismiss: () => void };
  theme: { color: string };
};
type CheckoutInstance = {
  open: () => void;
  on: (event: "payment.failed", handler: (response: { error?: { description?: string } }) => void) => void;
};
declare global {
  interface Window { Razorpay?: new (options: CheckoutOptions) => CheckoutInstance; }
}

async function post(path: string, body: unknown) {
  const response = await fetch(path, { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Checkout could not complete this request.");
  return data;
}

export function RazorpayCheckout({ testMode, configured }: { testMode: boolean; configured: boolean }) {
  const [ready, setReady] = useState(false);
  const [amount, setAmount] = useState("1.00");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [paid, setPaid] = useState(false);
  const [pending, setPending] = useState<PaymentResult | null>(null);
  const inFlight = useRef(false);

  async function verify(result: PaymentResult) {
    setPending(result);
    setMessage("Verifying your payment…");
    try {
      const data = await post("/api/verify-payment", result);
      setPaid(data.paid === true);
      setFailed(false);
      setMessage(data.paid
        ? `Payment confirmed. Reference: ${result.razorpay_payment_id}`
        : "Payment authorized. Capture is pending; use Check payment status to check again.");
      if (data.paid) setPending(null);
    } catch (error) {
      setFailed(true);
      setMessage(`${error instanceof Error ? error.message : "Verification failed."} Check payment status before paying again.`);
    } finally { inFlight.current = false; setBusy(false); }
  }

  async function checkout() {
    if (inFlight.current) return;
    if (!/^[0-9]+(?:\.[0-9]{1,2})?$/.test(amount)) {
      setFailed(true); setMessage("Enter an INR amount with at most two decimal places."); return;
    }
    const paise = Math.round(Number(amount) * 100);
    if (!Number.isSafeInteger(paise) || paise < 100 || paise > 1_000_000_000) {
      setFailed(true); setMessage("Enter an amount between ₹1 and ₹1,00,00,000."); return;
    }
    inFlight.current = true;
    setBusy(true); setFailed(false); setMessage("Preparing checkout…");
    try {
      if (!window.Razorpay) throw new Error("Checkout has not loaded. Please refresh and try again.");
      const order = await post("/api/create-order", { amount: paise, currency: "INR" });
      let completed = false;
      const modal = new window.Razorpay({
        key: order.key_id, amount: order.amount, currency: order.currency, order_id: order.order_id,
        name: "Pivot", description: "Pivot payment", theme: { color: "#111111" },
        handler: (result) => { completed = true; void verify(result); },
        modal: { ondismiss: () => {
          if (completed) return;
          inFlight.current = false; setBusy(false);
          setMessage("Checkout closed. Payment has not been confirmed.");
        } },
      });
      modal.on("payment.failed", (response) => {
        setFailed(true);
        setMessage(response.error?.description || "Payment failed. Try again in checkout or close the window.");
      });
      setMessage("Complete your payment in the secure checkout window.");
      modal.open();
    } catch (error) {
      inFlight.current = false; setBusy(false); setFailed(true);
      setMessage(error instanceof Error ? error.message : "Unable to open checkout.");
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center overflow-y-auto px-6 py-12">
      <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="afterInteractive"
        onReady={() => setReady(true)} onError={() => {
          setFailed(true); setMessage("Secure checkout could not load. Refresh to try again.");
        }} />
      <section className="w-full max-w-md rounded-3xl border border-border bg-background p-8 shadow-sm">
        <Link href="/" className="font-serif text-3xl font-semibold">Pivot.</Link>
        <h1 className="mb-3 mt-8 text-2xl font-semibold">Secure checkout</h1>
        <p className="mb-6 text-sm text-muted-foreground">
          {testMode ? "Test checkout — no real money is collected." : "Pay securely through Razorpay."}
          {" "}This is a one-time payment. It does not start or upgrade a Pivot subscription.
        </p>
        <label htmlFor="checkout-amount" className="mb-2 block text-sm font-medium">Amount (INR)</label>
        <input id="checkout-amount" type="text" inputMode="decimal" value={amount}
          disabled={busy || paid || !!pending} onChange={(event) => setAmount(event.target.value)}
          className="mb-5 w-full rounded-lg border border-border bg-background px-4 py-3 text-lg" />
        <button type="button" onClick={() => {
          if (pending) { inFlight.current = true; setBusy(true); void verify(pending); }
          else void checkout();
        }} disabled={!ready || !configured || busy || paid}
          className="w-full rounded-lg bg-primary px-4 py-3 font-medium text-primary-foreground disabled:opacity-50">
          {busy ? "Processing…" : paid ? "Payment confirmed" : pending ? "Check payment status" : "Pay with Razorpay"}
        </button>
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
          Review our <Link href="/terms" className="underline underline-offset-4">Terms & conditions</Link> and <Link href="/privacy" className="underline underline-offset-4">Privacy policy</Link>.
          {" "}Payment questions? <a href="mailto:info@pivotnow.in" className="underline underline-offset-4">Contact Pivot</a>.
        </p>
        {!configured && <p className="mt-4 text-sm" role="alert">Checkout is not configured yet.</p>}
        {message && <p className={`mt-4 text-sm ${failed ? "text-red-600" : "text-muted-foreground"}`}
          role={failed ? "alert" : "status"}>{message}</p>}
      </section>
    </main>
  );
}
