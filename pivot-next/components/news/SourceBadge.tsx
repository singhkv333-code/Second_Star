"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Source domain map — mirrors backend.triggers.credibility.source_brand_domain
// ---------------------------------------------------------------------------

export const SOURCE_DOMAIN: Record<string, string> = {
  Reuters: "reuters.com",
  Bloomberg: "bloomberg.com",
  FT: "ft.com",
  WSJ: "wsj.com",
  AP: "apnews.com",
  "The Hindu": "thehindu.com",
  Economist: "economist.com",
  "Business Standard": "business-standard.com",
  TOI: "timesofindia.indiatimes.com",
  CNBC: "cnbc.com",
  Moneycontrol: "moneycontrol.com",
  Mint: "livemint.com",
  "Google News": "news.google.com",
  "Yahoo News": "news.yahoo.com",
};

// ---------------------------------------------------------------------------
// SourceLogo — tiny rounded square showing a publication's favicon logo.
// Falls back to a first-letter monogram on load error (clearbit may be
// blocked or rate-limited — the fallback is silent, no console spam).
// ---------------------------------------------------------------------------

type SourceLogoProps = {
  sourceId: string;
  size?: number;
};

export function SourceLogo({ sourceId, size = 18 }: SourceLogoProps): React.ReactElement {
  const [errored, setErrored] = useState(false);
  const domain = SOURCE_DOMAIN[sourceId];
  const firstChar = (sourceId || "?")[0]!.toUpperCase();

  if (!domain || errored) {
    return (
      <span
        aria-label={sourceId + " logo"}
        title={sourceId}
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: size,
          height: size,
          borderRadius: 4,
          background: "var(--bg-elevated)",
          border: "1px solid var(--glass-border)",
          fontSize: Math.max(9, Math.round(size * 0.55)),
          fontWeight: 600,
          color: "var(--text-secondary)",
          fontFamily: "var(--font-ui)",
          flexShrink: 0,
          userSelect: "none",
        }}
      >
        {firstChar}
      </span>
    );
  }

  return (
    <img
      src={`https://logo.clearbit.com/${domain}`}
      alt={sourceId + " logo"}
      title={sourceId}
      width={size}
      height={size}
      onError={() => setErrored(true)}
      style={{
        width: size,
        height: size,
        borderRadius: 4,
        objectFit: "contain",
        flexShrink: 0,
        border: "1px solid var(--glass-border)",
        background: "var(--bg-base)",
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// SourceBadge — credibility tier pill (moved from triggers/SourceBadge.tsx)
// ---------------------------------------------------------------------------

type SourceBadgeProps = {
  score: number;
  className?: string;
};

type Tier = {
  label: string;
  variant: "success" | "secondary" | "muted";
};

export function credibilityTier(score: number): Tier {
  if (score >= 0.9) return { label: "Primary", variant: "success" };
  if (score >= 0.75) return { label: "Secondary", variant: "secondary" };
  return { label: "Tertiary", variant: "muted" };
}

export function SourceBadge({ score, className }: SourceBadgeProps) {
  const { label, variant } = credibilityTier(score);
  return (
    <Badge variant={variant} className={cn("text-[10px] px-1.5 py-0", className)}>
      {label}
    </Badge>
  );
}
