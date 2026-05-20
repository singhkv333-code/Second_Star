import { useCurrentFrame, interpolate, Easing } from "remotion";
import { colors, radii } from "../theme";
import { fontUi, fontMono, fontSerif } from "../fonts";

// Index strip + serif greeting + quick-action chips, mirroring
// pivot-next/components/DashboardTab.tsx's empty-state intro.

const INDICES = [
  { name: "NIFTY 50", value: "22,914.30", change: "+0.42%", positive: true },
  { name: "SENSEX", value: "75,418.04", change: "+0.31%", positive: true },
  { name: "BANK NIFTY", value: "48,201.65", change: "-0.18%", positive: false },
  { name: "INDIA VIX", value: "13.92", change: "-2.10%", positive: false },
];

const CHIPS = [
  { label: "Generate Report" },
  { label: "Run Agent" },
  { label: "Portfolio Health" },
  { label: "Market Pulse" },
  { label: "Top Movers" },
  { label: "Agents Calendar" },
  { label: "News-gated trade" },
];

type Props = {
  // 0..1 overall exit progress so this can fade as the user submits
  exitProgress?: number;
  // Stable greeting time used in copy
  greeting?: string;
  name?: string;
};

export const ChatGreeting: React.FC<Props> = ({
  exitProgress = 0,
  greeting = "Good morning",
  name = "Karanveer",
}) => {
  const frame = useCurrentFrame();

  const inOpacity = interpolate(frame, [0, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const inY = interpolate(frame, [0, 20], [12, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const opacity = inOpacity * (1 - exitProgress);

  // Stagger chips
  const chipDelay = 12;
  const chipStep = 3;

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        padding: "32px 56px 0",
        opacity,
        transform: `translateY(${inY - exitProgress * 18}px)`,
      }}
    >
      {/* Index strip */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 12,
          marginBottom: 60,
        }}
      >
        {INDICES.map((i) => (
          <div
            key={i.name}
            style={{
              padding: "14px 18px",
              borderRadius: radii.md,
              background: colors.bgCard,
              border: `1px solid ${colors.border}`,
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            <span
              style={{
                fontSize: 11,
                fontFamily: fontUi,
                fontWeight: 500,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                color: colors.textTertiary,
              }}
            >
              {i.name}
            </span>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              <span
                style={{
                  fontFamily: fontMono,
                  fontSize: 18,
                  fontWeight: 600,
                  color: colors.textPrimary,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {i.value}
              </span>
              <span
                style={{
                  fontFamily: fontMono,
                  fontSize: 11.5,
                  fontWeight: 600,
                  color: i.positive ? colors.profit : colors.loss,
                  // Soft-pill chip to match real Pivot UI
                  background: i.positive
                    ? "rgba(16, 185, 129, 0.15)"
                    : "rgba(239, 68, 68, 0.15)",
                  padding: "2px 8px",
                  borderRadius: radii.sm,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {i.change}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Greeting + chips center cluster */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 36,
          paddingBottom: 240,
        }}
      >
        <h1
          style={{
            fontFamily: fontSerif,
            fontWeight: 550,
            fontSize: 56,
            letterSpacing: "-0.04em",
            lineHeight: 1.05,
            color: colors.textPrimary,
            margin: 0,
            textAlign: "center",
          }}
        >
          {greeting}, {name}!
        </h1>

        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: 10, maxWidth: 1020 }}>
          {CHIPS.map((c, i) => {
            const t = interpolate(
              frame,
              [chipDelay + i * chipStep, chipDelay + i * chipStep + 14],
              [0, 1],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.bezier(0.16, 1, 0.3, 1),
              },
            );
            return (
              <div
                key={c.label}
                style={{
                  opacity: t,
                  transform: `translateY(${(1 - t) * 8}px)`,
                  padding: "10px 16px",
                  borderRadius: radii.pill,
                  background: colors.bgBase,
                  border: `1px solid ${colors.border}`,
                  color: colors.textSecondary,
                  fontFamily: fontUi,
                  fontSize: 13,
                  fontWeight: 500,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <ChipDot />
                {c.label}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

const ChipDot: React.FC = () => (
  <span
    style={{
      display: "inline-block",
      width: 12,
      height: 12,
      borderRadius: 3,
      background: "linear-gradient(135deg, #6366f1 0%, #38bdf8 100%)",
    }}
  />
);
