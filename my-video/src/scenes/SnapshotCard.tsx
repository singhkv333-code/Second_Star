import { useCurrentFrame, interpolate, Easing } from "remotion";
import { colors, radii } from "../theme";
import { fontUi, fontMono } from "../fonts";
import { Sparkline } from "../components/Sparkline";

// Replicates pivot-next's StockSnapshotCard for RELIANCE. Sections stagger
// in: header → sparkline reveal → range chips → stat grid → action bar.

type Props = {
  // Scene-local frame at which the card begins entering
  enterAt?: number;
  width?: number;
};

const RANGES = ["1D", "1W", "1M", "6M", "1Y", "5Y"];
const ACTIVE_RANGE = "1Y";

export const SnapshotCard: React.FC<Props> = ({ enterAt = 0, width = 640 }) => {
  const frame = useCurrentFrame();

  // Card-level fade-in / lift
  const cardT = interpolate(frame, [enterAt, enterAt + 18], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  // Section reveal stagger (relative to card enter)
  const sec = (offset: number, dur = 14): number =>
    interpolate(frame, [enterAt + offset, enterAt + offset + dur], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.bezier(0.16, 1, 0.3, 1),
    });

  const headerT = sec(8);
  const sparkRevealT = sec(20, 28);
  const chipsT = sec(36);
  const stat1T = sec(44);
  const stat2T = sec(52);
  const actionT = sec(60);

  // Live dot pulse
  const livePulse = 0.6 + 0.4 * (Math.sin(frame / 12) * 0.5 + 0.5);

  // Counter: animate the change percentage briefly
  const changePct = interpolate(frame, [enterAt + 14, enterAt + 36], [0, 1.85], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });
  const changeAbs = interpolate(frame, [enterAt + 14, enterAt + 36], [0, 26.05], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });
  const ltp = interpolate(frame, [enterAt + 14, enterAt + 40], [1406.45, 1432.5], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });

  return (
    <div
      style={{
        width,
        background: colors.bgCard,
        borderRadius: radii.lg,
        border: `1px solid ${colors.border}`,
        boxShadow: "0 30px 80px rgba(0,0,0,0.45), 0 6px 16px rgba(0,0,0,0.3)",
        opacity: cardT,
        transform: `translateY(${(1 - cardT) * 12}px)`,
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "20px 22px 14px",
          display: "flex",
          gap: 16,
          alignItems: "flex-start",
          justifyContent: "space-between",
          opacity: headerT,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <span
            style={{
              fontFamily: fontUi,
              fontSize: 10,
              fontWeight: 500,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              color: colors.textTertiary,
            }}
          >
            NSE · Energy
          </span>
          <h3
            style={{
              margin: "6px 0 2px",
              fontFamily: fontUi,
              fontSize: 19,
              fontWeight: 600,
              letterSpacing: "-0.01em",
              color: colors.textPrimary,
            }}
          >
            Reliance Industries
          </h3>
          <p
            style={{
              margin: 0,
              fontFamily: fontMono,
              fontSize: 10.5,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              color: colors.textTertiary,
            }}
          >
            RELIANCE
          </p>
        </div>

        <div style={{ textAlign: "right" }}>
          <p
            style={{
              margin: 0,
              fontFamily: fontUi,
              fontSize: 24,
              fontWeight: 600,
              letterSpacing: "-0.01em",
              fontVariantNumeric: "tabular-nums",
              color: colors.textPrimary,
            }}
          >
            ₹{ltp.toFixed(2)}
          </p>
          <div
            style={{
              marginTop: 4,
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              color: colors.profit,
              fontFamily: fontMono,
              fontSize: 13,
              fontWeight: 600,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            <TrendUpIcon />
            +₹{changeAbs.toFixed(2)} (+{changePct.toFixed(2)}%)
          </div>
          <div
            style={{
              marginTop: 6,
              fontFamily: fontUi,
              fontSize: 10.5,
              color: colors.textTertiary,
            }}
          >
            3:25 PM IST
          </div>
          <div
            style={{
              marginTop: 6,
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "2px 8px",
              borderRadius: radii.sm,
              background: "rgba(16, 185, 129, 0.12)",
              fontFamily: fontUi,
              fontSize: 10,
              fontWeight: 600,
              color: colors.profit,
              letterSpacing: "0.02em",
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: 999,
                background: colors.profit,
                opacity: livePulse,
                boxShadow: `0 0 0 ${livePulse * 3}px rgba(16, 185, 129, 0.18)`,
              }}
            />
            Live
          </div>
        </div>
      </div>

      {/* Sparkline */}
      <div style={{ padding: "0 22px" }}>
        <div style={{ height: 110 }}>
          <Sparkline
            width={width - 44}
            height={110}
            positive
            reveal={sparkRevealT}
          />
        </div>

        {/* Range chips */}
        <div
          style={{
            marginTop: 10,
            display: "grid",
            gridTemplateColumns: `repeat(${RANGES.length}, 1fr)`,
            gap: 6,
            paddingBottom: 16,
            opacity: chipsT,
          }}
        >
          {RANGES.map((r) => {
            const active = r === ACTIVE_RANGE;
            return (
              <div
                key={r}
                style={{
                  padding: "7px 0",
                  borderRadius: radii.sm,
                  textAlign: "center",
                  fontFamily: fontUi,
                  fontSize: 11.5,
                  fontWeight: 500,
                  letterSpacing: "0.02em",
                  background: active ? colors.textPrimary : "transparent",
                  color: active ? colors.bgBase : colors.textSecondary,
                }}
              >
                {r}
              </div>
            );
          })}
        </div>
      </div>

      {/* Stat grid row 1 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          borderTop: `1px solid ${colors.border}`,
          opacity: stat1T,
        }}
      >
        <StatCell label="Open" value="₹1,408.20" />
        <StatCell label="High" value="₹1,438.75" />
        <StatCell label="Low" value="₹1,401.10" />
        <StatCell label="Volume" value="12,84,302" last />
      </div>

      {/* Stat grid row 2 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          borderTop: `1px solid ${colors.border}`,
          opacity: stat2T,
        }}
      >
        <StatCell label="52w high" value="₹1,608.95" />
        <StatCell label="52w low" value="₹1,202.40" />
        <StatCell label="Mkt cap" value="₹19.68T" />
        <StatCell label="P/E" value="24.6" last />
      </div>

      {/* Action bar */}
      <div
        style={{
          display: "flex",
          gap: 8,
          padding: "14px 16px",
          borderTop: `1px solid ${colors.border}`,
          opacity: actionT,
          transform: `translateY(${(1 - actionT) * 6}px)`,
        }}
      >
        <button
          style={{
            flex: 1,
            height: 38,
            borderRadius: radii.pill,
            background: colors.textPrimary,
            color: colors.bgBase,
            border: "none",
            fontFamily: fontUi,
            fontWeight: 600,
            fontSize: 13,
          }}
        >
          Buy
        </button>
        <button
          style={{
            flex: 1,
            height: 38,
            borderRadius: radii.pill,
            background: "transparent",
            color: colors.textPrimary,
            border: `1px solid ${colors.borderHover}`,
            fontFamily: fontUi,
            fontWeight: 500,
            fontSize: 13,
          }}
        >
          Sell
        </button>
        <button
          style={{
            width: 44,
            height: 38,
            borderRadius: radii.pill,
            background: "transparent",
            color: colors.textTertiary,
            border: "none",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <BookmarkIcon />
        </button>
      </div>
    </div>
  );
};

const StatCell: React.FC<{ label: string; value: string; last?: boolean }> = ({
  label,
  value,
  last,
}) => (
  <div
    style={{
      padding: "12px 14px",
      borderRight: last ? "none" : `1px solid ${colors.border}`,
      display: "flex",
      flexDirection: "column",
      gap: 3,
    }}
  >
    <span
      style={{
        fontSize: 9.5,
        fontFamily: fontUi,
        fontWeight: 500,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        color: colors.textTertiary,
      }}
    >
      {label}
    </span>
    <span
      style={{
        fontFamily: fontMono,
        fontSize: 12.5,
        fontWeight: 500,
        color: colors.textPrimary,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {value}
    </span>
  </div>
);

const TrendUpIcon: React.FC = () => (
  <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
    <polyline points="17 6 23 6 23 12" />
  </svg>
);

const BookmarkIcon: React.FC = () => (
  <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
    <line x1="12" y1="7" x2="12" y2="13" />
    <line x1="9" y1="10" x2="15" y2="10" />
  </svg>
);
