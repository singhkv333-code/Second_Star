/**
 * Tests for liveQuoteManager — subscribe-counting and WS lifecycle.
 *
 * A mock WebSocket is installed on globalThis before each test and
 * removed after. All state is reset via _resetForTest().
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  subscribe,
  unsubscribe,
  _resetForTest,
  type Listener,
  type LiveTick,
} from "@/lib/liveQuoteManager";

// ---------------------------------------------------------------------------
// Mock WebSocket
// ---------------------------------------------------------------------------

type WsMessage = string;

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  readyState: number = WebSocket.CONNECTING;
  sent: WsMessage[] = [];
  url: string;

  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
    // Simulate async open on next tick so tests can wire listeners first.
    queueMicrotask(() => {
      this.readyState = WebSocket.OPEN;
      this.onopen?.();
    });
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(code = 1000, reason = ""): void {
    this.readyState = WebSocket.CLOSED;
    this.onclose?.({ code, reason });
  }

  /** Test helper: simulate a tick from the server. */
  simulateTick(symbol: string, ltp: number, change_pct: number, ts: number): void {
    this.onmessage?.({
      data: JSON.stringify({ type: "tick", symbol, ltp, change_pct, ts }),
    });
  }

  /** Test helper: simulate a ping. */
  simulatePing(ts: number): void {
    this.onmessage?.({ data: JSON.stringify({ type: "ping", ts }) });
  }
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  MockWebSocket.instances = [];
  _resetForTest();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test mock
  (globalThis as any).WebSocket = MockWebSocket;
});

afterEach(() => {
  _resetForTest();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test mock cleanup
  delete (globalThis as any).WebSocket;
});

// ---------------------------------------------------------------------------
// Helper: flush microtasks so queueMicrotask() callbacks run.
// ---------------------------------------------------------------------------

function flushMicrotasks(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("liveQuoteManager — subscribe counting", () => {
  it("opens a WS connection on first subscribe", async () => {
    const listener: Listener = vi.fn();
    subscribe("RELIANCE", listener);
    await flushMicrotasks();
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("sends subscribe message for first listener on a symbol", async () => {
    const listener: Listener = vi.fn();
    subscribe("RELIANCE", listener);
    await flushMicrotasks();

    const ws = MockWebSocket.instances[0]!;
    const subscribeMsg = ws.sent.find((s) => {
      const parsed = JSON.parse(s) as { type: string; symbols?: string[] };
      return parsed.type === "subscribe" && parsed.symbols?.includes("RELIANCE");
    });
    expect(subscribeMsg).toBeDefined();
  });

  it("does NOT send a second subscribe for a second listener on the same symbol", async () => {
    const listenerA: Listener = vi.fn();
    const listenerB: Listener = vi.fn();
    subscribe("RELIANCE", listenerA);
    await flushMicrotasks();
    const ws = MockWebSocket.instances[0]!;
    const countBefore = ws.sent.filter((s) => {
      const p = JSON.parse(s) as { type: string };
      return p.type === "subscribe";
    }).length;

    subscribe("RELIANCE", listenerB);
    await flushMicrotasks();
    const countAfter = ws.sent.filter((s) => {
      const p = JSON.parse(s) as { type: string };
      return p.type === "subscribe";
    }).length;

    expect(countAfter).toBe(countBefore); // no extra subscribe sent
  });

  it("does NOT unsubscribe when one of two listeners leaves", async () => {
    const listenerA: Listener = vi.fn();
    const listenerB: Listener = vi.fn();
    subscribe("RELIANCE", listenerA);
    subscribe("RELIANCE", listenerB);
    await flushMicrotasks();
    const ws = MockWebSocket.instances[0]!;

    unsubscribe("RELIANCE", listenerA);
    await flushMicrotasks();

    const unsubMsg = ws.sent.find((s) => {
      const p = JSON.parse(s) as { type: string };
      return p.type === "unsubscribe";
    });
    expect(unsubMsg).toBeUndefined(); // still one listener; no unsubscribe
  });

  it("sends unsubscribe when the last listener leaves", async () => {
    const listener: Listener = vi.fn();
    subscribe("RELIANCE", listener);
    await flushMicrotasks();
    const ws = MockWebSocket.instances[0]!;

    unsubscribe("RELIANCE", listener);
    await flushMicrotasks();

    const unsubMsg = ws.sent.find((s) => {
      const p = JSON.parse(s) as { type: string; symbols?: string[] };
      return p.type === "unsubscribe" && p.symbols?.includes("RELIANCE");
    });
    expect(unsubMsg).toBeDefined();
  });

  it("delivers ticks only to listeners for the matching symbol", async () => {
    const listenerR: Listener = vi.fn();
    const listenerT: Listener = vi.fn();
    subscribe("RELIANCE", listenerR);
    subscribe("TCS", listenerT);
    await flushMicrotasks();

    const ws = MockWebSocket.instances[0]!;
    ws.simulateTick("RELIANCE", 2850.0, 0.4, 1747140330);

    expect(listenerR).toHaveBeenCalledOnce();
    expect(listenerT).not.toHaveBeenCalled();

    const tick = (listenerR as ReturnType<typeof vi.fn>).mock.calls[0]![0] as LiveTick;
    expect(tick.symbol).toBe("RELIANCE");
    expect(tick.ltp).toBe(2850.0);
    expect(tick.changePct).toBe(0.4);
  });

  it("normalises symbol to upper-case + underscores", async () => {
    const listener: Listener = vi.fn();
    subscribe("nifty 50", listener);
    await flushMicrotasks();

    const ws = MockWebSocket.instances[0]!;
    const msg = ws.sent.find((s) => {
      const p = JSON.parse(s) as { type: string; symbols?: string[] };
      return p.type === "subscribe" && p.symbols?.includes("NIFTY_50");
    });
    expect(msg).toBeDefined();
  });

  it("replies pong to server ping", async () => {
    const listener: Listener = vi.fn();
    subscribe("RELIANCE", listener);
    await flushMicrotasks();

    const ws = MockWebSocket.instances[0]!;
    ws.simulatePing(1747140360);

    const pongMsg = ws.sent.find((s) => {
      const p = JSON.parse(s) as { type: string };
      return p.type === "pong";
    });
    expect(pongMsg).toBeDefined();
  });

  it("does not open a second WS when subscribing to a second symbol", async () => {
    const l1: Listener = vi.fn();
    const l2: Listener = vi.fn();
    subscribe("RELIANCE", l1);
    subscribe("TCS", l2);
    await flushMicrotasks();
    expect(MockWebSocket.instances).toHaveLength(1);
  });
});
