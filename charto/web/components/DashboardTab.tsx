"use client";

/**
 * DashboardTab — Quartr-design chat surface.
 *
 * Until the user sends their first message we render a Quartr-style
 * landing: a large serif greeting and a row of pill-shaped quick-action
 * chips. Once a message lands `ChatDemo` hides the intro and the
 * transcript fills the pane with the composer pinned at the bottom.
 *
 * Visual port from frontend-quartr/src/components/chat/ChatLanding.jsx
 * with the dark-only Quartr palette converted to a light/dark theme that
 * follows the global theme toggle. Visual/CSS only — no JS interactions
 * change.
 *
 * Data sources:
 *   - GET /auth/me              — greeting initial
 */

import { useEffect, useState } from "react";
import {
  Activity,
  Filter,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getMe,
  type UserProfile,
} from "@/lib/api";
import { isError } from "@/lib/types";
import { ChatDemo, type ChatDemoSeed, type ResumeConversation } from "@/components/chat/ChatDemo";
import type { WorkflowDraft } from "@/components/chat/WorkflowDraftCard";
import type { Workflow as WorkflowT } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type DashboardTabProps = {
  /** Open the workflow editor panel (forwarded to ChatDemo). */
  onOpenWorkflow: (workflow: WorkflowT) => void;
  /** Forwarded from ChatDemo: true once the user has sent ≥1 message.
   * AppShell uses this to hide the Active Agents rail. */
  onChatActiveChange?: (active: boolean) => void;
  /**
   * Called when a chat turn yields a new or amended workflow_draft_card.
   * AppShell uses this to push the draft into the bound editor.
   */
  onDraftFromChat?: (draft: WorkflowT) => void;
  /**
   * A prompt seeded from OUTSIDE the chat surface (e.g. the Home tab's
   * ready-prompt / strategy tiles). When it changes to a non-empty string
   * the composer is filled and auto-submitted, exactly like a chip click.
   */
  seededPrompt?: string;
  /** Called after `seededPrompt` has been consumed so the parent can clear it. */
  onSeededPromptConsumed?: () => void;
  /** Resume a persisted sidebar conversation (forwarded to ChatDemo). */
  resume?: ResumeConversation;
};

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
  action?: "demo";
};

const ACTION_CHIPS: ChipDef[] = [
  // Four quick-start prompts that seed the chat composer. Each one maps to a
  // real capability the agent can answer (portfolio, market, movers, news).
  { label: "Portfolio Health", Icon: Activity,   prompt: "Analyze my portfolio health — highlight concentration risk, biggest movers, and any rebalancing you'd suggest." },
  { label: "Market Today",     Icon: TrendingUp, prompt: "Give me a market pulse for today: how are NIFTY 50 and SENSEX doing, and what are the top gainers and losers?" },
  { label: "Watchlist Ideas",  Icon: Sparkles,   prompt: "Suggest 3 large-cap Indian stocks worth watching right now, with a one-line reason for each." },
  { label: "Screen Stocks",    Icon: Filter,     prompt: "Screen for Indian stocks with a market cap above ₹20,000 Cr, P/E under 25, and positive revenue growth. Show me the top 5 matches with key metrics." },
];

// ---------------------------------------------------------------------------
// Offline demo seed — the "Play demo" chip wires this into ChatDemo so the
// chat shows a user prompt → simulated streaming → workflow draft card,
// and the right-side editor panel opens with the same workflow loaded.
// Tweak freely; the shape is the same one the backend would emit on a
// successful `propose_workflow` tool call.
// ---------------------------------------------------------------------------

const DEMO_USER_PROMPT =
  "Every weekday at 3:00 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and email me the confirmation.";

const DEMO_DRAFT: WorkflowDraft = {
  name: "RELIANCE 3:00 PM buy",
  description:
    "Every weekday at 3:00 PM IST, if buying power is over ₹50,000, buy 10 shares of RELIANCE and notify by email.",
  steps: [
    {
      step_type: "trigger.schedule",
      label: "Every weekday at 3:00 PM IST",
      config: { cron: "0 15 * * 1-5", timezone: "Asia/Kolkata" },
    },
    {
      step_type: "fetch.portfolio",
      label: "Get my portfolio",
      config: {},
    },
    {
      step_type: "condition.numeric",
      label: "Buying power above ₹50,000",
      config: {
        left: "{{ context.1.buying_power }}",
        operator: ">",
        right: 50000,
      },
    },
    {
      step_type: "action.place_order",
      label: "Buy 10 RELIANCE",
      config: {
        symbol: "RELIANCE",
        side: "buy",
        quantity: 10,
        order_type: "market",
        requires_approval: true,
      },
    },
    {
      step_type: "notify.message",
      label: "Email me a confirmation",
      config: {
        channel: "email",
        template: "Bought {{ context.3.broker_order_id }}: 10 RELIANCE",
        vars: {},
      },
    },
  ],
  rationale:
    "Daily schedule fires at 3:00 PM IST; the portfolio fetch + numeric guard prevents over-leveraged orders. Approval is required before the order is placed.",
  warnings: [
    "Market orders fill at the next available price — buying power check is a guard, not a guarantee.",
  ],
  _render_hint: "workflow_draft_card",
};

const DEMO_INTRO =
  "Got it — here's a draft workflow for that strategy. Review the steps on the right and activate when ready.";

const DEMO_SEED: ChatDemoSeed = {
  userText: DEMO_USER_PROMPT,
  intro: DEMO_INTRO,
  draft: DEMO_DRAFT,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
  onChatActiveChange,
  onDraftFromChat,
  seededPrompt,
  onSeededPromptConsumed,
  resume,
}: DashboardTabProps): React.ReactElement {
  const [me, setMe] = useState<MeState>({ kind: "loading" });
  const [pendingPrompt, setPendingPrompt] = useState<string | undefined>(undefined);
  const [demoSeed, setDemoSeed] = useState<ChatDemoSeed | undefined>(undefined);

  // Adopt a prompt seeded from another tab (Home) — mirror it into the local
  // pending-prompt state so ChatDemo auto-submits it, then tell the parent it
  // was consumed so a repeat click of the SAME prompt re-fires.
  useEffect(() => {
    if (!seededPrompt) return;
    setPendingPrompt(seededPrompt);
    onSeededPromptConsumed?.();
  }, [seededPrompt, onSeededPromptConsumed]);

  useEffect(() => {
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
    if (chip.action === "demo") {
      // Re-seed by passing a fresh object so React's Object.is check fires
      // even if the user clicks "Play demo" repeatedly after a "New chat".
      setDemoSeed({ ...DEMO_SEED });
      return;
    }
    if (chip.prompt) setPendingPrompt(chip.prompt);
  };

  // ── Quartr-style empty-state intro: greeting + quick-action chips
  //    centered. The dashboard intro replaces ChatDemo's default tip
  //    card via the `intro` prop.
  const intro = (
    <div
      className="relative flex w-full flex-col items-center"
      style={{ gap: 28 }}
      data-testid="dashboard-intro"
    >
      {/* Greeting — serif (--font-experiment), 36–46px, weight 550, tight tracking */}
      {displayName !== null ? (
        <h1 className="q-greeting" data-testid="dashboard-greeting">
          {greeting}, {displayName}!
        </h1>
      ) : (
        <Skeleton
          style={{ height: 46, width: "min(360px, 80vw)" }}
          data-testid="greeting-loading"
        />
      )}

      {/* Quick action pills */}
      <div
        className="flex w-full flex-col items-center"
        style={{ gap: 10, maxWidth: 820 }}
      >
        <div
          className="flex w-full flex-wrap items-center justify-center"
          style={{ gap: 8 }}
          role="group"
          aria-label="Quick actions"
        >
          {ACTION_CHIPS.map((chip) => (
            <ActionChip key={chip.label} chip={chip} onClick={() => handleChipClick(chip)} />
          ))}
        </div>
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
        demoSeed={demoSeed}
        onDemoSeedConsumed={() => setDemoSeed(undefined)}
        onDraftFromChat={onDraftFromChat}
        resume={resume}
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

