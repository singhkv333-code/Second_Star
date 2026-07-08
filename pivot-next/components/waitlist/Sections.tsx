"use client";

/**
 * Sections — the mid-scroll sections of the waitlist page.
 *
 * - BuildSecuritiesSection: showcase of synthetic securities Pivot can mint
 * - EventTriggersSection: row of "agent" cards with a centered highlighted card
 * - FAQSection: accordion of common questions
 * - WordmarkFooter: oversized "Pivot" type at the bottom (Standout-style)
 * - WaitlistFormBlock: the final dark CTA with email capture
 */

import { useEffect, useState } from "react";
import { ArrowUp, Check, ChevronLeft, ChevronRight, Square } from "lucide-react";

// ─── How it works ───────────────────────────────────────────────────────

type HowStep = {
  n: string;
  title: string;
  body: string;
  preview: React.ReactNode;
};

export function HowItWorksSection(): React.ReactElement {
  const steps: HowStep[] = [
    {
      n: "01",
      title: "You enter a prompt",
      body: "Describe what you want in plain English — buy, sell, alert, automate, or build a strategy.",
      preview: <PromptPreview />,
    },
    {
      n: "02",
      title: "Pivot tracks the market",
      body: "Pivot watches prices, signals, and macro events 24/7, waiting for your conditions to fire.",
      preview: <TrackingPreview />,
    },
    {
      n: "03",
      title: "Pivot executes the trade",
      body: "When the moment arrives, Pivot places the order through your brokerage and reports back.",
      preview: <OrderPlacedPreview />,
    },
  ];

  return (
    <section className="bg-white px-6 py-[6.5rem] sm:py-[7.5rem]">
      <div className="mx-auto max-w-6xl">
        <div className="mx-auto max-w-2xl text-center">
          <div className="mb-4 inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.14em] text-[#4d555c]">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#4d555c]" />
            World&apos;s first AI Native investment platform
          </div>
          <h2 className="font-serif text-[42px] leading-[1.05] tracking-[-0.04em] text-[#0d0d0e] sm:text-[56px]">
            How it works
          </h2>
          <p className="mt-5 text-[15px] leading-7 text-[#4d555c]">
            Three steps from idea to executed trade. No charts, no manual
            monitoring, no order tickets.
          </p>
        </div>

        <div className="mt-14 grid grid-cols-1 gap-5 sm:grid-cols-3">
          {steps.map((s) => (
            <HowStepCard key={s.n} step={s} />
          ))}
        </div>
      </div>
    </section>
  );
}

/**
 * HowStepCard — outer light shell with two stacked panes:
 *  • Top: tall dark "live preview" surface (the working in the real product).
 *  • Bottom: light caption strip with step number, title, body.
 */
function HowStepCard({ step }: { step: HowStep }): React.ReactElement {
  return (
    <div className="flex h-full flex-col">
      <div className="relative h-[280px] overflow-hidden rounded-2xl bg-[#0d0d0e]">
        {step.preview}
      </div>
      <div className="flex flex-1 flex-col pt-6">
        <span className="font-serif text-[22px] leading-none tracking-[-0.02em] text-[#8a8f96]">
          {step.n}
        </span>
        <h3 className="mt-4 text-[18px] font-semibold tracking-tight text-[#0d0d0e]">
          {step.title}
        </h3>
        <p className="mt-2 flex-1 text-[13.5px] leading-6 text-[#4d555c]">
          {step.body}
        </p>
      </div>
    </div>
  );
}

/**
 * PromptPreview — replays the full compose-and-send loop:
 *   1. Composer pill types "Hey! Pivot, Buy 7 shares of TATASTEEL"
 *      character-by-character with a blinking caret.
 *   2. On completion, the composer clears and the message appears
 *      above as a right-aligned user bubble matching production
 *      geometry (UserBubble in chat/ChatDemo.tsx) — asymmetric
 *      16/16/2/16 radius, dark-mode #1f2127 elevated surface,
 *      #fbfcfc ink.
 *   3. Brief hold, then reset and loop.
 */
const PROMPT_SUFFIX = "Buy 7 shares of TATASTEEL when it drops below 140";
const PROMPT_FULL = `Hey! Pivot, ${PROMPT_SUFFIX}`;

function PromptPreview(): React.ReactElement {
  const [chars, setChars] = useState(0);
  const [sent, setSent] = useState(false);
  const [caret, setCaret] = useState(true);

  useEffect(() => {
    const blink = setInterval(() => setCaret((v) => !v), 520);
    let timer: ReturnType<typeof setTimeout>;

    const tick = (next: number) => {
      if (next <= PROMPT_FULL.length) {
        setChars(next);
        timer = setTimeout(() => tick(next + 1), 70);
        return;
      }
      // Typing done — pause, then "send"
      timer = setTimeout(() => {
        setSent(true);
        // Hold the bubble visible, then reset to start typing again
        timer = setTimeout(() => {
          setSent(false);
          setChars(0);
          timer = setTimeout(() => tick(1), 700);
        }, 2600);
      }, 500);
    };

    timer = setTimeout(() => tick(1), 600);
    return () => {
      clearInterval(blink);
      clearTimeout(timer);
    };
  }, []);

  // Split the typed text so "Hey! Pivot," stays bold once it's been
  // typed past, with the rest in normal weight.
  const heyPart = "Hey! Pivot,";
  const typedHey = PROMPT_FULL.slice(0, Math.min(chars, heyPart.length));
  const typedRest = chars > heyPart.length ? PROMPT_FULL.slice(heyPart.length, chars) : "";

  return (
    <div className="relative flex h-full flex-col justify-end p-5">
      {/* Faint grid texture for "interface" feel */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-30"
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.05) 1px, transparent 1px)",
          backgroundSize: "24px 24px",
        }}
      />

      {/* Sent user bubble — only appears after the message is "sent". */}
      <div className="relative mb-3 flex min-h-[44px] justify-end">
        {sent && (
          <div
            className="animate-[bubbleIn_280ms_cubic-bezier(0.22,1,0.36,1)_both]"
            style={{
              maxWidth: "90%",
              padding: "10px 14px",
              borderRadius: "16px 16px 2px 16px",
              background: "#1f2127",
              fontSize: 11.5,
              color: "#fbfcfc",
              lineHeight: 1.4,
              fontFamily: "var(--font-ui)",
              wordBreak: "break-word",
            }}
          >
            {PROMPT_FULL}
          </div>
        )}
      </div>

      <div
        className="relative flex items-center"
        style={{
          gap: 8,
          background: "rgba(255,255,255,0.04)",
          borderRadius: 9999,
          border: "1px solid rgba(255,255,255,0.12)",
          padding: "4px 4px 4px 16px",
        }}
      >
        <div
          className="relative min-w-0 flex-1 overflow-hidden"
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 13,
            lineHeight: "32px",
            color: "rgba(255,255,255,0.92)",
            height: 32,
            whiteSpace: "nowrap",
          }}
        >
          {sent ? (
            <span style={{ color: "rgba(255,255,255,0.35)" }}>
              Ask Pivot anything…
            </span>
          ) : (
            // Inner span absolutely pinned to the right edge. When the
            // typed text exceeds the container width, the leftmost
            // characters slide off the left edge (clipped by
            // overflow:hidden) while the caret stays glued to the right
            // — exactly how a real text input scrolls horizontally as
            // you type past its visible width.
            <span
              className="absolute right-0 top-0 whitespace-nowrap"
              style={{ lineHeight: "32px" }}
            >
              <span style={{ fontWeight: 600 }}>{typedHey}</span>
              <span style={{ color: "rgba(255,255,255,0.78)" }}>
                {typedRest}
              </span>
              <span
                aria-hidden
                style={{
                  display: "inline-block",
                  width: 1.5,
                  height: 14,
                  marginLeft: 1,
                  verticalAlign: "middle",
                  background: caret ? "rgba(255,255,255,0.85)" : "transparent",
                }}
              />
            </span>
          )}
        </div>
        <div
          className="flex shrink-0 items-center justify-center"
          style={{
            width: 32,
            height: 32,
            borderRadius: 9999,
            background: "#ffffff",
            color: "#0d0d0e",
          }}
        >
          {sent ? (
            <Square
              size={12}
              strokeWidth={0}
              fill="currentColor"
              aria-hidden={true}
              style={{ borderRadius: 2 }}
            />
          ) : (
            <ArrowUp size={14} strokeWidth={2.25} aria-hidden={true} />
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * TrackingPreview — clean, single-composition watcher card. Tells the
 * story of the prompt in card 01: Pivot is watching TATASTEEL and
 * waiting for it to drop below ₹140.
 *
 * Layout (top → bottom, no scattered chips, no radar):
 *   1. Status row — symbol on the left, live indicator on the right.
 *   2. Hero price — display-size LTP with a quiet change pill.
 *   3. Chart — area sparkline with a dashed horizontal trigger line at
 *      ₹140 labelled "Trigger" so the watch condition is visualised
 *      directly on the data.
 *   4. Condition footer — short status sentence with a soft pulsing
 *      dot to signal the watcher is running.
 *
 * Mirrors StockSnapshotCard's hierarchy (eyebrow → name → price →
 * sparkline → footer) so it feels like a real product widget rather
 * than a marketing illustration.
 */
/**
 * TrackingPreview — live watcher card. The price ticker, the chart's
 * trailing edge, and the "Updated Xs ago" footer all advance on a
 * short interval so the card reads as a live feed at rest.
 *
 * Hierarchy is the same as a real StockSnapshotCard: eyebrow → symbol
 * → price → chart with trigger line → footer status.
 */
function TrackingPreview(): React.ReactElement {
  // Base price series — chart-space y where 0=top, 100=bottom. Maps
  // prices 138→145 to y=100→0 via priceToY. We mutate the series on a
  // timer to make the chart look alive.
  const TRIGGER = 140;
  const priceToY = (p: number): number => ((145 - p) / 7) * 100;
  const triggerY = priceToY(TRIGGER);
  const SEED: number[] = [
    143.2, 142.9, 143.4, 142.6, 142.1, 142.4, 141.7, 141.9,
    141.3, 141.6, 141.0, 141.2, 140.8, 141.1, 140.6, 141.0,
    140.4, 140.7,
  ];

  const [prices, setPrices] = useState<number[]>(() => [...SEED, 141.2]);
  const [ageSec, setAgeSec] = useState<number>(2);

  // Tick: append a new price (mean-reverted random walk toward 140.8)
  // and drop the oldest, so the chart keeps the same length but appears
  // to scroll one bucket per tick.
  useEffect(() => {
    const id = setInterval(() => {
      setPrices((prev) => {
        const last = prev[prev.length - 1] ?? 141;
        const drift = 140.8;
        const noise = (Math.random() - 0.5) * 0.4;
        let next = last + (drift - last) * 0.18 + noise;
        // Hover above the trigger so the visual story holds — never
        // dip below 140.0 in this preview (the order would fire).
        if (next < 140.15) next = 140.15 + Math.random() * 0.2;
        if (next > 142.4) next = 142.4 - Math.random() * 0.2;
        return [...prev.slice(1), Number(next.toFixed(2))];
      });
      setAgeSec(0);
    }, 1800);
    return () => clearInterval(id);
  }, []);

  // Age counter — increments each second between ticks
  useEffect(() => {
    const id = setInterval(() => setAgeSec((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const last = prices[prices.length - 1] ?? 141;
  const prev = prices[prices.length - 2] ?? last;
  const delta = last - prev;
  const deltaPct = (delta / prev) * 100;
  const fmt2 = (n: number): string => n.toFixed(2);
  const fmtDelta = (n: number): string => (n >= 0 ? `+${fmt2(n)}` : fmt2(n));
  const isDown = delta < 0;
  const deltaColor = isDown ? "text-rose-300" : "text-emerald-300";

  return (
    <div className="relative flex h-full flex-col px-5 py-4">
      {/* Status row */}
      <div>
        <div className="text-[9px] font-medium uppercase tracking-[0.16em] text-white/45">
          NSE · Equity
        </div>
        <div className="mt-0.5 text-[14px] font-semibold tracking-tight text-white">
          TATASTEEL
        </div>
      </div>

      {/* Hero price — re-keyed on every tick so it can play a subtle
          fade-in, making the number feel like it's being refreshed. */}
      <div className="mt-3 flex items-baseline gap-2">
        <span
          key={`p-${last}`}
          className="text-[26px] font-semibold leading-none tracking-tight tabular-nums text-white"
          style={{ animation: "tickFade 220ms ease-out both" }}
        >
          ₹{fmt2(last)}
        </span>
        <span
          key={`d-${last}`}
          className={`text-[10px] font-medium tabular-nums ${deltaColor}`}
          style={{ animation: "tickFade 220ms ease-out both" }}
        >
          {fmtDelta(delta)} · {fmtDelta(deltaPct)}%
        </span>
      </div>

      {/* Chart */}
      <div className="relative mt-4 flex-1">
        <TrackingChart prices={prices} priceToY={priceToY} triggerY={triggerY} />
      </div>

      {/* Condition footer */}
      <div className="mt-3 flex items-center justify-between border-t border-white/[0.06] pt-2.5 text-[10.5px] text-white/55">
        <span>Trigger when price &lt; ₹{fmt2(TRIGGER)}</span>
        <span className="tabular-nums text-white/35">
          Updated {ageSec}s ago
        </span>
      </div>
    </div>
  );
}

/**
 * TrackingChart — area sparkline with a horizontal dashed trigger line
 * and a soft pulsing dot at the latest point so the chart has a live
 * "edge" without needing the line itself to redraw. The line path
 * itself updates whenever the parent's `prices` array shifts.
 */
function TrackingChart({
  prices,
  priceToY,
  triggerY,
}: {
  prices: number[];
  priceToY: (p: number) => number;
  triggerY: number;
}): React.ReactElement {
  const stepX = 100 / (prices.length - 1);
  const pts: [number, number][] = prices.map((p, i) => [i * stepX, priceToY(p)]);
  const d = pts
    .map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(2)},${p[1].toFixed(2)}`)
    .join(" ");
  const dFill = `${d} L100,100 L0,100 Z`;
  const lastPt = pts[pts.length - 1] ?? [100, 50];

  return (
    <div className="relative h-full w-full">
      <svg
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        className="absolute inset-0 h-full w-full"
        aria-hidden
      >
        <defs>
          <linearGradient id="watch-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(255,255,255,0.18)" />
            <stop offset="100%" stopColor="rgba(255,255,255,0)" />
          </linearGradient>
        </defs>

        {/* Smooth transitions on the price line + area so the chart
            doesn't pop on each tick — it eases between states. */}
        <path
          d={dFill}
          fill="url(#watch-fill)"
          style={{ transition: "d 800ms ease" }}
        />
        <path
          d={d}
          fill="none"
          stroke="rgba(255,255,255,0.85)"
          strokeWidth="0.9"
          vectorEffect="non-scaling-stroke"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ transition: "d 800ms ease" }}
        />

        {/* Trigger line */}
        <line
          x1="0"
          y1={triggerY}
          x2="100"
          y2={triggerY}
          stroke="rgba(244,114,128,0.65)"
          strokeWidth="0.6"
          strokeDasharray="2 2"
          vectorEffect="non-scaling-stroke"
        />

        {/* Live cursor at the latest point — soft pulsing ring + solid
            dot, anchored at the price tip so the chart has a heartbeat. */}
        <circle
          cx={lastPt[0]}
          cy={lastPt[1]}
          r="2.4"
          fill="rgba(255,255,255,0.18)"
          style={{
            transition: "cx 800ms ease, cy 800ms ease",
            animation: "watchPulse 1.8s ease-out infinite",
            transformOrigin: `${lastPt[0]}px ${lastPt[1]}px`,
          }}
        />
        <circle
          cx={lastPt[0]}
          cy={lastPt[1]}
          r="1.1"
          fill="rgba(255,255,255,0.95)"
          style={{ transition: "cx 800ms ease, cy 800ms ease" }}
        />
      </svg>

      {/* "Trigger ₹140" tag glued to the dashed line, on the right */}
      <span
        className="absolute right-0 -translate-y-1/2 rounded-sm bg-rose-400/15 px-1.5 py-0.5 text-[9px] font-medium tabular-nums text-rose-300"
        style={{ top: `${triggerY}%` }}
      >
        Trigger ₹140
      </span>
    </div>
  );
}

/**
 * OrderPlacedPreview — dark-mode replica of the real LogicCardChip
 * widget from components/chat/LogicCardChip.tsx that the chat thread
 * shows after a BUY tool fires. Same structure:
 *   • Snapshot header (eyebrow exchange · sector, company name, ticker
 *     mono tag, right-aligned LTP + change pill, mini sparkline strip).
 *   • BUY action eyebrow with a green dot.
 *   • Hero row — "Estimated total" right-aligned value.
 *   • 3-column stat strip with hairline dividers — Qty / Price / Order type.
 *   • Footer CTA replaced with the "Placed" terminal state from the real
 *     card (animated check + label, replaces the Confirm button).
 *   • Amber disclaimer hairline footer.
 */
function OrderPlacedPreview(): React.ReactElement {
  return (
    <div className="relative flex h-full items-center justify-center p-3">
      <div
        className="w-full overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.03]"
        style={{
          boxShadow:
            "0 1px 2px rgba(0,0,0,0.4), 0 18px 36px -18px rgba(76,175,80,0.22)",
        }}
      >
        {/* Snapshot header */}
        <div className="flex items-start justify-between gap-3 px-3.5 pt-2.5 pb-1.5">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[8.5px] font-medium uppercase tracking-wider text-white/55">
              <span>NSE</span>
              <span className="text-white/30">·</span>
              <span>Steel</span>
            </div>
            <div className="mt-0.5 truncate text-[12.5px] font-semibold tracking-tight text-white">
              Tata Steel Ltd
            </div>
            <div className="text-[9px] font-medium tracking-wider text-white/45">
              TATASTEEL
            </div>
          </div>
          <div className="flex flex-col items-end">
            <div className="text-[12px] font-semibold tabular-nums text-white">
              ₹139.90
            </div>
            <div className="mt-0.5 inline-flex items-center gap-0.5 rounded-full bg-rose-400/15 px-1.5 py-[1px] text-[8.5px] font-medium tabular-nums text-rose-300">
              ▼ 1.08%
            </div>
          </div>
        </div>

        {/* Mini sparkline strip */}
        <div className="px-3.5 pb-1.5">
          <MiniSpark />
        </div>

        {/* BUY eyebrow + Hero row on a single line */}
        <div className="flex items-center justify-between px-3.5 pt-1 pb-2">
          <span className="inline-flex items-center gap-1 text-[8.5px] font-medium uppercase tracking-wider tabular-nums text-emerald-300">
            <span className="h-1 w-1 rounded-full bg-emerald-400" aria-hidden />
            BUY · Bought at
          </span>
          <span className="text-[11px] font-semibold tabular-nums text-white">
            ₹139.90
          </span>
        </div>

        {/* 3-col stat strip */}
        <dl className="grid grid-cols-3 border-t border-white/[0.08]">
          <StatCell label="Qty" value="7" />
          <StatCell label="Price" value="₹139.90" hasDivider />
          <StatCell label="Total" value="₹979.30" hasDivider />
        </dl>

        {/* "Placed" terminal state — same shape as LogicCardChip's done state */}
        <div className="border-t border-white/[0.08] px-3.5 py-2">
          <div className="flex h-6 w-full items-center justify-center gap-1.5 text-[11px] font-medium tracking-tight text-white">
            <DrawCheck />
            Placed
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * DrawCheck — check glyph whose stroke draws itself on, matching the
 * Claude copy-confirmation animation. The path is dashed at full length
 * and the offset is animated from full → 0, so the stroke appears to
 * be drawn in one continuous gesture. Re-keyed on a loop so the gesture
 * replays.
 */
function DrawCheck(): React.ReactElement {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 3200);
    return () => clearInterval(id);
  }, []);
  return (
    <svg
      key={tick}
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={3}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="text-white/85"
      aria-hidden
    >
      <path
        d="M20 6 9 17l-5-5"
        style={{
          strokeDasharray: 28,
          strokeDashoffset: 28,
          animation: "drawCheck 520ms cubic-bezier(0.65, 0, 0.35, 1) 60ms forwards",
        }}
      />
    </svg>
  );
}

function StatCell({
  label,
  value,
  hasDivider,
}: {
  label: string;
  value: string;
  hasDivider?: boolean;
}): React.ReactElement {
  return (
    <div
      className={`flex min-w-0 flex-col gap-0.5 px-2.5 py-1.5 ${hasDivider ? "border-l border-white/[0.08]" : ""}`}
    >
      <dt className="truncate text-[8.5px] font-medium uppercase tracking-wider text-white/45">
        {label}
      </dt>
      <dd className="truncate text-[10.5px] font-medium tabular-nums text-white">
        {value}
      </dd>
    </div>
  );
}

/**
 * MiniSpark — static SVG sparkline mimicking the LogicCardChip
 * SnapshotHeader sparkline. Pre-baked path so we don't trigger a network
 * fetch for /api/markets/sparkline in this dark preview.
 */
function MiniSpark(): React.ReactElement {
  // 24 points trending down — price drifts lower until it crosses ₹140,
  // which is the trigger condition for the prompt in card 01.
  const points: [number, number][] = [
    [0, 4],  [4, 5],   [8, 4],  [12, 6],  [16, 5],  [20, 7],
    [24, 6], [28, 8],  [32, 7], [36, 9],  [40, 8],  [44, 10],
    [48, 9], [52, 11], [56, 10], [60, 12], [64, 11], [68, 13],
    [72, 14], [76, 13], [80, 15], [84, 16], [88, 17], [92, 18],
  ];
  const d = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${p[0]},${p[1]}`)
    .join(" ");
  const dFill = `${d} L92,22 L0,22 Z`;
  return (
    <svg
      viewBox="0 0 92 22"
      width="100%"
      height="22"
      preserveAspectRatio="none"
      aria-hidden
    >
      <defs>
        <linearGradient id="spark-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgba(244,114,128,0.35)" />
          <stop offset="100%" stopColor="rgba(244,114,128,0)" />
        </linearGradient>
      </defs>
      <path d={dFill} fill="url(#spark-fill)" />
      <path d={d} fill="none" stroke="rgb(244,114,128)" strokeWidth="1.25" />
    </svg>
  );
}

function Bolt({ large }: { large?: boolean } = {}): React.ReactElement {
  const size = large ? 14 : 9;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
      <path d="M13 2 3 14h7l-1 8 10-12h-7l1-8Z" />
    </svg>
  );
}

// ─── Build your own Securities ──────────────────────────────────────────

type Security = {
  name: string;
  type: string;
  description: string;
  stats: { label: string; value: string }[];
};

const SECURITIES: Security[] = [
  {
    name: "Barbell",
    type: "Two-leg structured",
    description:
      "90% in short-duration T-bills, 10% in 3x leveraged QQQ. Capped downside, asymmetric upside.",
    stats: [
      { label: "Yield", value: "5.1%" },
      { label: "Max DD", value: "−4.2%" },
    ],
  },
  {
    name: "Covered Call NIFTY",
    type: "Income overlay",
    description:
      "Long NIFTY 50 with monthly OTM call selling. Generates premium against a tracked index core.",
    stats: [
      { label: "Premium", value: "0.8%/mo" },
      { label: "Cap", value: "+4%" },
    ],
  },
  {
    name: "Momentum Basket",
    type: "Equal-weight basket",
    description:
      "Top 10 NSE names ranked by 12-1 momentum, rebalanced monthly. Tilted toward winners, no shorts.",
    stats: [
      { label: "Names", value: "10" },
      { label: "Rebal", value: "Monthly" },
    ],
  },
];

export function BuildSecuritiesSection(): React.ReactElement {
  return (
    <section data-nav-theme="dark" className="relative isolate overflow-hidden bg-[#0a0a0b] px-6 py-28 text-white sm:py-36">
      {/* Background animation — slow-drifting glow orbs. Distinct from the
          moving-lines field in CapabilityCanvas above, so the two dark
          sections read differently while sharing the same base color. */}
      <DriftOrbs />

      {/* Subtle dot grid sits behind the orbs for texture */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.18]"
        style={{
          backgroundImage:
            "radial-gradient(rgba(255,255,255,0.18) 1px, transparent 1px)",
          backgroundSize: "28px 28px",
        }}
      />

      <div className="relative z-10 mx-auto max-w-6xl">
        <div className="mx-auto max-w-2xl text-center">
          <div className="mb-4 inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.14em] text-white/70">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-white" />
            create
          </div>
          <h2 className="font-serif text-[42px] leading-[1.05] tracking-[-0.04em] text-white sm:text-[56px]">
            Build your own securities
          </h2>
          <p className="mt-5 text-[15px] leading-7 text-white/65">
            Describe a payoff in plain English. Pivot composes the legs,
            sizes them to your risk, and tracks them as a single position.
          </p>
        </div>

        <div className="mt-14 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {SECURITIES.map((s) => (
            <SecurityCard key={s.name} sec={s} />
          ))}
        </div>
      </div>
    </section>
  );
}

/**
 * DriftOrbs — three large, soft radial blooms drifting on independent
 * loops. Pure CSS, GPU-cheap. Gives the dark canvas a different feel
 * from the moving signal lines above so the two stacked dark sections
 * stay visually distinct.
 */
function DriftOrbs(): React.ReactElement {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      {/* Bright white bloom — top-left */}
      <div
        className="absolute"
        style={{
          width: 720,
          height: 720,
          top: "-15%",
          left: "-12%",
          background:
            "radial-gradient(circle, rgba(255,255,255,0.22) 0%, rgba(255,255,255,0.06) 35%, transparent 70%)",
          filter: "blur(50px)",
          animation: "orbDrift1 16s ease-in-out infinite",
        }}
      />
      {/* Soft white bloom — right */}
      <div
        className="absolute"
        style={{
          width: 780,
          height: 780,
          top: "20%",
          right: "-15%",
          background:
            "radial-gradient(circle, rgba(255,255,255,0.18) 0%, rgba(255,255,255,0.05) 35%, transparent 70%)",
          filter: "blur(60px)",
          animation: "orbDrift2 20s ease-in-out infinite",
        }}
      />
      {/* Faint white bloom — bottom-center */}
      <div
        className="absolute"
        style={{
          width: 640,
          height: 640,
          bottom: "-20%",
          left: "30%",
          background:
            "radial-gradient(circle, rgba(255,255,255,0.16) 0%, rgba(255,255,255,0.04) 40%, transparent 70%)",
          filter: "blur(55px)",
          animation: "orbDrift3 24s ease-in-out infinite",
        }}
      />
    </div>
  );
}

function SecurityCard({ sec }: { sec: Security }): React.ReactElement {
  return (
    <div className="group flex flex-col rounded-2xl border border-white/[0.08] bg-white/[0.035] p-6 backdrop-blur-sm transition-all hover:border-white/20 hover:bg-white/[0.06]">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-medium uppercase tracking-wider text-white/55">
          {sec.type}
        </span>
        <img
          src="/pivot-icon.png"
          alt="Pivot"
          width={44}
          height={44}
          className="shrink-0"
          style={{ display: "block", objectFit: "contain" }}
        />
      </div>
      <h3 className="mt-4 text-[20px] font-semibold tracking-tight text-white">
        {sec.name}
      </h3>
      <p className="mt-2 flex-1 text-[13px] leading-6 text-white/65">
        {sec.description}
      </p>
      <div className="mt-5 flex items-center gap-6 border-t border-white/[0.08] pt-4">
        {sec.stats.map((st) => (
          <div key={st.label}>
            <div className="text-[10px] uppercase tracking-wider text-white/45">
              {st.label}
            </div>
            <div className="mt-0.5 text-[14px] font-semibold text-white">
              {st.value}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Event-based agents ─────────────────────────────────────────────────

type Agent = {
  title: string;
  body: string;
  tags: string[];
  featured?: boolean;
};

const AGENTS: Agent[] = [
  {
    title: "Fed rate cut protection",
    body: "If the Fed cuts rates, trim 10% of bank stocks and rotate into my high-growth tech holdings.",
    tags: ["Market Monitoring", "Trading Strategy"],
  },
  {
    title: "$5k covered calls",
    body: "Sell 10 covered calls on PLTR (strike: $50, 30 DTE).",
    tags: ["Trading Strategies"],
  },
  {
    title: "CPI hedge",
    body: "Sell 10% of my consumer-staples portfolio and reinvest the proceeds into my high-growth tech stocks if CPI is >4% next month.",
    tags: ["Market Monitoring", "Risk Management"],
  },
  {
    title: "Retail Therapy",
    body: "Track my retail holdings. When they are all down 10% from 30-day high, buy $100 of each.",
    tags: ["Trading Strategy", "Market Monitoring"],
  },
  {
    title: "Idle cash management",
    body: "Sweep any cash over $20,000 in from my checking account to my bond account.",
    tags: ["Fund Management"],
  },
  {
    title: "Earnings drift",
    body: "After AAPL prints, buy 1% of the position if guidance beats and revenue grows >8% YoY.",
    tags: ["Market Monitoring", "Trading Strategy"],
  },
  {
    title: "Volatility shield",
    body: "If VIX closes above 25 for two sessions, rotate 15% of equities into short-duration treasuries.",
    tags: ["Risk Management"],
  },
  {
    title: "Dividend harvest",
    body: "Sweep declared dividends into a 60/40 ETF basket on the day they land in cash.",
    tags: ["Fund Management"],
  },
];

export function EventTriggersSection(): React.ReactElement {
  const [offset, setOffset] = useState(0);
  const [dir, setDir] = useState<1 | -1>(1);
  const [tick, setTick] = useState(0);
  const VISIBLE = 5;
  const FEATURED_SLOT = 2;

  // The card that's freshly entering this rotation:
  //   • dir === 1  (▶): leftmost card just shifted out, new agent appears at the rightmost slot (i === VISIBLE - 1)
  //   • dir === -1 (◀): rightmost card just shifted out, new agent appears at the leftmost slot (i === 0)
  const enteringSlot = dir === 1 ? VISIBLE - 1 : 0;

  const visibleAgents = Array.from({ length: VISIBLE }, (_, i) => {
    // Modulo over a non-empty constant array is always in-bounds; assert
    // non-null so the spread yields a complete Agent (not Agent | undefined
    // under noUncheckedIndexedAccess).
    const agent = AGENTS[(offset + i) % AGENTS.length]!;
    return { ...agent, featured: i === FEATURED_SLOT };
  });

  const step = (d: 1 | -1): void => {
    setDir(d);
    setOffset((o) => (o + d + AGENTS.length) % AGENTS.length);
    setTick((t) => t + 1);
  };

  return (
    <section className="bg-white px-6 pb-28 pt-28 sm:pb-36 sm:pt-36">
      <div className="mx-auto max-w-6xl">
        <div className="mx-auto max-w-2xl text-center">
          <div className="mb-4 inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.14em] text-[#4d555c]">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#4d555c]" />
            automate
          </div>
          <h2 className="font-serif text-[42px] leading-[1.05] tracking-[-0.04em] text-[#0d0d0e] sm:text-[56px]">
            Set event-based triggers
          </h2>
          <p className="mt-5 text-[15px] leading-7 text-[#4d555c]">
            Wire an agent to a real-world signal — earnings, macro prints,
            price moves — and Pivot executes the plan you described.
          </p>
        </div>

        <div className="relative mt-14">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5 lg:grid-rows-[260px]">
            {visibleAgents.map((a, i) => {
              const isEntering = i === enteringSlot;
              // Stable per-slot key for cards that stay; entering slot uses
              // `tick` so React remounts only that one and replays its CSS
              // animation while the others sit still.
              const key = isEntering ? `enter-${tick}` : `slot-${i}`;
              return (
                <div
                  key={key}
                  className="h-full"
                  style={
                    isEntering
                      ? {
                          animation: `agentSlide${dir === 1 ? "Right" : "Left"} 420ms cubic-bezier(0.22,1,0.36,1) both`,
                        }
                      : undefined
                  }
                >
                  <AgentCard agent={a} />
                </div>
              );
            })}
          </div>

          {/* Full-height blurred bands behind the arrow columns */}
          <div
            aria-hidden
            className="pointer-events-none absolute left-0 top-0 z-[5] hidden h-full w-24 -translate-x-1/2 bg-white/15 backdrop-blur-[3px] lg:block"
          />
          <div
            aria-hidden
            className="pointer-events-none absolute right-0 top-0 z-[5] hidden h-full w-24 translate-x-1/2 bg-white/15 backdrop-blur-[3px] lg:block"
          />

          {/* Centered arrow controls overlaid on the grid */}
          <div className="pointer-events-none absolute inset-x-0 top-1/2 z-10 hidden -translate-y-1/2 items-center justify-between px-2 lg:flex">
            <button
              type="button"
              aria-label="Previous agents"
              onClick={() => step(-1)}
              className="pointer-events-auto flex h-10 w-10 -translate-x-1/2 items-center justify-center rounded-full border border-black/10 bg-white text-[#0d0d0e] shadow-[0_8px_24px_-12px_rgba(0,0,0,0.35)] transition hover:bg-black hover:text-white"
            >
              <ChevronLeft size={18} strokeWidth={2} />
            </button>
            <button
              type="button"
              aria-label="Next agents"
              onClick={() => step(1)}
              className="pointer-events-auto flex h-10 w-10 translate-x-1/2 items-center justify-center rounded-full border border-black/10 bg-white text-[#0d0d0e] shadow-[0_8px_24px_-12px_rgba(0,0,0,0.35)] transition hover:bg-black hover:text-white"
            >
              <ChevronRight size={18} strokeWidth={2} />
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

function AgentCard({ agent }: { agent: Agent }): React.ReactElement {
  if (agent.featured) {
    return (
      <div className="relative flex h-full flex-col rounded-2xl border border-[#0d0d0e] bg-[#0d0d0e] p-5 text-white shadow-[0_20px_50px_-20px_rgba(0,0,0,0.5)] lg:scale-[1.05]">
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center gap-1 rounded-md bg-white/10 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-white">
            <Bolt /> Agent
          </span>
          <span className="rounded-full bg-white px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-[#0d0d0e]">
            Featured
          </span>
        </div>
        <h3 className="mt-3 text-[15px] font-semibold tracking-tight">
          {agent.title}
        </h3>
        <p className="mt-2 flex-1 text-[12px] leading-5 text-white/75">
          {agent.body}
        </p>
        <div className="mt-4 flex flex-wrap gap-1.5">
          {agent.tags.map((t) => (
            <span
              key={t}
              className="rounded-full border border-white/15 bg-white/[0.05] px-2 py-0.5 text-[10px] text-white/80"
            >
              {t}
            </span>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col rounded-2xl border border-black/[0.08] bg-white p-5 transition-all hover:border-black/20">
      <span className="inline-flex w-fit items-center gap-1 rounded-md bg-black/[0.05] px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-[#4d555c]">
        <Bolt /> Agent
      </span>
      <h3 className="mt-3 text-[14px] font-semibold tracking-tight text-[#0d0d0e]">
        {agent.title}
      </h3>
      <p className="mt-2 flex-1 text-[11.5px] leading-5 text-[#4d555c]">
        {agent.body}
      </p>
      <div className="mt-4 flex flex-wrap gap-1.5">
        {agent.tags.map((t) => (
          <span
            key={t}
            className="rounded-full border border-black/[0.08] bg-[#f6f6f8] px-2 py-0.5 text-[9.5px] text-[#4d555c]"
          >
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}

// ─── FAQ ────────────────────────────────────────────────────────────────

const FAQS: { q: string; a: string }[] = [
  {
    q: "What is Pivot?",
    a: "Pivot is an agentic investing assistant. You describe what you want — buy, sell, alert, rebalance, research — and Pivot plans the execution, runs it, and reports back.",
  },
  {
    q: "How does Pivot place trades?",
    a: "Pivot connects to your brokerage. Every order is queued for your approval until you switch a specific agent or workflow to autonomous mode.",
  },
  {
    q: "Can I backtest my ideas?",
    a: "Yes. Describe a strategy in plain English and Pivot runs it on historical data, surfaces win-rate, drawdown, and CAGR, and lets you tune parameters from there.",
  },
  {
    q: "Is my money safe?",
    a: "Your funds stay with your regulated brokerage. Pivot never custodies cash or securities — it only orchestrates instructions you approve.",
  },
];

export function FAQSection(): React.ReactElement {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <section className="bg-white px-6 py-28">
      <div className="mx-auto grid max-w-6xl grid-cols-1 gap-12 lg:grid-cols-[280px_1fr]">
        <h2 className="font-serif text-[36px] leading-[1.05] tracking-[-0.04em] text-[#0d0d0e] sm:text-[44px]">
          FAQs
        </h2>
        <div className="divide-y divide-black/[0.08] border-t border-black/[0.08]">
          {FAQS.map((f, i) => {
            const isOpen = open === i;
            return (
              <div key={f.q} className="py-5">
                <button
                  type="button"
                  onClick={() => setOpen(isOpen ? null : i)}
                  className="flex w-full items-center justify-between gap-6 text-left"
                  aria-expanded={isOpen}
                >
                  <span className="text-[16px] font-medium text-[#0d0d0e]">
                    {f.q}
                  </span>
                  {/* Aave-style two-bar plus → minus glyph. The vertical bar
                      rotates 90° on open so it collapses onto the horizontal
                      bar, turning + into −. */}
                  <span
                    className="relative inline-block h-4 w-4 shrink-0 text-[#4d555c]"
                    aria-hidden
                  >
                    <span className="absolute left-1/2 top-1/2 h-px w-4 -translate-x-1/2 -translate-y-1/2 bg-current" />
                    <span
                      className={`absolute left-1/2 top-1/2 h-4 w-px -translate-x-1/2 -translate-y-1/2 bg-current transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] ${
                        isOpen ? "rotate-90" : ""
                      }`}
                    />
                  </span>
                </button>
                {/* CSS-only smooth height collapse using grid-rows trick:
                    parent animates from 0fr → 1fr; inner div has min-height:0
                    + overflow-hidden so the child measures its natural size. */}
                <div
                  className="grid transition-[grid-template-rows,opacity] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]"
                  style={{
                    gridTemplateRows: isOpen ? "1fr" : "0fr",
                    opacity: isOpen ? 1 : 0,
                  }}
                >
                  <div className="overflow-hidden">
                    <p className="mt-3 max-w-2xl text-[14px] leading-7 text-[#4d555c]">
                      {f.a}
                    </p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

// ─── Final CTA block ────────────────────────────────────────────────────

export function WaitlistFormBlock(): React.ReactElement {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setSubmitted(true);
  };

  return (
    <section data-nav-theme="dark" className="bg-[#0d0d0e] px-6 py-20 text-white sm:py-24">
      <div className="mx-auto max-w-3xl text-center">
        <h2 className="font-serif text-[44px] leading-[1.05] tracking-[-0.04em] sm:text-[64px]">
          One message.
        </h2>
        <h2 className="font-serif italic text-[44px] leading-[1.05] tracking-[-0.04em] text-white/85 sm:text-[64px]">
          That&apos;s all it takes.
        </h2>

        {submitted ? (
          <div
            role="status"
            className="mx-auto mt-10 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/[0.05] px-5 py-3 text-[14px] text-white/85"
          >
            <CheckIcon /> You&apos;re on the list. We&apos;ll reach out soon.
          </div>
        ) : (
          <form
            onSubmit={onSubmit}
            className="mx-auto mt-10 flex max-w-md flex-col items-center gap-3 rounded-full border border-white/10 bg-white/[0.04] p-1.5 sm:flex-row"
          >
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="your@email.com"
              className="w-full flex-1 rounded-full bg-transparent px-4 py-2.5 text-[14px] text-white placeholder:text-white/45 focus:outline-none"
            />
            <button
              type="submit"
              className="w-full whitespace-nowrap rounded-full bg-white px-5 py-2.5 text-[14px] font-medium text-[#0d0d0e] transition-opacity hover:opacity-90 sm:w-auto"
            >
              Request access
            </button>
          </form>
        )}
      </div>
    </section>
  );
}

function CheckIcon(): React.ReactElement {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

// ─── Big wordmark footer (Standout-style) ──────────────────────────────

export function WordmarkFooter(): React.ReactElement {
  return (
    <footer data-nav-theme="dark" className="relative overflow-hidden bg-[#0a0a0b] text-white">
      {/* Subtle moving lines on the deep green so it doesn't feel flat */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        {[20, 50, 80].map((leftPct, i) => (
          <div
            key={leftPct}
            className="absolute top-0 h-full w-px bg-white/[0.05]"
            style={{ left: `${leftPct}%` }}
          >
            <div
              className="absolute left-0 h-[200px] w-px"
              style={{
                background:
                  "linear-gradient(180deg, transparent 0%, rgba(255,255,255,0.35) 50%, transparent 100%)",
                animation: `vSweep ${9 + i}s linear ${i * 0.9}s infinite`,
              }}
            />
          </div>
        ))}
      </div>

      <div className="relative mx-auto flex max-w-7xl flex-col items-center gap-10 px-6 pb-6 pt-24">
        <div
          aria-hidden
          className="grid w-full grid-cols-1 gap-10 text-[13px] text-transparent sm:grid-cols-3"
        >
          <div>
            <div className="text-[11px] uppercase tracking-[0.14em] text-transparent">
              &nbsp;
            </div>
            <ul className="mt-3 space-y-2">
              <li>&nbsp;</li>
              <li>&nbsp;</li>
              <li>&nbsp;</li>
              <li>&nbsp;</li>
            </ul>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-[0.14em] text-transparent">
              &nbsp;
            </div>
            <ul className="mt-3 space-y-2">
              <li>&nbsp;</li>
              <li>&nbsp;</li>
              <li>&nbsp;</li>
              <li>&nbsp;</li>
            </ul>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-[0.14em] text-transparent">
              &nbsp;
            </div>
            <ul className="mt-3 space-y-2">
              <li>&nbsp;</li>
              <li>&nbsp;</li>
              <li>&nbsp;</li>
              <li>&nbsp;</li>
            </ul>
          </div>
        </div>

        {/* Oversized wordmark — clips at bottom like Standout */}
        <div className="-mb-[6vw] w-full select-none text-center leading-[0.85]">
          <span
            className="block font-serif italic tracking-[-0.05em] text-white"
            style={{ fontSize: "clamp(140px, 24vw, 380px)" }}
          >
            Pivot.
          </span>
        </div>
      </div>
    </footer>
  );
}
