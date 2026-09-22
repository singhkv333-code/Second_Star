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
