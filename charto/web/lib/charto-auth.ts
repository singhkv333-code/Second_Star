"use client";

/**
 * charto-auth — the account the CHART is signed in to, read from the stock
 * page.
 *
 * The company page runs Pivot's components, so reaching for Pivot's
 * `getMe()`/`logoutUser()` looks right and is not: the two apps keep separate
 * sessions. Pivot's client reads `pivot_jwt` and expects the profile at the
 * top level; charto's dataserver reads `charto:auth:token` and answers
 * `{ user: { id, email, name } | null }`. Point the first at the second and
 * every visitor is "Account", signed in or not, and "Log out" clears a token
 * charto never issued while the charto session it was meant to end survives.
 *
 * So this file is to `preview/js/auth.js` what `chart-watchlists.ts` is to the
 * chart's watchlist panel: the same record, spelled once more in TypeScript.
 * Same storage key, same header, same endpoints — deliberately, so a session
 * started on the chart is the session the company page sees.
 */

import { useEffect, useState } from "react";

/** The exact localStorage key used by charto/preview/js/auth.js. */
export const CHARTO_TOKEN_STORAGE_KEY = "charto:auth:token";

export type ChartoUser = {
  id: number;
  email: string;
  name: string | null;
};

/** auth.js's own derivation, repeated for the same reason it repeats it in
 *  chat.js and main.js: same-origin behind the VM's nginx, explicit port in
 *  local dev. Notably NOT `NEXT_PUBLIC_PIVOT_API_BASE` — that is `/api`, and
 *  charto answers auth on bare `/auth/*`; `/api/auth/me` is a 404. */
function apiBase(): string {
  if (typeof window === "undefined") return "";
  return ["localhost", "127.0.0.1"].includes(window.location.hostname)
    ? "http://127.0.0.1:5174"
    : "";
}

export function readChartoToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(CHARTO_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function clearChartoToken(): void {
  try {
    localStorage.removeItem(CHARTO_TOKEN_STORAGE_KEY);
  } catch {
    /* a browser with storage denied is signed out already */
  }
}

/** `GET /auth/me`. Answers "nobody" for every failure, which is what the
 *  dataserver does too: charto is fully usable signed out, so a blip must
 *  never leave the header in a spinner or bounce anyone to a sign-in wall. */
export async function fetchChartoUser(): Promise<ChartoUser | null> {
  const token = readChartoToken();
  if (!token) return null;
  try {
    const response = await fetch(`${apiBase()}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) return null;
    const data = (await response.json()) as { user?: ChartoUser | null };
    const user = data.user ?? null;
    // A token the server no longer honours comes back as a 200 with no user.
    // Drop it, or the header offers "Sign out" for a session that is gone.
    if (!user) clearChartoToken();
    return user;
  } catch {
    return null;
  }
}

/** `POST /auth/logout`, then forget the token regardless — the same
 *  best-effort order as auth.js, because a network failure must not be able
 *  to keep someone signed in on their own machine. */
export async function logoutCharto(): Promise<void> {
  const token = readChartoToken();
  if (token) {
    try {
      await fetch(`${apiBase()}/auth/logout`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: "{}",
      });
    } catch {
      /* local sign-out regardless */
    }
  }
  clearChartoToken();
}

/** The signed-in charto user, or null. Null on the server and on first paint,
 *  so the header renders its signed-out shape and never blocks the page. */
export function useChartoUser(): ChartoUser | null {
  const [user, setUser] = useState<ChartoUser | null>(null);
  useEffect(() => {
    let live = true;
    void fetchChartoUser().then((result) => {
      if (live) setUser(result);
    });
    return () => {
      live = false;
    };
  }, []);
  return user;
}
