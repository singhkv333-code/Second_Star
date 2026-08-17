/* Charto preview — the chart's context menu.
 *
 * The SHELL only: build a menu from a list of rows, put it where the pointer
 * is, run nested ones, and take it down again. What the rows SAY is decided
 * where the chart state lives (js/main.js), because a menu that routes by
 * what is under the pointer — a drawing, a candle, empty chart — has to be
 * built by whoever can answer that question.
 *
 * Why a module rather than the forty lines of inline DOM this replaces:
 *   · three menus now, and each is the same sheet with different rows
 *   · submenus need hover timing, edge flipping and a pointer corridor,
 *     which is real behaviour and would otherwise be written three times
 *   · a keyboard has to be able to walk it
 *
 * The rows are OBJECTS, not markup, so a caller hands over a function rather
 * than a data-attribute and an index into an array it also has to keep:
 *
 *   { icon, label, hint, on() }        a row that does something
 *   { icon, label, sub: [...] }        a row that opens another menu
 *   { icon, label, sub: () => [...] }  built when it opens, not before
 *   { label, on(), tick: true }        a row that is currently ON
 *   { sep: true }                      a seam
 *   { head, note }                     the sheet's own header
 *
 * A LABEL IS A NAME, never a sentence. Two or three words, no trailing
 * punctuation, no ellipsis — a row that reads as prose has to be parsed
 * before it can be chosen, and a menu is scanned rather than read. Where a
 * row needs to carry a number, that goes in `hint` (the right-hand slot);
 * where it needs a longer explanation, that goes in `title`, which the
 * pointer asks for rather than the eye having to step over.
 *
 * A falsy entry is skipped, so a caller can write `cond && {…}` inline and
 * never assemble the array conditionally. A row with no `on` and no `sub`
 * is inert by construction — there is no such thing here as a row that
 * looks live and does nothing.
 */
"use strict";

const Ctx = (() => {
  /* Hover timings. The OPEN delay stops a submenu firing off every row the
   * pointer crosses on its way down the sheet; the SHUT delay is the corridor
   * — you leave the parent row diagonally to reach the submenu, which means
   * passing over rows that are not it, and closing on that would make a
   * submenu unreachable by any natural movement. */
  const OPEN_MS = 90, SHUT_MS = 260;
  const EDGE = 8;                 // the closest a sheet comes to the viewport

  let chain = [];                 // [{ el, owner }] outermost first
  let shutTimer = null;

  const esc = (v) => String(v == null ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  /* ── one row ─────────────────────────────────────────────────────────── */
  function buildRow(spec, depth) {
    const r = document.createElement("div");
    const sub = !!spec.sub;
    r.className = "ctx-row"
      + (spec.danger ? " danger" : "")
      + (spec.disabled ? " off" : "")
      + (spec.tick ? " on" : "");
    r.setAttribute("role", "menuitem");
    r.tabIndex = -1;
    if (spec.title) r.title = spec.title;
    // The trailing slot holds exactly one thing: a chevron if the row opens
    // another sheet, a tick if it reports a state, otherwise the shortcut.
    // Two of them in one slot is how a menu starts looking accidental.
    const trail = sub
      ? `<span class="ctx-more">${Icons.svg("chevronRight", "xs")}</span>`
      : spec.tick ? `<span class="ctx-tick">${Icons.svg("check", "xs")}</span>`
      : spec.hint ? `<span class="ctx-hint">${esc(spec.hint)}</span>` : "";
    r.innerHTML =
      `<span class="ctx-lead">`
      + (spec.icon ? Icons.svg(spec.icon, "sm") : `<i class="ctx-nopic"></i>`)
      + `<span class="ctx-label">${esc(spec.label)}</span></span>`
      + trail;

    if (spec.disabled) return r;

    if (sub) {
      r.setAttribute("aria-haspopup", "true");
      let openTimer = null;
      const arm = () => {
        clearTimeout(shutTimer); shutTimer = null;
        if (r.classList.contains("open")) return;
        openTimer = setTimeout(() => openSub(r, spec, depth), OPEN_MS);
      };
      r.addEventListener("mouseenter", arm);
      r.addEventListener("mouseleave", () => clearTimeout(openTimer));
      // A click on the parent opens it NOW — waiting out a hover delay after
      // a deliberate press is the menu ignoring an instruction it was given.
      r.addEventListener("click", (e) => {
        e.stopPropagation();
        clearTimeout(openTimer);
        openSub(r, spec, depth);
      });
    } else {
      // Anything that is NOT this row's own submenu is stale the moment the
      // pointer lands here, so a leaf closes deeper sheets on its way in.
      r.addEventListener("mouseenter", () => scheduleTrim(depth));
      r.addEventListener("click", (e) => {
        e.stopPropagation();
        close();
        // after close(): a handler that opens a dialog must not have this
        // sheet still on screen behind it
        if (spec.on) spec.on();
      });
    }
    return r;
  }

  /* ── one sheet ───────────────────────────────────────────────────────── */
  function buildMenu(items, depth) {
    const m = document.createElement("div");
    // A sheet where NO row carries a glyph gets no glyph gutter. The spacer
    // exists so labels line up when only some rows have icons; on a list of
    // plain names — the watchlists, the four prices, the questions — it is
    // 31px of empty paper before every word, which is what made a one-row
    // submenu read as a mostly-blank card.
    const anyIcon = items.some((it) => it && it.icon && !it.sep && !it.head);
    m.className = "ctx" + (depth ? " ctx-sub" : "") + (anyIcon ? "" : " ctx-plain");
    m.setAttribute("role", "menu");
    let lastWasSep = true;        // no leading rule, and never two in a row
    for (const it of items) {
      if (!it) continue;
      if (it.sep) {
        if (lastWasSep) continue;
        const s = document.createElement("div");
        s.className = "ctx-sep";
        m.appendChild(s);
        lastWasSep = true;
        continue;
      }
      if (it.head) {
        const h = document.createElement("div");
        h.className = "ctx-head";
        h.innerHTML = `<span class="ctx-head-t">${esc(it.head)}</span>`
          + (it.note ? `<span class="ctx-head-n">${esc(it.note)}</span>` : "")
          // a second, quieter line for the detail behind the headline number
          + (it.sub2 ? `<span class="ctx-head-s">${esc(it.sub2)}</span>` : "");
        m.appendChild(h);
        // The header draws its own rule, so it COUNTS as a seam: a menu whose
        // first group is conditional would otherwise open with two hairlines
        // 5px apart when that group came out empty.
        lastWasSep = true;
        continue;
      }
      lastWasSep = false;
      m.appendChild(buildRow(it, depth));
    }
    // a trailing rule is the same lie as a leading one
    const tail = m.lastElementChild;
    if (tail && tail.classList.contains("ctx-sep")) tail.remove();
    return m;
  }

  /** Put `m` on screen at (x, y), flipped rather than clipped. `flipX` is
   *  the x to use when the sheet will not fit to the right — for a submenu
   *  that is its parent's LEFT edge, so the flipped sheet lands beside the
   *  parent rather than on top of it. */
  function place(m, x, y, flipX) {
    m.style.visibility = "hidden";
    document.body.appendChild(m);
    const w = m.offsetWidth, h = m.offsetHeight;
    let left = x, top = y;
    if (left + w > innerWidth - EDGE) {
      left = (flipX == null ? x : flipX) - w;
      if (flipX != null && left < EDGE) left = x;   // no room either side: right wins
    }
    if (top + h > innerHeight - EDGE) top = innerHeight - EDGE - h;
    m.style.left = Math.max(EDGE, Math.round(left)) + "px";
    m.style.top = Math.max(EDGE, Math.round(top)) + "px";
    m.style.visibility = "";
    m.classList.add("in");
    glaze(m);
  }

  /* ── the material ────────────────────────────────────────────────────────
   * vendor/liquid-glass.js — the same SVG feDisplacementMap through
   * backdrop-filter the ask group already uses, so there is one glass in
   * this app rather than a menu that merely blurs beside a panel that
   * refracts. It attaches AFTER placement, because the displacement map is
   * generated at the element's measured size and corner radius.
   *
   * Gentler than the ask group's: a menu is a surface you read words off,
   * and a lens strong enough to be obvious at the rim smears a 13.5px label
   * two rows in. The module falls back to frosted blur on Safari and Firefox
   * by itself; the CSS `backdrop-filter` is the floor under both, and the
   * module's inline style outranks it wherever it lands.
   */
  function glaze(m) {
    if (!window.liquidGlass) return;
    try {
      m.__lg = liquidGlass(m, { scale: -42, chroma: 3, border: .09, mapBlur: 10,
                                blur: 7, saturate: 1.45, fallbackBlur: 22 });
    } catch { /* a sheet with no refraction is still a readable sheet */ }
  }
  function unglaze(m) {
    if (m.__lg) { try { m.__lg.destroy(); } catch {} m.__lg = null; }
  }

  /** Close every sheet deeper than `depth`. */
  function trim(depth) {
    while (chain.length > depth + 1) {
      const top = chain.pop();
      if (top.owner) top.owner.classList.remove("open");
      // The filter and its ResizeObserver are per-sheet and outlive the DOM
      // node unless they are told not to — a session of right-clicks would
      // otherwise leave a live SVG filter behind for every menu ever opened.
      unglaze(top.el);
      top.el.remove();
    }
  }
  function scheduleTrim(depth) {
    clearTimeout(shutTimer);
    shutTimer = setTimeout(() => trim(depth), SHUT_MS);
  }

  function openSub(rowEl, spec, depth) {
    clearTimeout(shutTimer); shutTimer = null;
    trim(depth);                                  // siblings first
    const items = typeof spec.sub === "function" ? spec.sub() : spec.sub;
    if (!items || !items.length) return;
    rowEl.classList.add("open");
    const m = buildMenu(items, depth + 1);
    // Kept open by the pointer being ANYWHERE in it, not by a row: the gap
    // between two rows is still inside the submenu.
    m.addEventListener("mouseenter", () => { clearTimeout(shutTimer); shutTimer = null; });
    m.addEventListener("mouseleave", () => scheduleTrim(depth));
    chain.push({ el: m, owner: rowEl });
    const r = rowEl.getBoundingClientRect();
    // −6 lines the submenu's FIRST ROW up with the row that opened it: the
    // sheet carries its own padding, and without that the two are off by it.
    place(m, r.right + 2, r.top - 6, r.left - 2);
  }

  /* ── the keyboard ────────────────────────────────────────────────────── */
  const rowsOf = (m) => [...m.querySelectorAll(":scope > .ctx-row:not(.off)")];

  function move(dir) {
    const m = chain[chain.length - 1].el;
    const rows = rowsOf(m);
    if (!rows.length) return;
    const i = rows.indexOf(document.activeElement);
    // Nothing focused yet: Down takes the first row, Up the last. After that
    // it wraps, which is what every menu on both platforms does.
    const next = i < 0 ? (dir > 0 ? 0 : rows.length - 1)
                       : (i + dir + rows.length) % rows.length;
    rows[next].focus();
  }

  function onKey(e) {
    if (!chain.length) return;
    const m = chain[chain.length - 1].el;
    const cur = document.activeElement;
    const inMenu = cur && m.contains(cur);
    if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation();
      if (chain.length > 1) {
        const owner = chain[chain.length - 1].owner;
        trim(chain.length - 2);
        if (owner) owner.focus();
      } else close();
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); move(e.key === "ArrowDown" ? 1 : -1); return;
    }
    if (e.key === "ArrowRight" && inMenu && cur.getAttribute("aria-haspopup")) {
      e.preventDefault();
      cur.click();
      const opened = chain[chain.length - 1];
      const first = rowsOf(opened.el)[0];
      if (first) first.focus();
      return;
    }
    if (e.key === "ArrowLeft" && chain.length > 1) {
      e.preventDefault();
      const owner = chain[chain.length - 1].owner;
      trim(chain.length - 2);
      if (owner) owner.focus();
      return;
    }
    if ((e.key === "Enter" || e.key === " ") && inMenu) {
      e.preventDefault(); cur.click();
    }
  }

  function onDown(e) {
    if (chain.some((c) => c.el.contains(e.target))) return;
    close();
  }

  /* ── the two entry points ────────────────────────────────────────────── */

  /** Open a menu at (x, y). Any menu already up is replaced, so two
   *  right-clicks in a row leave one sheet rather than two. */
  function open(x, y, items) {
    close();
    const m = buildMenu(items, 0);
    m.addEventListener("mouseenter", () => { clearTimeout(shutTimer); shutTimer = null; });
    chain.push({ el: m, owner: null });
    place(m, x, y, null);
    // Capture phase and `mousedown`, not click: the press that dismisses a
    // menu must not also land on the chart underneath and start a pan.
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    addEventListener("resize", close);
    addEventListener("blur", close);
    // A scroll or a zoom moves what the sheet is pointing at out from under
    // it, and a menu anchored to a price that is no longer there is worse
    // than no menu. The chart's own wheel handler is not ours to intercept,
    // so this listens rather than blocks.
    addEventListener("wheel", close, { passive: true, capture: true });
    return m;
  }

  function close() {
    if (!chain.length) return;
    clearTimeout(shutTimer); shutTimer = null;
    for (const c of chain) { unglaze(c.el); c.el.remove(); }
    chain = [];
    document.removeEventListener("mousedown", onDown, true);
    document.removeEventListener("keydown", onKey, true);
    removeEventListener("resize", close);
    removeEventListener("blur", close);
    removeEventListener("wheel", close, { capture: true });
  }

  return { open, close, isOpen: () => chain.length > 0 };
})();
