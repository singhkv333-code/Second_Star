/**
 * Tests for ChatDemo — Phase 1 wired version.
 * ChatDemo now calls POST /chat (legacy router), not proposeWorkflow.
 * We mock global fetch to control backend responses.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ChatDemo } from "@/components/chat/ChatDemo";

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
