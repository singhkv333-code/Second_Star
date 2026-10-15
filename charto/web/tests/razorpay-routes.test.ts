// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { createHmac } from "node:crypto";

const provider = vi.hoisted(() => ({ create: vi.fn(), fetch: vi.fn() }));
vi.mock("razorpay", () => ({ default: class {
  orders = { create: provider.create };
  payments = { fetch: provider.fetch };
} }));
import { POST as createOrder } from "../app/api/create-order/route";
import { POST as verifyPayment } from "../app/api/verify-payment/route";
import { orderToken, readOrderToken } from "../lib/razorpay-server";

function request(path: string, body: unknown, cookie = "") {
  return new NextRequest(`https://pivotnow.in/api/${path}`, {
    method: "POST", headers: { "content-type": "application/json", cookie }, body: JSON.stringify(body),
  });
}
function result() {
  return { razorpay_order_id: "order_test", razorpay_payment_id: "pay_test",
    razorpay_signature: createHmac("sha256", "test-secret").update("order_test|pay_test").digest("hex") };
}
function cookie() { return `pivot_order_order_test=${orderToken("order_test", 100, "INR")}`; }

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubEnv("RAZORPAY_KEY_ID", "rzp_test_fixture");
  vi.stubEnv("RAZORPAY_KEY_SECRET", "test-secret");
  provider.create.mockResolvedValue({ id: "order_test", amount: 100, currency: "INR" });
  provider.fetch.mockResolvedValue({ order_id: "order_test", amount: 100, currency: "INR", status: "captured" });
});

describe("Standard Checkout orders", () => {
  it("retires arbitrary-amount orders without contacting the provider", async () => {
    const response = await createOrder(request("create-order", { amount: 100 }));
    expect(response.status).toBe(410);
    expect(provider.create).not.toHaveBeenCalled();
    expect(await response.json()).toMatchObject({ checkout_url: "https://app.pivotnow.in/pricing" });
  });
});

describe("Standard Checkout verification", () => {
  it("rejects missing fields", async () => {
    expect((await verifyPayment(request("verify-payment", {}))).status).toBe(400);
  });
  it("rejects an invalid signature without fetching or marking a payment paid", async () => {
    const response = await verifyPayment(request("verify-payment", { ...result(), razorpay_signature: "0".repeat(64) }, cookie()));
    expect(response.status).toBe(400);
    expect(provider.fetch).not.toHaveBeenCalled();
  });
  it("requires a server-issued order for this browser", async () => {
    expect((await verifyPayment(request("verify-payment", result()))).status).toBe(401);
    expect(provider.fetch).not.toHaveBeenCalled();
  });
  it("rejects tampered and expired receipts", () => {
    expect(() => readOrderToken(`${orderToken("order_test", 100, "INR")}bad`, "order_test")).toThrow();
    const token = orderToken("order_test", 100, "INR");
    const now = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(now + 3_600_001);
    expect(() => readOrderToken(token, "order_test")).toThrow();
    vi.restoreAllMocks();
  });
  it("confirms a captured payment, including repeated verification", async () => {
    for (let i = 0; i < 2; i++) {
      const response = await verifyPayment(request("verify-payment", result(), cookie()));
      expect(response.status).toBe(200);
      expect(await response.json()).toMatchObject({ success: true, paid: true, status: "captured" });
    }
  });
  it("reports authorization as pending capture", async () => {
    provider.fetch.mockResolvedValue({ order_id: "order_test", amount: 100, currency: "INR", status: "authorized" });
    const response = await verifyPayment(request("verify-payment", result(), cookie()));
    expect(response.status).toBe(202);
    expect(await response.json()).toMatchObject({ success: true, paid: false });
  });
  it.each([
    { order_id: "order_other" }, { amount: 200 }, { currency: "USD" }, { status: "failed" },
  ])("rejects a mismatched or failed provider payment %s", async (change) => {
    provider.fetch.mockResolvedValue({ order_id: "order_test", amount: 100, currency: "INR", status: "captured", ...change });
    expect((await verifyPayment(request("verify-payment", result(), cookie()))).status).toBe(400);
  });
});
