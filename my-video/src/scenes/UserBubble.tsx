import { useCurrentFrame, interpolate, Easing } from "remotion";
import { colors } from "../theme";
import { fontUi } from "../fonts";

// Right-aligned user message bubble. Slides in from the right and stays.

type Props = {
  text: string;
  // Frame at which the bubble starts entering (relative to scene-local 0)
  enterAt?: number;
};

export const UserBubble: React.FC<Props> = ({ text, enterAt = 0 }) => {
  const frame = useCurrentFrame();
  const t = interpolate(frame, [enterAt, enterAt + 18], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "flex-end",
        opacity: t,
        transform: `translate(${(1 - t) * 24}px, ${(1 - t) * 6}px)`,
      }}
    >
      <div
        style={{
          maxWidth: 720,
          padding: "12px 18px",
          background: colors.bgCard,
          border: `1px solid ${colors.border}`,
          borderRadius: 20,
          color: colors.textPrimary,
          fontFamily: fontUi,
          fontSize: 15.5,
          lineHeight: 1.4,
        }}
      >
        {text}
      </div>
    </div>
  );
};
