import { AbsoluteFill, useCurrentFrame, interpolate, Easing } from "remotion";
import { theme } from "../theme";
import { fontSans, fontSerif } from "../fonts";
import { PivotLogo } from "../components/PivotLogo";

// Scene 7 — frames 630-720 (local 0-90, 3s)
//   0-15    Backdrop fades to cream (we're already on cream; just hold)
//   15-45   Three tagline lines stagger in (8 frames apart)
//   45-70   Logo fades in below
//   70-90   "Visit pivot.so" + green underline draws under "pivot.so"
export const Scene7_Outro: React.FC = () => {
  const frame = useCurrentFrame();

  // Master backdrop fade — incoming from Scene 6's dim wash
  const backdrop = interpolate(frame, [0, 15], [0.45, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Tagline lines
  const lineT = (start: number): number =>
    interpolate(frame, [start, start + 18], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.bezier(0.16, 1, 0.3, 1),
    });
  const line1 = lineT(15);
  const line2 = lineT(23);
  const line3 = lineT(31);

  // Logo
  const logoT = interpolate(frame, [45, 70], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

  // Visit line + underline
  const visitT = interpolate(frame, [70, 86], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const underlineT = interpolate(frame, [80, 90], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });

  return (
    <AbsoluteFill style={{ background: theme.cream, alignItems: "center", justifyContent: "center" }}>
      <AbsoluteFill style={{ background: `rgba(0,0,0,${backdrop})` }} />

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 50,
          position: "relative",
          zIndex: 1,
        }}
      >
        {/* Tagline */}
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4, textAlign: "center" }}>
          <Line text="No charts." color={theme.ink} t={line1} />
          <Line text="No clicks." color={theme.gray} t={line2} />
          <Line text="Just conversation." color={theme.ink} t={line3} />
        </div>

        {/* Logo — outro scale 3×; intro is 6× = 2× outro. */}
        <div
          style={{
            opacity: logoT,
            transform: `translateY(${(1 - logoT) * 8}px)`,
            marginTop: 16,
            display: "inline-flex",
          }}
        >
          <PivotLogo scale={3} form={1} />
        </div>

        {/* Visit pivot.so */}
        <div
          style={{
            opacity: visitT,
            transform: `translateY(${(1 - visitT) * 6}px)`,
            position: "relative",
            display: "inline-flex",
            alignItems: "baseline",
            gap: 8,
            fontFamily: fontSans,
            fontSize: 22,
            fontWeight: 500,
            color: theme.green,
          }}
        >
          <span style={{ color: theme.grayMid }}>Visit</span>
          <span style={{ position: "relative", display: "inline-block" }}>
            <span>pivot.so</span>
            <span
              style={{
                position: "absolute",
                left: 0,
                bottom: -6,
                height: 2,
                width: `${underlineT * 100}%`,
                background: theme.green,
                borderRadius: 2,
              }}
            />
          </span>
        </div>
      </div>
    </AbsoluteFill>
  );
};

const Line: React.FC<{ text: string; color: string; t: number }> = ({ text, color, t }) => (
  <div
    style={{
      opacity: t,
      transform: `translateY(${(1 - t) * 12}px)`,
      fontFamily: fontSerif,
      fontWeight: 400,
      fontSize: 88,
      letterSpacing: "-0.02em",
      lineHeight: 1.08,
      color,
    }}
  >
    {text}
  </div>
);
