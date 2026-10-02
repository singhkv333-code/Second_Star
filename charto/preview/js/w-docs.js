/* Charto preview — the Documents widget: read beside the chart.
 *
 * Drop in an annual report, a broker note, a screenshot, a CSV: PDFs open in
 * the browser's own viewer, Word files are turned into readable HTML
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

  function mount(host, ctx) {
    const LIST = `docs:${ctx.id}`;
    let files = [], cur = null, url = null;
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

    async function add(list) {
      let n = 0;
      for (const f of list) {
        if (f.size > MAX) { ctx.toast(`${f.name} is over 40 MB.`); continue; }
        const id = Math.random().toString(36).slice(2, 10);
        await idb.set(`doc:${id}`, f);
        files.unshift({ id, name: f.name, type: f.type, size: f.size, at: Date.now() });
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
      if (!blob) { view.innerHTML = empty("doc", "This file is no longer stored in this browser."); return; }
      const k = kindOf(meta.name, meta.type);
      if (k === "pdf") {
        url = URL.createObjectURL(blob);
        // a browser without a built-in PDF viewer (Android Chrome, some
        // embedded browsers) would draw an empty frame — say so instead
        view.innerHTML = navigator.pdfViewerEnabled === false
          ? empty("doc", "This browser cannot show PDFs inside a page.", `${ic("externalLink")}Open it in a new tab`, `data-dc="tab"`)
          : `<iframe class="dc-frame" title="${esc(meta.name)}" src="${url}#view=FitH"></iframe>`;
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

    let booted = false;
    return {
      show() { if (!booted) { booted = true; init(); } },
      ask: () => {
        const t = view.querySelector(".dc-doc, .dc-text");
        return t ? `Summarise this document (${cur.name}) and pull out anything that matters for the stock:\n\n${t.innerText.slice(0, 6000)}` : "";
      },
    };
  }

  Dock.register({
    type: "docs", title: "Documents", icon: "doc", hue: "rose", group: "Research",
    desc: "Read PDFs, Word files and images beside the chart", zone: "right", minW: 320, mount,
  });
})();
