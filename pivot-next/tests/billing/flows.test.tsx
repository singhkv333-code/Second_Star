/**
 * The flows a person walks through: a paywall that says the right thing and
 * offers the right way forward, a cancellation that is clear and actually
 * calls the server, and a checkout that refuses to sell a second subscription.
 */
import * as React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/chart",
  useSearchParams: () => new URLSearchParams(),
}));

import { SNAPSHOT } from "@/lib/billing/catalog";
import { fixtureMe, type Fixture } from "@/lib/billing/fixtures";
import { BillingProvider, showPaywall } from "@/components/billing/BillingProvider";
import { PaywallPanel } from "@/components/billing/PaywallDialog";
import { CancelPanel } from "@/components/billing/CancelFlow";
import { CheckoutView } from "@/components/billing/CheckoutView";
import { PlanCards, cardAction } from "@/components/billing/pricing";
import type { PaywallTrigger } from "@/lib/billing/entitlements";

const cat = { ...SNAPSHOT, checkout: true };
const NOW = Math.floor(Date.now() / 1000);
const me = (f: Fixture) => fixtureMe(f, cat, NOW);

function panel(f: Fixture, t: PaywallTrigger, extra: Partial<React.ComponentProps<typeof PaywallPanel>> = {}) {
  const onCheckout = vi.fn();
  const onClose = vi.fn();
  render(<PaywallPanel trigger={t} catalog={cat} me={me(f)} onClose={onClose} onCheckout={onCheckout} demo {...extra} />);
  return { onCheckout, onClose };
}

beforeEach(() => push.mockReset());

describe("usage-limit paywall", () => {
  const t: PaywallTrigger = {
    kind: "quota_exhausted", feature: "ai.credits", plan: "free", limit: 15, used: 15,
    resetsAt: NOW + 5 * 86400, upgradeTo: "pro", message: "You have used all 15 AI credits in your Free plan for this period.",
  };

  it("shows the server's sentence, the meter and every way forward", () => {
    panel("free_out", t);
    expect(screen.getByText(/You have used all 15 AI credits/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "15");
    expect(screen.getByText("Wait for the reset")).toBeInTheDocument();
    expect(screen.getByText("Upgrade")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not now" })).toBeInTheDocument();
  });

  it("offers the plan the server named, with the catalog's number, and checks out the chosen cycle", () => {
    const { onCheckout } = panel("free_out", t);
    expect(screen.getByText("200 AI credits a month")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Monthly" }));
    fireEvent.click(screen.getByRole("button", { name: /Continue to checkout/ }));
    expect(onCheckout).toHaveBeenCalledWith("pro", "monthly");
  });

  it("'Not now' dismisses without checking out", () => {
    const { onClose, onCheckout } = panel("free_out", t);
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(onClose).toHaveBeenCalledWith("dismiss");
    expect(onCheckout).not.toHaveBeenCalled();
  });

  it("a subscriber upgrades in place instead of buying again", () => {
    const { onCheckout } = panel("active", { ...t, plan: "pro", limit: 200, used: 200, upgradeTo: "pro_plus" });
    fireEvent.click(screen.getByRole("button", { name: /Upgrade to Pro\+/ }));
    expect(onCheckout).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Upgrade now" })).toBeInTheDocument();
  });

  it("on the top plan, invents no upsell", () => {
    panel("pro_plus", { ...t, plan: "pro_plus", limit: 500, used: 500, upgradeTo: null });
    expect(screen.queryByRole("button", { name: /checkout/i })).toBeNull();
    expect(screen.getByText(/highest limit available/)).toBeInTheDocument();
  });
});

describe("other paywall kinds", () => {
  it("signed out: an account, not a payment", () => {
    const onSignIn = vi.fn();
    panel("anonymous", { kind: "signin", feature: "ai.credits", plan: "anonymous", upgradeTo: "free" }, { onSignIn });
    expect(screen.queryByText(/checkout/i)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Create a free account" }));
    expect(onSignIn).toHaveBeenCalledWith("signup");
  });

  it("evicted tab: 'Use this tab' reclaims the slot", () => {
    const onReclaim = vi.fn();
    panel("free", { kind: "evicted", feature: "chart.parallel", plan: "free", limit: 10, used: 10, upgradeTo: "pro" }, { onReclaim });
    fireEvent.click(screen.getByRole("button", { name: "Use this tab" }));
    expect(onReclaim).toHaveBeenCalled();
  });

  it("any code can raise the paywall with a raw 402 body", async () => {
    render(
      <BillingProvider demo initial={{ catalog: cat, me: me("free") }}>
        <div />
      </BillingProvider>,
    );
    React.act(() =>
      showPaywall({ error: "Your Free plan allows 4 charts per tab.", code: "plan_limit", feature: "chart.panes", plan: "free", limit: 4, used: 4, upgrade_to: "pro" }),
    );
    expect(await screen.findByText("You are at 4 charts per tab")).toBeInTheDocument();
  });
});

describe("cancellation", () => {
  it("never needs more than review → confirm when no alternative exists, and calls the server", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, active_until: NOW, billing: me("canceling") }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const onDone = vi.fn();
    // Pro yearly has no cheaper paid plan and no yearly saving to offer
    render(<CancelPanel catalog={cat} me={me("active_annual")} onClose={vi.fn()} onDone={onDone} onAlternative={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Keep my plan" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue to cancel" }));
    fireEvent.click(screen.getByRole("button", { name: /Cancel subscription/ }));
    await waitFor(() => expect(screen.getByText("Your subscription is cancelled")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith("/billing/cancel", expect.objectContaining({ method: "POST" }));
    vi.unstubAllGlobals();
  });

  it("offers only alternatives that exist, and cancel stays one click away", () => {
    render(<CancelPanel catalog={cat} me={me("pro_plus")} onClose={vi.fn()} onDone={vi.fn()} onAlternative={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Continue to cancel" }));
    expect(screen.getByText("Switch to Pro instead")).toBeInTheDocument();
    expect(screen.getByText("Pay yearly and save")).toBeInTheDocument();
    expect(screen.queryByText(/pause/i)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Continue to cancel" }));
    expect(screen.getByRole("button", { name: /Cancel subscription/ })).toBeInTheDocument();
  });

  it("a failed cancel says so and changes nothing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: "The payment provider did not answer." }), { status: 502 })));
    render(<CancelPanel catalog={cat} me={me("active_annual")} onClose={vi.fn()} onDone={vi.fn()} onAlternative={vi.fn()} initialStep="confirm" />);
    fireEvent.click(screen.getByRole("button", { name: /Cancel subscription/ }));
    expect(await screen.findByText("The cancellation did not go through")).toBeInTheDocument();
    expect(screen.queryByText("Your subscription is cancelled")).toBeNull();
    vi.unstubAllGlobals();
  });
});

describe("checkout", () => {
  const renderCheckout = (f: Fixture, plan: "pro" | "pro_plus", cycle: "monthly" | "annual" = "annual", c = cat) =>
    render(
      <BillingProvider demo initial={{ catalog: c, me: me(f) }} mountDialog={false}>
        <CheckoutView plan={plan} initialCycle={cycle} returnTo="/chart" feature="chart.panes" />
      </BillingProvider>,
    );

  it("shows the true total and renewal terms before the button", () => {
    renderCheckout("free", "pro");
    expect(screen.getAllByText("₹5,388").length).toBeGreaterThan(0);
    expect(screen.getByText(/Renews automatically at ₹5,388 a year/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pay ₹5,388 securely/ })).toBeInTheDocument();
  });

  it("an existing subscriber is sent to change, never to a second purchase", () => {
    renderCheckout("active", "pro_plus");
    expect(screen.queryByRole("button", { name: /Pay / })).toBeNull();
    expect(screen.getByRole("button", { name: "Change to Pro+" })).toBeInTheDocument();
  });

  it("already on this exact plan: nothing to buy", () => {
    renderCheckout("active", "pro", "monthly");
    expect(screen.getByText("You are already on Pro")).toBeInTheDocument();
  });

  it("signed out: sign in first", () => {
    renderCheckout("anonymous", "pro");
    expect(screen.getByRole("button", { name: /Sign in to continue/ })).toBeInTheDocument();
  });

  it("without payment keys, says nothing can be charged", () => {
    renderCheckout("free", "pro", "annual", { ...cat, checkout: false });
    expect(screen.getByText("Payments are not switched on yet")).toBeInTheDocument();
  });

  it("success returns the user to the feature they wanted", async () => {
    renderCheckout("free", "pro");
    fireEvent.click(screen.getByRole("button", { name: /Pay ₹5,388 securely/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Back to where you were" }));
    expect(push).toHaveBeenCalledWith("/chart");
  });
});

describe("pricing cards follow the viewer", () => {
  it.each([
    ["anonymous", "free", "signup"],
    ["anonymous", "pro", "checkout"],
    ["free", "free", "current"],
    ["free", "pro", "checkout"],
    ["active", "pro", "current"],
    ["active", "pro_plus", "change"],
    ["active", "free", "cancel"],
    ["pro_plus", "pro", "change"],
    ["expired", "pro", "checkout"],
  ] as [Fixture, "free" | "pro" | "pro_plus", string][])("%s sees %s as %s", (f, plan, kind) => {
    expect(cardAction(cat, me(f), plan, "monthly").kind).toBe(kind);
  });

  it("renders a current-plan button as disabled", () => {
    render(<PlanCards catalog={cat} me={me("free")} cycle="annual" onAction={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Current plan" })).toBeDisabled();
  });
});
