/**
 * React hook that wraps `lib/ws.openRunStream` (real WS) or
 * `lib/mock-run.openMockRunStream` (Day 2 simulator) and exposes a single
 * piece of state to the consumer:
 *
 *   { run, isReconnecting, error }
 *
 * Picks its source from `getBackendSource()` so a single global toggle
 * flips every UI surface between mock and real on Day 5.
 */

"use client";

import { useEffect, useRef, useState } from "react";
import { getBackendSource } from "@/lib/api";
import { openMockRunStream } from "@/lib/mock-run";
import { openRunStream, type RunStreamCallbacks } from "@/lib/ws";
import type {
  Approval,
  ErrorBody,
  Run,
  RunStep,
  RunStatus,
} from "@/lib/types";

export type UseRunStreamState = {
  run: Run | null;
  isReconnecting: boolean;
  error: ErrorBody | null;
  /** Pending approvals seen via `approval_requested` frames. */
  pendingApprovals: Approval[];
};

type RunStreamHandle = { close: () => void };

export function useRunStream(runId: string | null): UseRunStreamState {
  const [state, setState] = useState<UseRunStreamState>({
    run: null,
    isReconnecting: false,
    error: null,
    pendingApprovals: [],
  });
  const handleRef = useRef<RunStreamHandle | null>(null);

  useEffect(() => {
    if (!runId) {
      setState({ run: null, isReconnecting: false, error: null, pendingApprovals: [] });
      return;
    }

    const callbacks: RunStreamCallbacks = {
      onSnapshot: (run) => {
        setState((prev) => ({ ...prev, run, error: null }));
      },
      onStepUpdate: ({ step }) => {
        setState((prev) => {
          if (!prev.run) return prev;
          return {
            ...prev,
            run: { ...prev.run, steps: mergeStep(prev.run.steps, step) },
          };
        });
      },
      onRunUpdate: ({ status, finished_at, halt_reason }) => {
        setState((prev) => {
          if (!prev.run) return prev;
          return {
            ...prev,
            run: {
              ...prev.run,
              status: status as RunStatus,
              finished_at,
              halt_reason,
            },
          };
        });
      },
      onApprovalRequested: ({ approval }) => {
        setState((prev) => ({
          ...prev,
          pendingApprovals: [
            ...prev.pendingApprovals.filter((a) => a.id !== approval.id),
            approval,
          ],
        }));
      },
      onConnectionStateChange: (s) => {
        setState((prev) => ({ ...prev, isReconnecting: s === "reconnecting" }));
      },
      onError: (err) => {
        setState((prev) => ({
          ...prev,
          error: { code: "internal_error", message: err.message },
        }));
      },
    };

    const handle =
      getBackendSource() === "mock"
        ? openMockRunStream(runId, callbacks)
        : openRunStream(runId, callbacks);
    handleRef.current = handle;

    return () => {
      handle.close();
      handleRef.current = null;
    };
  }, [runId]);

  return state;
}

/**
 * Test helper: resolve a pending approval client-side. The Day 5 wiring
 * will replace this with `decideApproval()` from lib/api.ts.
 */
export function resolveMockApproval(
  state: UseRunStreamState,
  approvalId: string,
): UseRunStreamState {
  return {
    ...state,
    pendingApprovals: state.pendingApprovals.filter((a) => a.id !== approvalId),
  };
}

function mergeStep(steps: RunStep[], next: RunStep): RunStep[] {
  const idx = steps.findIndex((s) => s.step_index === next.step_index);
  if (idx < 0) return [...steps, next];
  const out = [...steps];
  out[idx] = next;
  return out;
}
