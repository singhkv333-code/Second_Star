import { AbsoluteFill, useCurrentFrame, interpolate, Easing } from "remotion";
import { theme } from "../theme";
import { fontSerif, fontSerifItalic } from "../fonts";

// Frames 90-180 (3s).
// Local frames here run 0-90 (the Sequence rebases useCurrentFrame).
//   0-20   "One message." fades in + slides up 20px
//   25-45  "That's all investing takes." fades in
//   50-70  Green underline draws under "One message"
//   70-90  hold
export const Scene2_Tagline: React.FC = () => {
  const frame = useCurrentFrame();

  const headlineT = interpolate(frame, [0, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const headlineY = interpolate(headlineT, [0, 1], [20, 0]);

  const subT = interpolate(frame, [25, 45], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  const underlineT = interpolate(frame, [50, 70], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });

  return (
    <AbsoluteFill
      style={{
        background: theme.cream,
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div style={{ maxWidth: 1100, textAlign: "center" }}>
        {/* Headline + underline track */}
        <div
          style={{
            display: "inline-block",
            position: "relative",
            opacity: headlineT,
            transform: `translateY(${headlineY}px)`,
          }}
        >
          <span
            style={{
              fontFamily: fontSerif,
              fontWeight: 400,
              fontSize: 132,
              color: theme.ink,
              lineHeight: 1,
              letterSpacing: "-0.02em",
            }}
          >
            One message.
          </span>
          {/* Underline draws left → right */}
          <div
            style={{
              position: "absolute",
              left: 0,
              bottom: -14,
              height: 4,
              width: `${underlineT * 100}%`,
              background: theme.green,
              borderRadius: 2,
            }}
          />
        </div>

        <div
          style={{
            marginTop: 50,
            opacity: subT,
            fontFamily: fontSerifItalic,
            fontStyle: "italic",
            fontWeight: 400,
            fontSize: 64,
            color: theme.gray,
            lineHeight: 1.1,
            letterSpacing: "-0.01em",
          }}
        >
          That's all investing takes.
        </div>
      </div>
    </AbsoluteFill>
  );
};
