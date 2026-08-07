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
    // a ray is a line with ONE anchor — the dot sits where you clicked and
    // the stroke leaves the frame, which is exactly what the tool does
    hray: '<path d="M6.5 12h14"/><circle cx="3.5" cy="12" r="2.2"/>',
    vline: '<path d="M12 6.5v11"/><circle cx="12" cy="3.5" r="2.2"/><circle cx="12" cy="20.5" r="2.2"/>',
    // both axes through one point: the glyph IS the shape it draws
    crossline: '<path d="M12 3.5v17"/><path d="M3.5 12h17"/><circle cx="12" cy="12" r="1.6"/>',
    // trend line + the tag it carries. The tag is the whole difference
    // between this and `trend`, so it is the loudest thing in the frame.
    infoLine: '<path d="M6.4 17.6 15 9"/><circle cx="4.3" cy="19.7" r="2.2"/><circle cx="16.6" cy="7.4" r="2.2"/><rect x="12.5" y="14" width="8.5" height="6" rx="1.5"/>',
    // a line measured against the horizontal, with the swept angle drawn in
    trendAngle: '<path d="M3 20h17"/><path d="M3 20 17 6.7"/><path d="M11.5 20a8.5 8.5 0 0 0-2.36-5.87"/>',
    rect: '<rect x="3" y="6" width="18" height="12" rx="2"/>',
    channel: '<path d="M3 15 21 7"/><path d="M3 20 21 12"/>',
    // one edge FLAT, one sloped — the wedge this tool exists to draw
    flatChannel: '<path d="M3 6.5h18"/><path d="M3 19.5 21 11"/>',
    // two edges that are NOT parallel, each with its own pair of anchors
    disjointChannel: '<path d="M5.4 16.1 10 10.6"/><path d="M15 17.4 19.6 9.9"/><circle cx="3.6" cy="18.2" r="2"/><circle cx="11.4" cy="8.9" r="2"/><circle cx="13.3" cy="19.1" r="2"/><circle cx="21" cy="8.2" r="2"/>',
    /* ── pitchforks ────────────────────────────────────────
     * One drawn family, four glyphs. The frame is a real pitchfork: a base
     * line through the two outer pivots, three tines leaving it on the SAME
     * heading, and a handle running to the base's midpoint. Only the handle
     * changes between the four, because only the handle's origin is what
     * the four constructions disagree about — see js/tools.js. */
    pitchfork: '<path d="M4 8.6 14.5 21"/><path d="M4 8.6 10.5 4.3"/><path d="M9.25 14.8 15.75 10.5"/><path d="M14.5 21 21 16.7"/><path d="M2.6 20.4 9.25 14.8"/>',
    // Schiff — the handle starts half a swing up, so it is drawn short
    schiff: '<path d="M4 8.6 14.5 21"/><path d="M4 8.6 10.5 4.3"/><path d="M9.25 14.8 15.75 10.5"/><path d="M14.5 21 21 16.7"/><path d="M5.7 18.1 9.25 14.8"/><circle cx="4.6" cy="19.1" r="1.7"/>',
    // Modified Schiff — same origin dot, and the handle it replaced left dashed
    schiffMod: '<path d="M4 8.6 14.5 21"/><path d="M4 8.6 10.5 4.3"/><path d="M9.25 14.8 15.75 10.5"/><path d="M14.5 21 21 16.7"/><path d="M5.7 18.1 9.25 14.8"/><path stroke-dasharray="2 2" d="M2.6 20.4 5.7 18.1"/><circle cx="5.7" cy="18.1" r="1.7"/>',
    // Inside — no handle outside the swing at all; it starts ON the base
    insideFork: '<path d="M4 8.6 14.5 21"/><path d="M4 8.6 10.5 4.3"/><path d="M9.25 14.8 15.75 10.5"/><path d="M14.5 21 21 16.7"/><path d="M14.5 21 9.25 14.8"/><circle cx="14.5" cy="20.7" r="1.7"/>',
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
    chevronUp: '<path d="m18 15-6-6-6 6"/>',
    chevronLeft: '<path d="m15 18-6-6 6-6"/>',
    chevronRight: '<path d="m9 18 6-6-6-6"/>',
    // lucide "settings" — the gear TradingView puts on an indicator's legend
    settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
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
    /* The clockwise arrow Pivot's message rows use for "ask that again" —
     * lucide rotate-cw, not the bent undo arrow below it, because re-sending
     * a prompt moves the conversation FORWARD rather than reversing it. */
    rotateCw: '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
    /* The undo pair. lucide undo-2/redo-2: the arrow curls BACK on itself,
     * which is the glyph TradingView, Figma and every editor before them
     * settled on — a plain left arrow reads as "previous", not "revert". */
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5 5.5 5.5 0 0 1-5.5 5.5H11"/>',
    redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5A5.5 5.5 0 0 0 4 14.5 5.5 5.5 0 0 0 9.5 20H13"/>',
    pin: '<path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>',
    panelRight: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M15 3v18"/>',
    /* lucide "external-link" — the one glyph the web already agrees means
     * "this leaves the page you are on". The arrow BREAKS OUT of the frame,
     * which is the whole idea; a bare ↗ is a direction, not a destination,
     * and read as "sort" or "up" more often than as "open elsewhere". */
    externalLink: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h6"/>',

    // ── alerts ─────────────────────────────────────────────
    bell: '<path d="M10.27 21a2 2 0 0 0 3.46 0"/><path d="M3.26 15.33A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.67C19.41 13.96 18 12.5 18 8A6 6 0 0 0 6 8c0 4.5-1.41 5.96-2.74 7.33"/>',
    bellOff: '<path d="M8.7 3A6 6 0 0 1 18 8a21.3 21.3 0 0 0 .6 5"/><path d="M17 17H3s3-2 3-9a4.67 4.67 0 0 1 .3-1.7"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/><path d="m2 2 20 20"/>',
    pause: '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
    play: '<path d="M6 4.5v15l13-7.5z"/>',
    /* The empty alerts state draws at 74px, so it gets its own glyph rather
     * than the bar's bell scaled up. It is TradingView's own idea and the
     * right one: a clock face left OPEN in its lower-right quadrant, with
     * the + standing in the gap — an alert is a time that has not happened
     * yet, and the gap is what you are being asked to fill. The arc is the
     * 270° the plus does not occupy (large-arc, clockwise from 6 o'clock). */
    alertPlus: '<path d="M12 21A9 9 0 1 1 21 12"/><path d="M12 7v5l3.2 2"/><path d="M19.5 16.5v6"/><path d="M16.5 19.5h6"/>',
    // ── watchlist ──────────────────────────────────────────
    /* The STAR is the watchlist, not the bulleted list. Every Indian broker a
     * reader arrives from — Zerodha, Groww, Upstox — marks "things I follow"
     * with a star, and a list glyph beside a bell reads as "menu" rather than
     * as a subject. `list` stays: the alert LOG and the column picker are
     * lists in the ordinary sense and still use it. */
    star: '<path d="M11.53 2.3a.53.53 0 0 1 .94 0l2.31 4.68a2.12 2.12 0 0 0 1.6 1.16l5.16.75a.53.53 0 0 1 .3.91l-3.74 3.63a2.12 2.12 0 0 0-.61 1.88l.88 5.14a.53.53 0 0 1-.77.56l-4.62-2.43a2.12 2.12 0 0 0-1.97 0L6.4 21.01a.53.53 0 0 1-.77-.56l.88-5.14a2.12 2.12 0 0 0-.61-1.88L2.16 9.8a.53.53 0 0 1 .3-.91l5.16-.75a2.12 2.12 0 0 0 1.6-1.16z"/>',
    list: '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3.5 6h.01"/><path d="M3.5 12h.01"/><path d="M3.5 18h.01"/>',
    columns: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/><path d="M15 3v18"/>',
    sort: '<path d="M4 6h10"/><path d="M4 12h7"/><path d="M4 18h4"/><path d="M18 8v12"/><path d="m15 17 3 3 3-3"/>',

    // ── account ────────────────────────────────────────────
    // The signed-OUT avatar. A signed-in one is an initial, not a glyph —
    // drawn by main.js in CSS, because a letter is not an icon.
    user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    logOut: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><path d="M21 12H9"/>',

    // ── phone toolbar ──────────────────────────────────────
    // The bar has no room for words on every slot, so these four carry a
    // whole sheet each. Same Lucide set, same 24×24 frame.
    more: '<circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/><circle cx="5" cy="12" r="1.4"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    pen: '<path d="M21.2 6.8a2.82 2.82 0 0 0-4-4L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.63l4.36-1.33a2 2 0 0 0 .83-.5z"/><path d="m15 5 4 4"/>',
  };

  /* ── chart layout glyphs, generated ─────────────────────────────────────
   * One frame, split: the set reads as a family because only the divider
   * changes — same 18×16 frame, same radius, and the split drawn at the
   * icon's own stroke weight (the CSS glyphs these replace used a flat
   * 1.5px block that sat heavier than the border it crossed, and their
   * 15×13 box was a different aspect from every other icon in the header).
   *
   * With forty-two layouts these are DERIVED rather than drawn. Hand-drawing
   * them would mean forty-two chances for a glyph to disagree with the grid
   * it claims to describe; instead the glyph is read off the same
   * grid-of-letters the layout is defined by, drawing a divider wherever two
   * neighbouring cells belong to different panes and nowhere else. A layout
   * and its icon cannot drift apart because there is only one of them.
   */
  const FX = 3, FY = 4, FW = 18, FH = 16;
  const r2 = (n) => Math.round(n * 100) / 100;

  /** layoutSvg(["ab","ac"]) → an <svg> string drawn from that grid. */
  function layoutSvg(spec, cls = "") {
    const rows = spec.length, cols = spec[0].length;
    const cw = FW / cols, ch = FH / rows;
    const paths = [];

    // vertical dividers — merge consecutive rows into one stroke so a full
    // -height split is one path, not four stacked segments meeting end to end
    for (let c = 1; c < cols; c++) {
      let run = null;
      for (let r = 0; r <= rows; r++) {
        const split = r < rows && spec[r][c - 1] !== spec[r][c];
        if (split && run === null) run = r;
        if (!split && run !== null) {
          const x = r2(FX + c * cw);
          paths.push(`<path d="M${x} ${r2(FY + run * ch)}V${r2(FY + r * ch)}"/>`);
          run = null;
        }
      }
    }
    // horizontal dividers — same, merged across columns
    for (let r = 1; r < rows; r++) {
      let run = null;
      for (let c = 0; c <= cols; c++) {
        const split = c < cols && spec[r - 1][c] !== spec[r][c];
        if (split && run === null) run = c;
        if (!split && run !== null) {
          const y = r2(FY + r * ch);
          paths.push(`<path d="M${r2(FX + run * cw)} ${y}H${r2(FX + c * cw)}"/>`);
          run = null;
        }
      }
    }

    const klass = cls ? `icon ${cls}` : "icon";
    return `<svg class="${klass}" viewBox="0 0 24 24" aria-hidden="true">`
      + `<rect x="${FX}" y="${FY}" width="${FW}" height="${FH}" rx="2"/>`
      + paths.join("") + `</svg>`;
  }

  /** svg(name, extraClass) → an <svg class="icon …"> string. */
  function svg(name, cls = "") {
    const body = P[name];
    if (!body) throw new Error(`Icons: unknown icon "${name}"`);
    const klass = cls ? `icon ${cls}` : "icon";
    return `<svg class="${klass}" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
  }

  /* ── the search field's leading mark ────────────────────────────────────
   * Every "type to filter this list" box in the app wears the SAME
   * magnifier, and it is the only thing that says the box is focused (the
   * outline that used to do that job is gone — see .searchfield in the
   * stylesheet). So the glyph cannot be pasted per field: one that drifted
   * would take an active state with it.
   *
   * `field()` is for markup built in JS; `mountSearchFields()` walks the
   * static markup in index.html and puts the same glyph in the same slot.
   * Both write `svg.icon.sm` as the wrapper's FIRST child, which is what
   * the stylesheet positions. */
  const field = (inputHTML, cls = "") =>
    `<div class="searchfield${cls ? " " + cls : ""}">${svg("search", "sm")}${inputHTML}</div>`;

  function mountSearchFields(root = document) {
    for (const f of root.querySelectorAll(".searchfield")) {
      if (!f.querySelector(":scope > svg")) {
        f.insertAdjacentHTML("afterbegin", svg("search", "sm"));
      }
    }
  }

  return { svg, layoutSvg, field, mountSearchFields, paths: P };
})();

/* This file is loaded after the markup it decorates, so the fields exist by
 * the time the document is parsed — but not yet at THIS line, which runs
 * mid-parse. Waiting for the event is the difference between decorating the
 * whole page and decorating whatever happened to be above this <script>. */
if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => Icons.mountSearchFields());
  } else {
    Icons.mountSearchFields();
  }
}
