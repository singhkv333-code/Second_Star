/* Charto preview — the panels a tool prints beside its reply.
 *
 * A reply is prose. Some answers are not: a full pattern sweep comes back as
 * a market-structure read, several formations each with a status and one
 * measurement, and a dozen candlestick bars. Written out as sentences that is
 * a transcript rather than an answer — and left out, the reply has quietly
 * dropped the measurements the question asked for. The card is the third
 * option: the tool prints what it MEASURED, and the paragraph beside it is
 * free to say what that means.
 *
 * ── the rule this module lives under ────────────────────────────────────
 * It renders. It does not decide. Every string and number here came off the
 * `card` object the dataserver built from its own tool result (see
 * `_patterns_card`), so the panel and the prose are reading one source and
 * cannot state two different figures. Nothing is computed on this side —
 * not a count, not a total, not a "so that means". The two things it is
 * allowed to do to a value are FORMAT it (a price into the symbol's
 * currency, a timestamp into the app's short form) and DROP it: a missing
 * measurement takes its whole row with it rather than rendering as "NaN" or
 * "undefined · confirmed", exactly as the provenance card does.
 *
 * ── why the rows point back at the chart ────────────────────────────────
 * Every drawn object already has a scene id, and hovering a mention of one in
 * the thread already lights it up on the candles (main.js `indexChatRefs`).
 * A card row is a mention with better manners, so it carries the same
 * `data-ann` handle and gets that link for free. A row with no handle was
 * never drawn — and says so by being quieter, not by pretending.
 */
"use strict";

const Cards = (() => {
  const esc = (s) => String(s == null ? "" : s)
    .replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
                                  '"': "&quot;" }[c]));

  /* The dataserver stamps every time as "24 Jul 2026 15:15" — that format is
   * the contract (`_ist` / `_parse_ist`), so it is parsed rather than guessed
   * at, and never re-derived from an epoch on this side: the bars sit on the
   * exchange's clock and the browser does not. */
  const STAMP = /^(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})(?:\s+(\d{1,2}:\d{2}))?$/;
  const parse = (s) => STAMP.exec(String(s || "").trim());

  /** "24 Jul 2026 15:15" → "24 Jul 15:15". The year is the one field a reader
   *  of a chart they are looking at already knows. */
  function when(s) {
    const m = parse(s);
    if (!m) return String(s || "");
    return `${m[1]} ${m[2]}${m[4] ? " " + m[4] : ""}`;
  }

  /** A formation's window, closed up as far as the two ends allow:
   *  "16–28 Jul" inside one month, "28 Jun – 3 Jul" across two, and
   *  "28 Jul 09:15 → 15:15" when the whole thing lived inside one session. */
  function span(from, to) {
    const a = parse(from), b = parse(to);
    if (!a || !b) return [when(from), when(to)].filter(Boolean).join(" → ");
    if (a[1] === b[1] && a[2] === b[2] && a[3] === b[3]) {
      return a[4] && b[4] ? `${a[1]} ${a[2]} ${a[4]} → ${b[4]}` : `${a[1]} ${a[2]}`;
    }
    if (a[2] === b[2] && a[3] === b[3]) return `${a[1]}–${b[1]} ${a[2]}`;
    return `${a[1]} ${a[2]} – ${b[1]} ${b[2]}`;
  }

  /** Prices in the instrument's own currency and grouping — a card about a
   *  crypto pane must not print ₹, and must not group six figures the Indian
   *  way. Sym.of is the one place in the app that knows which. */
  function money(sym, v) {
    if (v == null || !Number.isFinite(Number(v))) return "";
    return Sym.of(sym).price(Number(v),
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  const TONE = { up: "up", down: "down", bullish: "up", bearish: "down" };
  const cap = (s) => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);

  /* A status is a BADGE, and this map holds only the two that are SETTLED.
   * "Confirmed" is a level that broke — a fact with a date behind it, in the
   * shape's own favour. "Broken" is the same event read the other way: a
   * trendline price has closed through is not a line any more, and saying so
   * in red is the one caution this panel raises.
   *
   * Everything else is a shape still open, and what that should look like
   * depends on the shape, so each renderer supplies its own fallback: an
   * unresolved formation takes the annotation amber (not the candle red — an
   * unresolved wedge is not a bearish wedge), while a trendline that is
   * simply intact takes the plain grey, because still holding is the
   * unremarkable state of a line rather than a caveat about it. */
  const BADGE = { confirmed: "ok", broken: "bad" };

  /** One row of a list: when it happened, what it was, and — where there is
   *  one — the price it happened at, closed against the card's right edge so
   *  every figure in the panel stacks into one column. */
  function row(cls, ann, left, mid, right) {
    return `<div class="scan-row${cls ? " " + cls : ""}"`
      + (ann ? ` data-ann="${esc(ann)}"` : "")
      + `><span class="when">${esc(left)}</span>`
      + `<span class="what">${mid}</span>`
      + (right ? `<b class="num">${esc(right)}</b>` : "")
      + `</div>`;
  }

  /** A section, or nothing at all. An empty heading over an empty list is a
   *  statement that something is missing; a section that simply isn't there
   *  is the same statement without the furniture. */
  function section(title, note, body) {
    if (!body) return "";
    return `<section class="scan-sec"><h4>${esc(title)}`
      + (note ? `<span class="n">${esc(note)}</span>` : "")
      + `</h4>${body}</section>`;
  }

  /** The stat strip's cell: a label, the figure, and — where a figure needs
   *  one — the QUALIFIER that says how to read it. An ADX of 29 means nothing
   *  to most readers until it is placed against Wilder's bands; a return gap
   *  means nothing until the window it was measured over is named.
   *
   *  Hoisted out of the two panels that had a copy each. Eight cards with
   *  eight private versions of this would drift, and a stat that looked
   *  different from card to card would be telling the reader the cards are
   *  unrelated when they are the same instrument answered differently. */
  function stat(k, v, tone, q) {
    return `<div class="scan-stat"><span class="k">${esc(k)}</span>`
      + `<b class="v${tone ? " " + tone : ""}">${esc(v)}</b>`
      + (q ? `<span class="q">${esc(q)}</span>` : "") + `</div>`;
  }

  const n2 = (sym, v) => Sym.of(sym).num(v,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  /** A signed figure with its sign shown, because the sign IS the reading.
   *  The formatter already prints the minus; only the plus has to be added,
   *  and it is added rather than implied — "+12.25 pp" and "12.25 pp" are the
   *  same number and only one of them says which way. */
  const signed = (sym, v, unit) => {
    if (v == null || !Number.isFinite(Number(v))) return "";
    const s = n2(sym, v);
    return (Number(v) > 0 ? "+" : "") + s + (unit || "");
  };
  const way = (v) => (Number(v) > 0 ? "up" : Number(v) < 0 ? "down" : "");

  /* ── bars: one scale, drawn against each other ─────────────────────────
   *
   * The panel's one chart primitive, and it is horizontal for a reason that
   * is not taste: this column is user-resizable down to 340px, and a vertical
   * grouped chart at that width is four stubs under a legend. A horizontal
   * row keeps its label, its figure and its length legible at any width the
   * splitter allows, and stacks to as many series as the payload has.
   *
   * Everything about a bar's LENGTH is drawing; the figure printed beside it
   * is the payload's own string. Nothing here rounds, totals or rescales a
   * number into the text — a bar that disagreed with the number next to it
   * would be the panel arguing with itself.
   *
   *   · the scale is the largest ABSOLUTE value in the set, never 100. ADX
   *     rarely passes 40 and a 0-100 axis renders the whole comparison as
   *     stubs, which loses the one thing a bar row exists to show;
   *   · `signed` puts zero in the middle, because a set that contains −0.9%
   *     and +0.28% is two directions and not two sizes;
   *   · `tick` marks a threshold that is part of the reading (25 on ADX, 50
   *     on RSI) — it is disclosed in the section note, never a bare line.
   */
  function bars(items, opt) {
    const o = opt || {};
    const top = o.max
      || items.reduce((m, x) => Math.max(m, Math.abs(Number(x.value) || 0)), 0)
      || 1;
    const half = o.signed ? 50 : 100;
    const rows = items.map((x) => {
      const v = Number(x.value);
      if (!Number.isFinite(v)) return "";
      const w = Math.min(half, Math.abs(v) / top * half);
      const left = o.signed ? (v >= 0 ? 50 : 50 - w) : 0;
      const tone = x.tone || "";
      const tick = (o.tick != null && !o.signed && o.tick <= top)
        ? `<i class="tick" style="left:${(o.tick / top * 100).toFixed(1)}%"></i>`
        : "";
      return `<div class="scan-bar">`
        + `<span class="lb">${esc(x.label)}</span>`
        + `<b class="num${tone ? " tone-" + tone : ""}">${esc(x.text)}</b>`
        + `<span class="track${o.signed ? " signed" : ""}">`
        + `<i class="fill${tone ? " " + tone : ""}" `
        + `style="left:${left.toFixed(1)}%;width:${w.toFixed(1)}%"></i>`
        + tick + `</span></div>`;
    }).join("");
    return rows ? `<div class="scan-bars">${rows}</div>` : "";
  }

  /** Several bars that belong to one window, under its name. Used where the
   *  same measurement was taken at more than one interval: the interval is
   *  the thing being compared and it has to label the group, not each bar. */
  function group(label, note, body) {
    if (!body) return "";
    return `<div class="scan-group"><div class="gl">${esc(label)}`
      + (note ? `<span class="gn">${esc(note)}</span>` : "")
      + `</div>${body}</div>`;
  }

  /** +DI against −DI. Shared by the trend read and the studies panel, which
   *  are two tools reading ONE ADX result — two copies of this could quietly
   *  draw the same pair two ways.
   *
   *  Scaled to the larger of the pair rather than to 100 for the same reason
   *  every bar row here is: DI rarely reaches 50. */
  function diLegs(sym, di) {
    if (!di || di.plus == null || di.minus == null) return "";
    return bars([
      { label: "+DI", value: di.plus, text: n2(sym, di.plus), tone: "up" },
      { label: "−DI", value: di.minus, text: n2(sym, di.minus), tone: "down" },
    ]);
  }

  /** A price ladder: the levels around price, with price IN it rather than
   *  beside it. A list of levels above and a list below leaves the reader to
   *  work out which side they are standing on; one column, high to low, with
   *  the current row marked, is the same information already read. */
  function rung(cls, price, sym, label, note) {
    return `<div class="scan-rung${cls ? " " + cls : ""}">`
      + `<b class="px">${esc(price)}</b>`
      + `<span class="lb">${esc(label)}</span>`
      + (note ? `<span class="nt">${esc(note)}</span>` : "")
      + `</div>`;
  }

  /** What was looked for, and whether it was there. Both halves — a chip
   *  saying no deal was printed is a measurement of the same table that
   *  would have shown one, and a panel that listed only the hits would let
   *  the reader assume the misses were never checked. */
  function chips(items) {
    const out = (items || []).map((x) =>
      `<span class="scan-chip${x.found ? " found" : ""}">`
      + `<b>${esc(x.what)}</b>`
      + (x.detail ? `<span>${esc(x.detail)}</span>` : "")
      + `</span>`).join("");
    return out ? `<div class="scan-chips">${out}</div>` : "";
  }

  /** A small table, for the one shape a row cannot carry: the same quantity
   *  for several subjects across several windows. Kept rare on purpose —
   *  this panel is a reading surface, not a spreadsheet.
   *
   *  Cells go in as HTML so a figure can carry emphasis, which means the
   *  CALLER escapes them. Every caller in this file does. */
  function grid(head, rows) {
    if (!rows || !rows.length) return "";
    const th = head.map((h, i) =>
      `<span class="gh${i ? "" : " lead"}">${esc(h)}</span>`).join("");
    const tr = rows.map((r) => `<div class="scan-gr">` + r.map((cell, i) =>
      `<span class="gc${i ? "" : " lead"}">${cell}</span>`).join("") + `</div>`).join("");
    return `<div class="scan-grid" style="--cols:${head.length}">`
      + `<div class="scan-gr head">${th}</div>${tr}</div>`;
  }

  /** One line that qualifies the section above it — the band a tally would
   *  flip in, the timeframe a gap is widest on. It is a sentence the payload
   *  computed, not a conclusion drawn here, and it is boxed rather than
   *  bolded so it never reads as the panel's own verdict. */
  function callout(text) {
    return text ? `<div class="scan-call">${esc(text)}</div>` : "";
  }

  /** What was scanned, on the card's own bottom margin. Every panel in this
   *  app says where its numbers came from. */
  function foot(text) {
    return text ? `<div class="scan-foot">${esc(text)}</div>` : "";
  }

  // ── the pattern sweep ───────────────────────────────────────────
  function patterns(c) {
    const sym = c.symbol;
    const counts = c.counts || {};

    /* The four figures the sweep is ABOUT, before any of the detail. The
     * structure read is the only one with a direction, so it is the only one
     * that takes a colour — a count is not bullish.
     *
     * Both counts are of what the panel SHOWS — tiles and rows — never of
     * what the payload held. `candles_found` counts names and would read 20
     * over eighteen rows, because one bar can qualify under several; a
     * heading that argues with the list under it leaves the reader no way to
     * tell which half is wrong. */
    const stats = [
      c.trend ? stat("Structure", cap(c.trend), TONE[c.trend] || "") : "",
      stat("Bars scanned", Sym.of(sym).num(c.bars_scanned)),
      stat("Chart patterns", counts.chart_found ?? (c.chart_patterns || []).length),
      stat("Candle signals", counts.candle_bars ?? (c.candles || []).length),
    ].filter(Boolean).join("");

    /* Deliberately uncoloured. Every one of these rows begins with the word
     * "Bullish" or "Bearish" — painting the sentence its own colour says the
     * same thing twice, and five red lines in a column is a panel raising its
     * voice about a swing sequence. The one figure that takes a colour in
     * this card is the structure read at the top, because that is the only
     * place direction is the finding rather than the wording. */
    const events = (c.events || []).map((e) =>
      row("", null, when(e.t), esc(e.what), money(sym, e.price))).join("");

    /* A formation gets a tile rather than a row because it is four facts, not
     * one: what it is, when it ran, which way the textbook reads it, and the
     * single number that decides it. Stacked as rows those four would need
     * four columns and the panel would become a spreadsheet. */
    const tiles = (c.chart_patterns || []).map((p) => {
      const badge = p.status
        ? `<span class="scan-badge ${BADGE[p.status] || "open"}">${esc(cap(p.status))}</span>`
        : "";
      const sub = [span(p.from, p.to), p.bias && p.bias !== "neutral"
        ? `${p.bias} bias` : ""].filter(Boolean).join(" · ");
      // The measurement and what became of it are one sentence: a neckline
      // that broke is a different fact from a neckline that hasn't, and
      // printing the level with no verdict beside it invites the reader to
      // supply the optimistic one.
      const fact = p.measure
        ? `<span class="scan-fact">${esc(p.measure.label)} `
          + `<b>${esc(money(sym, p.measure.value))}</b>`
          + (p.broke_at ? `<span class="broke"> broke ${esc(when(p.broke_at))}</span>` : "")
          + `</span>`
        : "";
      return `<div class="scan-tile${p.drawn ? " drawn" : ""}"`
        + (p.drawn && p.id ? ` data-ann="${esc(p.id)}"` : "")
        + `>${badge}<b class="nm">${esc(cap(p.name))}</b>`
        + `<span class="sub">${esc(sub)}</span>${fact}</div>`;
    }).join("");

    /* A candle row closes on its BIAS where a structure row closes on its
     * price — that word is the whole reason to read this list right to left,
     * and it is the shape's textbook reading rather than a forecast, which is
     * why it is a word and not an arrow. */
    const candles = (c.candles || []).map((k) => {
      const t = TONE[k.bias];
      return `<div class="scan-row${k.drawn ? " drawn" : ""}"`
        + (k.ann ? ` data-ann="${esc(k.ann)}"` : "")
        + `><span class="when">${esc(when(k.t))}</span>`
        + `<span class="what">${esc(cap((k.names || []).join(", ")))}</span>`
        + `<span class="bias${t ? " tone-" + t : ""}">`
        + `${esc(cap(k.bias || "neutral"))}</span></div>`;
    }).join("");

    /* Found versus drawn, said out loud. The caps are real — three formations
     * and `mark_limit` bars — and a list of twelve above a chart showing five
     * marks would have the reader hunting for seven that were never put
     * there. The panel is the honest place to say so, once. */
    const drew = (found, shown, noun, verb) =>
      (found && shown < found) ? `${found} ${noun} · ${shown} ${verb}` : "";

    const more = (c.candles || []).length > 8
      ? `<button type="button" class="scan-more" data-more>`
        + `${(c.candles.length - 8)} more</button>` : "";

    return `<div class="scan-stats">${stats}</div>`
      + section("Structure events", "", events)
      + section("Chart patterns",
                drew(counts.chart_found, counts.chart_drawn, "found", "drawn"),
                tiles && `<div class="scan-tiles">${tiles}</div>`)
      + section("Candlestick patterns",
                drew(counts.candle_bars, counts.candles_marked, "bars", "marked"),
                candles && `<div class="scan-rows${more ? " capped" : ""}">${candles}</div>${more}`)
      + `<div class="scan-foot">${esc(c.bars_scanned)} ${esc(c.interval)} bars`
      + (c.window ? ` · ${esc(c.window)}` : "") + `</div>`;
  }

  // ── the trend read ──────────────────────────────────────────────────
  //
  // Four measurements of one chart that are allowed to disagree, and the
  // panel's whole job is to let them. They sit side by side in the stat
  // strip at equal weight — no arrow, no verdict line, nothing that folds
  // "sideways" and "bearish" into a third word — because which of the four
  // matters is what the paragraph beside this is for.
  function trend(c) {
    const sym = c.symbol;

    /* Structure and bias are the two readings with a direction, so they are
     * the two that take a colour. ADX has no side at all — a high reading in
     * a bearish market is a strong DOWNtrend — and painting it would be the
     * panel inventing one. The range is two prices.
     *
     * The third line is a QUALIFIER: the word or figure that says how to read
     * the number above it, and the reason neither has to go in the prose. An
     * ADX of 29 means nothing to most readers until it is placed against
     * Wilder's own bands, and a high and a low do not say where price is
     * sitting between them. Both come off the payload; neither is derived
     * here. */
    const adx = c.adx || {};
    const rng = c.range || {};
    const nx = (v) => Sym.of(sym).num(v, { maximumFractionDigits: 2 });
    const stats = [
      c.structure ? stat("Trend", cap(c.structure), TONE[c.structure] || "") : "",
      c.bias ? stat("Bias", cap(c.bias), TONE[c.bias] || "") : "",
      adx.value != null
        ? stat(adx.period ? `ADX ${adx.period}` : "ADX", n2(sym, adx.value),
               "", adx.strength) : "",
      // One currency symbol, on the pair. Two ends of one range are one
      // quantity — "₹1,249.8–₹1,345.8" prices them as if they were two.
      rng.low != null && rng.high != null
        ? stat("Range", `${Sym.of(sym).cur}${nx(rng.low)}–${nx(rng.high)}`, "",
               rng.position_pct != null
                 ? `${nx(rng.position_pct)}% of range` : "") : "",
    ].filter(Boolean).join("");

    /* +DI against −DI, drawn against each other rather than listed. The
     * comparison IS the reading — 8.59 means nothing except next to 35.88 —
     * and two numbers in a column leave the reader to do the subtraction. */
    const di = diLegs(sym, c.di);

    // Same rows, same derivation, same wording as the pattern sweep's panel —
    // both are reading one market_structure result (`_struct_events`).
    const events = (c.events || []).map((e) =>
      row("", null, when(e.t), esc(e.what), money(sym, e.price))).join("");

    /* A fitted line is four facts — which line, what earned it the name,
     * whether it is still holding, and the one level that says so. `touches`
     * is not decoration: three real swings on the line is the whole reason
     * this counts as a trendline rather than a slope somebody saw, and a
     * panel that hid it would be asking to be trusted. The level's LABEL
     * carries the tense — a line price is respecting projects near the latest
     * bar, one it has closed through was near it. */
    const lines = (c.trendlines || []).map((t) => {
      const cls = BADGE[t.status] || "";
      const badge = t.status
        ? `<span class="scan-badge${cls ? " " + cls : ""}">`
          + `${esc(cap(t.status))}</span>` : "";
      const sub = t.touches
        ? `<span class="sub">${esc(t.touches)} touches</span>` : "";
      const fact = t.level != null
        ? `<span class="scan-fact">${esc(t.level_label || "At")} `
          + `<b>${esc(money(sym, t.level))}</b></span>` : "";
      return `<div class="scan-tile${t.drawn ? " drawn" : ""}"`
        + (t.drawn && t.id ? ` data-ann="${esc(t.id)}"` : "")
        + `>${badge}<b class="nm">${esc(cap(t.name))}</b>${sub}${fact}</div>`;
    }).join("");

    return `<div class="scan-stats">${stats}</div>`
      + section("DI comparison", "", di)
      + section("Structure events", "", events)
      + section("Trendlines", "", lines && `<div class="scan-tiles">${lines}</div>`)
      + foot(`${c.bars_scanned} ${c.interval} bars`
             + (c.window ? ` · ${c.window}` : ""));
  }

  // ── the studies panel ───────────────────────────────────────────────
  //
  // Six studies in three families, and the families are allowed to disagree.
  // The panel is built so that they CAN: the overlays get rows tinted by the
  // side price is on, momentum gets tiles carrying the change behind each
  // reading, and ADX sits between them with no colour at all, because it has
  // no side. Nothing in here folds the three into a fourth word — the tally
  // at the top is a count of the overlays and says so in its own qualifier.
  function indicators(c) {
    const sym = c.symbol;
    const rsi = c.rsi || {}, adx = c.adx || {};
    const stats = [
      c.alignment ? stat("Trend", cap(c.alignment), TONE[c.alignment] || "",
                         (c.overlays || []).length
                           ? `${(c.overlays || []).length} overlays` : "") : "",
      c.momentum ? stat("Momentum", cap(c.momentum), "",
                        rsi.period ? `RSI ${rsi.period} band` : "") : "",
      rsi.value != null
        ? stat(rsi.period ? `RSI ${rsi.period}` : "RSI", n2(sym, rsi.value)) : "",
      adx.value != null
        ? stat(adx.period ? `ADX ${adx.period}` : "ADX", n2(sym, adx.value),
               "", adx.strength) : "",
      c.price != null ? stat("Price", money(sym, c.price)) : "",
    ].filter(Boolean).join("");

    /* One row per overlay, tinted by the side price is on. The tint is the
     * reading — three rows the same colour is the tally the strip reports,
     * seen rather than counted — and the row still says the word, because a
     * colour alone is a claim nobody can check and is invisible to a reader
     * who cannot see it. */
    const rows = (c.overlays || []).map((o) => {
      const t = o.side === "below" ? "down" : o.side === "above" ? "up" : "";
      return `<div class="scan-read${t ? " tone-" + t : ""}">`
        + `<b class="nm">${esc(o.name)}</b>`
        + `<span class="nt">${esc(o.note || "")}</span>`
        + `<b class="num">${esc(money(sym, o.value))}</b></div>`;
    }).join("");

    const zone = c.zone
      ? callout(`Flip band ${money(sym, c.zone.lo)} – ${money(sym, c.zone.hi)}`
                + (c.zone.note ? ` · ${c.zone.note}` : "")) : "";

    /* Each momentum reading is three things and needs all three: the state,
     * the figures it was read off, and the comparison that earned the state.
     * "Easing" on its own is an opinion; "−1.35 · less negative than the
     * prior bar" is the measurement the word is short for. */
    const tiles = (c.readings || []).map((r) => `<div class="scan-tile">`
      + (r.state ? `<span class="scan-badge ${r.tone === "up" ? "ok" : "bad"}">`
                   + `${esc(cap(r.state))}</span>` : "")
      + `<b class="nm">${esc(r.name)}</b>`
      + `<span class="scan-fact"><b>${esc(r.figures)}</b>`
      + (r.why ? ` · ${esc(r.why)}` : "") + `</span></div>`).join("");

    return `<div class="scan-stats">${stats}</div>`
      + section("Price against the overlays", "", rows && rows + zone)
      + section("Directional strength", "+DI against −DI", diLegs(sym, c.di))
      + section("Momentum readings", "",
                tiles && `<div class="scan-tiles">${tiles}</div>`)
      + foot(`${c.bars_scanned} ${c.interval} bars`
             + (c.window ? ` · ${c.window}` : ""));
  }

  // ── the confirmation checklist ──────────────────────────────────────
  //
  // The panel most at risk of being read as a recommendation, so it is built
  // to resist that reading: the stages are numbered rather than scored, the
  // weight beside each says what a break COSTS rather than how likely it is,
  // and every condition carries the reading it is at now — which is what
  // turns a wish list into a measurement. There is deliberately no progress
  // bar and no percentage anywhere in here.
  function confirmation(c) {
    const sym = c.symbol;
    const stats = [
      c.direction ? stat("Confirming", cap(c.direction),
                         TONE[c.direction] || "") : "",
      c.of ? stat("Conditions met", `${c.met} of ${c.of}`, "",
                  "measured, not scored") : "",
      c.price != null ? stat("Price", money(sym, c.price)) : "",
    ].filter(Boolean).join("");

    const steps = (c.stages || []).map((s) => {
      const at = s.lo != null && s.hi != null
        ? `${money(sym, s.lo)} – ${money(sym, s.hi)}`
        : s.price != null ? money(sym, s.price) : "";
      return `<div class="scan-step${s.met ? " met" : ""}">`
        + `<span class="no">${esc(s.step)}</span>`
        + `<b class="nm">${esc(s.action)}${at ? " " + esc(at) : ""}</b>`
        + (s.weight ? `<span class="wt">${esc(s.weight)}</span>` : "")
        + (s.why ? `<span class="nt">${esc(s.why)}</span>` : "")
        + `</div>`;
    }).join("");

    /* "Not yet" rather than a cross. A condition that has not been met is a
     * chart that has not done something yet, which is a neutral fact about
     * today; a failure mark would read as the chart having tried. */
    const checks = (c.conditions || []).map((x) => {
      const now = x.now != null ? x.now
        : x.price != null ? money(sym, x.price) : "";
      return `<div class="scan-check${x.met ? " met" : ""}">`
        + `<span class="what">${esc(x.what)}</span>`
        + (now ? `<b class="num">${esc(now)}</b>` : "")
        + `<span class="st">${x.met ? "Met" : "Not yet"}</span></div>`;
    }).join("");

    return `<div class="scan-stats">${stats}</div>`
      + section("Staged price confirmation", "in order of what a break costs",
                steps && `<div class="scan-steps">${steps}</div>`)
      + section("Indicator conditions", "", checks
                && `<div class="scan-checks">${checks}</div>`)
      + foot(`${c.bars_scanned} ${c.interval} bars`
             + (c.window ? ` · ${c.window}` : ""));
  }

  // ── the timeframe ladder ────────────────────────────────────────────
  //
  // Every rung measured the same four ways, so the rows compare. The stance
  // word takes the row's tint and the four readings stay printed beside it,
  // because a ladder of six coloured words with no numbers is a mood board.
  // ADX and RSI then get their own bar rows ACROSS the rungs, which is the
  // one comparison the per-rung line cannot make: 29 on the 15-minute and 14
  // on the daily is the finding, and it is invisible in six separate rows.
  function timeframes(c) {
    const sym = c.symbol;
    const rungs = c.rungs || [];
    // Counted by the tool, not here — the panel draws, it does not derive.
    const tally = c.tally || {};
    const stats = [
      c.price != null ? stat("Price", money(sym, c.price)) : "",
      stat("Rungs measured", rungs.length,
           "", (c.unavailable || []).length
             ? `${(c.unavailable || []).length} unavailable` : ""),
      tally.leaning
        ? stat("Leaning", cap(tally.leaning), TONE[tally.leaning] || "",
               `${tally.majority} of ${tally.of} rungs`) : "",
    ].filter(Boolean).join("");

    /* The row's own numbers, in one line, in the order the votes were taken.
     * Composed here out of the payload's figures — which is formatting, not
     * derivation: every value printed is one the tool measured, and the row
     * adds no reading of its own beyond putting them in a sentence. */
    const rows = rungs.map((r) => {
      const t = String(r.stance || "").indexOf("bull") >= 0 ? "up"
        : String(r.stance || "").indexOf("bear") >= 0 ? "down" : "";
      const bits = [
        r.rsi != null ? `RSI ${n2(sym, r.rsi)}` : "",
        r.macd_hist != null ? `MACD hist ${n2(sym, r.macd_hist)}` : "",
        r.adx != null ? `ADX ${n2(sym, r.adx)}` : "",
        r.di ? (Number(r.di.plus) > Number(r.di.minus) ? "+DI>−DI" : "−DI>+DI") : "",
        r.ema50 != null ? `${r.ema50_side === "above" ? "above" : "below"} EMA 50` : "",
      ].filter(Boolean).join(" · ");
      return `<div class="scan-read${t ? " tone-" + t : ""}">`
        + `<b class="nm">${esc(r.label)}</b>`
        + `<span class="nt">${esc(bits)}</span>`
        + `<b class="num">${esc(cap(r.stance || ""))}</b></div>`;
    }).join("");

    const adxBars = bars(rungs.filter((r) => r.adx != null).map((r) => ({
      label: r.label, value: r.adx, text: n2(sym, r.adx),
      tone: Number(r.adx) >= 25 ? "ann" : "",
    })), { tick: 25 });
    const rsiBars = bars(rungs.filter((r) => r.rsi != null).map((r) => ({
      label: r.label, value: r.rsi, text: n2(sym, r.rsi),
      tone: Number(r.rsi) >= 50 ? "up" : "down",
    })), { tick: 50, max: 100 });

    /* The pooled levels, with price standing in the column rather than
     * beside it. `intervals` is the whole point of pooling — a zone three
     * timeframes found is better evidenced than one the 5-minute saw alone,
     * and the row names them rather than grading them. */
    const lv = (c.levels || []).slice();
    const withPrice = lv.map((x) => ({
      cls: x.role === "resistance" ? "res" : "sup",
      price: `${Sym.of(sym).cur}${Sym.of(sym).num(x.lo, { maximumFractionDigits: 2 })}`
             + `–${Sym.of(sym).num(x.hi, { maximumFractionDigits: 2 })}`,
      label: cap(x.role || ""),
      note: (x.intervals || []).join(", "),
      at: Number(x.price),
    }));
    if (c.price != null) {
      withPrice.push({ cls: "current", price: money(sym, c.price),
                       label: "Current price", note: "", at: Number(c.price) });
    }
    withPrice.sort((a, b) => b.at - a.at);
    const ladder = withPrice.length > 1
      ? `<div class="scan-ladder">`
        + withPrice.map((x) => rung(x.cls, x.price, sym, x.label, x.note)).join("")
        + `</div>` : "";

    const gone = (c.unavailable || []).length
      ? callout(`Not measured: ${(c.unavailable || []).join(" · ")}`) : "";

    return `<div class="scan-stats">${stats}</div>`
      + section("Every rung, measured the same four ways", "", rows)
      + section("ADX across timeframes", "25 marks a trending phase", adxBars)
      + section("RSI across timeframes", "50 is the midline", rsiBars)
      + section("Levels the timeframes agree on", "", ladder)
      + gone;
  }

  // ── the pair comparison ─────────────────────────────────────────────
  //
  // Every measured interval is its own column, and the columns exist to be
  // read against each other: a gap that triples between the daily window and
  // the weekly one is the finding, and neither window alone contains it. The
  // benchmark rides in the return group rather than in a note, because "both
  // fell" and "both fell while the index fell too" are different answers.
  function compare(c) {
    const syms = c.symbols || [];
    const sym = syms[0];
    const cols = c.intervals || [];
    const pair = syms.length === 2;

    const stats = [];
    for (const g of (c.gaps || [])) {
      stats.push(stat(`${g.label} return gap`, signed(sym, g.gap_pp, " pp"),
                      way(g.gap_pp), g.pair));
    }
    for (const col of cols) {
      const corr = col.correlation && pair
        ? col.correlation[`${syms[0]}~${syms[1]}`] : null;
      if (corr != null) {
        stats.push(stat(`${col.label} correlation`, n2(sym, corr), "",
                        "daily returns"));
      }
    }
    if (!stats.length) {
      for (const col of cols) {
        stats.push(stat(`${col.label} bars`, col.bars ? col.bars[syms[0]] : ""));
      }
    }

    /* One group per interval, one bar per symbol, and the benchmark as the
     * last bar of the group where there is one. Grouped this way because the
     * comparison inside a window is the reading and the comparison BETWEEN
     * windows is the second one — a flat list of six bars would offer
     * neither.
     *
     * Coloured by SYMBOL, not by sign, and the same symbol keeps its colour
     * through every section. Sign colouring is the right choice on a single
     * series and the wrong one here: in a year both names can be down, and
     * two red bars under two labels is a chart the eye cannot follow across
     * three sections. The sign is not lost — it is printed, and zero is
     * drawn down the middle of the track. */
    const series = (pick, opt) => cols.map((col) => {
      const items = syms.map((s, i) => ({
        label: s, value: col[pick] ? col[pick][s] : null,
        text: signed(sym, col[pick] ? col[pick][s] : null, "%"),
        tone: "s" + Math.min(i + 1, 4),
      }));
      if (pick === "ret" && col.benchmark) {
        items.push({ label: col.benchmark.name, value: col.benchmark.ret,
                     text: signed(sym, col.benchmark.ret, "%"), tone: "bench" });
      }
      return group(col.label, col.window || "",
                   bars(items.filter((x) => x.value != null),
                        { signed: !!(opt && opt.signed) }));
    }).join("");

    /* Turnover is the one quantity that is neither signed nor comparable
     * across instruments by size alone, and it comes in two units. A table
     * keeps the unit next to every figure; a bar row would have to pick one
     * scale for rupees crore and millions of dollars at once. */
    const units = { cr: " cr", musd: " M$" };
    const haveTurn = cols.some((col) => col.turnover);
    const turnRows = haveTurn ? syms.map((s) => [
      `<b>${esc(s)}</b>`,
      ...cols.map((col) => {
        const t = col.turnover && col.turnover[s];
        return t ? esc(Sym.of(sym).num(t.value, { maximumFractionDigits: 1 })
                       + (units[t.unit] || "")) : "—";
      }),
    ]) : [];

    return `<div class="scan-stats">${stats.join("")}</div>`
      // Return takes the centred zero because a return set really does hold
      // both signs and which side of nothing a name landed on is the whole
      // reading. Drawdown and ATR never do — a drawdown is a fall and an ATR
      // is a width — so centring them would spend half the track drawing a
      // side no value can ever be on. The figures keep their own signs.
      + section("Return", "per bar window, against the index",
                series("ret", { signed: true }))
      + section("Maximum drawdown", "peak to trough, inside the window",
                series("dd", {}))
      + section("ATR volatility", "average true range as % of price",
                series("atr", {}))
      + section("Average turnover per bar", "",
                haveTurn ? grid(["", ...cols.map((x) => x.label)], turnRows) : "")
      + ((c.unavailable || []).length
         ? callout(`Not measured: ${(c.unavailable || []).join(" · ")}`) : "");
  }

  // ── why it moved ────────────────────────────────────────────────────
  //
  // The size question FIRST, because a move inside its own normal range
  // needs no story and a panel that opened with a cause would have conceded
  // there was one. Then where inside the session it happened, then the
  // ladder price is standing on, and last what was looked for — including
  // what was looked for and not found, which is the half a reader would
  // otherwise assume was never checked.
  function move(c) {
    const sym = c.symbol;
    const s = c.stats || {};
    const win = c.window || {};
    const stats = [
      s.move_pct != null
        ? stat(win.sessions === 1 ? "Session move" : "Window move",
               signed(sym, s.move_pct, "%"), way(s.move_pct),
               win.sessions ? `${win.sessions} session${win.sessions === 1 ? "" : "s"}` : "")
        : "",
      s.typical_abs_move_pct != null
        ? stat("Typical move", `${n2(sym, s.typical_abs_move_pct)}%`, "",
               "this stock's own median") : "",
      s.abs_percentile != null
        ? stat("Size percentile", `${s.abs_percentile}`, "",
               "of its own past moves") : "",
      s.vol_vs_20d_avg != null
        ? stat("Volume vs 20d avg", `${n2(sym, s.vol_vs_20d_avg)}×`,
               Number(s.vol_vs_20d_avg) >= 1.5 ? "ann" : "") : "",
    ].filter(Boolean).join("");

    const segs = bars((c.segments || []).map((x) => ({
      label: x.name, value: x.pct, text: signed(sym, x.pct, "%"), tone: way(x.pct),
    })), { signed: true });

    const ladder = (c.ladder || []).length > 1
      ? `<div class="scan-ladder">` + (c.ladder || []).map((r) => rung(
          r.role === "current" ? "current" : r.role === "crossed" ? "crossed"
            : r.role === "resistance" ? "res" : "sup",
          money(sym, r.price), sym, r.label,
          r.touches != null ? `${r.touches} touches` : "")).join("")
        + `</div>` : "";

    /* The index's share, as rows rather than a chart: three quantities that
     * are one arithmetic sentence — the index moved this much, beta says
     * that much of the move was owed to it, and the residual is the part it
     * does not account for. A bar row would invite them to be compared by
     * size, which is not what they are. */
    const ix = c.index;
    const idx = ix ? [
      row("", null, ix.name, "moved", signed(sym, ix.index_pct, "%")),
      ix.expected_pct != null
        ? row("", null, `Beta ${n2(sym, ix.beta)}`, "would explain",
              signed(sym, ix.expected_pct, "%")) : "",
      ix.residual_pct != null
        ? row("", null, "Residual", "the part it does not explain",
              signed(sym, ix.residual_pct, "%")) : "",
    ].filter(Boolean).join("") : "";

    return `<div class="scan-stats">${stats}</div>`
      + section("Return by session segment",
                c.segments_of ? `on ${c.segments_of}` : "", segs)
      + section("The ladder price is standing on", "", ladder)
      + section("How much of it was the index", "", idx)
      + section("What was looked for", "", chips(c.context))
      + foot(win.from && win.to
             ? (win.from === win.to ? win.from : `${win.from} → ${win.to}`) : "");
  }

  const RENDER = { patterns, trend, indicators, confirmation, timeframes,
                   compare, move };

  return {
    /** A card object → an element for the thread, or null when this build has
     *  no renderer for that kind. Null rather than a placeholder: a panel
     *  reading "unsupported card" tells the user about our deploy schedule
     *  and nothing about their chart. */
    render(card) {
      if (!card || !RENDER[card.kind]) return null;
      let html;
      try {
        html = RENDER[card.kind](card);
      } catch (e) {
        console.warn("[charto] card render failed", card, e);
        return null;      // a broken panel must never cost the reply
      }
      if (!html) return null;
      const box = document.createElement("div");
      box.className = "scan";
      box.dataset.card = card.kind;
      box.innerHTML = html;
      const more = box.querySelector("[data-more]");
      if (more) {
        more.addEventListener("click", () => {
          box.querySelector(".scan-rows.capped").classList.remove("capped");
          more.remove();
        });
      }
      return box;
    },
  };
})();
