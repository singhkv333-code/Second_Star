"use client";

/**
 * ViewLifecycle — the lifecycle stepper:
 *   Open → Developing → Consensus → Resolved → Archived
 *
 * DESIGN LAW (components/views): square corners, borders only, no jargon,
 * 13px floor. The current marker is a FILLED SQUARE + border (no glow ring,
 * no box-shadow). Stage words route through statusLabel(). The connector
 * aligns to the dot via flex-start + a principled half-dot top offset (no
 * magic marginBottom).
 */

import * as React from "react";
import { statusLabel } from "@/components/views/view-format";
import { Num } from "@/components/views/Stat";
import { fmtDate } from "@/components/views/view-format";
import type { ViewStatus } from "@/lib/types";

interface ViewLifecycleProps {
  status: ViewStatus;
  createdAt: string;
  publishedAt: string | null;
  resolutionDate: string | null;
}

const DOT = 14;

/** The five display stages, in lifecycle order, each with its humanized word. */
const STAGE_KEYS = ["published", "developing", "consensus", "resolved", "archived"] as const;

/** Map a backend status onto a display-stage index (0..4). */
function currentStageIndex(status: string): number {
  switch ((status ?? "").toLowerCase()) {
    case "published":
    case "open":
      return 0;
    case "draft":
    case "developing":
      return 1;
    case "consensus":
      return 2;
    case "resolved":
      return 3;
    case "archived":
      return 4;
    default:
      return 1;
  }
}

export function ViewLifecycle({
  status,
  createdAt,
  publishedAt,
  resolutionDate,
}: ViewLifecycleProps) {
  const currentIdx = currentStageIndex(status);

  const dates: Array<string | null> = [
    publishedAt ? fmtDate(publishedAt) : null, // Open
    createdAt ? fmtDate(createdAt) : null, // Developing
    null, // Consensus
    resolutionDate ? fmtDate(resolutionDate) : null, // Resolved
    null, // Archived
  ];

  return (
    <div
      aria-label="View lifecycle"
      role="group"
      style={{ display: "flex", alignItems: "flex-start", width: "100%" }}
    >
      {STAGE_KEYS.map((key, i) => {
        const isPast = i < currentIdx;
        const isCurrent = i === currentIdx;
        const reached = isPast || isCurrent;

        const fill = reached ? "var(--text-primary)" : "var(--bg-base)";
        const border = isCurrent
          ? "1px solid var(--text-primary)"
          : reached
            ? "1px solid var(--text-secondary)"
            : "1px solid var(--glass-border)";

        const labelColor = isCurrent
          ? "var(--text-primary)"
          : reached
            ? "var(--text-secondary)"
            : "var(--text-tertiary)";

        const connectorReached = i < currentIdx;

        return (
          <React.Fragment key={key}>
            {/* Stage column */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 6,
                flex: "0 0 auto",
                minWidth: 0,
              }}
            >
              <div
                aria-current={isCurrent ? "step" : undefined}
                style={{
                  width: DOT,
                  height: DOT,
                  borderRadius: "var(--radius-pill)",
                  border,
                  background: fill,
                  flexShrink: 0,
                }}
              />
              <span
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: 13,
                  fontWeight: isCurrent ? 600 : 500,
                  color: labelColor,
                  textAlign: "center",
                  lineHeight: 1.3,
                  whiteSpace: "nowrap",
                }}
              >
                {statusLabel(key)}
              </span>
              {dates[i] && (
                <Num
                  size="label"
                  weight={500}
                  color="var(--text-tertiary)"
                  style={{ textAlign: "center" }}
                >
                  {dates[i]}
                </Num>
              )}
            </div>

            {/* Connector — aligned to dot center via half-dot top offset. */}
            {i < STAGE_KEYS.length - 1 && (
              <div
                aria-hidden
                style={{
                  flex: "1 1 auto",
                  minWidth: 12,
                  height: 1,
                  marginTop: DOT / 2,
                  background: connectorReached
                    ? "var(--text-secondary)"
                    : "var(--glass-border)",
                }}
              />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}
