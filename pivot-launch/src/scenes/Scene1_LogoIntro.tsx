import { AbsoluteFill, useCurrentFrame, interpolate, Easing } from "remotion";
import { theme } from "../theme";
import { PivotLogo } from "../components/PivotLogo";

// Frames 0-90 (3s).
//   0-28   bars draw + wordmark slides in (handled inside PivotLogo).
//   28-75  hold static.
//   75-90  scale down 2.5x → 1x and shift upward by 200px (handoff).
export const Scene1_LogoIntro: React.FC = () => {
  const frame = useCurrentFrame();

  // Hero scale lands at 6× (≈ 130 px tall bars, 192 px wordmark) — the
  // outro is 3× so 6/3 keeps the spec's 2:1 hero-to-outro ratio.
  const HERO_SCALE = 6;
  const OUTRO_SCALE = 3;
  const handoff = interpolate(frame, [75, 90], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });
  const scale = interpolate(handoff, [0, 1], [HERO_SCALE, OUTRO_SCALE]);
  const ty = interpolate(handoff, [0, 1], [0, -200]);

  return (
    <AbsoluteFill
      style={{
        background: theme.cream,
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          transform: `translateY(${ty}px)`,
          // Center the scaled logo manually since transformOrigin scaling
          // from left-bottom would shift it off-axis.
          display: "inline-flex",
        }}
      >
        <PivotLogo scale={scale} />
      </div>
    </AbsoluteFill>
  );
};
