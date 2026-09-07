/**
 * In-process simulator for the run-stream WebSocket so RunView can be
 * reviewed end-to-end before backend ships `WS /api/runs/{id}/stream`.
 *
 * Mirrors the `RunStream` shape from `lib/ws.ts` so the swap on Day 5 is
 * one toggle (see `setBackendSource` in `lib/api.ts`). Frame shapes match
 * docs/API_CONTRACT.md §10 exactly.
 */

import type {
  Approval,
  Run,
  RunStep,
  WsApprovalRequestedFrame,
  WsRunUpdateFrame,
  WsStepUpdateFrame,
} from "@/lib/types";
import type { RunStream, RunStreamCallbacks } from "@/lib/ws";

const STEP_TICK_MS = 800;
const APPROVAL_PAUSE_MS = 2000;

const DEMO_STEP_TYPES: ReadonlyArray<RunStep["step_type"]> = [
  "trigger.schedule",
  "fetch.portfolio",
  "condition.numeric",
  "action.place_order",
  "notify.message",
];

function nowIso(): string {
  return new Date().toISOString();
}

function blankRun(runId: string): Run {
  return {
    id: runId,
    workflow_id: "00000000-0000-4000-8000-000000000001",
    workflow_version: 1,
    triggered_by: "manual",
    started_at: nowIso(),
    finished_at: null,
    status: "running",
    halt_reason: null,
    error_message: null,
    context: {},
    steps: DEMO_STEP_TYPES.map((step_type, idx) => ({
      step_index: idx,
      step_type,
      status: "pending",
      started_at: null,
      finished_at: null,
      output: null,
      error_message: null,
      attempts: 0,
    })),
  };
}

/**
 * Drive `callbacks` with a deterministic 5-step demo run:
 *   0 trigger.schedule → 1 fetch.portfolio → 2 condition.numeric →
 *   3 action.place_order (pauses 2s for approval, then succeeds) →
 *   4 notify.message → run succeeds.
 *
 * Returns a `RunStream` whose `close()` halts the simulation immediately.
 */
export function openMockRunStream(
  runId: string,
  callbacks: RunStreamCallbacks,
): RunStream {
  let cancelled = false;
  const timers: ReturnType<typeof setTimeout>[] = [];

  const schedule = (delay: number, fn: () => void): void => {
    if (cancelled) return;
    const t = setTimeout(() => {
      if (!cancelled) fn();
    }, delay);
    timers.push(t);
  };

  const initial = blankRun(runId);
  // Send snapshot synchronously after a microtask so consumers can attach
  // state before the first update arrives.
  Promise.resolve().then(() => {
    if (cancelled) return;
    callbacks.onConnectionStateChange("open");
    callbacks.onSnapshot(initial);
  });

  const stepCount = DEMO_STEP_TYPES.length;

  // Precompute sample outputs so each step's expand-to-output panel has
  // something inspectable.
  const outputs: Record<number, Record<string, unknown> | null> = {
    0: null,
    1: { holdings: [], buying_power: 75000, total_value: 230000 },
    2: { passed: true },
    3: {
      order_id: "MOCK_ORDER_8821",
      status: "COMPLETE",
      client_request_id: `${runId.slice(0, 8)}:3:1`,
    },
    4: { channel: "email", delivered: true },
  };

  let cumulativeDelay = 0;

  for (let idx = 0; idx < stepCount; idx += 1) {
    const startDelay = cumulativeDelay;
    // Step → running.
    schedule(startDelay, () => {
      const frame: WsStepUpdateFrame = {
        type: "step_update",
        run_id: runId,
        step_index: idx,
        step: {
          step_index: idx,
          step_type: DEMO_STEP_TYPES[idx]!,
          status: "running",
          started_at: nowIso(),
          finished_at: null,
          output: null,
          error_message: null,
          attempts: 1,
        },
      };
      callbacks.onStepUpdate(frame);
    });

    cumulativeDelay += STEP_TICK_MS;

    // Step 3 (action.place_order) requires_approval=true → pause.
    if (idx === 3) {
      schedule(cumulativeDelay, () => {
        const approvalFrame: WsApprovalRequestedFrame = {
          type: "approval_requested",
          run_id: runId,
          approval: {
            id: "approval-mock-1",
            run_id: runId,
            step_index: 3,
            summary: "BUY 10 RELIANCE at market",
            requested_at: nowIso(),
            expires_at: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
            decision: null,
            decided_at: null,
          },
        };
        callbacks.onApprovalRequested(approvalFrame);

        const runUpd: WsRunUpdateFrame = {
          type: "run_update",
          run_id: runId,
          status: "awaiting_approval",
          finished_at: null,
          halt_reason: null,
        };
        callbacks.onRunUpdate(runUpd);

        // Mark the gated step status itself as awaiting_approval so the
        // step row paints amber while the banner is up.
        callbacks.onStepUpdate({
          type: "step_update",
          run_id: runId,
          step_index: 3,
          step: {
            step_index: 3,
            step_type: "action.place_order",
            status: "awaiting_approval",
            started_at: nowIso(),
            finished_at: null,
            output: null,
            error_message: null,
            attempts: 1,
          },
        });
      });
      cumulativeDelay += APPROVAL_PAUSE_MS;

      // Auto-resolve the approval (mock) and resume the step.
      schedule(cumulativeDelay, () => {
        callbacks.onRunUpdate({
          type: "run_update",
          run_id: runId,
          status: "running",
          finished_at: null,
          halt_reason: null,
        });
        callbacks.onStepUpdate({
          type: "step_update",
          run_id: runId,
          step_index: 3,
          step: {
            step_index: 3,
            step_type: "action.place_order",
            status: "running",
            started_at: nowIso(),
            finished_at: null,
            output: null,
            error_message: null,
            attempts: 2,
          },
        });
      });
      cumulativeDelay += STEP_TICK_MS;
    }

    // Step → succeeded.
    schedule(cumulativeDelay, () => {
      callbacks.onStepUpdate({
        type: "step_update",
        run_id: runId,
        step_index: idx,
        step: {
          step_index: idx,
          step_type: DEMO_STEP_TYPES[idx]!,
          status: "succeeded",
          started_at: nowIso(),
          finished_at: nowIso(),
          output: outputs[idx] ?? null,
          error_message: null,
          attempts: idx === 3 ? 2 : 1,
        },
      });
    });
    cumulativeDelay += STEP_TICK_MS;
  }

  // Run-level success.
  schedule(cumulativeDelay, () => {
    callbacks.onRunUpdate({
      type: "run_update",
      run_id: runId,
      status: "succeeded",
      finished_at: nowIso(),
      halt_reason: null,
    });
    callbacks.onConnectionStateChange("closed");
  });

  return {
    close: () => {
      cancelled = true;
      for (const t of timers) clearTimeout(t);
      callbacks.onConnectionStateChange("closed");
    },
    isOpen: () => !cancelled,
  };
}

// Helpers exported for tests.
export type { Approval };
