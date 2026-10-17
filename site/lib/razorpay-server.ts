// Server routes only: never import this module into a client component.
import Razorpay from "razorpay";
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

export class CheckoutError extends Error {
  constructor(message: string, public readonly status = 400) { super(message); }
}

export function paymentConfig() {
  const keyId = process.env.RAZORPAY_KEY_ID;
  const keySecret = process.env.RAZORPAY_KEY_SECRET;
  if (!keyId || !keySecret) throw new CheckoutError("Checkout is not configured.", 503);
  return { keyId, keySecret };
}

export function paymentClient() {
  const { keyId, keySecret } = paymentConfig();
  return new Razorpay({ key_id: keyId, key_secret: keySecret });
}

export function digest(message: string) {
  return createHmac("sha256", paymentConfig().keySecret).update(message).digest("hex");
}

export function signaturesMatch(expected: string, received: string) {
  if (!/^[a-f0-9]{64}$/i.test(received)) return false;
  return timingSafeEqual(Buffer.from(expected, "hex"), Buffer.from(received, "hex"));
}

export function orderCookieName(orderId: string) { return `pivot_order_${orderId}`; }

// An authenticated, HttpOnly order receipt binds verification to an order
// issued by this server to this browser. It works across Vercel instances,
// without an in-memory map or a new payment table. No access is granted here.
export function orderToken(orderId: string, amount: number, currency: string) {
  const payload = Buffer.from(JSON.stringify({ orderId, amount, currency,
    expires: Date.now() + 60 * 60 * 1000 })).toString("base64url");
  return `${payload}.${digest(`checkout-receipt:${payload}`)}`;
}

export function readOrderToken(token: string | undefined, orderId: string): {
  orderId: string; amount: number; currency: string;
} {
  if (token) {
    const [payload, signature] = token.split(".");
    if (payload && signature && signaturesMatch(digest(`checkout-receipt:${payload}`), signature)) {
      try {
        const order = JSON.parse(Buffer.from(payload, "base64url").toString());
        if (order.orderId === orderId && order.expires > Date.now()) return order;
      } catch { /* Invalid receipts fail closed. */ }
    }
  }
  throw new CheckoutError("This checkout has expired. Start a new payment.", 401);
}

export async function checkoutBody(request: NextRequest): Promise<Record<string, unknown>> {
  const origin = request.headers.get("origin");
  if (origin && origin !== request.nextUrl.origin) throw new CheckoutError("Invalid checkout origin.", 403);
  try {
    const body = await request.json();
    if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error();
    return body;
  } catch { throw new CheckoutError("Provide a valid JSON body."); }
}

export function newReceipt() { return `pivot_${randomBytes(12).toString("hex")}`; }

export function checkoutFailure(error: unknown) {
  if (error instanceof CheckoutError) {
    return NextResponse.json({ error: error.message }, { status: error.status });
  }
  const provider = error as { statusCode?: number } | null;
  const status = provider?.statusCode === 401 ? 401 : 500;
  // Provider responses may contain sensitive details. Do not relay or log them.
  return NextResponse.json({ error: status === 401
    ? "The payment provider could not authenticate checkout. Please contact Pivot."
    : "The payment provider could not complete this request. Please try again." }, { status });
}
