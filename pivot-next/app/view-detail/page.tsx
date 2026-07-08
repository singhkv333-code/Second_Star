"use client";

/**
 * /view-detail — the redesigned View DETAIL page (Kalshi-inspired, not a copy).
 *
 * When a user opens a "view" (a market question like "Will Nifty touch 30,000
 * before year-end?"), this is the page they land on:
 *
 *   ┌ header: question + subtitle ──────────────────────────────┐
 *   ├ LEFT (~65%): ReturnsChart      │ RIGHT (~35%): Calculator  │  (sticky)
 *   ├ Strategies: StrategyTable      │ StrategyExplanation       │
 *   └ TechnicalDetails (full width) ────────────────────────────┘
 *
 * Amount + selected-strategy live here (single source of truth) so the chart,
 * calculator, table and explanation all stay in sync. Mock data comes from
 * components/view-detail/strategies.ts — swap it for backend data later.
 *
 * The root layout locks `html, body { overflow: hidden }`; this is a long-scroll
 * page, so (like /view-pack) we release that lock on mount and restore it on
 * unmount.
 */

import * as React from "react";
import { Bookmark, Share2 } from "lucide-react";
import { ReturnsChart } from "@/components/view-detail/ReturnsChart";
import { StrategyCalculator } from "@/components/view-detail/StrategyCalculator";
import { StrategyExplanation } from "@/components/view-detail/StrategyExplanation";
import { StrategyTable } from "@/components/view-detail/StrategyTable";
import { TechnicalDetails } from "@/components/view-detail/TechnicalDetails";
import { STRATEGIES } from "@/components/view-detail/strategies";

const QUESTION = "Will the Nifty touch 30,000 before year-end?";
const SUBTITLE =
  "Three ways to express this belief with real money — from simply owning the market to a cheap long-shot bet. Pick an amount and see what each could become.";

/** The eyebrow above the title, e.g. "EQUITIES · INDEX". */
const CATEGORY = ["Equities", "Index"];
/** Thumbnail for the view. Leave null to render the themed placeholder tile. */
const THUMBNAIL: string | null = null;
/** Two hues for the placeholder tile when there's no thumbnail. */
const THEME: [string, string] = ["var(--pivot-blue)", "#0b1220"];

const DEFAULT_AMOUNT = 100_000;

export default function ViewDetailPage(): React.ReactElement {
  const [amount, setAmount] = React.useState<number>(DEFAULT_AMOUNT);
  const [selectedId, setSelectedId] = React.useState<string>(
    STRATEGIES[0]?.id ?? "",
  );

  const selected =
    STRATEGIES.find((s) => s.id === selectedId) ?? STRATEGIES[0] ?? null;

  // Release the root scroll lock for this long page; restore on unmount.
  React.useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const prevHtml = html.style.overflow;
    const prevBody = body.style.overflow;
    html.style.overflow = "auto";
    body.style.overflow = "auto";
    return () => {
      html.style.overflow = prevHtml;
      body.style.overflow = prevBody;
    };
  }, []);

  return (
    <div
      style={{
        height: "100dvh",
        overflowY: "auto",
        background: "var(--bg-app, var(--bg-base))",
      }}
    >
      <style>{`
        .vd-shell { max-width: 1240px; margin: 0 auto; padding: 36px 24px 96px; display: flex; flex-direction: column; gap: 36px; }
        .vd-main { display: grid; grid-template-columns: 1.85fr 1fr; gap: 40px; align-items: start; }
        .vd-sticky { position: sticky; top: 20px; }
        .vd-section { border-top: 1px solid var(--glass-border); padding-top: 28px; }
        .vd-strats { display: grid; grid-template-columns: 1.05fr 1fr; gap: 28px; align-items: stretch; }
        .vd-explain { border-left: 1px solid var(--glass-border); padding-left: 28px; }
        .vd-iconbtn:hover { background: var(--surface-hover) !important; color: var(--text-primary) !important; }
        @media (max-width: 940px) {
          .vd-main, .vd-strats { grid-template-columns: 1fr; }
          .vd-sticky { position: static; }
          .vd-shell { padding: 24px 16px 64px; gap: 28px; }
          .vd-explain { border-left: none; padding-left: 0; border-top: 1px solid var(--glass-border); padding-top: 20px; }
        }
        @media (max-width: 560px) {
          .vd-head { gap: 12px; }
          .vd-thumb { width: 56px !important; height: 56px !important; border-radius: 12px !important; }
        }
      `}</style>

      <div className="vd-shell">
        {/* header — Polymarket-style: square thumbnail + (eyebrow / title) */}
        <header style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div
            className="vd-head"
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 18,
            }}
          >
            <ViewThumbnail src={THUMBNAIL} theme={THEME} eyebrow={CATEGORY} />

            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  justifyContent: "space-between",
                  gap: 16,
                }}
              >
                <span
                  style={{
                    fontFamily: "var(--font-display)",
                    fontSize: 12,
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.08em",
                    color: "var(--text-tertiary)",
                  }}
                >
                  {CATEGORY.join(" · ")}
                </span>
                <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                  <HeaderIconButton label="Follow this view">
                    <Bookmark size={17} strokeWidth={1.75} />
                  </HeaderIconButton>
                  <HeaderIconButton label="Share">
                    <Share2 size={17} strokeWidth={1.75} />
                  </HeaderIconButton>
                </div>
              </div>
              <h1
                style={{
                  margin: "6px 0 0",
                  fontFamily: "var(--font-display)",
                  fontSize: "clamp(24px, 3vw, 33px)",
                  fontWeight: 700,
                  letterSpacing: "-0.025em",
                  lineHeight: 1.15,
                  color: "var(--text-primary)",
                }}
              >
                {QUESTION}
              </h1>
            </div>
          </div>
          <p
            style={{
              margin: 0,
              maxWidth: 720,
              fontFamily: "var(--font-display)",
              fontSize: 15,
              lineHeight: 1.55,
              color: "var(--text-secondary)",
            }}
          >
            {SUBTITLE}
          </p>

          {/* market-meta strip — hairline-separated facts, reads like a real
              market header (Kalshi-clean, but honest Pivot facts) */}
          <div
            className="vd-meta"
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "8px 0",
              marginTop: 10,
              paddingTop: 14,
              borderTop: "1px solid var(--glass-border)",
            }}
          >
            <MetaStat label="Resolves" value="31 Dec 2026" first />
            <MetaStat label="Horizon" value="~6 months" />
            <MetaStat label="Type" value="Index event" />
            <MetaStat label="Status" value="Open" tone="accent" />
          </div>
        </header>

        {/* main: returns graph + sticky calculator */}
        <section className="vd-main">
          <ReturnsChart
            amount={amount}
            strategies={STRATEGIES}
            highlightId={selectedId}
          />
          <div className="vd-sticky">
            <StrategyCalculator
              strategies={STRATEGIES}
              selectedId={selectedId}
              onSelect={setSelectedId}
              amount={amount}
              onAmount={setAmount}
            />
          </div>
        </section>

        {/* strategies: table + explanation */}
        <section
          className="vd-section"
          style={{ display: "flex", flexDirection: "column", gap: 18 }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <h2
              style={{
                margin: 0,
                fontFamily: "var(--font-display)",
                fontSize: 17,
                fontWeight: 650,
                letterSpacing: "-0.01em",
                color: "var(--text-primary)",
              }}
            >
              The three strategies
            </h2>
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 13,
                color: "var(--text-tertiary)",
              }}
            >
              Click a row to read what actually happens in that strategy.
            </span>
          </div>
          <div className="vd-strats">
            <StrategyTable
              strategies={STRATEGIES}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
            {selected && <StrategyExplanation strategy={selected} />}
          </div>
        </section>

        {/* technical details */}
        <section className="vd-section">
          <TechnicalDetails />
        </section>
      </div>
    </div>
  );
}

/**
 * The square thumbnail beside the view title (Polymarket-style). Renders the
 * image when `src` is set; otherwise a themed placeholder tile — a soft
 * diagonal gradient with the category initial, so every view still reads as a
 * distinct card even before art exists.
 */
function ViewThumbnail({
  src,
  theme,
  eyebrow,
}: {
  src: string | null;
  theme: [string, string];
  eyebrow: string[];
}): React.ReactElement {
  const initial = (eyebrow[0]?.[0] ?? "•").toUpperCase();
  return (
    <div
      className="vd-thumb"
      aria-hidden={!src}
      style={{
        flexShrink: 0,
        width: 72,
        height: 72,
        borderRadius: 16,
        overflow: "hidden",
        border: "1px solid var(--glass-border)",
        background: `linear-gradient(135deg, ${theme[0]}, ${theme[1]})`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        boxShadow: "0 1px 2px rgba(0,0,0,0.18)",
      }}
    >
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt=""
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      ) : (
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 30,
            fontWeight: 700,
            color: "rgba(255,255,255,0.92)",
            letterSpacing: "-0.02em",
          }}
        >
          {initial}
        </span>
      )}
    </div>
  );
}

/** One fact in the header market-meta strip, split by a vertical hairline. */
function MetaStat({
  label,
  value,
  first,
  tone,
}: {
  label: string;
  value: string;
  first?: boolean;
  tone?: "accent";
}): React.ReactElement {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 3,
        padding: first ? "0 18px 0 0" : "0 18px",
        borderLeft: first ? "none" : "1px solid var(--glass-border)",
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          color: "var(--text-tertiary)",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: "var(--font-display)",
          fontVariantNumeric: "tabular-nums",
          fontSize: 14,
          fontWeight: 600,
          color: tone === "accent" ? "var(--pivot-blue)" : "var(--text-primary)",
          whiteSpace: "nowrap",
        }}
      >
        {value}
      </span>
    </div>
  );
}

/** A subtle ghost icon button for the header action row. */
function HeaderIconButton({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className="vd-iconbtn"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 34,
        height: 34,
        borderRadius: 10,
        border: "none",
        background: "transparent",
        color: "var(--text-tertiary)",
        cursor: "pointer",
        transition:
          "background 160ms var(--ease-quartr), color 160ms var(--ease-quartr)",
      }}
    >
      {children}
    </button>
  );
}
