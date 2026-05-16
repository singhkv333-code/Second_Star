"use client";

/**
 * CapabilityCanvas — dark animated background with moving signal lines
 * and floating capability tiles.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUp } from "lucide-react";
import { Reveal } from "@/components/waitlist/scroll-fx";

type Capability = {
  kind: "alert" | "action" | "research" | "backtest" | "build" | "agent" | "portfolio";
  prompt: string;
  top: string;
  left: string;
};

type CapabilitySeed = Omit<Capability, "top" | "left">;

const CAPABILITY_SEEDS: CapabilitySeed[] = [
  { kind: "alert", prompt: "Alert me if BANKNIFTY drops by 5%" },
  { kind: "action", prompt: "Buy ₹50K of TECHM" },
  { kind: "build", prompt: "Build me a capital-protective security with equity exposure" },
  { kind: "agent", prompt: "Create an agent that buys SBIN on open and sells at close daily" },
  { kind: "portfolio", prompt: "Analyze my portfolio and suggest any rebalancing" },
  { kind: "backtest", prompt: "Backtest buy when RSI<30 and sell when RSI>70 on JSWSTEEL" },
  { kind: "research", prompt: "Give me a list of all pharma stocks whose P/E < 25" },
];

// Place tiles on an ellipse around the central title + chatbox.
// Angle 0 = right, sweeps clockwise. Starts at -90° (top) and goes around.
const ELLIPSE = { cx: 50, cy: 50, rx: 40, ry: 33 };

const N = CAPABILITY_SEEDS.length;
const STEP = 360 / N;
// Offset by half a step so tiles flank (not cover) the central title.
const START_ANGLE = -90 - STEP / 2;

const CAPABILITIES: Capability[] = CAPABILITY_SEEDS.map((seed, i) => {
  const angle = (START_ANGLE + STEP * i) * (Math.PI / 180);
  const left = ELLIPSE.cx + ELLIPSE.rx * Math.cos(angle);
  const top = ELLIPSE.cy + ELLIPSE.ry * Math.sin(angle);
  return { ...seed, top: `${top.toFixed(2)}%`, left: `${left.toFixed(2)}%` };
});

const LABEL: Record<Capability["kind"], string> = {
  alert: "alert",
  action: "action",
  research: "research",
  backtest: "backtest",
  build: "build",
  agent: "agent",
  portfolio: "portfolio",
};

export function CapabilityCanvas(): React.ReactElement {
  return (
    <div data-nav-theme="dark" className="relative isolate overflow-hidden bg-[#0a0a0b] py-14 text-white sm:py-28 lg:py-32">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(255,255,255,0.06)_0%,transparent_55%)]"
      />

      <MovingLines />

      <div className="relative">
        <div aria-hidden className="pointer-events-none absolute inset-0 hidden lg:block">
          {CAPABILITIES.map((c, idx) => (
            <Tile key={idx} cap={c} index={idx} />
          ))}
        </div>

        <Reveal className="relative z-10 mx-auto flex max-w-3xl flex-col items-center px-5 py-10 text-center sm:px-6 sm:py-24 lg:py-48">
          <div className="mb-6 inline-flex items-center gap-2 text-[10.5px] uppercase tracking-[0.14em] text-white/70 sm:mb-8 sm:text-[11px]">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-white" />
            one assistant for everything investing
          </div>
          <h2 className="font-serif text-[40px] leading-[1.04] tracking-[-0.03em] text-white sm:text-[56px] sm:tracking-[-0.04em] sm:leading-[1.05] lg:text-[64px]">
            Hey! Pivot
          </h2>

          <ShowcaseChatbox />

          <MobileCapabilityList />
        </Reveal>
      </div>
    </div>
  );
}

const SHOWCASE_SUFFIXES = [
  "alert me if BANKNIFTY drops by 5%",
  "buy ₹50K of TECHM",
  "build me a capital-protective security with equity exposure",
  "create an agent that buys SBIN on open and sells at close daily",
  "analyze my portfolio and suggest any rebalancing",
  "backtest buy when RSI<30 and sell when RSI>70 on JSWSTEEL",
  "give me a list of all pharma stocks whose P/E < 25",
];

function ShowcaseChatbox(): React.ReactElement {
  const CYCLE_MS = 2800;
  const [idx, setIdx] = useState(0);
  const [caret, setCaret] = useState(true);

  // Marquee state — when the prompt is wider than the viewport, slide the
  // text left over the cycle duration so the full prompt becomes readable
  // instead of getting clipped by `truncate`.
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const trackRef = useRef<HTMLSpanElement | null>(null);
  const [overflowPx, setOverflowPx] = useState(0);

  useEffect(() => {
    const cycle = setInterval(() => {
      setIdx((i) => (i + 1) % SHOWCASE_SUFFIXES.length);
    }, CYCLE_MS);
    const blink = setInterval(() => setCaret((v) => !v), 520);
    return () => {
      clearInterval(cycle);
      clearInterval(blink);
    };
  }, []);

  useLayoutEffect(() => {
    const measure = () => {
      const vp = viewportRef.current;
      const tr = trackRef.current;
      if (!vp || !tr) return;
      const diff = tr.scrollWidth - vp.clientWidth;
      setOverflowPx(diff > 0 ? diff : 0);
    };
    measure();
    const ro = new ResizeObserver(measure);
    if (viewportRef.current) ro.observe(viewportRef.current);
    return () => ro.disconnect();
  }, [idx]);

  // Hold the start and end positions briefly so the user can read both
  // edges; the middle of the cycle scrolls the text across.
  const HOLD_PCT = 18;
  const animDuration = `${CYCLE_MS}ms`;
  const animName = overflowPx > 0 ? "promptMarquee" : "promptIn";

  return (
    <div className="mt-8 w-full max-w-2xl sm:mt-12">
      <style>{`
        @keyframes promptMarquee {
          0% { transform: translateX(0); }
          ${HOLD_PCT}% { transform: translateX(0); }
          ${100 - HOLD_PCT}% { transform: translateX(var(--marquee-end)); }
          100% { transform: translateX(var(--marquee-end)); }
        }
      `}</style>
      <div
        className="flex items-center gap-2 px-3 py-1 sm:gap-2.5 sm:px-5"
        style={{
          background: "rgba(255,255,255,0.04)",
          borderRadius: 9999,
          border: "1px solid rgba(255,255,255,0.12)",
          paddingTop: 4,
          paddingBottom: 4,
          backdropFilter: "blur(8px)",
          WebkitBackdropFilter: "blur(8px)",
        }}
      >
        <div
          ref={viewportRef}
          className="relative flex-1 overflow-hidden text-left text-[13px] leading-[36px] sm:text-[14px] sm:leading-[44px]"
          style={{
            fontFamily: "var(--font-ui)",
            color: "rgba(255,255,255,0.9)",
          }}
        >
          <span
            ref={trackRef}
            key={idx}
            className="inline-block whitespace-nowrap will-change-transform"
            style={{
              ["--marquee-end" as string]: `-${overflowPx}px`,
              animation: `${animName} ${animDuration} cubic-bezier(0.4, 0, 0.2, 1) both`,
            }}
          >
            <span style={{ color: "rgba(255,255,255,0.95)", fontWeight: 600 }}>
              Hey! Pivot,
            </span>{" "}
            <span style={{ color: "rgba(255,255,255,0.78)" }}>
              {SHOWCASE_SUFFIXES[idx]}
            </span>
            <span
              aria-hidden
              style={{
                display: "inline-block",
                width: 1.5,
                height: 16,
                marginLeft: 2,
                verticalAlign: "middle",
                background: caret ? "rgba(255,255,255,0.85)" : "transparent",
                transition: "background 80ms linear",
              }}
            />
          </span>
        </div>

        <div
          className="flex h-8 w-8 shrink-0 items-center justify-center sm:h-10 sm:w-10"
          style={{
            borderRadius: 9999,
            background: "#ffffff",
            color: "#0d0d0e",
          }}
        >
          <ArrowUp size={16} strokeWidth={2} aria-hidden={true} />
        </div>
      </div>
    </div>
  );
}

function MobileCapabilityList(): React.ReactElement {
  // Mobile (<sm): 2-col compact grid, drop the most technical example to keep it tight.
  // sm and up: original single/double column with full padding.
  const mobileItems = CAPABILITIES.filter((c) => c.kind !== "backtest");
  return (
    <>
      {/* Mobile: tight 2-col grid */}
      <div className="mt-8 grid w-full grid-cols-2 gap-2.5 sm:hidden">
        {mobileItems.map((c, idx) => (
          <div
            key={idx}
            className="flex flex-col rounded-xl border border-white/[0.08] bg-white/[0.035] p-2.5 text-left backdrop-blur-sm"
          >
            <div className="mb-1.5 inline-flex w-fit items-center gap-1 rounded bg-white/[0.06] px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-white/80">
              <Bolt />
              {LABEL[c.kind]}
            </div>
            <div className="text-[11.5px] leading-[1.35] text-white/85">
              {c.prompt}
            </div>
          </div>
        ))}
      </div>

      {/* Tablet and up (sm – lg): drop the `backtest` tile so the 2-col grid
          stays symmetric. The dropped prompt still cycles through the chat
          above, so nothing actually disappears from the showcase. */}
      <div className="mt-10 hidden w-full grid-cols-1 gap-3 sm:mt-12 sm:grid sm:grid-cols-2 lg:hidden">
        {CAPABILITIES.filter((c) => c.kind !== "backtest").map((c, idx) => (
          <div
            key={idx}
            className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-4 text-left backdrop-blur-sm"
          >
            <div className="mb-2 inline-flex items-center gap-1.5 rounded-md bg-white/[0.06] px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-white/80">
              <Bolt />
              {LABEL[c.kind]}
            </div>
            <div className="text-[13px] leading-snug text-white/85">
              {c.prompt}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function Tile({ cap }: { cap: Capability; index: number }): React.ReactElement {
  return (
    <div
      className="absolute -translate-x-1/2 -translate-y-1/2"
      style={{ top: cap.top, left: cap.left }}
    >
      <div className="mx-auto mb-2 h-1.5 w-1.5 rounded-full bg-white/60 shadow-[0_0_8px_rgba(255,255,255,0.5)]" />

      <div className="mb-3 inline-flex items-center gap-1.5 rounded-md bg-white/[0.06] px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-white/80 backdrop-blur-sm">
        <Bolt />
        {LABEL[cap.kind]}
      </div>

      <div className="rounded-xl border border-white/[0.08] bg-white/[0.035] px-3.5 py-2.5 backdrop-blur-sm">
        <div className="max-w-[240px] text-[12.5px] leading-snug text-white/85">
          {cap.prompt}
        </div>
      </div>
    </div>
  );
}

function Bolt(): React.ReactElement {
  return (
    <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor">
      <path d="M13 2 3 14h7l-1 8 10-12h-7l1-8Z" />
    </svg>
  );
}

function MovingLines(): React.ReactElement {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      {[18, 32, 50, 68, 84].map((leftPct, i) => (
        <div
          key={`v-${leftPct}`}
          className="absolute top-0 h-full w-px bg-white/[0.04]"
          style={{ left: `${leftPct}%` }}
        >
          <div
            className="absolute left-0 h-[160px] w-px"
            style={{
              background:
                "linear-gradient(180deg, transparent 0%, rgba(255,255,255,0.55) 50%, transparent 100%)",
              animation: `vSweep ${7 + i * 1.3}s linear ${i * 0.7}s infinite`,
            }}
          />
        </div>
      ))}

      {[22, 44, 64, 82].map((topPct, i) => (
        <div
          key={`h-${topPct}`}
          className="absolute left-0 h-px w-full bg-white/[0.04]"
          style={{ top: `${topPct}%` }}
        >
          <div
            className="absolute top-0 h-px w-[180px]"
            style={{
              background:
                "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.45) 50%, transparent 100%)",
              animation: `hSweep ${8 + i * 1.1}s linear ${i * 1.2}s infinite`,
            }}
          />
        </div>
      ))}
    </div>
  );
}
