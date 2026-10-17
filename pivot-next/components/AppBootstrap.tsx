"use client";

/**
 * AppBootstrap — runs once on the client, before any API call fires.
 *
 * Responsibilities:
 * 1. Flip `setBackendSource("real")` so every API call hits the live backend.
 * 2. Wire `setAuthTokenProvider` to the one Pivot session (lib/session.ts).
 * 3. **Auth gate**: with no session, send the visitor to /login with this
 *    page in `?next=`, so signing in comes back here. The public pages in
 *    UNGATED_PATHS render without it, and the app's root opens as the GUEST
 *    workspace: the chart, open to everyone, with the account asked for only
 *    at the step that needs one (components/billing/SignInPanel.tsx).
 */

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { setAuthTokenProvider, setBackendSource } from "@/lib/api";
import { LoadingCubes } from "@/components/ui/LoadingCubes";
import { loginForHere } from "@/lib/returnTo";
import { saveSession, sessionToken } from "@/lib/session";

/** Routes that render without the auth gate: the /design showcase, public
 *  marketing and legal pages, the auth routes themselves, the plans (a
 *  visitor reads them before signing up) and the paywall design gallery. */
const UNGATED_PATHS = ["/about", "/terms", "/privacy", "/design", "/waitlist", "/login", "/signup", "/reset-password", "/verify-email", "/refunds", "/cancellation", "/contact", "/affiliate", "/pricing", "/paywall-gallery"];

/** The free, indexable research pages: a company page (not its statements
 *  sub-page, which reads signed-in APIs), the stock directory and the sector
 *  pages. A company page decides for itself what a signed-out reader sees
 *  (app/stock/[symbol]/view.tsx). */
const PUBLIC_PAGES = [/^\/stock\/[^/]+\/?$/, /^\/stocks(\/|$)/, /^\/sectors(\/|$)/];

type Phase = "loading" | "needs-auth" | "guest" | "ready";

/** The app's root, where a signed-out visitor gets the open workspace. */
const GUEST_PATHS = ["/"];

export function AppBootstrap({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  const pathname = usePathname();
  const router = useRouter();
  const ungated =
    pathname != null &&
    (UNGATED_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))
      || PUBLIC_PAGES.some((re) => re.test(pathname)));
  const [phase, setPhase] = useState<Phase>("loading");

  useEffect(() => {
    // Flip to real backend + wire token provider FIRST so any code path
    // that fires before the gate decision still sees the right state.
    setBackendSource("real");
    setAuthTokenProvider(() => {
      try {
        return getStoredToken();
      } catch {
        return null;
      }
    });

    if (sessionToken()) {
      setPhase("ready");
    } else if (pathname != null && GUEST_PATHS.includes(pathname)) {
      setPhase("guest");
    } else {
      setPhase("needs-auth");
      // Redirect to /login instead of showing a modal gate — but never
      // bounce the auth pages themselves (/login, /signup, …). Without
      // this guard, opening /signup with no token yet immediately
      // redirects back to /login, so the account-creation form is
      // unreachable.
      if (!ungated) {
        // Carry the page along (?next=) so signing in returns to it — a
        // shared chart's "Make it mine" lands here with the setup in the URL.
        router.replace(loginForHere());
      }
    }
  }, [router, ungated, pathname]);

  if (ungated) return <>{children}</>;
  if (phase === "loading") return <BootstrapSplash />;
  // "needs-auth" — show splash while the router.replace navigates.
  if (phase === "needs-auth") return <BootstrapSplash />;
  // "guest" and "ready" both render the app; AppShell reads the session
  // itself and opens the guest workspace when there is none.
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
