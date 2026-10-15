"use client";

/**
 * session — when a refusal really means "you are signed out".
 *
 * The app holds ONE session: Charto's token (`charto:auth:token`, mirrored
 * in `pivot_jwt`), which the chart, the shell and Pivot's API all accept.
 * Pivot's API checks it by reading Charto's store and then Pivot's database,
 * and until now any refusal from it (a 401) signed the person out: tokens
 * wiped, `/login` reloaded. Most of those refusals were not about the session
 * at all: the database was unreachable, or the account was not linked yet.
 * On a laptop the database firewall does not admit, that was every request.
 *
 * So a 401 is checked with the session's owner before anything is cleared.
 * Charto's `/auth/me` answers `{user}` for a live session and `{user: null}`
 * for a dead one; only the second signs the person out. An answer that does
 * not come back (a network fault, a 5xx) is not proof of anything, so it
 * keeps the session.
 */

import { loginForHere } from "@/lib/returnTo";

export const TOKEN_KEY = "pivot_jwt";
export const REFRESH_KEY = "pivot_refresh";
export const CHARTO_KEY = "charto:auth:token";

function read(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** The token every request should carry: the shell's copy, else the chart's. */
export function sessionToken(): string | null {
  return read(TOKEN_KEY) || read(CHARTO_KEY);
}

/** Forget the session on this device (both halves of the product). */
export function clearSession(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
    window.localStorage.removeItem(CHARTO_KEY);
  } catch {
    /* storage denied */
  }
}

let checking: Promise<boolean> | null = null;
let lastAlive = 0;

/** Is the Charto session still live? True on any doubt. One check at a time,
 *  and a confirmed answer holds for 30 s so a burst of 401s asks once. */
export function sessionAlive(): Promise<boolean> {
  const token = read(CHARTO_KEY);
  if (!token) return Promise.resolve(false);
  if (Date.now() - lastAlive < 30_000) return Promise.resolve(true);
  if (checking) return checking;
  checking = (async () => {
    try {
      const res = await fetch("/auth/me", {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (!res.ok) return true;
      const d = (await res.json().catch(() => null)) as { user?: unknown } | null;
      if (d && d.user) {
        lastAlive = Date.now();
        return true;
      }
      return d === null; // unreadable reply: not proof; {user: null}: gone
    } catch {
      return true;
    }
  })().finally(() => {
    checking = null;
  });
  return checking;
}

let leaving = false;

/**
 * A request came back 401. Sign out only if the session is really over, and
 * then to /login with this page in `?next=`, so signing in returns here.
 * Resolves true when the person is being signed out.
 */
export async function handleUnauthorized(): Promise<boolean> {
  if (leaving) return true;
  if (await sessionAlive()) return false;
  leaving = true;
  clearSession();
  window.location.href = loginForHere();
  return true;
}

/** The reason Pivot's API gave for refusing a live session, if any
 *  (backend/main.py `_explain_401`): "link_required" or "store_unavailable". */
export function authReason(res: Response): string | null {
  return res.headers.get("X-Auth-Reason");
}

export const LINK_REQUIRED_EVENT = "pivot:link-required";

/** Pivot's API says this session's account is not linked yet. Said once per
 *  page load; the shell shows the notice (AppBootstrap). */
let linkNoticeSent = false;
export function noteLinkRequired(): void {
  if (linkNoticeSent || typeof window === "undefined") return;
  linkNoticeSent = true;
  window.dispatchEvent(new CustomEvent(LINK_REQUIRED_EVENT));
}
