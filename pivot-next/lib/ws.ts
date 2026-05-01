/**
 * WebSocket client for `WS /api/runs/{id}/stream` per docs/API_CONTRACT.md §10.
 *
 * Behaviors:
 * - Connects with `?token=<jwt>` query param (browsers can't set Authorization
 *   on the upgrade request; we accept the alternate scheme from §10.1).
 * - Replies to server pings with pongs.
 * - Auto-reconnects with exponential backoff (1s, 2s, 4s, 8s, capped at 8s).
 * - On disconnect, the consumer-supplied poll callback fires every 2s with
 *   `getRun(id)` results so the UI can update + show "reconnecting…".
 * - Stops everything on `close()` or when the run reaches a terminal status
 *   (server closes with code 1000).
 *
 * Consumers pass typed callbacks for each frame variant — no broadcast bus,
 * no pattern subscribers. Keeps the wiring legible in the RunView component.
 */

import { getRun } from "@/lib/api";
import { isError } from "@/lib/types";
import type {
  Approval,
  Run,
  RunStep,
  RunStreamFrame,
  WsApprovalRequestedFrame,
  WsRunUpdateFrame,
  WsStepUpdateFrame,
} from "@/lib/types";

export type RunStreamCallbacks = {
  onSnapshot: (run: Run) => void;
  onStepUpdate: (frame: WsStepUpdateFrame) => void;
  onRunUpdate: (frame: WsRunUpdateFrame) => void;
  onApprovalRequested: (frame: WsApprovalRequestedFrame) => void;
  /**
   * Connection lifecycle. `state` flips to `"reconnecting"` while the WS is
   * down; the UI typically renders a small "reconnecting…" indicator and
   * keeps showing the latest poll snapshot.
   */
  onConnectionStateChange: (
    state: "connecting" | "open" | "reconnecting" | "closed",
  ) => void;
  /** Called for any unhandled error (parse failure, unknown frame type). */
  onError?: (err: Error) => void;
};

export type RunStreamOptions = {
  /** JWT token included as `?token=` query param. */
  token?: string | null;
  /** Override base URL; defaults to `${location.origin}/api`. */
  baseUrl?: string;
  /** Polling fallback interval while disconnected. Default 2000 ms (§10.1). */
  pollIntervalMs?: number;
  /** Max reconnect backoff. Default 8000 ms. */
  maxBackoffMs?: number;
};

export type RunStream = {
  /** Close the socket + stop polling. Idempotent. */
  close: () => void;
  /** Whether the socket is currently open. */
  isOpen: () => boolean;
};

function defaultBaseUrl(): string {
  if (typeof window === "undefined") return "/api";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api`;
}

export function openRunStream(
  runId: string,
  callbacks: RunStreamCallbacks,
  options: RunStreamOptions = {},
): RunStream {
  const pollIntervalMs = options.pollIntervalMs ?? 2000;
  const maxBackoffMs = options.maxBackoffMs ?? 8000;
  const base = options.baseUrl ?? defaultBaseUrl();

  let ws: WebSocket | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectAttempts = 0;
  let manuallyClosed = false;
  let runIsTerminal = false;

  const buildUrl = (): string => {
    const url = new URL(
      `${base}/runs/${encodeURIComponent(runId)}/stream`.replace(
        /^https?:/,
        base.startsWith("https") ? "wss:" : "ws:",
      ),
    );
    if (options.token) url.searchParams.set("token", options.token);
    return url.toString();
  };

  const stopPolling = (): void => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const startPolling = (): void => {
    if (pollTimer || manuallyClosed || runIsTerminal) return;
    pollTimer = setInterval(async () => {
      try {
        const result = await getRun(runId);
        if (isError(result)) {
          callbacks.onError?.(new Error(result.error.message));
          return;
        }
        callbacks.onSnapshot(result.data);
        if (isTerminalRunStatus(result.data.status)) {
          runIsTerminal = true;
          stopPolling();
          callbacks.onConnectionStateChange("closed");
        }
      } catch (err) {
        callbacks.onError?.(err instanceof Error ? err : new Error(String(err)));
      }
    }, pollIntervalMs);
  };

  const scheduleReconnect = (): void => {
    if (manuallyClosed || runIsTerminal) return;
    callbacks.onConnectionStateChange("reconnecting");
    startPolling();
    const backoff = Math.min(
      1000 * 2 ** reconnectAttempts,
      maxBackoffMs,
    );
    reconnectAttempts += 1;
    reconnectTimer = setTimeout(connect, backoff);
  };

  const connect = (): void => {
    if (manuallyClosed || runIsTerminal) return;
    callbacks.onConnectionStateChange("connecting");
    try {
      ws = new WebSocket(buildUrl());
    } catch (err) {
      callbacks.onError?.(err instanceof Error ? err : new Error(String(err)));
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      reconnectAttempts = 0;
      stopPolling();
      callbacks.onConnectionStateChange("open");
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      let frame: RunStreamFrame;
      try {
        frame = JSON.parse(event.data) as RunStreamFrame;
      } catch (err) {
        callbacks.onError?.(
          err instanceof Error ? err : new Error("Failed to parse WS frame"),
        );
        return;
      }
      dispatchFrame(frame);
    };

    ws.onerror = () => {
      // Browsers don't expose error detail; log a generic and let onclose drive reconnection.
      callbacks.onError?.(new Error("WebSocket error"));
    };

    ws.onclose = (event: CloseEvent) => {
      ws = null;
      // Clean close on terminal: do not reconnect.
      if (event.code === 1000) {
        runIsTerminal = true;
        stopPolling();
        callbacks.onConnectionStateChange("closed");
        return;
      }
      scheduleReconnect();
    };
  };

  const dispatchFrame = (frame: RunStreamFrame): void => {
    switch (frame.type) {
      case "snapshot":
        callbacks.onSnapshot(frame.run);
        if (isTerminalRunStatus(frame.run.status)) runIsTerminal = true;
        return;
      case "step_update":
        callbacks.onStepUpdate(frame);
        return;
      case "run_update":
        callbacks.onRunUpdate(frame);
        if (isTerminalRunStatus(frame.status)) runIsTerminal = true;
        return;
      case "approval_requested":
        callbacks.onApprovalRequested(frame);
        return;
      case "ping":
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "pong" }));
        }
        return;
      default: {
        // Exhaustiveness guard — unknown frame variants surface as a soft error.
        const unknown: { type?: string } = frame;
        callbacks.onError?.(
          new Error(`Unknown WS frame type: ${unknown.type ?? "unknown"}`),
        );
      }
    }
  };

  connect();

  return {
    close: () => {
      manuallyClosed = true;
      stopPolling();
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (ws) {
        try {
          ws.close(1000, "client closed");
        } catch {
          // Ignore — already closing.
        }
        ws = null;
      }
      callbacks.onConnectionStateChange("closed");
    },
    isOpen: () => ws !== null && ws.readyState === WebSocket.OPEN,
  };
}

function isTerminalRunStatus(status: Run["status"]): boolean {
  return (
    status === "succeeded" ||
    status === "failed" ||
    status === "cancelled"
  );
}

// Re-exports so the RunView only imports from "lib/ws".
export type { Approval, Run, RunStep, RunStreamFrame };
