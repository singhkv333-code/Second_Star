import { describe, expect, it } from "vitest";
import { returnTone } from "@/lib/agentsApi";

describe("returnTone", () => {
  it("does not paint an unknown return as a gain", () => {
    // The bug this pins: `(return_pct ?? 0) >= 0` read a MISSING return as
    // zero, and zero as a gain, so an agent with no forward-test record
    // rendered a profit-green sparkline under a "—". Two cards on the live
    // Agents tab looked like winners on no evidence at all.
    expect(returnTone(null)).toBe("unknown");
    expect(returnTone(undefined)).toBe("unknown");
  });

  it("treats a non-finite return as unknown rather than as a direction", () => {
    expect(returnTone(Number.NaN)).toBe("unknown");
    expect(returnTone(Number.POSITIVE_INFINITY)).toBe("unknown");
  });

  it("still reads a real flat return as a gain, which is the honest call", () => {
    // Zero is a RESULT, unlike null: the agent traded and ended level.
    expect(returnTone(0)).toBe("profit");
  });

  it("reads signed returns the obvious way", () => {
    expect(returnTone(4.3)).toBe("profit");
    expect(returnTone(-4.3)).toBe("loss");
    expect(returnTone(-0.0001)).toBe("loss");
  });
});
