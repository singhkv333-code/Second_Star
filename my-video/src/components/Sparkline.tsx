import { useCurrentFrame, interpolate, Easing } from "remotion";

// Generates a smooth pseudo-random series rising over `n` ticks with mild noise.
// Deterministic given the same seed so the line stays identical across frames.
const seriesFor = (
  n: number,
  seed: number,
  trend: number,
  amplitude: number,
): number[] => {
  const out: number[] = [];
  let v = 100;
  let rng = seed;
  for (let i = 0; i < n; i++) {
    // mulberry32
    rng |= 0;
    rng = (rng + 0x6d2b79f5) | 0;
    let t = Math.imul(rng ^ (rng >>> 15), 1 | rng);
    t = t + Math.imul(t ^ (t >>> 7), 61 | t) ^ t;
    const r = ((t ^ (t >>> 14)) >>> 0) / 4294967296 - 0.5;
    v += trend + r * amplitude;
    out.push(v);
  }
  return out;
};

type Props = {
  width: number;
  height: number;
  positive?: boolean;
  // 0..1 portion of the line revealed left → right
  reveal?: number;
  // when given, overrides the internal data
  data?: number[];
};

export const Sparkline: React.FC<Props> = ({
  width,
  height,
  positive = true,
  reveal,
  data,
}) => {
  const frame = useCurrentFrame();

  const points = data ?? seriesFor(64, 42, positive ? 0.4 : -0.4, 2.4);
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;

  const padX = 2;
  const padY = 6;
  const xs = points.map(
    (_, i) => padX + (i / (points.length - 1)) * (width - padX * 2),
  );
  const ys = points.map(
    (v) => height - padY - ((v - min) / range) * (height - padY * 2),
  );

  const linePath = xs.map((x, i) => `${i === 0 ? "M" : "L"}${x},${ys[i]}`).join(" ");
  const areaPath =
    `M${xs[0]},${height} ` +
    xs.map((x, i) => `L${x},${ys[i]}`).join(" ") +
    ` L${xs[xs.length - 1]},${height} Z`;

  const color = positive ? "#10b981" : "#ef4444";
  const gradId = `spark-grad-${positive ? "up" : "dn"}`;

  // Reveal: animate stroke-dasharray. We need the path length; use width
  // as a conservative upper bound so dash sweeps fully across.
  const totalLen = width * 2.2;
  const r =
    reveal !== undefined
      ? Math.max(0, Math.min(1, reveal))
      : interpolate(frame, [0, 40], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: Easing.bezier(0.16, 1, 0.3, 1),
        });

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ display: "block" }}
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.22} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
        <clipPath id={`reveal-${positive ? "up" : "dn"}`}>
          <rect x="0" y="0" width={width * r} height={height} />
        </clipPath>
      </defs>
      <g clipPath={`url(#reveal-${positive ? "up" : "dn"})`}>
        <path d={areaPath} fill={`url(#${gradId})`} />
        <path
          d={linePath}
          stroke={color}
          strokeWidth={1.6}
          fill="none"
          strokeLinejoin="round"
          strokeLinecap="round"
          strokeDasharray={totalLen}
          strokeDashoffset={0}
        />
      </g>
    </svg>
  );
};
