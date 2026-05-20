export const theme = {
  // Surfaces
  cream: "#F5F2EC", // primary background
  white: "#FFFFFF", // card surfaces
  border: "#E8E4DC", // subtle dividers

  // Text
  ink: "#1A1A1A", // headlines
  gray: "#8E8B85", // secondary text
  grayMid: "#5A5750", // body text just darker than gray

  // Brand accent (Pivot green)
  green: "#1B5E3F", // dark forest — primary accent
  greenSoft: "#D4ECDE", // badge backgrounds
  greenGlow: "rgba(27, 94, 63, 0.15)",
  greenDeep: "#0F7B4A",

  // P&L
  positive: "#0F7B4A",
  negative: "#B23B3B",

  // Fonts (loaded via @remotion/google-fonts in fonts.ts)
  serif: "Instrument Serif",
  sans: "Inter",
  mono: "JetBrains Mono",

  // Elevation
  shadowSm:
    "0 1px 2px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.04)",
  shadowMd:
    "0 2px 6px rgba(0,0,0,0.05), 0 12px 28px rgba(0,0,0,0.06)",
} as const;

export const spring = {
  damping: 18,
  mass: 0.6,
  stiffness: 120,
} as const;
