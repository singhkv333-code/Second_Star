import { theme } from "../theme";
import { fontSans, fontSerifItalic } from "../fonts";

type Props = {
  text: string;
  side: "user" | "assistant";
  // If true, the user bubble uses the italic serif (consistent with Scene 3).
  serif?: boolean;
  // Show typing indicator (3 dots) instead of text — assistant only.
  typing?: boolean;
  // 0..1 dot pulse phase, supplied by parent so timing is frame-driven.
  dotPhase?: number;
};

export const ChatBubble: React.FC<Props> = ({
  text,
  side,
  serif = false,
  typing = false,
  dotPhase = 0,
}) => {
  const isUser = side === "user";
  return (
    <div
      style={{
        display: "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
        width: "100%",
      }}
    >
      <div
        style={{
          maxWidth: 720,
          padding: typing ? "18px 22px" : "20px 26px",
          background: isUser ? theme.cream : theme.white,
          border: `1px solid ${theme.border}`,
          borderRadius: 24,
          boxShadow: theme.shadowSm,
          fontFamily: serif ? fontSerifItalic : fontSans,
          fontStyle: serif ? "italic" : "normal",
          fontWeight: serif ? 400 : 400,
          fontSize: serif ? 26 : 20,
          lineHeight: 1.45,
          color: theme.ink,
        }}
      >
        {typing ? <TypingDots phase={dotPhase} /> : text}
      </div>
    </div>
  );
};

const TypingDots: React.FC<{ phase: number }> = ({ phase }) => {
  // Three dots bouncing at 1.2 Hz, staggered by ~0.2 cycle each.
  const dot = (offset: number): number => {
    const x = (phase + offset) % 1;
    // peak at x=0.25 (bounce up)
    const t = Math.sin(x * Math.PI);
    return 1 + t * 0.6;
  };
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
      {[0, 0.2, 0.4].map((o, i) => (
        <span
          key={i}
          style={{
            display: "inline-block",
            width: 10,
            height: 10,
            borderRadius: 999,
            background: theme.gray,
            transform: `scale(${dot(o)})`,
          }}
        />
      ))}
    </span>
  );
};
