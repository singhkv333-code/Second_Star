/**
 * Tests for AppBootstrap — auth gate.
 *
 * Without a token in localStorage, AppBootstrap renders SignInPrompt
 * instead of children. The "Try demo account" button calls
 * /auth/register, stores the returned token, and reveals the children.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AppBootstrap } from "@/components/AppBootstrap";

// JSDOM in this Vitest setup doesn't expose a working window.localStorage
// (it's stubbed elsewhere or not provided). Install a minimal in-memory
// shim once at module load so every test can rely on getItem/setItem/
// removeItem and on a clean state per test via the beforeEach reset.
{
  const store: Record<string, string> = {};
  const ls = {
    getItem: (k: string): string | null =>
      Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null,
    setItem: (k: string, v: string): void => {
      store[k] = String(v);
    },
    removeItem: (k: string): void => {
      delete store[k];
    },
    clear: (): void => {
      for (const k of Object.keys(store)) delete store[k];
    },
    key: (i: number): string | null => Object.keys(store)[i] ?? null,
    get length(): number {
      return Object.keys(store).length;
    },
  };
  Object.defineProperty(window, "localStorage", {
    value: ls,
    configurable: true,
    writable: true,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

const CHILD_TEXT = "child rendered ok";
const Child = (): React.ReactElement => <div>{CHILD_TEXT}</div>;

describe("AppBootstrap", () => {
  it("renders SignInPrompt when no token in localStorage", async () => {
    render(<AppBootstrap><Child /></AppBootstrap>);
    await waitFor(() =>
      expect(screen.getByTestId("demo-account-btn")).toBeInTheDocument(),
    );
    expect(screen.queryByText(CHILD_TEXT)).not.toBeInTheDocument();
  });

  it("renders children directly when a token exists in localStorage", async () => {
    window.localStorage.setItem("pivot_jwt", "fake-token");
    render(<AppBootstrap><Child /></AppBootstrap>);
    await waitFor(() =>
      expect(screen.getByText(CHILD_TEXT)).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("demo-account-btn")).not.toBeInTheDocument();
  });

  it("Try demo account button calls /auth/register, stores token, reveals children", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(
          JSON.stringify({ access_token: "minted-jwt-from-register" }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
      );

    render(<AppBootstrap><Child /></AppBootstrap>);
    await waitFor(() =>
      expect(screen.getByTestId("demo-account-btn")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("demo-account-btn"));

    await waitFor(() =>
      expect(screen.getByText(CHILD_TEXT)).toBeInTheDocument(),
    );

    // Token persisted.
    expect(window.localStorage.getItem("pivot_jwt")).toBe(
      "minted-jwt-from-register",
    );

    // The fetch hit /auth/register with the demo password.
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const call = fetchSpy.mock.calls[0];
    expect(String(call[0])).toMatch(/\/auth\/register$/);
    const body = JSON.parse(String(call[1]?.body ?? "{}"));
    expect(body.password).toBe("password123");
    expect(body.email).toMatch(/@example\.com$/);
  });

  it("surfaces error inline when /auth/register fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("Internal Server Error", { status: 500 }),
    );

    render(<AppBootstrap><Child /></AppBootstrap>);
    fireEvent.click(await screen.findByTestId("demo-account-btn"));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeInTheDocument(),
    );
    // Children remain hidden — gate stays closed on error.
    expect(screen.queryByText(CHILD_TEXT)).not.toBeInTheDocument();
  });

  it("paste-token path also stores token + reveals children", async () => {
    render(<AppBootstrap><Child /></AppBootstrap>);
    await waitFor(() =>
      expect(screen.getByTestId("demo-account-btn")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("toggle-paste"));
    const textarea = screen.getByTestId("paste-textarea");
    fireEvent.change(textarea, { target: { value: "  pasted-jwt  " } });
    fireEvent.click(screen.getByTestId("paste-submit-btn"));

    await waitFor(() =>
      expect(screen.getByText(CHILD_TEXT)).toBeInTheDocument(),
    );
    expect(window.localStorage.getItem("pivot_jwt")).toBe("pasted-jwt");
  });

  it("paste-token form ignores empty submissions", async () => {
    render(<AppBootstrap><Child /></AppBootstrap>);
    await waitFor(() =>
      expect(screen.getByTestId("demo-account-btn")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("toggle-paste"));
    fireEvent.click(screen.getByTestId("paste-submit-btn"));
    // Still on the prompt, no token stored.
    expect(screen.queryByText(CHILD_TEXT)).not.toBeInTheDocument();
    expect(window.localStorage.getItem("pivot_jwt")).toBeNull();
  });
});
