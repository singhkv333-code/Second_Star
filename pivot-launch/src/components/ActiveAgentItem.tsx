import { theme } from "../theme";
import { fontSans } from "../fonts";

// Right-rail agent card. `pulse` (0..1) drives the live dot's scale.
type Props = {
  name: string;
  status: "Live" | "Idle" | "Active";
  // Optional secondary line e.g. "Next: Fri 3:55PM"
  secondary?: string;
  pulse?: number;
};

export const ActiveAgentItem: React.FC<Props> = ({
  name,
  status,
  secondary,
  pulse = 0,
}) => {
  const isLive = status === "Live" || status === "Active";
  const dotScale = 1 + pulse * 0.4;
  return (
    <div
      style={{
        padding: "16px 16px",
        border: `1px solid ${theme.border}`,
        borderRadius: 14,
        background: theme.white,
        fontFamily: fontSans,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 8,
        }}
      >
        <span
          style={{
            fontSize: 11,
            color: theme.gray,
            fontWeight: 500,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
          }}
        >
          Strategy
        </span>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11.5,
            color: isLive ? theme.green : theme.gray,
            fontWeight: 500,
          }}
        >
          <span
            style={{
              width: 9,
              height: 9,
              borderRadius: 999,
              background: isLive ? theme.green : theme.gray,
              transform: `scale(${dotScale})`,
              boxShadow: isLive && pulse > 0
                ? `0 0 0 ${pulse * 6}px ${theme.greenGlow}`
                : "none",
            }}
          />
          {status}
        </span>
      </div>
      <div
        style={{
          fontSize: 14,
          fontWeight: 500,
          color: theme.ink,
          letterSpacing: "-0.005em",
        }}
      >
        {name}
      </div>
      {secondary && (
        <div
          style={{
            marginTop: 6,
            fontSize: 12,
            color: theme.gray,
            fontWeight: 400,
          }}
        >
          {secondary}
        </div>
      )}
    </div>
  );
};
