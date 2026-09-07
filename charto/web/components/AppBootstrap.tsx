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
 * Refresh-token rotation IS now handled: when a token is present we start a
 * background auto-refresh (scheduleAutoRefresh) that silently trades the
 * 7-day refresh token for a fresh 12h access token before it expires and
 * whenever the tab regains focus — so users stay signed in across days
 * instead of hitting a wall of 401s every morning. Sign-out tears it down.
 */

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { setAuthTokenProvider, setBackendSource } from "@/lib/api";
import { scheduleAutoRefresh, stopAutoRefresh } from "@/lib/authToken";
import { LoadingCubes } from "@/components/ui/LoadingCubes";

// Charto's chart app signs the user in and keeps the bearer under its own
// key; this Next app is served from the same origin and talks to the same
// backend, so that is the token to send. `pivot_jwt` stays as the fallback
// because the shared api layer, the login page and the refresh flow were all
// written against it — reading Charto's key FIRST is what makes a session
// started on the chart carry into these pages without a second sign-in.
const TOKEN_KEYS = ["charto:auth:token", "pivot_jwt"] as const;

function readToken(): string | null {
  for (const key of TOKEN_KEYS) {
    try {
      const v = window.localStorage.getItem(key);
      if (v) return v;
    } catch {
      return null;
    }
  }
  return null;
}

/** Routes that render without the auth gate: the /design showcase, public
 *  marketing pages, and the auth routes themselves. */
// charto: the company page is public — it reads charto's own store, not a
// user's account, so it must open for anybody without a sign-in wall.
// `/paper` is ungated for the same reason `/stock` is: Charto's sign-in lives
// on the chart, and bouncing a signed-out visitor to this app's own /login
// would hand them the wrong form for the wrong account system. The page says
// where to sign in instead.
const UNGATED_PATHS = ["/", "/design", "/waitlist", "/login", "/signup", "/view-pack",
                       "/stock", "/paper"];

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
    setAuthTokenProvider(() => readToken());

    const stored = readToken();
    if (stored) {
      setPhase("ready");
      // Keep the access token warm for the whole session (timer + on focus),
      // so the direct-fetch data modules (chat, live quotes, portfolio,
      // screener) never hit an expired token mid-use.
      scheduleAutoRefresh();
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
    return () => stopAutoRefresh();
  }, [router, ungated]);

  if (ungated) return <>{children}</>;
  if (phase === "loading") return <BootstrapSplash />;
  // "needs-auth" — show splash while the router.replace navigates.
  if (phase === "needs-auth") return <BootstrapSplash />;
  return <>{children}</>;
}

function BootstrapSplash(): React.ReactElement {
  // Right after login/signup the brand intro is armed (a sessionStorage
  // flag set before navigation). In that window we must NOT flash the cube
  // loader — the intro opens on a white cover, so a plain white hold here
  // hands off to it seamlessly. This transition is always client-side (SPA
  // nav from /login), so reading sessionStorage synchronously is safe.
  let introArmed = false;
  if (typeof window !== "undefined") {
    try {
      introArmed = sessionStorage.getItem("pivot_login_intro") === "1";
    } catch {
      introArmed = false;
    }
  }
  if (introArmed) {
    return (
      <div className="min-h-screen" style={{ background: "#ffffff" }} aria-hidden="true" />
    );
  }
  return (
    <div
      className="flex min-h-screen items-center justify-center bg-background"
      aria-busy="true"
      aria-label="Loading"
    >
      <LoadingCubes size={150} />
    </div>
  );
}

/**
 * Read the stored JWT for use in WS connections (query param).
 * Returns null when not signed in.
 */
export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return readToken();
}

/**
 * Store a JWT — called from the sign-in prompt or externally by the
 * legacy chat when it receives a token.
 */
export function storeToken(token: string): void {
  localStorage.setItem(TOKEN_KEYS[1], token);
}
