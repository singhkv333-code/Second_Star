/**
 * Tests for ChatDemo — Phase 1 wired version.
 * ChatDemo now calls POST /chat (legacy router), not proposeWorkflow.
 * We mock global fetch to control backend responses.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ChatDemo } from "@/components/chat/ChatDemo";
import * as api from "@/lib/api";

const MOCK_CHAT_RESPONSE_DRAFT = {
  response: "Here is a workflow for you.",
  tools_called: ["propose_workflow"],
  raw_data: {
    _render_hint: "workflow_draft_card",
    name: "RELIANCE 3:55 PM buy",
    description: "Buy RELIANCE every weekday",
    steps: [
      { step_type: "trigger.schedule", label: "Every weekday at 3:55 PM IST", config: {} },
      { step_type: "fetch.portfolio", label: "Get portfolio", config: {} },
      { step_type: "condition.numeric", label: "Buying power > ₹50k", config: {} },
      { step_type: "action.place_order", label: "Buy 10 RELIANCE", config: {} },
      { step_type: "notify.message", label: "Email confirmation", config: {} },
    ],
    rationale: "Canonical demo workflow.",
    warnings: [],
  },
};

const MOCK_CHAT_RESPONSE_TEXT = {
  response: "I can help you with that.",
  raw_data: null,
};

const MOCK_CHAT_RESPONSE_FINANCIAL_BACKTEST = {
  response: "Backtested `pe_ratio < 15` from 2020-01-01 to 2024-12-31, Q rebalance.",
  raw_data: {
    _render_hint: "financial_backtest_chart",
    expression: "pe_ratio < 15",
    start: "2020-01-01",
    end: "2024-12-31",
    rebalance: "Q",
    metrics: {
      cagr_pct: 12.0,
      sharpe: 0.85,
      max_drawdown_pct: 22.0,
      hit_rate_pct: 55,
      total_return_pct: 75.5,
    },
    equity_curve: [
      { date: "2020-01-01", value: 100000 },
      { date: "2024-12-31", value: 175500 },
    ],
    benchmark_curve: [
      { date: "2020-01-01", value: 100000 },
      { date: "2024-12-31", value: 150000 },
    ],
    rebalances: [],
    n_trades: 22,
    warnings: [],
  },
};

const MOCK_CHAT_RESPONSE_LOGIC_CARD = {
  response: "Here's the order I'd register.",
  tools_called: ["place_market_order"],
  logiccard: {
    type: "market_order",
    action: "BUY",
    symbol: "RELIANCE",
    details: [
      { label: "Quantity", value: "10" },
      { label: "Order Type", value: "MARKET" },
    ],
    explanation: "Buy 10 RELIANCE immediately at ~₹2,500.",
    disclaimer: "This is automation of your instructions, not financial advice.",
    requires_confirmation: true,
    register_payload: {
      symbol: "RELIANCE", exchange: "NSE",
      transaction_type: "BUY", order_type: "MARKET",
      quantity: 10, price: 2500, product: "CNC",
    },
  },
  raw_data: { _render_hint: "logic_card" },
};

function mockFetch(body: unknown, status = 200): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    }),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ChatDemo", () => {
  it("renders textarea and send button", () => {
    render(<ChatDemo onOpenEditor={vi.fn()} />);
    expect(screen.getByTestId("chat-demo")).toBeInTheDocument();
    expect(screen.getByTestId("chat-textarea")).toBeInTheDocument();
    expect(screen.getByTestId("chat-submit-btn")).toBeInTheDocument();
  });

  it("send button is disabled when textarea is empty", () => {
    render(<ChatDemo onOpenEditor={vi.fn()} />);
    expect(screen.getByTestId("chat-submit-btn")).toBeDisabled();
  });

  it("send button enables when user types", () => {
    render(<ChatDemo onOpenEditor={vi.fn()} />);
    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Buy RELIANCE" },
    });
    expect(screen.getByTestId("chat-submit-btn")).not.toBeDisabled();
  });

  it("shows intro card before first message", () => {
    render(<ChatDemo onOpenEditor={vi.fn()} />);
    expect(screen.getByText(/Describe your strategy/i)).toBeInTheDocument();
    expect(screen.getByTestId("example-prompt-btn")).toBeInTheDocument();
  });

  it("clicking example prompt fills the textarea", () => {
    render(<ChatDemo onOpenEditor={vi.fn()} />);
    fireEvent.click(screen.getByTestId("example-prompt-btn"));
    const textarea = screen.getByTestId("chat-textarea") as HTMLTextAreaElement;
    expect(textarea.value).toContain("RELIANCE");
  });

  it("submitting calls POST /chat and shows draft card on draft response", async () => {
    mockFetch(MOCK_CHAT_RESPONSE_DRAFT);
    render(<ChatDemo onOpenEditor={vi.fn()} />);

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Buy RELIANCE every weekday" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("workflow-draft-card")).toBeInTheDocument();
    });
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/chat"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows assistant text bubble for regular text responses", async () => {
    mockFetch(MOCK_CHAT_RESPONSE_TEXT);
    render(<ChatDemo onOpenEditor={vi.fn()} />);

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => {
      expect(screen.getByText("I can help you with that.")).toBeInTheDocument();
    });
  });

  it("shows error message on fetch failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("Network error")),
    );
    render(<ChatDemo onOpenEditor={vi.fn()} />);

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "something" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("chat-error")).toBeInTheDocument();
      expect(screen.getByText(/Network error/i)).toBeInTheDocument();
    });
  });

  it("shows loading skeleton while request is in flight", async () => {
    let resolve: (v: unknown) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(
        new Promise((res) => {
          resolve = res;
        }),
      ),
    );

    render(<ChatDemo onOpenEditor={vi.fn()} />);
    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Buy RELIANCE" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    expect(screen.getByTestId("chat-loading")).toBeInTheDocument();
    resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(MOCK_CHAT_RESPONSE_DRAFT),
    });
    await waitFor(() =>
      expect(screen.queryByTestId("chat-loading")).not.toBeInTheDocument(),
    );
  });

  it("calls onOpenEditor with Workflow when 'Open in editor' is clicked", async () => {
    mockFetch(MOCK_CHAT_RESPONSE_DRAFT);
    const onOpenEditor = vi.fn();
    render(<ChatDemo onOpenEditor={onOpenEditor} />);

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Buy RELIANCE every weekday" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("open-in-editor-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("open-in-editor-button"));
    expect(onOpenEditor).toHaveBeenCalledTimes(1);
    const call = onOpenEditor.mock.calls[0];
    const arg = (call?.[0] ?? {}) as { name: string; status: string; steps: unknown[] };
    expect(arg.name).toBe("RELIANCE 3:55 PM buy");
    expect(arg.status).toBe("draft");
    expect(arg.steps).toHaveLength(5);
  });

  it("renders FinancialBacktestCard when raw_data._render_hint === 'financial_backtest_chart'", async () => {
    mockFetch(MOCK_CHAT_RESPONSE_FINANCIAL_BACKTEST);
    render(<ChatDemo onOpenEditor={vi.fn()} />);

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "backtest pe_ratio < 15 from 2020 to 2024" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("financial-backtest-card")).toBeInTheDocument();
    });
    expect(screen.getByText("pe_ratio < 15")).toBeInTheDocument();
    expect(screen.getByText(/Backtested/)).toBeInTheDocument();
  });

  it("renders LogicCardChip when raw_data._render_hint === 'logic_card'", async () => {
    mockFetch(MOCK_CHAT_RESPONSE_LOGIC_CARD);
    render(<ChatDemo onOpenEditor={vi.fn()} />);

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Buy 10 RELIANCE at market" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("logic-card-chip")).toBeInTheDocument();
    });
    // Intro bubble + the card both render
    expect(screen.getByText(/Here's the order I'd register/)).toBeInTheDocument();
    expect(screen.getByText("RELIANCE")).toBeInTheDocument();
  });

  it.each([
    // Long workflow description that contains "if price" — must NOT
    // be hijacked into a snapshot card with "IF" as the ticker.
    // (See screenshot: "no quote available for IF.NSE" bug.)
    "Build an agent that buys reliance every monday opening and sells if price decreases and holds if price increases. 3 shares.",
    "Every weekday at 3:55, buy 10 RELIANCE if my buying power is over 50000",
    "Set up a SIP that invests 5000 monthly in INFY",
    "What can you do?",
    "Hello",
  ])("phrase '%s' is NOT mistaken for a ticker shortcut", async (input) => {
    mockFetch(MOCK_CHAT_RESPONSE_TEXT);
    render(<ChatDemo onOpenEditor={vi.fn()} />);
    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: input },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));
    // Wait for the chat call to resolve, then assert no snapshot card.
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("stock-snapshot-loading")).not.toBeInTheDocument();
    expect(screen.queryByTestId("stock-snapshot-card")).not.toBeInTheDocument();
  });

  it.each([
    "RELIANCE",
    "$RELIANCE",
    "show me reliance",
    "what about TCS?",
    "INFY snapshot",
    "price of HDFCBANK",
  ])("phrase '%s' triggers a stock snapshot card without hitting /chat", async (input) => {
    // Stub the snapshot card's data fetches so it stays in loading
    // state without throwing. The chat shortcut is what we're testing.
    vi.spyOn(api, "getStockQuote").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "getSparkline").mockReturnValue(new Promise(() => {}));
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve("{}"),
    });
    vi.stubGlobal("fetch", fetchSpy);

    render(<ChatDemo onOpenEditor={vi.fn()} />);

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: input },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() =>
      expect(
        screen.getByTestId("stock-snapshot-loading"),
      ).toBeInTheDocument(),
    );
    // /chat should never have been called for this shortcut.
    expect(
      fetchSpy.mock.calls.some((c) =>
        String(c[0]).endsWith("/chat"),
      ),
    ).toBe(false);
  });

  it("Cmd+Enter submits the form", async () => {
    mockFetch(MOCK_CHAT_RESPONSE_TEXT);
    render(<ChatDemo onOpenEditor={vi.fn()} />);

    const textarea = screen.getByTestId("chat-textarea");
    fireEvent.change(textarea, { target: { value: "Buy RELIANCE" } });
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true });

    await waitFor(() => {
      expect(fetch).toHaveBeenCalled();
    });
  });
});
