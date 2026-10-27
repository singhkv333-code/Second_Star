"use client";

import { useEffect, useRef } from "react";

/**
 * The execution system, mounted inside the Pivot shell.
 *
 * `chart/plays.html` (the plays page: chat that builds plays, their
 * cards, Inbox, Review) is framed whole, for the same reason the chart is
 * (see ChartFrame): it is vanilla JS with its own design, and a frame keeps
 * it exactly as it is. It is served from this origin at /chart-app, so it
 * shares the shell's sign-in (`charto:auth:token` in localStorage) and talks
 * to the same data server.
 *
 * One thing crosses the boundary: the page's "Open chart" links. Framed, the
 * page posts `plays:open-chart` instead of opening a second window, and the
 * shell switches to its own Chart tab on that symbol. The plays have no
 * portfolio of their own: the shell's Portfolio (PortfolioTab, with its Day
 * book) is theirs.
 *
 * Inbox uses a separate instance opened by the shell bell. The Execution
 * instance keeps its conversation and strategy route when Inbox is opened.
 */
export function PlaysFrame({
  onOpenChart,
  onInboxCount,
  page = "execution",
}: {
  onOpenChart?: (symbol: string) => void;
  onInboxCount?: (count: number) => void;
  page?: "execution" | "inbox";
}): React.ReactElement {
  const ref = useRef<HTMLIFrameElement>(null);
  const onChartRef = useRef(onOpenChart);
  useEffect(() => { onChartRef.current = onOpenChart; }, [onOpenChart]);
  const onCountRef = useRef(onInboxCount);
  useEffect(() => { onCountRef.current = onInboxCount; }, [onInboxCount]);
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== window.location.origin || e.source !== ref.current?.contentWindow) return;
      const d = e.data as { type?: string; symbol?: unknown; count?: unknown } | null;
      if (d?.type === "plays:inbox-count") {
        const n = Number(d.count);
        onCountRef.current?.(Number.isFinite(n) && n > 0 ? Math.floor(n) : 0);
        return;
      }
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
      src={page === "inbox" ? "/chart-app/plays.html#/inbox" : "/chart-app/plays.html"}
      title={page === "inbox" ? "Pivot inbox" : "Pivot execution"}
      className="h-full w-full flex-1 border-0"
      // the workspace surface, so a dark palette does not flash white while
      // the page loads (the page itself takes the palette from the same store)
      style={{ background: "var(--workspace-surface, #ffffff)" }}
    />
  );
}
