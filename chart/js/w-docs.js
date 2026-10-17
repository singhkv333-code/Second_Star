/* Charto preview — the Documents widget: read beside the chart.
 *
 * Drop in an annual report, a broker note, a screenshot, a CSV: PDFs are
 * drawn here with PDF.js (vendor/docs — the browser's own viewer is blocked
 * inside a frame by some Chrome settings and absent on Android), Word files
 * are turned into readable HTML
 * (mammoth.js, loaded on first use), images and text show as they are, and
 * a spreadsheet file offers to open in a Sheet. Files stay in THIS browser
 * (IndexedDB) and nowhere else — nothing is uploaded — and the widget says so.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const { esc, ic, idb, empty, load } = WKit;
  const MAMMOTH = "./vendor/docs/mammoth.browser.min.js";
  const MAX = 40 * 1024 * 1024;
  const kindOf = (name, type) => {
    const n = name.toLowerCase();
    if (type === "application/pdf" || n.endsWith(".pdf")) return "pdf";
    if (n.endsWith(".docx")) return "docx";
    if (/^image\//.test(type)) return "image";
    if (/\.(xlsx|xls|csv|tsv)$/.test(n)) return "sheet";
    if (/^text\//.test(type) || /\.(txt|md|json|log)$/.test(n)) return "text";
    return "other";
  };
  const size = (b) => b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1e3)) + " KB";

  let pdfLib = null;
  const loadPdf = () => pdfLib || (pdfLib = import("../vendor/docs/pdf.min.js").then((m) => {
    m.GlobalWorkerOptions.workerSrc = new URL("vendor/docs/pdf.worker.min.js", document.baseURI).href;
    return m;
  }));

  /** A PDF drawn page by page into canvases: only the pages near the screen
   *  are rendered, at the device's pixel ratio, and redrawn when the tile's
   *  width changes so "fit width" stays true after a resize. */
  function pdfViewer(view, data, ctx, onText) {
    let doc = null, zoom = ctx.cfg.pdfZoom || "fit", width = 0, dead = false;
    const pages = [];
    view.innerHTML = `<div class="pv-bar">` +
        `<button type="button" class="sh-btn i" data-pv="prev" title="Previous page">${ic("chevronUp")}</button>` +
        `<span class="pv-at"><input type="text" inputmode="numeric" aria-label="Page"> / <b>…</b></span>` +
        `<button type="button" class="sh-btn i" data-pv="next" title="Next page">${ic("chevronDown")}</button>` +
        `<span class="sh-gap"></span>` +
        `<button type="button" class="sh-btn i" data-pv="out" title="Zoom out">−</button>` +
        `<button type="button" class="sh-btn pv-z" data-pv="fit" title="Fit the width"></button>` +
        `<button type="button" class="sh-btn i" data-pv="in" title="Zoom in">+</button>` +
      `</div><div class="pv-pages"></div>`;
    const box = view.querySelector(".pv-pages"), atIn = view.querySelector(".pv-at input");
    const scaleFor = (pg) => {
      const vp = pg.getViewport({ scale: 1 });
      const fit = Math.max(.2, (box.clientWidth - 24) / vp.width);
      return zoom === "fit" ? fit : zoom === "page" ? Math.min(fit, (box.clientHeight - 24) / vp.height) : Number(zoom);
    };
    let scaleNow = 1;            // the scale the pages are drawn at now, as a number
    const paintZoom = () => { view.querySelector(".pv-z").textContent = zoom === "fit" ? "Fit" : zoom === "page" ? "Page" : Math.round(zoom * 100) + "%"; };
    async function draw(i) {
      const slot = pages[i];
      if (!slot || slot.drawn === width + ":" + zoom) return;
      slot.drawn = width + ":" + zoom;
      const pg = await doc.getPage(i + 1);
      const sc = scaleFor(pg), vp = pg.getViewport({ scale: sc });
      const dpr = Math.min(2.5, devicePixelRatio || 1);
      const cv = document.createElement("canvas");
      cv.width = Math.floor(vp.width * dpr); cv.height = Math.floor(vp.height * dpr);
      cv.style.width = Math.floor(vp.width) + "px"; cv.style.height = Math.floor(vp.height) + "px";
      slot.el.style.width = cv.style.width; slot.el.style.height = cv.style.height;
      await pg.render({ canvasContext: cv.getContext("2d"), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
      if (dead) return;
      slot.el.replaceChildren(cv);
    }
    const io = new IntersectionObserver((ents) => {
      for (const e of ents) if (e.isIntersecting) draw(+e.target.dataset.i);
    }, { root: box, rootMargin: "600px 0px" });
    async function layoutPages() {
      width = box.clientWidth;
      const first = await doc.getPage(1);
      scaleNow = scaleFor(first);
      const vp = first.getViewport({ scale: scaleNow });
      for (const sl of pages) {
        sl.drawn = "";
        if (!sl.el.firstChild) { sl.el.style.width = Math.floor(vp.width) + "px"; sl.el.style.height = Math.floor(vp.height) + "px"; }
      }
      io.disconnect();
      pages.forEach((sl) => io.observe(sl.el));
      paintZoom();
    }
    const current = () => {
      const top = box.scrollTop + box.clientHeight / 3;
      let n = 0;
      for (let i = 0; i < pages.length; i++) if (pages[i].el.offsetTop <= top) n = i;
      return n;
    };
    const go = (i) => { const sl = pages[Math.max(0, Math.min(pages.length - 1, i))]; if (sl) box.scrollTo({ top: sl.el.offsetTop - 8 }); };
    box.addEventListener("scroll", () => { if (document.activeElement !== atIn) atIn.value = current() + 1; }, { passive: true });
    atIn.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { go(parseInt(atIn.value, 10) - 1); atIn.blur(); } });
    view.querySelector(".pv-bar").addEventListener("click", (e) => {
      const b = e.target.closest("[data-pv]");
      if (!b) return;
      const a = b.dataset.pv;
      if (a === "prev") return go(current() - 1);
      if (a === "next") return go(current() + 1);
      // + and − zoom about the middle of the view, the way a pinch does about the fingers
      if (a === "in" || a === "out") return zoomTo(scaleNow * (a === "in" ? 1.2 : 1 / 1.2), box.clientWidth / 2, box.clientHeight / 2);
      const at = current();
      zoom = zoom === "fit" ? "page" : "fit";
      ctx.setCfg({ pdfZoom: zoom });
      layoutPages().then(() => go(at));
    });

    /* Zoom where the pointer is: Ctrl + wheel, or a trackpad pinch (which the
     * browser reports as Ctrl + wheel). The pages stretch at once, the point
     * under the pointer stays under it, and they are redrawn sharp when the
     * gesture rests. */
    let settle = 0;
    function zoomTo(s1, cx, cy) {
      if (!doc || !pages.length) return;
      s1 = Math.max(.3, Math.min(5, s1));
      const r = s1 / scaleNow;
      if (Math.abs(r - 1) < .001) return;
      const pad = 12;                     // the strip's padding does not scale
      const x = box.scrollLeft + cx - pad, y = box.scrollTop + cy - pad;
      for (const sl of pages) {
        const w = parseFloat(sl.el.style.width) * r, h = parseFloat(sl.el.style.height) * r;
        sl.el.style.width = w + "px"; sl.el.style.height = h + "px";
        const cv = sl.el.firstChild;
        if (cv) { cv.style.width = w + "px"; cv.style.height = h + "px"; }
        sl.drawn = "";
      }
      // page gaps do not scale either: correct for the ones above the pointer
      const gapsAbove = pages.filter((sl) => sl.el.offsetTop + sl.el.offsetHeight < y).length * 12;
      scaleNow = s1;
      zoom = s1;
      box.scrollLeft = x * r + pad - cx;
      box.scrollTop = (y - gapsAbove) * r + gapsAbove + pad - cy;
      paintZoom();
      box.classList.add("zooming");
      clearTimeout(settle);
      settle = setTimeout(() => {
        box.classList.remove("zooming");
        ctx.setCfg({ pdfZoom: zoom });
        width = box.clientWidth;
        io.disconnect();
        pages.forEach((sl) => io.observe(sl.el));
      }, 200);
    }
    box.addEventListener("wheel", (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      e.stopPropagation();
      const r = box.getBoundingClientRect();
      const d = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
      // a pinch sends many small deltas, a mouse wheel few large ones (~100 a notch)
      zoomTo(scaleNow * Math.exp(-d * (Math.abs(d) >= 50 ? .0022 : .01)), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    let rz = 0;
    const ro = new ResizeObserver(() => {
      clearTimeout(rz);
      rz = setTimeout(() => { if (doc && Math.abs(box.clientWidth - width) > 8 && zoom !== Number(zoom)) layoutPages(); }, 120);
    });
    ro.observe(box);
    (async () => {
      try {
        const lib = await loadPdf();
        doc = await lib.getDocument({ data }).promise;
        if (dead) return;
        view.querySelector(".pv-at b").textContent = doc.numPages;
        atIn.value = 1;
        for (let i = 0; i < doc.numPages; i++) {
          const el_ = document.createElement("div");
          el_.className = "pv-page"; el_.dataset.i = i;
          box.appendChild(el_);
          pages.push({ el: el_, drawn: "" });
        }
        await layoutPages();
        // the words of the first pages, for "Ask in chat"
        let text = "";
        for (let i = 1; i <= Math.min(doc.numPages, 6) && text.length < 6000; i++) {
          const tc = await (await doc.getPage(i)).getTextContent();
          text += tc.items.map((x) => x.str).join(" ") + "\n";
        }
        onText(text.trim());
      } catch (e) {
        view.innerHTML = empty("doc", `This PDF could not be read: ${esc(e.message || e)}`);
      }
    })();
    return { destroy() { dead = true; io.disconnect(); ro.disconnect(); if (doc) doc.destroy(); } };
  }

  function mount(host, ctx) {
    const LIST = `docs:${ctx.id}`;
    let files = [], cur = null, url = null, viewer = null, pdfText = "";
    host.innerHTML =
      `<div class="dc-bar">` +
        `<button type="button" class="side-pick dc-pick" data-dc="pick"></button>` +
        `<span class="sh-gap"></span>` +
        `<button type="button" class="sh-btn" data-dc="add" title="Open a file">${ic("upload")}<span>Open file</span></button>` +
        `<button type="button" class="sh-btn i" data-dc="remove" title="Remove this file from the widget">${ic("trash")}</button>` +
      `</div>` +
      `<div class="dc-view"></div>` +
      `<input type="file" class="dc-file" multiple hidden accept=".pdf,.docx,.png,.jpg,.jpeg,.gif,.webp,.svg,.txt,.md,.csv,.tsv,.json,.xlsx,.xls">`;
    const $ = (s) => host.querySelector(s);
    const view = $(".dc-view");

    async function init() {
      files = (await idb.get(LIST)) || [];
      if (ctx.cfg.sort === "name") files.sort((a, b) => a.name.localeCompare(b.name));
      if (files.length) show(ctx.cfg.cur && files.find((f) => f.id === ctx.cfg.cur) ? ctx.cfg.cur : files[0].id);
      else blank();
    }
    function blank() {
      cur = null;
      $(".dc-pick").textContent = "Documents";
      $('[data-dc="remove"]').hidden = true;
      ctx.setTitle("");
      view.innerHTML = `<div class="dc-drop">${empty("doc",
        "Drop a PDF, Word file, image or CSV here to read it beside the chart. Files stay in this browser.",
        `${ic("upload")}Choose a file`, 'data-dc="add"')}</div>`;
    }

    async function add(list, src) {
      let n = 0;
      for (const f of list) {
        if (f.size > MAX) { ctx.toast(`${f.name} is over 40 MB.`); continue; }
        const id = Math.random().toString(36).slice(2, 10);
        await idb.set(`doc:${id}`, f);
        files.unshift({ id, name: f.name, type: f.type, size: f.size, at: Date.now(), ...(src ? { src } : {}) });
        n++;
      }
      if (!n) return;
      await idb.set(LIST, files);
      show(files[0].id);
    }

    async function show(id) {
      const meta = files.find((f) => f.id === id);
      if (!meta) return blank();
      cur = meta;
      ctx.setCfg({ cur: id });
      ctx.setTitle(meta.name.replace(/\.[^.]+$/, "").slice(0, 24));
      $(".dc-pick").innerHTML = `${esc(meta.name)}${files.length > 1 ? ic("chevronDown", "") : ""}`;
      $('[data-dc="remove"]').hidden = false;
      const blob = await idb.get(`doc:${id}`);
      if (url) URL.revokeObjectURL(url);
      url = null;
      if (viewer) { viewer.destroy(); viewer = null; }
      pdfText = "";
      host.style.setProperty("--dc-size", { s: "13px", m: "14.5px", l: "16.5px" }[ctx.cfg.textSize || "m"]);
      if (!blob) { view.innerHTML = empty("doc", "This file is no longer stored in this browser."); return; }
      const k = kindOf(meta.name, meta.type);
      if (k === "pdf") {
        url = URL.createObjectURL(blob);
        viewer = pdfViewer(view, new Uint8Array(await blob.arrayBuffer()), ctx, (t) => { pdfText = t; });
      } else if (k === "image") {
        url = URL.createObjectURL(blob);
        view.innerHTML = `<div class="dc-img"><img src="${url}" alt="${esc(meta.name)}"></div>`;
      } else if (k === "text") {
        view.innerHTML = `<pre class="dc-text">${esc((await blob.text()).slice(0, 400000))}</pre>`;
      } else if (k === "docx") {
        view.innerHTML = WKit.skel(10);
        try {
          await load(MAMMOTH);
          const r = await window.mammoth.convertToHtml({ arrayBuffer: await blob.arrayBuffer() });
          // mammoth emits semantic HTML only; strip anything active regardless
          const safe = r.value.replace(/<(script|style|iframe)[\s\S]*?<\/\1>/gi, "").replace(/\son\w+="[^"]*"/gi, "");
          view.innerHTML = `<article class="dc-doc">${safe}</article>`;
        } catch (e) { view.innerHTML = empty("doc", `This Word file could not be read: ${esc(e.message)}`); }
      } else if (k === "sheet") {
        view.innerHTML = empty("sheet", `${esc(meta.name)} is a spreadsheet.`, `${ic("sheet")}Open it in a Sheet`, 'data-dc="tosheet"');
      } else {
        view.innerHTML = empty("doc", `${esc(meta.name)} (${size(meta.size)}) cannot be previewed here.`);
      }
    }

    // a PDF from the Browser: fetched by the server (the publishing site will
    // not hand it to this page directly), then kept here like an upload
    async function fromUrl(src, name) {
      await ready;
      const had = files.find((f) => f.src === src);
      if (had) return show(had.id);
      view.innerHTML = `<div class="dc-fetch">${WKit.skel(10)}</div>`;
      let r;
      try { r = await fetch(`${WKit.API}/fetch-file?url=${encodeURIComponent(src)}`); } catch { r = null; }
      if (!r || !r.ok) {
        let why = "The PDF could not be fetched.";
        try { why = (await r.json()).error || why; } catch {}
        ctx.toast(why);
        return cur ? show(cur.id) : blank();
      }
      const nm = decodeURIComponent(r.headers.get("X-File-Name") || "") || name || "document.pdf";
      const blob = await r.blob();
      await add([new File([blob], nm, { type: "application/pdf" })], src);
    }

    async function remove() {
      if (!cur) return;
      await idb.del(`doc:${cur.id}`);
      files = files.filter((f) => f.id !== cur.id);
      await idb.set(LIST, files);
      files.length ? show(files[0].id) : blank();
    }

    $(".dc-file").addEventListener("change", (e) => { add([...e.target.files]); e.target.value = ""; });
    host.addEventListener("dragover", (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) { e.preventDefault(); host.classList.add("dc-over"); } });
    host.addEventListener("dragleave", (e) => { if (!host.contains(e.relatedTarget)) host.classList.remove("dc-over"); });
    host.addEventListener("drop", (e) => {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      e.preventDefault(); host.classList.remove("dc-over");
      add([...e.dataTransfer.files]);
    });
    host.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-dc]");
      if (!b) return;
      e.stopPropagation();
      const a = b.dataset.dc;
      if (a === "add") return $(".dc-file").click();
      if (a === "remove") return remove();
      if (a === "tab" && url) return open(url, "_blank", "noopener");
      if (a === "tosheet" && cur) {
        const blob = await idb.get(`doc:${cur.id}`);
        await load("./vendor/sheet/xlsx.full.min.js");
        const book = XLSX.read(await blob.arrayBuffer());
        const sh = book.Sheets[book.SheetNames[0]];
        const rows = XLSX.utils.sheet_to_json(sh, { header: 1, raw: true, defval: "" }).slice(0, 5000);
        return ctx.send("sheet", { table: { title: cur.name.replace(/\.[^.]+$/, ""), columns: rows[0] || [], rows: rows.slice(1) } });
      }
      if (a === "pick" && files.length > 1) {
        return ctx.menu(b, [{ head: "Documents" }, ...files.map((f) => ({ id: f.id, label: f.name, hint: size(f.size), on: cur && cur.id === f.id }))],
          (id) => show(id));
      }
    });

    let booted = false, ready = null;
    const boot = () => { if (!booted) { booted = true; ready = init(); } return ready; };
    return {
      show() { boot(); },
      receive(p) { if (p && p.url) { boot(); fromUrl(p.url, p.name); } },
      config(cfg, patch) { if (("textSize" in patch || "pdfZoom" in patch) && cur && !("pdfZoom" in patch && viewer)) show(cur.id); },
      ask: () => {
        const t = view.querySelector(".dc-doc, .dc-text");
        const text = t ? t.innerText : pdfText;
        return text ? { sub: `${cur.name}`, context: `Document "${cur.name}" (its text, first part):\n${text.slice(0, 5500)}`,
          question: "Summarise this and pull out anything that matters for the stock." } : null;
      },
    };
  }

  Dock.register({
    type: "docs", title: "Documents", icon: "doc", hue: "rose", group: "Research",
    desc: "Read PDFs, Word files and images beside the chart", zone: "right", minW: 320, mount,
    agent: { writes: { url: "a PDF / document URL to open", name: "its display name" } },
    settings: [
      { section: "Reading" },
      { key: "pdfZoom", label: "PDF opens at", def: "fit", options: [{ v: "fit", label: "Fit width" }, { v: "page", label: "Whole page" }, { v: 1, label: "100%" }] },
      { key: "textSize", label: "Text size", def: "m", hint: "Word and text files", options: [{ v: "s", label: "Small" }, { v: "m", label: "Medium" }, { v: "l", label: "Large" }] },
      { section: "Files" },
      { key: "sort", label: "List files by", def: "new", options: [{ v: "new", label: "Newest" }, { v: "name", label: "Name" }] },
      { label: "Files stay in this browser and are never uploaded. Clearing the site's data removes them.", kind: "note" },
    ],
  });
})();
