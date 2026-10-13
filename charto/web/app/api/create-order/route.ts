import { NextRequest, NextResponse } from "next/server";
import { CheckoutError, checkoutBody, checkoutFailure, newReceipt, orderCookieName,
  orderToken, paymentClient, paymentConfig } from "@/lib/razorpay-server";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  try {
    const body = await checkoutBody(request);
    if (typeof body.amount !== "number" || !Number.isSafeInteger(body.amount) ||
        body.amount < 100 || body.amount > 1_000_000_000) {
      throw new CheckoutError("Amount must be an integer between 100 and 1,000,000,000 paise.");
    }
    const currency = body.currency ?? "INR";
    if (currency !== "INR") throw new CheckoutError("Pivot checkout accepts INR only.");
    if (body.receipt !== undefined && (typeof body.receipt !== "string" ||
        !body.receipt.trim() || body.receipt.length > 40)) {
      throw new CheckoutError("Receipt must contain between 1 and 40 characters.");
    }
    const order = await paymentClient().orders.create({ amount: body.amount,
      currency, receipt: typeof body.receipt === "string" ? body.receipt : newReceipt() });
    const { keyId } = paymentConfig();
    const amount = Number(order.amount);
    const response = NextResponse.json({ order_id: order.id, amount,
      currency: order.currency, key_id: keyId }, { headers: { "Cache-Control": "no-store" } });
    response.cookies.set(orderCookieName(order.id), orderToken(order.id, amount, order.currency), {
      httpOnly: true, secure: request.nextUrl.protocol === "https:",
      sameSite: "strict", path: "/api/verify-payment", maxAge: 3600,
    });
    return response;
  } catch (error) { return checkoutFailure(error); }
}
