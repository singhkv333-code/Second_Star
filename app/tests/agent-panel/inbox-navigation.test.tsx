import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppShell } from "@/components/AppShell";
import * as api from "@/lib/api";

vi.mock("@paper-design/shaders-react", () => ({ MeshGradient: () => null }));

vi.mock("@/lib/launch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/launch")>();
  return { ...actual, tabEnabled: (tab: string) => tab === "plays" || tab === "inbox" || actual.tabEnabled(tab) };
});

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
  // A signed-in shell: without a session AppShell opens the guest workspace.
  // Node's own localStorage shadows jsdom's, so an in-memory one carries it.
  const store = new Map<string, string>([["charto:auth:token", "tok"]]);
  const ls = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  };
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: ls });
  Object.defineProperty(window, "localStorage", { configurable: true, value: ls });
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(
    new Response(JSON.stringify({ detail: "not mocked" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    }),
  )));
  if (typeof window !== "undefined") window.history.replaceState(null, "", "#");
  vi.spyOn(api, "listWorkflows").mockResolvedValue({ data: { items: [], next_cursor: null } });
  vi.spyOn(api, "listRuns").mockResolvedValue({ data: { items: [], next_cursor: null } });
  vi.spyOn(api, "getScheduledRuns").mockResolvedValue({ data: { items: [] } });
  vi.spyOn(api, "getPortfolioSummary").mockResolvedValue({
    data: { total_value: 0, invested_value: 0, total_pnl: 0, total_pnl_pct: 0, day_pnl: 0, num_holdings: 0 },
  });
  vi.spyOn(api, "getPortfolioHoldings").mockResolvedValue({ data: [] });
  vi.spyOn(api, "getMarketIndices").mockResolvedValue({ data: { items: [] } });
  vi.spyOn(api, "getMe").mockResolvedValue({ data: { id: "u1", email: "d@e.com", full_name: "Demo" } });
  vi.spyOn(api, "listConversations").mockResolvedValue({ data: { items: [], next_cursor: null } });
  vi.spyOn(api, "getAccountMode").mockResolvedValue({ data: { mode: "paper" } });
});


describe("Inbox navigation", () => {
  it("opens Inbox independently and preserves the mounted Execution frame", async () => {
    render(<AppShell />);
    fireEvent.click(screen.getByTestId("nav-plays"));
    const execution = await screen.findByTitle("Pivot execution") as HTMLIFrameElement;
    execution.contentWindow!.location.hash = "#/strategies";
    fireEvent.click(screen.getByTestId("inbox-bell"));
    const inbox = await screen.findByTitle("Pivot inbox") as HTMLIFrameElement;
    expect(window.location.hash).toBe("#inbox");
    expect(inbox).toHaveAttribute("src", "/chart-app/plays.html#/inbox");
    expect(execution.parentElement).toHaveClass("hidden");
    expect(execution.contentWindow!.location.hash).toBe("#/strategies");
    fireEvent.click(screen.getByTestId("inbox-bell"));
    expect(screen.getByTitle("Pivot execution")).toBe(execution);
    expect(execution.parentElement).not.toHaveClass("hidden");
    expect(inbox.parentElement).toHaveClass("hidden");
    expect(execution.contentWindow!.location.hash).toBe("#/strategies");
    expect(window.location.hash).toBe("#plays");
    fireEvent.click(screen.getByTestId("inbox-bell"));
    fireEvent.click(screen.getByTestId("nav-plays"));
    expect(execution.parentElement).not.toHaveClass("hidden");
    expect(execution.contentWindow!.location.hash).toBe("#/strategies");
  });
  it("toggles Inbox back to the original page", async () => {
    render(<AppShell />);
    fireEvent.click(screen.getByTestId("inbox-bell"));
    expect(await screen.findByTitle("Pivot inbox")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("inbox-bell"));
    expect(window.location.hash).toBe("#home");
    expect(screen.getByTitle("Pivot inbox").parentElement).toHaveClass("hidden");
  });
  it("opens Inbox first without mounting Execution, including on a direct link", async () => {
    window.history.replaceState(null, "", "#inbox");
    render(<AppShell />);
    expect(await screen.findByTitle("Pivot inbox")).toHaveAttribute("src", "/chart-app/plays.html#/inbox");
    expect(screen.queryByTitle("Pivot execution")).toBeNull();
    fireEvent.click(screen.getByTestId("nav-plays"));
    expect(await screen.findByTitle("Pivot execution")).toHaveAttribute("src", "/chart-app/plays.html");
  });
});
