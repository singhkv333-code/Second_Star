import { useCurrentFrame, interpolate, Easing } from "remotion";
import { colors } from "../theme";
import { fontSerif } from "../fonts";

// Pivot brand mark: 4 ascending bars (relative heights 0.38, 0.55, 0.78, 1.0)
// rising on a shared baseline. When `form` is provided we use it directly;
// otherwise the bars stagger in over a default window using the current frame.

export const BARS = [
  { h: 0.38, delay: 0 },
  { h: 0.55, delay: 6 },
  { h: 0.78, delay: 12 },
  { h: 1.0, delay: 18 },
] as const;

type Props = {
  // Glyph size in px (bars + gaps fit inside `size` square).
  size: number;
  // Override the natural frame-driven entrance with an explicit 0..1 progress.
  form?: number;
  // Show the "pivot" wordmark to the right of the bars.
  wordmark?: boolean;
  // Wordmark size; if omitted scales with `size`.
  wordmarkSize?: number;
  color?: string;
  wordmarkColor?: string;
};

export const PivotLogo: React.FC<Props> = ({
  size,
  form,
  wordmark = false,
  wordmarkSize,
  color = colors.textPrimary,
  wordmarkColor,
}) => {
  const frame = useCurrentFrame();

  // Bar widths/gaps replicate /public/pivot-icon.png proportions.
  const barCount = BARS.length;
  const gapRatio = 0.42;
  const unitW = size / (barCount + (barCount - 1) * gapRatio);
  const barW = unitW;
  const gap = unitW * gapRatio;

  // Each bar's progress: 0 → fully grown.
  const baseline = size * 0.95;

  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: size * 0.16,
      }}
    >
      <div
        style={{
          width: size,
          height: size,
          position: "relative",
        }}
      >
        {BARS.map((bar, i) => {
          const p =
            form !== undefined
              ? // shared form progress, but bars still stagger by 0.12 each
                interpolate(
                  form,
                  [i * 0.12, 0.5 + i * 0.12],
                  [0, 1],
                  {
                    extrapolateLeft: "clamp",
                    extrapolateRight: "clamp",
                    easing: Easing.bezier(0.16, 1, 0.3, 1),
                  },
                )
              : interpolate(
                  frame,
                  [bar.delay, bar.delay + 24],
                  [0, 1],
                  {
                    extrapolateLeft: "clamp",
                    extrapolateRight: "clamp",
                    easing: Easing.bezier(0.16, 1, 0.3, 1),
                  },
                );

          const fullH = size * bar.h * 0.78;
          const h = fullH * p;
          const x = i * (barW + gap);
          const y = baseline - h;

          return (
            <div
              key={i}
              style={{
                position: "absolute",
                left: x,
                top: y,
                width: barW,
                height: h,
                background: color,
                borderRadius: 2,
                opacity: p,
              }}
            />
          );
        })}
      </div>

      {wordmark && (
        <Wordmark
          size={wordmarkSize ?? size * 0.92}
          color={wordmarkColor ?? color}
          // wordmark appears after the bars finish forming
          delay={form !== undefined ? undefined : 30}
          form={form}
        />
      )}
    </div>
  );
};

const Wordmark: React.FC<{
  size: number;
  color: string;
  delay?: number;
  form?: number;
}> = ({ size, color, delay, form }) => {
  const frame = useCurrentFrame();
  const local = delay !== undefined ? frame - delay : 0;

  const opacity =
    form !== undefined
      ? interpolate(form, [0.55, 0.95], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      : interpolate(local, [0, 18], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: Easing.bezier(0.16, 1, 0.3, 1),
        });
  const x =
    form !== undefined
      ? interpolate(form, [0.55, 0.95], [-12, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      : interpolate(local, [0, 18], [-12, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: Easing.bezier(0.16, 1, 0.3, 1),
        });

  return (
    <span
      style={{
        fontFamily: fontSerif,
        fontWeight: 550,
        fontSize: size,
        letterSpacing: "-0.04em",
        lineHeight: 1,
        color,
        opacity,
        transform: `translateX(${x}px)`,
        marginLeft: -size * 0.04,
      }}
    >
      pivot
    </span>
  );
};
