/**
 * A refusal from Pivot's API signs a person out only when Charto says the
 * session is over. Before this, any 401 wiped the tokens and reloaded /login.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const CHARTO = "charto:auth:token";

// Node's own localStorage global shadows jsdom's here; an in-memory one, as
// in tests/agent-panel/app-bootstrap.test.tsx.
{
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, String(v)),
      removeItem: (k: string) => void store.delete(k),
      clear: () => store.clear(),
    },
  });
  Object.defineProperty(window, "localStorage", { configurable: true, value: globalThis.localStorage });
}

async function fresh() {
  vi.resetModules();
  return import("@/lib/session");
}

describe("handleUnauthorized", () => {
  let href = "";
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem(CHARTO, "tok");
    localStorage.setItem("pivot_jwt", "tok");
    href = "";
    vi.stubGlobal("location", {
      pathname: "/stock/RELIANCE", search: "", hash: "",
      get href() { return href; },
      set href(v: string) { href = v; },
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps a session Charto still knows", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ user: { id: 5 } }))));
    const s = await fresh();
    expect(await s.handleUnauthorized()).toBe(false);
    expect(localStorage.getItem(CHARTO)).toBe("tok");
    expect(href).toBe("");
  });

  it("keeps the session when Charto cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    const s = await fresh();
    expect(await s.handleUnauthorized()).toBe(false);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));
    const t = await fresh();
    expect(await t.handleUnauthorized()).toBe(false);
    expect(localStorage.getItem("pivot_jwt")).toBe("tok");
  });

  it("signs out, back to this page, when Charto says the session is gone", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ user: null }))));
    const s = await fresh();
    expect(await s.handleUnauthorized()).toBe(true);
    expect(localStorage.getItem(CHARTO)).toBeNull();
    expect(localStorage.getItem("pivot_jwt")).toBeNull();
    expect(href).toBe("/login?next=%2Fstock%2FRELIANCE");
  });

  it("signs out a Pivot-only session with no Charto token", async () => {
    localStorage.removeItem(CHARTO);
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const s = await fresh();
    expect(await s.handleUnauthorized()).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("asks Charto once for a burst of refusals", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ user: { id: 5 } })));
    vi.stubGlobal("fetch", fetch);
    const s = await fresh();
    await Promise.all([s.handleUnauthorized(), s.handleUnauthorized(), s.handleUnauthorized()]);
    await s.handleUnauthorized();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
