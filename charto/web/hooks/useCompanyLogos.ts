"use client";

import { useEffect, useState } from "react";
import { getCompanyLogos } from "@/lib/api";

/**
 * useCompanyLogos — resolve company logo URLs for a list of symbols in a
 * single batched request, for table/list surfaces (screener, portfolio).
 *
 * Returns a `symbol(UPPER) → URL | null` map. A `null` value means "looked
 * up, none found" → the caller renders a first-letter monogram.
 *
 * A module-level cache is shared across every hook instance and survives
 * remounts, so re-sorting a table, switching tabs, or re-rendering never
 * refetches a symbol whose logo (or confirmed absence) is already known.
 * Only the symbols missing from the cache are fetched.
 */

// UPPER symbol → URL | null. null = confirmed miss (don't refetch).
const _cache = new Map<string, string | null>();

export function useCompanyLogos(
  symbols: string[],
): Record<string, string | null> {
  const norm = Array.from(
    new Set(symbols.map((s) => s.trim().toUpperCase()).filter(Boolean)),
  );
  // Stable dependency: sorted, joined symbol list.
  const key = norm.slice().sort().join(",");

  // Bump to re-render once a fetch populates the cache.
  const [, force] = useState(0);

  useEffect(() => {
    const missing = norm.filter((s) => !_cache.has(s));
    if (missing.length === 0) return;
    let cancelled = false;
    getCompanyLogos(missing).then((map) => {
      if (cancelled) return;
      // Record every requested symbol — even those the backend omitted — so a
      // confirmed miss is cached as null and never refetched.
      missing.forEach((s) => _cache.set(s, map[s] ?? null));
      force((n) => n + 1);
    });
    return () => {
      cancelled = true;
    };
    // norm is derived from `key`; depending on `key` avoids array-identity loops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const out: Record<string, string | null> = {};
  norm.forEach((s) => {
    out[s] = _cache.get(s) ?? null;
  });
  return out;
}
