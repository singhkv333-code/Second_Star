import { useCurrentFrame, interpolate, Easing } from "remotion";
import { colors } from "../theme";
import { fontSerif } from "../fonts";
import { PivotLogo } from "../components/PivotLogo";

// Final beat: centered pivot wordmark + tagline over a soft vignette.
// Receives a backdrop fade so the previous scene's pixels dim out
// rather than cutting.

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();

  const backdropT = interpolate(frame, [0, 22], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });
  const wordmarkT = interpolate(frame, [10, 36], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const wordmarkY = interpolate(wordmarkT, [0, 1], [16, 0]);
  const taglineT = interpolate(frame, [24, 50], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: colors.bgBase,
        opacity: backdropT,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 28,
      }}
    >
      <div
        style={{
          opacity: wordmarkT,
          transform: `translateY(${wordmarkY}px)`,
        }}
      >
        <PivotLogo size={140} wordmark wordmarkSize={110} form={1} />
      </div>
      <div
        style={{
          opacity: taglineT,
          fontFamily: fontSerif,
          fontWeight: 500,
          fontSize: 32,
          letterSpacing: "-0.02em",
          color: colors.textSecondary,
        }}
      >
        From thought to trade.
      </div>
    </div>
  );
};
