import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QuickAsk } from "@/components/copilot/QuickAsk";

beforeEach(() => {
  const stored = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
    removeItem: (key: string) => stored.delete(key),
  });
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 200, top: 300, width: 440, height: 64, right: 640, bottom: 364,
    x: 200, y: 300, toJSON() {},
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const mount = () => render(<QuickAsk placeholder="Ask about this chart" contextLabel="Chart" onSubmit={vi.fn()} />);

it("keeps the time-axis area clear, remembers moves, and resets", () => {
  mount();
  const handle = screen.getByRole("button", { name: "Move prompt bar" });
  fireEvent.keyDown(handle, { key: "ArrowDown", shiftKey: true });
  let saved = JSON.parse(localStorage.getItem("pivot:quick-ask-position")!);
  expect(saved.top).toBeLessThanOrEqual(window.innerHeight - 128);
  fireEvent.keyDown(handle, { key: "ArrowLeft" });
  saved = JSON.parse(localStorage.getItem("pivot:quick-ask-position")!);
  expect(saved.left).toBe(192);
  cleanup();
  mount();
  expect(screen.getByTestId("copilot-quick-ask").style.top).toBe(`${saved.top}px`);
  fireEvent.doubleClick(screen.getByRole("button", { name: "Move prompt bar" }));
  expect(localStorage.getItem("pivot:quick-ask-position")).toBeNull();
  expect(screen.getByTestId("copilot-quick-ask").style.top).toBe("");
});

it("clamps an offscreen saved location after reopening on a smaller screen", () => {
  localStorage.setItem("pivot:quick-ask-position", JSON.stringify({ left: 99999, top: 99999 }));
  mount();
  const bar = screen.getByTestId("copilot-quick-ask");
  expect(parseFloat(bar.style.top)).toBe(window.innerHeight - 128);
});

it("drags from the alien without letting the bar cover the time axis", () => {
  vi.stubGlobal("PointerEvent", MouseEvent);
  mount();
  const handle = screen.getByRole("button", { name: "Move prompt bar" });
  handle.setPointerCapture = vi.fn();
  handle.releasePointerCapture = vi.fn();
  fireEvent.pointerDown(handle, { button: 0, clientX: 210, clientY: 310 });
  fireEvent.pointerMove(handle, { clientX: 310, clientY: 9999 });
  fireEvent.pointerUp(handle, { clientX: 310, clientY: 9999 });
  const saved = JSON.parse(localStorage.getItem("pivot:quick-ask-position")!);
  expect(saved.left).toBe(300);
  expect(saved.top).toBe(window.innerHeight - 128);
  expect(handle.setPointerCapture).toHaveBeenCalled();
  expect(handle.releasePointerCapture).toHaveBeenCalled();
});
