import { useCurrentFrame, interpolate, Easing } from "remotion";
import { colors } from "../theme";
import { fontUi } from "../fonts";

// WittyTicker — three bouncing bars + a swapping phrase. Mirrors
// pivot-next's chat thinking state (witty-bar + witty-phrase).

const PHRASES = [
  "Pulling up RELIANCE…",
  "Checking last close…",
  "Drawing the 1Y sparkline…",
];

type Props = {
  // Frame at which the indicator enters (scene-local)
  enterAt?: number;
  // Frame at which the indicator should start fading out (scene-local)
  exitAt?: number;
};

export const Thinking: React.FC<Props> = ({ enterAt = 0, exitAt }) => {
  const frame = useCurrentFrame();

  const enter = interpolate(frame, [enterAt, enterAt + 14], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const exit =
    exitAt !== undefined
      ? interpolate(frame, [exitAt, exitAt + 12], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: Easing.bezier(0.4, 0, 0.2, 1),
        })
      : 0;
  const opacity = enter * (1 - exit);

  // Phrase cycles every ~36 frames (1.2 s)
  const cycle = Math.max(0, frame - enterAt);
  const phraseIndex = Math.floor(cycle / 36) % PHRASES.length;
  const phrase = PHRASES[phraseIndex];

  // Bar heights — three out-of-phase sines so they read as a tiny chart.
  const bar = (seed: number, period: number): number => {
    const v = Math.sin(((frame + seed) / period) * Math.PI * 2);
    return 0.4 + ((v + 1) / 2) * 0.6; // 0.4 → 1.0
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "6px 4px",
        opacity,
      }}
    >
      {/* Three bouncing bars */}
      <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 16 }}>
        {[
          bar(0, 18),
          bar(6, 22),
          bar(11, 16),
        ].map((h, i) => (
          <div
            key={i}
            style={{
              width: 3,
              height: 14 * h,
              borderRadius: 1.5,
              background: colors.textSecondary,
            }}
          />
        ))}
      </div>
      <span
        key={phraseIndex}
        style={{
          fontFamily: fontUi,
          fontSize: 14,
          color: colors.textSecondary,
        }}
      >
        {phrase}
      </span>
    </div>
  );
};
