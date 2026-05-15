"use client";

/**
 * DesktopScale — proportionally shrinks the entire page on laptop-sized
 * viewports so the design composed for ~1600px still feels spacious at
 * 1280px or 1366px. Below the lg breakpoint (1024px) we leave the page
 * alone so the mobile layout renders at native size.
 *
 * Mechanism: sets html { zoom } based on window.innerWidth. `zoom` is
 * preferred over `transform: scale` because it preserves layout flow,
 * sticky positioning, and scroll math.
 */

import { useEffect } from "react";

const IDEAL_WIDTH = 1920;
const LG = 1024;

export function DesktopScale(): null {
  useEffect(() => {
    const apply = () => {
      const w = window.innerWidth;
      const root = document.documentElement;
      if (w < LG) {
        root.style.zoom = "";
        return;
      }
      if (w >= IDEAL_WIDTH) {
        root.style.zoom = "";
        return;
      }
      const z = w / IDEAL_WIDTH;
      root.style.zoom = String(z);
    };
    apply();
    window.addEventListener("resize", apply);
    return () => {
      window.removeEventListener("resize", apply);
      document.documentElement.style.zoom = "";
    };
  }, []);
  return null;
}
