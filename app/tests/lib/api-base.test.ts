import { afterEach, describe, expect, it, vi } from "vitest";
import { getApiBaseUrl } from "@/lib/api-base";

afterEach(() => vi.unstubAllEnvs());

describe("browser API address", () => {
  it.each([undefined, "", "C:/Program Files/Git/pv/api", "C:\\Program Files\\Git\\pv\\api", "file:///C:/pv/api"])(
    "uses the same-origin API for invalid address %s",
    (base) => {
      vi.stubEnv("NEXT_PUBLIC_PIVOT_API_BASE", base);
      expect(getApiBaseUrl()).toBe("/pv/api");
    },
  );

  it.each(["/pv/api", "/api", "http://127.0.0.1:8000/api", "https://app.pivotnow.in/pv/api"])(
    "preserves valid API address %s",
    (base) => {
      vi.stubEnv("NEXT_PUBLIC_PIVOT_API_BASE", `${base}/`);
      expect(getApiBaseUrl()).toBe(base);
    },
  );
});
