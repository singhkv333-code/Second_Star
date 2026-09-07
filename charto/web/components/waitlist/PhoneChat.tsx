"use client";

/**
 * PhoneChat — iPhone-style mock that plays a looping scripted chat
 * between the user and Pivot.
 *
 * Bubble + loader + send-button styling mirrors the real product
 * (components/chat/ChatDemo.tsx):
 *   - User turns: light-grey rounded bubble (16/16/2/16 corners), right-
 *     aligned, primary ink text — NOT inverted black.
 *   - Assistant turns: flowing prose on the page background, no bubble.
 *   - Loader between turns: three "witty-bar" bars animating like a
 *     mini volume strip (no text caption — just the bars).
 *   - Composer send: lucide ArrowUp inside the black circle.
 */

import { useEffect, useRef, useState } from "react";
import { ArrowUp } from "lucide-react";

type Turn =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string };

const SCRIPT: Turn[] = [
  { role: "user", text: "Buy ₹50k of QQQ at market open tomorrow." },
  { role: "assistant", text: "Order queued for 9:30 AM ET. I'll confirm fills the moment they land." },
  { role: "user", text: "Alert me if NIFTY drops 2% in a single day." },
  { role: "assistant", text: "Alert armed. I'll ping you the second it triggers." },
  { role: "user", text: "Backtest a 50/200 SMA crossover on RELIANCE." },
  { role: "assistant", text: "Running 5-year backtest… 42 trades, 61% win-rate, 14.2% CAGR." },
];

export function PhoneChat(): React.ReactElement {
  const [visible, setVisible] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Drive the script forward on a timer. Each cycle: show loader before
  // each assistant turn, reveal it, then move on. After the script ends,
  // restart from the top.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      while (!cancelled) {
        for (let i = 0; i <= SCRIPT.length; i++) {
          if (cancelled) return;
          if (i === SCRIPT.length) {
            await wait(2400);
            if (cancelled) return;
            setVisible(0);
            setLoading(false);
            await wait(400);
            break;
          }
          const turn = SCRIPT[i];
          if (!turn) break;
          if (turn.role === "assistant") {
            setLoading(true);
            await wait(1100);
            if (cancelled) return;
            setLoading(false);
          }
          setVisible(i + 1);
          await wait(turn.role === "user" ? 700 : 1800);
        }
      }
    };
    tick();
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the transcript pinned to the bottom as new turns appear.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [visible, loading]);

  const turns = SCRIPT.slice(0, visible);

  return (
    <div className="relative mx-auto h-[600px] w-[300px] sm:h-[640px] sm:w-[320px]">
      {/* Phone frame */}
      <div className="absolute inset-0 rounded-[44px] bg-[#0d0d0e] p-[10px] shadow-[0_30px_80px_-20px_rgba(0,0,0,0.4),0_10px_30px_-10px_rgba(0,0,0,0.2)]">
        <div className="relative h-full w-full overflow-hidden rounded-[36px] bg-white">
          {/* Notch */}
          <div className="absolute left-1/2 top-2 z-10 h-6 w-28 -translate-x-1/2 rounded-full bg-[#0d0d0e]" />

          {/* Status bar spacer */}
          <div className="h-10" />

          {/* Header */}
          <div className="flex items-center px-5 pb-1 pt-1">
            <img
              src="/pivot-light.png"
              alt="Pivot"
              width={56}
              height={56}
              style={{ display: "block", objectFit: "contain" }}
            />
          </div>

          {/* Transcript */}
          <div
            ref={scrollRef}
            className="h-[calc(100%-118px)] overflow-y-auto px-4 pb-4 pt-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            <div className="flex flex-col gap-4">
              {turns.map((t, idx) =>
                t.role === "user" ? (
                  <UserTurn key={idx} text={t.text} />
                ) : (
                  <AssistantTurn key={idx} text={t.text} />
                ),
              )}
              {loading && <Loader />}
            </div>
          </div>

          {/* Composer */}
          <div className="absolute inset-x-3 bottom-3 flex items-center gap-2 rounded-full border border-black/[0.08] bg-white px-4 py-2.5 shadow-sm">
            <span className="flex-1 truncate text-[12px] text-[#9aa1a8]">
              Ask Pivot anything…
            </span>
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-[#0d0d0e]">
              <ArrowUp size={14} strokeWidth={2.25} color="white" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * UserTurn — right-aligned light-grey bubble with the tucked-corner
 * radius the real product uses (16/16/2/16). Light ink on grey, not
 * inverted.
 */
function UserTurn({ text }: { text: string }): React.ReactElement {
  return (
    <div className="flex justify-end animate-[bubbleIn_280ms_cubic-bezier(0.22,1,0.36,1)_both]">
      <div
        className="whitespace-pre-wrap"
        style={{
          maxWidth: "82%",
          padding: "8px 12px",
          borderRadius: "14px 14px 2px 14px",
          background: "#ececee",
          fontSize: 12.5,
          color: "#0d0d0e",
          lineHeight: 1.45,
          wordBreak: "break-word",
        }}
      >
        {text}
      </div>
    </div>
  );
}

/**
 * AssistantTurn — flowing prose on the page background, no bubble.
 * Matches the real product's AssistantMessage rendering style.
 */
function AssistantTurn({ text }: { text: string }): React.ReactElement {
  return (
    <div className="animate-[bubbleIn_280ms_cubic-bezier(0.22,1,0.36,1)_both]">
      <p
        className="text-[12.5px] text-[#0d0d0e]"
        style={{ lineHeight: 1.55 }}
      >
        {text}
      </p>
    </div>
  );
}

/**
 * Loader — the WittyTicker pattern from ChatDemo: three bars rising and
 * falling on independent periods. No caption — just the bars, like the
 * user asked.
 */
function Loader(): React.ReactElement {
  return (
    <div className="animate-[bubbleIn_280ms_cubic-bezier(0.22,1,0.36,1)_both]">
      <span
        className="inline-flex items-end"
        style={{ gap: 2, height: 14 }}
        aria-hidden={true}
      >
        <span className="witty-bar" />
        <span className="witty-bar" />
        <span className="witty-bar" />
      </span>
    </div>
  );
}

function wait(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
