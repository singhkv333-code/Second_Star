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
    /* ── the ratio family ──────────────────────────────────
     * Sixteen glyphs on one grammar, so the flyout can be read down its left
     * edge rather than by its labels. A LADDER of horizontals means levels in
     * price; a COMB of verticals means levels in time; RAYS from a corner
     * mean a slope; RINGS mean distance from a pivot. The dot marks the
     * anchor a construction is built from, wherever a tool has one that the
     * eye would otherwise have to guess at. */
    // ladder + the extra leg that makes it an extension rather than a retrace
    fibExtension: '<path d="M3 8h18"/><path d="M3 12h18"/><path d="M3 16h18"/><path d="M3 20 8 4l5 9"/>',
    // the same ladder, tilted onto a trend
    fibChannel: '<path d="M3 21 21 11"/><path d="M3 17.5 21 7.5"/><path d="M3 15 21 5"/><path d="M3 13 21 3"/>',
    // a comb — levels in TIME, not price, spaced the way the sequence is
    fibTimeZone: '<path d="M3 3v18"/><path d="M6 3v18"/><path d="M10 3v18"/><path d="M16 3v18"/><circle cx="3" cy="21" r="1.6"/>',
    // rays off one corner, with the box they divide
    fibSpeedFan: '<path d="M3 21 21 3"/><path d="M3 21 21 9"/><path d="M3 21 21 15"/><path d="M3 21 15 3"/><path d="M3 3v18h18" stroke-dasharray="2 2"/>',
    // the comb again, but counted from a measured leg
    fibTimeExtension: '<path d="M3 20 9 8l4 6"/><path d="M13 3v18"/><path d="M17 3v18"/><path d="M21 3v18"/>',
    fibCircles: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5.5"/><circle cx="12" cy="12" r="2.2"/>',
    fibSpiral: '<path d="M14.6 20.4a8.8 8.8 0 1 0-8.5-8.9 5.4 5.4 0 1 0 5.6 5.2 3.3 3.3 0 1 1-3.2-3.2"/>',
    // half-rings opening the way the move went
    fibArcs: '<path d="M3 21a18 18 0 0 0 18-18"/><path d="M3 21a12 12 0 0 0 12-12"/><path d="M3 21a6 6 0 0 0 6-6"/><circle cx="3" cy="21" r="1.6"/>',
    // two rays and the rungs between them
    fibWedge: '<path d="M3 21 21 5"/><path d="M3 21h18"/><path d="M7.4 21a5 5 0 0 0 3.4-4"/><path d="M12.6 21a11 11 0 0 0 6.4-8.6"/><circle cx="3" cy="21" r="1.6"/>',
    // a pitchfork's handle, with a full ladder of tines
    pitchfan: '<path d="M3 21 21 3"/><path d="M3 21 21 8"/><path d="M3 21 21 13"/><path d="M3 21 21 18"/><path d="M3 21 16 3"/><circle cx="3" cy="21" r="1.7"/>',
    // ── gann: the grid, and what is drawn over it ─────────
    gannBox: '<rect x="3" y="4" width="18" height="16"/><path d="M9 4v16"/><path d="M15 4v16"/><path d="M3 9.33h18"/><path d="M3 14.67h18"/>',
    gannSquare: '<rect x="3" y="4" width="18" height="16"/><path d="M9 4v16"/><path d="M15 4v16"/><path d="M3 9.33h18"/><path d="M3 14.67h18"/><path d="M3 20 21 4"/><path d="M3 20a17 17 0 0 0 17-16"/>',
    // the same figure with ONE anchor — the dot is the whole difference
    gannSquareFixed: '<rect x="3" y="4" width="18" height="16"/><path d="M9 4v16"/><path d="M15 4v16"/><path d="M3 9.33h18"/><path d="M3 14.67h18"/><path d="M3 20 21 4"/><circle cx="3" cy="20" r="2.1"/>',
    gannFan: '<path d="M3 21 21 3"/><path d="M3 21 21 10"/><path d="M3 21 21 16"/><path d="M3 21 12 3"/><path d="M3 21 17 3"/>',
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

    /* ── what an alert operator DOES ───────────────────────────────────────
     * One picture per operator, drawn to the same grammar so the list can be
     * read down its left edge: the dashed horizontal IS the level, and the
     * solid stroke is the price doing the thing being described. Direction is
     * carried by an arrow head, never by colour — the menu is monochrome and
     * these have to survive it.
     */
    // either direction through the level: one stroke, a head at each end
    opCross: '<path d="M3 12h18" stroke-dasharray="2.5 2.5"/>' +
             '<path d="M12 3.5v17"/><path d="M9 6.5 12 3.5l3 3"/>' +
             '<path d="M9 17.5l3 3 3-3"/>',
    opCrossUp: '<path d="M3 12h18" stroke-dasharray="2.5 2.5"/>' +
               '<path d="M6.5 19 16.5 6"/><path d="M16.5 6h-4"/>' +
               '<path d="M16.5 6v4"/>',
    opCrossDown: '<path d="M3 12h18" stroke-dasharray="2.5 2.5"/>' +
                 '<path d="M6.5 5 16.5 18"/><path d="M16.5 18h-4"/>' +
                 '<path d="M16.5 18v-4"/>',
    /* A STATE, not an event, so nothing crosses anything: the level, and price
     * standing entirely on one side of it. Drawn as a chevron rather than a
     * candle zigzag — measured at 17px, a zigzag over a dashed line turns into
     * a single grey smudge and the two operators stop being distinguishable. */
    opAbove: '<path d="M3 16.5h18" stroke-dasharray="2.5 2.5"/>' +
             '<path d="M5.5 11 12 4.5 18.5 11"/>',
    opBelow: '<path d="M3 7.5h18" stroke-dasharray="2.5 2.5"/>' +
             '<path d="M5.5 13 12 19.5 18.5 13"/>',
    // a MOVE over a window: the span is the stroke, the head says which way
    opRise: '<path d="M4 19 12 8l4 4 4-7"/><path d="M20 5h-3.6"/><path d="M20 5v3.6"/>',
    opFall: '<path d="M4 5 12 16l4-4 4 7"/><path d="M20 19h-3.6"/><path d="M20 19v-3.6"/>',
    opMove: '<path d="M12 4v16"/><path d="M8.4 7.6 12 4l3.6 3.6"/>' +
            '<path d="M8.4 16.4 12 20l3.6-3.6"/>',
    /* A BAND as a solid rectangle, not two dashed rules — at this size the two
     * rules merge and the band disappears. The arrow says in or out. */
    opEnter: '<rect x="3" y="9.5" width="18" height="7" rx="1.5"/>' +
             '<path d="M12 2.5v5.5"/><path d="M9.2 5.2 12 8l2.8-2.8"/>',
    opExit: '<rect x="3" y="13" width="18" height="7" rx="1.5"/>' +
            '<path d="M12 10.5V5"/><path d="M9.2 7.8 12 5l2.8 2.8"/>',
    // a shape finishing: bars, and a tick that it did
    opCompletes: '<path d="M4.5 16V7"/><path d="M9.5 16v-5"/><path d="M14.5 16V5"/>' +
                 '<path d="M11.5 19.5 14 22l5.5-6"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
    arrowUp: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    arrowDown: '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    camera: '<path d="M14.5 4h-5L7.2 6.8H4a2 2 0 0 0-2 2V18a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8.8a2 2 0 0 0-2-2h-3.2L14.5 4z"/><circle cx="12" cy="13" r="3.2"/>',
    // voice input, and the same glyph struck through for the failure flash —
    // the pair Pivot's composer uses, so the two products say it the same way
    mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>',
    micOff: '<path d="M2 2 22 22"/><path d="M18.9 13.2A7 7 0 0 0 19 12v-2"/><path d="M5 10v2a7 7 0 0 0 11.9 5"/><path d="M15 9.3V5a3 3 0 0 0-5.7-1.3"/><path d="M9 9v3a3 3 0 0 0 5.1 2.1"/><path d="M12 19v3"/>',
    // Lucide's Loader2 — the glyph Pivot spins while a recording uploads
    loader: '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
    eye: '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.5 13.5 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><path d="m2 2 20 20"/>',
    fileText: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
    eraser: '<path d="M21 21H8a2 2 0 0 1-1.42-.587l-3.994-3.999a2 2 0 0 1 0-2.828l10-10a2 2 0 0 1 2.829 0l5.999 6a2 2 0 0 1 0 2.828L12.834 21"/><path d="m5.082 11.09 8.828 8.828"/>',
    copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
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
    /* Lucide's circled i, for a control that carries a caveat too long to
     * put in its label. It replaces a hand-built glyph — a 14px div with a
     * border-radius and an italic letter "i" typed into it — which was the
     * one mark in the app that was not drawn on this grid: it sat a hair
     * off the cap height beside it, took its weight from the font rather
     * than from the stroke, and could not follow the icon sizes. */
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',

    // ── account ────────────────────────────────────────────
    // The signed-OUT avatar. A signed-in one is an initial, not a glyph —
    // drawn by main.js in CSS, because a letter is not an icon.
    user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    logOut: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><path d="M21 12H9"/>',
    // Lucide's keyboard, for the shortcuts row: a key board reads as one at
    // 14px only because the caps are dots and the space bar is the one bar.
    keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/>'
      + '<path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8"/>',

    // ── phone toolbar ──────────────────────────────────────
    // The bar has no room for words on every slot, so these four carry a
    // whole sheet each. Same Lucide set, same 24×24 frame.
    more: '<circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/><circle cx="5" cy="12" r="1.4"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    pen: '<path d="M21.2 6.8a2.82 2.82 0 0 0-4-4L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.63l4.36-1.33a2 2 0 0 0 .83-.5z"/><path d="m15 5 4 4"/>',

    /* ── the chart's context menu ───────────────────────────
     * Four rows in that menu had no glyph in the set, and a menu where some
     * rows carry a mark and others do not reads as two menus stacked. Same
     * Lucide family, same 24 grid, so they sit on the cap height beside the
     * ones already here. */
    // attaching a POINT to the conversation — the same verb the composer's
    // chips already perform, so it wears the label a chip is
    tag: '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z"/>'
      + '<circle cx="7.4" cy="7.4" r="1.1"/>',
    // a list, gaining a row: `list` alone is the alert log's glyph and the
    // plus is the whole difference between reading one and adding to it
    listPlus: '<path d="M11 12H3"/><path d="M16 6H3"/><path d="M16 18H3"/>'
      + '<path d="M18 9v6"/><path d="M21 12h-6"/>',
    // a drawing pinned in place — the shackle is closed, which is the state
    // the row is switching INTO when it is not yet ticked
    lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    // the record behind a shape: bars against an axis, which is what a
    // hit-rate answer actually looks like when it comes back
    barChart: '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9"/>'
      + '<path d="M13 17V5"/><path d="M8 17v-3"/>',
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

  /* ── template tiles: a SECOND family, with different rules ─────────────
   *
   * The glyphs above are 24×24 stroke-only signage — they label a control you
   * already know how to use. These are something else: the twelve openings on
   * an empty chat, where the picture has to teach what the thing DOES before
   * the user has any vocabulary for it. So they are miniature illustrations,
   * not symbols, and they follow the convention that reads as "template
   * picker" everywhere it appears (Claude Design, Notion, Figma):
   *
   *   · 40×40 viewBox — four times the area of a glyph, which is what buys
   *     room for a drawing instead of a pictogram
   *   · 1.6 stroke in currentColor, round caps and joins
   *   · DUOTONE: exactly ONE accent per tile, marked `class="a"` (stroke) or
   *     `class="af"` (fill). One is the whole trick — the accent is what the
   *     tile is about, and a second one turns a drawing into a diagram.
   *     Colour comes from CSS so the accent follows the theme.
   *   · Every tile is drawn ON a chart, because that is charto's one subject.
   *     A generic magnifier for "screen" or a generic bell for "alert" would
   *     be an icon from any app; a magnifier over candles is from this one.
   *
   * Kept in this file rather than a new one because "where the icons live"
   * should have a single answer, but deliberately in its own map: mixing a
   * 40-unit path into P would silently render at 24 and nobody would know
   * why it looked thin.
   */
  const T = {
    // support and resistance: the candles are the evidence, the two flat
    // lines are the answer
    levels: '<path d="M7 32h26"/>'
      + '<path d="M11 15v14M18 12v17M25 17v12M31 14v15"/>'
      + '<rect x="9" y="19" width="4" height="6" rx="1"/>'
      + '<rect x="16" y="16" width="4" height="9" rx="1"/>'
      + '<rect x="23" y="20" width="4" height="5" rx="1"/>'
      + '<rect x="29" y="18" width="4" height="7" rx="1"/>'
      + '<path class="a" d="M6 14.5h28M6 28h28" stroke-dasharray="3 2.5"/>',
    // a formation: two converging rails and the apex they resolve at
    patterns: '<path d="M7 32h26"/>'
      + '<path d="M10 27.5 14 17.6l4 7.9 4-6 4 3.5 3.5-1.2"/>'
      + '<path class="a" d="M9 15 31 22M9 29l22-7"/>',
    // the line you draw, and the two bars it is pinned to
    trendlines: '<path d="M7 32h26"/>'
      + '<path d="M11 20v9M18 16v13M25 19v10M31 12v17"/>'
      + '<path class="a" d="M10 28.5 32 13"/>',
    // price above, its own pane below — the divider IS the idea
    indicators: '<path d="M7 17l5-5 5 4 5-7 5 6 4-3"/>'
      + '<path d="M6 22h28" opacity=".55"/>'
      + '<path class="a" d="M7 30c3 0 3-5 6-5s3 6 6 6 3-7 6-7 3 4 6 4"/>',
    // volume at price: the bars are the point, the longest one is the answer
    volumeProfile: '<path d="M7 32h26"/>'
      + '<path d="M10 15v14M15 12v17M20 18v11"/>'
      + '<path d="M24 26h6M24 22h4M24 14h5"/>'
      + '<path class="a" d="M24 18h11"/>',
    // the jump, and the reason attached to it
    whyMoved: '<path d="M7 32h26"/>'
      + '<path d="M9 28l4-1.5 4 1"/>'
      + '<path class="a" d="M17 27.5 23 15"/>'
      + '<path d="M23 15l4 3 5-2.5"/>'
      + '<path class="af" d="M28.6 8l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z" stroke="none"/>',
    // target above, entry, stop below — the only tile that earns two colours,
    // because up and down are not decoration here, they are the two outcomes
    planTrade: '<path d="M7 32h26"/>'
      + '<path d="M13 15v14M20 12v17M27 18v11"/>'
      + '<path class="up" d="M8 12h26"/>'
      + '<path d="M8 21h26" stroke-dasharray="3 2.5"/>'
      + '<path class="down" d="M8 28h26"/>',
    // does the shape actually work: the shape, then the record
    evidence: '<path d="M7 32h26"/>'
      + '<path d="M8 22l5-6 5 4 5-7"/>'
      + '<path class="a" d="M23 29v-6M28 29v-11M33 29v-8"/>'
      + '<path d="M21 32h14" opacity=".55"/>',
    // the same setup, across the market
    screen: '<rect x="6" y="8" width="28" height="24" rx="3"/>'
      + '<path d="M6 15h28M15 15v17M25 15v17" opacity=".55"/>'
      + '<path d="M8 27l3-3 2 2M17 26l3-4 2 3M27 28l2-3 2 2"/>'
      + '<rect class="af" x="15" y="15" width="10" height="8.5" rx="0" stroke="none" opacity=".16"/>'
      + '<path class="a" d="M17 21l3-4 2 3"/>',
    // two instruments, one origin, and the gap that opens
    compare: '<path d="M7 32h26"/>'
      + '<path d="M8 26l6-3 6-6 6-4 6-2"/>'
      + '<path class="a" d="M8 26l6 1 6 3 6-1 6 2"/>',
    // the level, and the moment it is crossed
    alert: '<path d="M7 32h26"/>'
      + '<path d="M7 27l6-2 5-5 5-3"/>'
      + '<path d="M6 20h28" stroke-dasharray="3 2.5"/>'
      + '<path class="a" d="M27 19a4.5 4.5 0 0 1 9 0c0 4 1.2 5 1.2 5H25.8s1.2-1 1.2-5z"/>'
      + '<path class="a" d="M30 27.5a1.8 1.8 0 0 0 3 0"/>',
    // a viewfinder around the chart: the BRACKETS carry the accent, because
    // the framing is the action — what is inside them is only the chart again
    screenshot: '<path class="a" d="M7 14.5V9.5A2 2 0 0 1 9 7.5h5"/>'
      + '<path class="a" d="M33 14.5V9.5a2 2 0 0 0-2-2h-5"/>'
      + '<path class="a" d="M7 25.5v5a2 2 0 0 0 2 2h5"/>'
      + '<path class="a" d="M33 25.5v5a2 2 0 0 1-2 2h-5"/>'
      + '<path d="M11 26h18"/>'
      + '<path d="M12 23l4-6 4 4 4-7 4 5"/>',
    // the print, and the bar that reacted to it
    earnings: '<path d="M7 32h26"/>'
      + '<path d="M11 20v9M18 22v7M31 14v15"/>'
      + '<rect x="9" y="22" width="4" height="5" rx="1"/>'
      + '<rect x="16" y="24" width="4" height="4" rx="1"/>'
      + '<rect x="29" y="17" width="4" height="9" rx="1"/>'
      + '<path class="a" d="M25 11v21" stroke-dasharray="3 2.5"/>'
      + '<rect class="af" x="21.5" y="6.5" width="7" height="5" rx="1.4" stroke="none"/>',
  };

  /** tile(name) → the 40×40 template illustration. */
  function tile(name) {
    const body = T[name];
    if (!body) throw new Error(`Icons: unknown tile "${name}"`);
    return `<svg class="tile-art" viewBox="0 0 40 40" aria-hidden="true">${body}</svg>`;
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

  return { svg, layoutSvg, tile, field, mountSearchFields, paths: P, tiles: T };
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
