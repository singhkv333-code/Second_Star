/**
 * Tests for the chat ↔ editor sync feature (Phase 3).
 *
 * Verifies:
 * 1. When a chat turn yields a workflow_draft_card AND the panel is open
 *    on an unsaved draft, onDraftFromChat is called with the updated draft
 *    (simulating the editor re-render path).
 * 2. When the panel is NOT open with a draft, the outgoing chat request body
 *    does NOT contain editor_draft.
 * 3. When the panel IS open with an unsaved draft, the outgoing chat request
 *    body includes editor_draft matching the WorkflowDraft shape.
 * 4. onDraftFromChat is also called when chat returns a NEW draft (not just
 *    an amendment of the currently-open one).
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { ChatDemo } from "@/components/chat/ChatDemo";
import {
  ActiveDraftContext,
  type ActiveDraftContextValue,
} from "@/components/agent-panel/active-draft-context";
import type { Workflow } from "@/lib/types";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const MOCK_DRAFT_RESPONSE = {
  response: "Here is your amended workflow.",
  tools_called: ["propose_workflow"],
  raw_data: {
    _render_hint: "workflow_draft_card",
    name: "Amended RELIANCE buy",
    description: "Amended: buy 20 shares of RELIANCE every weekday",
    steps: [
      {
        step_type: "trigger.schedule",
        label: "Every weekday at 3:55 PM IST",
        config: {},
      },
      {
        step_type: "action.place_order",
        label: "Buy 20 RELIANCE",
        config: { symbol: "RELIANCE", quantity: 20 },
      },
    ],
    rationale: "Amended rationale.",
    warnings: [],
  },
};

const MOCK_TEXT_RESPONSE = {
  response: "I can help with that.",
  raw_data: null,
};

/** An unsaved draft currently bound to the editor. */
const UNSAVED_DRAFT: Workflow = {
  id: "",
  name: "RELIANCE buy",
  description: "Buy 10 RELIANCE",
  status: "draft",
  version: 1,
  single_instance: true,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  activated_at: null,
  last_run_at: null,
  next_run_at: null,
  steps: [
    {
      id: "draft-step-0",
      step_index: 0,
      step_type: "trigger.schedule",
      label: "Every weekday",
      config: {},
    },
    {
      id: "draft-step-1",
      step_index: 1,
      step_type: "action.place_order",
      label: "Buy 10 RELIANCE",
      config: { symbol: "RELIANCE", quantity: 10 },
    },
  ],
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeSseStream(payload: unknown): ReadableStream<Uint8Array> {
  const event = `data: ${JSON.stringify({ type: "done", ...(payload as object) })}\n\n`;
  const bytes = new TextEncoder().encode(event);
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(bytes);
      controller.close();
    },
  });
}

function mockFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const spy = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    body: status >= 200 && status < 300 ? makeSseStream(body) : null,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}

/** Render ChatDemo with the ActiveDraftContext providing `ctxValue`. */
function renderWithDraftCtx(
  ctxValue: ActiveDraftContextValue,
  props: Partial<Parameters<typeof ChatDemo>[0]> = {},
) {
  return render(
    <ActiveDraftContext.Provider value={ctxValue}>
      <ChatDemo onOpenEditor={vi.fn()} {...props} />
    </ActiveDraftContext.Provider>,
  );
}

// ---------------------------------------------------------------------------
// Shared beforeEach
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("chat ↔ editor sync", () => {
  it("calls onDraftFromChat when chat returns a workflow_draft_card (editor closed)", async () => {
    mockFetch(MOCK_DRAFT_RESPONSE);
    const onDraftFromChat = vi.fn();

    // Editor is closed — panelOpenWithDraft = false, activeEditorDraft = null.
    renderWithDraftCtx(
      {
        activeEditorDraft: null,
        setActiveEditorDraft: vi.fn(),
        panelOpenWithDraft: false,
      },
      { onDraftFromChat },
    );

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Amend my RELIANCE workflow" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("workflow-draft-card")).toBeInTheDocument();
    });

    expect(onDraftFromChat).toHaveBeenCalledTimes(1);
    const arg = onDraftFromChat.mock.calls[0]![0] as Workflow;
    expect(arg.name).toBe("Amended RELIANCE buy");
    expect(arg.status).toBe("draft");
    expect(arg.steps).toHaveLength(2);
  });

  it("calls onDraftFromChat when chat returns a draft AND panel is open with unsaved draft", async () => {
    mockFetch(MOCK_DRAFT_RESPONSE);
    const onDraftFromChat = vi.fn();

    renderWithDraftCtx(
      {
        activeEditorDraft: UNSAVED_DRAFT,
        setActiveEditorDraft: vi.fn(),
        panelOpenWithDraft: true,
      },
      { onDraftFromChat },
    );

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Change quantity to 20" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => {
      expect(onDraftFromChat).toHaveBeenCalledTimes(1);
    });

    const arg = onDraftFromChat.mock.calls[0]![0] as Workflow;
    expect(arg.name).toBe("Amended RELIANCE buy");
    expect(arg.steps[1]?.config).toMatchObject({ symbol: "RELIANCE", quantity: 20 });
  });

  it("does NOT include editor_draft in the request body when panel is closed", async () => {
    const fetchSpy = mockFetch(MOCK_TEXT_RESPONSE);

    renderWithDraftCtx({
      activeEditorDraft: null,
      setActiveEditorDraft: vi.fn(),
      panelOpenWithDraft: false,
    });

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Buy RELIANCE" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());

    const call = fetchSpy.mock.calls[0] as [string, { body?: string }];
    const body = JSON.parse(call[1]?.body ?? "{}") as Record<string, unknown>;
    expect(body).not.toHaveProperty("editor_draft");
  });

  it("includes editor_draft in the request body when panel is open with unsaved draft", async () => {
    const fetchSpy = mockFetch(MOCK_TEXT_RESPONSE);

    renderWithDraftCtx({
      activeEditorDraft: UNSAVED_DRAFT,
      setActiveEditorDraft: vi.fn(),
      panelOpenWithDraft: true,
    });

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "Change the condition" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());

    const call = fetchSpy.mock.calls[0] as [string, { body?: string }];
    const body = JSON.parse(call[1]?.body ?? "{}") as Record<string, unknown>;
    expect(body).toHaveProperty("editor_draft");

    const editorDraft = body.editor_draft as {
      name: string;
      steps: unknown[];
      _render_hint: string;
    };
    expect(editorDraft.name).toBe("RELIANCE buy");
    expect(editorDraft.steps).toHaveLength(2);
    expect(editorDraft._render_hint).toBe("workflow_draft_card");
  });

  it("does NOT call onDraftFromChat for non-draft responses", async () => {
    mockFetch(MOCK_TEXT_RESPONSE);
    const onDraftFromChat = vi.fn();

    renderWithDraftCtx(
      {
        activeEditorDraft: null,
        setActiveEditorDraft: vi.fn(),
        panelOpenWithDraft: false,
      },
      { onDraftFromChat },
    );

    fireEvent.change(screen.getByTestId("chat-textarea"), {
      target: { value: "What is the PE ratio of RELIANCE?" },
    });
    fireEvent.click(screen.getByTestId("chat-submit-btn"));

    await waitFor(() => {
      expect(screen.getByText("I can help with that.")).toBeInTheDocument();
    });

    expect(onDraftFromChat).not.toHaveBeenCalled();
  });
});
