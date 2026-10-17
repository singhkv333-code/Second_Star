"use client";

/**
 * AppBootstrap — runs once on the client, before any API call fires.
 *
 * Responsibilities:
 * 1. Flip `setBackendSource("real")` so every API call hits the live backend.
 * 2. Wire `setAuthTokenProvider` to the one Pivot session (lib/session.ts).
 * 3. **Auth gate**: with no session, send the visitor to /login (the app's
 *    sign-in page; nginx routes it there) with this page in `?next=`. The
 *    public pages in UNGATED_PATHS render without it.
 */

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { setAuthTokenProvider, setBackendSource } from "@/lib/api";
import { saveSession, sessionToken } from "@/lib/session";
import { LoadingCubes } from "@/components/ui/LoadingCubes";

/** Routes that render without the auth gate: the /design showcase, public
 *  marketing pages, and the auth routes themselves. */
// The company page is public: it reads market data, not an account, so it
// opens for anybody without a sign-in wall.
// `/paper` is ungated for the same reason `/stock` is: the page says where to
// sign in instead of sending a visitor away.
const UNGATED_PATHS = ["/", "/design", "/waitlist", "/checkout", "/pricing", "/about", "/terms", "/privacy", "/refunds", "/cancellation", "/contact",
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
    setAuthTokenProvider(() => sessionToken());

    if (sessionToken()) {
      setPhase("ready");
    } else {
      setPhase("needs-auth");
      // Redirect to /login instead of showing a modal gate — but never
      // bounce the auth pages themselves (/login, /signup, …). Without
      // this guard, opening /signup with no token yet immediately
      // redirects back to /login, so the account-creation form is
      // unreachable.
      if (!ungated) {
        router.replace(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
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
  return sessionToken();
}

/**
 * Store a JWT — called from the sign-in prompt or externally by the
 * legacy chat when it receives a token.
 */
export function storeToken(token: string): void {
  saveSession(token);
}
