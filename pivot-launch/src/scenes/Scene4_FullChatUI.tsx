import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  Easing,
  spring,
  useVideoConfig,
} from "remotion";
import { ArrowRight, Bot, Calendar, LineChart, MessageSquare, Search, Wallet } from "lucide-react";
import { theme } from "../theme";
import { fontSans } from "../fonts";
import { PivotLogo } from "../components/PivotLogo";
import { ChatBubble } from "../components/ChatBubble";
import { AgentCard } from "../components/AgentCard";
import { USER_PROMPT, AI_RESPONSE, PORTFOLIO, EXISTING_AGENTS } from "../mock";

// Scene 4 — frames 300-420 (local 0-120, 4s)
//   0-20   "camera" zooms out: input shrinks from huge/centered to its
//          docked position at the bottom of the chat column. UI chrome
//          (top bar, sidebars) fades in.
//   20-40  user bubble slides up
//   40-60  typing indicator
//   60-90  AI response streams in
//   90-120 Agent card scales in
export const Scene4_FullChatUI: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Chrome reveal
  const chromeT = interpolate(frame, [0, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  // User message
  const userT = interpolate(frame, [20, 40], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  // Typing indicator visible 40..60
  const typingIn = interpolate(frame, [40, 50], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const typingOut = interpolate(frame, [58, 64], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const typingOpacity = typingIn * typingOut;
  const dotPhase = ((frame - 40) / 30) % 1;

  // Assistant response — stream characters in
  const aiTypeStart = 64;
  const aiTypeEnd = 90;
  const aiCharCount = Math.floor(
    interpolate(frame, [aiTypeStart, aiTypeEnd], [0, AI_RESPONSE.length], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );
  const aiText = AI_RESPONSE.slice(0, aiCharCount);
  const aiShown = frame >= aiTypeStart;

  // Agent card spring
  const cardT = spring({
    frame: frame - 90,
    fps,
    config: { damping: 14, mass: 0.6, stiffness: 120 },
    durationInFrames: 24,
  });

  return (
    <AbsoluteFill style={{ background: theme.cream }}>
      {/* ────────── Top bar ────────── */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 64,
          background: theme.white,
          borderBottom: `1px solid ${theme.border}`,
          display: "flex",
          alignItems: "center",
          padding: "0 28px",
          gap: 24,
          opacity: chromeT,
        }}
      >
        <div style={{ width: 220, transform: "scale(0.85)", transformOrigin: "left center" }}>
          <PivotLogo scale={1} form={1} />
        </div>

        {/* Center search (ghosted) */}
        <div
          style={{
            flex: 1,
            maxWidth: 520,
            margin: "0 auto",
            height: 38,
            border: `1px solid ${theme.border}`,
            borderRadius: 999,
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "0 16px",
            color: theme.gray,
            fontFamily: fontSans,
            fontSize: 13,
          }}
        >
          <Search size={14} />
          <span>Search stocks, strategies, conversations…</span>
        </div>

        {/* Portfolio metrics */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 24,
            fontFamily: fontSans,
          }}
        >
          <Metric label="Portfolio value" value={PORTFOLIO.value} />
          <Metric label="Day P&L" value={PORTFOLIO.dayPnl} positive />
          <Metric
            label="Total P&L"
            value={`${PORTFOLIO.totalPnl} (${PORTFOLIO.totalPnlPct})`}
            positive
          />
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 999,
              background: theme.green,
              color: theme.white,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontFamily: fontSans,
              fontWeight: 600,
              fontSize: 14,
            }}
          >
            K
          </div>
        </div>
      </div>

      {/* ────────── Left sidebar ────────── */}
      <div
        style={{
          position: "absolute",
          top: 64,
          left: 0,
          bottom: 0,
          width: 220,
          background: theme.white,
          borderRight: `1px solid ${theme.border}`,
          padding: "20px 14px",
          fontFamily: fontSans,
          opacity: chromeT,
        }}
      >
        <NavItem icon={<MessageSquare size={16} />} label="Chat" active />
        <NavItem icon={<Wallet size={16} />} label="Portfolio" />
        <NavItem icon={<Bot size={16} />} label="Agents" />
        <NavItem icon={<Calendar size={16} />} label="Calendar" />
        <NavItem icon={<LineChart size={16} />} label="Screener" />
      </div>

      {/* ────────── Right sidebar (Active Agents) ────────── */}
      <div
        style={{
          position: "absolute",
          top: 64,
          right: 0,
          bottom: 0,
          width: 320,
          background: theme.white,
          borderLeft: `1px solid ${theme.border}`,
          padding: "22px 18px",
          fontFamily: fontSans,
          opacity: chromeT,
        }}
      >
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
              fontFamily: fontSans,
              fontSize: 14,
              fontWeight: 600,
              color: theme.ink,
              letterSpacing: "-0.01em",
            }}
          >
            Active Agents
          </h4>
          <span
            style={{
              padding: "2px 10px",
              borderRadius: 999,
              background: theme.cream,
              color: theme.gray,
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            3
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {EXISTING_AGENTS.map((a) => (
            <IdleAgentCard key={a.name} name={a.name} status={a.status} />
          ))}
        </div>
      </div>

      {/* ────────── Main chat column ────────── */}
      <div
        style={{
          position: "absolute",
          top: 64,
          left: 220,
          right: 320,
          bottom: 0,
          padding: "40px 80px 0",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* Conversation */}
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            gap: 22,
            justifyContent: "flex-start",
            paddingTop: 30,
          }}
        >
          {/* User bubble */}
          <div
            style={{
              opacity: userT,
              transform: `translateY(${(1 - userT) * 14}px)`,
            }}
          >
            <ChatBubble side="user" text={USER_PROMPT} serif />
          </div>

          {/* Typing indicator */}
          {typingOpacity > 0.01 && (
            <div style={{ opacity: typingOpacity }}>
              <ChatBubble side="assistant" text="" typing dotPhase={dotPhase} />
            </div>
          )}

          {/* AI response */}
          {aiShown && (
            <div>
              <ChatBubble side="assistant" text={aiText} />
            </div>
          )}

          {/* Agent card */}
          {cardT > 0.01 && (
            <div
              style={{
                marginTop: 10,
                transform: `translateY(${(1 - cardT) * 16}px)`,
              }}
            >
              <AgentCard enter={cardT} />
            </div>
          )}
        </div>

        {/* Docked input pill at the bottom */}
        <div
          style={{
            padding: "16px 0 28px",
            opacity: chromeT,
          }}
        >
          <DockedInput />
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ─────────────── Helpers ───────────────

const Metric: React.FC<{ label: string; value: string; positive?: boolean }> = ({
  label,
  value,
  positive,
}) => (
  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
    <span style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", color: theme.gray, fontWeight: 500 }}>
      {label}
    </span>
    <span
      style={{
        fontSize: 13,
        fontWeight: 600,
        color: positive ? theme.positive : theme.ink,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {value}
    </span>
  </div>
);

const NavItem: React.FC<{ icon: React.ReactNode; label: string; active?: boolean }> = ({
  icon,
  label,
  active,
}) => (
  <div
    style={{
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "10px 12px",
      borderRadius: 10,
      background: active ? theme.cream : "transparent",
      color: active ? theme.ink : theme.grayMid,
      fontSize: 14,
      fontWeight: active ? 500 : 400,
      marginBottom: 4,
    }}
  >
    {icon}
    {label}
  </div>
);

const IdleAgentCard: React.FC<{ name: string; status: string }> = ({
  name,
  status,
}) => (
  <div
    style={{
      padding: "14px 14px",
      border: `1px solid ${theme.border}`,
      borderRadius: 12,
      background: theme.white,
    }}
  >
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
      <span style={{ fontFamily: fontSans, fontSize: 11, color: theme.gray, fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.06em" }}>Strategy</span>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontFamily: fontSans, fontSize: 11, color: theme.gray, fontWeight: 500 }}>
        <span style={{ width: 7, height: 7, borderRadius: 999, background: theme.gray }} />
        {status}
      </span>
    </div>
    <div style={{ fontFamily: fontSans, fontSize: 13, color: theme.ink, fontWeight: 500 }}>
      {name}
    </div>
  </div>
);

const DockedInput: React.FC = () => (
  <div
    style={{
      height: 56,
      background: theme.white,
      border: `1px solid ${theme.border}`,
      borderRadius: 28,
      boxShadow: theme.shadowSm,
      display: "flex",
      alignItems: "center",
      padding: "0 10px 0 22px",
      gap: 10,
    }}
  >
    <span
      style={{
        flex: 1,
        fontFamily: fontSans,
        fontSize: 14,
        color: theme.gray,
      }}
    >
      Ask Pivot anything…
    </span>
    <button
      style={{
        width: 38,
        height: 38,
        borderRadius: 999,
        background: theme.ink,
        color: theme.white,
        border: "none",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <ArrowRight size={16} strokeWidth={2.4} />
    </button>
  </div>
);
