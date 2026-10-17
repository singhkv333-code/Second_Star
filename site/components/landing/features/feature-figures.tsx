/**
 * feature-figures — the two slots that argue by drawing rather than by pane.
 *
 * Most of this section quotes the product literally: a real panel, real
 * numbers, the app's own chrome. Two claims cannot be made that way. "A
 * complete trading workspace" is about how much fits at once, and "Journal in
 * context" is about three things being in one place — neither survives being
 * shown as ONE pane, and showing four panes at postage-stamp size proves the
 * opposite of the claim. So these two are figures: a drawing, a name, a line.
 *
 * The drawings are not new art. `preview/js/icons.js` keeps a SECOND icon map
 * beside the 24-unit interface set — `T`, the 40-unit tiles behind the chat
 * template grid — under house rules worth repeating, because everything below
 * obeys them:
 *
 *   · 40×40, "room for a drawing instead of a pictogram"
 *   · stroke only, currentColor, round caps and joins, weight set ONCE by CSS
 *     so no tile can drift heavier than its neighbours
 *   · DUOTONE: exactly one accent per tile, `.a` stroked or `.af` filled.
 *     "One is the whole trick — the accent is what the tile is about, and a
 *     second one turns a drawing into a diagram."
 *   · every tile is drawn ON a chart, because that is charto's one subject.
 *     A generic magnifier for "screen" would be an icon from any app; a
 *     magnifier over candles is from this one.
 *
 * Three of the six below are lifted verbatim from that map. The other three —
 * `layouts`, `fundamentals` and `notes` — are drawn here to the same rules, on
 * the same 40-unit grid, because the template grid never needed them; each one
 * says below why the nearest existing tile was not the answer, and all three
 * belong upstream if the app ever wants them.
 */
"use client";

import * as React from "react";

/* ── the drawings ────────────────────────────────────────────────────────── */

/**
 * 40-unit tile bodies. Keys marked VERBATIM are byte-for-byte `Icons.T` from
 * `preview/js/icons.js`; changing one here without changing it there is how
 * two copies of a design system start.
 */
const TILES = {
  /* NEW. The split IS the subject, so the split carries the accent: one frame
     quartered, and a different instrument drawn in each quarter. Deliberately
     not the `screen` tile's shape — that one is a table with a row picked out,
     this one is four charts at once.
     Drawn out to the 4/36 margins rather than the 6/34 the framed tiles use:
     a rectangle reads smaller than an open drawing of the same width, and at
     the landing size this one has to hold a line beside two that fill the box. */
  layouts:
    '<rect x="4" y="7" width="32" height="26" rx="3"/>' +
    '<path d="M7.4 16.4 10.8 12.8l2.8 2.7 3.1-4.2"/>' +
    '<path d="M23.4 17 26.6 14.5l2.9 1.8 2.9-4"/>' +
    '<path d="M7.4 29.4 10.8 25.6l2.8 2.3 3.1-3.2"/>' +
    '<path d="M23.4 30 26.6 27.6l2.9 1.7 2.9-3.6"/>' +
    '<path class="a" d="M20 7v26M4 20h32"/>',

  /* VERBATIM — `indicators`: price above, its own pane below, the divider IS
     the idea, and the oscillator underneath carries the accent. */
  indicators:
    '<path d="M7 17l5-5 5 4 5-7 5 6 4-3"/>' +
    '<path d="M6 22h28" opacity=".55"/>' +
    '<path class="a" d="M7 30c3 0 3-5 6-5s3 6 6 6 3-7 6-7 3 4 6 4"/>',

  /* NEW. `earnings` was the obvious verbatim choice here and it was the wrong
     one twice over: it draws an EVENT — the print and the bar that reacted to
     it — where this column is about the statements themselves, and its one
     accent is a FILL, which at 132px stops being a marker and becomes the
     loudest object in the row. This says what the column says instead: the
     company's numbers standing beside the chart rather than in another tab,
     with the sheet as the accent. */
  fundamentals:
    '<path d="M5 31h14" opacity=".55"/>' +
    '<path d="M6 25.5 10 19.5l4 4 4.5-7.5"/>' +
    '<rect class="a" x="22.5" y="8" width="12.5" height="24" rx="2"/>' +
    '<path class="a" d="M25.6 13.5h6.3M25.6 18h6.3M25.6 22.5h4.2"/>',

  /* VERBATIM — `planTrade`: target above, entry, stop below. Its own comment
     upstream: "the only tile that earns two colours, because up and down are
     not decoration here, they are the two outcomes." */
  planTrade:
    '<path d="M7 32h26"/>' +
    '<path d="M13 15v14M20 12v17M27 18v11"/>' +
    '<path class="up" d="M8 12h26"/>' +
    '<path d="M8 21h26" stroke-dasharray="3 2.5"/>' +
    '<path class="down" d="M8 28h26"/>',

  /* NEW. A note is only a note when it is attached to something: bars, a
     leader, and the written card at the end of it. The card is the accent —
     the bars underneath are the same bars every other tile draws. */
  notes:
    '<path d="M7 32h26"/>' +
    '<path d="M11 21v8M18 17v12M25 23v6"/>' +
    '<rect x="9" y="23" width="4" height="4" rx="1"/>' +
    '<rect x="16" y="20" width="4" height="6" rx="1"/>' +
    '<rect x="23" y="24.5" width="4" height="3.5" rx="1"/>' +
    '<path d="M21.4 14.8 18.4 17" opacity=".55"/>' +
    '<rect class="a" x="21.5" y="6" width="12.5" height="9" rx="1.6"/>' +
    '<path class="a" d="M24.5 9.4h6.5M24.5 11.9h4"/>',

  /* VERBATIM — `screenshot`: a viewfinder around the chart, the BRACKETS
     carrying the accent "because the framing is the action — what is inside
     them is only the chart again". Which is exactly what a journalled chart
     is: the frame you kept, around the chart you already had. */
  screenshot:
    '<path class="a" d="M7 14.5V9.5A2 2 0 0 1 9 7.5h5"/>' +
    '<path class="a" d="M33 14.5V9.5a2 2 0 0 0-2-2h-5"/>' +
    '<path class="a" d="M7 25.5v5a2 2 0 0 0 2 2h5"/>' +
    '<path class="a" d="M33 25.5v5a2 2 0 0 1-2 2h-5"/>' +
    '<path d="M11 26h18"/>' +
    '<path d="M12 23l4-6 4 4 4-7 4 5"/>',
} as const;

export type TileName = keyof typeof TILES;

/**
 * `Icons.tile(name)` — the same 40-box, the same stroke geometry, sized and
 * weighted by CSS rather than by markup so the six stay one family.
 */
function Tile({ name }: { name: TileName }) {
  return (
    <svg
      className="cf-tile"
      viewBox="0 0 40 40"
      aria-hidden="true"
      focusable="false"
      dangerouslySetInnerHTML={{ __html: TILES[name] }}
    />
  );
}

/* ── the presentation ────────────────────────────────────────────────────── */

export type Figure = {
  tile: TileName;
  name: string;
  line: string;
};

/**
 * A divided row of figures. Vertical hairlines only — the columns are one
 * shelf, and a box around each would make three cards out of one claim.
 *
 * Each column reveals itself rather than riding in on the article: they enter
 * together, so the scroll layer's batch picks all three up in one group and
 * staggers them left to right.
 */
export function FigureRow({ figures }: { figures: readonly Figure[] }) {
  return (
    <div className="cf-fig-row" data-count={figures.length}>
      {figures.map((f) => (
        <div className="cf-fig" key={f.name} data-reveal>
          <div className="cf-fig-art">
            <Tile name={f.tile} />
          </div>
          <h4>{f.name}</h4>
          <p>{f.line}</p>
        </div>
      ))}
    </div>
  );
}
