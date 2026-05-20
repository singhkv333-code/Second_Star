import { AbsoluteFill, useCurrentFrame, interpolate, Easing } from "remotion";
import { X } from "lucide-react";
import { theme } from "../theme";
import { fontSans } from "../fonts";
import { AnimatedCursor } from "../components/AnimatedCursor";
import { BacktestChart } from "../components/BacktestChart";
import { BACKTEST_STATS } from "../mock";

// Scene 5 — frames 420-540 (local 0-120, 4s)
//   0-20    Cursor moves toward the Backtest button — handled visually
//           by showing a faint underlying card backdrop. The button
//           highlight + green glow appear at 18.
//   20-40   Side panel slides in from right (width 600px).
//   40-90   Backtest line draws across.
//   90-110  3 stat tiles fade in sequentially.
//   110-120 Cursor moves to Activate, click ripple flashes.
export const Scene5_AgentBacktest: React.FC = () => {
  const frame = useCurrentFrame();

  // Underlying backdrop fade (we keep just a soft cream wash so the
  // panel doesn't sit on a black void)
  const backdropDim = interpolate(frame, [20, 40], [0, 0.35], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Side panel slide-in
  const panelT = interpolate(frame, [20, 40], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.22, 1, 0.36, 1),
  });
  const panelX = interpolate(panelT, [0, 1], [620, 0]);

  // Chart line draw
  const lineReveal = interpolate(frame, [40, 90], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });

  // Stat tile reveals (staggered)
  const tile1 = interpolate(frame, [88, 100], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.bezier(0.16, 1, 0.3, 1) });
  const tile2 = interpolate(frame, [94, 106], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.bezier(0.16, 1, 0.3, 1) });
  const tile3 = interpolate(frame, [100, 112], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.bezier(0.16, 1, 0.3, 1) });

  // Activate button pulse
  const activatePulse = interpolate(
    frame,
    [100, 108, 116, 120],
    [0, 0.7, 1, 0],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    },
  );

  // Cursor path: starts off-panel near where the Backtest button sat
  // (frame 0-18), then floats over the Activate area inside the panel
  // by frame 100. Click ripple at frame 112.
  const cursorXBefore = interpolate(frame, [0, 18], [900, 940], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const cursorYBefore = 720;

  const cursorXAfter = interpolate(frame, [60, 112], [1280, 1620], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });
  const cursorYAfter = interpolate(frame, [60, 112], [720, 940], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });
  const useAfter = frame >= 40;
  const cursorX = useAfter ? cursorXAfter : cursorXBefore;
  const cursorY = useAfter ? cursorYAfter : cursorYBefore;

  const click = interpolate(frame, [112, 120], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ background: theme.cream }}>
      {/* Soft backdrop dim layer */}
      <AbsoluteFill
        style={{
          background: `rgba(0,0,0,${backdropDim})`,
        }}
      />

      {/* Side panel */}
      <div
        style={{
          position: "absolute",
          top: 0,
          right: 0,
          bottom: 0,
          width: 620,
          background: theme.white,
          borderLeft: `1px solid ${theme.border}`,
          boxShadow: "-12px 0 40px rgba(0,0,0,0.06)",
          transform: `translateX(${panelX}px)`,
          padding: "28px 32px",
          display: "flex",
          flexDirection: "column",
          gap: 22,
          fontFamily: fontSans,
        }}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <div style={{ fontSize: 11, color: theme.gray, fontWeight: 500, letterSpacing: "0.08em", textTransform: "uppercase" }}>
              Agent Editor
            </div>
            <h2 style={{ margin: "4px 0 0", fontSize: 22, fontWeight: 600, color: theme.ink, letterSpacing: "-0.01em" }}>
              RELIANCE Weekday Dip-Buy
            </h2>
          </div>
          <button
            style={{
              width: 36,
              height: 36,
              borderRadius: 10,
              border: "none",
              background: "transparent",
              color: theme.gray,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              cursor: "pointer",
            }}
          >
            <X size={20} />
          </button>
        </div>

        {/* Tab row */}
        <div
          style={{
            display: "flex",
            gap: 0,
            borderBottom: `1px solid ${theme.border}`,
          }}
        >
          {["Strategy", "Backtest", "Code", "Logs"].map((t, i) => {
            const active = i === 1;
            return (
              <div
                key={t}
                style={{
                  padding: "10px 16px",
                  marginBottom: -1,
                  borderBottom: active ? `2px solid ${theme.green}` : "2px solid transparent",
                  color: active ? theme.ink : theme.gray,
                  fontSize: 13,
                  fontWeight: active ? 600 : 500,
                }}
              >
                {t}
              </div>
            );
          })}
        </div>

        {/* Chart label */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <div>
            <div style={{ fontSize: 11, color: theme.gray, fontWeight: 500, letterSpacing: "0.08em", textTransform: "uppercase" }}>Portfolio value · 6 months</div>
            <div style={{ marginTop: 2, fontSize: 22, fontWeight: 600, color: theme.ink, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.01em" }}>
              ₹1,12,400
            </div>
          </div>
          <div style={{ fontSize: 13, fontWeight: 600, color: theme.positive, fontVariantNumeric: "tabular-nums" }}>
            +{BACKTEST_STATS.returnPct}
          </div>
        </div>

        {/* Backtest chart */}
        <div style={{ background: theme.white }}>
          <BacktestChart width={556} height={200} reveal={lineReveal} />
        </div>

        {/* Stat tiles */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
          <StatTile
            label="Return"
            value={BACKTEST_STATS.returnPct}
            valueColor={theme.positive}
            big
            opacity={tile1}
          />
          <StatTile label="Win rate" value={BACKTEST_STATS.winRate} opacity={tile2} />
          <StatTile
            label="Max drawdown"
            value={BACKTEST_STATS.maxDrawdown}
            valueColor={theme.negative}
            opacity={tile3}
          />
        </div>

        <div style={{ flex: 1 }} />

        {/* Activate */}
        <button
          style={{
            height: 48,
            borderRadius: 12,
            border: "none",
            background: theme.green,
            color: theme.white,
            fontFamily: fontSans,
            fontWeight: 600,
            fontSize: 14,
            transform: `scale(${1 + activatePulse * 0.04})`,
            boxShadow: activatePulse > 0 ? `0 0 0 ${10 * activatePulse}px ${theme.greenGlow}` : "none",
            cursor: "pointer",
          }}
        >
          Activate Agent
        </button>
      </div>

      {/* Faint underlying card hint when the cursor is "outside" the panel */}
      {frame < 40 && (
        <div
          style={{
            position: "absolute",
            top: 240,
            left: 360,
            width: 520,
            height: 320,
            borderRadius: 16,
            border: `1px solid ${theme.border}`,
            background: theme.white,
            opacity: 1 - interpolate(frame, [20, 40], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
            boxShadow: theme.shadowSm,
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            padding: 24,
            fontFamily: fontSans,
          }}
        >
          <div>
            <div style={{ fontSize: 11, color: theme.gray, letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500 }}>
              Strategy Agent · Draft
            </div>
            <div style={{ fontSize: 22, fontWeight: 600, color: theme.ink, marginTop: 6, letterSpacing: "-0.01em" }}>
              RELIANCE Weekday Dip-Buy
            </div>
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <div
              style={{
                flex: 1,
                height: 42,
                borderRadius: 10,
                border: `1px solid ${frame > 14 ? theme.green : theme.border}`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 14,
                color: theme.ink,
                fontWeight: 500,
                boxShadow: frame > 14 ? `0 0 0 4px ${theme.greenGlow}` : "none",
              }}
            >
              Backtest
            </div>
            <div
              style={{
                flex: 1,
                height: 42,
                borderRadius: 10,
                background: theme.green,
                color: theme.white,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 14,
                fontWeight: 500,
              }}
            >
              Activate
            </div>
          </div>
        </div>
      )}

      {/* Cursor */}
      <AnimatedCursor x={cursorX} y={cursorY} click={click > 0 ? click : 0} />
    </AbsoluteFill>
  );
};

const StatTile: React.FC<{
  label: string;
  value: string;
  valueColor?: string;
  big?: boolean;
  opacity: number;
}> = ({ label, value, valueColor = theme.ink, big = false, opacity }) => (
  <div
    style={{
      padding: "16px 18px",
      borderRadius: 14,
      border: `1px solid ${theme.border}`,
      background: theme.cream,
      opacity,
      transform: `translateY(${(1 - opacity) * 8}px)`,
      fontFamily: fontSans,
    }}
  >
    <div style={{ fontSize: 11, color: theme.gray, fontWeight: 500, letterSpacing: "0.08em", textTransform: "uppercase" }}>
      {label}
    </div>
    <div
      style={{
        marginTop: 6,
        fontSize: big ? 28 : 22,
        fontWeight: 600,
        color: valueColor,
        fontVariantNumeric: "tabular-nums",
        letterSpacing: "-0.01em",
      }}
    >
      {value}
    </div>
  </div>
);
