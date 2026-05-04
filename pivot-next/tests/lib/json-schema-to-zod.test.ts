import { describe, expect, it } from "vitest";
import {
  defaultConfigFromSchema,
  jsonSchemaToZod,
  UnsupportedSchemaError,
} from "@/lib/json-schema-to-zod";
import { MOCK_CATALOG } from "@/lib/mock-catalog";
import type { ConfigSchema } from "@/lib/types";

describe("jsonSchemaToZod", () => {
  it("converts every v1 step type's config_schema without throwing", () => {
    expect(MOCK_CATALOG.step_types.length).toBe(24);
    for (const def of MOCK_CATALOG.step_types) {
      expect(() => jsonSchemaToZod(def.config_schema)).not.toThrow();
    }
  });

  it("emits the expected set of fields for trigger.schedule", () => {
    const def = MOCK_CATALOG.step_types.find(
      (s) => s.step_type === "trigger.schedule",
    );
    expect(def).toBeDefined();
    const { fields, schema } = jsonSchemaToZod(def!.config_schema);
    expect(fields.map((f) => f.name)).toEqual(["cron", "timezone"]);
    expect(fields.every((f) => f.required)).toBe(true);
    expect(
      schema.safeParse({ cron: "55 15 * * 1-5", timezone: "Asia/Kolkata" })
        .success,
    ).toBe(true);
    expect(schema.safeParse({ cron: "55 15 * * 1-5" }).success).toBe(false);
  });

  it("validates enum values for action.place_order.side", () => {
    const def = MOCK_CATALOG.step_types.find(
      (s) => s.step_type === "action.place_order",
    );
    const { schema } = jsonSchemaToZod(def!.config_schema);
    const ok = schema.safeParse({
      symbol: "RELIANCE",
      side: "buy",
      quantity: 1,
      order_type: "market",
    });
    expect(ok.success).toBe(true);
    const bad = schema.safeParse({
      symbol: "RELIANCE",
      side: "yolo",
      quantity: 1,
      order_type: "market",
    });
    expect(bad.success).toBe(false);
  });

  it("rejects array properties with UnsupportedSchemaError", () => {
    const schema: ConfigSchema = {
      type: "object",
      properties: { items: { type: "array" } },
      required: [],
    };
    expect(() => jsonSchemaToZod(schema)).toThrow(UnsupportedSchemaError);
  });

  it("rejects $ref properties with UnsupportedSchemaError", () => {
    const schema: ConfigSchema = {
      type: "object",
      properties: { ref: { $ref: "#/definitions/foo" } },
      required: [],
    };
    expect(() => jsonSchemaToZod(schema)).toThrow(UnsupportedSchemaError);
  });

  it("treats integer minimum/maximum bounds", () => {
    const schema: ConfigSchema = {
      type: "object",
      properties: { n: { type: "integer", minimum: 1, maximum: 5 } },
      required: ["n"],
    };
    const { schema: zod } = jsonSchemaToZod(schema);
    expect(zod.safeParse({ n: 3 }).success).toBe(true);
    expect(zod.safeParse({ n: 0 }).success).toBe(false);
    expect(zod.safeParse({ n: 6 }).success).toBe(false);
    expect(zod.safeParse({ n: 2.5 }).success).toBe(false);
  });
});

describe("defaultConfigFromSchema", () => {
  it("picks up `default` values from properties", () => {
    const def = MOCK_CATALOG.step_types.find(
      (s) => s.step_type === "fetch.news",
    );
    const out = defaultConfigFromSchema(def!.config_schema);
    expect(out.limit).toBe(10);
  });

  it("returns an empty object when no defaults are present", () => {
    const def = MOCK_CATALOG.step_types.find(
      (s) => s.step_type === "fetch.portfolio",
    );
    expect(defaultConfigFromSchema(def!.config_schema)).toEqual({});
  });
});
