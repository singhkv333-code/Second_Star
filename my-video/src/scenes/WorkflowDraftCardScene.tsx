import { useCurrentFrame, interpolate, Easing, spring, useVideoConfig } from "remotion";
import { colors, radii } from "../theme";
import { fontUi, fontMono } from "../fonts";

// Hero card: mirrors pivot-next/components/chat/WorkflowDraftCard.tsx
// for the chat-driven agent draft flow.
//
// Per-step scene timeline (scene-local frames):
//
//   0-14    card lifts up + shadow grows
//   8-22    sky "Agent" chip + "Draft" badge slide in
//   20-44   title types in character-by-character
//   30-50   description fades in
//   38-52   "Why this?" sparkle link appears
//   44-96   four step pills stagger in (icon snap + text slide)
//   96-110  "Save & activate ↗" primary pill appears
//   108-120 backtest · open in editor ghost links
//   118-130 amber warning footer slides up
//   130-160 hold; activate pill pulses then "Activated" flip
//   160-200 card recedes / cross-fades to next scene
//
// All animations are frame-deterministic (no CSS transitions or
// Tailwind animate classes, per Remotion rules).

type Props = {
  // Scene-local frame at which the card begins entering. Defaults to 0
  // so this can be dropped directly inside a <Sequence>.
  enterAt?: number;
  // Width of the card itself (the scene centers it horizontally).
  width?: number;
  // If true, the card flips to its "Activated · Running" confirmation
  // state. Used by the orchestrator at the end of the scene.
  activated?: boolean;
};

const TITLE = "RELIANCE Weekday Dip-Buy";
const DESCRIPTION =
  "Every Friday at 3:55 PM IST, if RELIANCE is down 1% or more intraday, market-buy ₹10,000 worth of shares.";

const STEPS = [
  { label: "Every Friday, 3:55 PM IST", icon: "calendar-clock" },
  { label: "Pull RELIANCE intraday quote", icon: "wallet" },
  { label: "Day change ≤ −1%", icon: "git-branch" },
  { label: "Register buy ₹10,000 (CNC)", icon: "shopping-cart" },
] as const;

export const WorkflowDraftCardScene: React.FC<Props> = ({
  enterAt = 0,
  width = 680,
  activated = false,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Card lift
  const cardT = spring({
    frame: frame - enterAt,
    fps,
    config: { damping: 14, mass: 0.6, stiffness: 120 },
    durationInFrames: 22,
  });

  // Generic stagger helper (scene-local)
  const at = (offset: number, dur = 14): number =>
    interpolate(frame, [enterAt + offset, enterAt + offset + dur], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.bezier(0.16, 1, 0.3, 1),
    });

  const skyChipT = at(8, 12);
  const draftChipT = spring({
    frame: frame - enterAt - 10,
    fps,
    config: { damping: 12, mass: 0.4, stiffness: 200 },
    durationInFrames: 14,
  });
  const descT = at(30, 16);
  const whyT = at(38, 14);

  // Title typewriter: ~1.2 frames/char
  const titleChars = Math.max(
    0,
    Math.min(TITLE.length, Math.floor((frame - enterAt - 20) / 1.2)),
  );
  const typedTitle = TITLE.slice(0, titleChars);

  const stepStart = 44;
  const stepStep = 8; // frames between successive step pills

  const savePillT = at(96, 14);
  const ghostLinksT = at(108, 12);
  const warningT = at(118, 12);

  // Activate pulse from frame 130 onwards (3 pulses, 16f each)
  const pulse = interpolate(
    frame,
    [enterAt + 130, enterAt + 138, enterAt + 146],
    [0, 1, 0],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.bezier(0.34, 1.56, 0.64, 1),
    },
  );

  // Activated state cross-fade
  const activatedT = activated ? 1 : 0;

  return (
    <div
      style={{
        width,
        background: colors.bgCard,
        border: `1px solid ${colors.border}`,
        borderRadius: radii.lg,
        overflow: "hidden",
        opacity: cardT,
        transform: `translateY(${(1 - cardT) * 16}px)`,
        boxShadow: `0 ${30 * cardT}px ${80 * cardT}px rgba(0, 0, 0, ${0.45 * cardT}), 0 ${6 * cardT}px ${16 * cardT}px rgba(0, 0, 0, ${0.3 * cardT})`,
        fontFamily: fontUi,
      }}
    >
      {/* ─── Header row ─── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "20px 24px 0",
        }}
      >
        {/* Sky-tinted "Agent" chip — matches the real card's sky-100/dark
            variant (the only non-neutral accent in this scene). */}
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "3px 10px",
            borderRadius: radii.sm,
            background: "rgba(56, 189, 248, 0.15)",
            color: "#7dd3fc",
            fontFamily: fontUi,
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: "-0.005em",
            opacity: skyChipT,
            transform: `translateX(${(1 - skyChipT) * -8}px)`,
          }}
        >
          Agent
        </span>

        {/* "1 · Draft" right-side meta */}
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            transform: `scale(${0.6 + draftChipT * 0.4})`,
            opacity: draftChipT,
          }}
        >
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              fontFamily: fontMono,
              fontSize: 11,
              color: colors.textSecondary,
              fontWeight: 500,
            }}
          >
            <ClockIcon />
            1
          </span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              fontFamily: fontUi,
              fontSize: 11,
              color: colors.textSecondary,
              fontWeight: 500,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: 999,
                background: colors.textTertiary,
              }}
            />
            Draft
          </span>
        </div>
      </div>

      {/* ─── Title ─── */}
      <h3
        style={{
          margin: "12px 24px 0",
          fontFamily: fontUi,
          fontSize: 22,
          fontWeight: 600,
          color: colors.textPrimary,
          letterSpacing: "-0.01em",
          lineHeight: 1.2,
          minHeight: 28,
        }}
      >
        {typedTitle}
      </h3>

      {/* ─── Description ─── */}
      <p
        style={{
          margin: "10px 24px 0",
          fontFamily: fontUi,
          fontSize: 13.5,
          color: colors.textSecondary,
          lineHeight: 1.55,
          opacity: descT,
          transform: `translateY(${(1 - descT) * 4}px)`,
        }}
      >
        {DESCRIPTION}
      </p>

      {/* ─── Why this? ─── */}
      <div
        style={{
          margin: "10px 24px 0",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          fontFamily: fontUi,
          fontSize: 11.5,
          color: colors.textSecondary,
          fontWeight: 500,
          opacity: whyT,
        }}
      >
        <SparkleIcon />
        Why this?
      </div>

      {/* ─── Step pills ─── */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          padding: "20px 24px 4px",
        }}
      >
        {STEPS.map((step, i) => {
          const t = at(stepStart + i * stepStep, 14);
          const iconT = at(stepStart + i * stepStep + 4, 10);
          return (
            <div
              key={step.label}
              style={{
                opacity: t,
                transform: `translateX(${(1 - t) * 8}px)`,
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "12px 14px",
                background: colors.bgElevated,
                border: `1px solid ${colors.border}`,
                borderRadius: radii.md,
              }}
            >
              <div
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 999,
                  background: colors.bgPrimary,
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: colors.textSecondary,
                  transform: `scale(${0.7 + iconT * 0.32})`,
                }}
              >
                <StepIcon name={step.icon} />
              </div>
              <span
                style={{
                  fontFamily: fontUi,
                  fontSize: 13.5,
                  fontWeight: 500,
                  color: colors.textPrimary,
                  letterSpacing: "-0.005em",
                }}
              >
                {step.label}
              </span>
            </div>
          );
        })}
      </div>

      {/* ─── Save & activate pill ─── */}
      <div style={{ padding: "16px 24px 0" }}>
        <button
          style={{
            width: "100%",
            height: 50,
            borderRadius: radii.pill,
            border: "none",
            background:
              activatedT > 0.5
                ? colors.profit
                : colors.textPrimary,
            color: activatedT > 0.5 ? "#062a18" : colors.bgBase,
            fontFamily: fontUi,
            fontSize: 14,
            fontWeight: 600,
            letterSpacing: "-0.005em",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            opacity: savePillT,
            transform: `translateY(${(1 - savePillT) * 8}px) scale(${1 + pulse * 0.04})`,
            boxShadow:
              pulse > 0
                ? `0 0 0 ${pulse * 10}px rgba(255, 255, 255, 0.08)`
                : "none",
            cursor: "pointer",
          }}
        >
          {activatedT > 0.5 ? (
            <>
              <CheckIcon />
              Activated · Running
            </>
          ) : (
            <>
              Save &amp; activate
              <ArrowUpRightIcon />
            </>
          )}
        </button>
      </div>

      {/* ─── Ghost links ─── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 14,
          padding: "12px 24px 0",
          fontFamily: fontUi,
          fontSize: 11.5,
          color: colors.textTertiary,
          opacity: ghostLinksT,
        }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          <HistoryIcon />
          Backtest
        </span>
        <span>·</span>
        <span>Open in editor</span>
      </div>

      {/* ─── Amber warning footer ─── */}
      <div
        style={{
          marginTop: 16,
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "10px 24px",
          background: "rgba(245, 158, 11, 0.06)",
          borderTop: `1px solid ${colors.border}`,
          fontFamily: fontUi,
          fontSize: 11,
          color: "#f5b454",
          opacity: warningT,
          transform: `translateY(${(1 - warningT) * 4}px)`,
        }}
      >
        <AlertIcon />
        This is automation of your instructions, not financial advice.
      </div>
    </div>
  );
};

// ─────────────── Icons (inline so we don't depend on lucide here) ───────────────

const StepIcon: React.FC<{ name: string }> = ({ name }) => {
  switch (name) {
    case "calendar-clock":
      return (
        <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 7.5V6a2 2 0 0 0-2-2h-1V2m-12 2H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h6" />
          <path d="M8 2v4M3 10h18" />
          <circle cx="17" cy="17" r="5" />
          <path d="M17 14v3l1.5 1.5" />
        </svg>
      );
    case "wallet":
      return (
        <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12V7H5a2 2 0 0 1 0-4h14v4" />
          <path d="M3 5v14a2 2 0 0 0 2 2h16v-5" />
          <path d="M18 12a2 2 0 0 0 0 4h4v-4Z" />
        </svg>
      );
    case "git-branch":
      return (
        <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <line x1="6" y1="3" x2="6" y2="15" />
          <circle cx="18" cy="6" r="3" />
          <circle cx="6" cy="18" r="3" />
          <path d="M18 9a9 9 0 0 1-9 9" />
        </svg>
      );
    case "shopping-cart":
      return (
        <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="9" cy="21" r="1" />
          <circle cx="20" cy="21" r="1" />
          <path d="M1 1h4l2.7 13.4a2 2 0 0 0 2 1.6h9.7a2 2 0 0 0 2-1.6L23 6H6" />
        </svg>
      );
    default:
      return null;
  }
};

const SparkleIcon: React.FC = () => (
  <svg width={12} height={12} viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z" />
  </svg>
);

const ClockIcon: React.FC = () => (
  <svg width={10} height={10} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <path d="M12 6v6l4 2" />
  </svg>
);

const ArrowUpRightIcon: React.FC = () => (
  <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
    <path d="M7 17L17 7" />
    <path d="M7 7h10v10" />
  </svg>
);

const CheckIcon: React.FC = () => (
  <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M20 6L9 17l-5-5" />
  </svg>
);

const HistoryIcon: React.FC = () => (
  <svg width={10} height={10} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
    <path d="M3 3v5h5" />
    <path d="M12 7v5l4 2" />
  </svg>
);

const AlertIcon: React.FC = () => (
  <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="8" x2="12" y2="12" />
    <line x1="12" y1="16" x2="12.01" y2="16" />
  </svg>
);
