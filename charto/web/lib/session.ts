"use client";

/**
 * session — the one Pivot sign-in, on this device.
 *
 * Pivot has one account system: the account service behind `/auth/*` on
 * this origin (sign-up, sign-in, Google, password reset, email confirmation,
 * sign-out). Signing in gives one session token. Every part of the product
 * carries that same token: the shell, the workspace frame, and Pivot's API,
 * which maps it to the account's data (pivot/backend/auth/accounts.py).
 *
 * The token lives under SESSION_KEY. The key's text is older than the one
 * account system and is kept as it is: the workspace frame reads it too, and
 * renaming it would sign every open session out. `pivot_jwt` and
 * `pivot_refresh` belonged to the API's old sign-in; they are only ever
 * cleared now.
 *
 * WHEN A REQUEST IS REFUSED (401)
 * The account service is asked first. Its `/auth/me` answers `{user}` for a
 * live session and `{user: null}` for a dead one; only the second signs the
 * person out, back to /login with this page in `?next=`. An answer that does
 * not come back (a network fault, a 5xx) is not proof of anything, so it keeps
 * the session. Before this, any 401 wiped the session and reloaded /login.
 */

/** /login for the page the user is on, so signing in comes back here. */
function loginForHere(): string {
  const here = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  return here && here !== "/" ? `/login?next=${encodeURIComponent(here)}` : "/login";
}

export const SESSION_KEY = "charto:auth:token";
const RETIRED_KEYS = ["pivot_jwt", "pivot_refresh"];

function read(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** The session token every request carries, or null when signed out. */
export function sessionToken(): string | null {
  return read(SESSION_KEY);
}

/** Keep the session a sign-in just gave (and drop the retired keys). */
export function saveSession(token: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(SESSION_KEY, token);
    for (const k of RETIRED_KEYS) window.localStorage.removeItem(k);
  } catch {
    /* storage denied */
  }
  lastAlive = Date.now();
}

/** Forget the session on this device. */
export function clearSession(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(SESSION_KEY);
    for (const k of RETIRED_KEYS) window.localStorage.removeItem(k);
  } catch {
    /* storage denied */
  }
}

let checking: Promise<boolean> | null = null;
let lastAlive = 0;

/** Is the session still live? True on any doubt. One check at a time, and a
 *  confirmed answer holds for 30 s so a burst of 401s asks once. */
export function sessionAlive(): Promise<boolean> {
  const token = sessionToken();
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
 * then to /login with this page in `?next=`. Resolves true when signing out.
 */
export async function handleUnauthorized(): Promise<boolean> {
  if (leaving) return true;
  if (await sessionAlive()) return false;
  leaving = true;
  clearSession();
  window.location.href = loginForHere();
  return true;
}
