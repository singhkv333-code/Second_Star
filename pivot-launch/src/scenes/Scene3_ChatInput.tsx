import { AbsoluteFill, useCurrentFrame, interpolate, Easing } from "remotion";
import { ArrowRight } from "lucide-react";
import { theme } from "../theme";
import { fontSans, fontSerifItalic } from "../fonts";
import { USER_PROMPT } from "../mock";

// Scene 3 — 180-300 (local 0-120, 4s).
//   0-20    chat input fades + scales 1.1 → 1, ambient green glow
//   20-110  user types USER_PROMPT char-by-char (~25ms/char ≈ 0.75 frames/char)
//   110-120 send button glow pulses
export const Scene3_ChatInput: React.FC = () => {
  const frame = useCurrentFrame();

  const enterT = interpolate(frame, [0, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const enterScale = interpolate(enterT, [0, 1], [1.1, 1]);

  // Type frame range and per-char duration: ~25 ms/char = ~0.75 frames/char
  const typeStart = 20;
  const typeEnd = 105; // leaves ~15 frames of "settled" caret
  const totalChars = USER_PROMPT.length;
  const charProgress = interpolate(
    frame,
    [typeStart, typeEnd],
    [0, totalChars],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const typedCount = Math.floor(charProgress);
  const typed = USER_PROMPT.slice(0, typedCount);

  // Placeholder visible only before first char.
  const hasText = typedCount > 0;
  const placeholderOpacity = interpolate(frame, [16, 22], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Caret blinks ~1 Hz (period 30 frames @ 30fps).
  const caretOn =
    !hasText
      ? false
      : Math.floor(frame / 15) % 2 === 0 || frame < typeEnd;

  // Send button pulse after typing ends.
  const pulse = interpolate(frame, [108, 116, 120], [0, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.34, 1.56, 0.64, 1),
  });

  return (
    <AbsoluteFill
      style={{
        background: theme.cream,
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          position: "relative",
          width: 920,
          opacity: enterT,
          transform: `scale(${enterScale})`,
          transformOrigin: "center center",
        }}
      >
        {/* Ambient green glow behind the bar */}
        <div
          style={{
            position: "absolute",
            inset: -80,
            background:
              "radial-gradient(closest-side, rgba(27,94,63,0.18), rgba(27,94,63,0) 70%)",
            filter: "blur(40px)",
            zIndex: 0,
          }}
        />

        {/* Input pill */}
        <div
          style={{
            position: "relative",
            zIndex: 1,
            height: 76,
            background: theme.white,
            border: `1px solid ${theme.border}`,
            borderRadius: 38,
            boxShadow: theme.shadowMd,
            display: "flex",
            alignItems: "center",
            padding: "0 12px 0 28px",
            gap: 14,
          }}
        >
          {/* Text area */}
          <div
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              minWidth: 0,
              position: "relative",
              fontFamily: hasText ? fontSerifItalic : fontSans,
              fontStyle: hasText ? "italic" : "normal",
              fontWeight: 400,
              fontSize: hasText ? 28 : 18,
              color: hasText ? theme.ink : theme.gray,
              lineHeight: 1.25,
              whiteSpace: "nowrap",
              overflow: "hidden",
            }}
          >
            {!hasText && (
              <span
                style={{
                  opacity: placeholderOpacity,
                  fontFamily: fontSans,
                  fontStyle: "normal",
                  fontSize: 18,
                  color: theme.gray,
                  fontWeight: 400,
                }}
              >
                Ask Pivot anything about your portfolio, markets, or
                strategies…
              </span>
            )}
            {hasText && (
              <span style={{ display: "inline-flex", alignItems: "center" }}>
                <span>{typed}</span>
                {caretOn && (
                  <span
                    style={{
                      display: "inline-block",
                      width: 2,
                      height: 32,
                      background: theme.ink,
                      marginLeft: 3,
                      marginBottom: -4,
                    }}
                  />
                )}
              </span>
            )}
          </div>

          {/* Send button */}
          <button
            style={{
              width: 52,
              height: 52,
              borderRadius: 999,
              background: theme.ink,
              color: theme.white,
              border: "none",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow:
                pulse > 0
                  ? `0 0 0 ${pulse * 10}px ${theme.greenGlow}`
                  : "none",
              transform: `scale(${1 + pulse * 0.08})`,
            }}
          >
            <ArrowRight size={22} strokeWidth={2.2} />
          </button>
        </div>
      </div>
    </AbsoluteFill>
  );
};
