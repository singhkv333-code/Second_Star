"use client";

/**
 * DashboardTab — Quartr-style premium home dashboard.
 *
 * Center pane: 4 index cards (NIFTY 50 / SENSEX / BANK NIFTY / NIFTY MIDCAP 100),
 * greeting, action chips, and a big chat input.
 *
 * Data sources:
 *   - GET /api/markets/indices  — index strip
 *   - GET /auth/me              — greeting name
 */

import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  BarChart2,
  BookOpen,
  CalendarDays,
  Heart,
  Newspaper,
  Rocket,
  TrendingUp,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  getMarketIndices,
  getMe,
  type IndexQuote,
  type UserProfile,
} from "@/lib/api";
import { isError } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type DashboardTabProps = {
  /** Called when user submits the chat input — parent routes to chat tab. */
  onSubmitPrompt: (prompt: string) => void;
  /** Called when user clicks Calendar chip — parent switches to calendar tab. */
  onOpenCalendar: () => void;
};

type IndicesState =
  | { kind: "loading" }
  | { kind: "ok"; items: IndexQuote[] }
  | { kind: "hidden" }; // 503 — hide silently

type MeState =
  | { kind: "loading" }
  | { kind: "ok"; profile: UserProfile }
  | { kind: "fallback"; name: string };

// ---------------------------------------------------------------------------
// Action chips
// ---------------------------------------------------------------------------

type ChipDef = {
  label: string;
  Icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  prompt?: string;
  action?: "calendar";
};

const ACTION_CHIPS: ChipDef[] = [
  { label: "Generate Report", Icon: BookOpen, prompt: "Generate a portfolio performance report for this week." },
  { label: "Run Agent", Icon: Rocket, prompt: "Show me my active agents and their last run status." },
  { label: "Portfolio Health", Icon: Heart, prompt: "Analyze my portfolio health and suggest any rebalancing." },
  { label: "Market Pulse", Icon: TrendingUp, prompt: "Give me a market pulse summary for today." },
  { label: "Top Movers", Icon: BarChart2, prompt: "What are the top movers in NIFTY 50 today?" },
  { label: "Earnings Calendar", Icon: CalendarDays, action: "calendar" },
  { label: "News Digest", Icon: Newspaper, prompt: "Summarize today's top financial news." },
];

// ---------------------------------------------------------------------------
// INR formatter
// ---------------------------------------------------------------------------

function fmtValue(n: number): string {
  // For index values, skip currency symbol and use plain number formatting
  return new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  }).format(n);
}

function getHourGreeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good Morning";
  if (h < 17) return "Good Afternoon";
  return "Good Evening";
}

// ---------------------------------------------------------------------------
// DashboardTab
// ---------------------------------------------------------------------------

export function DashboardTab({
  onSubmitPrompt,
  onOpenCalendar,
}: DashboardTabProps): React.ReactElement {
  const [indices, setIndices] = useState<IndicesState>({ kind: "loading" });
  const [me, setMe] = useState<MeState>({ kind: "loading" });
  const [prompt, setPrompt] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    getMarketIndices()
      .then((result) => {
        if (isError(result)) {
          // Any error — hide strip, don't block dashboard
          setIndices({ kind: "hidden" });
          return;
        }
        setIndices({ kind: "ok", items: result.data.items });
      })
      .catch(() => setIndices({ kind: "hidden" }));

    getMe()
      .then((result) => {
        if (isError(result)) {
          setMe({ kind: "fallback", name: "Trader" });
          return;
        }
        const p = result.data;
        const displayName =
          p.full_name && p.full_name.trim().length > 0
            ? p.full_name.split(" ")[0]
            : p.email.split("@")[0];
        setMe({ kind: "ok", profile: { ...p, full_name: displayName ?? null } });
      })
      .catch(() => setMe({ kind: "fallback", name: "Trader" }));
  }, []);

  const greeting = getHourGreeting();
  const displayName =
    me.kind === "ok"
      ? (me.profile.full_name ?? "Trader")
      : me.kind === "fallback"
        ? me.name
        : null;

  const handleChipClick = (chip: ChipDef): void => {
    if (chip.action === "calendar") {
      onOpenCalendar();
      return;
    }
    if (chip.prompt) {
      onSubmitPrompt(chip.prompt);
    }
  };

  const handleSubmit = (): void => {
    const trimmed = prompt.trim();
    if (!trimmed) return;
    onSubmitPrompt(trimmed);
    setPrompt("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="flex flex-col gap-8" data-testid="dashboard-tab">
      {/* Index strip */}
      <IndexStrip state={indices} />

      {/* Greeting */}
      <div>
        {displayName ? (
          <h1
            className="font-serif text-3xl font-semibold tracking-tight text-foreground"
            data-testid="dashboard-greeting"
          >
            {greeting}, {displayName}!
          </h1>
        ) : (
          <Skeleton className="h-9 w-64" data-testid="greeting-loading" />
        )}
        <p className="mt-1 text-sm text-muted-foreground">
          {new Date().toLocaleDateString("en-IN", {
            weekday: "long",
            year: "numeric",
            month: "long",
            day: "numeric",
          })}
        </p>
      </div>

      {/* Action chips */}
      <div
        className="flex flex-wrap gap-2"
        role="group"
        aria-label="Quick actions"
      >
        {ACTION_CHIPS.map((chip) => (
          <button
            key={chip.label}
            type="button"
            onClick={() => handleChipClick(chip)}
            className={cn(
              "flex items-center gap-1.5 rounded-full border bg-card px-3.5 py-1.5",
              "text-xs font-medium text-foreground",
              "hover:bg-muted/60 hover:border-border/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              "transition-colors",
            )}
          >
            <chip.Icon className="h-3.5 w-3.5 text-muted-foreground" aria-hidden={true} />
            {chip.label}
          </button>
        ))}
      </div>

      {/* Big chat input */}
      <div className="rounded-2xl border bg-card shadow-sm">
        <textarea
          ref={textareaRef}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask Pivot anything about your portfolio, markets, or strategies…"
          rows={3}
          aria-label="Chat input"
          data-testid="dashboard-chat-input"
          className={cn(
            "w-full resize-none rounded-t-2xl bg-transparent px-5 pt-4 pb-2",
            "text-sm placeholder:text-muted-foreground/60",
            "focus:outline-none",
          )}
        />
        <div className="flex items-center justify-between border-t px-4 py-2.5">
          <span className="text-[11px] text-muted-foreground">
            Cmd+Enter to send
          </span>
          <Button
            size="sm"
            onClick={handleSubmit}
            disabled={!prompt.trim()}
            className="h-7 gap-1.5 rounded-full px-3 text-xs"
            aria-label="Send message"
            data-testid="dashboard-chat-submit"
          >
            Send
            <ArrowRight className="h-3.5 w-3.5" aria-hidden={true} />
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Index strip
// ---------------------------------------------------------------------------

function IndexStrip({ state }: { state: IndicesState }): React.ReactElement | null {
  if (state.kind === "hidden") return null;

  if (state.kind === "loading") {
    return (
      <div
        className="grid grid-cols-2 gap-3 sm:grid-cols-4"
        data-testid="index-strip-loading"
        aria-label="Loading market indices"
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-20 rounded-xl" />
        ))}
      </div>
    );
  }

  return (
    <div
      className="grid grid-cols-2 gap-3 sm:grid-cols-4"
      data-testid="index-strip"
      role="list"
      aria-label="Market indices"
    >
      {state.items.map((q) => (
        <IndexCard key={q.symbol} quote={q} />
      ))}
    </div>
  );
}

function IndexCard({ quote }: { quote: IndexQuote }): React.ReactElement {
  const positive = quote.change >= 0;
  return (
    <div
      className="flex flex-col gap-1 rounded-xl border bg-card px-4 py-3"
      role="listitem"
      aria-label={`${quote.name}: ${fmtValue(quote.value)}`}
    >
      <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {quote.name}
      </span>
      <span className="font-serif text-xl font-semibold tabular-nums text-foreground">
        {fmtValue(quote.value)}
      </span>
      <div className="flex items-center gap-1.5">
        <span
          className={cn(
            "inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums",
            positive
              ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400"
              : "bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-400",
          )}
        >
          {positive ? "+" : ""}
          {quote.change_pct.toFixed(2)}%
        </span>
        <span
          className={cn(
            "text-[10px] tabular-nums",
            positive
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-rose-600 dark:text-rose-400",
          )}
        >
          {positive ? "+" : ""}
          {fmtValue(quote.change)}
        </span>
      </div>
    </div>
  );
}
