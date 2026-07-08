"use client";

/**
 * LoginIntroGate — plays the PIVOT brand intro (LoginIntro) exactly once,
 * right after a successful login or signup.
 *
 * Flow: the auth forms call `armLoginIntro()` on success, which sets a
 * one-shot sessionStorage flag, then navigate to "/". This gate mounts
 * inside AppShell, consumes the flag on mount, lazy-injects the "Anybody"
 * variable font the animation morphs through, and renders the overlay
 * until it finishes. sessionStorage (not local) so it never replays on a
 * plain reload of an already-signed-in session.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { LoginIntro } from "@/components/onboarding/LoginIntro";

declare global {
  interface Window {
    /** True while the post-login brand intro owns the screen (hold + play).
     *  Read by ProductTour to defer its auto-start. */
    __pivotIntroPending?: boolean;
  }
}

const FLAG = "pivot_login_intro";
const FONT_ID = "anybody-intro-font";
/** The "Anybody" variable face the intro morphs through. Exported so the
 *  auth pages can warm it while the user types (see AnybodyFontPreload) —
 *  otherwise it downloads cold on first login and the intro starts blank. */
export const ANYBODY_FONT_HREF =
  "https://fonts.googleapis.com/css2?family=Anybody:ital,wdth,wght@0,50..150,100..900&display=block";
/** Fired when the intro finishes (or is skipped) — the product tour waits
 *  on this so the two never overlap on a first-run signup. */
export const INTRO_DONE_EVENT = "pivot:intro-done";

/** Call on successful auth, immediately before navigating to the app. */
export function armLoginIntro(): void {
  try {
    sessionStorage.setItem(FLAG, "1");
  } catch {
    /* private mode — the intro simply won't play, no harm */
  }
}

/** Beat of pure white after login before the slabs start rising, so the
 *  animation doesn't begin the instant the app pops in. */
const LEAD_IN_MS = 450;

type Phase = "idle" | "hold" | "play";

export function LoginIntroGate(): React.ReactElement | null {
  // Decide the OPENING phase synchronously from the flag, so the gate's very
  // first render already paints the white hold — the app never flashes
  // through before an effect can cover it. Reading (not writing) the flag in
  // the initializer is pure enough; the effect does the consume. This only
  // ever runs on the client SPA nav from /login (no SSR for that transition),
  // and on a cold load of "/" the flag is absent → "idle" on both sides, so
  // there's no hydration mismatch.
  const [phase, setPhase] = useState<Phase>(() => {
    if (typeof window === "undefined") return "idle";
    try {
      return sessionStorage.getItem(FLAG) === "1" ? "hold" : "idle";
    } catch {
      return "idle";
    }
  });
  // One-shot guard: the arm step CONSUMES the sessionStorage flag, so it must
  // run exactly once. React StrictMode (dev) mounts→cleans→remounts the
  // effect; a plain effect would consume the flag on the first pass and then
  // no-op on the second, and its cleanup would cancel the lead-in timer —
  // leaving the gate stuck on the white hold. The ref makes arming idempotent
  // and we deliberately DON'T clear the timer on the fake cleanup.
  const armedRef = useRef(false);

  useEffect(() => {
    if (armedRef.current) return;
    armedRef.current = true;

    let armed = false;
    try {
      armed = sessionStorage.getItem(FLAG) === "1";
      if (armed) sessionStorage.removeItem(FLAG);
    } catch {
      armed = false;
    }
    if (!armed) {
      // Nothing to play — let any first-run tour proceed immediately.
      window.__pivotIntroPending = false;
      window.dispatchEvent(new CustomEvent(INTRO_DONE_EVENT));
      return;
    }

    // Mark the intro as owning the screen for its whole lifetime (hold +
    // play), so the product tour holds off even during the white lead-in
    // when the LoginIntro overlay isn't mounted yet.
    window.__pivotIntroPending = true;

    // Persistent registration of the display face (survives the auth-page
    // unmount; the page-level preload has already warmed the binary).
    if (!document.getElementById(FONT_ID)) {
      const link = document.createElement("link");
      link.id = FONT_ID;
      link.rel = "stylesheet";
      link.href = ANYBODY_FONT_HREF;
      document.head.appendChild(link);
    }

    // Hold on a white cover for a beat, then start the animation.
    setPhase("hold");
    window.setTimeout(() => setPhase("play"), LEAD_IN_MS);
  }, []);

  // Stable identity: AppShell re-renders often in the first seconds after
  // login (conversations/account/metrics fetches resolving). LoginIntro's
  // animation-driving effect depends on `onDone` (see LoginIntro.tsx), so a
  // fresh `finish` on every render was restarting the rAF loop mid-animation
  // — the "plays twice" bug. useCallback keeps it referentially stable.
  const finish = useCallback((): void => {
    setPhase("idle");
    window.__pivotIntroPending = false;
    window.dispatchEvent(new CustomEvent(INTRO_DONE_EVENT));
  }, []);

  if (phase === "idle") return null;
  if (phase === "hold") {
    // Seamless with both the (suppressed-loader) bootstrap white screen
    // before it and the LoginIntro white cover after it.
    return (
      <div
        aria-hidden="true"
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 200,
          background: "#ffffff",
          pointerEvents: "none",
        }}
      />
    );
  }
  return <LoginIntro onDone={finish} />;
}

export default LoginIntroGate;
