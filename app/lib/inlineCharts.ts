/**
 * Charts a reply places inline with `[[chart:<id>]]`.
 *
 * A tool offers charts of its own data; the backend gives each an id, streams
 * it with `tool_done`, repeats it in `done.raw_data._charts` and persists it
 * with the turn. Whichever of those arrives registers it here, so a marker in
 * the text finds its chart however the message was loaded.
 */
import { useSyncExternalStore } from "react";

export type InlineChartPayload = {
  _render_hint: string;
  id: string;
  title?: string;
} & Record<string, unknown>;

const charts = new Map<string, InlineChartPayload>();
const listeners = new Set<() => void>();

const isChart = (c: unknown): c is InlineChartPayload =>
  !!c && typeof c === "object"
  && typeof (c as InlineChartPayload).id === "string"
  && typeof (c as InlineChartPayload)._render_hint === "string";

/** Accepts a list or an id-keyed map; anything else is ignored. */
export function registerCharts(input: unknown): void {
  const list = Array.isArray(input) ? input
    : input && typeof input === "object" ? Object.values(input) : [];
  let added = false;
  for (const c of list) {
    if (isChart(c) && charts.get(c.id) !== c) {
      charts.set(c.id, c);
      added = true;
    }
  }
  if (added) listeners.forEach((l) => l());
}

const subscribe = (l: () => void): (() => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

export function useInlineChart(id: string): InlineChartPayload | undefined {
  return useSyncExternalStore(subscribe, () => charts.get(id), () => charts.get(id));
}

const MARKER = /\[\[chart:([a-z0-9]{3,16})\]\]/g;
// A marker still arriving on the stream ("[[cha") is held back, not printed.
const PARTIAL = /\[(?:\[(?:c(?:h(?:a(?:r(?:t(?::[a-z0-9]*)?)?)?)?)?)?)?$/;

/** Text split at chart markers: strings are markdown, objects are charts. */
export function splitCharts(text: string): (string | { chart: string })[] {
  const t = text.replace(PARTIAL, "");
  const out: (string | { chart: string })[] = [];
  let last = 0;
  for (const m of t.matchAll(MARKER)) {
    out.push(t.slice(last, m.index), { chart: m[1]! });
    last = m.index! + m[0].length;
  }
  out.push(t.slice(last));
  return out.filter((p) => typeof p !== "string" || p.trim());
}

/** The reply as plain text, for copying: markers carry nothing readable. */
export const stripChartMarkers = (text: string): string =>
  text.replace(MARKER, "").replace(/\n{3,}/g, "\n\n");
