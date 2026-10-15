/**
 * authToken — the bearer token for the direct-fetch data modules (chat
 * stream, live quotes, portfolio, screener).
 *
 * It is the one Pivot session (lib/session.ts). A session lasts 30 days from
 * sign-in and has nothing to refresh, so this only reads it. The refresh
 * machinery that lived here served the API's old sign-in, which is gone.
 */

import { sessionToken } from "@/lib/session";

/** The session token, or null when signed out. */
export async function getAccessToken(): Promise<string | null> {
  return sessionToken();
}

/** The same, for call sites that cannot await. */
export function getAccessTokenSync(): string | null {
  return sessionToken();
}
