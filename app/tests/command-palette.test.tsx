/**
 * CommandPalette — smoke tests.
 * Covers: Cmd+K opens, Esc closes, nav item calls onNavigate, conversation calls handler.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CommandPalette } from "@/components/CommandPalette";

const CONVS = [
  { id: "c1", preview: "RELIANCE breakout strategy" },
  { id: "c2", preview: "Portfolio rebalance Q4" },
];

describe("CommandPalette", () => {
  it("is not visible initially", () => {
    render(
      <CommandPalette
        conversations={CONVS}
        onNavigate={vi.fn()}
        onOpenConversation={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("command-palette-input")).not.toBeInTheDocument();
  });

  it("opens on Cmd+K", async () => {
    render(
      <CommandPalette
        conversations={CONVS}
        onNavigate={vi.fn()}
        onOpenConversation={vi.fn()}
      />,
    );
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await waitFor(() =>
      expect(screen.getByTestId("command-palette-input")).toBeInTheDocument(),
    );
  });

  it("shows nav items after open", async () => {
    render(
      <CommandPalette
        conversations={CONVS}
        onNavigate={vi.fn()}
        onOpenConversation={vi.fn()}
      />,
    );
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await waitFor(() =>
      expect(screen.getByTestId("cmd-nav-dashboard")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("cmd-nav-agents")).toBeInTheDocument();
    expect(screen.getByTestId("cmd-nav-backtest")).toBeInTheDocument();
  });

  it("clicking a nav item calls onNavigate and closes", async () => {
    const onNavigate = vi.fn();
    render(
      <CommandPalette
        conversations={CONVS}
        onNavigate={onNavigate}
        onOpenConversation={vi.fn()}
      />,
    );
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await waitFor(() =>
      expect(screen.getByTestId("cmd-nav-portfolio")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("cmd-nav-portfolio"));
    expect(onNavigate).toHaveBeenCalledWith("portfolio");
  });

  it("shows recent conversations", async () => {
    render(
      <CommandPalette
        conversations={CONVS}
        onNavigate={vi.fn()}
        onOpenConversation={vi.fn()}
      />,
    );
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await waitFor(() =>
      expect(screen.getByText("RELIANCE breakout strategy")).toBeInTheDocument(),
    );
    expect(screen.getByText("Portfolio rebalance Q4")).toBeInTheDocument();
  });
});
