// @vitest-environment node
import { expect, it, vi } from "vitest";
vi.mock("next/navigation", () => ({ redirect: (url: string) => { throw new Error(url); } }));
import CheckoutPage from "../app/checkout/page";

it("routes a selected plan and cycle to the authenticated app", async () => {
  await expect(CheckoutPage({ searchParams: Promise.resolve({ plan: "pro_plus", cycle: "monthly" }) }))
    .rejects.toThrow("https://app.pivotnow.in/checkout?plan=pro_plus&cycle=monthly");
});
it("sends an unselected or invalid arbitrary payment to plan selection", async () => {
  await expect(CheckoutPage({ searchParams: Promise.resolve({ plan: "malicious", amount: "1" }) }))
    .rejects.toThrow("https://app.pivotnow.in/pricing");
});
