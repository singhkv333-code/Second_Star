import { NextRequest, NextResponse } from "next/server";
import { CheckoutError, checkoutBody, checkoutFailure, digest, orderCookieName,
  paymentClient, readOrderToken, signaturesMatch } from "@/lib/razorpay-server";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  try {
    const body = await checkoutBody(request);
    const orderId = body.razorpay_order_id;
    const paymentId = body.razorpay_payment_id;
    const signature = body.razorpay_signature;
    if (typeof orderId !== "string" || !/^order_[a-zA-Z0-9]+$/.test(orderId) ||
        typeof paymentId !== "string" || !/^pay_[a-zA-Z0-9]+$/.test(paymentId) ||
        typeof signature !== "string" || !signature) {
      throw new CheckoutError("Payment ID, order ID and signature are required.");
    }
    const order = readOrderToken(request.cookies.get(orderCookieName(orderId))?.value, orderId);
    // Use the server-issued order ID, never trust the callback alone.
    if (!signaturesMatch(digest(`${order.orderId}|${paymentId}`), signature)) {
      throw new CheckoutError("Payment signature did not match. Payment was not verified.");
    }
    const payment = await paymentClient().payments.fetch(paymentId);
    if (payment.order_id !== order.orderId || Number(payment.amount) !== order.amount ||
        payment.currency !== order.currency) throw new CheckoutError("Payment does not match this order.");
    if (payment.status !== "captured" && payment.status !== "authorized") {
      throw new CheckoutError("The provider has not confirmed this payment.");
    }
    // A valid signature is not proof of capture. Never call an authorized
    // payment paid, and never activate a subscription from this endpoint.
    const paid = payment.status === "captured";
    return NextResponse.json({ success: true, paid, status: payment.status,
      order_id: order.orderId, payment_id: paymentId }, {
      status: paid ? 200 : 202, headers: { "Cache-Control": "no-store" },
    });
  } catch (error) { return checkoutFailure(error); }
}
