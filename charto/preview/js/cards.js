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
  // Same origin rule the rest of the app uses: served from the dataserver in
  // production, cross-origin only when the preview folder is on its own port.
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
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
  // The badge grades the formation, it does not report the detector's state.
  // "Confirmed" answered "has the level broken"; the reader's question is
  // "is this worth anything", and those are different — a confirmed shape of
  // a kind that has never beaten its control is a confirmed nothing. The
  // backend answers the second (`_pattern_strength`); these are its three
  // words. `confirmed`/`broken` stay because the same map dresses the
  // journal tiles below, which really are reporting a state.
  /* TWO AXES, and they were sharing one map.
   *
   * STRENGTH is confidence — how well evidenced a formation or a level is.
   * It went through this same map, so `Strong` rendered in the candle GREEN
   * and `Weak` in the candle RED. That is not a shade of wrong, it is a
   * different statement: it painted a strong RESISTANCE — overhead supply,
   * the thing that stops a rally — in the colour the chart uses for a bar
   * that closed up, and a weakly-evidenced level in the colour for a bar
   * that closed down. Confidence has no direction, so it cannot borrow the
   * vocabulary of direction.
   *
   * So strength is one ink at three intensities: filled and dark, filled and
   * mid, outlined and faint. That is deliberately the same ladder the chart
   * itself now paints levels with (scene.js SHADE: 1 / .66 / .4) — the panel
   * and the chart are describing the same property and a reader moving
   * between them should not have to learn it twice.
   *
   * STATUS is a binary fact with a date behind it — a trendline is intact or
   * it broke — and keeps the semantic pair, because there "broken" really is
   * the negative outcome rather than a direction. */
  const STRENGTH = { Strong: "s-strong", Moderate: "s-mid", Weak: "s-weak" };
  const BADGE = { confirmed: "ok", broken: "bad" };

  /* ── the formation glyph ──────────────────────────────────────────────────
   *
   * A small schematic drawing of the shape, carried on the tile so the name
   * is not the only thing that says what was found. It is a DIAGRAM, not a
   * reading of these bars: a bull flag is a pole and a down-drifting channel
   * no matter which stock drew it, so the glyph is keyed on the pattern and
   * its bias, never on the payload's prices — the card already prints those.
   *
   * Direction is the one live bit: a bull flag's pole runs up and a bear
   * flag's runs down, so `.up`/`.down` flip the geometry and tint. Anything
   * we don't have a purpose-drawn shape for falls back to a plain trend
   * stroke in the bias colour rather than a wrong picture. */
  const GLYPH_VB = "0 0 40 28";
  function glyphBody(name, dir) {
    const n = (name || "").toLowerCase();
    // A flag: a steep pole, then a tight channel that drifts AGAINST the pole
    // (the consolidation), then the breakout tick resuming the pole's way.
    if (n.includes("flag") || n.includes("pennant")) {
      return dir === "down"
        ? `<path class="g-pole" d="M4 4 L13 20"/>`
          + `<path class="g-chan" d="M13 20 L22 15 M17 24 L26 19"/>`
          + `<path class="g-brk" d="M24 17 L32 25"/>`
        : `<path class="g-pole" d="M4 24 L13 8"/>`
          + `<path class="g-chan" d="M13 8 L22 13 M17 4 L26 9"/>`
          + `<path class="g-brk" d="M24 11 L32 3"/>`;
    }
    // Triangles / wedges: two converging edges resolving toward the apex.
    if (n.includes("triangle") || n.includes("wedge")) {
      return dir === "down"
        ? `<path class="g-chan" d="M4 8 L30 14 M4 20 L30 15"/>`
          + `<path class="g-brk" d="M28 15 L36 22"/>`
        : `<path class="g-chan" d="M4 20 L30 14 M4 8 L30 13"/>`
          + `<path class="g-brk" d="M28 13 L36 6"/>`;
    }
    // Double tops / bottoms: the twin turn with the neckline it breaks.
    if (n.includes("double") || n.includes("head")) {
      return dir === "down"
        ? `<path class="g-chan" d="M4 20 L11 6 L18 18 L25 6 L32 20"/>`
          + `<path class="g-brk" d="M11 18 L32 18"/>`
        : `<path class="g-chan" d="M4 8 L11 22 L18 10 L25 22 L32 8"/>`
          + `<path class="g-brk" d="M11 10 L32 10"/>`;
    }
    // Channel / range: two parallels.
    if (n.includes("channel") || n.includes("range") || n.includes("rectangle")) {
      return `<path class="g-chan" d="M4 9 L34 9 M4 19 L34 19"/>`
        + `<path class="g-brk" d="${dir === "down" ? "M30 19 L37 25" : "M30 9 L37 3"}"/>`;
    }
    // Fallback: a single directional stroke. Honest about saying little.
    return dir === "down"
      ? `<path class="g-brk" d="M4 6 L20 15 L36 24"/>`
      : `<path class="g-brk" d="M4 24 L20 13 L36 4"/>`;
  }
  function patternGlyph(name, bias) {
    const dir = bias === "bearish" ? "down" : "up";
    return `<span class="scan-glyph scan-glyph-${dir}" aria-hidden="true">`
      + `<svg viewBox="${GLYPH_VB}" fill="none" xmlns="http://www.w3.org/2000/svg">`
      + glyphBody(name, dir) + `</svg></span>`;
  }

  /* ── the flag schematic ────────────────────────────────────────────────────
   *
   * The hero answer to "is there a flag": the shape drawn to scale from the
   * detector's own coordinates — the pole's two ends, the consolidation box,
   * and the measured-move target it projects to. It is a DIAGRAM of real
   * numbers, not a reading invented here: every price comes off `geo`, which
   * the backend fills only when all five are real (dataserver `_patterns_card`),
   * and the "not to scale" line is honest because the X axis is schematic
   * while the Y axis is true. No coordinate is guessed; a missing `geo` means
   * no diagram, never a drawn placeholder. */
  function flagDiagram(sym, g, up) {
    // The schematic is drawn in the same green/red the chart paints the
    // formation with (--pat-up / --pat-down), not the candle tones, so the
    // diagram and the real drawing on the bars agree.
    const col = up ? "var(--pat-up)" : "var(--pat-down)";
    const fill = up ? "var(--pat-up-soft)" : "var(--pat-down-soft)";
    const W = 520, H = 196, padT = 24, padB = 34, padL = 46, padR = 128;
    const ps = [g.pole_from, g.pole_to, g.flag_high, g.flag_low, g.measured_move]
      .filter((v) => Number.isFinite(Number(v))).map(Number);
    let lo = Math.min(...ps), hi = Math.max(...ps);
    const pad = (hi - lo) * 0.18 || 1; lo -= pad; hi += pad;
    const Y = (v) => padT + (H - padT - padB) * (hi - v) / (hi - lo);
    const x0 = padL, x1 = W - padR;
    const xA = x0, xB = x0 + (x1 - x0) * 0.42,
          xC = x0 + (x1 - x0) * 0.70, xD = x1;
    const yHi = Y(g.flag_high), yLo = Y(g.flag_low);
    const yTop = Math.min(yHi, yLo), hBox = Math.abs(yLo - yHi);
    // price prints next to the detector's own figure, no re-rounding
    const px = (v) => esc(money(sym, v));
    const T = (x, y, s, anc, c, w) =>
      `<text x="${x}" y="${y}" font-size="11" fill="${c}" text-anchor="${anc}"`
      + `${w ? ` font-weight="${w}"` : ""}>${s}</text>`;
    const midTop = yTop + Math.min(5, hBox * 0.25),
          midBot = yTop + hBox - Math.min(5, hBox * 0.25);
    const dx = xC - xB;
    return `<svg viewBox="0 0 ${W} ${H}" fill="none" class="pw-svg" preserveAspectRatio="xMidYMid meet">`
      // consolidation box
      + `<rect x="${xB.toFixed(1)}" y="${yTop.toFixed(1)}" width="${dx.toFixed(1)}"`
      + ` height="${hBox.toFixed(1)}" rx="2" fill="${fill}"/>`
      + `<line x1="${xB}" y1="${yHi}" x2="${xC}" y2="${yHi}" stroke="${col}" stroke-width="1.25" stroke-opacity=".55"/>`
      + `<line x1="${xB}" y1="${yLo}" x2="${xC}" y2="${yLo}" stroke="${col}" stroke-width="1.25" stroke-opacity=".55"/>`
      // the pole
      + `<path d="M${xA} ${Y(g.pole_from).toFixed(1)} L${xB} ${Y(g.pole_to).toFixed(1)}"`
      + ` stroke="${col}" stroke-width="3" stroke-linecap="round"/>`
      // the consolidation path (muted, inside the box)
      + `<path d="M${xB} ${Y(g.pole_to).toFixed(1)}`
      + ` L${(xB + dx * 0.20).toFixed(1)} ${midBot.toFixed(1)}`
      + ` L${(xB + dx * 0.42).toFixed(1)} ${midTop.toFixed(1)}`
      + ` L${(xB + dx * 0.62).toFixed(1)} ${midBot.toFixed(1)}`
      + ` L${(xB + dx * 0.82).toFixed(1)} ${midTop.toFixed(1)}`
      + ` L${xC} ${midBot.toFixed(1)}" fill="none" stroke="var(--muted-foreground)"`
      + ` stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" opacity=".6"/>`
      // breakout projection (dashed): leaves the box from the corner the
      // move resumes through — top-right for a bull flag, bottom-right for a
      // bear — so it never crosses the high/low labels sitting beside the box.
      + `<path d="M${xC} ${(up ? yHi : yLo).toFixed(1)} L${xD} ${Y(g.measured_move).toFixed(1)}"`
      + ` stroke="var(--faint)" stroke-width="1.5" stroke-dasharray="4 4" stroke-linecap="round"/>`
      // faint guide ticks carrying the flag high / low out to their labels
      + `<line x1="${xC}" y1="${yHi}" x2="${xC + 44}" y2="${yHi}" stroke="${col}" stroke-width="1" stroke-dasharray="2 3" stroke-opacity=".4"/>`
      + `<line x1="${xC}" y1="${yLo}" x2="${xC + 44}" y2="${yLo}" stroke="${col}" stroke-width="1" stroke-dasharray="2 3" stroke-opacity=".4"/>`
      // anchor dots
      + `<circle cx="${xA}" cy="${Y(g.pole_from).toFixed(1)}" r="3.5" fill="${col}"/>`
      + `<circle cx="${xB}" cy="${Y(g.pole_to).toFixed(1)}" r="3.5" fill="${col}"/>`
      // labels
      + T((xA + xB) / 2, Y((g.pole_from + g.pole_to) / 2) - 9, "Pole", "middle", col, 600)
      + T(xA, Y(g.pole_from) + 17, px(g.pole_from), "start", "var(--muted-foreground)")
      + T(xB, Y(g.pole_to) - 8, px(g.pole_to), "middle", "var(--muted-foreground)")
      + T((xB + xC) / 2, yTop + hBox + 19, "Flag (consolidation)", "middle", "var(--muted-foreground)")
      + T(xC + 48, yHi + 3.5, `${px(g.flag_high)} high`, "start", "var(--muted-foreground)")
      + T(xC + 48, yLo + 3.5, `${px(g.flag_low)} low`, "start", "var(--muted-foreground)")
      + T(xD, Y(g.measured_move) + (up ? -9 : 15), "Breakout?", "end", "var(--faint)")
      + `</svg>`;
  }

  /* The full pattern widget — header, schematic (or a large glyph well when
   * the formation has no scale geometry), a borderless stat strip and an
   * honest-boundary footer. It is the view a condensed list row expands into,
   * so it is built for EVERY formation, not only the flags: a flag carries its
   * `geo` and draws to scale; a double top / triangle / channel has no scale
   * coordinates from the backend, so it shows its directional glyph large and
   * reports only the facts the detector actually gave — the window, the
   * strength, and the one measured level where there is one. Nothing is
   * invented to fill the card; a missing number is a stat that is not there. */
  function patternHero(c, p) {
    const sym = c.symbol;
    const up = p.bias !== "bearish";
    const g = p.geo;
    const hasGeo = g && g.kind === "flag";
    const dir = up ? "up" : "down";
    const icon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"`
      + ` stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">`
      + `<path d="M3 17 L9 11 L13 15 L21 7"/><path d="M21 7 L15 7 M21 7 L21 13"/></svg>`;
    const confirmed = p.status === "confirmed" || !!p.broke_at;
    const pills = [
      confirmed ? "" : `<span class="pw-pill warn">Unconfirmed</span>`,
      p.bias && p.bias !== "neutral"
        ? `<span class="pw-pill ${dir}">${esc(cap(p.bias))} bias</span>` : "",
      p.strength ? `<span class="pw-pill plain">${esc(cap(p.strength))}</span>` : "",
    ].filter(Boolean).join("");
    const sub = [esc(sym), c.interval ? `${esc(c.interval)} chart` : "",
                 span(p.from, p.to)].filter(Boolean).join(" · ");
    const stat = (k, v, s, sc) =>
      `<div class="pw-stat"><div class="k">${esc(k)}</div>`
      + `<div class="v">${v}</div>`
      + (s ? `<div class="s${sc ? " " + sc : ""}">${s}</div>` : "") + `</div>`;

    let figure, stats;
    if (hasGeo) {
      // Flag: the shape drawn to scale from the detector's own coordinates, and
      // a stat strip of the pole / rise / flag-range numbers it is made of.
      // The pole's % move is derived HERE only as a presentation of two
      // detector numbers (to − from over from) — a ratio of givens, not a new
      // measurement — and is dropped if the base is unusable.
      const base = Number(g.pole_from);
      const pct = Number.isFinite(base) && base !== 0
        ? ((Number(g.pole_to) - base) / Math.abs(base)) * 100 : null;
      const wide = Number(g.flag_high) - Number(g.flag_low);
      // The pole stat is the pole's LENGTH — its magnitude — with the signed %
      // beside it carrying the direction. A bear flag's pole is a drop, so the
      // length is a positive number falling at a negative rate; printing "₹-37"
      // for a length reads as a typo.
      const poleMag = Math.abs(g.pole_to - g.pole_from);
      figure = `<div class="pw-diagram">${flagDiagram(sym, g, up)}</div>`
        + `<div class="pw-note">Schematic — price true, spacing not to scale</div>`;
      stats = `<div class="pw-stats">`
        + stat("Pole", esc(money(sym, poleMag)),
               pct != null ? `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%` : "",
               pct != null ? (pct >= 0 ? "up" : "down") : "")
        + stat(up ? "Rise" : "Decline", esc(money(sym, g.pole_from)),
               `to ${esc(money(sym, g.pole_to))}`)
        + stat("Flag range", esc(money(sym, g.flag_low)),
               `to ${esc(money(sym, g.flag_high))}`
               + (Number.isFinite(wide) ? ` · ${esc(money(sym, Math.abs(wide)))} wide` : ""))
        + `</div>`;
    } else {
      // No scale geometry: NO diagram. A formation the backend did not give
      // scale coordinates for (a double top, a triangle) cannot be drawn to the
      // bars, and a stylised glyph standing in for a real schematic reads as a
      // broken figure — a scribble in a tinted box — not as honesty about what
      // is missing. So the card drops the picture entirely and shows only the
      // facts the detector DID give: the measured level a reader acts on, with
      // its verdict (broke / watching); the window; the strength. The header's
      // own icon and bias pill still carry the direction. Nothing is invented.
      figure = "";
      const facts = [];
      if (p.measure && p.measure.value != null)
        facts.push(stat(cap(p.measure.label || "Level"),
          esc(money(sym, p.measure.value)),
          p.broke_at ? `broke ${esc(when(p.broke_at))}`
                     : (confirmed ? "confirmed" : "not yet broken")));
      const winText = span(p.from, p.to);
      if (winText) facts.push(stat("Window", esc(winText), ""));
      if (p.strength)
        facts.push(stat("Strength", esc(cap(p.strength)),
          p.bias && p.bias !== "neutral" ? `${esc(cap(p.bias))} reading` : ""));
      stats = facts.length ? `<div class="pw-stats">${facts.join("")}</div>` : "";
    }

    const warn = confirmed ? "" :
      `<div class="pw-foot"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"`
      + ` stroke-width="2" stroke-linecap="round" stroke-linejoin="round">`
      + `<path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>`
      + `<div><b>Pattern is drawn but not yet confirmed.</b> Treat as a watch, not a signal.</div></div>`;
    return `<div class="pw ${dir}"${p.drawn && p.id ? ` data-ann="${esc(p.id)}"` : ""}>`
      + `<div class="pw-head"><div class="pw-ico ${dir}">${icon}</div>`
      + `<div class="pw-htext"><div class="pw-title">${esc(cap(p.name))}</div>`
      + `<div class="pw-sub">${sub}</div></div>`
      + `<div class="pw-pills">${pills}</div></div>`
      + figure + stats + warn + `</div>`;
  }

  /* ── the condensed formation row ──────────────────────────────────────────
   *
   * One formation as a single scannable line rather than a tile: a direction
   * arrow and the name, the window beneath it; the strength in the middle; and
   * the measured level with its role closed against the right edge, so every
   * figure in the list stacks into one column. The whole row is a button —
   * clicking it expands the full detail card (schematic, stats, the watch-not-
   * signal boundary) inline beneath, which is why the index is carried in
   * `data-pat`: the panel it toggles is pre-built once and sits in the fold
   * below, keyed by the same index. A row still carries `data-ann`, so hovering
   * it lights the drawn shape on the chart exactly as the old tile did. */
  function patternRow(sym, p, idx) {
    const up = p.bias !== "bearish";
    const arrow = p.bias === "bearish"
      ? `<span class="pl-arrow dn" aria-hidden="true">▼</span>`
      : (p.bias === "bullish" || up
          ? `<span class="pl-arrow up" aria-hidden="true">▲</span>` : "");
    const sub = span(p.from, p.to);
    const strengthCls = STRENGTH[cap(p.strength || "")] || "";
    const strength = p.strength
      ? `<span class="pl-str ${strengthCls}">${esc(cap(p.strength))}</span>` : "";
    // The measured level and its role (Neckline / Pole …), the one number a
    // reader acts on. Printed only when the detector gave it; its tense — broke
    // vs. still open — rides along so the figure never reads as settled when it
    // is not.
    // Always its own cell, empty or not, so the four-column grid stays aligned
    // and the caret keeps the last column whether or not a level was measured.
    const fact = (p.measure && p.measure.value != null)
      ? `<span class="pl-fact"><b>${esc(money(sym, p.measure.value))}</b>`
        + `<span>${esc(cap(p.measure.label || "Level"))}`
        + (p.broke_at ? ` · broke` : "") + `</span></span>`
      : `<span class="pl-fact"></span>`;
    return `<button type="button" class="pl-row" data-pat="${idx}"`
      + (p.drawn && p.id ? ` data-ann="${esc(p.id)}"` : "")
      + ` aria-expanded="false">`
      + `<span class="pl-name"><span class="pl-nm">${arrow}${esc(cap(p.name))}</span>`
      + (sub ? `<span class="pl-sub">${esc(sub)}</span>` : "") + `</span>`
      + `<span class="pl-mid">${strength}</span>`
      + fact
      + `<span class="pl-caret" aria-hidden="true">`
      + `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"`
      + ` stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>`
      + `</span></button>`;
  }

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

  /* ── the builder's cards ───────────────────────────────────────────────
   *
   * Ported from Pivot's own chat widgets (WorkflowDraftCard.tsx,
   * IndicatorBacktestCard.tsx) rather than re-imagined, because the shape of
   * those cards is an argument that was already had: the "Agent" chip and
   * Draft dot say what state this is in, the reasoning hides behind "Why
   * this?" so the steps stay the hero, the CTA rail is ONE primary pill with
   * ghost actions beneath it instead of a three-button grid, and warnings sit
   * above the buttons where they are read before a decision rather than
   * after it.
   *
   * Not a transliteration. Pivot's are React with hooks and Tailwind; this
   * folder has no build step and `Cards.render` returns a string. So the
   * structure and the behaviour come across, and the mechanics are Charto's.
   *
   * Neither card computes anything. Every figure printed is the payload's
   * own — a card that recomputed a metric could disagree with the engine that
   * produced it, and the disagreement would be invisible.
   */

  const STEP_ICON = {
    trigger: "bell", fetch: "search", condition: "opCross",
    action: "position", notify: "chat", control: "chevronRight",
  };

  /** Last line of defence against a dev-string leak, mirroring Pivot's
   *  `isLeakedLabel` — the backend backfills friendly labels, but a raw
   *  `action.place_order` reaching the card must never be shown as one. */
  function stepLabel(step) {
    const raw = String(step.label || "").trim();
    const type = String(step.step_type || "");
    if (raw && raw !== type && !/^[a-z]+\.[a-z_]+$/.test(raw)) return raw;
    const tail = type.includes(".") ? type.slice(type.indexOf(".") + 1) : type;
    const words = tail.replace(/[._]+/g, " ").trim();
    return words ? words.charAt(0).toUpperCase() + words.slice(1) : type;
  }

  /** One step. A compound trigger's config is a DSL tree; the backend
   *  travels the English sentence for it alongside (`readback`), because
   *  asking a reader to audit a parse tree to find out what their own
   *  strategy says is not showing them their strategy. Steps without a
   *  sentence show the config fields that carry meaning — a place_order's
   *  side and size — and not the ones that are plumbing. */
  /** A step's config as ONE SENTENCE, or nothing.
   *
   *  The old fallback printed the config's own keys — `symbol INFY  side buy
   *  quantity 15  order_type market  product CNC` — which is a form field
   *  list, not a description, and it printed whatever was in the value. So a
   *  sell sized to the position rendered as
   *
   *      quantity {{ context.3.holdings.INFY.quantity }}
   *
   *  a runtime template address, shown to someone deciding whether to arm a
   *  strategy with money behind it. That is the engine's plumbing on the
   *  user's screen, and there is no reading of it that helps them.
   *
   *  So the sentence is composed from the fields that carry meaning, and a
   *  templated size — which is precisely the case that HAS no literal number
   *  yet — is named for what it does instead. A shape this cannot say is
   *  rendered as nothing: the step's own label already names it, and silence
   *  beats leaking the internals of a shape nobody wrote a sentence for. */
  const TEMPLATED = /\{\{.*\}\}/;
  function orderSentence(cfg) {
    const side = String(cfg.side || "").toLowerCase();
    if (!side) return "";
    const sym = cfg.symbol || cfg.target_symbol || "";
    const qty = cfg.quantity;
    const size = qty == null || qty === "" ? ""
      : TEMPLATED.test(String(qty)) ? "your whole position in"
      : `${qty} ${String(qty) === "1" ? "share of" : "shares of"}`;
    const how = String(cfg.order_type || "").toLowerCase() === "limit"
      && cfg.limit_price != null ? ` at ${cfg.limit_price}` : " at market";
    const verb = side === "sell" ? "Sell" : "Buy";
    return `${verb} ${size ? size + " " : ""}${sym}${how}`.replace(/\s+/g, " ").trim();
  }
  function draftStep(s, i) {
    const cfg = s.config || {};
    const icon = STEP_ICON[String(s.step_type || "").split(".")[0]] || "info";
    const line = s.readback || orderSentence(cfg);
    const detail = line ? `<p class="wf-readback">${esc(line)}</p>` : "";
    return `<li class="wf-step" style="animation-delay:${i * 45}ms">`
      + `<span class="wf-ico">${Icons.svg(icon, "sm")}</span>`
      + `<div class="wf-body"><b class="wf-label">${esc(stepLabel(s))}</b>`
      + detail + `</div></li>`;
  }

  /* Pivot shows five and counts the rest. The cap is the same here for the
   * same reason: past five the list stops being a shape you can take in and
   * becomes a thing you scroll, and the card is meant to be glanced at. */
  const MAX_VISIBLE_STEPS = 5;

  function workflowDraft(c) {
    const steps = c.steps || [];
    const shown = steps.slice(0, MAX_VISIBLE_STEPS);
    const hidden = steps.length - shown.length;
    const list = steps.length
      ? `<ol class="wf-steps">${shown.map(draftStep).join("")}`
        + (hidden > 0 ? `<li class="wf-more">+${hidden} more step`
            + `${hidden > 1 ? "s" : ""}</li>` : "")
        + `</ol>`
      : "";
    // Warnings are the honest half of this card — a trailing exit modelled in
    // backtest but registering only its initial stop live is exactly the
    // thing to read BEFORE arming, so it sits above the buttons.
    const warn = (c.warnings || []).concat(c.live_warnings || []);
    const blockers = c.backtestable === false ? (c.backtest_blockers || []) : [];
    const warnBlock = warn.length
      ? `<ul class="wf-warn">${warn.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`
      : "";
    const why = c.rationale
      ? `<button type="button" class="wf-why" data-wf-why aria-expanded="false">`
        + `${Icons.svg("info", "sm")}<span>Why this?</span></button>`
        + `<p class="wf-why-body" hidden>${esc(c.rationale)}</p>`
      : "";
    const ttl = c.valid_until
      ? `<span class="wf-chip-ttl">until ${esc(c.valid_until)}</span>` : "";
    // The draft travels ON the card so the Backtest button can post the
    // exact steps the user is looking at, rather than the FE rebuilding a
    // request that could drift from what is rendered.
    /* The description is dropped when it only restates the first step.
     *
     * A draft's description is written as "Entry: <the entry condition>",
     * and the entry step's own tile carries that same sentence directly
     * underneath — so the card opened by saying the same thing twice, in
     * two type sizes, before showing anything new. Compared loosely
     * (case and the "Entry:" prefix removed) because the two are generated
     * separately and need not match to the character. */
    const _norm = (t) => String(t || "").toLowerCase()
      .replace(/^\s*(?:entry|exit)\s*:\s*/, "")
      // …and the per-leaf "of SYMBOL". The description is generated with it
      // and the step's readback without it (the bridge strips it once it
      // knows the draft is single-symbol), so the two would never compare
      // equal on the one shape this check exists to catch.
      .replace(/\s+of\s+[a-z0-9&.\-]+/g, "")
      .replace(/\s+/g, " ").trim();
    const _backs = new Set(steps.map((x) => _norm(x && x.readback)).filter(Boolean));
    /* A two-sided strategy's description is the steps JOINED — "Entry: … ·
     * Exit: …" — so it has to be split on the same separator before it can
     * be compared. Suppressed only when EVERY part is a step's own sentence;
     * a description carrying anything the steps do not say is kept whole. */
    const _parts = String(c.description || "").split("·")
      .map(_norm).filter(Boolean);
    const desc = (_parts.length && _parts.every((x) => _backs.has(x)))
      ? "" : c.description;

    const payload = attrJSON({ name: c.name, steps: steps });
    return `<div class="wf-card" data-wf data-draft="${payload}">`
      + `<div class="wf-top"><span class="wf-chip">Agent</span>`
      + `<span class="wf-state">${ttl}`
      + `<span class="wf-dot" aria-hidden="true"></span>Draft</span></div>`
      + `<h3 class="wf-title">${esc(c.name || "Strategy draft")}</h3>`
      + (desc ? `<p class="wf-desc">${esc(desc)}</p>` : "")
      + why
      + list
      + warnBlock
      + (blockers.length
          ? `<p class="wf-note">${esc(blockers[0])}</p>` : "")
      + `<div class="wf-cta">`
      + `<button type="button" class="wf-primary" data-wf-activate>`
      + `Save &amp; activate</button>`
      + `<div class="wf-ghosts">`
      + (blockers.length ? ""
          : `<button type="button" class="wf-ghost" data-wf-backtest>`
            + `${Icons.svg("clock", "sm")}`
            // A run that already happened is named as one, so a reader
            // returning to the thread is not invited to pay for it twice.
            + `<span>${c.backtest ? "Re-run backtest" : "Backtest"}</span>`
            + `</button>`)
      + `</div></div>`
      // The result is filed ON the card, so it comes back with the card. A
      // backtest costs seconds and returns a paragraph of evidence the reply
      // above was written against; losing it to a page reload left the
      // strategy claiming a verdict with nothing behind it.
      + `<div class="wf-slot" data-wf-slot${c.backtest ? "" : " hidden"}>`
      + (c.backtest ? strategyBacktest(c.backtest) : "")
      + `</div></div>`;
  }

  /* ── the strategy layer on the chart ──────────────────────────────
   *
   * A backtest answers in two places. The card answers "was it any good"; the
   * CHART answers "what did it actually do" — and that second question is one
   * no table can take, because its answers are shapes: how long trades ran,
   * whether the wins were the long ones, whether the strategy was in the
   * market at all between them.
   *
   * Everything here is derived from the payload's own trade list. The card
   * computes GEOMETRY — a bar time, a pixel span — and never a statistic.
   */
  const SCENE_OWNER = "backtest", SCENE_PREFIX = "bt:";
  const IST = 19800;

  /** The loaded chart's bars, or an empty list when there is no chart. */
  const chartBars = () =>
    (window.__charto && window.__charto.state && window.__charto.state.bars) || [];

  /* An ISO date onto the bar it belongs to.
   *
   * A trade date is a SESSION, not an instant, so it cannot be handed to the
   * chart as an epoch and hoped for: on a 5-minute chart "2023-09-11" is 75
   * bars, and on a daily chart it is a bar stamped at whatever hour the feed
   * chose. Both resolve here, by searching the bars actually loaded — so the
   * mark lands on a bar that exists rather than at a clock reading that may
   * fall in a gap, a holiday or the middle of the night.
   *
   * Bars are held in CHART time (main.js adds IST); annotations are handed
   * back in real time, because the scene layer adds it again on the way out.
   */
  function barFor(iso) {
    const bars = chartBars();
    if (!bars.length || !iso) return null;
    const day = Date.parse(String(iso).slice(0, 10) + "T00:00:00Z");
    if (!Number.isFinite(day)) return null;
    // Chart-time midnight, and NOT the date plus IST: a daily bar is already
    // keyed at chart-time midnight of its own session (the 27th's bar sits at
    // 2022-09-27T00:00Z in chart time), so offsetting the target as well
    // stepped every search one session late. Intraday resolves off the same
    // line — the first bar after that day's chart-time midnight is 09:15.
    const want = day / 1000;
    if (bars[bars.length - 1].time < want) return null;
    if (bars[0].time >= want) return bars[0];
    let lo = 0, hi = bars.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (bars[mid].time < want) lo = mid; else hi = mid;
    }
    return bars[hi];
  }

  /* Bars are held in CHART time (main.js adds IST); annotations are handed
   * back in real time, because the scene layer adds it again on the way out. */
  const sceneT = (bar) => bar.time - IST;

  /* Trades → scene annotations. One per trade, plus the rail.
   *
   * The two price series do NOT agree, and pretending otherwise is the whole
   * hazard here. Pivot back-adjusts for dividends; Charto's own store does
   * not, so the same RELIANCE entry is ₹1087.82 to the engine and ₹1141 to
   * this chart — a ladder that steps at every ex-date and closes to nothing
   * on the most recent trade. Drawn at the engine's price, the object would
   * float five percent clear of the candle it claims to be an entry on, and
   * nothing on screen would tell the user which number was wrong.
   *
   * So each trade takes ONE anchor from the chart and ONE magnitude from the
   * engine: the entry sits on this chart's own bar, and the exit is that
   * anchor carried by the engine's own return. The object then lands on the
   * candles it is about, its height is exactly the return the card prints,
   * and the two can never disagree — because only one of them is measured
   * here. No price is invented: both ends are a real bar or a real result.
   */
  function strategyItems(c) {
    const items = [], spans = [];
    const bars = chartBars();
    const lastBar = bars.length ? bars[bars.length - 1] : null;
    (c.trades || []).forEach((t, i) => {
      const inBar = barFor(t.entry_date);
      // A trade still open at the end of the window closes at the last bar —
      // that is where the money still is, and dropping it would quietly
      // under-report how long the strategy holds.
      const outBar = t.exit_date ? barFor(t.exit_date) : lastBar;
      if (!inBar || !outBar) return;
      const ep = Number(inBar.open);
      const r = Number(t.return_pct);
      if (!Number.isFinite(ep)) return;
      // return_pct is the engine's PRICE return (net_pnl is the one after
      // costs), so carrying the anchor by it lands where the exit bar sits
      // on this chart's scale rather than on the engine's.
      const xp = Number.isFinite(r) ? ep * (1 + r) : ep;
      const t0 = sceneT(inBar), end = Math.max(sceneT(outBar), t0);
      items.push({
        id: `${SCENE_PREFIX}t${i}`, kind: "trade", pane: "price",
        owner: SCENE_OWNER,
        entry: { t: t0, v: ep }, exit: { t: end, v: xp },
        // The engine's own sign, not a re-derivation from the two prices:
        // net_pnl is after costs, and a trade that gained 0.2% and paid 0.3%
        // in brokerage is a LOSS however the prices look.
        win: Number(t.net_pnl) > 0,
        text: Number.isFinite(r)
          ? `${r >= 0 ? "+" : "\u2212"}${Math.abs(r * 100).toFixed(2)}%` : "",
      });
      spans.push([t0, end]);
    });
    /* A position still HELD is not a trade that did not happen.
     *
     * A buy-only strategy — "buy 5 every time price crosses the 50 EMA" —
     * closes nothing, so it has no round-trips at all; reporting that as an
     * empty chart would be the same lie as reporting it as no activity. Its
     * open lots are drawn as entries and nothing else: there is no exit to
     * draw a path to, and forty ribbons all running to the right edge would
     * be forty overlapping shapes saying one thing the rail already says.
     */
    (c.open_lots || []).forEach((l, i) => {
      const inBar = barFor(l.entry_date);
      if (!inBar || !Number.isFinite(Number(inBar.open))) return;
      const t0 = sceneT(inBar);
      items.push({
        id: `${SCENE_PREFIX}o${i}`, kind: "trade", pane: "price",
        owner: SCENE_OWNER,
        entry: { t: t0, v: Number(inBar.open) },
        exit: { t: t0, v: Number(inBar.open) },
        win: true, open: true,
        // Explicit, because this is the only place the mark explains itself.
        // "Holding 5" left the reader to guess whether 5 was a price, a
        // count or a day; "Bought 5 · 2024-03-11" cannot be read two ways.
        text: `Bought ${l.quantity || ""} · ${String(l.entry_date).slice(0, 10)}`
          .replace("  ", " "),
      });
      if (lastBar) spans.push([t0, sceneT(lastBar)]);
    });
    if (spans.length) {
      /* One line naming the layer. The shaded columns and the chevrons are
       * unlabelled by design — forty-nine captions would bury the candles —
       * but unlabelled is not the same as unexplained, and a reader looking
       * at blue marks has to be able to find out what they are without
       * hovering one. Counted from what was actually built, never from the
       * payload, so it can never name more marks than are drawn. */
      const nOpen = items.filter((x) => x.open).length;
      const nClosed = items.length - nOpen;
      const bits = [];
      if (nClosed) bits.push(`${nClosed} trade${nClosed === 1 ? "" : "s"}`);
      if (nOpen) bits.push(`${nOpen} still held`);
      items.push({ id: `${SCENE_PREFIX}rail`, kind: "exposure", pane: "price",
                   owner: SCENE_OWNER, spans,
                   label: `Strategy · ${bits.join(" · ")}` });
    }
    return items;
  }

  /** Take the strategy layer off the chart, and nothing else with it. */
  const clearStrategy = () => ({
    kind: "clear", scope: "id_prefix", prefix: SCENE_PREFIX,
    owner: SCENE_OWNER,
  });

  /* Why the button can be inert.
   *
   * Drawing INFY's trades over a RELIANCE chart would be a fabrication the
   * user has no way to catch — the shapes look exactly as convincing on the
   * wrong instrument. So the symbol has to agree, and when it does not the
   * button says which one to switch to rather than going quiet.
   */
  function strategyBlocker(c) {
    if (!window.__charto || !window.__charto.scene) return "No chart loaded.";
    if (!(c.trades || []).length && !(c.open_lots || []).length) {
      return "Nothing was bought or sold in this window.";
    }
    const want = String(c.symbol || "").toUpperCase();
    const have = String(window.__charto.symbol || "").toUpperCase();
    if (want && have && want !== have) return `Open ${want} to see these trades.`;
    if (!chartBars().length) return "No bars loaded.";
    return "";
  }

  /** A backtest read-out.
   *
   * The verdict leads because it is the only line that says whether the
   * return under it means anything — a 40% CAGR on nine trades and a 60% CAGR
   * on nine hundred are not the same claim, and the verdict is the engine's
   * own answer to which one this is.
   *
   * Everything below it is arranged as an argument rather than a dump: the
   * headline figures, then the one comparison that decides whether the
   * strategy was worth running at all (it versus simply holding), then the
   * curve, then whether the result came from the whole window or one lucky
   * stretch, then what the resampling says the drawdown could have been.
   *
   * Every series and every figure is the payload's own. The charts derive
   * GEOMETRY from those series — a pixel path, a bar width — and never a
   * statistic: nothing here prints a number the engine did not compute.
   */
  function strategyBacktest(c) {
    const m = c.metrics || {};
    const v = m.trust_verdict || {};
    const fs = m.forward_stats || {};
    const mc = m.monte_carlo || {};
    const sp = m.sub_periods || {};
    const sym = c.symbol || "";
    const closedCount = (c.trades || []).length;
    const openCount = (c.open_lots || []).length;
    const fin = (x) => x != null && Number.isFinite(Number(x));
    const num = (x, d) => (fin(x) ? n2(sym, x) + (d || "") : "—");
    const pct = (x) => (fin(x) ? signed(sym, x, "%") : "—");
    // A drawdown has no direction to report — it is a depth. The engine hands
    // it over as a magnitude, so signing it printed "+1.89%", which reads as
    // a gain in the one field that can only ever be a loss.
    const depth = (x) => (fin(x) ? `−${n2(sym, Math.abs(Number(x)))}%` : "—");

    // `label` is a written sentence fragment; `verdict` is the raw enum. Only
    // the fallback needs capitalising, and the CSS no longer does it for both.
    const verdict = v.label || cap(String(v.verdict || "").replace(/_/g, " "));
    const verdictBlock = verdict
      ? `<div class="wf-verdict" data-verdict="${esc(String(v.verdict || ""))}">`
        + `<b>${esc(verdict)}</b>`
        + (v.rationale ? `<span>${esc(v.rationale)}</span>` : "")
        + `</div>` : "";

    // The percentage is the RULE's (fully invested while in a position), so
    // the rupee figure is just capital × it — shown so "+7.6%" also reads as
    // what it would have meant for the money.
    const inr = fin(m.pnl_inr) && fin(m.starting_capital)
      ? `${Number(m.pnl_inr) < 0 ? "−" : "+"}₹${Math.abs(Math.round(m.pnl_inr)).toLocaleString("en-IN")}`
        + ` on ₹${Math.round(m.starting_capital).toLocaleString("en-IN")}`
      : "";
    const strip = stat("Return", pct(m.total_return_pct), way(m.total_return_pct), inr)
      + stat("CAGR", fin(m.cagr_pct) ? pct(m.cagr_pct) : "—", way(m.cagr_pct))
      + stat("Max drawdown", depth(m.max_drawdown_pct), "down")
      /* Hit rate is over CLOSED round-trips, and a strategy that has not
       * closed one has no hit rate — it does not have a hit rate of zero.
       * The engine reports 0.0 for both cases, so beside "Trades 49" the
       * card read "0.00% · 0 won", which says forty-nine losing trades
       * about a strategy that has not sold anything. Undefined is the
       * honest answer, and the qualifier says why it is undefined. */
      + (closedCount === 0 && openCount > 0
          ? stat("Hit rate", "—", "", "nothing closed yet")
          : stat("Hit rate", fin(m.hit_rate_pct) ? num(m.hit_rate_pct, "%") : "—",
                 "", m.n_wins == null ? "" : `${esc(m.n_wins)} won`))
      + stat("Sharpe", num(m.sharpe), "",
             fin(fs.deflated_sharpe) ? `deflated ${num(fs.deflated_sharpe)}` : "")
      // …and the count says WHICH it is counting, for the same reason.
      + stat("Trades", m.n_trades ?? "—", "",
             closedCount === 0 && openCount > 0 ? `${openCount} still held`
               : fin(m.capital_utilization_pct)
                 ? `in the market ${Math.round(m.capital_utilization_pct)}% of the time` : "");

    // The comparison that decides whether any of this was worth doing. Two
    // figures the payload already carries, drawn against one shared scale so
    // the gap is a length rather than a subtraction the reader performs.
    const bench = m.benchmark_return_pct != null
      ? m.benchmark_return_pct : c.bench_buy_hold_return_pct;
    // Return is half the comparison. For anything sold as protection the
    // other half — how deep each one fell — is the one that decides it, so
    // the worst drop is drawn on its own shared scale beneath.
    const benchDd = m.benchmark_max_drawdown_pct;
    const versus = (fin(m.total_return_pct) && fin(bench))
      ? section("Versus holding", "same window", bars([
          { label: "Strategy", value: m.total_return_pct,
            text: pct(m.total_return_pct), tone: way(m.total_return_pct) },
          { label: "Buy & hold", value: bench, text: pct(bench), tone: way(bench) },
        ], { signed: true })
        + (fin(benchDd) && fin(m.max_drawdown_pct)
          ? bars([
              { label: "Strategy, worst drop", value: Math.abs(m.max_drawdown_pct),
                text: depth(m.max_drawdown_pct), tone: "down" },
              { label: "Buy & hold, worst drop", value: Math.abs(benchDd),
                text: depth(benchDd), tone: "down" },
            ])
          : ""))
      : "";

    const curve = equityChart(c.equity_curve || [], c.signals || []);

    // Did the edge show up across the window, or in one stretch? A single
    // total cannot answer that and this list can — it is the engine's own
    // per-period split, drawn as columns off a zero line.
    const periods = (sp.period_returns_pct || []);
    const spread = periods.length > 1
      ? section("Period by period",
                fin(sp.positive_period_frac)
                  ? `${Math.round(sp.positive_period_frac * 100)}% positive` : "",
                columns(periods, sym))
      : "";

    // Resampling says what the equity curve alone cannot: this path was one
    // draw, and these are the drawdowns the same edge produced on others.
    const mcRows = [];
    if (fin(mc.dd_median_pct)) mcRows.push({
      label: "Typical", value: Math.abs(mc.dd_median_pct),
      text: depth(mc.dd_median_pct), tone: "down" });
    if (fin(mc.dd_worst_pct)) mcRows.push({
      label: "Worst", value: Math.abs(mc.dd_worst_pct),
      text: depth(mc.dd_worst_pct), tone: "down" });
    const fan = mcFan(mc, c.equity_curve || []);
    const mcBlock = (mcRows.length || fan)
      ? section("Monte Carlo",
                mc.n_sims ? `${mc.n_sims} runs` : "",
                fan + (mcRows.length ? bars(mcRows) : ""))
      : "";

    // The rigor rows only appear when the engine actually computed them — an
    // empty "Monte-Carlo: —" implies a test that ran and said nothing.
    const rigor = [
      fin(fs.psr) ? ["Probabilistic Sharpe", num(fs.psr)] : null,
      // Rounded up to a whole bar: min-TRL is a COUNT of observations, and
      // "54,863.60 obs" prints six-tenths of a trading day.
      fin(fs.min_trl)
        ? ["Min track record",
           `${Math.ceil(Number(fs.min_trl)).toLocaleString("en-IN")} obs`]
        : null,
      fs.n_obs ? ["Observations", String(fs.n_obs)] : null,
      (fs.num_trials && fs.num_trials > 1)
        ? ["Deflated for trials", String(fs.num_trials)] : null,
      fin(mc.prob_loss) ? ["Chance of a loss", `${Math.round(mc.prob_loss * 100)}%`] : null,
      fin(sp.concentration) ? ["Return concentration", num(sp.concentration)] : null,
    ].filter(Boolean);
    const rigorBlock = rigor.length
      ? section("Robustness checks", "",
          `<dl class="wf-rigor">${rigor.map(([k, val]) =>
            `<div><dt>${esc(k)}</dt><dd>${esc(val)}</dd></div>`).join("")}</dl>`)
      : "";

    const assume = (c.assumptions || []).length
      ? `<ul class="wf-warn">${c.assumptions.map((a) =>
          `<li>${esc(a)}</li>`).join("")}</ul>` : "";

    return `<div class="wf-card">`
      + `<div class="wf-top"><span class="wf-chip wf-chip-sim">Simulation</span>`
      + (c.period_label ? `<span class="wf-state">${esc(c.period_label)}</span>` : "")
      + `</div>`
      + `<h3 class="wf-title">${esc(sym)} backtest</h3>`
      + (c.tree_summary ? `<p class="wf-desc">${esc(c.tree_summary)}</p>` : "")
      + verdictBlock
      + `<div class="scan-stats">${strip}</div>`
      + versus
      + curve
      + spread
      + mcBlock
      + rigorBlock
      + tradeRows(c.trades || [], sym)
      + assume
      + onChartCta(c);
  }

  /* The one control this card carries. A backtest's own numbers are already
   * on it; the only thing left to offer is the reading it cannot print. */
  function onChartCta(c) {
    const blocked = strategyBlocker(c);
    return `<div class="wf-cta wf-cta-solo">`
      + `<button type="button" class="wf-ghost" data-bt-chart`
      + (blocked ? ` disabled title="${esc(blocked)}"` : "")
      + ` aria-pressed="false">${Icons.svg("candles", "sm")}`
      + `<span>Show on chart</span></button>`
      + (blocked ? `<span class="wf-cta-note">${esc(blocked)}</span>` : "")
      + `</div>`;
  }

  /** The equity curve: a shape, with the trades marked on it.
   *
   * Not a chart in the axes-and-ticks sense — where the money ended is a
   * question the figures above already answer. What a curve adds is HOW it
   * got there: smoothly or in one jump, and at which moments the strategy was
   * actually in the market. So it carries a fill (area reads as a level, a
   * bare line reads as a rate), a starting baseline to measure against, and a
   * dot per signal placed at the bar it fired on.
   */
  function equityChart(points, signals) {
    const rows = points.filter((p) => p && Number.isFinite(Number(p.v)));
    if (rows.length < 3) return "";
    const vals = rows.map((p) => Number(p.v));
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const span = (hi - lo) || 1;
    const W = 100, H = 44;
    const xAt = (i) => (i / (rows.length - 1)) * W;
    const yAt = (val) => H - ((val - lo) / span) * H;
    const d = vals.map((val, i) =>
      `${i ? "L" : "M"}${xAt(i).toFixed(2)} ${yAt(val).toFixed(2)}`).join(" ");
    const area = `${d} L${W} ${H} L0 ${H} Z`;
    const up = vals[vals.length - 1] >= vals[0];
    const base = yAt(vals[0]).toFixed(2);
    // Signals carry dates; the curve carries the same dates. Match on the
    // date rather than assuming the two arrays share an index — they do not
    // when the strategy sat out part of the window.
    const at = new Map(rows.map((p, i) => [String(p.t).slice(0, 10), i]));
    // Marks are DOM, not SVG. The plot is stretched with
    // preserveAspectRatio="none" so a viewBox unit is wider than it is tall,
    // and an SVG <circle> inside it is drawn as an ELLIPSE — the fills came
    // out as fat horizontal lozenges. A positioned span is round at every
    // panel width, and it can carry its own hover label.
    const marks = (signals || []).slice(0, 80).map((s) => {
      const key = String(s.t || s.date || "").slice(0, 10);
      const i = at.get(key);
      if (i == null) return "";
      const side = s.side === "sell" ? "s" : "b";
      const label = `${side === "s" ? "Exit" : "Entry"} · ${key}`;
      return `<span class="wf-mark ${side}" data-v="${esc(label)}"`
        + ` style="left:${(xAt(i) / W * 100).toFixed(2)}%;`
        + `top:${(yAt(vals[i]) / H * 100).toFixed(2)}%"></span>`;
    }).join("");
    return `<div class="wf-chart"><div class="wf-plot">`
      + `<svg class="wf-curve${up ? " up" : " down"}"`
      + ` viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">`
      + `<path class="fill" d="${area}"/>`
      + `<line class="base" x1="0" y1="${base}" x2="${W}" y2="${base}"/>`
      + `<path class="line" d="${d}"/></svg>${marks}</div>`
      + `<div class="wf-axis"><span>${esc(String(rows[0].t).slice(0, 10))}</span>`
      + `<span>${esc(String(rows[rows.length - 1].t).slice(0, 10))}</span></div></div>`;
  }

  /* The bootstrap, drawn.
   *
   * "Chance of a loss: 31%" is a number you either trust or you don't. The
   * same fact as a cloud of runs — the same edge, its periods reshuffled in
   * blocks — around the one path that actually happened is a fact you can
   * SEE, and it answers the question the equity curve is silently begging:
   * was this the strategy, or was this the ordering?
   *
   * Three things, and deliberately no fourth. The cloud is the spread. The
   * median is where the middle of it ran. The bold line is you. Percentile
   * bands on top of a visible cloud would be the same information drawn
   * twice.
   *
   * Every series is the engine's own; this derives a pixel path from them
   * and never a statistic.
   */
  function mcFan(mc, points) {
    const paths = (mc || {}).paths || {};
    const sample = (paths.sample || []).filter((r) => Array.isArray(r) && r.length > 2);
    const median = paths.points || [];
    if (!sample.length || median.length < 3) return "";

    // The realised path, on the simulations' own scale (% from the start) and
    // resampled to their x-grid so the two can be laid over each other.
    const eq = (points || []).map((p) => Number(p && p.v))
      .filter(Number.isFinite);
    const N = median.length;
    let actual = [];
    if (eq.length > 2 && eq[0]) {
      actual = Array.from({ length: N }, (_, i) => {
        const at = (i / (N - 1)) * (eq.length - 1);
        const lo = Math.floor(at), hi = Math.min(eq.length - 1, lo + 1);
        const v = eq[lo] + (eq[hi] - eq[lo]) * (at - lo);
        return (v / eq[0] - 1) * 100;
      });
    }

    /* The scale is the CLOUD's 1st-to-99th percentile, not its extremes.
     * Two runaway resamples out of a thousand would otherwise set the range
     * and flatten the other thirty into a single grey line — the shape of
     * the spread is the point, and it cannot survive being scaled by its own
     * tails. The few paths that leave the frame are clipped by the viewBox,
     * which is honest: they are drawn as far as the plot goes. The realised
     * path and zero are always inside the range, because those two are the
     * comparison the whole picture exists to make.
     */
    const pool = sample.flat().filter(Number.isFinite).sort((a, b) => a - b);
    if (!pool.length) return "";
    const q = (f) => pool[Math.min(pool.length - 1,
                                   Math.max(0, Math.round(f * (pool.length - 1))))];
    const must = actual.filter(Number.isFinite).concat(0);
    let lo = Math.min(q(0.01), ...must), hi = Math.max(q(0.99), ...must);
    const pad = ((hi - lo) || 1) * 0.1;
    lo -= pad; hi += pad;
    const W = 100, H = 76, span = (hi - lo) || 1;
    const xAt = (i) => (i / (N - 1)) * W;
    const yAt = (v) => H - ((v - lo) / span) * H;
    const d = (row) => row.map((v, i) =>
      `${i ? "L" : "M"}${xAt(i).toFixed(2)} ${yAt(v).toFixed(2)}`).join(" ");

    const cloud = sample.map((row) =>
      `<path class="sim" d="${d(row)}"/>`).join("");
    const end = actual.length ? actual[actual.length - 1] : null;
    const up = end != null && end >= 0;
    // The one place the eye should land: where YOUR run finished inside the
    // spread. DOM, not SVG — the plot is stretched with
    // preserveAspectRatio="none", so an SVG circle in it is drawn as an
    // ellipse (see the equity curve's marks for the same reason).
    const tip = end == null ? "" :
      `<span class="wf-tip ${up ? "up" : "down"}"`
      + ` data-v="${esc(`Your run · ${end >= 0 ? "+" : "−"}`
                        + `${Math.abs(end).toFixed(1)}%`)}"`
      + ` style="left:100%;top:${(yAt(end) / H * 100).toFixed(2)}%"></span>`;

    return `<div class="wf-chart"><div class="wf-plot">`
      + `<svg class="wf-fan${up ? " up" : " down"}" viewBox="0 0 ${W} ${H}"`
      + ` preserveAspectRatio="none" aria-hidden="true">`
      + `${cloud}`
      + `<line class="zero" x1="0" y1="${yAt(0).toFixed(2)}"`
      + ` x2="${W}" y2="${yAt(0).toFixed(2)}"/>`
      + `<path class="med" d="${d(median)}"/>`
      + (actual.length ? `<path class="act" d="${d(actual)}"/>` : "")
      + `</svg>${tip}</div>`
      + `<div class="wf-key">`
      + `<span class="k-act">Your run</span>`
      + `<span class="k-med">Median</span>`
      + `<span class="k-sim">${sample.length} of ${mc.n_sims || 0} runs</span>`
      + `</div></div>`;
  }

  /** Signed columns off a zero line — the shape for "was it every period, or
   *  one of them". Heights are geometry from the given list; no figure here
   *  is computed or printed. */
  function columns(values, sym) {
    const nums = values.map(Number).filter(Number.isFinite);
    if (!nums.length) return "";
    const top = Math.max(...nums.map(Math.abs)) || 1;
    const cells = nums.map((val) => {
      // Floor at 4%, not 2%: a period that returned -0.4% against a 3%
      // best still HAPPENED, and a 1px sliver reads as a rendering gap
      // rather than as the smallest bar in the set.
      const h = Math.max(4, Math.abs(val) / top * 50);
      const cls = val > 0 ? "up" : val < 0 ? "down" : "";
      const side = val >= 0 ? "bottom" : "top";
      return `<span class="wf-col" data-v="${esc(signed(sym, val, "%"))}">`
        + `<i class="${cls}" style="${side}:50%;height:${h.toFixed(1)}%"></i></span>`;
    }).join("");
    return `<div class="wf-cols">${cells}<i class="wf-zero"></i></div>`;
  }

  /** The trades, capped. The full list belongs in a table the user can sort;
   *  what a chat card owes is enough rows to see whether the wins and losses
   *  are the same size, which is the question a hit rate cannot answer. */
  const MAX_TRADE_ROWS = 6;
  const EXIT_REASON = {
    exit_tree: "Exit rule", stop_loss: "Stop", take_profit: "Target",
    end_of_window: "Window end", hold_to_end: "Held to end",
    max_bars: "Time exit", signal: "Signal",
  };
  function tradeRows(trades, sym) {
    if (!trades.length) return "";
    const rows = trades.slice(0, MAX_TRADE_ROWS).map((t) => {
      /* return_pct is a FRACTION (0.059 = 5.9%), unlike every *_pct on the
       * metrics block, which are already percentages. Printed straight it
       * turned a 5.91% trade into "+0.06%" — off by two orders of magnitude
       * in the one column a reader checks the strategy against. */
      const ret = Number(t.return_pct) * 100;
      const cls = Number.isFinite(ret) ? (ret > 0 ? "up" : ret < 0 ? "down" : "") : "";
      const why = String(t.exit_reason || "");
      return `<tr><td>${esc(String(t.entry_date || "").slice(0, 10))}</td>`
        + `<td>${esc(String(t.exit_date || "open").slice(0, 10))}</td>`
        + `<td class="wf-num ${cls}">${Number.isFinite(ret)
            ? signed(sym, ret, "%") : "—"}</td>`
        + `<td class="wf-reason">${esc(EXIT_REASON[why]
            || why.replace(/_/g, " "))}</td></tr>`;
    }).join("");
    const more = trades.length - MAX_TRADE_ROWS;
    return section("Trades", `${trades.length}`,
      `<table class="wf-trades"><thead><tr><th>In</th><th>Out</th>`
      + `<th class="wf-num">Return</th><th>Why</th></tr></thead>`
      + `<tbody>${rows}</tbody></table>`
      + (more > 0 ? `<p class="wf-more">+${more} more</p>` : ""));
  }

  /* ── strategy, options, and the relationship tools ────────────────────
   *
   * Same construction as the two above: every figure is the payload's own,
   * the charts turn a given series into geometry and never into a statistic,
   * and a field the engine did not compute takes its row with it rather than
   * rendering as "—" beside five real ones.
   */

  /* NO BASKET RENDERER. `build_strategy`'s card was removed from the seam
   * (see `_PIVOT_CARD_KINDS` in dataserver.py): on this surface a constructed
   * basket is always registered as a plan in the same turn, and the plan card
   * is the one with the button. Two panels of identical weights, only one of
   * which could be pressed, is the bug this deletion is. */

  /** A data-provenance banner, ABOVE the numbers it qualifies.
   *
   *  Without a Kite session the option tools return MOCK strikes and premiums
   *  — structurally perfect, financially fictional. That belongs before the
   *  first figure, not in a footnote under it: a reader who has already priced
   *  a spread off the card has been misled by the time they reach the bottom.
   *  Live data renders nothing at all, which is the correct amount of
   *  furniture for the normal case. */
  function provenance(c) {
    const status = String(c.data_status || "").toLowerCase();
    if (!status || status === "live" || status === "ok") return "";
    const note = c.stale_note || (status === "mock"
      ? "This option data is not live." : `Data status: ${status}.`);
    return `<p class="wf-stale" role="status">${esc(note)}</p>`;
  }

  /** An option structure, led by its payoff.
   *
   *  A spread's numbers — max loss, max profit, breakeven — are three points
   *  ON one curve, and reading them as three separate figures is what makes
   *  options feel like arithmetic homework. Drawn, the shape says the whole
   *  thing at once: where it makes money, where it stops, and how far the
   *  underlying has to travel to get there. */
  function optionStrategy(c) {
    const s = c.summary || {};
    const k = c.computed || {};
    const cr = c.critique || {};
    const und = s.underlying || c.underlying || "";
    const fin = (x) => x != null && Number.isFinite(Number(x));
    const rupee = (x) => (fin(x) ? money(und, Math.abs(Number(x))) : "—");
    const legs = (s.legs || []).map((l) =>
      `<li class="wf-leg"><span class="wf-side ${String(l.side).toLowerCase()}">`
      + `${esc(l.side)}</span>`
      + `<b>${esc(l.strike)} ${esc(l.option_type)}</b>`
      + (fin(l.mid) ? `<span class="wf-num">${esc(money(und, l.mid))}</span>` : "")
      + `</li>`).join("");
    const g = k.net_greeks || {};
    const greeks = ["delta", "gamma", "theta", "vega"]
      .filter((x) => fin(g[x]))
      .map((x) => stat(cap(x), n2(und, g[x]), x === "theta" && g[x] < 0 ? "down" : ""))
      .join("");
    const strip = stat("Max profit", rupee(k.max_profit), "up")
      + stat("Max loss", rupee(k.max_loss), "down")
      + stat("Net premium", rupee(k.net_premium),
             Number(k.net_premium) >= 0 ? "up" : "down",
             Number(k.net_premium) >= 0 ? "credit" : "debit")
      + stat("Chance of profit", fin(k.pop) ? `${Math.round(k.pop * 100)}%` : "—", "")
      + stat("Breakeven", (k.breakevens || []).map((b) => n2(und, b)).join(" / ") || "—", "")
      + stat("Capital", rupee(k.capital_required ?? k.margin_estimate), "");
    // Rule-based, from the engine — flags the user should read before the
    // structure looks clever. Severity drives the tone, not my judgement.
    const flags = (cr.flags || []).map((f) =>
      `<li class="sev-${esc(f.severity || "info")}">${esc(f.text)}</li>`).join("");
    return `<div class="wf-card">`
      + `<div class="wf-top"><span class="wf-chip">Options</span>`
      + (s.expiry ? `<span class="wf-state">${esc(s.expiry)}</span>` : "")
      + `</div>`
      + `<h3 class="wf-title">${esc(und)} `
      + `${esc(String(s.template || "").replace(/_/g, " "))}</h3>`
      + (cr.summary ? `<p class="wf-desc">${esc(cr.summary)}</p>` : "")
      + provenance(c)
      + payoffChart(k.payoff || [], k.breakevens || [], c.spot ?? s.spot)
      + `<div class="scan-stats">${strip}</div>`
      + section("Legs", `${(s.legs || []).length}`,
                legs ? `<ul class="wf-legs">${legs}</ul>` : "")
      + section("Net greeks", "", greeks ? `<div class="scan-stats">${greeks}</div>` : "")
      + section("Risks", "", flags ? `<ul class="wf-flags">${flags}</ul>` : "")
      + (k.margin_note ? `<p class="wf-note">${esc(k.margin_note)}</p>` : "");
  }

  /** The payoff at expiry: profit above the line, loss below it, split at
   *  zero rather than coloured by slope. The zero crossing IS the breakeven,
   *  so it is drawn once as a line and not also listed as a claim. */
  function payoffChart(points, breakevens, spot) {
    const pts = points.filter((p) => p && Number.isFinite(Number(p.pnl))
                                     && Number.isFinite(Number(p.s)));
    if (pts.length < 3) return "";
    const xs = pts.map((p) => Number(p.s)), ys = pts.map((p) => Number(p.pnl));
    const xLo = Math.min(...xs), xHi = Math.max(...xs);
    // Headroom above and below. Without it the flat top of a spread sits
    // exactly on the viewBox edge and the stroke is shaved in half — the
    // capped profit, which is the whole point of the structure, read as a
    // clipping artefact.
    const yRaw = [Math.min(...ys, 0), Math.max(...ys, 0)];
    const pad = ((yRaw[1] - yRaw[0]) || 1) * 0.12;
    const yLo = yRaw[0] - pad, yHi = yRaw[1] + pad;
    const xSpan = (xHi - xLo) || 1, ySpan = (yHi - yLo) || 1;
    const W = 100, H = 56;
    const X = (v) => ((v - xLo) / xSpan) * W;
    const Y = (v) => H - ((v - yLo) / ySpan) * H;
    const zero = Y(0);
    const line = pts.map((p, i) =>
      `${i ? "L" : "M"}${X(p.s).toFixed(2)} ${Y(p.pnl).toFixed(2)}`).join(" ");
    // Two fills, each clipped to its own side of zero by a rectangle — a
    // single path cannot be two colours, and splitting the series by sign
    // would need interpolation this card has no business doing.
    const id = "po" + Math.random().toString(36).slice(2, 8);
    const area = `${line} L${X(xs[xs.length - 1]).toFixed(2)} ${zero.toFixed(2)}`
      + ` L${X(xs[0]).toFixed(2)} ${zero.toFixed(2)} Z`;
    const marks = (breakevens || []).filter(Number.isFinite)
      .map((b) => `<line class="be" x1="${X(b).toFixed(2)}" y1="0" `
        + `x2="${X(b).toFixed(2)}" y2="${H}"/>`).join("");
    const spotMark = Number.isFinite(Number(spot))
      ? `<line class="spot" x1="${X(spot).toFixed(2)}" y1="0" `
        + `x2="${X(spot).toFixed(2)}" y2="${H}"/>` : "";
    return `<div class="wf-chart"><svg class="wf-payoff" viewBox="0 0 ${W} ${H}"`
      + ` preserveAspectRatio="none" aria-hidden="true">`
      + `<defs>`
      + `<clipPath id="${id}u"><rect x="0" y="0" width="${W}" height="${zero.toFixed(2)}"/></clipPath>`
      + `<clipPath id="${id}d"><rect x="0" y="${zero.toFixed(2)}" width="${W}" height="${(H - zero).toFixed(2)}"/></clipPath>`
      + `</defs>`
      + `<path class="up" d="${area}" clip-path="url(#${id}u)"/>`
      + `<path class="down" d="${area}" clip-path="url(#${id}d)"/>`
      + `<line class="zero" x1="0" y1="${zero.toFixed(2)}" x2="${W}" y2="${zero.toFixed(2)}"/>`
      + marks + spotMark
      + `<path class="line" d="${line}"/></svg>`
      + `<div class="wf-axis"><span>${esc(n2("", xLo))}</span>`
      + `<span>${esc(n2("", xHi))}</span></div></div>`;
  }

  /** The chain, centred on the money. Open interest is drawn as a bar behind
   *  the figure because the SHAPE of OI across strikes is the read — where the
   *  writers are — and a column of six-digit numbers hides it completely. */
  function optionChain(c) {
    const rows = c.rows || [];
    const und = c.underlying || "";
    const fin = (x) => x != null && Number.isFinite(Number(x));
    const em = c.expected_move || {};
    const strip = stat("Spot", fin(c.spot) ? money(und, c.spot) : "—", "")
      + stat("ATM", fin(c.atm_strike) ? n2(und, c.atm_strike) : "—", "")
      + stat("Max pain", fin(c.max_pain) ? n2(und, c.max_pain) : "—", "")
      + stat("Expected move", fin(em.pct) ? `±${n2(und, em.pct)}%` : "—", "",
             fin(em.abs) ? `±${n2(und, em.abs)}` : "")
      + stat("PCR (OI)", fin(c.pcr_oi) ? n2(und, c.pcr_oi) : "—", "")
      + stat("Lot", c.lot_size ?? "—", "");
    const topOI = rows.reduce((m, r) => Math.max(
      m, Number(r.ce?.oi) || 0, Number(r.pe?.oi) || 0), 0) || 1;
    const cell = (side, oi, ltp) =>
      `<td class="wf-oi ${side}"><i style="width:${
        Math.min(100, (Number(oi) || 0) / topOI * 100).toFixed(1)}%"></i>`
      + `<span>${fin(ltp) ? esc(n2(und, ltp)) : "—"}</span></td>`;
    const body = rows.map((r) => {
      const atm = fin(c.atm_strike) && Number(r.strike) === Number(c.atm_strike);
      return `<tr${atm ? ' class="atm"' : ""}>`
        + cell("ce", r.ce?.oi, r.ce?.ltp)
        + `<td class="wf-strike">${esc(n2(und, r.strike))}</td>`
        + cell("pe", r.pe?.oi, r.pe?.ltp) + `</tr>`;
    }).join("");
    return `<div class="wf-card">`
      + `<div class="wf-top"><span class="wf-chip">Chain</span>`
      + `<span class="wf-state">${esc(c.expiry || "")}</span></div>`
      + `<h3 class="wf-title">${esc(und)} option chain</h3>`
      + provenance(c)
      + `<div class="scan-stats">${strip}</div>`
      + section("Calls · strike · puts", `${rows.length}`,
          body ? `<table class="wf-chain"><thead><tr><th>CE</th>`
            + `<th class="wf-strike">Strike</th><th>PE</th></tr></thead>`
            + `<tbody>${body}</tbody></table>` : "");
  }

  /** Pairs, cointegration, cross-sectional portfolios — four tools, one
   *  shape: a claim, some numbers, and whether the claim survives them. They
   *  share a renderer because giving each its own would be four cards that
   *  look different for no reason a reader could name. */
  function quantResult(c) {
    const m = c.metrics || {};
    const sym = (c.pair && c.pair[0]) || (c.symbols && c.symbols[0]) || "";
    const fin = (x) => x != null && Number.isFinite(Number(x));
    const title = c.pair ? `${c.pair.join(" / ")}`
      : c.symbols ? `${c.symbols.length} names`
      : "Result";
    const co = c.cointegration || (c.is_cointegrated != null ? c : null);
    const verdict = co && co.is_cointegrated != null
      ? `<div class="wf-verdict" data-verdict="${co.is_cointegrated ? "ok" : "no"}">`
        + `<b>${co.is_cointegrated ? "Cointegrated" : "Not cointegrated"}</b>`
        + (co.note ? `<span>${esc(co.note)}</span>` : "")
        + `</div>` : "";
    const KEYS = [
      ["total_return_pct", "Return", "pct"], ["cagr_pct", "CAGR", "pct"],
      ["sharpe", "Sharpe", "n"], ["max_drawdown_pct", "Max drawdown", "dd"],
      ["hit_rate_pct", "Hit rate", "pct"], ["n_trades", "Trades", "raw"],
    ];
    const strip = KEYS.filter(([k]) => m[k] != null).map(([k, label, kind]) => {
      const v = m[k];
      if (kind === "pct") return stat(label, signed(sym, v, "%"), way(v));
      if (kind === "dd") return stat(label, `−${n2(sym, Math.abs(v))}%`, "down");
      if (kind === "n") return stat(label, n2(sym, v), "");
      return stat(label, v, "");
    }).join("");
    // The Johansen test IS a comparison: at each rank, the trace statistic
    // against its 95% critical value, and cointegration is claimed only where
    // the statistic clears the bar. Printing two columns of numbers hides the
    // one thing that matters — whether it cleared — so the bar carries the
    // threshold as a tick and the figures stay quoted verbatim beside it.
    const trace = c.trace_stats || [], crit = c.crit_95 || [];
    const johansen = (trace.length && trace.length === crit.length)
      ? bars(trace.map((t, i) => ({
          label: `Rank ${i}`,
          value: crit[i] ? Number(t) / Number(crit[i]) : 0,
          text: `${n2(sym, t)} vs ${n2(sym, crit[i])}`,
          tone: Number(t) > Number(crit[i]) ? "up" : "",
        })), { max: 1.5, tick: 1 })
      : "";
    const found = (c.cointegrated || []).slice(0, 8).map((p) =>
      `<div class="wf-kv-row"><b>${esc(
        Array.isArray(p.pair) ? p.pair.join(" / ") : (p.pair || ""))}</b>`
      + `<span>${esc(fin(p.p_value) ? `p ${n2(sym, p.p_value)}` : "")}</span></div>`
    ).join("");
    return `<div class="wf-card">`
      + `<div class="wf-top"><span class="wf-chip wf-chip-sim">Simulation</span>`
      + (c.period ? `<span class="wf-state">${esc(c.period)}</span>` : "")
      + `</div>`
      + `<h3 class="wf-title">${esc(title)}</h3>`
      + (c.summary ? `<p class="wf-desc">${esc(c.summary)}</p>` : "")
      + verdict
      + (strip ? `<div class="scan-stats">${strip}</div>` : "")
      + section("Trace vs 95% critical",
                c.n_obs ? `${c.n_obs} obs` : "", johansen)
      + section("Cointegrated pairs", `${(c.cointegrated || []).length}`, found)
      + (c.note ? `<p class="wf-note">${esc(c.note)}</p>` : "");
  }

  /** JSON into an attribute. The thread's `esc` leaves quotes alone, which
   *  is right for a text node and would break out of an attribute. */
  function attrJSON(value) {
    return JSON.stringify(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  /* The draft card's behaviour. Pivot's version runs the backtest through
   * its own API rather than a chat turn, and renders the RESULT inside the
   * draft card — the strategy and its evidence in one object, so nothing has
   * to be scrolled back to. Same here, against `/execution/backtest`.
   *
   * Save & activate was inert, and the reason on it — that Charto accounts do
   * not map to Pivot's — stopped being true when the paper book shipped.
   * `POST /strategies` arms the draft under the CHARTO user, into Charto's own
   * book, evaluated by Charto's own runtime; Pivot's accounts were never
   * involved. So the button posts, and the only honest blocker left is not
   * being signed in — which the button says, once pressed, rather than
   * pre-emptively greying out a capability the reader would never discover.
   */
  /* Show on chart.
   *
   * A toggle rather than a one-way "draw": the layer is dense by design, and
   * anything you can put on a chart you have to be able to take off it in the
   * same gesture. Only ONE backtest is ever on the chart — pressing this on a
   * second card clears the first, because two strategies' trades interleaved
   * in the same two colours is not a comparison, it is a mess.
   */
  function wireOnChart(box, payload) {
    const btn = box.querySelector("[data-bt-chart]");
    if (!btn) return;
    const label = btn.querySelector("span");
    // A blocker computed at render time can be WRONG by the time anyone
    // reads it. "No bars loaded" is the case that matters: a restored thread
    // repaints before the chart has fetched anything, so every backtest that
    // survived a reload came back with its one control dead — permanently,
    // because nothing ever asked the question again. The transient blockers
    // are re-asked when the bars arrive; the permanent ones (no trades, wrong
    // symbol) are left exactly as they were.
    if (btn.disabled) {
      const recheck = () => {
        if (strategyBlocker(payload)) return;
        btn.disabled = false;
        btn.removeAttribute("title");
        label.textContent = "Show on chart";
        // The note beside it said why the button was dead. It is not, now.
        const note = btn.parentElement
          && btn.parentElement.querySelector(".wf-cta-note");
        if (note) note.remove();
        document.removeEventListener("charto:bars-loaded", recheck);
      };
      if (!/nothing was bought|open .* to see/i.test(btn.title || "")) {
        document.addEventListener("charto:bars-loaded", recheck);
        recheck();
      }
      // No early return. The listener is attached either way — a disabled
      // button cannot be clicked, so the attribute is the whole of the
      // guard, and returning here left `recheck` un-disabling a control that
      // had nothing behind it.
    }
    btn.addEventListener("click", () => {
      const scene = window.__charto && window.__charto.scene;
      if (!scene) return;
      const on = btn.getAttribute("aria-pressed") === "true";
      if (on) {
        scene.apply([clearStrategy()]);
        btn.setAttribute("aria-pressed", "false");
        label.textContent = "Show on chart";
        return;
      }
      // Any other card showing its own trades stands down first, so the
      // pressed state on screen always matches what is actually drawn.
      document.querySelectorAll('[data-bt-chart][aria-pressed="true"]')
        .forEach((other) => {
          other.setAttribute("aria-pressed", "false");
          const l = other.querySelector("span");
          if (l) l.textContent = "Show on chart";
        });
      const items = strategyItems(payload);
      if (!items.length) {
        label.textContent = "No bars for these dates";
        btn.disabled = true;
        return;
      }
      scene.apply([clearStrategy()].concat(items));
      btn.setAttribute("aria-pressed", "true");
      label.textContent = "Hide from chart";
    });
  }

  function wireDraft(box, card) {
    const why = box.querySelector("[data-wf-why]");
    if (why) {
      const body = box.querySelector(".wf-why-body");
      why.addEventListener("click", () => {
        const open = body.hidden;
        body.hidden = !open;
        why.setAttribute("aria-expanded", String(open));
        why.querySelector("span").textContent = open ? "Hide reasoning" : "Why this?";
      });
    }
    const activate = box.querySelector("[data-wf-activate]");
    if (activate) {
      activate.title = "Arm this against the live tick, into your paper book";
      activate.addEventListener("click", async () => {
        if (activate.disabled) return;
        activate.disabled = true;
        const was = activate.textContent;
        activate.textContent = "Arming…";
        let draft = {};
        try { draft = JSON.parse(box.querySelector("[data-wf]").dataset.draft); }
        catch (e) { draft = {}; }
        try {
          const res = await fetch(`${API}/strategies`, {
            method: "POST",
            headers: (window.Auth && Auth.headers)
              ? Auth.headers({ "Content-Type": "application/json" })
              : { "Content-Type": "application/json" },
            body: JSON.stringify({ draft: card.draft || card }),
          });
          const data = await res.json();
          if (!res.ok) {
            // The server's refusal names the reason — a schedule, a short
            // entry, no fixed size. That sentence is worth more on the card
            // than the word "Failed", because every one of them is something
            // the user can act on.
            activate.textContent = "Save & activate";
            activate.disabled = false;
            const note = document.createElement("p");
            note.className = "wf-stale";
            note.setAttribute("role", "status");
            note.textContent = res.status === 401
              ? "Sign in to arm a strategy — it is stored against your account."
              : String((data && data.error) || "Could not arm this draft.");
            const cta = box.querySelector(".wf-cta");
            const prev = box.querySelector(".wf-cta ~ .wf-stale");
            if (prev) prev.remove();
            if (cta) cta.after(note);
            return;
          }
          activate.textContent = "Armed";
          const dot = box.querySelector(".wf-dot");
          if (dot) dot.classList.add("on");
          const st = box.querySelector(".wf-state");
          if (st) st.lastChild.textContent = "Armed";
        } catch (e) {
          console.warn("[charto] arm failed", e);
          activate.textContent = was;
          activate.disabled = false;
        }
      });
    }

    const run = box.querySelector("[data-wf-backtest]");
    if (!run) return;
    const slot = box.querySelector("[data-wf-slot]");
    const label = run.querySelector("span");
    run.addEventListener("click", async () => {
      if (run.disabled) return;
      run.disabled = true;
      label.textContent = "Running…";
      let draft = {};
      try { draft = JSON.parse(box.querySelector("[data-wf]").dataset.draft); }
      catch (e) { draft = {}; }
      try {
        const res = await fetch(`${API}/execution/backtest`, {
          method: "POST",
          headers: (window.Auth && Auth.headers)
            ? Auth.headers({ "Content-Type": "application/json" })
            : { "Content-Type": "application/json" },
          body: JSON.stringify(draft),
        });
        const data = await res.json();
        if (!res.ok || data.error) throw new Error(data.detail || data.error
          || `HTTP ${res.status}`);
        slot.innerHTML = strategyBacktest(data);
        slot.hidden = false;
        // the nested read-out carries its own on-chart control
        wireOnChart(slot, data);
        label.textContent = "Re-run backtest";
        // Onto the card RECORD, which is the same object the thread holds —
        // then one event, because the thread owns saving and this file does
        // not know it exists. A result kept only in the DOM is a result the
        // next reload throws away.
        if (card) {
          card.backtest = data;
          document.dispatchEvent(new CustomEvent("charto:card-updated",
                                                 { detail: { card } }));
        }
      } catch (e) {
        // The failure goes where the result would have gone. A backtest that
        // silently does nothing reads as a dead button.
        slot.innerHTML = `<p class="wf-note">${esc(String(e.message || e))}</p>`;
        slot.hidden = false;
        label.textContent = "Backtest";
      } finally {
        run.disabled = false;
      }
    });
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

  /** The comparison shape: one COLUMN per subject, off a shared baseline.
   *
   *  This replaced a stack of full-width horizontal bars, and the reason is
   *  what the reading actually is. A comparison is "which of these is bigger",
   *  and that question is answered by putting the quantities SIDE BY SIDE
   *  where one glance crosses all of them. Laid out as rows, each bar got the
   *  full panel width and the eye had to travel down five of them, holding
   *  each length in memory to compare it with the next — so five names became
   *  five separate readings and a scroll. Standing up in one row they are one
   *  reading, and the panel that took three screens takes a third of one.
   *
   *  Built from the same tones and the same tokens as the rows it replaces —
   *  colour is per SUBJECT and held across every section, the sign is printed
   *  rather than coloured, and a signed set gets zero down the middle. It is
   *  CSS, not a chart library: these cards are rendered as HTML strings and
   *  injected, so anything needing a live node after insertion would need an
   *  init pass that does not exist here, plus a vendored bundle. Columns are
   *  two boxes and a percentage. */
  function colChart(items, opt) {
    const o = opt || {};
    const vals = items.filter((x) => Number.isFinite(Number(x.value)));
    if (!vals.length) return "";
    const top = vals.reduce((m, x) => Math.max(m, Math.abs(Number(x.value))), 0) || 1;
    // Not the full track: the tallest column needs headroom for its own
    // figure, which rides the bar's outer tip and is part of the reading. On
    // a signed chart that tip can be BELOW zero, so the half-track is held to
    // 32% — at 41% a -30% bar pushed its label down into the name gutter and
    // printed "-27.88%" across "HDFCBANK".
    const span = o.signed ? 32 : 78;
    /* An all-negative set hangs DOWN from a baseline at the top.
     *
     * Maximum drawdown is the case: every value is a fall, so the set holds
     * one sign and the chart is unsigned — and drawn the ordinary way, a
     * -34.59% drawdown grew UPWARD as the tallest bar in the group. The
     * geometry was right and the reading was backwards: the worst fall was
     * the biggest climb on screen, and the reader has to translate the
     * picture against itself to get the sense of it. Inverting the axis makes
     * the picture say what the number says. ATR, all-positive, is untouched. */
    const inv = !o.signed && vals.every((x) => Number(x.value) <= 0);
    const cols = vals.map((x) => {
      const v = Number(x.value);
      const h = Math.max(2, Math.abs(v) / top * span);
      const up = v >= 0;
      // The figure rides the bar's OUTER tip and never crosses it: above a
      // column that grows up, below one that hangs down.
      const vpos = o.signed
        ? (up ? `bottom:${(50 + h).toFixed(1)}%` : `top:${(50 + h).toFixed(1)}%`)
        : (inv ? `top:${h.toFixed(1)}%` : `bottom:${h.toFixed(1)}%`);
      const bpos = o.signed
        ? (up ? `bottom:50%;height:${h.toFixed(1)}%`
              : `top:50%;height:${h.toFixed(1)}%`)
        : (inv ? `top:0;height:${h.toFixed(1)}%`
               : `bottom:0;height:${h.toFixed(1)}%`);
      // `neg` so the rounding can follow the OUTER tip: a column hanging
      // below zero is rounded at its bottom, which is the end the eye reads
      // as its head. Rounding the top of both would put the soft edge at the
      // baseline on one of them, where the flat side belongs.
      return `<div class="cc-col">`
        + `<span class="cc-v" style="${vpos}">${esc(x.text)}</span>`
        + `<i class="cc-bar${(o.signed ? up : !inv) ? "" : " neg"}`
        + `${x.tone ? " " + x.tone : ""}" style="${bpos}"></i>`
        + `<span class="cc-n">${esc(x.label)}</span></div>`;
    }).join("");
    return `<div class="cc-plot${o.signed ? " signed" : ""}${inv ? " inv" : ""}">`
      + `<i class="cc-base"></i>${cols}</div>`;
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
      /* THE TIMEFRAME COMES FIRST, and it is the whole reason this row was
       * reordered. A sweep is per-interval, so "what's forming on all
       * timeframes" is several of these panels stacked — and the interval was
       * named in ONE place: the foot, under thirteen formation tiles and
       * seventeen candle rows. Four panels deep, the only label saying which
       * chart you were reading sat about a screen and a half below the
       * patterns it belonged to, so the stack read as one enormous
       * undifferentiated list. A panel that can appear beside its own
       * siblings has to say which one it is before it says anything else. */
      stat("Timeframe", esc(c.interval || "—")),
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

    /* A formation is four facts — what it is, when it ran, which way the
     * textbook reads it, and the one number that decides it — carried as a
     * clean, scannable ROW rather than a tile in a grid. The eye cannot rank a
     * grid, so a gallery of tiles gave the confirmed formation that answers the
     * question the same weight as the ninth moderate nobody asked about. A
     * ranked list reads top-down, and each row expands IN PLACE into the full
     * detail card (schematic, stats, the watch-not-signal boundary) — so the
     * depth that used to live in the tiles is one click away on every row, not
     * only on the flags. The backend orders them (drawn, then confirmed, then
     * strongest) and says how many are worth showing; the rest fold.
     *
     * Rows and their pre-built detail panels are indexed in lockstep across the
     * WHOLE formation list, so the "N more" fold can reveal later rows without
     * their detail indices colliding with the head's. */
    const srcPatterns = c.chart_patterns || [];
    const nShow = Math.min(srcPatterns.length,
      counts.chart_shown || srcPatterns.length);
    const shownRows = srcPatterns.slice(0, nShow)
      .map((p, i) => patternRow(sym, p, i)).join("");
    const restRows = srcPatterns.slice(nShow)
      .map((p, i) => patternRow(sym, p, nShow + i)).join("");
    // Every formation's detail panel, keyed by its absolute index — built once
    // and shared whether the row is in the head or behind the fold.
    const detailPanels = srcPatterns.map((p, i) =>
      `<div class="pl-detail" data-pat-detail="${i}" hidden>`
      + patternHero(c, p) + `</div>`).join("");
    // The overflow rows live INSIDE the same list card, hidden until revealed,
    // so "N more" extends one connected table rather than opening a second
    // bordered list beneath the first. The detail panels sit at the end of the
    // same card, so expanding any row — head or revealed — drops its detail in
    // place within the one list.
    const restWrap = restRows
      ? `<div class="pl-rest" data-more-rows hidden>${restRows}</div>` : "";
    const rowsMore = restRows
      ? `<button type="button" class="scan-more" data-more="[data-more-rows]">`
        + `${srcPatterns.length - nShow} more</button>`
      : "";
    const patternBlock = shownRows
      ? `<div class="pl-list">${shownRows}${restWrap}${detailPanels}</div>${rowsMore}`
      : "";

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

    const foot = `<div class="scan-foot">${esc(c.bars_scanned)} ${esc(c.interval)} bars`
      + (c.window ? ` · ${esc(c.window)}` : "") + `</div>`;

    const briefMode = c.density === "brief";

    /* The chart-patterns section: the clean formation list, with the "N more"
     * fold and the detail panels its rows expand into. One block, used as the
     * section body in the full sweep and as the brief head alike, so the two
     * modes render the same list rather than two divergent ones. */
    const patternSection = section("Chart patterns",
      drew(counts.chart_found, counts.chart_drawn, "found", "drawn"), patternBlock);

    /* The whole sweep, which is a BODY rather than the return value. In brief
     * mode the formation list is the head (above the fold), so the folded body
     * carries only what the head did not — a panel that repeated the list would
     * also duplicate its detail-panel ids and break the expand wiring. */
    const sweepRest = section("Structure events", "", events)
      + section("Candlestick patterns",
                drew(counts.candle_bars, counts.candles_marked, "bars", "marked"),
                candles && `<div class="scan-rows${more ? " capped" : ""}">${candles}</div>${more}`);

    if (!briefMode) {
      return `<div class="scan-stats">${stats}</div>`
        + section("Structure events", "", events)
        + patternSection
        + section("Candlestick patterns",
                  drew(counts.candle_bars, counts.candles_marked, "bars", "marked"),
                  candles && `<div class="scan-rows${more ? " capped" : ""}">${candles}</div>${more}`)
        + foot;
    }

    /* ── the brief head ───────────────────────────────────────────────
     *
     * A sweep that marked nothing is EVIDENCE for the prose, not the answer to
     * the question. The compact form is the ranked formation list at full
     * presence — each row carrying the neckline price and its verdict, each one
     * expanding in place into the schematic detail — and everything else the
     * sweep found (the stat grid, the structure events, the candle bars) folded
     * one click away, never dropped. */
    const restBars = counts.candle_bars || 0;
    const rest = [restBars ? `${restBars} candle bar${restBars === 1 ? "" : "s"}` : "",
                  "structure events", "scan stats"].filter(Boolean).join(" · ");

    return patternSection
      + `<button type="button" class="scan-more" data-more="[data-more-full]">`
      + `${esc(rest)}</button>`
      /* `hidden` rather than a class: nothing styles this wrapper, so the UA
       * rule applies cleanly and the reveal needs no CSS of its own — which
       * matters because this panel's stylesheet lives in index.html and a
       * renderer should not need an edit there to ship a fold. */
      + `<div data-more-full hidden><div class="scan-stats">${stats}</div>${sweepRest}</div>`
      + foot;
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

    /* Applied studies use the same row grammar as the fixed overlays: name,
     * reason, directional reading. Their raw values already live on the chart
     * and would only duplicate the legend here. */
    const onChart = (c.chart_studies || []).map((s) => {
      return `<div class="scan-read${s.tone ? " tone-" + s.tone : ""}">`
        + `<b class="nm">${esc(s.name)}</b>`
        + `<span class="nt">${esc(s.note || "")}</span>`
        + `<b class="num">${esc(cap(s.state))}</b></div>`;
    }).join("");

    /* Each momentum reading is three things and needs all three: the state,
     * the figures it was read off, and the comparison that earned the state.
     * "Easing" on its own is an opinion; "−1.35 · less negative than the
     * prior bar" is the measurement the word is short for. */
    // The contents STACK — badge, name, figures — so they wrap into a
    // `.scan-tile-main` column. Without it the three sit on the flex ROW that
    // `.scan-tile` is, which turned the badge into a left-hand circle and
    // truncated the name to "RSI…"; the column is how the strength tiles above
    // already lay out, so the two read the same.
    const tiles = (c.readings || []).map((r) => `<div class="scan-tile">`
      + `<div class="scan-tile-main">`
      + (r.state ? `<span class="scan-badge ${r.tone === "up" ? "ok" : "bad"}">`
                   + `${esc(cap(r.state))}</span>` : "")
      + `<b class="nm">${esc(r.name)}</b>`
      + `<span class="scan-fact"><b>${esc(r.figures)}</b>`
      + (r.why ? ` · ${esc(r.why)}` : "") + `</span></div></div>`).join("");

    return `<div class="scan-stats">${stats}</div>`
      + section("On this chart", "applied studies",
                onChart && `<div class="scan-reads">${onChart}</div>`)
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
  // Every timeframe measured the same four ways, so the rows compare. The
  // stance word takes the row's tint and the four readings stay printed
  // beside it, because six coloured words with no numbers is a mood board.
  // ADX and RSI then get their own bar rows ACROSS the timeframes, which is
  // the one comparison a single row cannot make: 29 on the 15-minute and 14
  // on the daily is the finding, and it is invisible in six separate rows.
  //
  // THE WORD "RUNG" DOES NOT APPEAR IN THIS PANEL, and that is a rule rather
  // than a preference. `rung` is the internal name for a row of the ladder —
  // fine in code, and it had leaked into the interface as "Rungs measured",
  // "3 of 3 rungs" and a heading reading "Every rung, measured the same four
  // ways". A reader has never seen that word and cannot look it up: the
  // thing being counted is a TIMEFRAME, which they already know, and naming
  // it anything else buys nothing and costs comprehension. Internal labels
  // in the UI are the single most reliable sign that nobody read the screen
  // back as a stranger would. Keep the variable names; never print them.
  function timeframes(c) {
    const sym = c.symbol;
    const rungs = c.rungs || [];
    // Counted by the tool, not here — the panel draws, it does not derive.
    const tally = c.tally || {};
    const stats = [
      c.price != null ? stat("Price", money(sym, c.price)) : "",
      stat("Timeframes", rungs.length,
           "", (c.unavailable || []).length
             ? `${(c.unavailable || []).length} unavailable` : ""),
      // "Leaning" was a hedge word doing a noun's job. The figure under it
      // is already the hedge — "3 of 4 agree" says the disagreement out
      // loud — so the label can simply name the axis being read.
      tally.leaning
        ? stat("Direction", cap(tally.leaning), TONE[tally.leaning] || "",
               `${tally.majority} of ${tally.of} agree`) : "",
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
      ].filter(Boolean);
      /* Each reading is its own element, not one joined string.
       *
       * Joined, the line broke wherever it ran out of room — mid-measurement,
       * so a rung read "… · below EMA 5" and the "0" started the next line.
       * A number split across two lines is not a number. Wrapped separately
       * each reading is atomic (white-space: nowrap on the child) and the
       * break can only happen BETWEEN readings, which is the only place a
       * break means anything. The separator hangs off the preceding item so a
       * wrapped line never opens with a stray dot. */
      return `<div class="scan-read${t ? " tone-" + t : ""}">`
        + `<b class="nm">${esc(r.label)}</b>`
        + `<span class="nt">${bits.map((b) => `<i>${esc(b)}</i>`).join("")}</span>`
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

    const momentum = section("Each timeframe", "RSI · MACD · ADX · EMA 50", rows)
      + section("ADX by timeframe", "25 marks a trending phase", adxBars)
      + section("RSI by timeframe", "50 is the midline", rsiBars);
    const shared = section("Levels several timeframes share", "", ladder);

    /* WHICH HALF IS THE ANSWER.
     *
     * This panel carries two unrelated readings — momentum on every rung, and
     * the level detector pooled across them — and it used to print them in
     * one fixed order with the levels last, three sections down. Asked "where
     * are the key support levels across timeframes" the tool was right, the
     * prose was right, and the panel showed RSI, MACD and ADX; the zones the
     * question was about were below the fold and went unseen.
     *
     * `focus` comes from the tool (see _mtf_focus): the model declares what
     * the question is about and this only obeys it. So the panel leads with
     * the levels and folds the momentum away — folds, not drops, because the
     * other half is still true and one click is a cheaper way to be wrong
     * about the ask than a missing section is. */
    if (c.focus === "levels" && ladder) {
      return `<div class="scan-stats">${stats}</div>`
        + shared
        + `<button type="button" class="scan-more" data-more="[data-more-mtf]">`
        + `momentum on ${rungs.length} timeframe${rungs.length === 1 ? "" : "s"}`
        + `</button><div data-more-mtf hidden>${momentum}</div>`
        + gone;
    }
    return `<div class="scan-stats">${stats}</div>`
      + momentum + shared + gone;
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
    if (!syms.length || !cols.length) return "";

    // The PRIMARY interval drives the table — one row per symbol, ranked by
    // return, highest first — which is the same shape and visual language as a
    // screen. A multi-interval comparison still measures every window, but the
    // reading a table makes best is "who is ahead, and by how much", and that
    // is one window's ordering. The per-interval gaps and correlation ride
    // above as context, the way a screen's criteria do.
    const primary = cols[0];
    const g = (pick, s) => (primary[pick] ? primary[pick][s] : null);

    // Context strip: the return gap and correlation the model reads from, kept
    // because they are the two numbers a pair comparison is usually about.
    const stats = [];
    for (const gap of (c.gaps || [])) {
      stats.push(stat(`${gap.label} return gap`, signed(sym, gap.gap_pp, " pp"),
                      way(gap.gap_pp), gap.pair));
    }
    for (const col of cols) {
      const corr = col.correlation && pair
        ? col.correlation[`${syms[0]}~${syms[1]}`] : null;
      if (corr != null) {
        stats.push(stat(`${col.label} correlation`, n2(sym, corr), "",
                        "daily returns"));
      }
    }

    // Rank the symbols by the primary interval's return, highest first. The
    // benchmark rides in as a final, unranked row where the window carries one,
    // because "both fell" and "both fell while the index fell too" are
    // different answers.
    const ranked = syms.slice().sort((a, b) => {
      const ra = g("ret", a), rb = g("ret", b);
      if (ra == null && rb == null) return 0;
      if (ra == null) return 1;
      if (rb == null) return -1;
      return rb - ra;
    }).map((s) => ({ sym: s }));

    const units = { cr: " cr", musd: " M$" };
    const turn = (s) => {
      const t = primary.turnover && primary.turnover[s];
      return t ? esc(Sym.of(sym).num(t.value, { maximumFractionDigits: 1 })
                     + (units[t.unit] || "")) : "—";
    };
    const haveTurn = ranked.some((r) => primary.turnover && primary.turnover[r.sym]);

    const cols2 = [
      { label: "Return", align: "num",
        get: (r) => { const v = g("ret", r.sym);
          return v == null ? "—"
            : `<span class="sc-tone ${way(v)}">${signed(sym, v, "%")}</span>`; } },
      { label: "Max DD", align: "num",
        get: (r) => { const v = g("dd", r.sym); return v == null ? "—" : `${n2(sym, v)}%`; } },
      { label: "ATR %", align: "num",
        get: (r) => { const v = g("atr", r.sym); return v == null ? "—" : `${n2(sym, v)}%`; } },
    ];
    if (haveTurn) cols2.push({ label: "Turnover", align: "num", get: (r) => turn(r.sym) });

    const windowLabel = primary.window
      ? `${primary.label} · ${primary.window}` : primary.label;
    const meta = `${ranked.length} symbols · ranked by ${primary.label} return`
      + (c.intervals.length > 1
          ? ` · also measured on ${c.intervals.slice(1).map((x) => x.label).join(", ")}` : "");

    // The index as a final, muted footer row inside the same table — its
    // return next to the names it is the baseline for.
    let extraFoot = "";
    if (primary.benchmark && primary.benchmark.ret != null) {
      const b = primary.benchmark;
      extraFoot = `<tr class="sc-foot"><td></td>`
        + `<td class="sc-foot-label">${esc(b.name)}</td>`
        + `<td class="sc-num"><span class="sc-tone ${way(b.ret)}">${signed(sym, b.ret, "%")}</span></td>`
        + `<td></td>`.repeat(cols2.length - 1)
        + `</tr>`;
    }

    const table = screenTable({
      title: "Peer comparison",
      criteria: windowLabel ? `Window: ${windowLabel}` : "",
      meta,
      firstCol: "Symbol",
      rows: ranked,
      cols: cols2,
      rowSym: (r) => ({ sym: r.sym, name: null }),
      page: ranked.length,   // a peer set is short; never fold it
      extraFoot,
    });

    return (stats.length ? `<div class="scan-stats">${stats.join("")}</div>` : "")
      + table
      + ((c.unavailable || []).length
         ? callout(`Not measured: ${(c.unavailable || []).join(" · ")}`) : "");
  }

  // ── why it moved ────────────────────────────────────────────────────
  //
  // The size question FIRST, because a move inside its own normal range
  // needs no story and a panel that opened with a cause would have conceded
  // there was one. Then where inside the session it happened, then the
  // ladder price is standing on. The evidence-search context remains
  // available to the model without being repeated in this dense card.
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
      + section("Levels around price", "", ladder)
      + section("Index attribution", "", idx)
      + foot(win.from && win.to
             ? (win.from === win.to ? win.from : `${win.from} → ${win.to}`) : "");
  }

  /* ── a registered plan ────────────────────────────────────────────────
   *
   * A plan is the one card on this surface that can SPEND, so it is built
   * around the difference between what has been decided and what has been
   * done. The legs are the decision; the Activate button is the doing; and
   * until it is pressed every leg reads "pending" rather than a quantity,
   * because the share counts are resolved against the mark at press time and
   * printing a number now would be printing a number that will not be the one
   * that fills.
   *
   * It is a MANIFEST and nothing else: what will be bought, how much of each,
   * why, and what has happened to it. The rationale paragraph, the assumptions
   * list, the evidence list and the review line were all here once, and all
   * four were prose — which put the same five tickers on screen twice, once in
   * a sentence and again in the rows underneath it, inside a panel whose whole
   * job is to be the row underneath. The model already says all of it in the
   * reply, better, because prose is what a reply is for. The card is the part
   * you press.
   */
  /* Symbol, size, what happened. The per-leg `why` is NOT here: it is two to
   * three lines of the model's prose per name, so an eight-name basket was
   * a page of argument wearing a table's clothes — and the same argument is
   * in the reply above, written once, where it reads as writing. A row in a
   * manifest answers "what and how much", and the answer to "why" that
   * belongs beside it is the fill price. */
  function planLegRow(l) {
    const size = l.quantity != null ? `${l.quantity} sh`
      : l.weight_pct != null ? `${Number(l.weight_pct).toFixed(1)}%`
      : l.notional_inr != null ? money(l.symbol, l.notional_inr) : "—";
    const state = l.state === "filled"
      ? `filled at ${money(l.symbol, l.fill_price)}`
      : l.state === "armed" ? "armed — waiting on its condition"
      : l.state === "rejected" ? (l.detail || "refused")
      : (l.conditional ? "waits for its condition" : "buys on activate");
    return `<div class="plan-leg" data-leg-state="${esc(l.state)}">`
      + `<b>${esc(l.symbol)}</b><span class="plan-size">${esc(size)}</span>`
      + `<i class="plan-state">${esc(state)}</i></div>`;
  }

  /* No weights chart. Every leg row already carries its own size beside its
   * symbol, so a bar list above them was the same eight numbers a second
   * time, ranked — and ranking is not the question here. A plan is a list of
   * decisions with a button under it; the sizes are one column of that list,
   * not a chart of their own. */
  function plan(c) {
    const legs = c.legs || [];
    const done = legs.some((l) => l.state === "filled" || l.state === "armed");
    const live = c.state === "active";
    /* Capital and leg count, and nothing else. `horizon` is a free-text field
     * the model fills, and it arrives as a sentence ("not specified; review
     * after 6 months") at least as often as it arrives as a phrase — which is
     * a paragraph wrapped onto two lines of a status chip. It is prose, so it
     * belongs in the reply with the rest of the prose. */
    const meta = [
      c.capital_inr != null ? money(legs[0] && legs[0].symbol, c.capital_inr) : "",
      `${legs.length} leg${legs.length === 1 ? "" : "s"}`,
    ].filter(Boolean).join(" · ");
    // The button's label is the honest description of what pressing it does,
    // and it differs by plan: a basket buys, a set of conditions arms, a mix
    // does both. "Activate" alone would hide which.
    const nCond = legs.filter((l) => l.conditional).length;
    const cta = nCond === legs.length ? "Arm this plan"
      : nCond === 0 ? "Buy this plan" : "Activate — buy and arm";
    return `<div class="wf-card" data-plan="${esc(String(c.id || ""))}">`
      + `<div class="wf-top"><span class="wf-chip">Plan</span>`
      + `<span class="wf-state">${esc(meta)}`
      + `<span class="wf-dot${live ? " on" : ""}" aria-hidden="true"></span>`
      + `${live ? "Active" : "Registered"}</span></div>`
      + `<h3 class="wf-title">${esc(c.name || "Plan")}</h3>`
      + section("Legs", "", legs.map(planLegRow).join(""))
      + (c.last_error ? `<p class="wf-stale" role="status">`
          + `${esc(c.last_error)}</p>` : "")
      + `<div class="wf-cta">`
      + `<button type="button" class="wf-primary" data-plan-go`
      + (done || live ? " disabled" : "")
      + `>${esc(live || done ? "Activated" : cta)}</button>`
      + `<div class="wf-ghosts"></div></div>`
      + `<p class="wf-note plan-note">`
      + (live || done
          ? "Filled into the simulated paper book. No real order was placed."
          : "Nothing has been bought. Pressing this fills the simulated paper "
            + "book at the current mark — no real order is ever placed.")
      + `</p></div>`;
  }

  /* The Activate press. One POST, and the card repaints from what came back
   * rather than from what it hoped — a leg that was refused for want of a
   * price has to say so on the card that offered it, beside the ones that
   * filled. Partial success is the normal outcome for a ten-name basket and
   * it is rendered as such, not as an error banner over a working plan. */
  function wirePlan(box, card) {
    const btn = box.querySelector("[data-plan-go]");
    if (!btn || btn.disabled) return;
    /* The id is on the CARD, and `box` is the .scan wrapper render() put
     * around it — so `box.getAttribute("data-plan")` was reading an attribute
     * that has never been on that element. It returned null, the guard below
     * disabled the button, and every plan card this build has ever drawn
     * arrived with a dead Activate. Nothing was refused and nothing errored;
     * the POST simply never happened, which is why the plans table is full of
     * `draft` rows with an empty `last_error`. Ask the element that carries
     * the attribute, the way every other handler in this file does. */
    const holder = box.querySelector("[data-plan]");
    const id = holder && holder.getAttribute("data-plan");
    if (!id) { btn.disabled = true; return; }
    const label = btn.textContent;
    btn.addEventListener("click", async () => {
      if (btn.disabled) return;
      btn.disabled = true;
      btn.textContent = "Working…";
      try {
        const res = await fetch(`${API}/plans/${encodeURIComponent(id)}/activate`, {
          method: "POST",
          headers: (window.Auth && Auth.headers)
            ? Auth.headers({ "Content-Type": "application/json" })
            : { "Content-Type": "application/json" },
          body: "{}",
        });
        const data = await res.json();
        if (!res.ok) {
          /* The server's refusal is a sentence the user can act on — "sign in
           * to use plans", "this plan was retired". Printed as the button's
           * label it was both unreadable and terminal: the control kept the
           * error as its name and stayed disabled, so a signed-out user could
           * never press it again after signing in. Same treatment the draft
           * card already gives a refusal — a note under the CTA, button back. */
          btn.textContent = label;
          btn.disabled = false;
          const note = document.createElement("p");
          note.className = "wf-stale";
          note.setAttribute("role", "status");
          note.textContent = res.status === 401
            ? "Sign in to activate — a plan fills into your own paper book."
            : String((data && data.error) || "Could not activate this plan.");
          const cta = box.querySelector(".wf-cta");
          const prev = box.querySelector(".wf-cta ~ .wf-stale");
          if (prev) prev.remove();
          if (cta) cta.after(note);
          return;
        }
        /* Repaint IN PLACE. `box.replaceWith(rebuilt)` swapped the .scan
         * wrapper for the bare .wf-card inside it, so an activated plan lost
         * the panel chrome — and its `data-card` — the moment it succeeded. */
        box.innerHTML = plan(Object.assign({}, card, data));
      } catch (e) {
        console.warn("[charto] plan activate failed", e);
        btn.disabled = false;
        btn.textContent = label;
      }
    });
  }

  /* ── the one table design, shared by `screen` and `compare` ───────────
   *
   * A bordered, rounded card carrying a ranked table — the same shape the main
   * chat's ScreenResultsCard renders, so a screen on the chart and a screen in
   * the full chat read as one product. The caller supplies the title, the one
   * head control, the chips/meta above the table, the columns, the rows, and
   * an optional median footer; everything below here is presentation.
   *
   * `cols`: [{ label, align?: "num"|"text", get(row) -> cell html,
   *            foot?(rows) -> cell html }]. A cell's html is trusted — callers
   * build it through `esc`/`signed`/`num`. `rowSym(row)` gives the symbol and
   * name for the company cell; `PAGE` rows show, the rest fold behind "Show
   * all N" through the panel's own [data-more] handler.
   */
  const SCREEN_PAGE = 10;

  function symCell(sym, name) {
    const initials = esc(String(sym || "?").slice(0, 2));
    return `<div class="sc-symcell">`
      + `<span class="sc-logo" data-logo="${esc(sym)}">${initials}</span>`
      + `<span class="sc-sym"><b>${esc(sym)}</b>`
      + (name && name !== sym ? `<span>${esc(name)}</span>` : "")
      + `</span></div>`;
  }

  function screenTable(opt) {
    const rows = opt.rows || [];
    const cols = opt.cols || [];
    if (!rows.length) return "";

    const head = `<tr><th class="sc-rank">#</th><th class="sc-sym">${esc(opt.firstCol || "Company")}</th>`
      + cols.map((col) =>
          `<th class="${col.align === "text" ? "" : "sc-num"}">${esc(col.label)}</th>`).join("")
      + `</tr>`;

    const rowHtml = (r, i) =>
      `<tr><td class="sc-rank">${i + 1}</td>`
      + `<td>${symCell(opt.rowSym(r).sym, opt.rowSym(r).name)}</td>`
      + cols.map((col) =>
          `<td class="${col.align === "text" ? "" : "sc-num"}">${col.get(r)}</td>`).join("")
      + `</tr>`;

    const page = Math.max(1, opt.page || SCREEN_PAGE);
    const shown = rows.slice(0, page).map(rowHtml).join("");
    const rest = rows.length > page
      ? `<tbody data-more-rows hidden>`
        + rows.slice(page).map((r, i) => rowHtml(r, page + i)).join("")
        + `</tbody>` : "";

    // The median / summary footer, drawn only when a column asks for one, plus
    // any explicit extra footer rows the caller supplies (a benchmark row).
    const medianRow = cols.some((col) => typeof col.foot === "function")
      ? `<tr class="sc-foot">`
        + `<td></td><td class="sc-foot-label">${esc(opt.footLabel || `Median of ${rows.length}`)}</td>`
        + cols.map((col) => {
            const f = typeof col.foot === "function" ? col.foot(rows) : "";
            return `<td class="${col.align === "text" ? "" : "sc-num"}">${f || ""}</td>`;
          }).join("")
        + `</tr>` : "";
    const footRows = medianRow + (opt.extraFoot || "");
    const foot = footRows ? `<tfoot>${footRows}</tfoot>` : "";

    const more = rows.length > page
      ? `<button type="button" class="sc-more" data-more="[data-more-rows]">`
        + `Show all ${rows.length}`
        + `<svg class="icon" viewBox="0 0 24 24" width="14" height="14" fill="none" `
        + `stroke="currentColor" stroke-width="2" stroke-linecap="round" `
        + `stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>`
        + `</button>` : "";

    return `<div class="sc-card">`
      + `<div class="sc-head"><div class="sc-title">${esc(opt.title)}</div>`
      + (opt.control || "") + `</div>`
      + (opt.chips || "")
      + (opt.criteria ? `<div class="sc-crit">${esc(opt.criteria)}</div>` : "")
      + (opt.rankLine ? `<div class="sc-rank-line">${esc(opt.rankLine)}</div>` : "")
      + (opt.meta ? `<div class="sc-meta">${esc(opt.meta)}</div>` : "")
      + `<div class="sc-tablewrap"><table class="sc-table">`
      + `<thead>${head}</thead>`
      + `<tbody>${shown}</tbody>${rest}${foot}</table></div>`
      + more
      + `</div>`;
  }

  /** The middle value of a numeric column — presentation arithmetic over what
   *  the card already shows, drawn in the footer. Null below three numbers. */
  function median(rows, get) {
    const xs = rows.map(get)
      .filter((v) => typeof v === "number" && Number.isFinite(v))
      .sort((a, b) => a - b);
    if (xs.length < 3) return null;
    const m = xs.length >> 1;
    return xs.length % 2 ? xs[m] : (xs[m - 1] + xs[m]) / 2;
  }

  /** A universe screen: what it looked for, what matched, and a control that
   *  carries the membership to the Screener.
   *
   *  The panel lists the top rows only — the same page the reply prints — but
   *  the BUTTON hands over `card.symbols`, which is every match. A user who
   *  opens a 44-name screen and finds 15 has been given a different screen
   *  than the one they were shown the count for.
   *
   *  The control is only rendered when this chart is FRAMED by the shell,
   *  because the Screener is the shell's tab: standalone on :5173 there is
   *  nowhere for it to go, and a button that does nothing is worse than no
   *  button. `window.parent !== window` is the whole test — the frame is
   *  same-origin by construction (next.config proxies /chart-app), which is
   *  also why the postMessage below can name a concrete origin.
   */
  function screen(c) {
    const rows = Array.isArray(c.rows) ? c.rows : [];
    if (!rows.length) return "";
    const syms = Array.isArray(c.symbols) ? c.symbols : rows.map((r) => r.symbol);
    const sortKey = (c.sorted_by && c.sorted_by.feature) || "";
    // Up to three numeric columns beyond the symbol, chosen from the row the
    // screen actually returned — the engine already trimmed these to the
    // features the query referenced, so this shows what was screened on
    // rather than a fixed set that might be all nulls.
    const SKIP = new Set(["symbol", "name", "industry", "as_of", "pattern",
                          "universe_rate", "volume_profile"]);
    const keys = [];
    if (sortKey && rows.some((r) => r[sortKey] != null)) keys.push(sortKey);
    for (const k of Object.keys(rows[0] || {})) {
      if (keys.length >= 3) break;
      if (SKIP.has(k) || k === sortKey) continue;
      if (rows.some((r) => typeof r[k] === "number")) keys.push(k);
    }
    const label = (k) => k.replace(/_/g, " ");
    const fmt = (v) => (typeof v === "number"
      ? (Math.abs(v) >= 1000 ? v.toFixed(0) : v.toFixed(2))
      : (v == null ? "—" : esc(String(v))));
    const cols = keys.map((k) => ({
      label: label(k),
      align: rows.some((r) => typeof r[k] === "number") ? "num" : "text",
      get: (r) => fmt(r[k]),
      foot: (rr) => { const m = median(rr, (r) => r[k]); return m == null ? "" : fmt(m); },
    }));

    const framed = (() => {
      try { return window.parent && window.parent !== window; }
      catch { return false; }
    })();
    // Count line stays honest about the three numbers that differ: matched,
    // shown here, and the universe they came from.
    const matched = c.matched != null ? c.matched : rows.length;
    const meta = `${matched} of ${c.universe || "?"}`
      + (rows.length < matched ? ` · top ${rows.length}` : "")
      + (c.as_of ? ` · ${String(c.as_of)}` : "");

    const control = framed
      ? `<button type="button" class="ui-btn ui-btn-sm" data-screen-open `
        + `title="Open these ${syms.length} names in the Screener">Open`
        + `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" `
        + `stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" `
        + `aria-hidden="true"><path d="M7 17 17 7"/><path d="M8 7h9v9"/></svg></button>`
      : "";

    return screenTable({
      title: c.title || "Screen",
      control,
      criteria: c.criteria || "",
      rankLine: c.ranking || "",
      meta,
      firstCol: "Company",
      rows,
      cols,
      rowSym: (r) => ({ sym: r.symbol, name: r.name }),
    });
  }

  /** Hand this screen to the shell's Screener tab.
   *
   *  Charto does NOT persist the screen. It cannot: saving is per-user and the
   *  Pivot user table and Charto's are disjoint numbering schemes over
   *  different databases, so a screen written from here would be filed under
   *  whichever stranger happens to hold that id on the other side. The shell
   *  holds the Pivot session, so the shell saves. This is a handover, and the
   *  message is the whole payload — the receiver needs nothing from us later.
   *
   *  Targeted at our own origin rather than "*": the frame is same-origin by
   *  construction, and a wildcard would post a user's screen to whatever else
   *  ever ends up hosting this page.
   */
  function wireScreen(box, card) {
    const btn = box.querySelector("[data-screen-open]");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const syms = Array.isArray(card.symbols) && card.symbols.length
        ? card.symbols
        : (card.rows || []).map((r) => r.symbol).filter(Boolean);
      if (!syms.length) return;
      try {
        window.parent.postMessage({
          type: "charto:open-screen",
          screen: {
            symbols: syms,
            criteria: card.criteria || "",
            ranking: card.ranking || "",
            as_of: card.as_of || "",
            matched: card.matched != null ? card.matched : syms.length,
            universe: card.universe || 0,
            sorted_by: card.sorted_by || null,
            filters_applied: card.filters_applied || [],
          },
        }, window.location.origin);
        // Acknowledge in place. The tab switch happens in the parent, which
        // this frame cannot observe, so without this the button looks inert
        // on a slow switch and gets pressed again.
        btn.classList.add("is-done");
        const was = btn.textContent;
        btn.textContent = "Opened in Screener";
        setTimeout(() => {
          btn.classList.remove("is-done");
          btn.textContent = was;
        }, 2400);
      } catch (e) {
        console.warn("[charto] screen handover failed", e);
      }
    });
  }

  // ── a custom indicator, as a file ───────────────────────────────────
  //
  // The name, an animated document icon, and two icon buttons: put it on or
  // take it off the chart, and open its code in the sidebar (codeview.js).
  // Everything else about the build lives behind Open.
  //
  // The icon is Lordicon's wired-outline document (vendor/icons/README.md —
  // free licence, attribution required), played by lottie-web: it unfolds
  // once when the card lands and again on hover, recoloured to the theme's
  // ink and the brand teal through the icon's own colour classes.
  let LOTTIE = null;
  function lottieKit() {
    if (!LOTTIE) {
      LOTTIE = new Promise((ok, bad) => {
        const s = document.createElement("script");
        s.src = "./vendor/icons/lottie_light.min.js";
        s.onload = () => fetch("./vendor/icons/document.json").then((r) => r.json())
          .then((data) => ok({ lottie: window.lottie, data })).catch(bad);
        s.onerror = bad;
        document.head.appendChild(s);
      });
    }
    return LOTTIE;
  }
  const hexRgb = (h) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(h).trim());
    if (!m) return null;
    const n = parseInt(m[1], 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255, 1];
  };
  /** A deep copy with every primary/secondary stroke and fill set to `pal`. */
  function recolour(data, pal) {
    const walk = (o) => {
      if (Array.isArray(o)) { o.forEach(walk); return; }
      if (!o || typeof o !== "object") return;
      if ((o.ty === "st" || o.ty === "fl") && pal[o.cl]) {
        o.c = { a: 0, k: pal[o.cl] };
      }
      for (const k in o) if (o[k] && typeof o[k] === "object") walk(o[k]);
    };
    const copy = JSON.parse(JSON.stringify(data));
    walk(copy);
    return copy;
  }
  function mountIcon(host, failed) {
    lottieKit().then(({ lottie, data }) => {
      const css = getComputedStyle(host);
      const ink = hexRgb(css.getPropertyValue("--cx-ink")) || [0.05, 0.05, 0.06, 1];
      const tint = hexRgb(css.getPropertyValue("--cx-tint")) || [0.03, 0.6, 0.5, 1];
      host.innerHTML = "";
      const anim = lottie.loadAnimation({
        container: host, renderer: "svg", loop: false, autoplay: false,
        animationData: recolour(data, { primary: ink, secondary: tint }),
        rendererSettings: { preserveAspectRatio: "xMidYMid meet" },
      });
      const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
      anim.addEventListener("DOMLoaded", () => {
        if (still || failed) anim.goToAndStop(269, true);     // the rest pose
        else anim.playSegments([70, 160], true);              // in-unfold
      });
      if (!still) {
        const card = host.closest(".cx-file");
        card.addEventListener("mouseenter", () => anim.playSegments([269, 359], true));
      }
    }).catch(() => { host.innerHTML = Icons.svg("fileText", "sm"); });
  }

  function customIndicator(c) {
    const toggle = c.ok && c.id
      ? `<button type="button" class="cx-btn cx-chart" data-cx-act="toggle" aria-pressed="false"
           title="Add to chart" aria-label="Add to chart">`
        + `<span class="cx-off">${Icons.svg("plus", "sm")}</span>`
        + `<span class="cx-on">${Icons.svg("check", "sm")}</span>`
        + `<span class="cx-rm">${Icons.svg("x", "sm")}</span></button>` : "";
    const why = c.ok ? "" : (c.kept_previous ? "Edit failed validation — the previous version is kept"
                                             : "Failed validation — not on the chart");
    return `<div class="cx-file${c.ok ? "" : " is-bad"}"${why ? ` title="${esc(why)}"` : ""}>`
      + `<span class="cx-file-icon" data-cx-icon title="Animated icon by Lordicon.com"></span>`
      + `<button type="button" class="cx-file-name" data-cx-act="open"${c.id ? "" : " disabled"}>`
      + `${esc(c.title || "Custom indicator")}</button>`
      + `<span class="cx-file-acts">${toggle}`
      + `<button type="button" class="cx-btn" data-cx-act="open" title="Open code" aria-label="Open code"`
      + `${c.id ? "" : " disabled"}>${Icons.svg("panelRight", "sm")}</button></span></div>`;
  }

  /* The chart toggle reflects the chart, not the moment the card was printed:
   * main.js answers `__chartoCustomActive(id)`, and every change to the active
   * set (menu, legend ×, chat) repaints every card. */
  function syncToggle(box, id) {
    const b = box.querySelector(".cx-chart");
    if (!b) return;
    const on = typeof window.__chartoCustomActive === "function" && window.__chartoCustomActive(id);
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.title = on ? "Remove from chart" : "Add to chart";
    b.setAttribute("aria-label", b.title);
  }

  function wireCustom(box, card) {
    box.querySelectorAll("[data-cx-act]").forEach((b) => b.addEventListener("click", (e) => {
      e.stopPropagation();
      let action = b.dataset.cxAct;
      if (action === "toggle") {
        action = b.getAttribute("aria-pressed") === "true" ? "remove" : "add";
      }
      document.dispatchEvent(new CustomEvent("charto:custom-indicator",
        { detail: { action, id: card.id } }));
    }));
    const icon = box.querySelector("[data-cx-icon]");
    if (icon) mountIcon(icon, !card.ok);
    syncToggle(box, card.id);
    document.addEventListener("charto:indicators-changed", () => {
      if (box.isConnected) syncToggle(box, card.id);
    });
  }

  const RENDER = { patterns, trend, indicators, confirmation, timeframes,
                   compare, move, screen, workflow_draft: workflowDraft,
                   strategy_backtest: strategyBacktest,
                   option_strategy: optionStrategy,
                   option_chain: optionChain,
                   quant_result: quantResult, plan,
                   custom_indicator: customIndicator };

  return {
    /** A card object → an element for the thread, or null when this build has
     *  no renderer for that kind. Null rather than a placeholder: a panel
     *  reading "unsupported card" tells the user about our deploy schedule
     *  and nothing about their chart. */
    render(card) {
      if (!card || !RENDER[card.kind]) return null;
      // The thread card is the backend-grounded pattern inventory. Mirror it
      // into the adjacent drawer so the chart can stay quiet while every
      // detection remains inspectable. This is data handoff only: the drawer
      // never invents, ranks or modifies detector values.
      if (card.kind === "patterns" && window.PatternDrawer) {
        window.PatternDrawer.setPatterns(card);
      }
      let html;
      try {
        html = RENDER[card.kind](card);
      } catch (e) {
        console.warn("[charto] card render failed", card, e);
        return null;      // a broken panel must never cost the reply
      }
      if (!html) return null;
      const box = document.createElement("div");
      // the file card is its own object, not a measurement panel
      box.className = card.kind === "custom_indicator" ? "cx-file-card" : "scan";
      box.dataset.card = card.kind;
      box.innerHTML = html;
      /* Every fold in the panel, not the first one. This used to reach for a
       * single `[data-more]` and unconditionally uncap `.scan-rows.capped`,
       * which was exactly right while the only fold in existence was the
       * candle list. A panel now carries up to three — the brief body, the
       * tile overflow, the candle rows — and the old handler would have wired
       * the first and thrown on the others, taking the whole panel down with
       * it (the render is inside a try, this wiring is not).
       *
       * The button names its own target in `data-more`; a bare `data-more`
       * keeps the original meaning, so the candle list needed no change. */
      box.querySelectorAll("[data-more]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const sel = btn.getAttribute("data-more");
          const target = sel ? box.querySelector(sel)
                             : box.querySelector(".scan-rows.capped");
          if (!target) return;
          target.classList.remove("capped");
          target.hidden = false;
          btn.remove();
        });
      });
      /* A formation row expands into its detail card IN PLACE. The panels were
       * rendered once, after the list, keyed by the row's index; on first open
       * the panel is moved to sit directly beneath its row so the detail reads
       * as belonging to the line it came from, then toggled on every click.
       * Moving rather than cloning keeps one panel per formation, so a row in
       * the folded "N more" list opens the same card as one in the head. */
      box.querySelectorAll(".pl-row[data-pat]").forEach((rowEl) => {
        rowEl.addEventListener("click", () => {
          const idx = rowEl.getAttribute("data-pat");
          const panel = box.querySelector(`.pl-detail[data-pat-detail="${idx}"]`);
          if (!panel) return;
          // Land it under its row the first time; thereafter it already sits
          // there (it belongs to this row and no other moves it).
          if (panel.previousElementSibling !== rowEl) {
            rowEl.after(panel);
          }
          const open = panel.hidden;
          panel.hidden = !open;
          rowEl.setAttribute("aria-expanded", open ? "true" : "false");
          rowEl.classList.toggle("open", open);
        });
      });
      if (box.querySelector("[data-screen-open]")) wireScreen(box, card);
      if (box.querySelector("[data-wf]")) wireDraft(box, card);
      if (box.querySelector("[data-plan]")) wirePlan(box, card);
      if (box.querySelector("[data-cx-act]")) wireCustom(box, card);
      // The on-chart control belongs to whichever payload owns the TRADES. A
      // standalone backtest card is its own payload; a draft repainted with a
      // stored result has that read-out nested in its slot, and the payload
      // there is `card.backtest`. Scoped, because a box-level query reaches
      // INTO the slot — which wired the restored control twice, once with the
      // draft as its payload, and the two listeners cancelled each other out.
      const slot = card.backtest ? box.querySelector("[data-wf-slot]") : null;
      if (slot) wireOnChart(slot, card.backtest);
      else if (box.querySelector("[data-bt-chart]")) wireOnChart(box, card);
      return box;
    },
  };
})();
