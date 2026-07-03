"use client";

/**
 * StockHoverActions — the Kite-style quick-action bar that appears when
 * hovering a stock row (chat tables, screener grid, portfolio holdings).
 *
 * Layout mirrors Zerodha's watchlist affordance, restyled to Pivot's calm
 * Quartr idiom: a floating pill with
 *
 *   [B] [S]  |  chart · option chain · ask Pivot
 *
 * B/S jump to chat with a prefilled order sentence (register-not-execute —
 * the chat pipeline builds the order card, the user confirms). Chart opens
 * the stock page. Option chain prefills the chain request. Ask Pivot jumps
 * to chat with the security ATTACHED (the @-mention context), so "what do
 * you think?" is already grounded.
 *
 * Render inside a `position: relative` row while hovered; the bar
 * positions itself to the right edge, vertically centered.
 */

import { useRouter, usePathname } from "next/navigation";
import { LayoutGrid, LineChart, MessageCircle } from "lucide-react";

// ---------------------------------------------------------------------------
// Chat seeding — shared jump helper
// ---------------------------------------------------------------------------

type ChatSeed = {
  text?: string;
  mode?: "automation" | "agent" | "backtest" | null;
  attach?: {
    kind: "security";
    symbol: string;
    name: string;
    logo_url?: string | null;
  };
};

/** Dispatch twice (now-ish + after nav settles) — the chat surface stays
 * mounted on "/", but jumps from other routes (/stock/[symbol]) need the
 * remount to finish first. Seeding is idempotent (attachment dedupe,
 * same-text intent), so the double fire is safe. */
function dispatchSeed(seed: ChatSeed): void {
  const fire = (): void => {
    window.dispatchEvent(
      new CustomEvent("pivot:seed-composer", { detail: seed }),
    );
  };
  requestAnimationFrame(fire);
  window.setTimeout(fire, 400);
}

// ---------------------------------------------------------------------------
// The bar
// ---------------------------------------------------------------------------

export function StockHoverActions({
  symbol,
  name,
  logoUrl,
  /** Extra classes for positioning inside the host row. */
  className,
  style,
}: {
  symbol: string;
  name?: string;
  logoUrl?: string | null;
  className?: string;
  style?: React.CSSProperties;
}): React.ReactElement {
  const router = useRouter();
  const pathname = usePathname();
  const sym = symbol.toUpperCase();

  const goChat = (seed: ChatSeed): void => {
    if (pathname === "/") {
      window.location.hash = "#chat";
    } else {
      router.push("/#chat");
    }
    dispatchSeed(seed);
  };

  const stop = (e: React.MouseEvent): void => {
    // Rows are often themselves clickable/linked — the bar must never
    // trigger the row's own navigation.
    e.preventDefault();
    e.stopPropagation();
  };

  return (
    <div
      data-testid={`stock-hover-actions-${sym}`}
      className={className}
      onClick={stop}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: 3,
        borderRadius: 10,
        background: "var(--bg-elevated)",
        border: "1px solid var(--glass-border)",
        boxShadow: "0 4px 16px rgba(0,0,0,0.14)",
        fontFamily: "var(--font-ui)",
        animation: "draftCardIn-quartr 140ms cubic-bezier(0.22, 1, 0.36, 1) both",
        ...style,
      }}
    >
      {/* Buy / Sell — Kite's B/S squares, Pivot palette. */}
      <TradeButton
        label="B"
        title={`Buy ${sym}`}
        bg="#4184f3"
        onClick={(e) => {
          stop(e);
          goChat({ text: `Buy 10 ${sym} at market`, mode: "automation" });
        }}
      />
      <TradeButton
        label="S"
        title={`Sell ${sym}`}
        bg="#eb5b3c"
        onClick={(e) => {
          stop(e);
          goChat({ text: `Sell 10 ${sym} at market`, mode: "automation" });
        }}
      />

      <span
        aria-hidden="true"
        style={{
          width: 1,
          height: 16,
          margin: "0 2px",
          background: "var(--glass-border)",
        }}
      />

      <IconButton
        title={`${sym} chart & profile`}
        onClick={(e) => {
          stop(e);
          router.push(`/stock/${encodeURIComponent(sym)}`);
        }}
      >
        <LineChart size={13.5} strokeWidth={2} aria-hidden="true" />
      </IconButton>
      <IconButton
        title={`${sym} option chain`}
        onClick={(e) => {
          stop(e);
          // Open the full-screen option chain directly — no chat detour.
          // A global host (mounted once in AppShell) listens for this.
          window.dispatchEvent(
            new CustomEvent("pivot:open-option-chain", {
              detail: { underlying: sym },
            }),
          );
        }}
      >
        <LayoutGrid size={13.5} strokeWidth={2} aria-hidden="true" />
      </IconButton>
      <IconButton
        title={`Ask Pivot about ${sym}`}
        onClick={(e) => {
          stop(e);
          goChat({
            attach: {
              kind: "security",
              symbol: sym,
              name: name || sym,
              logo_url: logoUrl ?? null,
            },
          });
        }}
      >
        <MessageCircle size={13.5} strokeWidth={2} aria-hidden="true" />
      </IconButton>
    </div>
  );
}

function TradeButton({
  label,
  title,
  bg,
  onClick,
}: {
  label: string;
  title: string;
  bg: string;
  onClick: (e: React.MouseEvent) => void;
}): React.ReactElement {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      className="inline-flex items-center justify-center"
      style={{
        width: 24,
        height: 24,
        borderRadius: 7,
        border: "none",
        background: bg,
        color: "#fff",
        fontFamily: "var(--font-ui)",
        fontSize: 11.5,
        fontWeight: 700,
        cursor: "pointer",
        transition: "transform 0.12s var(--ease-quartr), filter 0.12s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.filter = "brightness(1.08)";
        e.currentTarget.style.transform = "scale(1.06)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.filter = "none";
        e.currentTarget.style.transform = "none";
      }}
    >
      {label}
    </button>
  );
}

function IconButton({
  title,
  onClick,
  children,
}: {
  title: string;
  onClick: (e: React.MouseEvent) => void;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      className="inline-flex items-center justify-center"
      style={{
        width: 24,
        height: 24,
        borderRadius: 7,
        border: "none",
        background: "transparent",
        color: "var(--text-secondary)",
        cursor: "pointer",
        transition:
          "color 0.15s var(--ease-quartr), background-color 0.15s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = "var(--text-primary)";
        e.currentTarget.style.background = "var(--surface-active)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = "var(--text-secondary)";
        e.currentTarget.style.background = "transparent";
      }}
    >
      {children}
    </button>
  );
}
