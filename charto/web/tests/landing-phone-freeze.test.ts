import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/*
 * Under 720px the landing's planet is `display:none`, so its canvas measures
 * 0×0 and the halftone grid steps by R/19 = 0: the loop never ends and every
 * phone freezes on load. Fixed in b38a57b3, removed by accident in aabac23b,
 * and phones froze again. These two guards must stay.
 */
const frame = readFileSync(join(__dirname, "../components/landing/v3/frame.tsx"), "utf8");

describe("landing phone freeze guards", () => {
  it("never paints a zero-size canvas", () => {
    expect(frame).toMatch(/if \(w > 0 && h > 0\) drawRef\.current\(ctx, t, w, h\)/);
  });

  it("refuses a zero grid step in the planet", () => {
    expect(frame).toMatch(/const s = R \/ 19;\s*\n\s*if \(!\(s > 0\)\) return;/);
  });
});
