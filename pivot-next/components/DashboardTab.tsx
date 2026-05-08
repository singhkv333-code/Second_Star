"use client";

/**
 * DashboardTab — Quartr-design chat surface.
 *
 * Until the user sends their first message we render a Quartr-style
 * landing: 4 index cards on a strip, a large serif greeting, and a row
 * of pill-shaped quick-action chips. Once a message lands `ChatDemo`
 * hides the intro and the transcript fills the pane with the composer
 * pinned at the bottom.
 *
 * Visual port from frontend-quartr/src/components/chat/ChatLanding.jsx
 * with the dark-only Quartr palette converted to a light/dark theme that
 * follows the global theme toggle. Visual/CSS only — no JS interactions
 * change.
 *
 * Data sources:
 *   - GET /api/markets/indices  — index strip
 *   - GET /auth/me              — greeting initial
 */

import { useEffect, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  CalendarDays,
  FileText,
  Newspaper,
  TrendingUp,
  Workflow,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getMarketIndices,
  getMe,
  type IndexQuote,
  type UserProfile,
} from "@/lib/api";
import { isError } from "@/lib/types";
import { ChatDemo } from "@/components/chat/ChatDemo";
import type { Workflow as WorkflowT } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type DashboardTabProps = {
  /** Open the workflow editor panel (forwarded to ChatDemo). */
  onOpenWorkflow: (workflow: WorkflowT) => void;
  /** Called when user clicks the Agents Calendar chip — parent switches tab. */
  onOpenCalendar: () => void;
  /** Forwarded from ChatDemo: true once the user has sent ≥1 message.
   * AppShell uses this to hide the Active Agents rail. */
  onChatActiveChange?: (active: boolean) => void;
};

type IndicesState =
  | { kind: "loading" }
  | { kind: "ok"; items: IndexQuote[] }
  | { kind: "hidden" };

type MeState =
  | { kind: "loading" }
  | { kind: "ok"; profile: UserProfile }
  | { kind: "fallback"; name: string };

// ---------------------------------------------------------------------------
// Action chips — labels mirrored from frontend-quartr/.../ChatLanding.jsx
// (icons aligned 1:1 with that file).
// ---------------------------------------------------------------------------

type ChipDef = {
  label: string;
  Icon: React.ComponentType<{ size?: number; strokeWidth?: number; style?: React.CSSProperties; "aria-hidden"?: boolean }>;
  prompt?: string;
  action?: "calendar";
};

const ACTION_CHIPS: ChipDef[] = [
  { label: "Generate Report",   Icon: FileText,     prompt: "Generate a portfolio performance report for this week." },
  { label: "Run Agent",         Icon: Workflow,     prompt: "Show me my active agents and their last run status." },
  { label: "Portfolio Health",  Icon: Activity,     prompt: "Analyze my portfolio health and suggest any rebalancing." },
  { label: "Market Pulse",      Icon: TrendingUp,   prompt: "Give me a market pulse summary for today." },
  { label: "Top Movers",        Icon: ArrowUpRight, prompt: "What are the top movers in NIFTY 50 today?" },
  { label: "Agents Calendar", Icon: CalendarDays, action: "calendar" },
  { label: "News Digest",       Icon: Newspaper,    prompt: "Summarize today's top financial news." },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtIndexValue(n: number): string {
  return new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  }).format(n);
}

function fmtChange(change: number, pct: number): string {
  const sign = change >= 0 ? "+" : "−";
  const abs = Math.abs(change).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  return `${sign}${abs} (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)`;
}

function getHourGreeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good Morning";
  if (h < 17) return "Good Afternoon";
  return "Good Evening";
}

/** Pick the friendliest display name we can: prefer the user's first
 *  name (full_name split on whitespace), fall back to the email prefix
 *  before the @ sign, then to "there". Mirrors the greeting's
 *  conversational tone. */
function getDisplayName(name: string | null | undefined, email: string | null | undefined): string {
  const trimmed = (name || "").trim();
  if (trimmed) return trimmed.split(/\s+/)[0]!;
  const e = (email || "").trim();
  if (e) {
    const local = e.split("@")[0]!;
    // De-clutter auto-registered demo accounts like "demo_motpgygl_..."
    if (/^demo[_\d]/i.test(local)) return "there";
    return local;
  }
  return "there";
}

// ---------------------------------------------------------------------------
// DashboardTab
// ---------------------------------------------------------------------------

export function DashboardTab({
  onOpenWorkflow,
  onOpenCalendar,
  onChatActiveChange,
}: DashboardTabProps): React.ReactElement {
  const [indices, setIndices] = useState<IndicesState>({ kind: "loading" });
  const [me, setMe] = useState<MeState>({ kind: "loading" });
  const [pendingPrompt, setPendingPrompt] = useState<string | undefined>(undefined);

  useEffect(() => {
    getMarketIndices()
      .then((result) => {
        if (isError(result)) {
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
        setMe({ kind: "ok", profile: result.data });
      })
      .catch(() => setMe({ kind: "fallback", name: "Trader" }));
  }, []);

  const greeting = getHourGreeting();
  const displayName =
    me.kind === "ok"
      ? getDisplayName(me.profile.full_name, me.profile.email)
      : me.kind === "fallback"
        ? me.name
        : null;

  const handleChipClick = (chip: ChipDef): void => {
    if (chip.action === "calendar") {
      onOpenCalendar();
      return;
    }
    if (chip.prompt) setPendingPrompt(chip.prompt);
  };

  // ── Quartr-style empty-state intro: index strip on top, greeting +
  //    quick-action chips centered. The dashboard intro replaces
  //    ChatDemo's default tip card via the `intro` prop.
  //    Index strip docks to the top via absolute positioning so the
  //    greeting/chips can vertically centre in the remaining space.
  const intro = (
    <div
      className="relative flex w-full flex-col items-center"
      style={{ gap: 28 }}
      data-testid="dashboard-intro"
    >
      {/* Index strip — pinned to the top of the chat area */}
      <div className="w-full" style={{ marginBottom: 8 }}>
        <IndexStrip state={indices} />
      </div>

      {/* Greeting — Fraunces, 36–46px, weight 550, tight tracking */}
      {displayName !== null ? (
        <h1 className="q-greeting" data-testid="dashboard-greeting">
          {greeting}, {displayName}!
        </h1>
      ) : (
        <Skeleton style={{ height: 46, width: 360 }} data-testid="greeting-loading" />
      )}

      {/* Quick action pills */}
      <div
        className="flex w-full flex-wrap items-center justify-center"
        style={{ gap: 8, maxWidth: 820 }}
        role="group"
        aria-label="Quick actions"
      >
        {ACTION_CHIPS.map((chip) => (
          <ActionChip key={chip.label} chip={chip} onClick={() => handleChipClick(chip)} />
        ))}
      </div>
    </div>
  );

  return (
    <div
      className="flex h-full min-h-0 w-full flex-col"
      data-testid="dashboard-tab"
      style={{ background: "var(--bg-base)" }}
    >
      <ChatDemo
        onOpenEditor={onOpenWorkflow}
        intro={intro}
        prefill={pendingPrompt}
        prefillAutoSubmit
        onPrefillConsumed={() => setPendingPrompt(undefined)}
        onActiveChange={onChatActiveChange}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// ActionChip — Quartr pill with leading icon, hover lifts surface
// ---------------------------------------------------------------------------

function ActionChip({
  chip,
  onClick,
}: {
  chip: ChipDef;
  onClick: () => void;
}): React.ReactElement {
  const { Icon, label } = chip;
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center"
      style={{
        gap: 8,
        padding: "9px 14px",
        background: "var(--bg-base)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-pill)",
        color: "var(--text-secondary)",
        fontFamily: "var(--font-ui)",
        fontSize: 13,
        fontWeight: "var(--weight-medium)" as unknown as number,
        cursor: "pointer",
        transition:
          "color 0.35s var(--ease-quartr), background-color 0.35s var(--ease-quartr), border-color 0.35s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = "var(--text-primary)";
        e.currentTarget.style.borderColor = "var(--glass-border-hover)";
        e.currentTarget.style.background = "var(--bg-elevated)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = "var(--text-secondary)";
        e.currentTarget.style.borderColor = "var(--glass-border)";
        e.currentTarget.style.background = "var(--bg-base)";
      }}
    >
      <Icon
        size={14}
        strokeWidth={1.75}
        style={{ color: "var(--text-tertiary)" }}
        aria-hidden={true}
      />
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Index strip — Quartr cards
// ---------------------------------------------------------------------------

function IndexStrip({ state }: { state: IndicesState }): React.ReactElement | null {
  if (state.kind === "hidden") return null;

  if (state.kind === "loading") {
    return (
      <div
        className="grid"
        style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 12 }}
        data-testid="index-strip-loading"
        aria-label="Loading market indices"
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton
            key={i}
            style={{ height: 96, borderRadius: "var(--radius-md)" }}
          />
        ))}
      </div>
    );
  }

  return (
    <div
      className="grid"
      style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 12 }}
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
      role="listitem"
      aria-label={`${quote.name}: ${fmtIndexValue(quote.value)}`}
      className="flex flex-col"
      style={{
        gap: 8,
        padding: "14px 16px",
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        transition: "border-color 0.35s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--glass-border-hover)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--glass-border)"; }}
    >
      <div
        className="q-uppercase-label truncate"
        style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
      >
        {quote.name}
      </div>
      <div
        className="q-display tabular-nums"
        style={{ fontSize: 18, lineHeight: 1.1, color: "var(--text-primary)" }}
      >
        {fmtIndexValue(quote.value)}
      </div>
      <div
        className="self-start q-mono"
        style={{
          padding: "3px 8px",
          borderRadius: "var(--radius-xs)",
          fontSize: 11.5,
          fontWeight: "var(--weight-medium)" as unknown as number,
          background: positive
            ? "rgba(16, 185, 129, 0.12)"
            : "rgba(239, 68, 68, 0.12)",
          color: positive ? "var(--color-profit)" : "var(--color-loss)",
          border: positive
            ? "1px solid rgba(16, 185, 129, 0.25)"
            : "1px solid rgba(239, 68, 68, 0.25)",
        }}
      >
        {fmtChange(quote.change, quote.change_pct)}
      </div>
    </div>
  );
}
