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
 * The ONE exception is `wrap: true`, for a row whose label is the literal
 * text of a question about to be sent. There the sentence IS the choice —
 * you are picking a prompt, numbers and all, not a command — so it wraps
 * rather than truncating, and its sheet is allowed to be wider.
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
      + (spec.wrap ? " wrap" : "")
      + (spec.danger ? " danger" : "")
      + (spec.disabled ? " off" : "")
      + (spec.tick ? " on" : "");
    r.setAttribute("role", "menuitem");
    r.tabIndex = -1;
    if (spec.title) r.title = spec.title;
    // A hint that is a VALUE reads at full strength; one that is a keyboard
    // reminder stays quiet. The two share a slot, and a price dimmed to the
    // weight of "⌥ R" is data the eye slides off. Which is which is legible
    // from the string itself — a currency mark or a digit — so no call site
    // has to restate what it already wrote.
    const numeric = spec.hint && /^[₹$€£¥]|^[+-]?\d/.test(String(spec.hint));
    // The trailing slot holds exactly one thing: a chevron if the row opens
    // another sheet, a tick if it reports a state, otherwise the shortcut.
    // Two of them in one slot is how a menu starts looking accidental.
    const trail = sub
      ? `<span class="ctx-more">${Icons.svg("chevronRight", "xs")}</span>`
      : spec.tick ? `<span class="ctx-tick">${Icons.svg("check", "xs")}</span>`
      : spec.hint ? `<span class="ctx-hint${numeric ? " num" : ""}">`
                    + `${esc(spec.hint)}</span>` : "";
    // A `swatch` row wears a colour dot where the glyph goes — the colour
    // picker's rows ARE their colour, so a named-hex label beside a dot would
    // be saying the same thing twice. `null` is the theme accent, drawn as the
    // primary so "Accent" reads as a colour and not a blank.
    const lead = ("swatch" in spec)
      ? `<span class="ctx-swatch" style="--sw:${esc(spec.swatch || "var(--primary)")}"></span>`
      : spec.icon ? Icons.svg(spec.icon, "sm") : `<i class="ctx-nopic"></i>`;
    r.innerHTML =
      `<span class="ctx-lead">`
      + lead
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
    const anyIcon = items.some((it) => it && (it.icon || ("swatch" in it)) && !it.sep && !it.head);
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
    // Guarded, because the observer below can be handed the same long-lived
    // menu on every open — a second lens on one element leaks the first.
    if (!window.liquidGlass || m.__lg) return;
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

  /* ── the same lens, for every other menu in the app ──────────────────────
   * The header's lists and the rail's tool flyouts are `.dropdown`s — built by
   * js/main.js, and by three dialog modules that have never heard of this file.
   * They are the same object a right-click sheet is: a list of rows floated
   * over candles. They wear the same paper in the stylesheet, and this gives
   * them the same refraction.
   *
   * An OBSERVER rather than a call at each site. A dropdown is opened from a
   * dozen places (a header button, a rail hover, a keyboard shortcut, a dialog
   * re-render) and a glaze() bolted onto each of them is a dozen chances for
   * the next menu to be added without one. `.open` is the one thing they all
   * do, so that is what is watched.
   *
   * Glazed on first open and KEPT. These are long-lived nodes that spend most
   * of their life display:none, where a filter costs nothing, and the module's
   * own ResizeObserver re-generates the map when the list's size changes — so
   * there is nothing to tear down and no work on the second open.
   *
   * The indicator settings dialog (`.dlg.indicator-settings`) is glazed too — it
   * is a glass panel over candles like everything else. `.open` lands on its
   * `.dlg-wrap`, not the `.dlg` itself, so the lens is attached to the child
   * when the wrapper opens. `.select-menu` — a dropdown that opens INSIDE such a
   * dialog — is still skipped: it carries its own opaque paper. */
  /* ── keep an anchored menu inside the viewport ───────────────────────────
   * The header pills, the chart-type swapper, the indicator list and every
   * other `.dropdown` that lives next to its button are placed by CSS alone:
   * `position:absolute; top:calc(100% + 6px); left:0`, with a `max-height`
   * measured off `100dvh`. That max-height is wrong the moment the button is
   * not at the top of the viewport — it reserves a full screen of room above
   * the menu that isn't there, so a long list (Indicators is 20+ rows) runs
   * off the BOTTOM edge and is clipped before it ever starts scrolling. The
   * same list opened near the right edge of the window overflows SIDEWAYS.
   *
   * So on open we measure the real gap. The menu is anchored to its wrapper,
   * which sits on the trigger button, so the button's rect tells us how much
   * room is below it and above it; we cap `max-height` to whichever side we
   * use, flip UP when below is cramped and above is roomier, and swing the
   * menu to open leftward (`.right`) when it would spill past the right edge.
   * Menus that position themselves — `.floating` (fixed, appended to body),
   * `.side` (the drawing rail's flyouts, offset horizontally), and any menu
   * carrying an inline top/bottom already — are left exactly as they are. */
  function placeAnchored(m) {
    if (!m.classList.contains("dropdown")) return;
    if (m.classList.contains("floating") || m.classList.contains("side")
        || m.classList.contains("select-menu")) return;
    // The class toggles below re-enter this observer; a flag keeps placement a
    // single settling pass per open rather than a ping-pong of mutations.
    if (m.__placing) return;
    m.__placing = true;
    try { settle(); } finally { m.__placing = false; }
    function settle() {
    // A self-positioning site set these inline; don't fight it.
    if (m.style.top || m.style.bottom) return;
    const wrap = m.parentElement;
    if (!wrap) return;
    // The trigger is the wrapper (which is pinned on the button). Measuring the
    // wrapper rather than the button keeps this correct when the wrap holds a
    // pill, an icon and a label — the menu is anchored to the wrap's box.
    // Remember the alignment the markup authored (`.right` on the right-hand
    // toolbar menus, `.up` on the mobile chat sheet). We only ever ADD a flip
    // to keep the menu on screen — never strip one the author chose — and we
    // restore this baseline on close.
    if (m.dataset.alignSeen == null) {
      m.dataset.alignSeen = "1";
      m.dataset.alignRight = m.classList.contains("right") ? "1" : "";
      m.dataset.alignUp = m.classList.contains("up") ? "1" : "";
    }
    const r = wrap.getBoundingClientRect();
    if (!r.height && !r.width) return;            // wrapper not laid out yet
    const GAP = 6;
    const below = innerHeight - r.bottom - GAP - EDGE;
    const above = r.top - GAP - EDGE;
    // Flip up only when below genuinely can't hold the menu AND above is the
    // roomier side — a short menu near the top stays down, as it reads best.
    // An author-chosen `.up` always wins.
    const needed = m.scrollHeight;
    const up = m.dataset.alignUp || (below < needed && above > below);
    m.classList.toggle("up", !!up);
    const room = Math.max(120, Math.floor(up ? above : below));
    m.style.maxHeight = room + "px";
    // Horizontal: a left-aligned menu that would spill past the right edge
    // swings to align on its anchor's right edge. A menu the markup already
    // right-aligned stays right-aligned.
    const w = m.offsetWidth;
    const wouldClipRight = !m.dataset.alignRight
      && r.left + w > innerWidth - EDGE && r.right - w > EDGE;
    m.classList.toggle("right", !!(m.dataset.alignRight || wouldClipRight));
    }
  }
  /* Opened menus set an inline max-height / flip class above; strip them on
   * close so the next open measures from the CSS baseline, not a stale cap. */
  function unplaceAnchored(m) {
    if (!m.classList || !m.classList.contains("dropdown")) return;
    if (m.classList.contains("floating") || m.classList.contains("side")) return;
    if (!m.style.top && !m.style.bottom) {
      m.style.maxHeight = "";
      m.classList.toggle("up", m.dataset.alignUp === "1");
      m.classList.toggle("right", m.dataset.alignRight === "1");
    }
  }

  function glazeMenus(root) {
    // .sh-fillmenu is the sheet's colour swatches: a palette has to be read on
    // flat paper, so it opts out in the stylesheet and here alike.
    const wanted = (n) => n && n.classList && n.classList.contains("dropdown")
      && !n.classList.contains("select-menu") && !n.classList.contains("sh-fillmenu");
    const glazeDlg = (wrap) => {
      const dlg = wrap.querySelector && wrap.querySelector(".dlg.indicator-settings");
      if (dlg) glaze(dlg);
    };
    for (const n of root.querySelectorAll(".dropdown.open")) {
      if (wanted(n)) { glaze(n); placeAnchored(n); }
    }
    for (const w of root.querySelectorAll(".dlg-wrap.open")) glazeDlg(w);
    // Most menus outside this file are built FRESH on every open — created
    // with `open` already in their class and appended (the widget menus in
    // js/dock.js, the watchlist's, the symbol picker, the chat's subject
    // list…). No class ever changes on those, so watching attributes alone
    // left every one of them merely blurred while the long-lived menus
    // refracted: two materials for one object. So arrivals are watched too,
    // and a fresh menu's lens is released when the menu leaves — each lens
    // adds a filter to one shared <defs>, and a menu rebuilt on every open
    // would otherwise leave one behind per open. The callback runs after the
    // opener's synchronous placement, so the map is cut to the final box.
    new MutationObserver((recs) => {
      for (const r of recs) {
        if (r.type === "childList") {
          for (const n of r.removedNodes) if (n.__lg && !n.isConnected) unglaze(n);
          for (const n of r.addedNodes) {
            if (n.nodeType === 1 && n.classList.contains("open") && wanted(n)) glaze(n);
          }
          continue;
        }
        const n = r.target;
        if (!n.classList) continue;
        const open = n.classList.contains("open");
        if (n.classList.contains("dropdown") && !n.classList.contains("select-menu")) {
          if (open) { glaze(n); placeAnchored(n); }
          else unplaceAnchored(n);
          continue;
        }
        if (open && n.classList.contains("dlg-wrap")) glazeDlg(n);
      }
    }).observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => glazeMenus(document.body));
  } else {
    glazeMenus(document.body);
  }

  /* ── which way a menu unfolds ────────────────────────────────────────────
   * The stylesheet reveals a menu top to bottom (drop-down) unless it hangs
   * ABOVE what opened it, where it unrolls upward instead. No opener has to
   * say which: the press that opened it is remembered, and a sheet whose foot
   * is above that point was placed above it. Stamped as data-drop before the
   * first paint (observer callbacks run before rendering), so the animation
   * never starts the wrong way. Submenus keep their sideways entrance. */
  let pressY = null;
  addEventListener("pointerdown", (e) => { pressY = e.clientY; }, true);
  addEventListener("keydown", () => { pressY = null; }, true);
  function stampDrop(n) {
    if (!n || !n.classList || n.classList.contains("ctx-sub")) return;
    const isMenu = (n.classList.contains("dropdown") && n.classList.contains("open"))
      || (n.classList.contains("ctx") && n.classList.contains("in"));
    if (!isMenu) return;
    const r = n.getBoundingClientRect();
    const up = n.classList.contains("up") || (pressY != null && r.height > 0 && r.bottom <= pressY + 2);
    n.dataset.drop = up ? "up" : "down";
  }
  new MutationObserver((recs) => {
    for (const r of recs) {
      if (r.type === "attributes") stampDrop(r.target);
      else for (const n of r.addedNodes) if (n.nodeType === 1) stampDrop(n);
    }
  }).observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });

  return {
    open, close, isOpen: () => chain.length > 0,
    /** THE app's lens, for anything outside this file that needs to be made of
     *  the same glass — the chart's reset button, so far. Exported rather than
     *  copied: the settings below are a look, and a second copy of them is a
     *  second look the moment either is tuned. Idempotent (see glaze). */
    glass: glaze,
  };
})();
