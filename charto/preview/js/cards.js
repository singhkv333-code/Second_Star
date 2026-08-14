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

  // ── the pattern sweep ───────────────────────────────────────────
  function patterns(c) {
    const sym = c.symbol;
    const counts = c.counts || {};

    /* The four figures the sweep is ABOUT, before any of the detail. The
     * structure read is the only one with a direction, so it is the only one
     * that takes a colour — a count is not bullish. */
    const stat = (k, v, tone) =>
      `<div class="scan-stat"><span class="k">${esc(k)}</span>`
      + `<b class="v${tone ? " " + tone : ""}">${esc(v)}</b></div>`;
    /* Both counts are of what the panel SHOWS — tiles and rows — never of
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
    const stat = (k, v, tone, q) =>
      `<div class="scan-stat"><span class="k">${esc(k)}</span>`
      + `<b class="v${tone ? " " + tone : ""}">${esc(v)}</b>`
      + (q ? `<span class="q">${esc(q)}</span>` : "") + `</div>`;
    const adx = c.adx || {};
    const rng = c.range || {};
    const n2 = (v) => Sym.of(sym).num(v, { maximumFractionDigits: 2 });
    const stats = [
      c.structure ? stat("Trend", cap(c.structure), TONE[c.structure] || "") : "",
      c.bias ? stat("Bias", cap(c.bias), TONE[c.bias] || "") : "",
      adx.value != null
        ? stat(adx.period ? `ADX ${adx.period}` : "ADX",
               Sym.of(sym).num(adx.value, { minimumFractionDigits: 2,
                                            maximumFractionDigits: 2 }),
               "", adx.strength) : "",
      // One currency symbol, on the pair. Two ends of one range are one
      // quantity — "₹1,249.8–₹1,345.8" prices them as if they were two.
      rng.low != null && rng.high != null
        ? stat("Range", `${Sym.of(sym).cur}${n2(rng.low)}–${n2(rng.high)}`, "",
               rng.position_pct != null
                 ? `${n2(rng.position_pct)}% of range` : "") : "",
    ].filter(Boolean).join("");

    /* +DI against −DI, drawn against each other rather than listed. The
     * comparison IS the reading — 8.59 means nothing except next to 35.88 —
     * and two numbers in a column leave the reader to do the subtraction.
     *
     * The bars are scaled to the LARGER of the pair, not to 100: DI rarely
     * reaches 50, so a 0-100 scale renders both legs as stubs and the one
     * fact the section exists to show disappears. A bar length is a way of
     * drawing a number, not a second number — the figure printed on the
     * right is the payload's own, and nothing here is rounded into it. */
    let di = "";
    if (c.di && c.di.plus != null && c.di.minus != null) {
      const top = Math.max(Number(c.di.plus), Number(c.di.minus)) || 1;
      const leg = (label, v, tone) => {
        const w = Math.max(0, Math.min(100, Number(v) / top * 100));
        return `<div class="scan-di">`
          + `<span class="lb tone-${tone}">${esc(label)}</span>`
          + `<b class="num">${esc(Sym.of(sym).num(v,
              { minimumFractionDigits: 2, maximumFractionDigits: 2 }))}</b>`
          + `<span class="track"><i class="fill ${tone}" `
          + `style="width:${w.toFixed(1)}%"></i></span></div>`;
      };
      di = leg("+DI", c.di.plus, "up") + leg("−DI", c.di.minus, "down");
    }

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
      + section("DI comparison", "", di && `<div class="scan-dis">${di}</div>`)
      + section("Structure events", "", events)
      + section("Trendlines", "", lines && `<div class="scan-tiles">${lines}</div>`)
      + `<div class="scan-foot">${esc(c.bars_scanned)} ${esc(c.interval)} bars`
      + (c.window ? ` · ${esc(c.window)}` : "") + `</div>`;
  }

  const RENDER = { patterns, trend };

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
