"use client";

/**
 * SocialSignIn — the shadcn login-03 social block: "Login with Apple" /
 * "Login with Google" outline buttons over an "Or continue with" divider.
 * Sits above the email/password form on the login page.
 *
 * Google is live when NEXT_PUBLIC_GOOGLE_CLIENT_ID is set: we load Google
 * Identity Services, run its OAuth popup for an access token, and hand that
 * to the backend (POST /auth/google) which verifies it with Google and
 * issues Pivot tokens. On success we arm the brand intro and land on the
 * app — identical to an email login. Without the env var (or for Apple,
 * which has no backend yet) the buttons stay honest: a "coming soon" toast,
 * never a fake success.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { googleLogin } from "@/lib/api";
import { armLoginIntro } from "@/components/onboarding/LoginIntroGate";
import { isError } from "@/lib/types";

const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ?? "";
const GIS_SRC = "https://accounts.google.com/gsi/client";

// Minimal typing for the slice of Google Identity Services we use.
interface TokenClient {
  requestAccessToken: (overrides?: { prompt?: string }) => void;
}
interface GoogleOAuth {
  accounts: {
    oauth2: {
      initTokenClient: (config: {
        client_id: string;
        scope: string;
        callback: (resp: { access_token?: string; error?: string }) => void;
      }) => TokenClient;
    };
  };
}
declare global {
  interface Window {
    google?: GoogleOAuth;
  }
}

function AppleIcon(): React.ReactElement {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
      <path d="M17.05 20.28c-.98.95-2.05.8-3.08.35-1.09-.46-2.09-.48-3.24 0-1.44.62-2.2.44-3.06-.35C2.79 15.25 3.51 7.59 9.05 7.31c1.35.07 2.29.74 3.08.8 1.18-.24 2.31-.93 3.57-.84 1.51.12 2.65.72 3.4 1.8-3.12 1.87-2.38 5.98.48 7.13-.57 1.5-1.31 2.99-2.54 4.09l.01-.01zM12.03 7.25c-.15-2.23 1.66-4.07 3.74-4.25.29 2.58-2.34 4.5-3.74 4.25z" />
    </svg>
  );
}

function GoogleIcon(): React.ReactElement {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
      />
    </svg>
  );
}

export function SocialSignIn(): React.ReactElement {
  const router = useRouter();
  const [gisReady, setGisReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const tokenClientRef = useRef<TokenClient | null>(null);

  const comingSoon = (provider: string): void => {
    toast(`${provider} sign-in is coming soon`, {
      description: "For now, sign in with your email and password.",
    });
  };

  // Called with the Google access token once the popup resolves.
  const onGoogleToken = useCallback(
    async (accessToken: string): Promise<void> => {
      setBusy(true);
      const res = await googleLogin(accessToken);
      if (isError(res)) {
        setBusy(false);
        toast("Couldn't sign in with Google", {
          description: res.error.message || "Please try again.",
        });
        return;
      }
      // Same hand-off as an email login: play the brand intro, enter the app.
      armLoginIntro();
      router.replace("/");
    },
    [router],
  );

  // Load Google Identity Services once, only when a client id is configured.
  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return;

    const init = (): void => {
      if (!window.google || tokenClientRef.current) return;
      tokenClientRef.current = window.google.accounts.oauth2.initTokenClient({
        client_id: GOOGLE_CLIENT_ID,
        scope: "openid email profile",
        callback: (resp) => {
          if (resp.access_token) {
            void onGoogleToken(resp.access_token);
          } else {
            // User closed the popup / denied consent — quietly reset.
            setBusy(false);
          }
        },
      });
      setGisReady(true);
    };

    if (window.google) {
      init();
      return;
    }
    let script = document.querySelector<HTMLScriptElement>(
      `script[src="${GIS_SRC}"]`,
    );
    if (!script) {
      script = document.createElement("script");
      script.src = GIS_SRC;
      script.async = true;
      script.defer = true;
      document.head.appendChild(script);
    }
    script.addEventListener("load", init);
    return () => script?.removeEventListener("load", init);
  }, [onGoogleToken]);

  const onGoogleClick = (): void => {
    if (!GOOGLE_CLIENT_ID) {
      comingSoon("Google");
      return;
    }
    if (!gisReady || !tokenClientRef.current) {
      toast("Google sign-in is still loading", {
        description: "One moment, then try again.",
      });
      return;
    }
    setBusy(true);
    tokenClientRef.current.requestAccessToken();
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3">
        <Button
          type="button"
          variant="outline"
          className="w-full"
          disabled={busy}
          onClick={() => comingSoon("Apple")}
        >
          <AppleIcon />
          Login with Apple
        </Button>
        <Button
          type="button"
          variant="outline"
          className="w-full"
          disabled={busy}
          onClick={onGoogleClick}
        >
          <GoogleIcon />
          {busy ? "Signing in…" : "Login with Google"}
        </Button>
      </div>

      {/* "Or continue with" divider — the label sits on the page surface so
          the hairline reads as a single continuous rule behind it. */}
      <div className="relative text-center text-sm">
        <div
          className="absolute inset-0 top-1/2 h-px"
          style={{ background: "var(--glass-border)" }}
          aria-hidden="true"
        />
        <span
          className="relative px-3 text-xs"
          style={{ background: "var(--bg-base)", color: "var(--text-tertiary)" }}
        >
          Or continue with
        </span>
      </div>
    </div>
  );
}

export default SocialSignIn;
