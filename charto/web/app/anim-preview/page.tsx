"use client";

/**
 * /anim-preview — standalone preview for the LoginIntro brand animation
 * (not wired into the real login flow yet).
 *
 *   • auto-plays on load; "Replay" restarts it
 *   • ?t=1.8 freezes the exact frame at t seconds (frame inspection)
 *
 * Loads the "Anybody" variable font (wght 100–900 × wdth 50–150) that the
 * animation morphs through. When the intro ships for real, move this font
 * link to the login route.
 */

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";

import { LoginIntro } from "@/components/onboarding/LoginIntro";

function MockApp(): React.ReactElement {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "var(--bg-base)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Faux topbar */}
      <div
        style={{
          height: 60,
          borderBottom: "1px solid var(--glass-border)",
          display: "flex",
          alignItems: "center",
          padding: "0 28px",
          gap: 24,
          fontFamily: "var(--font-ui)",
          fontSize: 13.5,
          color: "var(--text-secondary)",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: 700,
            fontSize: 18,
            letterSpacing: "-0.03em",
            color: "var(--text-primary)",
          }}
        >
          Pivot
        </span>
        <span>Chat</span>
        <span>Opinion Markets</span>
        <span>Portfolio</span>
        <span style={{ marginLeft: "auto" }}>₹77,945</span>
      </div>
      {/* Faux greeting */}
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <h1
          style={{
            fontFamily: "var(--font-serif)",
            fontWeight: 550,
            fontSize: 44,
            letterSpacing: "-0.02em",
            color: "var(--text-primary)",
          }}
        >
          Good Morning, Karan!
        </h1>
      </div>
    </div>
  );
}

function Preview(): React.ReactElement {
  // `?.` because Next 15.5 types useSearchParams() as possibly null — it is,
  // during prerender outside a Suspense boundary. 15.1 typed it as always
  // present, so the upgrade turned a latent crash into a build error.
  const params = useSearchParams();
  const tParam = params?.get("t") ?? null;
  const freezeAt = tParam != null ? Number(tParam) : undefined;
  // Remounting LoginIntro restarts its internal timeline.
  const [runId, setRunId] = useState(0);
  const [done, setDone] = useState(false);

  return (
    <>
      {/* Anybody variable font — the face the animation morphs through. */}
      {/* eslint-disable-next-line @next/next/no-page-custom-font */}
      <link
        href="https://fonts.googleapis.com/css2?family=Anybody:ital,wdth,wght@0,50..150,100..900&display=block"
        rel="stylesheet"
      />

      <MockApp />

      {(!done || freezeAt != null) && (
        <LoginIntro
          key={runId}
          freezeAt={Number.isFinite(freezeAt) ? freezeAt : undefined}
          onDone={() => setDone(true)}
        />
      )}

      {/* Replay — sits above everything, preview-only chrome. */}
      <button
        type="button"
        data-testid="replay-intro"
        onClick={() => {
          setDone(false);
          setRunId((n) => n + 1);
        }}
        style={{
          position: "fixed",
          right: 20,
          bottom: 20,
          zIndex: 300,
          padding: "9px 18px",
          borderRadius: "calc(var(--radius) - 2px)",
          border: "none",
          background: "hsl(var(--primary))",
          color: "hsl(var(--primary-foreground))",
          fontFamily: "var(--font-ui)",
          fontSize: 13.5,
          fontWeight: 500,
          cursor: "pointer",
        }}
      >
        Replay
      </button>
    </>
  );
}

export default function AnimPreviewPage(): React.ReactElement {
  return (
    <Suspense fallback={null}>
      <Preview />
    </Suspense>
  );
}
