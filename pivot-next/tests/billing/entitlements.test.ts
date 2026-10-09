/**
 * The plan logic the UI draws from. Where a function mirrors Python in
 * charto/data/entitlements.py, the expectations here are the Python's.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { SNAPSHOT, fromRawCatalog } from "@/lib/billing/catalog";
import {
  allows,
  annualSaving,
  checkoutBlock,
  checkoutHref,
  effectivePlan,
  losses,
  meter,
  overAfterDowngrade,
  overPlan,
  safeReturnPath,
  subscriptionView,
  triggerFromRefusal,
  upgradeFor,
} from "@/lib/billing/entitlements";
import { fixtureMe, type Fixture } from "@/lib/billing/fixtures";
import { clean } from "@/lib/billing/analytics";
import { featureLine } from "@/lib/billing/presentation";
import type { Subscription } from "@/lib/billing/types";

const NOW = 1_800_000_000;
const DAY = 86400;
const cat = { ...SNAPSHOT, checkout: true };

describe("catalog", () => {
  it("is a byte-for-byte copy of charto's plans_catalog.json", () => {
    const src = path.resolve(__dirname, "../../../charto/data/plans_catalog.json");
    if (!fs.existsSync(src)) return; // pivot-next built on its own (Docker)
    const snap = path.resolve(__dirname, "../../lib/billing/catalog.snapshot.json");
    expect(JSON.parse(fs.readFileSync(snap, "utf8"))).toEqual(JSON.parse(fs.readFileSync(src, "utf8")));
  });

  it("reads prices and limits from the catalog, not from code", () => {
    const pro = SNAPSHOT.plans.find((p) => p.id === "pro")!;
    expect(SNAPSHOT.plans.map((p) => p.id)).toEqual(["free", "pro", "pro_plus"]);
    expect(pro.prices.monthly?.amount).toBe(49900);
    expect(pro.prices.annual?.amount).toBe(538800);
    expect(SNAPSHOT.plans[0]!.features["ai.credits"]).toBe(15);
    expect(SNAPSHOT.trial_days).toBeNull();
  });

  it("marks a trial only when the catalog gives whole days", () => {
    const raw = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../lib/billing/catalog.snapshot.json"), "utf8"));
    expect(fromRawCatalog({ ...raw, trial: { days: 7 } }).trial_days).toBe(7);
    expect(fromRawCatalog({ ...raw, trial: { days: 0 } }).trial_days).toBeNull();
  });

  it("annual saving is 12 months minus the annual price", () => {
    expect(annualSaving(cat, "pro")).toBe(49900 * 12 - 538800);
    expect(annualSaving(cat, "free")).toBe(0);
  });
});

describe("effectivePlan mirrors entitlements._effective", () => {
  const sub = (s: Partial<Subscription>): Subscription => ({
    plan: "pro", cycle: "monthly", status: "active", period_start: NOW - DAY, period_end: NOW + DAY,
    cancel_at_period_end: false, grace_until: null, provider: "razorpay", ...s,
  });
  it.each([
    ["no row", null, "free"],
    ["active in period", sub({}), "pro"],
    ["active, renewal late but in grace", sub({ period_end: NOW - 10, grace_until: NOW + DAY }), "pro"],
    ["active, period over, no grace", sub({ period_end: NOW - 10 }), "free"],
    ["past_due in grace", sub({ status: "past_due", grace_until: NOW + DAY }), "pro"],
    ["past_due after grace", sub({ status: "past_due", grace_until: NOW - 1 }), "free"],
    ["cancelled, paid through", sub({ status: "cancelled" }), "pro"],
    ["cancelled, over", sub({ status: "cancelled", period_end: NOW - 1 }), "free"],
    ["created (never paid)", sub({ status: "created" }), "free"],
    ["expired", sub({ status: "expired" }), "free"],
  ] as const)("%s", (_n, s, want) => {
    expect(effectivePlan(s as Subscription | null, NOW)).toBe(want);
  });
});

describe("subscriptionView: every fixture lands in its state", () => {
  it.each([
    ["anonymous", "anonymous"],
    ["free", "free"],
    ["incomplete", "incomplete"],
    ["trialing", "trialing"],
    ["active", "active"],
    ["scheduled", "active"],
    ["canceling", "canceling"],
    ["past_due", "past_due"],
    ["expired", "expired"],
    ["comp", "comp"],
  ] as [Fixture, string][])("%s → %s", (f, state) => {
    const now = Math.floor(Date.now() / 1000);
    expect(subscriptionView(fixtureMe(f, cat, now), now).state).toBe(state);
  });

  it("a scheduled change is surfaced with its date", () => {
    const v = subscriptionView(fixtureMe("scheduled", cat));
    expect(v.pending?.plan).toBe("pro");
  });
});

describe("allows() never pre-blocks when the server would not", () => {
  it("allows everything while the plan is unknown or the paywall is off", () => {
    expect(allows(null, "chart.panes", 99)).toBe(true);
    const off = { ...fixtureMe("free", cat), paywall_enabled: false };
    expect(allows(off, "chart.panes", 99)).toBe(true);
  });
  it("applies the cap when the paywall is on", () => {
    const m = fixtureMe("free", cat);
    expect(allows(m, "chart.panes", 4)).toBe(true);
    expect(allows(m, "chart.panes", 5)).toBe(false);
    expect(allows(m, "watchlists", 10_000)).toBe(true); // null = unlimited
  });
  it("lock markers follow the catalog regardless of the switch", () => {
    expect(overPlan(cat, "chart.panes", "free", 6)).toBe(true);
    expect(overPlan(cat, "chart.panes", "pro", 8)).toBe(false);
    expect(overPlan(cat, "chart.indicators", "pro_plus", 500)).toBe(false);
  });
});

describe("upgradeFor mirrors entitlements.upgrade_for", () => {
  it("names the cheapest plan that would have allowed it", () => {
    expect(upgradeFor(cat, "ai.credits", "free", 15)).toBe("pro");
    expect(upgradeFor(cat, "ai.credits", "pro", 200)).toBe("pro_plus");
    expect(upgradeFor(cat, "chart.parallel", "free", 25)).toBe("pro_plus");
    expect(upgradeFor(cat, "chart.panes", "pro", 8)).toBeNull(); // 8 is the ceiling
    expect(upgradeFor(cat, "chart.custom_timeframes", "free")).toBe("pro");
  });
});

describe("refusals become the right paywall", () => {
  it("a signed-out refusal asks for an account, not money", () => {
    const t = triggerFromRefusal({ error: "Sign in to get more AI credits.", code: "quota_exhausted", feature: "ai.credits", plan: "anonymous", limit: 3, used: 3, upgrade_to: "free" });
    expect(t.kind).toBe("signin");
    expect(t.message).toBe("Sign in to get more AI credits.");
  });
  it("keeps the server's numbers and reset date", () => {
    const t = triggerFromRefusal({ error: "x", code: "quota_exhausted", feature: "ai.credits", plan: "free", limit: 15, used: 15, upgrade_to: "pro", resets_at: NOW });
    expect(t).toMatchObject({ kind: "quota_exhausted", limit: 15, used: 15, upgradeTo: "pro", resetsAt: NOW });
  });
});

describe("downgrades and cancellation", () => {
  it("lists what gets smaller, never unbuilt features", () => {
    const keys = losses(cat, "pro", "free").map((l) => l.key);
    expect(keys).toContain("ai.credits");
    expect(keys).toContain("alerts.price");
    expect(keys).toContain("chart.history_bars"); // live, only its Pro number is unsettled
    expect(keys).not.toContain("alerts.watchlist"); // not built
    expect(keys).not.toContain("ai.summaries"); // not built
  });
  it("names alerts that would pause", () => {
    const over = overAfterDowngrade(cat, fixtureMe("scheduled", cat), "pro");
    expect(over.find((o) => o.key === "alerts.price")).toMatchObject({ used: 520, limit: 400 });
  });
});

describe("checkout guards (the 401 / 409 / 503 billing.api_checkout answers)", () => {
  it.each([
    ["anonymous", "pro", "monthly", "signin"],
    ["active", "pro", "monthly", "same_plan"],
    ["active", "pro_plus", "monthly", "use_change"],
    ["canceling", "pro", "annual", "use_change"],
    ["free", "pro", "annual", null],
    ["expired", "pro", "monthly", null],
    ["incomplete", "pro", "annual", null],
  ] as [Fixture, "pro" | "pro_plus", "monthly" | "annual", string | null][])("%s buying %s %s → %s", (f, plan, cycle, want) => {
    const b = checkoutBlock(cat, fixtureMe(f, cat), plan, cycle);
    expect(b?.reason ?? null).toBe(want);
  });
  it("says so when payments are not configured", () => {
    expect(checkoutBlock({ ...cat, checkout: false }, fixtureMe("free", cat), "pro", "annual")?.reason).toBe("unavailable");
  });
});

describe("returning the user to where they were", () => {
  it.each([
    ["/chart?symbol=INFY", "/chart?symbol=INFY"],
    ["https://evil.example", "/"],
    ["//evil.example", "/"],
    ["/\\evil.example", "/"],
    ["/checkout?plan=pro", "/"],
    ["/pricing", "/"],
    [null, "/"],
  ])("%s → %s", (input, want) => {
    expect(safeReturnPath(input)).toBe(want);
  });
  it("checkout links carry plan, cycle and a safe return", () => {
    const href = checkoutHref("pro", "annual", { returnTo: "//x.io", feature: "chart.panes" });
    const q = new URLSearchParams(href.split("?")[1]);
    expect(q.get("plan")).toBe("pro");
    expect(q.get("return")).toBe("/");
    expect(q.get("feature")).toBe("chart.panes");
  });
});

describe("display", () => {
  it("meters read low at 80% and out at the limit", () => {
    expect(meter(5, 15).tone).toBe("ok");
    expect(meter(12, 15).tone).toBe("low");
    expect(meter(15, 15).tone).toBe("out");
    expect(meter(9, null).tone).toBe("ok");
  });
  it("feature lines keep acronyms", () => {
    expect(featureLine("ai.credits", cat.features["ai.credits"]!, 200)).toBe("200 AI credits a month");
    expect(featureLine("alerts.expiry_days", cat.features["alerts.expiry_days"]!, null)).toBe("Alerts never expire");
  });
});

describe("analytics carries nothing personal", () => {
  it("drops unknown keys and free text", () => {
    const out = clean({ plan: "pro", email: "a@b.c", user_id: 7, surface: "x".repeat(60), feature: "ai.credits" });
    expect(out).toEqual({ plan: "pro", feature: "ai.credits" });
  });
});
