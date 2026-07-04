"use client";

/**
 * AppBootstrap — runs once on the client, before any API call fires.
 *
 * Responsibilities:
 * 1. Flip `setBackendSource("real")` so every API call hits the live backend.
 * 2. Wire `setAuthTokenProvider` to read the JWT from localStorage.
 * 3. **Auth gate**: if no token exists on mount, render `SignInPrompt`
 *    BEFORE children. Otherwise the app loads, every API call 401s, and
 *    every tab renders an empty error state — bad first-run UX.
 *
 * Auth flow for the demo:
 *   - Click "Try demo account" → POST /auth/register with an auto-
 *     generated email → token stored → app comes alive.
 *   - Or paste a JWT from /auth/register / /auth/login manually.
 *
 * Out of scope here: refresh-token rotation, sign-out flow, 401 detection
 * mid-session. v1 demo only needs first-run-friendly auth.
 */

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { setAuthTokenProvider, setBackendSource } from "@/lib/api";

const TOKEN_KEY = "pivot_jwt";

/** Routes that render without the auth gate: the /design showcase, public
 *  marketing pages, and the auth routes themselves. */
const UNGATED_PATHS = ["/design", "/waitlist", "/login", "/signup", "/view-pack"];

type Phase = "loading" | "needs-auth" | "ready";

export function AppBootstrap({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  const pathname = usePathname();
  const router = useRouter();
  const ungated =
    pathname != null &&
    UNGATED_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
  const [phase, setPhase] = useState<Phase>("loading");

  useEffect(() => {
    // Flip to real backend + wire token provider FIRST so any code path
    // that fires before the gate decision still sees the right state.
    setBackendSource("real");
    setAuthTokenProvider(() => {
      try {
        return localStorage.getItem(TOKEN_KEY);
      } catch {
        return null;
      }
    });

    let stored: string | null = null;
    try {
      stored = localStorage.getItem(TOKEN_KEY);
    } catch {
      stored = null;
    }
    if (stored) {
      setPhase("ready");
    } else {
      setPhase("needs-auth");
      // Redirect to /login instead of showing a modal gate — but never
      // bounce the auth pages themselves (/login, /signup, …). Without
      // this guard, opening /signup with no token yet immediately
      // redirects back to /login, so the account-creation form is
      // unreachable.
      if (!ungated) {
        router.replace("/login");
      }
    }
  }, [router, ungated]);

  if (ungated) return <>{children}</>;
  if (phase === "loading") return <BootstrapSplash />;
  // "needs-auth" — show splash while the router.replace navigates.
  if (phase === "needs-auth") return <BootstrapSplash />;
  return <>{children}</>;
}

function BootstrapSplash(): React.ReactElement {
  return (
    <div
      className="flex min-h-screen items-center justify-center bg-background text-muted-foreground"
      aria-busy="true"
      aria-label="Loading"
    >
      <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10">
        <span className="text-sm font-bold text-primary">P</span>
      </div>
    </div>
  );
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
