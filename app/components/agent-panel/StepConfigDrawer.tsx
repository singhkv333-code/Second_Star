"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useForm, type FieldErrors, type Path, type Resolver } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, ChevronLeft, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { StepIcon } from "@/components/agent-panel/step-icon";
import { RefChipPicker } from "@/components/agent-panel/RefChipPicker";
import { ConditionBuilder } from "@/components/agent-panel/ConditionBuilder";
import { useDslSchema } from "@/components/agent-panel/use-dsl-schema";
import {
  jsonSchemaToZod,
  type FormField,
} from "@/lib/json-schema-to-zod";
import { validateRefsInString } from "@/lib/refs";
import type {
  DslNode,
  DslSchema,
  ErrorBody,
  Step,
  StepTypeDef,
  Workflow,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export type StepConfigDrawerProps = {
  step: Step;
  catalogEntry: StepTypeDef;
  workflow: Workflow;
  /** Called with the new config object on Save. */
  onSave: (config: Record<string, unknown>) => Promise<{ error?: ErrorBody } | void> | { error?: ErrorBody } | void;
  onClose: () => void;
};

type FormShape = Record<string, unknown>;

/**
 * Secondary drawer that opens over the AgentPanel when a step card is
 * clicked. The form is generated entirely from the step's `config_schema`
 * — no hardcoded forms.
 *
 * Keyboard:
 *   - Esc closes the drawer (does NOT close the parent AgentPanel — the parent
 *     listens on `window.keydown` and we `stopPropagation` to scope this Esc).
 *   - Cmd/Ctrl + Enter saves.
 */
export function StepConfigDrawer({
  step,
  catalogEntry,
  workflow,
  onSave,
  onClose,
}: StepConfigDrawerProps): React.ReactElement {
  const conversion = useMemo(() => {
    try {
      return { ok: true as const, ...jsonSchemaToZod(catalogEntry.config_schema) };
    } catch (err) {
      return {
        ok: false as const,
        error:
          err instanceof Error
            ? err.message
            : "Could not parse this step's config schema",
      };
    }
  }, [catalogEntry.config_schema]);

  // Hooks must run unconditionally — guard with a no-op resolver when the
  // schema couldn't be parsed.
  const resolver = useMemo<Resolver<FormShape>>(
    () =>
      conversion.ok
        ? (zodResolver(conversion.schema) as unknown as Resolver<FormShape>)
        : (async () => ({ values: {}, errors: {} })),
    [conversion],
  );

  const defaultValues = useMemo<FormShape>(() => {
    if (!conversion.ok) return {};
    return buildInitialValues(conversion.fields, step.config);
  }, [conversion, step.config]);

  const form = useForm<FormShape>({ resolver, defaultValues, mode: "onSubmit" });
  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<ErrorBody | null>(null);
  const [refError, setRefError] = useState<string | null>(null);
  const submittedAt = useRef(0);

  // Step changed → re-seed the form.
  useEffect(() => {
    form.reset(defaultValues);
    setApiError(null);
    setRefError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step.id, defaultValues]);

  const priorSteps = useMemo(
    () => workflow.steps.filter((s) => s.step_index < step.step_index),
    [workflow.steps, step.step_index],
  );
  const hasWebhookTrigger = workflow.steps[0]?.step_type === "trigger.webhook";

  // DSL schema for the visual ConditionBuilder — loads in the background. For a
  // compound step (trigger.compound / trigger.exit_compound / condition.compound)
  // the `entry` config field renders as the tree builder instead of raw JSON;
  // until the schema loads (or for any other step) it falls back to the JSON
  // object editor.
  const dslSchemaState = useDslSchema();
  const dslSchema: DslSchema | null =
    dslSchemaState.status === "ready" ? dslSchemaState.schema : null;
  const treeField = dslSchema
    ? dslSchema.tree_fields[catalogEntry.step_type] ?? null
    : null;
  const treeFieldName = treeField?.field ?? null;
  const treeFieldMode: "entry" | "exit" = treeField?.mode ?? "entry";

  const handleSubmit = form.handleSubmit(async (values) => {
    if (!conversion.ok) return;

    // Final ref-namespace gate across every string field.
    const refIssues: string[] = [];
    for (const field of conversion.fields) {
      if (field.kind !== "string") continue;
      const raw = values[field.name];
      if (typeof raw !== "string") continue;
      const errors = validateRefsInString(raw, priorSteps, hasWebhookTrigger);
      for (const e of errors) {
        refIssues.push(`{{ ${e.ref} }}: ${e.reason}`);
      }
    }
    if (refIssues.length > 0) {
      setRefError(refIssues.join("; "));
      return;
    }
    setRefError(null);

    setSubmitting(true);
    submittedAt.current = Date.now();
    try {
      const result = await Promise.resolve(onSave(values));
      if (result && typeof result === "object" && "error" in result && result.error) {
        const err = result.error;
        setApiError(err);
        // Highlight the offending field if backend returned `details.field`.
        const field = err.details?.field;
        if (typeof field === "string" && field in values) {
          form.setError(field as Path<FormShape>, { type: "server", message: err.message });
        }
      } else {
        setApiError(null);
        onClose();
      }
    } finally {
      setSubmitting(false);
    }
  });

  // Esc + Cmd/Ctrl+Enter inside the drawer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        e.stopPropagation();
        void handleSubmit();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, handleSubmit]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Configure step: ${catalogEntry.label}`}
      className={cn(
        "absolute inset-y-0 right-0 z-50 flex w-full max-w-[480px] flex-col border-l bg-background shadow-2xl",
        "animate-in slide-in-from-right",
      )}
      data-testid="step-config-drawer"
    >
      <header className="flex items-start justify-between gap-3 border-b px-5 py-4">
        <div className="flex min-w-0 items-start gap-3">
          <button
            type="button"
            aria-label="Back"
            onClick={onClose}
            className="mt-0.5 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          </button>
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted">
            <StepIcon name={catalogEntry.icon} className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Step {step.step_index + 1} · {catalogEntry.step_type}
            </p>
            <h2 className="truncate text-base font-semibold tracking-tight">
              {catalogEntry.label}
            </h2>
            {catalogEntry.description && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                {catalogEntry.description}
              </p>
            )}
          </div>
        </div>
        <button
          type="button"
          aria-label="Close step config"
          onClick={onClose}
          className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </header>

      {!conversion.ok ? (
        <div role="alert" className="flex flex-1 flex-col items-center justify-center px-6 text-center">
          <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
          <p className="text-sm font-medium">Couldn&apos;t render config form</p>
          <p className="mt-1 max-w-sm text-xs text-muted-foreground">{conversion.error}</p>
        </div>
      ) : conversion.fields.length === 0 ? (
        <EmptyConfigState onClose={onClose} />
      ) : (
        <form
          onSubmit={handleSubmit}
          className="flex flex-1 flex-col"
          data-testid="step-config-form"
        >
          <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5">
            {conversion.fields.map((field) => (
              <FormFieldRow
                key={field.name}
                field={field}
                value={form.watch(field.name) as unknown}
                onChange={(v) =>
                  form.setValue(field.name as Path<FormShape>, v as never, {
                    shouldDirty: true,
                  })
                }
                error={extractFieldError(form.formState.errors, field.name)}
                priorSteps={priorSteps}
                hasWebhookTrigger={hasWebhookTrigger}
                dslSchema={dslSchema}
                treeFieldName={treeFieldName}
                treeFieldMode={treeFieldMode}
              />
            ))}
          </div>

          {(apiError || refError) && (
            <div
              role="alert"
              className="border-t border-destructive/30 bg-destructive/10 px-5 py-3 text-xs text-destructive"
            >
              {apiError ? apiError.message : refError}
            </div>
          )}

          <div className="flex items-center justify-end gap-2 border-t px-5 py-3">
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Saving…" : "Save"}
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Field renderers
// ---------------------------------------------------------------------------

type FieldRowProps = {
  field: FormField;
  value: unknown;
  onChange: (v: unknown) => void;
  error?: string;
  priorSteps: Step[];
  hasWebhookTrigger: boolean;
  /** DSL schema for rendering the ConditionBuilder on the compound-tree field. */
  dslSchema: DslSchema | null;
  /** The config field name that holds the compound tree (e.g. "entry"), if any. */
  treeFieldName: string | null;
  treeFieldMode: "entry" | "exit";
};

function FormFieldRow({
  field,
  value,
  onChange,
  error,
  priorSteps,
  hasWebhookTrigger,
  dslSchema,
  treeFieldName,
  treeFieldMode,
}: FieldRowProps): React.ReactElement {
  const fieldId = `field-${field.name}`;
  const isTreeField = Boolean(
    dslSchema && treeFieldName && field.name === treeFieldName,
  );
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <Label htmlFor={fieldId} className="text-sm">
          {field.label}
          {field.required && (
            <span aria-hidden="true" className="ml-1 text-destructive">*</span>
          )}
        </Label>
        {field.kind !== "boolean" && field.default !== undefined && !isTreeField && (
          <span className="text-[10px] text-muted-foreground">
            default: {String(field.default)}
          </span>
        )}
      </div>

      {isTreeField && dslSchema ? (
        <ConditionBuilder
          value={(value as DslNode | null | undefined) ?? null}
          onChange={(node) => onChange(node)}
          mode={treeFieldMode}
          schema={dslSchema}
        />
      ) : (
        renderControl({
          field,
          fieldId,
          value,
          onChange,
          priorSteps,
          hasWebhookTrigger,
          invalid: Boolean(error),
        })
      )}

      {field.description && !error && !isTreeField && (
        <p className="text-[11px] text-muted-foreground">{field.description}</p>
      )}
      {error && (
        <p
          role="alert"
          className="text-[11px] font-medium text-destructive"
          data-testid={`field-error-${field.name}`}
        >
          {error}
        </p>
      )}
    </div>
  );
}

function renderControl({
  field,
  fieldId,
  value,
  onChange,
  priorSteps,
  hasWebhookTrigger,
  invalid,
}: {
  field: FormField;
  fieldId: string;
  value: unknown;
  onChange: (v: unknown) => void;
  priorSteps: Step[];
  hasWebhookTrigger: boolean;
  invalid: boolean;
}): React.ReactElement {
  switch (field.kind) {
    case "boolean":
      return (
        <Switch
          id={fieldId}
          checked={value === true}
          onCheckedChange={(b) => onChange(b)}
          aria-invalid={invalid}
        />
      );

    case "enum": {
      const opts = field.enumValues ?? [];
      const stringValue =
        value === undefined || value === null ? "" : String(value);
      return (
        <Select
          value={stringValue}
          onValueChange={(v) => {
            // Coerce numeric enums back to numbers if the original options
            // were numeric.
            if (typeof opts[0] === "number") onChange(Number(v));
            else onChange(v);
          }}
        >
          <SelectTrigger
            id={fieldId}
            aria-invalid={invalid}
            data-testid={`select-${field.name}`}
          >
            <SelectValue placeholder={
              field.default !== undefined ? String(field.default) : "Select…"
            } />
          </SelectTrigger>
          <SelectContent>
            {opts.map((opt) => (
              <SelectItem key={String(opt)} value={String(opt)}>
                {String(opt)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
    }

    case "number":
    case "integer": {
      const n =
        typeof value === "number" ? String(value) : value === undefined || value === null ? "" : String(value);
      return (
        <Input
          id={fieldId}
          type="number"
          inputMode={field.kind === "integer" ? "numeric" : "decimal"}
          step={field.kind === "integer" ? 1 : "any"}
          min={field.minimum}
          max={field.maximum}
          value={n}
          placeholder={
            field.default !== undefined ? String(field.default) : undefined
          }
          aria-invalid={invalid}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") return onChange(undefined);
            const num = Number(raw);
            onChange(Number.isFinite(num) ? num : raw);
          }}
        />
      );
    }

    case "object": {
      const json =
        typeof value === "string"
          ? value
          : value === undefined
            ? ""
            : JSON.stringify(value, null, 2);
      return (
        <Textarea
          id={fieldId}
          value={json}
          rows={4}
          aria-invalid={invalid}
          placeholder='{ "key": "value" }'
          className="font-mono text-[12px]"
          onChange={(e) => {
            const raw = e.target.value;
            if (raw.trim() === "") return onChange(undefined);
            try {
              onChange(JSON.parse(raw));
            } catch {
              // Hold the raw string until the user finishes typing; zod
              // will reject on submit if it never becomes valid JSON.
              onChange(raw);
            }
          }}
        />
      );
    }

    case "string":
    default: {
      const stringValue =
        value === undefined || value === null
          ? ""
          : typeof value === "string"
            ? value
            : String(value);
      return (
        <RefChipPicker
          id={fieldId}
          value={stringValue}
          onChange={(v) => onChange(v)}
          placeholder={
            field.default !== undefined ? String(field.default) : undefined
          }
          priorSteps={priorSteps}
          hasWebhookTrigger={hasWebhookTrigger}
          aria-invalid={invalid}
        />
      );
    }
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildInitialValues(
  fields: FormField[],
  current: Record<string, unknown>,
): FormShape {
  const out: FormShape = {};
  for (const field of fields) {
    if (field.name in current) {
      out[field.name] = current[field.name];
    } else if (field.default !== undefined) {
      out[field.name] = field.default;
    } else if (field.kind === "boolean") {
      out[field.name] = false;
    } else {
      out[field.name] = "";
    }
  }
  return out;
}

function extractFieldError(
  errors: FieldErrors<FormShape>,
  name: string,
): string | undefined {
  const entry = errors[name];
  if (!entry) return undefined;
  const message = (entry as { message?: unknown }).message;
  if (typeof message === "string" && message.length > 0) return message;
  return "Invalid value";
}

function EmptyConfigState({ onClose }: { onClose: () => void }): React.ReactElement {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-8 text-center">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <span aria-hidden="true">·</span>
      </div>
      <p className="text-sm font-medium">No configuration needed</p>
      <p className="mt-1 max-w-xs text-xs text-muted-foreground">
        This step type runs without any per-step configuration.
      </p>
      <Button className="mt-4" variant="outline" size="sm" onClick={onClose}>
        Close
      </Button>
    </div>
  );
}
