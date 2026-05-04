import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentPanel } from "@/components/agent-panel/AgentPanel";
import { _clearCatalogCache, setStepTypesSource } from "@/lib/api";

beforeEach(() => {
  _clearCatalogCache();
  setStepTypesSource("mock");
});

describe("AgentPanel", () => {
  it("does not render anything when closed", () => {
    render(<AgentPanel open={false} onOpenChange={() => {}} />);
    expect(screen.queryByTestId("agent-panel")).toBeNull();
  });

  it("renders the demo workflow with all 5 step cards once the catalog loads", async () => {
    render(<AgentPanel open={true} onOpenChange={() => {}} />);

    // Header: name input prefilled from the demo workflow.
    await waitFor(() => {
      expect(
        screen.getByDisplayValue(/RELIANCE 3:55 PM buy/i),
      ).toBeInTheDocument();
    });

    // Five step cards, indices 0..4.
    for (let i = 0; i < 5; i += 1) {
      expect(screen.getByTestId(`step-card-${i}`)).toBeInTheDocument();
    }

    // Status badge shows draft.
    expect(screen.getByText("Draft")).toBeInTheDocument();
  });

  it("invokes onOpenChange(false) when Escape is pressed", async () => {
    const calls: boolean[] = [];
    render(
      <AgentPanel
        open={true}
        onOpenChange={(next) => calls.push(next)}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("agent-panel")).toBeInTheDocument();
    });
    await userEvent.keyboard("{Escape}");
    expect(calls).toContain(false);
  });

  it("invokes onOpenChange(false) when the close button is clicked", async () => {
    const calls: boolean[] = [];
    render(
      <AgentPanel
        open={true}
        onOpenChange={(next) => calls.push(next)}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("agent-panel")).toBeInTheDocument();
    });
    await userEvent.click(
      screen.getByRole("button", { name: /close agent panel/i }),
    );
    expect(calls).toContain(false);
  });

  it("exposes a vertical resize handle on the panel's left edge", async () => {
    render(<AgentPanel open={true} onOpenChange={() => {}} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("agent-panel-resize-handle"),
      ).toBeInTheDocument();
    });
    const handle = screen.getByTestId("agent-panel-resize-handle");
    expect(handle).toHaveAttribute("aria-orientation", "vertical");
  });
});
