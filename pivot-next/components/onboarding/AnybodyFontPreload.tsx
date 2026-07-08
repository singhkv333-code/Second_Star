"use client";

/**
 * AnybodyFontPreload — warms the "Anybody" variable font on the auth pages
 * so the post-login brand intro starts instantly instead of blank.
 *
 * The stylesheet alone doesn't fetch the binary (Google Fonts only pulls it
 * when an element actually uses the family), so we also render a hidden
 * PIVOT in the exact heavy/expanded state the intro measures — that forces
 * the binary to download while the user is still typing their credentials.
 * By the time they submit, it's cached and the intro's fonts.check() passes
 * immediately.
 */

import { ANYBODY_FONT_HREF } from "@/components/onboarding/LoginIntroGate";

export function AnybodyFontPreload(): React.ReactElement {
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-page-custom-font */}
      <link rel="stylesheet" href={ANYBODY_FONT_HREF} />
      <span
        aria-hidden="true"
        style={{
          position: "fixed",
          left: -9999,
          top: -9999,
          fontFamily: '"Anybody", sans-serif',
          fontWeight: 900,
          fontStretch: "150%",
          fontVariationSettings: '"wght" 900, "wdth" 150',
          pointerEvents: "none",
          opacity: 0,
        }}
      >
        PIVOT
      </span>
    </>
  );
}

export default AnybodyFontPreload;
