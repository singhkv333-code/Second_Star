import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/" }));
vi.mock("@/hooks/useCompanyLogos", () => ({ useCompanyLogos: () => ({}) }));
vi.mock("@/lib/authToken", () => ({ getAccessToken: async () => "t" }));

const script: { events: unknown[]; fail?: number } = { events: [] };
vi.mock("@/lib/assist", async () => {
  const actual = await vi.importActual<typeof import("@/lib/assist")>("@/lib/assist");
  return {
    ...actual,
    warmAssist: vi.fn(),
    streamAssist: async function* () {
      if (script.fail) throw new actual.AssistHttpError(script.fail);
      for (const ev of script.events) yield ev;
    },
  };
});

import { AssistantPanel } from "@/components/copilot/AssistantPanel";

const props = {
  page: "stock" as const, symbol: "INFY", contextLabel: "INFY",
  onOpenFullChat: vi.fn(), onClose: vi.fn(),
};

describe("AssistantPanel", () => {
  beforeEach(() => { sessionStorage.clear(); script.fail = undefined; });

  it("shows the lookups it ran and the answer", async () => {
    script.events = [
      { type: "start" },
      { type: "tool_start", name: "get_symbol_news", label: "Reading the news · INFY" },
      { type: "tool_done", name: "get_symbol_news", ok: true },
      { type: "delta", text: "**Two** headlines." },
      { type: "done", response: "**Two** headlines." },
    ];
    render(<AssistantPanel {...props} seed="Any news?" />);
    await screen.findByText("Two");
    expect(screen.getByText(/1 lookup/)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/1 lookup/));
    expect(screen.getByText("Reading the news · INFY")).toBeInTheDocument();
  });

  it("puts a failure in one sentence with a way to retry", async () => {
    script.events = [{ type: "start" },
      { type: "error", message: "Pivot couldn't reach its analysis service just now. Please try again in a moment." }];
    render(<AssistantPanel {...props} seed="Is it cheap?" />);
    await screen.findByText(/couldn't reach its analysis service/);
    expect(screen.getByText("Try again")).toBeInTheDocument();
  });

  it("never shows a status code when the request itself fails", async () => {
    script.fail = 503;
    render(<AssistantPanel {...props} seed="Is it cheap?" />);
    await waitFor(() => expect(screen.getByText(/couldn't answer that just now/)).toBeInTheDocument());
    expect(screen.queryByText(/503/)).toBeNull();
  });

  it("offers page-specific starting points", () => {
    render(<AssistantPanel {...props} />);
    expect(screen.getByText("Is INFY expensive right now?")).toBeInTheDocument();
  });
});
