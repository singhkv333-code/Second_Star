"use client";

/**
 * Sections — the mid-scroll sections of the waitlist page.
 *
 * - HowItWorksSection: three step cards with live previews.
 * - BuildSecuritiesSection: showcase of synthetic securities.
 * - EventTriggersSection: row of "agent" cards with a centered featured card.
 * - FAQSection: accordion.
 * - WordmarkFooter: oversized "Pivot" type at the bottom.
 * - WaitlistFormBlock: the final dark CTA with email capture.
 */

import { useEffect, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Square,
  ArrowUp,
  CalendarClock,
  GitBranch,
  ShoppingCart,
  TrendingUp,
  Instagram,
  Twitter,
} from "lucide-react";
import { Reveal } from "@/components/waitlist/scroll-fx";

// ─── How it works ───────────────────────────────────────────────────────

type HowStep = {
  n: string;
  title: string;
  body: string;
  preview: React.ReactNode;
};

type WorkflowStep = {
  iconName: "calendar-clock" | "git-branch" | "shopping-cart" | "trending-up";
  kind: string;
  label: string;
};

type OrderScenario = {
  exchange: string;
  sector: string;
  name: string;
  ticker: string;
  tag?: string;
  price: string;
  deltaPct: string;
  side: "BUY" | "SELL";
  sideVerb: string;
  qty: string;
  totalLabel: string;
  total: string;
  trend: "up" | "down";
};

type Scenario = {
  prompt: string;
  workflowTitle: string;
  steps: [WorkflowStep, WorkflowStep, WorkflowStep];
  order: OrderScenario;
};

const SCENARIOS: Scenario[] = [
  {
    prompt: "Hey! Pivot, Buy 7 shares of TATASTEEL when it drops below 140",
    workflowTitle: "TATASTEEL dip buy",
    steps: [
      { iconName: "trending-up", kind: "Trigger", label: "TATASTEEL price ≤ ₹140" },
      { iconName: "git-branch", kind: "Condition", label: "Confirm dip on NSE feed" },
      { iconName: "shopping-cart", kind: "Action", label: "Buy 7 shares of TATASTEEL" },
    ],
    order: {
      exchange: "NSE",
      sector: "Steel",
      name: "Tata Steel Ltd",
      ticker: "TATASTEEL",
      price: "₹139.90",
      deltaPct: "▼ 1.08%",
      side: "BUY",
      sideVerb: "Bought at",
      qty: "7",
      totalLabel: "₹139.90",
      total: "₹979.30",
      trend: "down",
    },
  },
  {
    prompt:
      "Hey! Pivot, start a monthly SIP of ₹5,000 in HDFCBANK whenever RBI cuts repo rate",
    workflowTitle: "Repo-rate SIP",
    steps: [
      { iconName: "calendar-clock", kind: "Trigger", label: "When RBI cuts repo rate" },
      { iconName: "git-branch", kind: "Condition", label: "Cut is confirmed by RBI" },
      { iconName: "shopping-cart", kind: "Action", label: "Buy ₹5,000 of HDFCBANK" },
    ],
    order: {
      exchange: "NSE",
      sector: "Bank",
      name: "HDFC Bank Ltd",
      ticker: "HDFCBANK",
      tag: "SIP · Monthly",
      price: "₹1,612.40",
      deltaPct: "▲ 0.42%",
      side: "BUY",
      sideVerb: "Bought at",
      qty: "3.1",
      totalLabel: "₹1,612.40",
      total: "₹5,000",
      trend: "up",
    },
  },
  {
    prompt: "Hey! Pivot, sell all my ETERNAL shares if it crosses ₹300",
    workflowTitle: "ETERNAL take-profit",
    steps: [
      { iconName: "trending-up", kind: "Trigger", label: "ETERNAL price ≥ ₹300" },
      { iconName: "git-branch", kind: "Condition", label: "Confirm crossover holds" },
      { iconName: "shopping-cart", kind: "Action", label: "Sell all ETERNAL shares" },
    ],
    order: {
      exchange: "NSE",
      sector: "Consumer",
      name: "Eternal Ltd",
      ticker: "ETERNAL",
      price: "₹301.20",
      deltaPct: "▲ 0.84%",
      side: "SELL",
      sideVerb: "Sold at",
      qty: "40",
      totalLabel: "₹301.20",
      total: "₹12,048",
      trend: "up",
    },
  },
];

export function HowItWorksSection(): React.ReactElement {
  const [idx, setIdx] = useState(0);
  const scenario = SCENARIOS[idx]!;

  const handlePromptDone = (): void => {
    setIdx((i) => (i + 1) % SCENARIOS.length);
  };

  const steps: HowStep[] = [
    {
      n: "01",
      title: "You enter a prompt",
      body: "Describe what you want in plain English — buy, sell, alert, automate, or build a strategy.",
      preview: <PromptPreview prompt={scenario.prompt} onDone={handlePromptDone} />,
    },
    {
      n: "02",
      title: "Pivot tracks the market",
      body: "Pivot watches prices, signals, and macro events 24/7, waiting for your conditions to fire.",
      preview: (
        <TrackingPreview
          key={`track-${idx}`}
          title={scenario.workflowTitle}
          steps={scenario.steps}
        />
      ),
    },
    {
      n: "03",
      title: "Pivot executes the trade",
      body: "When the moment arrives, Pivot places the order through your brokerage and reports back.",
      preview: <OrderPlacedPreview key={`order-${idx}`} order={scenario.order} />,
    },
  ];

  return (
    <section id="how-it-works" className="scroll-mt-24 bg-white px-5 py-14 sm:px-6 sm:py-28 lg:py-32">
      <div className="mx-auto max-w-6xl">
        <Reveal className="mx-auto max-w-2xl text-center">
          <div className="mb-4 inline-flex items-center gap-2 text-[10.5px] uppercase tracking-[0.14em] text-[#4d555c] sm:text-[11px]">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#4d555c]" />
            <span className="hidden sm:inline">World&apos;s first AI Native investment platform</span>
            <span className="sm:hidden">AI Native investment platform</span>
          </div>
          <h2 className="font-serif text-[36px] leading-[1.04] tracking-[-0.03em] text-[#0d0d0e] sm:text-[48px] sm:tracking-[-0.04em] sm:leading-[1.05] lg:text-[56px]">
            How it works
          </h2>
          <p className="mt-4 text-[14.5px] leading-[1.6] text-[#4d555c] sm:mt-5 sm:text-[15px] sm:leading-7">
            Three steps from idea to executed trade. No charts, no manual
            monitoring, no order tickets.
          </p>
        </Reveal>

        <Reveal delay={120} className="mt-8 grid grid-cols-1 gap-4 sm:mt-14 sm:gap-5 sm:grid-cols-3">
          {steps.map((s) => (
            <HowStepCard key={s.n} step={s} />
          ))}
        </Reveal>
      </div>
    </section>
  );
}

function HowStepCard({ step }: { step: HowStep }): React.ReactElement {
  return (
    <div className="flex h-full flex-col">
      <div className="relative h-[260px] overflow-hidden rounded-2xl bg-[#0d0d0e] sm:h-[280px]">
        {step.preview}
      </div>
      <div className="flex flex-1 flex-col pt-3.5 sm:pt-5">
        <span className="font-serif text-[20px] leading-none tracking-[-0.02em] text-[#8a8f96] sm:text-[22px]">
          {step.n}
        </span>
        <h3 className="mt-2 text-[16px] font-semibold tracking-tight text-[#0d0d0e] sm:mt-3 sm:text-[18px]">
          {step.title}
        </h3>
        <p className="mt-1.5 flex-1 text-[12.5px] leading-[1.5] text-[#4d555c] sm:mt-2 sm:text-[13.5px] sm:leading-6">
          {step.body}
        </p>
      </div>
    </div>
  );
}

function PromptPreview({
  prompt,
  onDone,
}: {
  prompt: string;
  onDone: () => void;
}): React.ReactElement {
  const [chars, setChars] = useState(0);
  const [sent, setSent] = useState(false);
  const [caret, setCaret] = useState(true);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  useEffect(() => {
    setChars(0);
    setSent(false);
    const blink = setInterval(() => setCaret((v) => !v), 520);
    let timer: ReturnType<typeof setTimeout>;

    const tick = (next: number) => {
      if (next <= prompt.length) {
        setChars(next);
        timer = setTimeout(() => tick(next + 1), 50);
        return;
      }
      timer = setTimeout(() => {
        setSent(true);
        timer = setTimeout(() => {
          onDoneRef.current();
        }, 3000);
      }, 450);
    };

    timer = setTimeout(() => tick(1), 500);
    return () => {
      clearInterval(blink);
      clearTimeout(timer);
    };
  }, [prompt]);

  const heyPart = "Hey! Pivot,";
  const typedHey = prompt.slice(0, Math.min(chars, heyPart.length));
  const typedRest = chars > heyPart.length ? prompt.slice(heyPart.length, chars) : "";

  return (
    <div className="relative flex h-full flex-col justify-end p-5">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-30"
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.05) 1px, transparent 1px)",
          backgroundSize: "24px 24px",
        }}
      />

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
            {prompt}
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

function TrackingPreview({
  title,
  steps,
}: {
  title: string;
  steps: WorkflowStep[];
}): React.ReactElement {
  const renderIcon = (name: WorkflowStep["iconName"]): React.ReactNode => {
    const cls = "h-3.5 w-3.5";
    if (name === "calendar-clock") return <CalendarClock className={cls} aria-hidden />;
    if (name === "git-branch") return <GitBranch className={cls} aria-hidden />;
    if (name === "shopping-cart") return <ShoppingCart className={cls} aria-hidden />;
    return <TrendingUp className={cls} aria-hidden />;
  };

  return (
    <div className="relative flex h-full flex-col px-4 py-4">
      <div className="min-w-0">
        <div className="text-[9px] font-medium uppercase tracking-[0.16em] text-white/45">
          Workflow · Draft
        </div>
        <div className="mt-0.5 truncate text-[13px] font-semibold tracking-tight text-white">
          {title}
        </div>
      </div>

      <ol className="mt-3 flex flex-1 flex-col gap-1.5">
        {steps.map((s, i) => (
          <li
            key={i}
            className="relative rounded-lg opacity-0"
            style={{
              animation: "stepIn-quartr 420ms cubic-bezier(0.22,1,0.36,1) both",
              animationDelay: `${i * 420}ms`,
            }}
          >
            <span
              aria-hidden
              className="pointer-events-none absolute inset-0 rounded-lg"
              style={{
                padding: 1,
                background:
                  "conic-gradient(from var(--angle), transparent 0deg, transparent 280deg, rgba(255,255,255,0.85) 320deg, rgba(255,255,255,0.0) 360deg)",
                WebkitMask:
                  "linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0)",
                WebkitMaskComposite: "xor",
                maskComposite: "exclude",
                animation: `borderTrace 2.6s linear infinite`,
                animationDelay: `${i * 420 + 520}ms`,
              }}
            />
            <div className="relative flex items-center gap-2.5 rounded-lg border border-white/[0.08] bg-white/[0.03] px-2.5 py-2">
              <span
                aria-hidden
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-white/[0.06] text-white/75"
              >
                {renderIcon(s.iconName)}
              </span>
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="text-[8.5px] font-medium uppercase tracking-[0.14em] text-white/40">
                  {s.kind}
                </span>
                <span className="truncate text-[11.5px] font-medium tracking-tight text-white">
                  {s.label}
                </span>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function OrderPlacedPreview({ order }: { order: OrderScenario }): React.ReactElement {
  const isSell = order.side === "SELL";
  const isDown = order.trend === "down";
  const deltaTint = isDown
    ? "bg-rose-400/15 text-rose-300"
    : "bg-emerald-400/15 text-emerald-300";
  const sideTint = isSell ? "text-rose-300" : "text-emerald-300";
  const sideDot = isSell ? "bg-rose-400" : "bg-emerald-400";

  return (
    <div className="relative flex h-full items-center justify-center p-3">
      <div
        className="w-full overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.03]"
        style={{
          boxShadow:
            "0 1px 2px rgba(0,0,0,0.4), 0 18px 36px -18px rgba(76,175,80,0.22)",
        }}
      >
        <div className="flex items-start justify-between gap-3 px-3.5 pt-2.5 pb-1.5">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[8.5px] font-medium uppercase tracking-wider text-white/55">
              <span>{order.exchange}</span>
              <span className="text-white/30">·</span>
              <span>{order.sector}</span>
            </div>
            <div className="mt-0.5 truncate text-[12.5px] font-semibold tracking-tight text-white">
              {order.name}
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[9px] font-medium tracking-wider text-white/45">
                {order.ticker}
              </span>
              {order.tag && (
                <span className="inline-flex items-center rounded-sm bg-emerald-400/15 px-1 py-[1px] text-[8.5px] font-medium tracking-wider text-emerald-300">
                  {order.tag}
                </span>
              )}
            </div>
          </div>
          <div className="flex flex-col items-end">
            <div className="text-[12px] font-semibold tabular-nums text-white">
              {order.price}
            </div>
            <div
              className={`mt-0.5 inline-flex items-center gap-0.5 rounded-full px-1.5 py-[1px] text-[8.5px] font-medium tabular-nums ${deltaTint}`}
            >
              {order.deltaPct}
            </div>
          </div>
        </div>

        <div className="px-3.5 pb-1.5">
          <MiniSpark trend={order.trend} />
        </div>

        <div className="flex items-center justify-between px-3.5 pt-1 pb-2">
          <span
            className={`inline-flex items-center gap-1 text-[8.5px] font-medium uppercase tracking-wider tabular-nums ${sideTint}`}
          >
            <span className={`h-1 w-1 rounded-full ${sideDot}`} aria-hidden />
            {order.side} · {order.sideVerb}
          </span>
          <span className="text-[11px] font-semibold tabular-nums text-white">
            {order.totalLabel}
          </span>
        </div>

        <dl className="grid grid-cols-3 border-t border-white/[0.08]">
          <StatCell label="Qty" value={order.qty} />
          <StatCell label="Price" value={order.totalLabel} hasDivider />
          <StatCell label="Total" value={order.total} hasDivider />
        </dl>

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

function DrawCheck(): React.ReactElement {
  return (
    <svg
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

function MiniSpark({ trend }: { trend: "up" | "down" }): React.ReactElement {
  const upPoints: [number, number][] = [
    [0, 18], [4, 17],  [8, 18], [12, 16], [16, 17], [20, 15],
    [24, 16], [28, 14], [32, 15], [36, 13], [40, 14], [44, 12],
    [48, 13], [52, 11], [56, 12], [60, 10], [64, 11], [68, 9],
    [72, 8],  [76, 9],  [80, 7],  [84, 6],  [88, 5],  [92, 4],
  ];
  const downPoints: [number, number][] = [
    [0, 4],  [4, 5],   [8, 4],  [12, 6],  [16, 5],  [20, 7],
    [24, 6], [28, 8],  [32, 7], [36, 9],  [40, 8],  [44, 10],
    [48, 9], [52, 11], [56, 10], [60, 12], [64, 11], [68, 13],
    [72, 14], [76, 13], [80, 15], [84, 16], [88, 17], [92, 18],
  ];
  const points = trend === "up" ? upPoints : downPoints;
  const stroke = trend === "up" ? "rgb(74,222,128)" : "rgb(244,114,128)";
  const fillTop = trend === "up" ? "rgba(74,222,128,0.35)" : "rgba(244,114,128,0.35)";
  const fillBottom = trend === "up" ? "rgba(74,222,128,0)" : "rgba(244,114,128,0)";
  const gradId = `spark-fill-${trend}`;
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
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={fillTop} />
          <stop offset="100%" stopColor={fillBottom} />
        </linearGradient>
      </defs>
      <path d={dFill} fill={`url(#${gradId})`} />
      <path d={d} fill="none" stroke={stroke} strokeWidth="1.25" />
    </svg>
  );
}

// ─── Build your own Securities ──────────────────────────────────────────

type Security = {
  name: string;
  type: string;
  prompt: string;
  description: string;
  stats: { label: string; value: string }[];
};

const SECURITIES: Security[] = [
  {
    name: "Capital Shield",
    type: "Two-leg structured",
    prompt:
      "I want to create a security wherein I don't lose my money but still earn some upside if markets go up.",
    description:
      "88% in short-duration debt, 12% in long-dated NIFTY 50 call options. Near-full downside protection with equity upside participation.",
    stats: [
      { label: "Floor", value: "97%" },
      { label: "Upside Cap", value: "+18%/yr" },
      { label: "Max DD", value: "−3%" },
    ],
  },
  {
    name: "Covered Call NIFTY",
    type: "Income overlay",
    prompt:
      "I already hold NIFTY. Create a security through which I can earn some extra monthly income from it.",
    description:
      "Long NIFTY 50 with monthly OTM call selling. Generates premium against a tracked index core.",
    stats: [
      { label: "Premium", value: "0.8%/mo" },
      { label: "Cap", value: "+4%" },
    ],
  },
  {
    name: "Gold Barbell",
    type: "Two-leg structured",
    prompt:
      "I want a security in which gold is my safety net with a little aggressive equity on the side.",
    description:
      "75% in GOLD ETFs, 25% in 2x leveraged NIFTY ETF. Hard-asset stability with a leveraged equity kicker on the side.",
    stats: [
      { label: "Gold Alloc", value: "75%" },
      { label: "Max DD", value: "−18%" },
      { label: "Rebal", value: "Quarterly" },
    ],
  },
];

export function BuildSecuritiesSection(): React.ReactElement {
  // Mobile + tablet horizontal carousel — mirrors EventTriggersSection so the
  // sm-to-lg range never shows the awkward 2-column split with only 3 cards.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const MOBILE_COPIES = 5;
  const bufferedSecurities = Array.from(
    { length: MOBILE_COPIES },
    () => SECURITIES,
  ).flat();
  const MIDDLE_START = SECURITIES.length * Math.floor(MOBILE_COPIES / 2);
  const [centeredIdx, setCenteredIdx] = useState<number>(MIDDLE_START);
  const centeredIdxRef = useRef<number>(MIDDLE_START);
  centeredIdxRef.current = centeredIdx;

  const scrollToCardIdx = (idx: number, behavior: ScrollBehavior): void => {
    const el = scrollRef.current;
    if (!el) return;
    const cards = el.querySelectorAll<HTMLElement>("[data-security-card]");
    const target = cards[idx];
    if (!target) return;
    el.scrollTo({
      left: target.offsetLeft - (el.clientWidth - target.offsetWidth) / 2,
      behavior,
    });
  };

  useEffect(() => {
    scrollToCardIdx(MIDDLE_START, "auto");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let raf = 0;
    const compute = () => {
      const cards = el.querySelectorAll<HTMLElement>("[data-security-card]");
      if (!cards.length) return;
      const center = el.scrollLeft + el.clientWidth / 2;
      let nearest = 0;
      let minDist = Infinity;
      cards.forEach((c, i) => {
        const cc = c.offsetLeft + c.offsetWidth / 2;
        const d = Math.abs(cc - center);
        if (d < minDist) {
          minDist = d;
          nearest = i;
        }
      });
      if (nearest !== centeredIdxRef.current) setCenteredIdx(nearest);
    };
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(compute);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, []);

  const scrollStep = (d: 1 | -1): void => {
    const el = scrollRef.current;
    if (!el) return;
    const cards = el.querySelectorAll<HTMLElement>("[data-security-card]");
    if (!cards.length) return;
    let cursor = centeredIdx;
    const wouldOverflow = cursor + d < 0 || cursor + d > cards.length - 1;
    if (wouldOverflow) {
      const equivalent =
        MIDDLE_START +
        ((cursor % SECURITIES.length) + SECURITIES.length) % SECURITIES.length;
      scrollToCardIdx(equivalent, "auto");
      cursor = equivalent;
    }
    scrollToCardIdx(cursor + d, "smooth");
  };

  return (
    <section data-nav-theme="dark" className="relative isolate overflow-hidden bg-[#0a0a0b] py-14 text-white sm:py-28 lg:px-6 lg:py-32">
      <DriftOrbs />

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
        <Reveal className="mx-auto max-w-2xl px-5 text-center sm:px-6">
          <div className="mb-4 inline-flex items-center gap-2 text-[10.5px] uppercase tracking-[0.14em] text-white/70 sm:text-[11px]">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-white" />
            Create (Upcoming)
          </div>
          <h2 className="font-serif text-[36px] leading-[1.04] tracking-[-0.03em] text-white sm:text-[48px] sm:tracking-[-0.04em] sm:leading-[1.05] lg:text-[56px]">
            Build your own securities
          </h2>
          <p className="mt-4 text-[14.5px] leading-[1.6] text-white/65 sm:mt-5 sm:text-[15px] sm:leading-7">
            Describe a payoff in plain English. Pivot composes the legs,
            sizes them to your risk, and tracks them as a single position.
          </p>
        </Reveal>

        {/* Mobile (<sm): plain vertical stack */}
        <Reveal delay={120} className="mt-10 grid grid-cols-1 gap-5 px-5 sm:hidden">
          {SECURITIES.map((s) => (
            <SecurityCard key={s.name} sec={s} />
          ))}
        </Reveal>

        {/* Tablet (sm – lg): horizontal carousel */}
        <Reveal delay={120} className="relative mt-10 hidden sm:block lg:hidden">
          <div
            ref={scrollRef}
            className="flex snap-x snap-mandatory gap-3 overflow-x-auto scroll-smooth px-[14vw] pb-2 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {bufferedSecurities.map((s, i) => (
              <div
                key={i}
                data-security-card
                className="flex h-auto min-h-[440px] w-[72vw] max-w-[420px] flex-none snap-center"
              >
                <SecurityCard sec={s} />
              </div>
            ))}
          </div>

          <div className="mt-5 flex items-center justify-center gap-3 px-5">
            <button
              type="button"
              aria-label="Previous security"
              onClick={() => scrollStep(-1)}
              className="flex h-10 w-10 items-center justify-center rounded-full border border-white/15 bg-white/[0.06] text-white/85 backdrop-blur-sm transition active:bg-white active:text-[#0d0d0e]"
            >
              <ChevronLeft size={18} strokeWidth={2} />
            </button>
            <button
              type="button"
              aria-label="Next security"
              onClick={() => scrollStep(1)}
              className="flex h-10 w-10 items-center justify-center rounded-full border border-white/15 bg-white/[0.06] text-white/85 backdrop-blur-sm transition active:bg-white active:text-[#0d0d0e]"
            >
              <ChevronRight size={18} strokeWidth={2} />
            </button>
          </div>
        </Reveal>

        {/* Desktop grid (lg+) — 3 cards side by side */}
        <Reveal delay={120} className="mt-14 hidden grid-cols-3 gap-5 lg:grid">
          {SECURITIES.map((s) => (
            <SecurityCard key={s.name} sec={s} />
          ))}
        </Reveal>
      </div>
    </section>
  );
}

function DriftOrbs(): React.ReactElement {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
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
    <div className="group flex h-full w-full flex-col rounded-2xl bg-white/[0.035] p-4 backdrop-blur-sm transition-all hover:bg-white/[0.06] sm:p-6">
      <div className="flex justify-end">
        <div
          className="max-w-[88%] text-[11.5px] leading-snug text-white/85 sm:text-[12.5px]"
          style={{
            padding: "8px 11px",
            borderRadius: "14px 14px 2px 14px",
            background: "rgba(255,255,255,0.08)",
            fontFamily: "var(--font-ui)",
          }}
        >
          {sec.prompt}
        </div>
      </div>
      <div className="mt-4 flex items-center justify-between sm:mt-6">
        <span className="text-[10px] font-medium uppercase tracking-wider text-white/55">
          {sec.type}
        </span>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/pivot-icon.png"
          alt="Pivot"
          width={44}
          height={44}
          className="h-8 w-8 shrink-0 sm:h-11 sm:w-11"
          style={{ display: "block", objectFit: "contain" }}
        />
      </div>
      <h3 className="mt-3 text-[17px] font-semibold tracking-tight text-white sm:mt-4 sm:text-[20px]">
        {sec.name}
      </h3>
      <p className="mt-1.5 flex-1 text-[12px] leading-[1.55] text-white/65 sm:mt-2 sm:text-[13px] sm:leading-6">
        {sec.description}
      </p>
      <div className="mt-4 flex items-center justify-between gap-3 border-t border-white/[0.08] pt-3.5 sm:mt-6 sm:gap-6 sm:pt-5">
        {sec.stats.map((st) => (
          <div key={st.label}>
            <div className="text-[9.5px] uppercase tracking-wider text-white/45 sm:text-[10px]">
              {st.label}
            </div>
            <div className="mt-0.5 text-[12.5px] font-semibold text-white sm:text-[14px]">
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
  featured?: boolean;
};

const AGENTS: Agent[] = [
  {
    title: "Geopolitical crude hedge",
    body: "If China attacks Taiwan, buy crude oil futures.",
  },
  {
    title: "Budget defence play",
    body: "If the Government of India raises defence expenditure in this year's budget, invest ₹20,000 in Hindustan Aeronautics Ltd.",
  },
  {
    title: "Inflation gold tilt",
    body: "If CPI for this month prints above 6%, increase the weightage of gold in my portfolio by 10%.",
  },
  {
    title: "Crude spike IOC buy",
    body: "If crude oil price crosses $100 per barrel, buy 10 shares of Indian Oil.",
  },
  {
    title: "Repo cut bank rotation",
    body: "If RBI cuts the repo rate, buy 100 shares of UBI.",
  },
  {
    title: "Earnings momentum buy",
    body: "Watch Bajaj Finance earnings — if earnings increase by more than 5% from the previous year, buy 15 shares.",
  },
  {
    title: "Wartime defence ETF",
    body: "If India goes into a war with any nation, invest ₹50,000 into a defence ETF.",
  },
  {
    title: "ETERNAL profitability buy",
    body: "Buy ETERNAL if they announce an EBITDA-positive quarter.",
  },
];

export function EventTriggersSection(): React.ReactElement {
  const [offset, setOffset] = useState(0);
  const [dir, setDir] = useState<1 | -1>(1);
  const [tick, setTick] = useState(0);
  const VISIBLE = 5;
  const FEATURED_SLOT = 2;

  const enteringSlot = dir === 1 ? VISIBLE - 1 : 0;

  const visibleAgents = Array.from({ length: VISIBLE }, (_, i) => {
    // Modulo against a non-empty array — index is always in bounds.
    const agent = AGENTS[(offset + i) % AGENTS.length]!;
    return { ...agent, featured: i === FEATURED_SLOT };
  });

  const step = (d: 1 | -1): void => {
    setDir(d);
    setOffset((o) => (o + d + AGENTS.length) % AGENTS.length);
    setTick((t) => t + 1);
  };

  // Mobile carousel — 5 copies of the list so the user always has left/right
  // peeks and plenty of headroom in both directions without any silent
  // teleport gymnastics. Centered card on scroll becomes the featured one.
  const mobileScrollRef = useRef<HTMLDivElement | null>(null);
  const MOBILE_COPIES = 5;
  const mobileTripled = Array.from({ length: MOBILE_COPIES }, () => AGENTS).flat();
  const MIDDLE_START = AGENTS.length * Math.floor(MOBILE_COPIES / 2);
  const [centeredIdx, setCenteredIdx] = useState<number>(MIDDLE_START);
  const centeredIdxRef = useRef<number>(MIDDLE_START);
  centeredIdxRef.current = centeredIdx;

  const scrollToCardIdx = (idx: number, behavior: ScrollBehavior): void => {
    const el = mobileScrollRef.current;
    if (!el) return;
    const cards = el.querySelectorAll<HTMLElement>("[data-mobile-agent-card]");
    const target = cards[idx];
    if (!target) return;
    el.scrollTo({
      left: target.offsetLeft - (el.clientWidth - target.offsetWidth) / 2,
      behavior,
    });
  };

  // Initial position: middle copy, first card. Run once after mount.
  useEffect(() => {
    scrollToCardIdx(MIDDLE_START, "auto");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Track which card is centered as the user scrolls so the featured style
  // can follow it. No silent reset — the buffer copies handle the headroom.
  useEffect(() => {
    const el = mobileScrollRef.current;
    if (!el) return;
    let raf = 0;
    const compute = () => {
      const cards = el.querySelectorAll<HTMLElement>("[data-mobile-agent-card]");
      if (!cards.length) return;
      const center = el.scrollLeft + el.clientWidth / 2;
      let nearest = 0;
      let minDist = Infinity;
      cards.forEach((c, i) => {
        const cc = c.offsetLeft + c.offsetWidth / 2;
        const d = Math.abs(cc - center);
        if (d < minDist) {
          minDist = d;
          nearest = i;
        }
      });
      if (nearest !== centeredIdxRef.current) setCenteredIdx(nearest);
    };
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(compute);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, []);

  const scrollMobile = (d: 1 | -1): void => {
    const el = mobileScrollRef.current;
    if (!el) return;
    const cards = el.querySelectorAll<HTMLElement>("[data-mobile-agent-card]");
    if (!cards.length) return;
    let cursor = centeredIdx;
    // If we'd step off the buffered list, silently jump to the equivalent
    // card in the middle copy first, then animate the step. This makes the
    // tap-driven navigation feel infinite without the user seeing a reset.
    const wouldOverflow = cursor + d < 0 || cursor + d > cards.length - 1;
    if (wouldOverflow) {
      const equivalent = MIDDLE_START + ((cursor % AGENTS.length) + AGENTS.length) % AGENTS.length;
      scrollToCardIdx(equivalent, "auto");
      cursor = equivalent;
    }
    scrollToCardIdx(cursor + d, "smooth");
  };

  return (
    <section className="bg-white py-14 sm:px-6 sm:py-28 lg:py-32">
      <div className="mx-auto max-w-6xl">
        <Reveal className="mx-auto max-w-2xl px-5 text-center sm:px-0">
          <div className="mb-4 inline-flex items-center gap-2 text-[10.5px] uppercase tracking-[0.14em] text-[#4d555c] sm:text-[11px]">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#4d555c]" />
            automate
          </div>
          <h2 className="font-serif text-[36px] leading-[1.04] tracking-[-0.03em] text-[#0d0d0e] sm:text-[48px] sm:tracking-[-0.04em] sm:leading-[1.05] lg:text-[56px]">
            Set event-based triggers
          </h2>
          <p className="mt-4 text-[14.5px] leading-[1.6] text-[#4d555c] sm:mt-5 sm:text-[15px] sm:leading-7">
            Wire an agent to a real-world signal — earnings, macro prints,
            price moves — and Pivot executes the plan you described.
          </p>
        </Reveal>

        {/* Mobile + tablet horizontal carousel: 1 card centered with peek on both sides */}
        <div className="relative mt-8 lg:hidden">
          <div
            ref={mobileScrollRef}
            className="flex snap-x snap-mandatory gap-3 overflow-x-auto scroll-smooth px-[14vw] pb-2 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {mobileTripled.map((a, i) => (
              <div
                key={i}
                data-mobile-agent-card
                className="h-[260px] w-[72vw] flex-none snap-center"
              >
                <MobileAgentCard agent={a} featured={i === centeredIdx} />
              </div>
            ))}
          </div>

          <div className="mt-5 flex items-center justify-center gap-3 px-5">
            <button
              type="button"
              aria-label="Previous agent"
              onClick={() => scrollMobile(-1)}
              className="flex h-10 w-10 items-center justify-center rounded-full border border-black/10 bg-white text-[#0d0d0e] shadow-[0_8px_24px_-12px_rgba(0,0,0,0.35)] transition active:bg-black active:text-white"
            >
              <ChevronLeft size={18} strokeWidth={2} />
            </button>
            <button
              type="button"
              aria-label="Next agent"
              onClick={() => scrollMobile(1)}
              className="flex h-10 w-10 items-center justify-center rounded-full border border-black/10 bg-white text-[#0d0d0e] shadow-[0_8px_24px_-12px_rgba(0,0,0,0.35)] transition active:bg-black active:text-white"
            >
              <ChevronRight size={18} strokeWidth={2} />
            </button>
          </div>
        </div>

        {/* Desktop grid (lg+) — original 5-card layout */}
        <div className="relative mt-10 hidden px-5 sm:mt-14 sm:px-0 lg:block">
          <div className="grid grid-cols-5 grid-rows-[220px] gap-4">
            {visibleAgents.map((a, i) => {
              const isEntering = i === enteringSlot;
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

          <div
            aria-hidden
            className="pointer-events-none absolute left-0 top-0 z-[5] hidden h-full w-24 -translate-x-1/2 bg-white/15 backdrop-blur-[3px] lg:block"
          />
          <div
            aria-hidden
            className="pointer-events-none absolute right-0 top-0 z-[5] hidden h-full w-24 translate-x-1/2 bg-white/15 backdrop-blur-[3px] lg:block"
          />

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

function MobileAgentCard({
  agent,
  featured,
}: {
  agent: Agent;
  featured: boolean;
}): React.ReactElement {
  if (featured) {
    return (
      <div className="flex h-full flex-col rounded-2xl border border-[#0d0d0e] bg-[#0d0d0e] p-6 text-white shadow-[0_20px_50px_-20px_rgba(0,0,0,0.5)]">
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center rounded-md bg-white/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-white">
            Agent
          </span>
          <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[#0d0d0e]">
            Featured
          </span>
        </div>
        <h3 className="mt-5 text-[19px] font-semibold leading-tight tracking-tight">
          {agent.title}
        </h3>
        <p className="mt-3 flex-1 text-[13.5px] leading-[1.55] text-white/75">
          {agent.body}
        </p>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col rounded-2xl bg-white p-6 shadow-[0_-4px_16px_-8px_rgba(15,23,42,0.08),0_1px_2px_rgba(15,23,42,0.04),0_8px_24px_-12px_rgba(15,23,42,0.10)]">
      <span className="inline-flex w-fit items-center rounded-md bg-black/[0.05] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-[#4d555c]">
        Agent
      </span>
      <h3 className="mt-5 text-[18px] font-semibold leading-tight tracking-tight text-[#0d0d0e]">
        {agent.title}
      </h3>
      <p className="mt-3 flex-1 text-[13.5px] leading-[1.55] text-[#4d555c]">
        {agent.body}
      </p>
    </div>
  );
}

function AgentCard({ agent }: { agent: Agent }): React.ReactElement {
  if (agent.featured) {
    return (
      <div className="relative flex h-full flex-col rounded-2xl border border-[#0d0d0e] bg-[#0d0d0e] p-5 text-white shadow-[0_20px_50px_-20px_rgba(0,0,0,0.5)] lg:scale-[1.05]">
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center rounded-md bg-white/10 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-white">
            Agent
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
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col rounded-2xl bg-white p-5 shadow-[0_-4px_16px_-8px_rgba(15,23,42,0.08),0_1px_2px_rgba(15,23,42,0.04),0_8px_24px_-12px_rgba(15,23,42,0.10)] transition-all hover:shadow-[0_-6px_20px_-8px_rgba(15,23,42,0.10),0_2px_4px_rgba(15,23,42,0.06),0_14px_32px_-14px_rgba(15,23,42,0.16)]">
      <span className="inline-flex w-fit items-center rounded-md bg-black/[0.05] px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-[#4d555c]">
        Agent
      </span>
      <h3 className="mt-3 text-[14px] font-semibold tracking-tight text-[#0d0d0e]">
        {agent.title}
      </h3>
      <p className="mt-2 flex-1 text-[11.5px] leading-5 text-[#4d555c]">
        {agent.body}
      </p>
    </div>
  );
}

// ─── FAQ ────────────────────────────────────────────────────────────────

const FAQS: { q: string; a: string }[] = [
  {
    q: "What is Pivot?",
    a: "Pivot is an agentic investing assistant. You describe what you want, whether it's a buy, sell, alert, rebalance, or research, and Pivot plans the execution, runs it, and reports back.",
  },
  {
    q: "How does Pivot place trades?",
    a: "Pivot connects to your brokerage. You approve the workflow or the trade, and Pivot places the order on your behalf. You are always in full control.",
  },
  {
    q: "Can I backtest my ideas?",
    a: "Yes. Describe a strategy in plain English and Pivot runs it on historical data, surfaces win-rate, drawdown, and CAGR.",
  },
  {
    q: "What kind of strategies can I set up through Pivot?",
    a: "Anything driven by technicals or fundamentals of a stock, event-based triggers like macro prints, geopolitical events, or earnings, and any kind of price action. The build-your-own-securities feature is currently in development and will be available later.",
  },
  {
    q: "Can Pivot handle SIPs and recurring investments?",
    a: "Yes. You can set up SIPs on any stock, ETF, or basket, on any cadence you want, and Pivot will execute them on schedule.",
  },
  {
    q: "Is my money safe?",
    a: "Your funds stay with your regulated brokerage. Pivot never custodies cash or securities; it only orchestrates instructions you approve.",
  },
];

export function FAQSection(): React.ReactElement {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <section className="bg-white px-5 py-14 sm:px-6 sm:py-28 lg:py-32">
      <Reveal className="mx-auto grid max-w-6xl grid-cols-1 gap-8 sm:gap-12 lg:grid-cols-[280px_1fr]">
        <h2 className="font-serif text-[32px] leading-[1.04] tracking-[-0.03em] text-[#0d0d0e] sm:text-[40px] sm:tracking-[-0.04em] sm:leading-[1.05] lg:text-[44px]">
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
                  className="flex w-full items-center justify-between gap-4 text-left sm:gap-6"
                  aria-expanded={isOpen}
                >
                  <span className="text-[15px] font-medium text-[#0d0d0e] sm:text-[16px]">
                    {f.q}
                  </span>
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
                <div
                  className="grid transition-[grid-template-rows,opacity] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]"
                  style={{
                    gridTemplateRows: isOpen ? "1fr" : "0fr",
                    opacity: isOpen ? 1 : 0,
                  }}
                >
                  <div className="overflow-hidden">
                    <p className="mt-3 max-w-2xl text-[13.5px] leading-[1.65] text-[#4d555c] sm:text-[14px] sm:leading-7">
                      {f.a}
                    </p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </Reveal>
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
    <section data-nav-theme="dark" className="bg-[#0d0d0e] px-5 py-14 text-white sm:px-6 sm:py-28 lg:py-32">
      <Reveal className="mx-auto max-w-3xl text-center">
        <h2 className="font-serif text-[38px] leading-[1.04] tracking-[-0.03em] sm:text-[52px] sm:tracking-[-0.04em] sm:leading-[1.05] lg:text-[64px]">
          One message.
        </h2>
        <h2 className="font-serif italic text-[38px] leading-[1.04] tracking-[-0.03em] text-white/85 sm:text-[52px] sm:tracking-[-0.04em] sm:leading-[1.05] lg:text-[64px]">
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
            className="mx-auto mt-8 flex max-w-md flex-col items-stretch gap-2.5 sm:mt-10 sm:flex-row sm:items-center sm:gap-1.5 sm:rounded-full sm:border sm:border-white/10 sm:bg-white/[0.04] sm:p-1.5"
          >
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="your@email.com"
              className="w-full flex-1 rounded-full border border-white/12 bg-white/[0.06] px-5 py-3 text-[15px] text-white placeholder:text-white/45 focus:border-white/30 focus:outline-none sm:border-0 sm:bg-transparent sm:px-4 sm:py-2.5 sm:text-[14px]"
            />
            <button
              type="submit"
              className="w-full whitespace-nowrap rounded-full bg-white px-5 py-3 text-[15px] font-medium text-[#0d0d0e] transition-opacity hover:opacity-90 sm:w-auto sm:py-2.5 sm:text-[14px]"
            >
              Join the Waitlist
            </button>
          </form>
        )}
      </Reveal>
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

// ─── Big wordmark footer ──────────────────────────────────────────

export function WordmarkFooter(): React.ReactElement {
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const wordRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    const word = wordRef.current;
    if (!sentinel || !word) return;
    if (
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      word.classList.add("is-visible");
      return;
    }
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            word.classList.add("is-visible");
            obs.disconnect();
            break;
          }
        }
      },
      { threshold: 0.1, rootMargin: "0px 0px -10% 0px" },
    );
    obs.observe(sentinel);
    return () => obs.disconnect();
  }, []);

  return (
    <footer ref={sentinelRef} data-nav-theme="dark" className="relative overflow-hidden bg-[#0a0a0b] text-white">
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

      <div className="relative mx-auto flex max-w-7xl flex-col items-center px-5 pb-8 pt-10 sm:px-6 sm:pb-10 sm:pt-16">
        <div className="wordmark-mask w-full select-none pb-2 text-center leading-[0.95]">
          <span
            ref={wordRef}
            className="wordmark-reveal font-serif italic tracking-[-0.05em]"
            style={{
              fontSize: "clamp(110px, 19vw, 300px)",
              backgroundImage:
                "linear-gradient(to bottom, rgba(255,255,255,0.95) 0%, rgba(255,255,255,0.55) 55%, rgba(255,255,255,0.08) 100%)",
              WebkitBackgroundClip: "text",
              backgroundClip: "text",
              color: "transparent",
              paddingTop: "0.12em",
              paddingBottom: "0.08em",
            }}
          >
            Pivot.
          </span>
        </div>

        <div className="absolute bottom-4 right-2 flex items-center gap-3 sm:bottom-6 sm:right-3 sm:gap-4">
          <a
            href="https://www.instagram.com/investwithpivot/"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Pivot on Instagram"
            className="flex h-9 w-9 items-center justify-center rounded-full border border-white/15 bg-white/[0.04] text-white/75 backdrop-blur-sm transition hover:border-white/30 hover:text-white"
          >
            <Instagram size={16} strokeWidth={1.75} aria-hidden />
          </a>
          <a
            href="https://x.com/investwithpivot"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Pivot on X"
            className="flex h-9 w-9 items-center justify-center rounded-full border border-white/15 bg-white/[0.04] text-white/75 backdrop-blur-sm transition hover:border-white/30 hover:text-white"
          >
            <Twitter size={16} strokeWidth={1.75} aria-hidden />
          </a>
        </div>
      </div>
    </footer>
  );
}
