/**
 * Tests for ChatDemo — #39 Day 6.
 * Covers textarea rendering, submit flow (success + error), loading state,
 * example prompt shortcut, and "Open in editor" handoff.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ChatDemo } from "@/components/chat/ChatDemo";
import * as api from "@/lib/api";

const MOCK_DRAFT = {
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
};

beforeEach(() => {
  vi.restoreAllMocks();
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

  it("submitting calls proposeWorkflow and shows draft card on success", async () => {
    vi.spyOn(api, "proposeWorkflow").mockResolvedValue({ data: MOCK_DRAFT });
    render(<ChatDemo onOpenEditor={vi.fn()} />);

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Buy RELIANCE" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("workflow-draft-card")).toBeInTheDocument();
    });
    expect(api.proposeWorkflow).toHaveBeenCalledWith("Buy RELIANCE");
  });

  it("shows error message on API failure", async () => {
    vi.spyOn(api, "proposeWorkflow").mockResolvedValue({
      error: { code: "validation_error", message: "Intent is too vague" },
    });
    render(<ChatDemo onOpenEditor={vi.fn()} />);

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "something" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("chat-error")).toBeInTheDocument();
      expect(screen.getByText("Intent is too vague")).toBeInTheDocument();
    });
  });

  it("shows loading skeleton while request is in flight", async () => {
    let resolve: (v: { data: typeof MOCK_DRAFT }) => void = () => {};
    vi.spyOn(api, "proposeWorkflow").mockReturnValue(
      new Promise((res) => { resolve = res; }),
    );

    render(<ChatDemo onOpenEditor={vi.fn()} />);
    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Buy RELIANCE" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    expect(screen.getByTestId("chat-loading")).toBeInTheDocument();
    resolve({ data: MOCK_DRAFT });
    await waitFor(() =>
      expect(screen.queryByTestId("chat-loading")).not.toBeInTheDocument(),
    );
  });

  it("calls onOpenEditor with Workflow when 'Open in editor' is clicked", async () => {
    vi.spyOn(api, "proposeWorkflow").mockResolvedValue({ data: MOCK_DRAFT });
    const onOpenEditor = vi.fn();
    render(<ChatDemo onOpenEditor={onOpenEditor} />);

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Buy RELIANCE" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("open-in-editor-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("open-in-editor-button"));
    expect(onOpenEditor).toHaveBeenCalledTimes(1);
    // Should receive a Workflow object (has name, steps, status)
    const call = onOpenEditor.mock.calls[0];
    const arg = (call?.[0] ?? {}) as { name: string; status: string; steps: unknown[] };
    expect(arg.name).toBe("RELIANCE 3:55 PM buy");
    expect(arg.status).toBe("draft");
    expect(arg.steps).toHaveLength(5);
  });

  it("Cmd+Enter submits the form", async () => {
    vi.spyOn(api, "proposeWorkflow").mockResolvedValue({ data: MOCK_DRAFT });
    render(<ChatDemo onOpenEditor={vi.fn()} />);

    const textarea = screen.getByTestId("chat-textarea");
    fireEvent.change(textarea, { target: { value: "Buy RELIANCE" } });
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true });

    await waitFor(() => {
      expect(api.proposeWorkflow).toHaveBeenCalledWith("Buy RELIANCE");
    });
  });
});
