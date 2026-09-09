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
 *   Auth   — nothing crosses, and today nothing is shared either. The frame
 *            is same-origin so the two CAN see one localStorage, but they
 *            keep different keys issued by different servers: the shell holds
 *            `pivot_jwt` from Pivot's API, the chart holds
 *            `charto:auth:token` from Charto's dataserver. So the chart asks
 *            for its own sign-in the first time and remembers it after that.
 *            Same origin is the precondition for ever fixing that — a chart
 *            on a second port could not share a token even in principle — but
 *            it is not the fix. That is a login that mints both.
 *   Symbol — the `src`. The chart already switches symbol by assigning
 *            `location.search` and reloading; setting `src` is that same door.
 *            A postMessage that swapped it in place would be a second, weaker
 *            mechanism for something that already works.
 *   Theme  — postMessage, because the shell owns the toggle and a light shell
 *            around a dark chart is the one thing the frame can get visibly
 *            wrong.
 */

/** Where the chart app is served from — a PATH on this origin, not a host.
 *
 *  next.config.ts proxies /chart-app to the chart's own server (:5173 in
 *  development, nginx in production), so the browser only ever sees one
 *  origin. Pointing the frame at a second port instead would work for exactly
 *  one thing — showing candles — and break the rest: the chart keeps its auth
 *  token, workspace and saved layouts in localStorage, which is per-origin, so
 *  a chart on :5173 is a chart the user signed into the shell is signed out
 *  of. Same origin is the whole design, not a deployment detail.
 */
const CHART_BASE = "/chart-app";

type Props = {
  /** Symbol to open. Changing it reloads the frame, by design. */
  symbol?: string;
  /** "dark" | "light", pushed to the chart whenever the shell's theme changes. */
  theme?: "dark" | "light";
  /** Width in px of the shell's nav rail, which overlays this frame's left
   *  edge on the chart route. The frame spans the full window so the chart's
   *  header can act as the shell's ONE top bar and reach the left edge; the
   *  chart then insets everything below that header by this much so the rail
   *  is not sitting on top of its tools. 0 when nothing overlays us. */
  railWidth?: number;
};

export function ChartFrame({ symbol, theme, railWidth = 0 }: Props): React.ReactElement {
  const ref = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  // Built once per symbol. Deliberately NOT dependent on `theme`: rebuilding
  // the URL on a theme flip would reload the chart and throw away the user's
  // drawings, indicators and scroll position to change a colour.
  // `/chart-app/index.html`, and both halves of that are deliberate.
  //
  // The chart's HTML loads its 30-odd scripts by RELATIVE path, so what they
  // resolve against is decided by the frame's URL. At `/chart-app` the base is
  // `/`, every `js/*.js` became `/js/*.js`, and the shell answered its own 404
  // page — which the browser then refused to execute as script, so the frame
  // rendered chrome with no chart in it.
  //
  // `/chart-app/` would fix the base, but Next 308-redirects a trailing slash
  // away before the rewrite runs and we are back at the first case. Naming the
  // file gives the same base with no redirect and no global config change.
  const src = `${CHART_BASE}/index.html${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`;

  const post = useCallback((msg: Record<string, unknown>) => {
    const win = ref.current?.contentWindow;
    if (!win) return;
    // Target a concrete origin, never "*": a wildcard would deliver the
    // message to whatever happens to be loaded there if the src ever changes
    // under us. The frame is same-origin by construction, so this is ours.
    win.postMessage(msg, window.location.origin);
  }, []);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== window.location.origin || e.source !== ref.current?.contentWindow) return;
      const d = e.data as { type?: string } | null;
      if (d?.type === "chart:ready") { setReady(true); setFailed(false); }
      if (d?.type === "chart:error") { setReady(false); setFailed(true); }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  useEffect(() => {
    setReady(false);
    setFailed(false);
  }, [src, attempt]);

  useEffect(() => {
    if (ready) return;
    const timer = window.setTimeout(() => setFailed(true), 20000);
    return () => window.clearTimeout(timer);
  }, [ready, src, attempt]);

  // Push the theme once the frame has announced itself, and on every later
  // change. Before "chart:ready" the listener inside the frame does not exist
  // yet and the message would land on nothing.
  useEffect(() => {
    if (!ready || !theme) return;
    post({ type: "pivot:theme", mode: theme });
  }, [ready, theme, post]);

  // Same contract for the rail inset: the chart cannot measure a rail that
  // belongs to the parent document, so the shell states it. Sent on ready and
  // on every later change (the rail can collapse), so the chart's tools move
  // out from under it instead of hiding beneath it.
  useEffect(() => {
    if (!ready) return;
    post({ type: "pivot:railpad", width: railWidth });
  }, [ready, railWidth, post]);

  return (
    <div className="relative flex flex-1 min-h-0 flex-col" style={{ background: "var(--bg-base)" }}>
    {(!ready || failed) && (
      <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3" style={{ background: "var(--bg-base)", color: "var(--text-secondary)" }} role="status">
        {failed ? "The chart could not finish loading." : "Loading chart…"}
        {failed && <button type="button" onClick={() => setAttempt((value) => value + 1)}>Reload chart</button>}
      </div>
    )}
    <iframe
      key={attempt}
      ref={ref}
      src={src}
      onLoad={() => {
        setReady(false);
        post({ type: "pivot:hello" });
        if (theme) post({ type: "pivot:theme", mode: theme });
        post({ type: "pivot:railpad", width: railWidth });
      }}
      onError={() => setFailed(true)}
      style={{ visibility: ready && !failed ? "visible" : "hidden" }}
      title="Chart"
      // `flex-1 min-h-0`, not `h-full`. The parent (AppShell's chart pane) is
      // `flex flex-col`, and on a flex CHILD `height:100%` resolves against a
      // container whose height flexbox has not finished computing — Chrome
      // falls back to the frame's intrinsic 150px. The chart app inside is
      // `height:100dvh` against THAT box, so its header, left tool rail and
      // right panel all laid out inside a sliver: the distorted, half-drawn
      // chrome with no candles. Flexing the frame instead makes it consume the
      // pane's real height, which is the height the chart then measures.
      // `min-h-0` because a flex item's default `min-height:auto` would let the
      // frame's own content push it past the pane and re-introduce clipping.
      className="flex-1 min-h-0 w-full border-0"
      // The chart needs its own storage (the auth token, the workspace, saved
      // layouts) and same-origin is what grants it. `allow-scripts` plus
      // `allow-same-origin` on a frame we serve ourselves is not a sandbox
      // escape — it is the same trust the page already has.
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads"
      allow="clipboard-write; microphone"
    />
    </div>
  );
}
