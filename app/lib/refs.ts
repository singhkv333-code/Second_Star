/**
 * Ref-namespace helpers for inter-step data passing.
 *
 * Allowed ref shapes (per docs/ARCHITECTURE.md §6):
 *   {{ context.<step_index>.<dotted.path> }}
 *   {{ context.webhook_payload.<dotted.path> }}   // only when a trigger.webhook step exists
 *   {{ now }}
 *   {{ workflow.<field> }}                        // id | name | version
 *
 * Anything else is rejected with a clear error so the form doesn't ship a
 * config the engine will reject at run time.
 */

import type { Step } from "@/lib/types";

export const WORKFLOW_FIELDS = ["id", "name", "version"] as const;

export type RefSuggestion = {
  /** Final string the user inserts, e.g. "context.1.buying_power". */
  value: string;
  /** Human label shown in the picker, e.g. "context.1 — fetch.portfolio". */
  label: string;
  /** Tag rendered next to the suggestion. */
  category: "context" | "webhook" | "now" | "workflow";
};

const REF_REGEX = /\{\{\s*([^}]+?)\s*\}\}/g;

/**
 * Extract all ref expressions from a string. Returns the inner namespace
 * (everything between `{{` and `}}`), trimmed.
 */
export function extractRefs(value: string): string[] {
  const out: string[] = [];
  for (const match of value.matchAll(REF_REGEX)) {
    if (match[1]) out.push(match[1].trim());
  }
  return out;
}

export type RefValidationError = {
  ref: string;
  reason: string;
};

/**
 * Validate every ref inside a string against the allowed namespaces.
 * `priorSteps` is the list of steps that come before the step using the
 * ref — only those step indices can be referenced via `context.<n>`.
 * `hasWebhookTrigger` is true if step 0 is a `trigger.webhook`.
 */
export function validateRefsInString(
  value: string,
  priorSteps: Step[],
  hasWebhookTrigger: boolean,
): RefValidationError[] {
  const errors: RefValidationError[] = [];
  const validIndices = new Set(priorSteps.map((s) => s.step_index));
  for (const ref of extractRefs(value)) {
    const reason = whyInvalid(ref, validIndices, hasWebhookTrigger);
    if (reason) errors.push({ ref, reason });
  }
  return errors;
}

function whyInvalid(
  ref: string,
  validIndices: Set<number>,
  hasWebhookTrigger: boolean,
): string | null {
  if (ref === "now") return null;

  const parts = ref.split(".");

  if (parts[0] === "context") {
    const second = parts[1];
    if (!second) return "context.<step_index|webhook_payload>.<path>";
    if (second === "webhook_payload") {
      if (!hasWebhookTrigger) {
        return "context.webhook_payload requires a trigger.webhook step at index 0";
      }
      return parts.length >= 3 ? null : "context.webhook_payload.<path>";
    }
    const idx = Number(second);
    if (!Number.isInteger(idx) || idx < 0) {
      return `context.<step_index> must be a non-negative integer (got "${second}")`;
    }
    if (!validIndices.has(idx)) {
      return `step ${idx} is not before this step`;
    }
    return parts.length >= 3 ? null : "context.<step_index>.<path>";
  }

  if (parts[0] === "workflow") {
    const second = parts[1];
    if (!second || !WORKFLOW_FIELDS.includes(second as (typeof WORKFLOW_FIELDS)[number])) {
      return `workflow.<${WORKFLOW_FIELDS.join("|")}>`;
    }
    return null;
  }

  return `unknown namespace "${parts[0] ?? ""}"`;
}

// ---------------------------------------------------------------------------
// Config-level helpers (used by StepTypePicker capability mirror + lint wiring)
// ---------------------------------------------------------------------------

/**
 * Recursively collect all `{{ ... }}` ref expressions from every string leaf
 * in a step config object (or a plain string). Returns the inner namespaces
 * trimmed, with duplicates removed.
 *
 * Works on arbitrarily nested objects/arrays — the config's JSON Schema allows
 * nested structures (e.g. `legs`, `condition`) so we must recurse.
 */
export function extractRefsFromConfig(
  config: Record<string, unknown> | unknown,
): string[] {
  const seen = new Set<string>();

  function walk(value: unknown): void {
    if (typeof value === "string") {
      for (const ref of extractRefs(value)) {
        seen.add(ref);
      }
    } else if (Array.isArray(value)) {
      for (const item of value) {
        walk(item);
      }
    } else if (value !== null && typeof value === "object") {
      for (const v of Object.values(value as Record<string, unknown>)) {
        walk(v);
      }
    }
  }

  walk(config);
  return [...seen];
}

/**
 * Return the set of prior step indices referenced by a step's config.
 *
 * Only `context.<N>.*` refs contribute — `context.webhook_payload.*`,
 * `workflow.*`, and `now` are namespace refs and don't point at step indices.
 *
 * The picker's client capability mirror uses this to understand which prior
 * steps' outputs the step under edit actually consumes, so it can highlight
 * the right buckets even before the debounced /lint call fires.
 */
export function referencedStepIndices(
  config: Record<string, unknown>,
): Set<number> {
  const indices = new Set<number>();
  for (const ref of extractRefsFromConfig(config)) {
    const parts = ref.split(".");
    if (parts[0] === "context" && parts[1] && parts[1] !== "webhook_payload") {
      const idx = Number(parts[1]);
      if (Number.isInteger(idx) && idx >= 0) {
        indices.add(idx);
      }
    }
  }
  return indices;
}

/**
 * Build the suggestion list for the chip picker, given the steps that come
 * BEFORE the step currently being edited and whether a webhook trigger
 * exists in the workflow. Suggestions are returned in priority order.
 */
export function buildRefSuggestions(
  priorSteps: Step[],
  hasWebhookTrigger: boolean,
): RefSuggestion[] {
  const out: RefSuggestion[] = [];

  for (const step of priorSteps) {
    out.push({
      value: `context.${step.step_index}.`,
      label: `context.${step.step_index} — ${step.step_type}`,
      category: "context",
    });
  }

  if (hasWebhookTrigger) {
    out.push({
      value: "context.webhook_payload.",
      label: "context.webhook_payload",
      category: "webhook",
    });
  }

  out.push({
    value: "now",
    label: "now (current ISO timestamp)",
    category: "now",
  });

  for (const field of WORKFLOW_FIELDS) {
    out.push({
      value: `workflow.${field}`,
      label: `workflow.${field}`,
      category: "workflow",
    });
  }

  return out;
}
