"use client";

/**
 * BrokerCard — one broker in the picker grid.
 *
 * Visual language matches the rest of Pivot (Quartr tokens): a calm
 * `--bg-primary` surface on a `--glass-border` hairline, `--radius-lg` corners,
 * dense-but-breathable spacing. The broker's brand accent appears ONLY as a
 * thin top rule and a faint connection dot — never a gradient wash.
 *
 * Each card carries exactly one primary action; the parent owns the actual
 * connect/manage logic and passes the resolved label + handler in.
 */

import { ArrowRight, Check, ChevronRight } from "lucide-react";
import { BrokerLogo } from "./BrokerLogo";
import { connectionBadge } from "./broker-ui";
import type { Broker } from "@/lib/types";

export function BrokerCard({
  broker,
  primaryLabel,
  onPrimary,
}: {
  broker: Broker;
  /** Label for the single CTA, e.g. "Connect Zerodha" / "Manage". */
  primaryLabel: string;
  onPrimary: () => void;
}): React.ReactElement {
  const badge = connectionBadge(broker);
  const connected = broker.status.connected;
  const dotColor =
    badge.tone === "connected"
      ? "var(--color-profit)"
      : badge.tone === "mock"
        ? "var(--color-warn)"
        : "var(--text-tertiary)";

  return (
    <div
      data-testid={`broker-card-${broker.id}`}
      className="group relative flex flex-col overflow-hidden"
      style={{
        background: "var(--bg-base)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-lg)",
        transition:
          "border-color 0.25s var(--ease-quartr), box-shadow 0.25s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "var(--glass-border-hover)";
        e.currentTarget.style.boxShadow =
          "0 1px 2px rgba(15,23,42,0.04), 0 18px 36px -22px rgba(15,23,42,0.26)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--glass-border)";
        e.currentTarget.style.boxShadow = "none";
      }}
    >
      <div className="flex flex-1 flex-col gap-3.5 p-4 lg:p-5">
        {/* Header: logo + name/persistence, with the status pill top-right */}
        <div className="flex items-start gap-3">
          <BrokerLogo
            brokerId={broker.id}
            logo={broker.logo}
            name={broker.name}
            accent={broker.accent}
            size={44}
          />
          <div className="flex min-w-0 flex-1 flex-col">
            <span
              className="truncate"
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: 600,
                fontSize: 15,
                letterSpacing: "-0.01em",
                color: "var(--text-primary)",
                lineHeight: 1.2,
              }}
            >
              {broker.name}
            </span>
            <span
              className="mt-1 inline-flex w-fit items-center gap-1.5"
              style={{ fontSize: 11, color: "var(--text-tertiary)" }}
            >
              <span
                aria-hidden
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: dotColor,
                  boxShadow:
                    badge.tone === "connected"
                      ? `0 0 0 3px color-mix(in srgb, ${"var(--color-profit)"} 18%, transparent)`
                      : "none",
                }}
              />
              {badge.label}
            </span>
          </div>
        </div>

        {/* Blurb */}
        <p
          style={{
            fontSize: 12.5,
            lineHeight: 1.5,
            color: "var(--text-secondary)",
            margin: 0,
          }}
        >
          {broker.blurb}
        </p>

        {/* Capability tags */}
        {broker.tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {broker.tags.map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center gap-1"
                style={{
                  padding: "2.5px 8px",
                  borderRadius: "var(--radius-pill)",
                  background: "var(--surface-active)",
                  color: "var(--text-secondary)",
                  fontSize: 10.5,
                  fontWeight: 500,
                  letterSpacing: "0.01em",
                  whiteSpace: "nowrap",
                }}
              >
                <Check
                  size={10}
                  strokeWidth={2.5}
                  style={{ color: "var(--text-tertiary)" }}
                  aria-hidden
                />
                {tag}
              </span>
            ))}
          </div>
        )}

        {/* Single primary action, pinned to the bottom of the card. */}
        <div className="mt-auto pt-1.5">
          <button
            type="button"
            onClick={onPrimary}
            data-testid={`broker-card-cta-${broker.id}`}
            className="inline-flex w-full items-center justify-center gap-1.5"
            style={{
              height: 42,
              borderRadius: "var(--radius-md)",
              fontFamily: "var(--font-ui)",
              fontSize: 13,
              fontWeight: 600,
              letterSpacing: "-0.005em",
              cursor: "pointer",
              transition:
                "background-color 0.2s var(--ease-quartr), color 0.2s var(--ease-quartr), border-color 0.2s var(--ease-quartr)",
              ...(connected
                ? {
                    background: "transparent",
                    border: "1px solid var(--glass-border)",
                    color: "var(--text-primary)",
                  }
                : {
                    background: "var(--text-primary)",
                    border: "1px solid var(--text-primary)",
                    color: "var(--bg-base)",
                  }),
            }}
            onMouseEnter={(e) => {
              if (connected) {
                e.currentTarget.style.background = "var(--surface-active)";
                e.currentTarget.style.borderColor = "var(--glass-border-hover)";
              } else {
                // Subtle darken (text stays crisp) instead of fading the whole button.
                const hov = "color-mix(in srgb, var(--text-primary) 86%, var(--bg-base))";
                e.currentTarget.style.background = hov;
                e.currentTarget.style.borderColor = hov;
              }
            }}
            onMouseLeave={(e) => {
              if (connected) {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.borderColor = "var(--glass-border)";
              } else {
                e.currentTarget.style.background = "var(--text-primary)";
                e.currentTarget.style.borderColor = "var(--text-primary)";
              }
            }}
          >
            {primaryLabel}
            {connected ? (
              <ChevronRight size={15} strokeWidth={2} aria-hidden />
            ) : (
              <ArrowRight size={15} strokeWidth={2} aria-hidden />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
