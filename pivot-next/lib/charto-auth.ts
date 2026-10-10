"use client";

/**
 * charto-auth — the CHART's session, read from the shell.
 *
 * The shell signs into Pivot (`pivot_jwt`), but a broker connection has to be
 * stored where the thing that PLACES the orders can read it: charto's
 * `charto_users.db`, keyed by a charto user id. The two user tables are
 * disjoint, so the broker page authenticates against charto's session rather
 * than the shell's — same key and header `charto/preview/js/auth.js` uses.
 */

export const CHARTO_TOKEN_STORAGE_KEY = "charto:auth:token";

export function readChartoToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(CHARTO_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

/**
 * Development only: if the shell is signed in but the chart is not, ask
 * `/dev/charto-session` for the chart session of the dev account named in
 * .env.local. Anywhere else, or for any other account, it is a no-op.
 */
export async function ensureDevChartoSession(): Promise<void> {
  if (process.env.NODE_ENV !== "development" || typeof window === "undefined") return;
  if (readChartoToken()) return;
  let jwt: string | null = null;
  try {
    jwt = localStorage.getItem("pivot_jwt");
  } catch {
    return;
  }
  if (!jwt) return;
  try {
    const r = await fetch("/dev/charto-session", { headers: { Authorization: `Bearer ${jwt}` }, cache: "no-store" });
    if (!r.ok) return;
    const { token } = (await r.json()) as { token?: string };
    if (token) localStorage.setItem(CHARTO_TOKEN_STORAGE_KEY, token);
  } catch {
    /* the chart stays signed out, as it would without this */
  }
}
