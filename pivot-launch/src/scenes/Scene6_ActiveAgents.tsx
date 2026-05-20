import { AbsoluteFill, useCurrentFrame, interpolate, Easing } from "remotion";
import { theme } from "../theme";
import { fontSans } from "../fonts";
import { ActiveAgentItem } from "../components/ActiveAgentItem";
import { EXISTING_AGENTS } from "../mock";

// Scene 6 — frames 540-630 (local 0-90, 3s)
//   0-15    Panel closes (slide right out — handled by Scene 5 fadeout
//           in the master, so here we just open on the right rail).
//           Camera "pans" to the right sidebar — represented by
//           positioning the rail near screen center and scaling it up.
//   15-45   New agent card slides in at top of list.
//   45-75   Counter ticks 3 → 4.
//   75-90   Gentle zoom-out to prep for outro.
export const Scene6_ActiveAgents: React.FC = () => {
  const frame = useCurrentFrame();

  // Camera: at the start (frame 0), we should still have the panel
  // visually "closing" — for that the master plays Scene 5 with a
  // tail-out. Here we just present the right rail centered and a touch
  // larger than its app-shell size so the viewer reads it as a zoom.
  const railScale = interpolate(frame, [0, 30], [1.0, 1.15], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const railScaleOut = interpolate(frame, [75, 90], [1.15, 1.05], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });
  const finalScale = frame < 75 ? railScale : railScaleOut;

  // Dim wash that brightens slightly toward the end (prepping outro)
  const dim = interpolate(frame, [75, 90], [0, 0.45], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // New live card slide-in
  const newCardT = interpolate(frame, [15, 45], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const newCardY = (1 - newCardT) * -10;

  // Live dot pulse: 1 Hz scale 1→1.2→1, starts after the card lands
  const pulsePhase = ((frame - 30) / 30) % 1;
  const dotPulse = pulsePhase > 0 ? Math.sin(pulsePhase * Math.PI) : 0;

  // Counter tick — interpolates 3 → 4
  const counter = interpolate(frame, [45, 75], [3, 4], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });
  // Big number to display: ceil once we cross 3.5
  const displayCount = counter < 3.5 ? "3" : "4";
  // Subtle flip wobble on the number: scale dips at transition
  const counterScale = (() => {
    const t = (counter - 3) / 1; // 0..1
    if (t > 0.45 && t < 0.55) return 0.85;
    return 1;
  })();

  return (
    <AbsoluteFill style={{ background: theme.cream }}>
      <AbsoluteFill style={{ background: `rgba(0,0,0,${dim})` }} />

      {/* Center the right-rail on screen and scale it up so it reads
          as a zoom into the Active Agents column. */}
      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          width: 460,
          transform: `translate(-50%, -50%) scale(${finalScale})`,
          fontFamily: fontSans,
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 6px",
            marginBottom: 16,
          }}
        >
          <h4
            style={{
              margin: 0,
              fontFamily: fontSans,
              fontSize: 22,
              fontWeight: 600,
              color: theme.ink,
              letterSpacing: "-0.01em",
            }}
          >
            Active Agents
          </h4>
          <span
            style={{
              padding: "4px 14px",
              borderRadius: 999,
              background: theme.greenSoft,
              color: theme.green,
              fontSize: 14,
              fontWeight: 600,
              fontVariantNumeric: "tabular-nums",
              display: "inline-block",
              transform: `scale(${counterScale})`,
              minWidth: 30,
              textAlign: "center",
            }}
          >
            {displayCount}
          </span>
        </div>

        {/* New live card */}
        <div
          style={{
            opacity: newCardT,
            transform: `translateY(${newCardY}px)`,
            marginBottom: 10,
          }}
        >
          <ActiveAgentItem
            name="RELIANCE Weekday Dip-Buy"
            status="Live"
            secondary="Active · Next: Fri 3:55 PM"
            pulse={Math.max(0, dotPulse)}
          />
        </div>

        {/* Existing idle agents (the 3) */}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {EXISTING_AGENTS.map((a) => (
            <ActiveAgentItem key={a.name} name={a.name} status="Idle" />
          ))}
        </div>
      </div>
    </AbsoluteFill>
  );
};
