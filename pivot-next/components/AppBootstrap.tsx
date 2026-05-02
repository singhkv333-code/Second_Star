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
import { setAuthTokenProvider, setBackendSource } from "@/lib/api";

const TOKEN_KEY = "pivot_jwt";

type Phase = "loading" | "needs-auth" | "ready";

export function AppBootstrap({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
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
    setPhase(stored ? "ready" : "needs-auth");
  }, []);

  if (phase === "loading") return <BootstrapSplash />;
  if (phase === "needs-auth") {
    return <SignInPrompt onToken={() => setPhase("ready")} />;
  }
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

/**
 * Sign-in prompt — shown by AppBootstrap when no token exists in
 * localStorage. Two paths:
 *   - "Try demo account" — POST /auth/register with an auto-generated
 *     email + canonical demo password. Backend returns a fresh token;
 *     we store it + signal ready. Single click, no typing.
 *   - "Paste an existing token" — accordion that reveals a textarea
 *     for users who want to bring their own JWT.
 *
 * Hits the legacy /auth/register endpoint directly (relative to the
 * stripped-base URL — same trick as `requestLegacy` in lib/api.ts).
 */
export function SignInPrompt({
  onToken,
}: {
  onToken: (token: string) => void;
}): React.ReactElement {
  const [demoState, setDemoState] = useState<
    | { kind: "idle" }
    | { kind: "pending" }
    | { kind: "error"; message: string }
  >({ kind: "idle" });
  const [pasteOpen, setPasteOpen] = useState(false);
  const [value, setValue] = useState("");

  const handleDemo = async (): Promise<void> => {
    setDemoState({ kind: "pending" });
    try {
      const token = await registerDemoAccount();
      storeToken(token);
      onToken(token);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Couldn't reach backend";
      setDemoState({ kind: "error", message: msg });
    }
  };

  const handlePasteSubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;
    storeToken(trimmed);
    onToken(trimmed);
  };

  return (
    <div
      role="dialog"
      aria-label="Sign in"
      className="flex min-h-screen flex-col items-center justify-center bg-background p-8"
    >
      <div className="w-full max-w-sm space-y-5 rounded-xl border bg-card p-6 shadow-sm">
        <div>
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10">
            <span className="text-sm font-bold text-primary">P</span>
          </div>
          <h2 className="mt-3 text-base font-semibold">Welcome to Pivot</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Try the Agent System with a one-click demo account, or paste an
            existing token.
          </p>
        </div>

        <button
          type="button"
          onClick={handleDemo}
          disabled={demoState.kind === "pending"}
          data-testid="demo-account-btn"
          className="inline-flex w-full items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
        >
          {demoState.kind === "pending" ? "Creating demo account…" : "Try demo account"}
        </button>

        {demoState.kind === "error" && (
          <p
            role="alert"
            className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive"
          >
            {demoState.message}
          </p>
        )}

        <button
          type="button"
          onClick={() => setPasteOpen((v) => !v)}
          className="text-xs text-muted-foreground hover:text-foreground"
          data-testid="toggle-paste"
        >
          {pasteOpen ? "Hide" : "I have a token →"}
        </button>

        {pasteOpen && (
          <form onSubmit={handlePasteSubmit} className="space-y-3">
            <textarea
              aria-label="JWT token"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              rows={3}
              placeholder="eyJ..."
              data-testid="paste-textarea"
              className="w-full rounded-md border bg-background px-3 py-2 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <button
              type="submit"
              data-testid="paste-submit-btn"
              className="inline-flex w-full items-center justify-center rounded-md border px-4 py-2 text-sm font-medium hover:bg-muted"
            >
              Continue with pasted token
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

// ── Demo account registration ────────────────────────────────────────


/**
 * Resolve the backend host for the legacy /auth/register call. Mirrors
 * `getLegacyBase()` in lib/api.ts (strips the trailing /api). We
 * re-implement here instead of importing — lib/api.ts only exports its
 * public surface, and we don't want this single helper to grow that
 * module's import graph.
 */
function legacyAuthBase(): string {
  const base =
    (typeof process !== "undefined" &&
      process.env.NEXT_PUBLIC_PIVOT_API_BASE) ||
    "/api";
  return base.replace(/\/api\/?$/, "");
}

async function registerDemoAccount(): Promise<string> {
  // Auto-generate a unique email each click so reloads don't collide.
  // .example.com is the IETF-reserved domain; the backend's email
  // validator (rfc 5322 + special-domain rejection) accepts it.
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 8);
  const email = `demo_${ts}_${rand}@example.com`;

  const url = `${legacyAuthBase()}/auth/register`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email,
      password: "password123",
      full_name: "Demo",
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(
      `Backend ${res.status}: ${text.slice(0, 200) || "register failed"}`,
    );
  }
  const data = (await res.json()) as { access_token?: string };
  if (!data.access_token) {
    throw new Error("Backend response missing access_token");
  }
  return data.access_token;
}
