"use client";

import { Toaster as SonnerToaster, type ToasterProps } from "sonner";

/**
 * Pivot-styled toaster.
 *
 * Sonner's `richColors` swaps the toast for a green-tinted success card
 * that doesn't fit the rest of the Quartr-inspired neutral surface
 * language (bg-primary card with a single glass-border hairline). We
 * override sonner's per-tone CSS variables to use Pivot's tokens so
 * success/error toasts read as the same calm panel with only a small
 * status accent inherited from the built-in icons.
 */
const TOAST_STYLE = {
  "--normal-bg": "var(--bg-primary)",
  "--normal-text": "var(--text-primary)",
  "--normal-border": "var(--glass-border)",
  "--success-bg": "var(--bg-primary)",
  "--success-text": "var(--text-primary)",
  "--success-border": "var(--glass-border)",
  "--error-bg": "var(--bg-primary)",
  "--error-text": "var(--text-primary)",
  "--error-border": "var(--glass-border)",
  "--info-bg": "var(--bg-primary)",
  "--info-text": "var(--text-primary)",
  "--info-border": "var(--glass-border)",
  "--warning-bg": "var(--bg-primary)",
  "--warning-text": "var(--text-primary)",
  "--warning-border": "var(--glass-border)",
  fontFamily: "var(--font-ui)",
} as React.CSSProperties;

const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <SonnerToaster
      className="toaster group"
      style={TOAST_STYLE}
      toastOptions={{
        style: {
          background: "var(--bg-primary)",
          color: "var(--text-primary)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-md)",
          fontFamily: "var(--font-ui)",
          fontSize: 13,
          letterSpacing: "-0.005em",
          padding: "12px 14px",
          boxShadow: "0 10px 30px rgba(0,0,0,0.14)",
        },
        classNames: {
          title: "text-[var(--text-primary)] font-medium",
          description: "text-[var(--text-secondary)]",
          actionButton:
            "group-[.toast]:bg-[var(--text-primary)] group-[.toast]:text-[var(--bg-primary)]",
          cancelButton:
            "group-[.toast]:bg-transparent group-[.toast]:text-[var(--text-secondary)]",
        },
      }}
      {...props}
    />
  );
};

export { Toaster };
