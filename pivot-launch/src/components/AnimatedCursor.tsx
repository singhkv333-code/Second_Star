import { MousePointer2 } from "lucide-react";
import { theme } from "../theme";

// A fake macOS-ish cursor that we animate by parent-positioning.
// Position is supplied by the scene; we don't add motion here so the
// parent stays in control of timing (frame-driven interpolate).
type Props = {
  x: number;
  y: number;
  // 0..1 click ripple — when > 0 we draw an expanding ring underneath.
  click?: number;
};

export const AnimatedCursor: React.FC<Props> = ({ x, y, click = 0 }) => (
  <div
    style={{
      position: "absolute",
      left: x,
      top: y,
      pointerEvents: "none",
      zIndex: 100,
    }}
  >
    {click > 0 && (
      <div
        style={{
          position: "absolute",
          top: 6,
          left: 6,
          width: 24,
          height: 24,
          borderRadius: 999,
          border: `2px solid ${theme.green}`,
          opacity: 1 - click,
          transform: `translate(-50%, -50%) scale(${0.6 + click * 2})`,
        }}
      />
    )}
    <MousePointer2
      size={26}
      color={theme.ink}
      fill={theme.white}
      strokeWidth={1.6}
      style={{ filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.2))" }}
    />
  </div>
);
