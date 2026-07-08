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

// jsdom does not implement HTMLCanvasElement.getContext, which TradingView
// lightweight-charts calls on mount + teardown. Stub a chainable no-op 2D
// context so chart components render (and dispose) silently in component tests.
if (typeof HTMLCanvasElement !== "undefined") {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- jsdom polyfill
  const proto = HTMLCanvasElement.prototype as any;
  if (typeof proto.getContext !== "function" || !proto.__lwcStub) {
    proto.getContext = () =>
      new Proxy(
        {
          canvas: { width: 0, height: 0 },
          measureText: () => ({ width: 0 }),
          getImageData: () => ({ data: [] }),
          createLinearGradient: () => ({ addColorStop: () => {} }),
          setLineDash: () => {},
        },
        { get: (t, p) => (p in t ? (t as any)[p] : () => {}) },
      );
    proto.__lwcStub = true;
  }
}

afterEach(() => {
  cleanup();
});
