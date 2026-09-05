"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The charting engine, mounted inside the Pivot shell.
 *
 * WHY AN IFRAME AND NOT A PORT
 *
 * `charto/preview` is ~26k lines of vanilla JS over a vendored Lightweight
 * Charts build carrying six hand-applied patches (see
 * `charto/preview/VENDOR_PATCHES.md` — the bundle is a different minification
 * from upstream, so a patch written against upstream source silently does not
 * apply). Re-expressing that as React would put every one of those at risk and
 * buy nothing a user can see. The chart is the differentiator; the shell is
 * chrome around it.
 *
 * WHAT CROSSES THE BOUNDARY, AND WHAT DOES NOT
 *
 *   Auth   — nothing crosses. Production serves the shell and the chart from
 *            ONE origin through nginx, and the chart reads its token from
 *            localStorage. Same origin, same localStorage: a user signed in
 *            here is already signed in there. This is the whole reason the
 *            iframe is same-origin rather than a separate host.
 *   Symbol — the `src`. The chart already switches symbol by assigning
 *            `location.search` and reloading; setting `src` is that same door.
 *            A postMessage that swapped it in place would be a second, weaker
 *            mechanism for something that already works.
 *   Theme  — postMessage, because the shell owns the toggle and a light shell
 *            around a dark chart is the one thing the frame can get visibly
 *            wrong.
 */

/** Where the chart app is served from.
 *
 *  Empty string = same origin, which is the production topology and the only
 *  one where the localStorage hand-off works. Set NEXT_PUBLIC_CHART_ORIGIN in
 *  development, where the shell is on :3000 and the chart on :5173 — auth will
 *  not carry across that gap, which is expected: sign in on the chart itself.
 */
const CHART_ORIGIN = process.env.NEXT_PUBLIC_CHART_ORIGIN ?? "";

type Props = {
  /** Symbol to open. Changing it reloads the frame, by design. */
  symbol?: string;
  /** "dark" | "light", pushed to the chart whenever the shell's theme changes. */
  theme?: "dark" | "light";
};

export function ChartFrame({ symbol, theme }: Props): React.ReactElement {
  const ref = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);

  // Built once per symbol. Deliberately NOT dependent on `theme`: rebuilding
  // the URL on a theme flip would reload the chart and throw away the user's
  // drawings, indicators and scroll position to change a colour.
  const src = `${CHART_ORIGIN}/${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`;

  const post = useCallback((msg: Record<string, unknown>) => {
    const win = ref.current?.contentWindow;
    if (!win) return;
    // Target the frame's own origin, never "*": a wildcard would deliver the
    // message to whatever happens to be loaded there if the src ever changes
    // under us.
    win.postMessage(msg, CHART_ORIGIN || window.location.origin);
  }, []);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      const expected = CHART_ORIGIN || window.location.origin;
      if (e.origin !== expected) return;
      const d = e.data as { type?: string } | null;
      if (d && d.type === "chart:ready") setReady(true);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Push the theme once the frame has announced itself, and on every later
  // change. Before "chart:ready" the listener inside the frame does not exist
  // yet and the message would land on nothing.
  useEffect(() => {
    if (!ready || !theme) return;
    post({ type: "pivot:theme", mode: theme });
  }, [ready, theme, post]);

  return (
    <iframe
      ref={ref}
      src={src}
      title="Chart"
      className="h-full w-full border-0"
      // The chart needs its own storage (the auth token, the workspace, saved
      // layouts) and same-origin is what grants it. `allow-scripts` plus
      // `allow-same-origin` on a frame we serve ourselves is not a sandbox
      // escape — it is the same trust the page already has.
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads"
      allow="clipboard-write; microphone"
    />
  );
}
