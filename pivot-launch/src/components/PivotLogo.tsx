import { useCurrentFrame, spring, interpolate, useVideoConfig, Easing } from "remotion";
import { theme, spring as springCfg } from "../theme";
import { fontSans } from "../fonts";

// Pivot's mark: a four-bar equalizer with irregular heights, followed by
// the wordmark "pivot". Bars grow sequentially (spring), wordmark fades
// and slides in once the last bar is forming.

const BAR_HEIGHTS = [14, 22, 10, 18] as const;
const BAR_WIDTH = 3;
const BAR_GAP = 3;

type Props = {
  // Final draw size multiplier. 1× = 22px tall (tallest bar) and 32px wordmark.
  scale?: number;
  // Override the natural frame-driven entrance. 0..1 progress.
  form?: number;
  color?: string;
};

export const PivotLogo: React.FC<Props> = ({
  scale = 1,
  form,
  color = theme.ink,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Frame anchors per spec:
  //   bar1: 0-6, bar2: 4-10, bar3: 8-14, bar4: 12-18, wordmark: 18-28
  const barStarts = [0, 4, 8, 12] as const;
  const wordmarkStart = 18;
  const wordmarkEnd = 28;

  const barProgress = (i: number): number => {
    if (form !== undefined) {
      // Distribute bars across the 0..0.7 form progress range, with stagger.
      const start = i * 0.12;
      const end = start + 0.28;
      return interpolate(form, [start, end], [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
        easing: Easing.bezier(0.16, 1, 0.3, 1),
      });
    }
    const s = spring({
      frame: frame - barStarts[i],
      fps,
      config: springCfg,
      durationInFrames: 12,
    });
    return s;
  };

  const wordmarkProgress =
    form !== undefined
      ? interpolate(form, [0.6, 1], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: Easing.bezier(0.16, 1, 0.3, 1),
        })
      : interpolate(frame, [wordmarkStart, wordmarkEnd], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: Easing.bezier(0.16, 1, 0.3, 1),
        });

  // Tallest bar defines glyph height. Spacing then fits left to right.
  const maxBarHeight = Math.max(...BAR_HEIGHTS);
  const glyphWidth =
    BAR_HEIGHTS.length * BAR_WIDTH + (BAR_HEIGHTS.length - 1) * BAR_GAP;

  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "flex-end",
        gap: 12,
        transform: `scale(${scale})`,
        // Scale around the visual center so the parent's flex-center
        // works correctly — the layout box stays small but the visual
        // expands symmetrically from its midpoint.
        transformOrigin: "center center",
        // Reserve baseline so scaling doesn't move the bottom edge.
        height: maxBarHeight,
      }}
    >
      {/* Bars */}
      <div
        style={{
          width: glyphWidth,
          height: maxBarHeight,
          position: "relative",
          display: "flex",
          alignItems: "flex-end",
          gap: BAR_GAP,
        }}
      >
        {BAR_HEIGHTS.map((h, i) => {
          const p = barProgress(i);
          return (
            <div
              key={i}
              style={{
                width: BAR_WIDTH,
                height: h * p,
                background: color,
                borderRadius: 1,
              }}
            />
          );
        })}
      </div>

      {/* Wordmark */}
      <span
        style={{
          fontFamily: fontSans,
          fontWeight: 600,
          fontSize: 32,
          color,
          letterSpacing: "-0.5px",
          lineHeight: 1,
          opacity: wordmarkProgress,
          transform: `translateX(${(1 - wordmarkProgress) * 8}px)`,
          // Align baseline visually with the tallest bar.
          display: "inline-block",
          marginBottom: -6,
        }}
      >
        pivot
      </span>
    </div>
  );
};
