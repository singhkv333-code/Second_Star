import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

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
