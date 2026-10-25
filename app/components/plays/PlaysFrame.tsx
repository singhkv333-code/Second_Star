"use client";

import { useEffect, useRef } from "react";

/**
 * The execution system, mounted inside the Pivot shell.
 *
 * `chart/plays.html` (the plays page: chat that builds plays, their
 * cards, Inbox, Portfolio, Review) is framed whole, for the same reason the
 * chart is (see ChartFrame): it is vanilla JS with its own design, and a
 * frame keeps it exactly as it is. It is served from this origin at
 * /chart-app, so it shares the shell's sign-in (`charto:auth:token` in
 * localStorage) and talks to the same data server.
 *
 * One thing crosses the boundary: the page's "Open chart" links. Framed, the
 * page posts `plays:open-chart` instead of opening a second window, and the
 * shell switches to its own Chart tab on that symbol.
 */
export function PlaysFrame({ onOpenChart }: { onOpenChart?: (symbol: string) => void }): React.ReactElement {
  const ref = useRef<HTMLIFrameElement>(null);
  const onChartRef = useRef(onOpenChart);
  useEffect(() => { onChartRef.current = onOpenChart; }, [onOpenChart]);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== window.location.origin || e.source !== ref.current?.contentWindow) return;
      const d = e.data as { type?: string; symbol?: unknown } | null;
      if (d?.type !== "plays:open-chart") return;
      const symbol = typeof d.symbol === "string" && /^[\w:&.@ -]{1,40}$/.test(d.symbol) ? d.symbol : "";
      onChartRef.current?.(symbol || "NIFTY");
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  return (
    <iframe
      ref={ref}
      src="/chart-app/plays.html"
      title="Pivot execution"
      className="h-full w-full flex-1 border-0"
      style={{ background: "#fafafa" }}
    />
  );
}
