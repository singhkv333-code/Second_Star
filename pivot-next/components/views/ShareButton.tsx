"use client";

/**
 * ShareButton — the single, canonical share control for the Views feature.
 *
 * Clean, icon-only: copies the current URL (or opens the native share sheet on
 * mobile), with a brief "Copied" tick. Used verbatim in BOTH the Views grid
 * header (ViewsTab) and the view detail header (ViewDetailPage) so the two can
 * never visually drift. The self-scoped hover rule travels with the component,
 * and an explicit `--bg-base` fill means the hairline border frames the same on
 * any surface.
 */

import * as React from "react";
import { Share2, Check } from "lucide-react";

export function ShareButton({
  ariaLabel = "Share",
}: {
  /** Accessible label; the two call sites differ only in this text. */
  ariaLabel?: string;
}): React.ReactElement {
  const [copied, setCopied] = React.useState(false);

  const onShare = React.useCallback(() => {
    const url = typeof window !== "undefined" ? window.location.href : "";
    const nav = typeof navigator !== "undefined" ? navigator : undefined;
    if (nav?.share) {
      nav.share({ url }).catch(() => {});
      return;
    }
    nav?.clipboard?.writeText(url).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      },
      () => {},
    );
  }, []);

  return (
    <>
      <style>{`.vwd-share:hover { color: var(--text-primary) !important; background: color-mix(in srgb, var(--text-primary) 8%, transparent) !important; }`}</style>
      <button
        type="button"
        onClick={onShare}
        aria-label={ariaLabel}
        title={copied ? "Copied" : "Share"}
        className="vwd-share"
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 34,
          height: 34,
          color: "var(--text-secondary)",
          background: "transparent",
          border: "none",
          borderRadius: "var(--radius-md)",
          padding: 0,
          cursor: "pointer",
          transition:
            "color 160ms var(--ease-quartr), background 160ms var(--ease-quartr)",
        }}
      >
        {copied ? (
          <Check size={16} aria-hidden style={{ color: "var(--pivot-blue)" }} />
        ) : (
          <Share2 size={16} aria-hidden />
        )}
      </button>
    </>
  );
}
