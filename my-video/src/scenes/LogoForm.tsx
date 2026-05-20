import { AbsoluteFill, useCurrentFrame, interpolate, Easing } from "remotion";
import { colors } from "../theme";
import { fontUi } from "../fonts";
import { PivotLogo } from "../components/PivotLogo";

// Scene 1 — Bars rise on a black void, "pivot" wordmark fades in,
// tagline appears underneath. The hero composition then shrinks
// toward the top-left corner to hand off to the AppShell scene.

type Props = {
  // overall scene length (frames) – used to time the handoff retreat
  totalFrames: number;
};

export const LogoForm: React.FC<Props> = ({ totalFrames }) => {
  const frame = useCurrentFrame();

  // Tagline timing
  const taglineOpacity = interpolate(frame, [52, 75], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const taglineY = interpolate(frame, [52, 75], [10, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  // Hand-off transform: at the very end of the scene, scale + translate
  // the hero cluster toward where the topbar wordmark lives so the next
  // scene can take the baton without a visual cut.
  const handoffStart = totalFrames - 25;
  const handoff = interpolate(frame, [handoffStart, totalFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });
  const scale = interpolate(handoff, [0, 1], [1, 0.18]);
  const translateX = interpolate(handoff, [0, 1], [0, -780]);
  const translateY = interpolate(handoff, [0, 1], [0, -480]);

  return (
    <AbsoluteFill style={{ background: colors.bgBase }}>
      <AbsoluteFill
        style={{
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 32,
            transform: `translate(${translateX}px, ${translateY}px) scale(${scale})`,
            transformOrigin: "center center",
          }}
        >
          <PivotLogo size={180} wordmark wordmarkSize={140} />

          <div
            style={{
              opacity: taglineOpacity * (1 - handoff),
              transform: `translateY(${taglineY}px)`,
              color: colors.textSecondary,
              fontFamily: fontUi,
              fontSize: 22,
              fontWeight: 400,
              letterSpacing: "0.02em",
            }}
          >
            Trading, instructed in plain English.
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
