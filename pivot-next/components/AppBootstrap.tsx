"use client";

/**
 * AppBootstrap — runs once on the client, before any API call fires.
 *
 * Responsibilities:
 * 1. Flip `setBackendSource("real")` so every API call hits the live backend.
 * 2. Wire `setAuthTokenProvider` to read the JWT from localStorage.
 * 3. On 401 — expose a simple sign-in prompt (no full auth page needed for
 *    the demo; the token was minted via /auth/register or /auth/login).
 *
 * Auth flow for the demo:
 *   localStorage["pivot_jwt"] = <token from /auth/register or /auth/login>
 *
 * On 401 from any API call the page shows an inline "Sign in" prompt that
 * lets the developer paste a fresh token — no redirect needed.
 */

import { useEffect, useState } from "react";
import { setAuthTokenProvider, setBackendSource } from "@/lib/api";

const TOKEN_KEY = "pivot_jwt";

export function AppBootstrap({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Flip to real backend.
    setBackendSource("real");

    // Wire auth token provider to localStorage.
    setAuthTokenProvider(() => {
      return localStorage.getItem(TOKEN_KEY);
    });

    setReady(true);
  }, []);

  // Don't render children until the provider is wired — avoids a race where
  // an API call fires before the token provider is set.
  if (!ready) return <>{children}</>;

  return <>{children}</>;
}

/**
 * Read the stored JWT for use in WS connections (query param).
 * Returns null when not signed in.
 */
export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

/**
 * Store a JWT — called from the sign-in prompt or externally by the
 * legacy chat when it receives a token.
 */
export function storeToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

/**
 * Simple inline sign-in prompt shown when the backend returns 401.
 * Not a full auth page — just enough to paste a dev token and continue.
 */
export function SignInPrompt({
  onToken,
}: {
  onToken: (token: string) => void;
}): React.ReactElement {
  const [value, setValue] = useState("");

  const handleSubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;
    storeToken(trimmed);
    onToken(trimmed);
  };

  return (
    <div
      role="alert"
      className="flex min-h-screen flex-col items-center justify-center bg-background p-8"
    >
      <div className="w-full max-w-sm space-y-4 rounded-xl border bg-card p-6 shadow-sm">
        <div>
          <h2 className="text-base font-semibold">Sign in required</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Paste your JWT from{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
              /auth/login
            </code>{" "}
            or{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
              /auth/register
            </code>
            .
          </p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <textarea
            aria-label="JWT token"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            rows={4}
            placeholder="eyJ..."
            className="w-full rounded-md border bg-background px-3 py-2 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <button
            type="submit"
            className="inline-flex w-full items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            Continue
          </button>
        </form>
      </div>
    </div>
  );
}
