/* Charto preview — the embed seam.
 *
 * The chart is becoming a route inside Pivot's Next.js shell (/chart) rather
 * than a separate site, and it gets there in an iframe. That is a deliberate
 * choice over a port: this app is 26k lines of vanilla JS over a vendored
 * Lightweight Charts bundle with six hand-applied patches (VENDOR_PATCHES.md),
 * and rewriting it as React would risk every one of them for nothing a user
 * can see.
 *
 * THREE THINGS THE FRAME NEEDS, AND HOW LITTLE THIS FILE HAS TO DO
 *
 *   Auth   — nothing. The token lives in localStorage under
 *            "charto:auth:token", and the frame is SAME-ORIGIN in production
 *            (nginx serves both the shell and this app). Same origin means the
 *            same localStorage, so a user signed in on the shell is already
 *            signed in here. Cross-origin would need a token hand-off; it is
 *            not built, because the topology does not call for it.
 *
 *   Symbol — nothing. This app already changes symbol by assigning
 *            `location.search = "?symbol=X"` and reloading (main.js:4445), and
 *            layouts.js says in as many words that the primary symbol is fixed
 *            at boot. The parent therefore switches symbols by setting the
 *            iframe's src, which is the same reload through the same door. A
 *            postMessage that tried to swap the symbol in place would be a
 *            second, weaker mechanism for something that already works.
 *
 *   Theme  — this file. The shell owns the theme toggle; a user flipping to
 *            light must not leave a dark chart sitting in the middle of the
 *            page. `Theme.set(mode)` repaints canvas and CSS together.
 *
 * INERT WHEN NOT FRAMED. Standalone is still the primary way this app runs —
 * it is what `/` serves today — so everything below is behind the top-window
 * check and adds one listener when embedded, nothing when not.
 */
"use strict";

(() => {
  // Not in a frame: the standalone app owns its own theme and says nothing to
  // anybody. Returning here is what keeps this file free at `/`.
  if (window.parent === window) return;

  // Same-origin only. In production nginx serves the shell and this app from
  // one origin, so a message from anywhere else is not the shell — it is
  // somebody else's page that has framed us, and it does not get to repaint
  // the chart or learn what the user is looking at.
  const trusted = (e) => e.origin === window.location.origin;

  const tell = (msg) => {
    try {
      window.parent.postMessage(msg, window.location.origin);
    } catch { /* the parent went away mid-navigation; nothing to do */ }
  };

  window.addEventListener("message", (e) => {
    if (!trusted(e)) return;
    const d = e.data;
    if (!d || typeof d !== "object") return;

    if (d.type === "pivot:theme" && (d.mode === "dark" || d.mode === "light")) {
      // `persist` left true on purpose: if the user goes back to the chart
      // standalone, it should still be wearing the theme they chose in the
      // shell. One theme per person, not one per surface.
      try { Theme.set(d.mode); } catch { /* theme.js not up yet */ }
    }
  });

  // Tell the shell we are alive, and which symbol we ended up on. The shell
  // uses this to label its own chrome — it cannot read the iframe's URL
  // (same-origin would allow it, but the shell should not be reaching into
  // this app's internals to find out something the app can simply say).
  const announce = () => {
    let symbol = null;
    try {
      symbol = new URLSearchParams(window.location.search).get("symbol");
    } catch { /* ignore */ }
    tell({ type: "chart:ready", symbol });
  };

  if (document.readyState === "complete") announce();
  else window.addEventListener("load", announce);
})();
