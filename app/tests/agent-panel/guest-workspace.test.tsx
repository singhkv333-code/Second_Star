/**
 * Signed out, the app's root is the open workspace: the chart with BTC and
 * the Screener, sign-up in the header, and the account asked for only at the
 * step that needs one.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppShell } from "@/components/AppShell";
import * as api from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(),
    back: vi.fn(), forward: vi.fn(), refresh: vi.fn(),
  }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

beforeEach(() => {
  vi.restoreAllMocks();
  const store = new Map<string, string>();       // no session: a guest
  const ls = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  };
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: ls });
  Object.defineProperty(window, "localStorage", { configurable: true, value: ls });
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(
    new Response("{}", { status: 404, headers: { "Content-Type": "application/json" } }))));
  window.history.replaceState(null, "", "/#home");
  vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({
    data: { total_value: 0, invested_value: 0, total_pnl: 0, total_pnl_pct: 0, day_pnl: 0, num_holdings: 0 },
  });
});

describe("the guest workspace", () => {
  it("opens on the chart with BTC and the Screener, whatever the hash said", async () => {
    render(<AppShell />);
    await waitFor(() => expect(window.location.hash).toBe("#chart"));
    const frame = document.querySelector<HTMLIFrameElement>('iframe[title="Chart"]');
    expect(frame?.getAttribute("src")).toContain("symbol=BTCUSDT");
    expect(frame?.getAttribute("src")).toContain("panel=screener");
    expect(screen.getByTestId("guest-signup")).toBeInTheDocument();
    expect(screen.queryByTestId("account-menu-trigger")).toBeNull();
  });

  it("asks for an account instead of opening another tab", async () => {
    const seen: unknown[] = [];
    window.addEventListener("pivot:paywall", (e) => seen.push((e as CustomEvent).detail));
    render(<AppShell />);
    await waitFor(() => expect(window.location.hash).toBe("#chart"));
    const portfolio = screen.getAllByRole("button").find((b) => /portfolio/i.test(b.getAttribute("aria-label") ?? b.textContent ?? ""));
    expect(portfolio).toBeTruthy();
    fireEvent.click(portfolio!);
    expect(seen).toContainEqual(expect.objectContaining({ kind: "signin", plan: "anonymous", feature: "tab:portfolio" }));
    expect(window.location.hash).toBe("#chart");
    expect(api.getPortfolioSummary).not.toHaveBeenCalled();
  });
});
