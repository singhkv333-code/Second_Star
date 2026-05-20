import { useCurrentFrame, interpolate, Easing } from "remotion";
import { colors, radii } from "../theme";
import { fontUi } from "../fonts";

// Composer — pinned to the bottom of the chat area like ChatDemo's
// ChatComposer. The prompt types character-by-character driven by frame.

// The user's natural-language instruction. Matches the user-bubble
// text and the WorkflowDraftCard generated downstream.
const PROMPT =
  "Buy ₹10k of Reliance every Friday at 3:55 PM if it's down 1% or more";

type Props = {
  // Frame at which typing begins (relative to scene's local 0)
  typeStart?: number;
  // Frames per character; lower = faster
  cps?: number;
  // Fade out (0..1) — used after the user "submits"
  exitProgress?: number;
  // Flash for the send button press
  sendPulseAt?: number;
};

export const Composer: React.FC<Props> = ({
  typeStart = 8,
  // ~30 fps × 1.4 frames/char ≈ 21 chars/s — fast enough for a 70-char
  // prompt to land in ~95 frames (≈ 3.2 s) so the eye reads the full
  // intent before the send pulse fires.
  cps = 1.4,
  exitProgress = 0,
  sendPulseAt,
}) => {
  const frame = useCurrentFrame();

  const charsRevealed = Math.max(
    0,
    Math.min(PROMPT.length, Math.floor((frame - typeStart) / cps)),
  );
  // Once the user has "submitted" (exit progress > 0.3), the composer
  // clears just like ChatGPT — the typed text disappears and we render
  // the placeholder hint again.
  const cleared = exitProgress > 0.3;
  const typed = cleared ? "" : PROMPT.slice(0, charsRevealed);

  // Caret blinks ~ every 18 frames (0.6s @ 30fps). Hidden once we start
  // exiting so the bubble feels finished.
  const caretOn = Math.floor(frame / 14) % 2 === 0 && !cleared;

  // Send pulse: brief scale-up + glow on the button.
  const pulse =
    sendPulseAt !== undefined
      ? interpolate(frame, [sendPulseAt, sendPulseAt + 6, sendPulseAt + 14], [1, 1.15, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: Easing.bezier(0.34, 1.56, 0.64, 1),
        })
      : 1;

  return (
    <div
      style={{
        position: "absolute",
        left: 56,
        right: 56,
        bottom: 32,
        opacity: 1 - exitProgress * 0.3,
        transform: `translateY(${exitProgress * 10}px)`,
      }}
    >
      <div
        style={{
          background: colors.bgCard,
          border: `1px solid ${colors.borderHover}`,
          borderRadius: 26,
          padding: "14px 18px 14px 20px",
          display: "flex",
          alignItems: "center",
          gap: 12,
          boxShadow: "0 16px 40px rgba(0,0,0,0.35), 0 2px 6px rgba(0,0,0,0.25)",
        }}
      >
        {/* Paperclip */}
        <button
          style={{
            width: 32,
            height: 32,
            borderRadius: radii.pill,
            background: "transparent",
            border: "none",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            color: colors.textTertiary,
            cursor: "pointer",
          }}
        >
          <PaperclipIcon />
        </button>

        {/* Typed prompt + caret */}
        <div
          style={{
            flex: 1,
            fontFamily: fontUi,
            fontSize: 17,
            fontWeight: 400,
            color: colors.textPrimary,
            lineHeight: 1.4,
            display: "flex",
            alignItems: "center",
            minHeight: 28,
          }}
        >
          {typed.length === 0 && (
            <span style={{ color: colors.textTertiary }}>
              Ask anything — “buy 5 RELIANCE at open”, “market pulse”…
            </span>
          )}
          {typed.length > 0 && <span>{typed}</span>}
          {caretOn && (
            <span
              style={{
                display: "inline-block",
                width: 2,
                height: 22,
                background: colors.textPrimary,
                marginLeft: 2,
                marginBottom: -3,
              }}
            />
          )}
        </div>

        {/* Mode pill */}
        <div
          style={{
            padding: "6px 12px",
            borderRadius: radii.pill,
            background: colors.bgElevated,
            color: colors.textSecondary,
            fontFamily: fontUi,
            fontSize: 12,
            fontWeight: 500,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <SparkleIcon />
          Agent
        </div>

        {/* Send button */}
        <button
          style={{
            width: 38,
            height: 38,
            borderRadius: radii.pill,
            background: typed.length > 0 ? colors.textPrimary : colors.bgElevated,
            color: typed.length > 0 ? colors.bgBase : colors.textTertiary,
            border: "none",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            transform: `scale(${pulse})`,
            transition: "background 200ms",
            boxShadow:
              sendPulseAt !== undefined && frame >= sendPulseAt && frame < sendPulseAt + 14
                ? "0 0 0 6px rgba(255,255,255,0.08)"
                : "none",
          }}
        >
          <ArrowUpIcon />
        </button>
      </div>

      {/* Three mode pills below the composer — matches the real Pivot
          chrome (Automation / Agent / Backtest). "Agent" is the active
          selection (filled), the others are ghost outlines. */}
      <div
        style={{
          display: "flex",
          gap: 10,
          justifyContent: "center",
          marginTop: 14,
        }}
      >
        <ModePill icon={<ZapIcon />} label="Automation" />
        <ModePill icon={<BotIcon />} label="Agent" active />
        <ModePill icon={<LineChartIcon />} label="Backtest" />
      </div>
    </div>
  );
};

const ModePill: React.FC<{
  icon: React.ReactNode;
  label: string;
  active?: boolean;
}> = ({ icon, label, active }) => (
  <div
    style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 6,
      padding: "6px 12px",
      borderRadius: radii.pill,
      background: active ? colors.bgElevated : "transparent",
      border: `1px solid ${active ? colors.borderHover : colors.border}`,
      color: active ? colors.textPrimary : colors.textTertiary,
      fontFamily: fontUi,
      fontSize: 11.5,
      fontWeight: 500,
    }}
  >
    {icon}
    {label}
  </div>
);

const ZapIcon: React.FC = () => (
  <svg width={11} height={11} viewBox="0 0 24 24" fill="currentColor">
    <path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z" />
  </svg>
);
const BotIcon: React.FC = () => (
  <svg width={11} height={11} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="8" width="18" height="12" rx="2" />
    <path d="M12 4v4M8 14h.01M16 14h.01" />
  </svg>
);
const LineChartIcon: React.FC = () => (
  <svg width={11} height={11} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 3v18h18" />
    <path d="M7 14l4-4 4 4 5-7" />
  </svg>
);

const PaperclipIcon: React.FC = () => (
  <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
  </svg>
);

const SparkleIcon: React.FC = () => (
  <svg width={12} height={12} viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z" />
  </svg>
);

const ArrowUpIcon: React.FC = () => (
  <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
    <line x1="12" y1="19" x2="12" y2="5" />
    <polyline points="5 12 12 5 19 12" />
  </svg>
);
