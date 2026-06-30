"use client";

import { useState, useEffect } from "react";

/**
 * Reads a CSS custom property from the document root and returns its
 * concrete value. Re-reads on theme change (dark class toggle on <html>)
 * so recharts fills and strokes stay correct in both light and dark mode.
 *
 * Guards typeof window for SSR; returns a safe default "#000" until mount.
 */
export function useTokenColor(varName: string): string {
  const [color, setColor] = useState<string>("#000000");

  useEffect(() => {
    if (typeof window === "undefined") return;

    const read = () => {
      const val = getComputedStyle(document.documentElement)
        .getPropertyValue(varName)
        .trim();
      setColor(val || "#000000");
    };

    read();

    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });

    return () => obs.disconnect();
  }, [varName]);

  return color;
}

/**
 * Batch variant of {@link useTokenColor}. Pass a map of arbitrary keys → CSS
 * custom-property names; get back a map of the same keys → concrete resolved
 * colors. Re-reads every entry on a `.dark` class mutation of <html> so
 * recharts <Cell> / gradient colors flip with the theme.
 *
 * Usage:
 *   const c = useTokenColors({ profit: "--color-profit", loss: "--color-loss" });
 *   <Cell fill={v >= 0 ? c.profit : c.loss} />
 *
 * SSR-safe: returns "#000000" for every key until mounted.
 */
export function useTokenColors<K extends string>(
  vars: Record<K, string>,
): Record<K, string> {
  // Stable key list + a join key so the effect only re-runs when the set of
  // requested vars actually changes (not on every render's fresh object).
  const keys = Object.keys(vars) as K[];
  const signature = keys.map((k) => `${k}:${vars[k]}`).join("|");

  const [colors, setColors] = useState<Record<K, string>>(() => {
    const init = {} as Record<K, string>;
    for (const k of keys) init[k] = "#000000";
    return init;
  });

  useEffect(() => {
    if (typeof window === "undefined") return;

    const read = () => {
      const cs = getComputedStyle(document.documentElement);
      const next = {} as Record<K, string>;
      for (const k of keys) {
        next[k] = cs.getPropertyValue(vars[k]).trim() || "#000000";
      }
      setColors(next);
    };

    read();

    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });

    return () => obs.disconnect();
    // `signature` captures both the keys and their var names; intentionally the
    // single dependency so we don't thrash on fresh object identities.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  return colors;
}
