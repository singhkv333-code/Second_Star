/**
 * Side-panel exclusivity coordinator.
 *
 * The app has several independent right-side editors (workflow editor, option
 * chain, option strategy builder, backtest detail, IPO detail/application).
 * Each owns its own open state in different places, so without coordination
 * two could be open at once. This tiny module-level bus lets every panel
 * announce when it opens; any *other* open panel closes itself in response —
 * giving "open one → the others auto-close" without a shared store.
 */

import { useEffect, useRef } from "react";

type Listener = (openedId: string) => void;

const listeners = new Set<Listener>();

/** Tell every other panel that `id` just opened (they close themselves). */
function announcePanelOpen(id: string): void {
  listeners.forEach((fn) => fn(id));
}

function subscribe(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/**
 * Make a side panel mutually exclusive with all others.
 *
 * @param id     stable, unique panel id
 * @param open   whether this panel is currently open
 * @param close  closes this panel (called when another panel opens)
 */
export function useExclusiveSidePanel(id: string, open: boolean, close: () => void): void {
  // Keep the latest close callback without re-subscribing each render.
  const closeRef = useRef(close);
  closeRef.current = close;

  // Close self whenever a *different* panel announces that it opened.
  useEffect(() => {
    return subscribe((openedId) => {
      if (openedId !== id) closeRef.current();
    });
  }, [id]);

  // Announce on the open transition (false → true) so the others close.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open && !wasOpen.current) announcePanelOpen(id);
    wasOpen.current = open;
  }, [open, id]);
}
