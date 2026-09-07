"use client";

/**
 * CardErrorBoundary — a per-card React error boundary for the chat thread.
 *
 * Chat renders ~24 card types keyed off a backend `_render_hint`. A single
 * malformed payload (a missing `metrics` block, an unexpected null) used to
 * throw during render and — with no boundary — take down the ENTIRE /#chat
 * route with a full-screen "Unhandled Runtime Error" overlay. That is a
 * catastrophic failure mode for one bad card in a long conversation.
 *
 * This boundary isolates each card: if one throws, only that card collapses
 * into a small inline "couldn't render" notice; the rest of the thread (and
 * the composer) stays fully usable. Wrap each rendered card with it.
 */

import * as React from "react";
import { AlertTriangle } from "lucide-react";

type Props = {
  children: React.ReactNode;
  /** Short label for logs / the fallback copy, e.g. the render hint. */
  label?: string;
};

type State = { hasError: boolean };

export class CardErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  override componentDidCatch(error: Error, info: React.ErrorInfo): void {
    // Keep it in the console for debugging; never re-throw (that would
    // bubble to the route-level overlay we're trying to prevent).
    console.error(
      `[chat-card${this.props.label ? `:${this.props.label}` : ""}] render failed`,
      error,
      info.componentStack,
    );
  }

  override render(): React.ReactNode {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          data-testid="chat-card-error"
          className="mb-2 mt-1 w-full max-w-[388px] rounded-2xl border border-amber-500/40 bg-amber-50/50 px-4 py-3 text-[12px] text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/[0.06] dark:text-amber-300"
        >
          <span className="inline-flex items-center gap-1.5 font-medium">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            This card couldn&apos;t be shown
          </span>
          <p className="mt-1 leading-snug text-amber-700/90 dark:text-amber-400/90">
            The rest of your chat is unaffected — try re-asking, or continue below.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}
