// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RazorpayCheckout } from "../components/RazorpayCheckout";

vi.mock("next/script", () => ({ default: ({ onReady }: { onReady: () => void }) =>
  <button onClick={onReady}>Load checkout script</button> }));

let options: ConstructorParameters<NonNullable<Window["Razorpay"]>>[0];
let failure: (response: { error?: { description?: string } }) => void;
const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({ ok: true, json: async () => ({ order_id: "order_test", amount: 100, currency: "INR", key_id: "rzp_test_fixture" }) });
  window.Razorpay = class {
    constructor(config: typeof options) { options = config; }
    open() {}
    on(_event: "payment.failed", handler: typeof failure) { failure = handler; }
  };
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function openCheckout() {
  render(<RazorpayCheckout testMode configured />);
  fireEvent.click(screen.getByText("Load checkout script"));
  fireEvent.click(screen.getByRole("button", { name: "Pay with Razorpay" }));
  await screen.findByText("Complete your payment in the secure checkout window.");
}

it("opens checkout with the server order and handles cancellation", async () => {
  await openCheckout();
  expect(options).toMatchObject({ order_id: "order_test", amount: 100, currency: "INR", key: "rzp_test_fixture" });
  options.modal.ondismiss();
  await screen.findByText("Checkout closed. Payment has not been confirmed.");
  expect((screen.getByRole("button", { name: "Pay with Razorpay" }) as HTMLButtonElement).disabled).toBe(false);
});

it("shows payment.failed errors", async () => {
  await openCheckout();
  failure({ error: { description: "Bank declined payment" } });
  await screen.findByRole("alert");
  expect(screen.getByText("Bank declined payment")).toBeTruthy();
});

it("sends all callback fields for verification and offers retry without another charge", async () => {
  await openCheckout();
  fetchMock.mockResolvedValueOnce({ ok: false, json: async () => ({ error: "Provider unavailable" }) });
  const result = { razorpay_order_id: "order_test", razorpay_payment_id: "pay_test", razorpay_signature: "test-signature" };
  options.handler(result);
  await screen.findByRole("button", { name: "Check payment status" });
  expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/verify-payment");
  expect(JSON.parse(fetchMock.mock.calls[1]?.[1].body)).toEqual(result);
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ paid: true }) });
  fireEvent.click(screen.getByRole("button", { name: "Check payment status" }));
  await screen.findByText("Payment confirmed. Reference: pay_test");
  expect(fetchMock.mock.calls.filter(([url]) => url === "/api/create-order")).toHaveLength(1);
});

it("rejects invalid amounts before calling the API", async () => {
  render(<RazorpayCheckout testMode configured />);
  fireEvent.click(screen.getByText("Load checkout script"));
  fireEvent.change(screen.getByLabelText("Amount (INR)"), { target: { value: "0.99" } });
  fireEvent.click(screen.getByRole("button", { name: "Pay with Razorpay" }));
  await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  expect(fetchMock).not.toHaveBeenCalled();
});
