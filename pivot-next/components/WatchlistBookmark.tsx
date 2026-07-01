"use client";

import React, { useEffect, useRef, useState } from "react";
import { Bookmark, Check } from "lucide-react";
import {
  useWatchlists,
  isInAnyWatchlist,
  addToWatchlist,
  removeFromWatchlist,
} from "@/lib/watchlists";

/**
 * WatchlistBookmark — the bookmark control on the stock page. The icon is
 * filled when the symbol is saved in ANY watchlist; clicking it opens a small
 * popover to pick WHICH of the five numbered lists to add it to (toggle each
 * on/off). Backed by the shared watchlist store, so it stays in sync with the
 * screener's WatchlistStrip.
 */
export function WatchlistBookmark({
  symbol,
  size = 20,
  buttonSize = 38,
}: {
  symbol: string;
  size?: number;
  buttonSize?: number;
}): React.ReactElement {
  const state = useWatchlists();
  const saved = isInAnyWatchlist(state, symbol);
  const sym = symbol.trim().toUpperCase();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent): void => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative inline-flex shrink-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label={saved ? "Edit watchlists" : "Add to watchlist"}
        aria-expanded={open}
        data-testid="bookmark-btn"
        className="inline-flex shrink-0 items-center justify-center"
        style={{
          width: buttonSize,
          height: buttonSize,
          background: open ? "var(--surface-active)" : "transparent",
          border: "none",
          borderRadius: "var(--radius-sm)",
          color: saved ? "var(--text-primary)" : "var(--text-tertiary)",
          cursor: "pointer",
          transition:
            "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "var(--surface-active)";
          e.currentTarget.style.color = "var(--text-primary)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = open ? "var(--surface-active)" : "transparent";
          e.currentTarget.style.color = saved ? "var(--text-primary)" : "var(--text-tertiary)";
        }}
      >
        <Bookmark
          size={size}
          strokeWidth={2}
          fill={saved ? "currentColor" : "none"}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            zIndex: 30,
            width: 220,
            background: "var(--bg-primary)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
            boxShadow:
              "0 1px 2px rgba(0,0,0,0.06), 0 12px 30px -8px rgba(0,0,0,0.22)",
            padding: 6,
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <div
            style={{
              padding: "6px 10px 8px",
              fontSize: 10,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              color: "var(--text-tertiary)",
              fontFamily: "var(--font-ui)",
              fontWeight: 600,
            }}
          >
            Add to watchlist
          </div>
          {state.lists.map((l) => {
            const inList = l.tickers.includes(sym);
            return (
              <button
                key={l.id}
                type="button"
                role="menuitemcheckbox"
                aria-checked={inList}
                onClick={() =>
                  inList
                    ? removeFromWatchlist(symbol, l.id)
                    : addToWatchlist(symbol, l.id)
                }
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 8,
                  padding: "8px 10px",
                  background: "transparent",
                  border: "none",
                  borderRadius: "var(--radius-sm)",
                  cursor: "pointer",
                  textAlign: "left",
                  color: "var(--text-primary)",
                  fontFamily: "var(--font-ui)",
                  fontSize: 12.5,
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "var(--bg-secondary)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                }}
              >
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "baseline",
                    gap: 8,
                    minWidth: 0,
                  }}
                >
                  <span style={{ fontWeight: 500 }}>Watchlist {l.id}</span>
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--text-tertiary)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {l.tickers.length
                      ? `${l.tickers.length} stock${l.tickers.length === 1 ? "" : "s"}`
                      : "empty"}
                  </span>
                </span>
                <span
                  aria-hidden="true"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: 18,
                    height: 18,
                    flexShrink: 0,
                    borderRadius: "var(--radius-sm)",
                    border: inList
                      ? "1px solid var(--text-primary)"
                      : "1px solid var(--glass-border)",
                    background: inList ? "var(--text-primary)" : "transparent",
                    color: "var(--bg-primary)",
                  }}
                >
                  {inList && <Check size={12} strokeWidth={3} />}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
