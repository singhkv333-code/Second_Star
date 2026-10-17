"use client";

/**
 * DeepLinkButton — a "go straight there" link that opens a broker's exact
 * input page in a new tab. The label states the destination explicitly
 * ("Open Dhan → API Access") so the user always knows where the click lands,
 * per the redirect-first onboarding bar. Renders nothing when `href` is absent
 * (the backend didn't supply that deep-link).
 */

import { ExternalLink } from "lucide-react";

export function DeepLinkButton({
  href,
  label,
  accent,
  variant = "outline",
}: {
  href?: string | null;
  label: string;
  /** Brand accent for the icon tint on the filled variant. */
  accent?: string;
  /** "outline" = bordered chip; "accent" = subtle brand-tinted fill. */
  variant?: "outline" | "accent";
}): React.ReactElement | null {
  if (!href) return null;

  const accentColor = accent || "var(--text-primary)";
  const base: React.CSSProperties = {
    height: 34,
    padding: "0 12px",
    borderRadius: "var(--radius-sm)",
    fontFamily: "var(--font-ui)",
    fontSize: 12.5,
    fontWeight: 500,
    letterSpacing: "-0.005em",
    cursor: "pointer",
    textDecoration: "none",
    transition:
      "background-color 0.2s var(--ease-quartr), border-color 0.2s var(--ease-quartr), color 0.2s var(--ease-quartr)",
  };

  const variantStyle: React.CSSProperties =
    variant === "accent"
      ? {
          background: `color-mix(in srgb, ${accentColor} 12%, transparent)`,
          border: `1px solid color-mix(in srgb, ${accentColor} 28%, transparent)`,
          color: "var(--text-primary)",
        }
      : {
          background: "var(--bg-base)",
          border: "1px solid var(--glass-border)",
          color: "var(--text-secondary)",
        };

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1.5"
      style={{ ...base, ...variantStyle }}
      onMouseEnter={(e) => {
        if (variant === "accent") {
          e.currentTarget.style.background = `color-mix(in srgb, ${accentColor} 18%, transparent)`;
        } else {
          e.currentTarget.style.background = "var(--surface-active)";
          e.currentTarget.style.borderColor = "var(--glass-border-hover)";
          e.currentTarget.style.color = "var(--text-primary)";
        }
      }}
      onMouseLeave={(e) => {
        if (variant === "accent") {
          e.currentTarget.style.background = `color-mix(in srgb, ${accentColor} 12%, transparent)`;
        } else {
          e.currentTarget.style.background = "var(--bg-base)";
          e.currentTarget.style.borderColor = "var(--glass-border)";
          e.currentTarget.style.color = "var(--text-secondary)";
        }
      }}
    >
      <span>{label}</span>
      <ExternalLink
        size={13}
        strokeWidth={2}
        style={{ color: variant === "accent" ? accentColor : "var(--text-tertiary)" }}
        aria-hidden
      />
    </a>
  );
}
