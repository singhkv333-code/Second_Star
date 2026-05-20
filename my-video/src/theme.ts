// Design tokens lifted from pivot-next/app/globals.css (dark mode).
// Keep these in sync if the product palette shifts.

export const colors = {
  bgBase: "#0d0d0e",
  bgPrimary: "#111212",
  bgSecondary: "#15161a",
  bgCard: "#181a1f",
  bgElevated: "#1f2127",

  border: "rgba(255, 255, 255, 0.06)",
  borderHover: "rgba(255, 255, 255, 0.12)",
  borderFocus: "rgba(255, 255, 255, 0.24)",

  textPrimary: "#fbfcfc",
  textSecondary: "#8f98a1",
  textTertiary: "#6b7280",
  textDisabled: "#485259",

  profit: "#10b981",
  loss: "#ef4444",
  warn: "#f59e0b",
  pivotBlue: "#60a5fa",
  priceLine: "#e2e8f0",

  sky: "#38bdf8",
  emerald100: "rgba(16, 185, 129, 0.15)",
  emeraldBorder: "rgba(16, 185, 129, 0.3)",
} as const;

export const radii = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  pill: 9999,
} as const;
