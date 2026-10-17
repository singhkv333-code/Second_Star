/* Charto preview — the dialog kit.
 *
 * The parts every settings dialog in this app is built from, in one place so
 * there is exactly ONE colour picker, ONE dressed <select>, and ONE answer to
 * "where does this card sit on screen". It was extracted from the indicator
 * dialog the day a second one (chart settings) needed the same pieces —
 * copying a 200-line HSV picker would have meant two pickers drifting apart,
 * which is the same disease as two brains.
 *
 * Colour math lives here rather than being borrowed from js/indicators.js: a
 * picker is the only thing in the app that thinks in HSV, and a dialog kit
 * must not reach into a data module. indicators.js keeps its own paint-time
 * `withAlpha` for the same reason in reverse — series painting cannot depend
 * on the dialog layer.
 */
"use strict";

const DlgKit = (() => {
  // TradingView's swatch grid, generated rather than typed: a row of
  // neutrals over ten hues, each in five steps from tint to shade. Ten
  // columns wide, so a column IS a hue and picking "a darker green" is a
  // vertical move — which is the whole point of the layout.
  const HUES = ["#f23645", "#ff9800", "#ffeb3b", "#4caf50", "#089981",
                "#00bcd4", "#2962ff", "#673ab7", "#9c27b0", "#e91e63"];
  const NEUTRALS = ["#ffffff", "#d1d4dc", "#b2b5be", "#9598a1", "#787b86",
                    "#5d606b", "#434651", "#2a2e39", "#1e222d", "#000000"];
  const STEPS = [0.62, 0.34, 0, -0.28, -0.52];   // + toward white, − toward black

  const hex2 = (n) => Math.max(0, Math.min(255, Math.round(n)))
    .toString(16).padStart(2, "0");

  function step(hex, t) {
    const n = parseInt(hex.slice(1), 16);
    const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    const to = t >= 0 ? 255 : 0;
    const k = Math.abs(t);
    return "#" + c.map((x) => hex2(x + (to - x) * k)).join("");
  }

  const PALETTE_ROWS = [NEUTRALS, ...STEPS.map((t) => HUES.map((h) => step(h, t)))];

  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ── colour helpers ──────────────────────────────────────
  function hexOf(c) {
    const m = /^#([0-9a-f]{6})$/i.exec(String(c).trim());
    if (m) return `#${m[1]}`;
    const r = /^rgba?\(([^)]+)\)$/i.exec(String(c).trim());
    if (!r) return "#ffffff";
    const p = r[1].split(",").map((x) => parseInt(x.trim(), 10));
    return `#${p.slice(0, 3).map((n) => n.toString(16).padStart(2, "0")).join("")}`;
  }

  function alphaOf(c) {
    const r = /^rgba\(([^)]+)\)$/i.exec(String(c).trim());
    if (!r) return 1;
    const p = r[1].split(",");
    return p.length > 3 ? Math.max(0, Math.min(1, parseFloat(p[3]))) : 1;
  }

  function withAlpha(color, a) {
    if (a >= 1) return hexOf(color);
    const n = parseInt(hexOf(color).slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  /** #rrggbb → {h 0-360, s 0-1, v 0-1} and back. A square of saturation
   *  against value is the only way to pick a shade by eye. */
  function rgbToHsv(hexStr) {
    const n = parseInt(hexOf(hexStr).slice(1), 16);
    const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    let h = 0;
    if (d) {
      if (max === r) h = ((g - b) / d) % 6;
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
      if (h < 0) h += 360;
    }
    return { h, s: max ? d / max : 0, v: max };
  }
  function hsvToHex(h, s, v) {
    const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c;
    const seg = [[c, x, 0], [x, c, 0], [0, c, x], [0, x, c], [x, 0, c], [c, 0, x]];
    const [r, g, b] = seg[Math.min(5, Math.floor(h / 60))] || seg[0];
    const to = (u) => Math.round((u + m) * 255).toString(16).padStart(2, "0");
    return `#${to(r)}${to(g)}${to(b)}`;
  }

  // ── placement ───────────────────────────────────────────
  /** Anchor a popover to a control, kept inside the viewport. It opens below
   *  and left-aligned, flipping above only when there is no room under. */
  function place(pop, anchor) {
    const r = anchor.getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight;
    const left = Math.max(8, Math.min(r.left, innerWidth - w - 8));
    const below = r.bottom + 6;
    const top = below + h <= innerHeight - 8 ? below
      : Math.max(8, r.top - h - 6);
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
  }

  // ── select → the app's own menu ─────────────────────────
  // A native <select> renders its list with the OS, which is the one surface
  // in this app no stylesheet can reach: a white box with a blue-black
  // highlight and system type, inside a dialog built to Pivot's spec. The
  // <select> stays in the DOM as the value holder — every existing input /
  // change handler keeps working untouched — and a button drawn like the
  // other fields shows the value, with a .dropdown menu for the options.
  let selectMenu = null;

  function dressSelects(root) {
    root.querySelectorAll("select.dlg-select").forEach((sel) => {
      if (sel.dataset.dressed) return;
      sel.dataset.dressed = "1";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = sel.className.replace("dlg-select", "select-btn");
      btn.innerHTML = `<span class="val"></span>${Icons.svg("chevronDown", "xs")}`;
      const paint = () => {
        const o = sel.options[sel.selectedIndex];
        const v = btn.querySelector(".val");
        // the closed control shows the same glyph the list did, so picking one
        // does not lose the thing that made it recognisable
        v.innerHTML = (o && o.dataset.icon ? Icons.svg(o.dataset.icon, "op") : "")
          + `<span>${esc(o ? o.textContent : "")}</span>`;
      };
      paint();
      sel.after(btn);
      sel.classList.add("is-dressed");
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        openSelectMenu(sel, btn, paint);
      });
      sel.addEventListener("change", paint);
    });
  }

  function openSelectMenu(sel, btn, paint) {
    closeSelectMenu();
    selectMenu = document.createElement("div");
    selectMenu.className = "dropdown select-menu open";
    // `data-icon` on an <option> draws that glyph before the label. Optional
    // everywhere, so every existing select is unchanged — but where the choices
    // are geometric (what an alert operator DOES) a picture says it faster than
    // the words can.
    selectMenu.innerHTML = [...sel.options].map((o, i) =>
      `<div class="item${i === sel.selectedIndex ? " on" : ""}" data-i="${i}">` +
      `<span class="lead">` +
        (o.dataset.icon ? Icons.svg(o.dataset.icon, "op") : "") +
        `<span>${esc(o.textContent)}</span></span>` +
      `${i === sel.selectedIndex ? Icons.svg("check", "xs") : ""}</div>`).join("");
    document.body.appendChild(selectMenu);
    selectMenu.style.minWidth = `${Math.max(btn.offsetWidth, 132)}px`;
    place(selectMenu, btn);
    selectMenu.addEventListener("pointerdown", (e) => e.stopPropagation());
    selectMenu.addEventListener("click", (e) => {
      e.stopPropagation();
      const it = e.target.closest("[data-i]");
      if (!it) return;
      sel.selectedIndex = Number(it.dataset.i);
      paint();
      // the same two events the native control fired, so nothing downstream
      // needs to know the list was ours
      sel.dispatchEvent(new Event("input", { bubbles: true }));
      sel.dispatchEvent(new Event("change", { bubbles: true }));
      closeSelectMenu();
    });
  }

  function closeSelectMenu() {
    if (selectMenu) { selectMenu.remove(); selectMenu = null; }
  }

  // ── colour popover ──────────────────────────────────────
  /* The picker, opened against a .dlg-swatch button.
   *
   *   DlgKit.openSwatch(btn, { value, onPick })
   *
   * `value` is the colour the control currently holds (hex or rgba); `onPick`
   * receives every intermediate colour, alpha already folded in, because
   * these dialogs apply live and the picker is the thing being dragged. */
  let swatchPop = null;

  function openSwatch(btn, opts) {
    closeSwatch();
    closeSelectMenu();
    const cur = opts.value || "#ffffff";
    const alpha = alphaOf(cur);
    let hex = hexOf(cur);
    const onPick = opts.onPick || (() => {});

    swatchPop = document.createElement("div");
    swatchPop.className = "dlg-swatch-pop";
    swatchPop.innerHTML =
      `<div class="grid">${PALETTE_ROWS.map((row) => row.map((c) =>
        `<button type="button" class="sw" data-c="${c}" style="--sw:${c}"
           title="${c}"></button>`).join("")).join("")}</div>
       <div class="pop-row">
         <button type="button" class="custom-tile" title="Custom colour">${Icons.svg("plus")}</button>
         <span class="pop-label">Custom</span>
       </div>
       <div class="pk" hidden>
         <div class="pk-area"><span class="pk-dot"></span></div>
         <input class="pk-hue" type="range" min="0" max="360" step="1" value="0"
                aria-label="Hue">
         <div class="pop-row pk-foot">
           <span class="pk-preview"></span>
           <input class="input pk-hex" type="text" spellcheck="false" maxlength="7"
                  aria-label="Hex colour">
         </div>
       </div>
       <div class="pop-sep"></div>
       <div class="pop-row opacity">
         <span class="pop-label">Opacity</span>
         <input type="range" min="0" max="100" step="1" value="${Math.round(alpha * 100)}">
         <output>${Math.round(alpha * 100)}%</output>
       </div>`;
    document.body.appendChild(swatchPop);
    // the popover lives on <body>, so its own presses must not reach the
    // document handler that dismisses it — dragging the opacity slider was
    // closing the thing being dragged
    swatchPop.addEventListener("pointerdown", (e) => e.stopPropagation());
    place(swatchPop, btn);

    const range = swatchPop.querySelector(".opacity input");
    const out = swatchPop.querySelector("output");
    const pk = swatchPop.querySelector(".pk");
    const area = swatchPop.querySelector(".pk-area");
    const dot = swatchPop.querySelector(".pk-dot");
    const hue = swatchPop.querySelector(".pk-hue");
    const hexIn = swatchPop.querySelector(".pk-hex");
    const preview = swatchPop.querySelector(".pk-preview");

    function markSelected() {
      const want = hex.toLowerCase();
      swatchPop.querySelectorAll(".sw").forEach((s) =>
        s.classList.toggle("sel", s.dataset.c.toLowerCase() === want));
    }
    function pick(next, fromPicker) {
      hex = next;
      const a = Number(range.value) / 100;
      const v = withAlpha(next, a);
      onPick(v);
      btn.style.setProperty("--sw", v);
      markSelected();
      if (!fromPicker && !pk.hidden) paintPicker();
      if (fromPicker) { preview.style.background = next; hexIn.value = next; }
    }

    // ── the custom picker: saturation/value field + hue, in the page's own
    // idiom. The native <input type="color"> opened Chrome's OS panel —
    // an eyedropper, R/G/B spinners and a system typeface dropped into the
    // middle of a Pivot dialog.
    function paintPicker() {
      const { h, s, v } = rgbToHsv(hex);
      hue.value = Math.round(h);
      area.style.setProperty("--hue", h);
      dot.style.left = `${s * 100}%`;
      dot.style.top = `${(1 - v) * 100}%`;
      dot.style.background = hex;
      preview.style.background = hex;
      hexIn.value = hex;
    }
    function fromArea(e) {
      const r = area.getBoundingClientRect();
      const s = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      const v = 1 - Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
      const next = hsvToHex(Number(hue.value), s, v);
      dot.style.left = `${s * 100}%`;
      dot.style.top = `${(1 - v) * 100}%`;
      dot.style.background = next;
      pick(next, true);
    }
    area.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      area.setPointerCapture(e.pointerId);
      fromArea(e);
      const move = (ev) => fromArea(ev);
      const up = () => {
        area.removeEventListener("pointermove", move);
        area.removeEventListener("pointerup", up);
      };
      area.addEventListener("pointermove", move);
      area.addEventListener("pointerup", up);
    });
    hue.addEventListener("input", () => {
      const { s, v } = rgbToHsv(hex);
      area.style.setProperty("--hue", hue.value);
      const next = hsvToHex(Number(hue.value), s, v);
      dot.style.background = next;
      pick(next, true);
    });
    hexIn.addEventListener("change", () => {
      const m = /^#?([0-9a-f]{6})$/i.exec(hexIn.value.trim());
      if (!m) { hexIn.value = hex; return; }
      pick(`#${m[1].toLowerCase()}`);
      paintPicker();
    });

    markSelected();

    swatchPop.addEventListener("click", (e) => {
      e.stopPropagation();
      const c = e.target.closest("[data-c]");
      if (c) { pick(c.dataset.c); return; }
      if (e.target.closest(".custom-tile")) {
        pk.hidden = !pk.hidden;
        swatchPop.classList.toggle("picking", !pk.hidden);
        if (!pk.hidden) { paintPicker(); place(swatchPop, btn); }
      }
    });
    range.addEventListener("input", () => {
      out.textContent = `${range.value}%`;
      pick(hex);
    });
  }

  function closeSwatch() {
    if (swatchPop) { swatchPop.remove(); swatchPop = null; }
  }

  const closePopovers = () => { closeSwatch(); closeSelectMenu(); };
  // one document handler for both popovers, registered once by the kit
  document.addEventListener("pointerdown", closePopovers);

  // ── the card's own position ─────────────────────────────
  /** Take a dialog card out of the backdrop's centring and give it a position
   *  of its own. Everything that resizes it afterwards — a tab whose controls
   *  need a wider card, a drag — then moves the RIGHT and BOTTOM edges only.
   *  Centred, a card that grows re-centres, so switching tabs slid the whole
   *  dialog out from under the pointer that was about to click the next one.
   *
   *  The anchor is kept in this closure, not read back off the DOM. The
   *  backdrop animates in with a scale, and an ancestor transform both moves
   *  a fixed child's containing block and shows up in getBoundingClientRect —
   *  so measuring the card to reposition it made the left edge creep by a few
   *  px on every tab. A number we set is a number we can trust. */
  function draggable(dlg, handle) {
    let pinX = 0, pinY = 0;

    function pin(left, top) {
      pinX = Math.round(left);
      pinY = Math.round(top);
      dlg.style.position = "fixed";
      dlg.style.margin = "0";
      dlg.style.left = `${pinX}px`;
      dlg.style.top = `${pinY}px`;
    }
    const pinned = () => dlg.style.position === "fixed";

    /** Keep a pinned card on screen after its content changed size. The left
     *  and top edges move only as a last resort — when growing right or down
     *  would otherwise push the card off the viewport. offsetWidth/Height are
     *  layout metrics, so they ignore the wrapper's animation transform. */
    function reclamp() {
      const w = dlg.offsetWidth, h = dlg.offsetHeight;
      pin(Math.max(8, Math.min(pinX, innerWidth - w - 8)),
          Math.max(8, Math.min(pinY, innerHeight - h - 8)));
    }

    /** Hand the card back to the backdrop's centring, then pin it there once
     *  it has been laid out — the one call that must happen at open. */
    function centre() {
      dlg.style.position = "";
      dlg.style.left = dlg.style.top = dlg.style.margin = "";
    }
    function pinCentred() {
      pin(Math.max(8, (innerWidth - dlg.offsetWidth) / 2),
          Math.max(8, (innerHeight - dlg.offsetHeight) / 2));
    }

    // drag by the header, the way TradingView's dialog moves
    (handle || dlg).addEventListener("pointerdown", (e) => {
      if (e.target.closest("button")) return;
      const dx = e.clientX - pinX, dy = e.clientY - pinY;
      const w = dlg.offsetWidth;
      const move = (ev) => {
        pin(Math.max(8, Math.min(ev.clientX - dx, innerWidth - w - 8)),
            Math.max(8, Math.min(ev.clientY - dy, innerHeight - 60)));
      };
      const up = () => {
        removeEventListener("pointermove", move);
        removeEventListener("pointerup", up);
      };
      addEventListener("pointermove", move);
      addEventListener("pointerup", up);
    });

    // a pinned card holds absolute coordinates, so a shrinking window can
    // strand it off the edge — it never re-centres, it just comes back in.
    // A closed card measures zero, and clamping THAT would pin it against the
    // right edge for the next open.
    addEventListener("resize", () => {
      if (pinned() && dlg.offsetWidth) reclamp();
    });

    return { pin, pinned, reclamp, centre, pinCentred };
  }

  return {
    PALETTE_ROWS,
    hexOf, alphaOf, withAlpha, rgbToHsv, hsvToHex,
    place, dressSelects, closeSelectMenu,
    openSwatch, closeSwatch, closePopovers,
    draggable,
  };
})();
