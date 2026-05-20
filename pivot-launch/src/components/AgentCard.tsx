import { theme } from "../theme";
import { fontSans } from "../fonts";
import { AGENT } from "../mock";

type Props = {
  // 0..1 enter progress (scales the card in with subtle overshoot)
  enter?: number;
  // Highlight the Backtest button (used in Scene 5)
  highlightBacktest?: boolean;
  // Pulse the Activate button (used in Scene 5)
  pulseActivate?: number;
};

export const AgentCard: React.FC<Props> = ({
  enter = 1,
  highlightBacktest = false,
  pulseActivate = 0,
}) => {
  return (
    <div
      style={{
        width: 520,
        background: theme.white,
        border: `1px solid ${theme.border}`,
        borderRadius: 16,
        boxShadow: theme.shadowSm,
        padding: 24,
        fontFamily: fontSans,
        opacity: enter,
        transform: `scale(${0.92 + enter * 0.08})`,
        transformOrigin: "center top",
      }}
    >
      {/* Header row */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 14,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              width: 9,
              height: 9,
              borderRadius: 999,
              background: theme.green,
            }}
          />
          <span
            style={{
              fontFamily: fontSans,
              fontWeight: 500,
              fontSize: 14,
              color: theme.grayMid,
            }}
          >
            Strategy Agent
          </span>
        </div>
        <span
          style={{
            padding: "3px 10px",
            borderRadius: 999,
            background: theme.cream,
            color: theme.gray,
            fontFamily: fontSans,
            fontSize: 11,
            fontWeight: 500,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
          }}
        >
          Draft
        </span>
      </div>

      {/* Name */}
      <h3
        style={{
          margin: 0,
          fontFamily: fontSans,
          fontWeight: 600,
          fontSize: 22,
          color: theme.ink,
          letterSpacing: "-0.01em",
          marginBottom: 18,
        }}
      >
        {AGENT.name}
      </h3>

      {/* Field rows */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <FieldRow label="Trigger" value={AGENT.trigger} />
        <FieldRow label="Condition" value={AGENT.condition} />
        <FieldRow label="Action" value={AGENT.action} />
        <FieldRow label="Product" value={AGENT.product} />
      </div>

      {/* Buttons */}
      <div style={{ display: "flex", gap: 10, marginTop: 22 }}>
        <button
          style={{
            flex: 1,
            height: 42,
            borderRadius: 10,
            border: `1px solid ${highlightBacktest ? theme.green : theme.border}`,
            background: "transparent",
            color: theme.ink,
            fontFamily: fontSans,
            fontSize: 14,
            fontWeight: 500,
            boxShadow: highlightBacktest
              ? `0 0 0 4px ${theme.greenGlow}`
              : "none",
            cursor: "pointer",
          }}
        >
          Backtest
        </button>
        <button
          style={{
            flex: 1,
            height: 42,
            borderRadius: 10,
            border: "none",
            background: theme.green,
            color: theme.white,
            fontFamily: fontSans,
            fontSize: 14,
            fontWeight: 500,
            transform: `scale(${1 + pulseActivate * 0.04})`,
            boxShadow:
              pulseActivate > 0 ? `0 0 0 6px ${theme.greenGlow}` : "none",
            cursor: "pointer",
          }}
        >
          Activate
        </button>
      </div>
    </div>
  );
};

const FieldRow: React.FC<{ label: string; value: string }> = ({
  label,
  value,
}) => (
  <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: 16 }}>
    <span
      style={{
        fontFamily: fontSans,
        fontSize: 12,
        fontWeight: 500,
        textTransform: "uppercase",
        letterSpacing: "0.08em",
        color: theme.gray,
      }}
    >
      {label}
    </span>
    <span
      style={{
        fontFamily: fontSans,
        fontSize: 14,
        fontWeight: 500,
        color: theme.ink,
      }}
    >
      {value}
    </span>
  </div>
);
