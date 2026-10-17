/**
 * The welcome after a payment: it lists what the plan unlocked from the
 * catalog, and it shows once per plan, only for a fresh Razorpay payment.
 */
import * as React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/chart",
  useSearchParams: () => new URLSearchParams(),
}));

import { SNAPSHOT } from "@/lib/billing/catalog";
import { fixtureMe } from "@/lib/billing/fixtures";
import { markWelcomed, unwelcomedPlan, WelcomePanel } from "@/components/billing/WelcomeDialog";
import type { BillingMe } from "@/lib/billing/types";

const cat = { ...SNAPSHOT, checkout: true };
const NOW = Math.floor(Date.now() / 1000);
const fresh = (base: BillingMe, start = NOW - 60): BillingMe =>
  ({ ...base, subscription: base.subscription && { ...base.subscription, period_start: start } });

// Node 25 ships its own localStorage that shadows jsdom's; give each test a plain one.
beforeEach(() => {
  const m = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, String(v)),
    removeItem: (k: string) => void m.delete(k),
    clear: () => m.clear(),
  });
});

describe("welcome panel", () => {
  it("names the plan and lists its catalog benefits", () => {
    const onClose = vi.fn();
    render(<WelcomePanel plan="pro" catalog={cat} me={fixtureMe("active", cat, NOW)} onClose={onClose} onBilling={vi.fn()} />);
    expect(screen.getByText("Payment successful")).toBeInTheDocument();
    expect(screen.getByText("Welcome to Pivot Pro")).toBeInTheDocument();
    expect(screen.getByText("200 AI credits a month")).toBeInTheDocument();
    expect(screen.getByText(/Renews on/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start using Pro" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("Pro+ shows its own benefits", () => {
    render(<WelcomePanel plan="pro_plus" catalog={cat} me={fixtureMe("pro_plus", cat, NOW)} onClose={vi.fn()} onBilling={vi.fn()} />);
    expect(screen.getByText("Welcome to Pivot Pro+")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").length).toBeGreaterThan(2);
  });
});

describe("when the welcome shows", () => {
  it("a fresh Razorpay payment is welcomed once", () => {
    const me = fresh(fixtureMe("active", cat, NOW));
    expect(unwelcomedPlan(me)).toBe("pro");
    markWelcomed("pro");
    expect(unwelcomedPlan(me)).toBeNull();
  });

  it("not for a subscription that started days ago (a renewal or an old payer)", () => {
    expect(unwelcomedPlan(fixtureMe("active", cat, NOW))).toBeNull();
  });

  it("not for free, unpaid, failed or granted plans", () => {
    for (const f of ["free", "incomplete", "past_due", "comp"] as const) {
      expect(unwelcomedPlan(fresh(fixtureMe(f, cat, NOW)))).toBeNull();
    }
  });

  it("an upgrade to Pro+ is welcomed even after Pro was", () => {
    markWelcomed("pro");
    expect(unwelcomedPlan(fresh(fixtureMe("pro_plus", cat, NOW)))).toBe("pro_plus");
  });
});
