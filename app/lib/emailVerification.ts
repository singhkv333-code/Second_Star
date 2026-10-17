/**
 * Confirming the account's email (dataserver/auth_guard.py).
 *
 *   GET  /auth/me              user.email_verified: true | false | null
 *   POST /auth/verify          {token} from the emailed link
 *   POST /auth/verify/resend   another link, a few an hour at most
 *
 * Same-origin fetches, like the password reset page: these routes belong to
 * the chart server, which the app reaches at its own origin. `requestLegacy`
 * would send them to Pivot's API instead, which has none of them.
 */

import { getStoredToken } from "@/components/AppBootstrap";

async function call<T>(path: string, body?: Record<string, unknown>): Promise<{ ok: boolean; data: T & { error?: string } }> {
  const token = getStoredToken();
  try {
    const res = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = (await res.json().catch(() => ({}))) as T & { error?: string };
    return { ok: res.ok, data };
  } catch {
    return { ok: false, data: { error: "Could not reach Pivot. Check your connection and try again." } as T & { error?: string } };
  }
}

/** True, false, or null when the server cannot say. */
export async function emailVerified(): Promise<boolean | null> {
  const { ok, data } = await call<{ user?: { email_verified?: boolean | null } | null }>("/auth/me");
  const v = ok ? data.user?.email_verified : null;
  return typeof v === "boolean" ? v : null;
}

export async function resendVerification(): Promise<{ ok: boolean; message?: string }> {
  const { ok, data } = await call<{ already?: boolean }>("/auth/verify/resend", {});
  if (!ok) return { ok: false, message: data.error };
  return { ok: true, message: data.already ? "Your email is already confirmed." : undefined };
}

export async function confirmEmail(token: string): Promise<{ ok: boolean; email?: string; message?: string }> {
  const { ok, data } = await call<{ email?: string }>("/auth/verify", { token });
  return ok ? { ok: true, email: data.email } : { ok: false, message: data.error };
}
