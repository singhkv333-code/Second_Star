import { describe, expect, it } from "vitest";
import {
  buildRefSuggestions,
  extractRefs,
  validateRefsInString,
} from "@/lib/refs";
import type { Step } from "@/lib/types";

const step = (idx: number, type: string): Step => ({
  id: `s${idx}`,
  step_index: idx,
  step_type: type,
  label: null,
  config: {},
});

describe("extractRefs", () => {
  it("extracts trimmed refs from a template string", () => {
    const refs = extractRefs("Hi {{ context.1.buying_power }} and {{now}}");
    expect(refs).toEqual(["context.1.buying_power", "now"]);
  });
});

describe("validateRefsInString", () => {
  const prior = [step(0, "trigger.schedule"), step(1, "fetch.portfolio")];

  it("accepts a valid context.<index>.path", () => {
    expect(
      validateRefsInString("{{ context.1.buying_power }}", prior, false),
    ).toEqual([]);
  });

  it("rejects context referencing a future step", () => {
    const errors = validateRefsInString(
      "{{ context.5.foo }}",
      prior,
      false,
    );
    expect(errors.length).toBe(1);
    expect(errors[0]?.reason).toMatch(/not before/);
  });

  it("rejects an unknown namespace", () => {
    const errors = validateRefsInString("{{ foo.bar }}", prior, false);
    expect(errors.length).toBe(1);
    expect(errors[0]?.reason).toMatch(/unknown namespace/);
  });

  it("accepts now and workflow.<field>", () => {
    expect(validateRefsInString("{{ now }}", prior, false)).toEqual([]);
    expect(
      validateRefsInString("{{ workflow.name }}", prior, false),
    ).toEqual([]);
    const bad = validateRefsInString("{{ workflow.bogus }}", prior, false);
    expect(bad.length).toBe(1);
  });

  it("only allows webhook_payload when a webhook trigger exists", () => {
    const errs = validateRefsInString(
      "{{ context.webhook_payload.symbol }}",
      prior,
      false,
    );
    expect(errs.length).toBe(1);
    const ok = validateRefsInString(
      "{{ context.webhook_payload.symbol }}",
      prior,
      true,
    );
    expect(ok).toEqual([]);
  });
});

describe("buildRefSuggestions", () => {
  it("includes context.<n> for each prior step plus now and workflow.*", () => {
    const prior = [step(0, "trigger.schedule"), step(1, "fetch.portfolio")];
    const out = buildRefSuggestions(prior, false);
    const values = out.map((s) => s.value);
    expect(values).toContain("context.0.");
    expect(values).toContain("context.1.");
    expect(values).toContain("now");
    expect(values).toContain("workflow.id");
    expect(values).toContain("workflow.name");
    expect(values).toContain("workflow.version");
    expect(values).not.toContain("context.webhook_payload.");
  });

  it("includes context.webhook_payload only when hasWebhookTrigger=true", () => {
    const out = buildRefSuggestions([step(0, "trigger.webhook")], true);
    const values = out.map((s) => s.value);
    expect(values).toContain("context.webhook_payload.");
  });
});
