"use client";

import {
  Toaster as SonnerToaster,
  type ToasterProps,
} from "sonner";
import {
  CheckCircle2,
  Info,
  Loader2,
  TriangleAlert,
  XCircle,
} from "lucide-react";

/**
 * Pivot-styled toaster.
 *
 * Sonner's `richColors` swaps the toast for a green-tinted success card
 * that doesn't fit the rest of the Quartr-inspired neutral surface
 * language (bg-primary card with a single glass-border hairline). We keep
 * the calm neutral panel and instead carry status purely through a small
 * tinted icon chip on the left — the Linear/Vercel pattern: one quiet
 * surface, one coloured accent, clear title/description hierarchy.
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

/**
 * A status icon wrapped in a soft tinted square chip. Sized to align with
 * a two-line toast (title + description) without growing the toast height
 * for the common single-line case.
 */
function ToastIcon({
  children,
  tint,
}: {
  children: React.ReactNode;
  tint: string;
}): React.ReactElement {
  return (
    <span
      className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-[6px]"
      style={{ backgroundColor: `hsl(var(--${tint}) / 0.14)`, color: `hsl(var(--${tint}))` }}
      aria-hidden="true"
    >
      {children}
    </span>
  );
}

const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <SonnerToaster
      className="toaster group"
      style={TOAST_STYLE}
      gap={10}
      offset={16}
      icons={{
        success: (
          <ToastIcon tint="success">
            <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={2.25} />
          </ToastIcon>
        ),
        error: (
          <ToastIcon tint="destructive">
            <XCircle className="h-3.5 w-3.5" strokeWidth={2.25} />
          </ToastIcon>
        ),
        info: (
          <ToastIcon tint="info">
            <Info className="h-3.5 w-3.5" strokeWidth={2.25} />
          </ToastIcon>
        ),
        warning: (
          <ToastIcon tint="warning">
            <TriangleAlert className="h-3.5 w-3.5" strokeWidth={2.25} />
          </ToastIcon>
        ),
        loading: (
          <ToastIcon tint="info">
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2.25} />
          </ToastIcon>
        ),
      }}
      toastOptions={{
        style: {
          background: "var(--bg-primary)",
          color: "var(--text-primary)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-md)",
          fontFamily: "var(--font-ui)",
          fontSize: 13,
          lineHeight: "18px",
          letterSpacing: "-0.006em",
          padding: "11px 13px",
          boxShadow:
            "0 1px 2px rgba(15,18,22,0.06), 0 8px 24px rgba(15,18,22,0.12)",
        },
        classNames: {
          toast: "items-start gap-2.5",
          icon: "m-0",
          content: "gap-0.5",
          title: "text-[13px] font-medium leading-[18px] text-[var(--text-primary)]",
          description:
            "text-[12px] leading-[16px] text-[var(--text-secondary)]",
          actionButton:
            "group-[.toast]:h-7 group-[.toast]:rounded-md group-[.toast]:bg-[var(--text-primary)] group-[.toast]:px-2.5 group-[.toast]:text-[12px] group-[.toast]:font-medium group-[.toast]:text-[var(--bg-primary)]",
          cancelButton:
            "group-[.toast]:h-7 group-[.toast]:rounded-md group-[.toast]:bg-transparent group-[.toast]:px-2 group-[.toast]:text-[12px] group-[.toast]:text-[var(--text-secondary)] group-[.toast]:hover:text-[var(--text-primary)]",
          closeButton:
            "group-[.toast]:border-[var(--glass-border)] group-[.toast]:bg-[var(--bg-primary)] group-[.toast]:text-[var(--text-secondary)] group-[.toast]:hover:text-[var(--text-primary)]",
        },
      }}
      {...props}
    />
  );
};

export { Toaster };
