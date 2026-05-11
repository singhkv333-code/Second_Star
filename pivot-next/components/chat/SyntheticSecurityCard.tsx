"use client";

import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

export type SyntheticSecurityLeg = {
  symbol: string;
  weight?: number;
  side?: "long" | "short";
};

export type SyntheticSecurityPayload = {
  name?: string;
  description?: string;
  legs?: SyntheticSecurityLeg[];
  [key: string]: unknown;
};

export type SyntheticSecurityCardProps = {
  payload: SyntheticSecurityPayload;
  className?: string;
};

export function SyntheticSecurityCard({ payload, className }: SyntheticSecurityCardProps) {
  const name = payload.name ?? "Synthetic security";
  const legs = Array.isArray(payload.legs) ? payload.legs : [];

  return (
    <div
      className={cn(
        "max-w-md rounded-xl border border-border bg-card px-4 py-3 shadow-sm",
        className,
      )}
      data-testid="synthetic-security-card"
    >
      <div className="flex items-center gap-2 text-sm font-medium text-foreground">
        <Sparkles className="h-4 w-4 text-muted-foreground" aria-hidden />
        {name}
      </div>
      {payload.description && (
        <p className="mt-1 text-xs text-muted-foreground">{payload.description}</p>
      )}
      {legs.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-foreground/90">
          {legs.map((leg, i) => (
            <li key={`${leg.symbol}-${i}`} className="flex items-center justify-between">
              <span className="font-mono">{leg.symbol}</span>
              <span className="text-muted-foreground">
                {leg.side ?? ""}
                {leg.weight !== undefined ? ` ${(leg.weight * 100).toFixed(1)}%` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default SyntheticSecurityCard;
