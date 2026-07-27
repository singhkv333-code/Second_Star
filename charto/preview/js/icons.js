/* Charto preview — icon set.
 *
 * Lucide (https://lucide.dev, ISC) paths, inlined so the preview stays a
 * zero-dependency static folder. Lucide is the icon set shadcn/ui ships with,
 * so these are the same glyphs the rest of the design language assumes.
 *
 * House rules: 24×24 viewBox, stroke-only, currentColor, no fills except
 * deliberate dots. Size comes from CSS (.icon / .icon-sm), never the markup.
 */
"use strict";

const Icons = (() => {
  const P = {
    // ── drawing tools (left rail) ──────────────────────────
    crosshair: '<circle cx="12" cy="12" r="9"/><path d="M22 12h-4"/><path d="M6 12H2"/><path d="M12 6V2"/><path d="M12 22v-4"/>',
    trend: '<path d="M6.8 17.2 17.2 6.8"/><circle cx="4.5" cy="19.5" r="2.5"/><circle cx="19.5" cy="4.5" r="2.5"/>',
    ray: '<path d="M6.8 17.2 18 6"/><circle cx="4.5" cy="19.5" r="2.5"/><path d="M13.5 6H18v4.5"/>',
    hline: '<path d="M6.5 12h11"/><circle cx="3.5" cy="12" r="2.2"/><circle cx="20.5" cy="12" r="2.2"/>',
    vline: '<path d="M12 6.5v11"/><circle cx="12" cy="3.5" r="2.2"/><circle cx="12" cy="20.5" r="2.2"/>',
    rect: '<rect x="3" y="6" width="18" height="12" rx="2"/>',
    channel: '<path d="M3 15 21 7"/><path d="M3 20 21 12"/>',
    triangle: '<path d="M12 4 21 19H3Z"/>',
    extended: '<path d="M2 18 22 6"/><circle cx="8" cy="14.2" r="2.2"/><circle cx="16" cy="9.8" r="2.2"/>',
    // stacked reward/risk boxes, the way a position tool reads on a chart
    position: '<rect x="3" y="5" width="18" height="6" rx="1"/><rect x="3" y="13" width="18" height="6" rx="1"/><path d="M3 12h18"/>',
    fib: '<path d="M3 5h18"/><path d="M3 9.67h18"/><path d="M3 14.33h13"/><path d="M3 19h18"/>',
    brush: '<path d="m9.06 11.9 8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/>',
    text: '<path d="M12 4v16"/><path d="M4 7V5a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v2"/><path d="M9 20h6"/>',
    measure: '<path d="M21.3 15.3a2.4 2.4 0 0 1 0 3.4l-2.6 2.6a2.4 2.4 0 0 1-3.4 0L2.7 8.7a2.41 2.41 0 0 1 0-3.4l2.6-2.6a2.41 2.41 0 0 1 3.4 0Z"/><path d="m14.5 12.5 2-2"/><path d="m11.5 9.5 2-2"/><path d="m8.5 6.5 2-2"/><path d="m17.5 15.5 2-2"/>',
    magnet: '<path d="m6 15-4-4 6.75-6.77a7.79 7.79 0 0 1 11 11L13 22l-4-4 6.39-6.36a2.14 2.14 0 0 0-3-3L6 15"/><path d="m5 8 4 4"/><path d="m12 15 4 4"/>',
    trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><path d="M12 15V3"/>',

    // ── chrome ─────────────────────────────────────────────
    candles: '<path d="M9 5v4"/><path d="M9 15v4"/><rect x="6.5" y="9" width="5" height="6" rx="1"/><path d="M17 3v6"/><path d="M17 15v6"/><rect x="14.5" y="9" width="5" height="6" rx="1"/>',
    indicators: '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="m19 9-5 5-4-4-3 3"/>',
    chevronDown: '<path d="m6 9 6 6 6-6"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
    arrowUp: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    arrowDown: '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    camera: '<path d="M14.5 4h-5L7.2 6.8H4a2 2 0 0 0-2 2V18a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8.8a2 2 0 0 0-2-2h-3.2L14.5 4z"/><circle cx="12" cy="13" r="3.2"/>',
    eye: '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.5 13.5 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><path d="m2 2 20 20"/>',
    fileText: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
    eraser: '<path d="M21 21H8a2 2 0 0 1-1.42-.587l-3.994-3.999a2 2 0 0 1 0-2.828l10-10a2 2 0 0 1 2.829 0l5.999 6a2 2 0 0 1 0 2.828L12.834 21"/><path d="m5.082 11.09 8.828 8.828"/>',
    copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    panelRight: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M15 3v18"/>',
  };

  /** svg(name, extraClass) → an <svg class="icon …"> string. */
  function svg(name, cls = "") {
    const body = P[name];
    if (!body) throw new Error(`Icons: unknown icon "${name}"`);
    const klass = cls ? `icon ${cls}` : "icon";
    return `<svg class="${klass}" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
  }

  return { svg, paths: P };
})();
