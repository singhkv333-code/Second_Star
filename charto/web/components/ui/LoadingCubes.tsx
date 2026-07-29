/**
 * LoadingCubes — the platform's loading indicator: a set of isometric cubes
 * that tumble in an endless Escher-style loop (SMIL-animated SVG asset in
 * public/loaders). Replaces the plain "P" splash.
 *
 * The source art is black-stroke / white-fill (solid white cubes on dark).
 * We invert in LIGHT mode so it reads as solid black cubes on white, and
 * leave it as-is in DARK mode — either way the cubes stay solid, not a
 * wireframe. SMIL runs natively inside an <img>, so no inline markup.
 */

import { cn } from "@/lib/utils";

export function LoadingCubes({
  size = 160,
  className,
  label = "Loading",
}: {
  /** Rendered box size in px (the cubes sit centred within it). */
  size?: number;
  className?: string;
  label?: string;
}): React.ReactElement {
  return (
    <img
      src="/loaders/isometric-cubes.svg"
      width={size}
      height={size}
      alt={label}
      // invert (black cubes) on light; original (white cubes) on dark.
      className={cn("invert dark:invert-0", className)}
      style={{ display: "block" }}
    />
  );
}

export default LoadingCubes;
