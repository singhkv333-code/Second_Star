/**
 * Minimal JSON Schema (draft 2020-12) → Zod adapter.
 *
 * Scope per Day 2 brief:
 *   - Supported types: `string`, `number`, `integer`, `boolean`, `enum`, `object`.
 *   - `required` honoured at the object level.
 *   - `array` and `$ref` are explicitly rejected — they shipped as v3 work
 *     so the StepConfigDrawer never silently drops fields it cannot render.
 *
 * Why hand-rolled? The Zod ecosystem has several JSON-Schema converters but
 * they bring in megabyte-scale dependencies for what amounts to twenty lines
 * of switching here. The v1 step-type catalog has a tightly bounded surface
 * (see docs/API_CONTRACT.md §8.1) — the dial-in cost is one tiny module that
 * we own and can extend deterministically.
 *
 * Returns `{ schema, fields }` where `fields` is the ordered list the form
 * renderer uses to lay out controls (we keep it because `z.object().shape`
 * loses property ordering on iteration in some browsers).
 */

import { z } from "zod";
import type { ConfigSchema } from "@/lib/types";

// ---------------------------------------------------------------------------
// Field descriptor — what the form renderer needs to draw a control
// ---------------------------------------------------------------------------

export type FieldKind =
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "enum"
  | "object";

export type FormField = {
  /** JSON property name (the form key). */
  name: string;
  kind: FieldKind;
  required: boolean;
  /** From JSON Schema `title`, fallback to the property name humanised. */
  label: string;
  /** From JSON Schema `description`. */
  description?: string;
  /** From JSON Schema `default` — used as placeholder / initial value. */
  default?: unknown;
  /** Enum options when `kind === "enum"`. Strings or numbers. */
  enumValues?: ReadonlyArray<string | number>;
  /** Numeric bounds (passed to zod). */
  minimum?: number;
  maximum?: number;
};

export type SchemaConversion = {
  /** Zod object schema for the whole config. */
  schema: z.ZodTypeAny;
  /** Ordered field descriptors for the renderer. */
  fields: FormField[];
};

// ---------------------------------------------------------------------------
// Public entry
// ---------------------------------------------------------------------------

export class UnsupportedSchemaError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "UnsupportedSchemaError";
  }
}

export function jsonSchemaToZod(schema: ConfigSchema): SchemaConversion {
  if (schema.type !== "object") {
    throw new UnsupportedSchemaError(
      `Top-level schema must be type "object" (got ${String(schema.type)})`,
    );
  }

  const properties = (schema.properties ?? {}) as Record<string, unknown>;
  const required = Array.isArray(schema.required)
    ? new Set<string>(schema.required as string[])
    : new Set<string>();

  const shape: Record<string, z.ZodTypeAny> = {};
  const fields: FormField[] = [];

  for (const [name, raw] of Object.entries(properties)) {
    if (!isPlainObject(raw)) {
      throw new UnsupportedSchemaError(
        `Property "${name}" is not a JSON Schema object`,
      );
    }
    const propSchema = raw as Record<string, unknown>;
    rejectUnsupported(name, propSchema);

    const isRequired = required.has(name);
    const { zod, field } = convertProperty(name, propSchema, isRequired);
    shape[name] = zod;
    fields.push(field);
  }

  return { schema: z.object(shape), fields };
}

// ---------------------------------------------------------------------------
// Per-property conversion
// ---------------------------------------------------------------------------

type PropertyResult = { zod: z.ZodTypeAny; field: FormField };

function convertProperty(
  name: string,
  prop: Record<string, unknown>,
  required: boolean,
): PropertyResult {
  const label = stringOr(prop.title, humaniseKey(name));
  const description = typeof prop.description === "string" ? prop.description : undefined;
  const def = prop.default;

  // Enum first — `type` is optional when `enum` is present.
  if (Array.isArray(prop.enum)) {
    const values = prop.enum.filter(
      (v): v is string | number =>
        typeof v === "string" || typeof v === "number",
    );
    if (values.length === 0) {
      throw new UnsupportedSchemaError(
        `Property "${name}" has an empty or unsupported enum`,
      );
    }
    const zodEnum =
      typeof values[0] === "string"
        ? z.enum(values as [string, ...string[]])
        : z.union(
            values.map((v) => z.literal(v)) as unknown as readonly [
              z.ZodTypeAny,
              z.ZodTypeAny,
              ...z.ZodTypeAny[],
            ],
          );
    return {
      zod: applyOptional(zodEnum, required, def),
      field: {
        name,
        kind: "enum",
        required,
        label,
        description,
        default: def,
        enumValues: values,
      },
    };
  }

  const type = prop.type;

  switch (type) {
    case "string":
      return {
        zod: applyOptional(buildStringZod(prop, required), required, def),
        field: { name, kind: "string", required, label, description, default: def },
      };

    case "number":
    case "integer": {
      const min = numberOrUndef(prop.minimum);
      const max = numberOrUndef(prop.maximum);
      let z1: z.ZodNumber = z.number();
      if (type === "integer") z1 = z1.int();
      if (min !== undefined) z1 = z1.min(min);
      if (max !== undefined) z1 = z1.max(max);
      return {
        zod: applyOptional(z1, required, def),
        field: {
          name,
          kind: type,
          required,
          label,
          description,
          default: def,
          minimum: min,
          maximum: max,
        },
      };
    }

    case "boolean":
      return {
        zod: applyOptional(z.boolean(), required, def),
        field: { name, kind: "boolean", required, label, description, default: def },
      };

    case "object":
      // Free-form object — the form renderer treats this as a JSON textarea.
      // We accept any object; deeper validation belongs server-side.
      return {
        zod: applyOptional(
          z.record(z.string(), z.unknown()),
          required,
          def,
        ),
        field: { name, kind: "object", required, label, description, default: def },
      };

    case undefined:
      // No `type` and no `enum` — treat as free-form value (string-coerced).
      // condition.numeric `left` / `right` arrive in this shape so refs and
      // numbers both pass through.
      return {
        zod: required ? z.unknown() : z.unknown().optional(),
        field: { name, kind: "string", required, label, description, default: def },
      };

    default:
      throw new UnsupportedSchemaError(
        `Property "${name}" has unsupported type "${String(type)}"`,
      );
  }
}

function buildStringZod(
  prop: Record<string, unknown>,
  required: boolean,
): z.ZodTypeAny {
  let s = z.string();
  // Required strings must not be empty — react-hook-form treats untouched
  // string fields as `""`, and we don't want to ship a missing value.
  if (required) s = s.min(1, "Required");
  const min = numberOrUndef(prop.minLength);
  const max = numberOrUndef(prop.maxLength);
  if (min !== undefined) s = s.min(min);
  if (max !== undefined) s = s.max(max);
  return s;
}

function applyOptional<T extends z.ZodTypeAny>(
  zod: T,
  required: boolean,
  _def: unknown,
): z.ZodTypeAny {
  if (required) return zod;
  // Optional: accept undefined OR an empty string (form initial state) and
  // map empty strings to undefined so the submitted payload stays clean.
  return z.preprocess(
    (val) => (val === "" || val === undefined ? undefined : val),
    zod.optional(),
  );
}

// ---------------------------------------------------------------------------
// Reject array / $ref outright
// ---------------------------------------------------------------------------

function rejectUnsupported(
  name: string,
  prop: Record<string, unknown>,
): void {
  if ("$ref" in prop) {
    throw new UnsupportedSchemaError(
      `Property "${name}" uses $ref — not supported in v1 (deferred to v3)`,
    );
  }
  if (prop.type === "array") {
    throw new UnsupportedSchemaError(
      `Property "${name}" is an array — not supported in v1 (deferred to v3)`,
    );
  }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function stringOr(v: unknown, fallback: string): string {
  return typeof v === "string" && v.length > 0 ? v : fallback;
}

function numberOrUndef(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function humaniseKey(key: string): string {
  return key
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

/**
 * Derive a default config object from a JSON schema by reading each
 * property's `default`. Properties without a `default` are dropped so the
 * created step starts with the minimum viable shape (required ones can be
 * filled in via the StepConfigDrawer).
 */
export function defaultConfigFromSchema(
  schema: ConfigSchema,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const properties = (schema.properties ?? {}) as Record<string, unknown>;
  for (const [name, raw] of Object.entries(properties)) {
    if (!isPlainObject(raw)) continue;
    if ("default" in raw) {
      out[name] = (raw as Record<string, unknown>).default;
    }
  }
  return out;
}
