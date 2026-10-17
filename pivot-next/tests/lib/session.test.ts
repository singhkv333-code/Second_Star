/**
 * A refusal from Pivot's API signs a person out only when the account
 * service says the session is over. Before this, any 401 wiped the session
 * and reloaded /login.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const SESSION = "charto:auth:token"; // lib/session.ts SESSION_KEY

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
    localStorage.setItem(SESSION, "tok");
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

  it("keeps a session the account service still knows", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ user: { id: 5 } }))));
    const s = await fresh();
    expect(await s.handleUnauthorized()).toBe(false);
    expect(localStorage.getItem(SESSION)).toBe("tok");
    expect(href).toBe("");
  });

  it("keeps the session when the account service cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    const s = await fresh();
    expect(await s.handleUnauthorized()).toBe(false);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));
    const t = await fresh();
    expect(await t.handleUnauthorized()).toBe(false);
    expect(localStorage.getItem("pivot_jwt")).toBe("tok");
  });

  it("signs out, back to this page, when the session is gone", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ user: null }))));
    const s = await fresh();
    expect(await s.handleUnauthorized()).toBe(true);
    expect(localStorage.getItem(SESSION)).toBeNull();
    expect(localStorage.getItem("pivot_jwt")).toBeNull();
    expect(href).toBe("/login?next=%2Fstock%2FRELIANCE");
  });

  it("leaves a guest where they are: no session, nothing to sign out of", async () => {
    // The open workspace (2026-10-17): a signed-out visitor's refused request
    // is just that request, never a trip to /login.
    localStorage.removeItem(SESSION);
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const s = await fresh();
    expect(await s.handleUnauthorized()).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
    expect(href).toBe("");
  });

  it("asks once for a burst of refusals", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ user: { id: 5 } })));
    vi.stubGlobal("fetch", fetch);
    const s = await fresh();
    await Promise.all([s.handleUnauthorized(), s.handleUnauthorized(), s.handleUnauthorized()]);
    await s.handleUnauthorized();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
