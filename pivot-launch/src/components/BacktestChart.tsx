import { theme } from "../theme";

type Props = {
  width: number;
  height: number;
  // 0..1 — how much of the line is drawn (left-to-right)
  reveal: number;
};

// Spec path: M 0 150 L 50 145 L 100 140 L 150 138 L 200 128 L 250 132 L 300 120 L 350 105 L 400 95
// We normalize to width/height so it can scale freely.
const SPEC = [
  [0, 150],
  [50, 145],
  [100, 140],
  [150, 138],
  [200, 128],
  [250, 132],
  [300, 120],
  [350, 105],
  [400, 95],
] as const;

const SPEC_W = 400;
const SPEC_H = 160;

export const BacktestChart: React.FC<Props> = ({ width, height, reveal }) => {
  const xs = SPEC.map(([x]) => (x / SPEC_W) * width);
  const ys = SPEC.map(([, y]) => (y / SPEC_H) * height);

  const linePath = xs.map((x, i) => `${i === 0 ? "M" : "L"} ${x} ${ys[i]}`).join(" ");
  const areaPath =
    `M ${xs[0]} ${height} ` +
    xs.map((x, i) => `L ${x} ${ys[i]}`).join(" ") +
    ` L ${xs[xs.length - 1]} ${height} Z`;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ display: "block", overflow: "visible" }}
    >
      <defs>
        <linearGradient id="bt-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={theme.green} stopOpacity={0.18} />
          <stop offset="100%" stopColor={theme.green} stopOpacity={0} />
        </linearGradient>
        <clipPath id="bt-reveal">
          <rect x="0" y="0" width={Math.max(1, reveal * width)} height={height} />
        </clipPath>
      </defs>

      {/* baseline gridlines */}
      {[0.25, 0.5, 0.75].map((p) => (
        <line
          key={p}
          x1={0}
          y1={height * p}
          x2={width}
          y2={height * p}
          stroke={theme.border}
          strokeWidth={1}
          strokeDasharray="3 5"
        />
      ))}

      <g clipPath="url(#bt-reveal)">
        <path d={areaPath} fill="url(#bt-fill)" />
        <path
          d={linePath}
          fill="none"
          stroke={theme.green}
          strokeWidth={2.4}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {/* tip dot */}
        {reveal > 0.95 && (
          <circle
            cx={xs[xs.length - 1]}
            cy={ys[ys.length - 1]}
            r={5}
            fill={theme.green}
            stroke={theme.white}
            strokeWidth={2}
          />
        )}
      </g>
    </svg>
  );
};
