import { useCurrentFrame, interpolate } from "remotion";
import { colors, radii } from "../theme";
import { fontUi, fontMono } from "../fonts";
import { PivotLogo } from "../components/PivotLogo";

// Full Pivot app chrome: topbar (logo + search + metric strip + account)
// and left sidebar with conversation list mock. Children render inside
// the main content area. Used by ChatGreeting/Composer/Thinking/Snapshot
// scenes as a stable backdrop so the chrome stays put while the chat
// region animates.

type Props = {
  children?: React.ReactNode;
  // Optional camera transform applied to the entire shell (used for the
  // zoom-into-card moments in later scenes).
  cameraScale?: number;
  cameraX?: number;
  cameraY?: number;
  // Reveal progress for the shell chrome (0..1). Used when entering from
  // the logo-form scene so the chrome materializes around the hero logo
  // settling into the topbar.
  chromeReveal?: number;
  // 0..1 — how much depth-of-field blur to apply to the chrome (topbar,
  // sidebars). 1 = ~6px blur. Used to push a card into the foreground.
  chromeBlur?: number;
  // Override the right rail counter (default 3). Used by the Active
  // Agents 3→4 beat to tick the badge.
  rightRailCount?: number;
  // When provided, prepends a new "Live" card at the top of the right
  // rail. Tuple of `[name, enterProgress]` where enterProgress is 0..1.
  liveAgent?: { name: string; enter: number };
};

const TOPBAR_HEIGHT = 64;
const SIDEBAR_WIDTH = 260;
const RIGHT_RAIL_WIDTH = 320;

export const AppShell: React.FC<Props> = ({
  children,
  cameraScale = 1,
  cameraX = 0,
  cameraY = 0,
  chromeReveal,
  chromeBlur = 0,
  rightRailCount = 3,
  liveAgent,
}) => {
  const frame = useCurrentFrame();
  const reveal =
    chromeReveal ??
    interpolate(frame, [0, 18], [1, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
  // Chrome blur lifts the foreground card during push-ins. Cap at 6px
  // so the chrome stays recognizable but unfocused.
  const blurPx = Math.max(0, Math.min(1, chromeBlur)) * 6;

  return (
    <div
      style={{
        width: 1920,
        height: 1080,
        background: colors.bgBase,
        position: "relative",
        transform: `translate(${cameraX}px, ${cameraY}px) scale(${cameraScale})`,
        transformOrigin: "center center",
      }}
    >
      {/* Topbar */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: TOPBAR_HEIGHT,
          background: colors.bgPrimary,
          borderBottom: `1px solid ${colors.border}`,
          display: "flex",
          alignItems: "center",
          padding: "0 24px",
          gap: 24,
          opacity: reveal,
          filter: blurPx > 0 ? `blur(${blurPx}px)` : "none",
        }}
      >
        {/* Logo cluster — left aligned, leaves space for sidebar to feel
            integrated. Sized to match pivot-next's topbar wordmark (22px). */}
        <div style={{ width: SIDEBAR_WIDTH - 24, display: "flex", alignItems: "center" }}>
          <PivotLogo size={34} wordmark wordmarkSize={26} form={1} />
        </div>

        {/* Search pill */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            height: 38,
            padding: "0 16px",
            width: 360,
            background: colors.bgPrimary,
            border: `1px solid ${colors.border}`,
            borderRadius: radii.pill,
            color: colors.textTertiary,
            fontFamily: fontUi,
            fontSize: 13,
            opacity: reveal,
          }}
        >
          <SearchIcon />
          <span>Search stocks, strategies, conversations…</span>
        </div>

        {/* Metric strip — pushes to the right */}
        <div style={{ marginLeft: "auto", display: "flex", gap: 28, alignItems: "center", opacity: reveal }}>
          <Metric label="Portfolio value" value="₹ 18,42,617" />
          <Metric label="Day P&L" value="+₹ 9,840" positive />
          <Metric label="Cash" value="₹ 1,24,300" />
          {/* Account chip */}
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: radii.pill,
              background: "linear-gradient(135deg, #4f46e5 0%, #0ea5e9 100%)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#fff",
              fontFamily: fontUi,
              fontWeight: 600,
              fontSize: 14,
            }}
          >
            K
          </div>
        </div>
      </div>

      {/* Sidebar */}
      <Sidebar reveal={reveal} blurPx={blurPx} />

      {/* Right rail — Active Agents */}
      <ActiveAgentsRail
        reveal={reveal}
        count={rightRailCount}
        live={liveAgent}
        blurPx={blurPx}
      />

      {/* Main content */}
      <div
        style={{
          position: "absolute",
          top: TOPBAR_HEIGHT,
          left: SIDEBAR_WIDTH,
          right: RIGHT_RAIL_WIDTH,
          bottom: 0,
          background: colors.bgBase,
          overflow: "hidden",
        }}
      >
        {children}
      </div>
    </div>
  );
};

const Metric: React.FC<{ label: string; value: string; positive?: boolean }> = ({
  label,
  value,
  positive,
}) => (
  <div style={{ display: "flex", flexDirection: "column", gap: 2, alignItems: "flex-end" }}>
    <span
      style={{
        fontSize: 10,
        fontFamily: fontUi,
        fontWeight: 500,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        color: colors.textTertiary,
      }}
    >
      {label}
    </span>
    <span
      style={{
        fontSize: 14,
        fontFamily: fontMono,
        fontWeight: 600,
        fontVariantNumeric: "tabular-nums",
        color: positive ? colors.profit : colors.textPrimary,
      }}
    >
      {value}
    </span>
  </div>
);

const SearchIcon: React.FC = () => (
  <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="8" />
    <line x1="21" y1="21" x2="16.65" y2="16.65" />
  </svg>
);

const Sidebar: React.FC<{ reveal: number; blurPx: number }> = ({
  reveal,
  blurPx,
}) => {
  const conversations = [
    { title: "RELIANCE chart pull", ago: "now", active: true },
    { title: "NIFTY weekly close ranges", ago: "2 h" },
    { title: "Buy 5 RELIANCE at open agent", ago: "yesterday" },
    { title: "Portfolio rebalance Q2", ago: "May 11" },
    { title: "TCS earnings reaction watch", ago: "May 9" },
    { title: "Bank Nifty 1-min momentum", ago: "May 6" },
  ];
  return (
    <div
      style={{
        position: "absolute",
        top: TOPBAR_HEIGHT,
        left: 0,
        bottom: 0,
        width: SIDEBAR_WIDTH,
        background: colors.bgPrimary,
        borderRight: `1px solid ${colors.border}`,
        padding: "16px 12px",
        opacity: reveal,
        filter: blurPx > 0 ? `blur(${blurPx}px)` : "none",
      }}
    >
      <button
        style={{
          width: "100%",
          padding: "9px 12px",
          display: "flex",
          alignItems: "center",
          gap: 8,
          borderRadius: radii.md,
          border: `1px solid ${colors.border}`,
          background: colors.bgCard,
          color: colors.textPrimary,
          fontFamily: fontUi,
          fontSize: 13,
          fontWeight: 500,
        }}
      >
        <PlusIcon /> New chat
      </button>

      <div
        style={{
          marginTop: 18,
          fontSize: 10.5,
          fontFamily: fontUi,
          fontWeight: 500,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: colors.textTertiary,
          paddingLeft: 8,
          marginBottom: 6,
        }}
      >
        Conversations
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {conversations.map((c) => (
          <div
            key={c.title}
            style={{
              padding: "8px 10px",
              display: "flex",
              flexDirection: "column",
              gap: 2,
              borderRadius: radii.sm,
              background: c.active ? "rgba(255,255,255,0.04)" : "transparent",
            }}
          >
            <span
              style={{
                fontFamily: fontUi,
                fontSize: 12.5,
                color: c.active ? colors.textPrimary : colors.textSecondary,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {c.title}
            </span>
            <span
              style={{
                fontFamily: fontUi,
                fontSize: 10.5,
                color: colors.textTertiary,
              }}
            >
              {c.ago}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};

const PlusIcon: React.FC = () => (
  <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </svg>
);

// ─────────────── Active Agents (right rail) ───────────────
//
// Matches the real pivot-next ActiveAgentsRail visual: three idle
// strategy cards by default. When `live` is supplied, a green-dot "Live"
// card prepends at the top — used for the 3→4 beat. `count` controls
// the badge in the header so we can tick it independently of the card
// list (the count animates a few frames after the card lands).

const IDLE_AGENTS = [
  { name: "INFY weekly dip-buy" },
  { name: "TCS monthly SIP" },
  { name: "RELIANCE 3:55 PM weekday buy" },
] as const;

const ActiveAgentsRail: React.FC<{
  reveal: number;
  count: number;
  live?: { name: string; enter: number };
  blurPx: number;
}> = ({ reveal, count, live, blurPx }) => {
  const useFrame = useCurrentFrame();
  // 1 Hz pulse phase for the live dot.
  const pulse =
    Math.sin((useFrame / 30) * Math.PI * 2) * 0.5 + 0.5; // 0..1
  // Subtle 1-frame scale dip on the counter when count jumps integer
  const counterScale = 1; // (we keep it stable; the badge is the focal point)

  return (
    <div
      style={{
        position: "absolute",
        top: TOPBAR_HEIGHT,
        right: 0,
        bottom: 0,
        width: RIGHT_RAIL_WIDTH,
        background: colors.bgPrimary,
        borderLeft: `1px solid ${colors.border}`,
        padding: "18px 16px",
        opacity: reveal,
        filter: blurPx > 0 ? `blur(${blurPx}px)` : "none",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 14,
        }}
      >
        <h4
          style={{
            margin: 0,
            fontFamily: fontUi,
            fontSize: 14,
            fontWeight: 600,
            color: colors.textPrimary,
            letterSpacing: "-0.005em",
          }}
        >
          Active Agents
        </h4>
        <span
          style={{
            padding: "2px 10px",
            borderRadius: radii.pill,
            background: "rgba(16, 185, 129, 0.15)",
            color: colors.profit,
            fontFamily: fontMono,
            fontSize: 11.5,
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
            transform: `scale(${counterScale})`,
            display: "inline-block",
            minWidth: 24,
            textAlign: "center",
          }}
        >
          {count}
        </span>
      </div>

      {/* New "Live" card (optional) */}
      {live && live.enter > 0.01 && (
        <div
          style={{
            opacity: live.enter,
            transform: `translateY(${(1 - live.enter) * -10}px)`,
            marginBottom: 10,
          }}
        >
          <AgentRowCard
            name={live.name}
            status="Live"
            secondary="Active · Next: Fri 3:55 PM"
            pulse={pulse}
          />
        </div>
      )}

      {/* Idle cards */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {IDLE_AGENTS.map((a) => (
          <AgentRowCard key={a.name} name={a.name} status="Idle" />
        ))}
      </div>
    </div>
  );
};

const AgentRowCard: React.FC<{
  name: string;
  status: "Idle" | "Live";
  secondary?: string;
  pulse?: number;
}> = ({ name, status, secondary, pulse = 0 }) => {
  const live = status === "Live";
  return (
    <div
      style={{
        padding: "12px 12px",
        background: colors.bgCard,
        border: `1px solid ${colors.border}`,
        borderRadius: radii.md,
        fontFamily: fontUi,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 6,
        }}
      >
        <span
          style={{
            fontSize: 10,
            fontWeight: 500,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            color: colors.textTertiary,
          }}
        >
          Strategy
        </span>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontFamily: fontUi,
            fontSize: 11,
            fontWeight: 500,
            color: live ? colors.profit : colors.textTertiary,
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: 999,
              background: live ? colors.profit : colors.textTertiary,
              transform: `scale(${1 + pulse * 0.5})`,
              boxShadow:
                live && pulse > 0
                  ? `0 0 0 ${3 + pulse * 5}px rgba(16, 185, 129, 0.12)`
                  : "none",
            }}
          />
          {status}
        </span>
      </div>
      <div
        style={{
          fontFamily: fontUi,
          fontSize: 13,
          fontWeight: 500,
          color: colors.textPrimary,
        }}
      >
        {name}
      </div>
      {secondary ? (
        <div
          style={{
            marginTop: 6,
            fontSize: 11,
            color: colors.textTertiary,
          }}
        >
          {secondary}
        </div>
      ) : (
        <div
          style={{
            marginTop: 6,
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11,
            color: colors.textTertiary,
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: 999,
              border: `1.2px solid ${colors.textTertiary}`,
            }}
          />
          Never run
          <span
            style={{
              marginLeft: "auto",
              color: colors.textDisabled,
            }}
          >
            —
          </span>
        </div>
      )}
    </div>
  );
};
