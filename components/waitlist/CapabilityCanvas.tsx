"use client";

/**
 * CapabilityCanvas — dark animated background with moving signal lines
 * and floating capability tiles.
 */

import { useEffect, useState } from "react";
import { ArrowUp } from "lucide-react";

type Capability = {
  kind: "action" | "alert" | "research" | "agent" | "build";
  prompts: string[];
  top: string;
  left: string;
};

const CAPABILITIES: Capability[] = [
  {
    kind: "action",
    top: "22%",
    left: "8%",
    prompts: [
      "Buy ₹50k of QQQ at market open",
      "Sell 200 RELIANCE at limit ₹2,840",
      "Place SIP of ₹10k in NIFTY BeES",
    ],
  },
  {
    kind: "alert",
    top: "14%",
    left: "62%",
    prompts: [
      "Alert me if TSLA drops 5% intraday",
      "Ping me when AAPL hits all-time high",
      "Notify me on RBI rate decision",
    ],
  },
  {
    kind: "research",
    top: "55%",
    left: "14%",
    prompts: [
      "Summarise INFY Q3 earnings",
      "Compare ICICI Bank vs HDFC Bank",
      "What's driving today's NIFTY move?",
    ],
  },
  {
    kind: "agent",
    top: "82%",
    left: "78%",
    prompts: [
      "Run my CPI hedge agent",
      "Square off losers above 8% drawdown",
      "Sweep cash above ₹20k into bonds",
    ],
  },
  {
    kind: "build",
    top: "38%",
    left: "78%",
    prompts: [
      "Build a barbell on TQQQ + BIL",
      "Create a covered-call security on NIFTY",
      "Design a momentum basket of 10 stocks",
    ],
  },
  {
    kind: "action",
    top: "82%",
    left: "18%",
    prompts: [
      "Backtest 50/200 SMA on RELIANCE",
      "Backtest RSI(14) mean reversion",
      "Stress-test my portfolio for -10% NIFTY",
    ],
  },
];

const LABEL: Record<Capability["kind"], string> = {
  action: "action",
  alert: "alert",
  research: "research",
  agent: "agent",
  build: "build",
};

export function CapabilityCanvas(): React.ReactElement {
  return (
    <div data-nav-theme="dark" className="relative isolate overflow-hidden bg-[#0a0a0b] text-white">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(255,255,255,0.06)_0%,transparent_55%)]"
      />

      <MovingLines />

      <div aria-hidden className="pointer-events-none absolute inset-0">
        {CAPABILITIES.map((c, idx) => (
          <Tile key={idx} cap={c} index={idx} />
        ))}
      </div>

      <div className="relative z-10 mx-auto flex max-w-3xl flex-col items-center px-6 py-44 text-center sm:py-60">
        <div className="mb-8 inline-flex items-center gap-2 text-[11px] uppercase tracking-[0.14em] text-white/70">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-white" />
          one assistant for everything investing
        </div>
        <h2 className="font-serif text-[44px] leading-[1.05] tracking-[-0.04em] text-white sm:text-[64px]">
          Hey! Pivot
        </h2>

        <ShowcaseChatbox />
      </div>
    </div>
  );
}

const SHOWCASE_SUFFIXES = [
  "build me a low-volatility SIP basket",
  "buy ₹50k of QQQ at market open tomorrow",
  "alert me if NIFTY drops 2% in a single day",
  "backtest a 50/200 SMA crossover on RELIANCE",
  "create a covered-call security on NIFTY",
  "summarise INFY's last earnings call",
  "square off losers above 8% drawdown",
];

function ShowcaseChatbox(): React.ReactElement {
  const [idx, setIdx] = useState(0);
  const [caret, setCaret] = useState(true);

  useEffect(() => {
    const cycle = setInterval(() => {
      setIdx((i) => (i + 1) % SHOWCASE_SUFFIXES.length);
    }, 2800);
    const blink = setInterval(() => setCaret((v) => !v), 520);
    return () => {
      clearInterval(cycle);
      clearInterval(blink);
    };
  }, []);

  return (
    <div className="mt-12 w-full max-w-2xl">
      <div
        className="flex items-center"
        style={{
          gap: 10,
          background: "rgba(255,255,255,0.04)",
          borderRadius: 9999,
          border: "1px solid rgba(255,255,255,0.12)",
          padding: "4px 4px 4px 20px",
          backdropFilter: "blur(8px)",
          WebkitBackdropFilter: "blur(8px)",
        }}
      >
        <div
          className="flex-1 truncate text-left"
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 14,
            lineHeight: "44px",
            color: "rgba(255,255,255,0.9)",
          }}
        >
          <span style={{ color: "rgba(255,255,255,0.95)", fontWeight: 600 }}>
            Hey! Pivot,
          </span>{" "}
          <span
            key={idx}
            style={{ color: "rgba(255,255,255,0.78)" }}
            className="animate-[promptIn_400ms_cubic-bezier(0.22,1,0.36,1)_both]"
          >
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
        </div>

        <div
          className="flex shrink-0 items-center justify-center"
          style={{
            width: 40,
            height: 40,
            borderRadius: 9999,
            background: "#ffffff",
            color: "#0d0d0e",
          }}
        >
          <ArrowUp size={18} strokeWidth={2} aria-hidden={true} />
        </div>
      </div>
    </div>
  );
}

function Tile({ cap }: { cap: Capability; index: number }): React.ReactElement {
  return (
    <div
      className="absolute -translate-x-1/2 -translate-y-1/2"
      style={{ top: cap.top, left: cap.left }}
    >
      <div className="mx-auto mb-2 h-1.5 w-1.5 rounded-full bg-white/60 shadow-[0_0_8px_rgba(255,255,255,0.5)]" />

      <div className="mb-2 inline-flex items-center gap-1.5 rounded-md bg-white/[0.06] px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-white/80 backdrop-blur-sm">
        <Bolt />
        {LABEL[cap.kind]}
      </div>

      <div className="rounded-xl border border-white/[0.08] bg-white/[0.035] px-3.5 py-2.5 backdrop-blur-sm">
        <div className="max-w-[220px] text-[12.5px] leading-snug text-white/85">
          {cap.prompts[0]}
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
