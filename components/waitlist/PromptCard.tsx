"use client";

/**
 * PromptCard — mobile alternative to PhoneChat. Curved-edge card that
 * cycles through the same scripted prompts + Pivot replies, without the
 * phone frame chrome.
 */

import { useEffect, useRef, useState } from "react";
import { ArrowUp } from "lucide-react";

type Turn =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string };

const SCRIPT: Turn[] = [
  { role: "user", text: "Buy 50 shares of IREDA at market open tomorrow." },
  { role: "assistant", text: "Order queued for 9:30 AM IST. I'll confirm fills the moment they land." },
  { role: "user", text: "Alert me if NIFTY drops 2% in a single day." },
  { role: "assistant", text: "Alert armed. I'll ping you the second it triggers." },
  { role: "user", text: "Backtest a 50/200 SMA crossover on RELIANCE." },
  { role: "assistant", text: "Running 5-year backtest… 42 trades, 61% win-rate, 14.2% CAGR." },
];

export function PromptCard(): React.ReactElement {
  const [visible, setVisible] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

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

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [visible, loading]);

  const turns = SCRIPT.slice(0, visible);

  return (
    <div className="relative mx-auto w-full max-w-[440px]">
      <div className="relative overflow-hidden rounded-[28px] border border-black/[0.06] bg-white p-4 shadow-[0_20px_60px_-20px_rgba(0,0,0,0.25),0_6px_20px_-10px_rgba(0,0,0,0.12)] sm:rounded-[32px] sm:p-5">
        <div className="flex items-center gap-2 pb-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/pivot-light.png"
            alt="Pivot"
            width={44}
            height={44}
            style={{ display: "block", objectFit: "contain" }}
          />
          <span className="text-[13px] font-medium tracking-tight text-[#0d0d0e]">
            Pivot
          </span>
        </div>

        <div
          ref={scrollRef}
          className="h-[320px] overflow-y-auto pb-3 pr-1 pt-1 [scrollbar-width:none] sm:h-[360px] [&::-webkit-scrollbar]:hidden"
        >
          <div className="flex flex-col gap-3.5">
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

        <div className="mt-2 flex items-center gap-2 rounded-full border border-black/[0.08] bg-white px-4 py-2.5 shadow-sm">
          <span className="flex-1 truncate text-[13px] text-[#9aa1a8]">
            Ask Pivot anything…
          </span>
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-[#0d0d0e]">
            <ArrowUp size={14} strokeWidth={2.25} color="white" />
          </div>
        </div>
      </div>
    </div>
  );
}

function UserTurn({ text }: { text: string }): React.ReactElement {
  return (
    <div className="flex justify-end animate-[bubbleIn_280ms_cubic-bezier(0.22,1,0.36,1)_both]">
      <div
        className="whitespace-pre-wrap"
        style={{
          maxWidth: "85%",
          padding: "9px 13px",
          borderRadius: "16px 16px 4px 16px",
          background: "#ececee",
          fontSize: 13,
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

function AssistantTurn({ text }: { text: string }): React.ReactElement {
  return (
    <div className="animate-[bubbleIn_280ms_cubic-bezier(0.22,1,0.36,1)_both]">
      <p
        className="text-[13px] text-[#0d0d0e]"
        style={{ lineHeight: 1.55 }}
      >
        {text}
      </p>
    </div>
  );
}

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
