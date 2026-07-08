/**
 * Client-side capability mirror for the StepTypePicker.
 *
 * Replicates the simulator's classify() / accumulate() logic from
 * docs/plans/WORKFLOW_EDITOR_PLAN.html verbatim. This is a pure, synchronous,
 * zero-network computation — it runs inside useMemo on every picker open.
 *
 * Rules (from the HTML spec, §03 and §05):
 *   - "Recommended" — a trigger when seq is empty; or a step whose requires[]
 *     are all satisfied by the accumulated flow state; or action after condition
 *     / condition after fetch when no requirements are outstanding.
 *   - "Available"   — step has no requires[], not a natural follow-on, but not
 *     missing anything.
 *   - "Needs setup" — at least one requires[] entry is unmet by the accumulated
 *     state AND unmet by the ambient flag. Step remains clickable (hybrid
 *     strictness: the user may already hold the position/order).
 *
 * "Blocked" (trigger at index>0, or non-trigger at index 0) is handled by the
 * hard structural filter BEFORE this function is called — this function only
 * sees the post-filter candidate list.
 */

import type { Step, StepTypeDef } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type CapBucket = "recommended" | "available" | "needs-setup";

export type ClassifyResult = {
  bucket: CapBucket;
  /** The first unmet warn text, if bucket === "needs-setup". */
  unmetWarn: string | null;
};

// ---------------------------------------------------------------------------
// Core engine (mirrors HTML accumulate() + classify())
// ---------------------------------------------------------------------------

/**
 * Accumulate capability tags from steps[0..seq.length-1].
 * Mirrors the HTML accumulate() function exactly:
 *   consume first, then produce (per step).
 */
export function accumulateCaps(steps: Step[], catalog: Map<string, StepTypeDef>): Set<string> {
  const st = new Set<string>();
  for (const step of steps) {
    const def = catalog.get(step.step_type);
    if (!def?.compat) continue;
    for (const c of def.compat.consumes) st.delete(c);
    for (const p of def.compat.produces) st.add(p);
  }
  return st;
}

/**
 * Classify a candidate step type against the current accumulated capability
 * state. Mirrors the HTML classify() function's bucket logic.
 *
 * `accumulated` — capabilities produced by steps[0..insertIndex-1].
 * `prevCat`     — category of the immediately preceding step (for natural
 *                 follow-on detection: action-after-condition, condition-after-fetch).
 */
export function classifyStepType(
  def: StepTypeDef,
  accumulated: Set<string>,
  prevCat: string | null,
): ClassifyResult {
  const reqs = def.compat?.requires ?? [];
  let needsSetup = false;
  let firstUnmetWarn: string | null = null;
  let satFlow = 0;

  for (const r of reqs) {
    const satisfiedByFlow = r.any_of.some((c) => accumulated.has(c));
    if (satisfiedByFlow) {
      satFlow++;
    } else {
      // Not satisfied by flow → needs-setup (ambient is not considered here;
      // the picker never has live ambient state from the broker — we always
      // show the warning so the user knows the dependency).
      needsSetup = true;
      if (firstUnmetWarn === null) firstUnmetWarn = r.warn;
    }
  }

  if (needsSetup) {
    return { bucket: "needs-setup", unmetWarn: firstUnmetWarn };
  }

  if (reqs.length > 0 && satFlow > 0) {
    // All requirements satisfied by earlier flow steps.
    return { bucket: "recommended", unmetWarn: null };
  }

  // No outstanding requirements — apply natural-follow-on heuristics.
  if (def.category === "action" && prevCat === "condition") {
    return { bucket: "recommended", unmetWarn: null };
  }
  if (def.category === "condition" && prevCat === "fetch") {
    return { bucket: "recommended", unmetWarn: null };
  }

  return { bucket: "available", unmetWarn: null };
}

// ---------------------------------------------------------------------------
// Partition helper used by the picker
// ---------------------------------------------------------------------------

export type BucketedStep = {
  def: StepTypeDef;
  result: ClassifyResult;
};

export type BucketedGroups = {
  recommended: BucketedStep[];
  available: BucketedStep[];
  needsSetup: BucketedStep[];
};

/**
 * Partition a list of visible step-type defs into the three buckets, given the
 * steps already in the workflow BEFORE insertIndex.
 *
 * `priorSteps` — workflow.steps.slice(0, insertIndex), already in index order.
 * `catalogMap` — Map<step_type, StepTypeDef> for O(1) lookup.
 */
export function partitionIntoBuckets(
  visibleDefs: StepTypeDef[],
  priorSteps: Step[],
  catalogMap: Map<string, StepTypeDef>,
): BucketedGroups {
  const accumulated = accumulateCaps(priorSteps, catalogMap);
  const lastPriorStep = priorSteps[priorSteps.length - 1];
  const prevCat = lastPriorStep != null
    ? (catalogMap.get(lastPriorStep.step_type)?.category ?? null)
    : null;

  const out: BucketedGroups = { recommended: [], available: [], needsSetup: [] };

  for (const def of visibleDefs) {
    const result = classifyStepType(def, accumulated, prevCat);
    if (result.bucket === "recommended") out.recommended.push({ def, result });
    else if (result.bucket === "needs-setup") out.needsSetup.push({ def, result });
    else out.available.push({ def, result });
  }

  return out;
}
