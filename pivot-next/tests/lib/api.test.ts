import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  _clearCatalogCache,
  getStepTypes,
  getWorkflow,
  setStepTypesSource,
} from "@/lib/api";
import { isError } from "@/lib/types";
import { MOCK_CATALOG } from "@/lib/mock-catalog";

const fetchMock = vi.fn();

beforeEach(() => {
  _clearCatalogCache();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setStepTypesSource("mock");
});

describe("getStepTypes (mock source)", () => {
  it("returns the inline mock catalog without hitting the network", async () => {
    setStepTypesSource("mock");
    const result = await getStepTypes();
    expect(isError(result)).toBe(false);
    if (!isError(result)) {
      expect(result.data.catalog_version).toBe(MOCK_CATALOG.catalog_version);
      expect(result.data.step_types.length).toBe(MOCK_CATALOG.step_types.length);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("getWorkflow (real fetch wrapper)", () => {
  it("parses a 200 JSON body into { data }", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: "abc",
          name: "demo",
          description: null,
          status: "draft",
          version: 1,
          single_instance: true,
          created_at: "2026-05-01T00:00:00Z",
          updated_at: "2026-05-01T00:00:00Z",
          activated_at: null,
          last_run_at: null,
          next_run_at: null,
          steps: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const result = await getWorkflow("abc");
    expect(isError(result)).toBe(false);
    if (!isError(result)) {
      expect(result.data.id).toBe("abc");
    }
  });

  it("maps a structured backend error envelope into { error }", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: {
            code: "not_found",
            message: "Workflow not found",
          },
        }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      ),
    );
    const result = await getWorkflow("missing");
    expect(isError(result)).toBe(true);
    if (isError(result)) {
      expect(result.error.code).toBe("not_found");
      expect(result.error.message).toBe("Workflow not found");
    }
  });

  it("falls back to a synthetic error when the backend returns non-JSON", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("oops", {
        status: 500,
        headers: { "Content-Type": "text/plain" },
      }),
    );
    const result = await getWorkflow("x");
    expect(isError(result)).toBe(true);
    if (isError(result)) {
      expect(result.error.code).toBe("internal_error");
    }
  });
});
