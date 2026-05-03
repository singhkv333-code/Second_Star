import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// jsdom does not implement window.matchMedia. Stub it so components that
// read `prefers-color-scheme` (e.g. the theme toggle in AppShell) don't crash.
if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- jsdom polyfill
  (window as any).matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}

// jsdom does not implement ResizeObserver / hasPointerCapture, both of which
// are touched by Radix UI primitives (Select, Dropdown, etc.). Stub them so
// component tests can render those primitives.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
if (typeof globalThis.ResizeObserver === "undefined") {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- jsdom polyfill
  (globalThis as any).ResizeObserver = ResizeObserverStub;
}
if (typeof Element !== "undefined") {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- jsdom polyfill
  const proto = Element.prototype as any;
  if (typeof proto.hasPointerCapture !== "function") {
    proto.hasPointerCapture = () => false;
  }
  if (typeof proto.releasePointerCapture !== "function") {
    proto.releasePointerCapture = () => {};
  }
  if (typeof proto.setPointerCapture !== "function") {
    proto.setPointerCapture = () => {};
  }
  if (typeof proto.scrollIntoView !== "function") {
    proto.scrollIntoView = () => {};
  }
}

afterEach(() => {
  cleanup();
});
