/**
 * Codes at checkout: a discount changes what is charged and says for how
 * long; a free-time code replaces payment with a redeem; a bad code says why.
 */
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/checkout",
  useSearchParams: () => new URLSearchParams(),
}));

import { SNAPSHOT } from "@/lib/billing/catalog";
import { fixtureMe } from "@/lib/billing/fixtures";
import { billingApi } from "@/lib/billing/api";
import { BillingProvider } from "@/components/billing/BillingProvider";
import { CheckoutView } from "@/components/billing/CheckoutView";
import type { CodeInfo } from "@/lib/billing/types";

const cat = { ...SNAPSHOT, checkout: true };
const NOW = Math.floor(Date.now() / 1000);

const LAUNCH40: CodeInfo = {
  kind: "discount", code: "LAUNCH40", plan: "pro", plan_name: "Pro", cycles: "any", percent_off: 40,
  amount_off: null, discount_cycles: 3, valid_until: null, summary: "40% off Pro for 3 billing cycles",
  offers: [
    { plan: "pro", plan_name: "Pro", cycle: "monthly", was: 59900, amount: 35900, discount_cycles: 3 },
    { plan: "pro", plan_name: "Pro", cycle: "annual", was: 598800, amount: 359200, discount_cycles: 3 },
  ],
};
const FRIENDS30: CodeInfo = {
  kind: "access", code: "FRIENDS30", plan: "pro", plan_name: "Pro", days: 30, valid_until: null,
  summary: "Pro free for 30 days",
};

const renderCheckout = (initialCode?: string) =>
  render(
    <BillingProvider demo initial={{ catalog: cat, me: fixtureMe("free", cat, NOW) }} mountDialog={false}>
      <CheckoutView plan="pro" initialCycle="monthly" initialCode={initialCode} />
    </BillingProvider>,
  );

afterEach(() => vi.restoreAllMocks());

describe("codes at checkout", () => {
  it("a discount from the link lowers what is due and states how long it lasts", async () => {
    const check = vi.spyOn(billingApi, "checkCode").mockResolvedValue({ ok: true, status: 200, data: { coupon: LAUNCH40 } });
    renderCheckout("launch40");
    await waitFor(() => expect(screen.getByRole("button", { name: /Pay ₹359 securely/ })).toBeInTheDocument());
    expect(check).toHaveBeenCalledWith("LAUNCH40", "pro", "monthly");
    expect(screen.getByText("−₹240")).toBeInTheDocument();
    expect(screen.getByText(/for 3 billing cycles\. The subscription then ends, and you can renew at ₹599/)).toBeInTheDocument();
  });

  it("removing the code restores the catalog price", async () => {
    vi.spyOn(billingApi, "checkCode").mockResolvedValue({ ok: true, status: 200, data: { coupon: LAUNCH40 } });
    renderCheckout("LAUNCH40");
    await screen.findByRole("button", { name: /Pay ₹359 securely/ });
    fireEvent.click(screen.getByRole("button", { name: "Remove code" }));
    expect(screen.getByRole("button", { name: /Pay ₹599 securely/ })).toBeInTheDocument();
  });

  it("a free-time code redeems instead of paying", async () => {
    vi.spyOn(billingApi, "checkCode").mockResolvedValue({ ok: true, status: 200, data: { coupon: FRIENDS30 } });
    const redeem = vi.spyOn(billingApi, "redeem").mockResolvedValue({
      ok: true, status: 200,
      data: { redeemed: { ...FRIENDS30, until: NOW + 30 * 86400 }, billing: fixtureMe("comp", cat, NOW) },
    });
    const checkout = vi.spyOn(billingApi, "checkout");
    renderCheckout();
    fireEvent.click(screen.getByRole("button", { name: /Have a code/ }));
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "friends30" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    const go = await screen.findByRole("button", { name: "Start 30 days of Pro, free" });
    expect(screen.getByText("₹0")).toBeInTheDocument();
    fireEvent.click(go);
    await screen.findByText("Code redeemed");
    expect(redeem).toHaveBeenCalledWith("FRIENDS30");
    expect(checkout).not.toHaveBeenCalled();
  });

  it("a refused code says why and leaves the price alone", async () => {
    vi.spyOn(billingApi, "checkCode").mockResolvedValue({ ok: false, status: 409, error: "This code has been fully used.", code: "used_up" });
    renderCheckout("GONE");
    await screen.findByText("This code has been fully used.");
    expect(screen.getByRole("button", { name: /Pay ₹599 securely/ })).toBeInTheDocument();
  });
});
