"use client";

/**
 * Retired: the one Pivot session lives in lib/session.ts. This re-export
 * stays only while lib/billing/api.ts imports it; point that at
 * `sessionToken` from "@/lib/session" and delete this file.
 */

import { SESSION_KEY, sessionToken } from "@/lib/session";

export const CHARTO_TOKEN_STORAGE_KEY = SESSION_KEY;

export function readChartoToken(): string | null {
  return sessionToken();
}
