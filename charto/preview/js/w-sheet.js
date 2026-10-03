/* Charto preview — the Sheet widget: a spreadsheet beside the chart.
 *
 * The grid is Jspreadsheet CE (MIT) with its formula plugin; Excel files go
 * in and out through SheetJS. All of it is vendored (vendor/sheet/) and
 * loaded the first time a sheet is opened, never with the page.
 *
 * What makes it this app's spreadsheet rather than any spreadsheet is a small
 * set of MARKET FUNCTIONS that read the same store the chart draws:
 *
 *   =PRICE("TCS")   last price          =CHG("TCS")     change on the day
 *   =CHGPCT("TCS")  change, %           =PREVCLOSE("TCS")
 *   =HIGH/LOW/OPEN("TCS")  today's range
 *   =FEATURE("TCS", "rsi14")  any screener feature, end of day
 *
 * A function asked about a symbol it has no price for yet answers "…" and
 * fills in when /quotes answers; one the store holds NOTHING for answers
 * #N/A — never a zero. Prices refresh every 30 seconds while a sheet is on
 * screen. Exported to Excel, these cells carry their values (Excel has no
 * PRICE()), and the export says so.
 *
 * Other widgets can hand a table to a sheet (Dock.send("sheet", {table})):
 * the screener's results, a financial statement. It arrives as a new tab.
 * Workbooks are kept per sheet widget in this browser (IndexedDB).
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const { API, esc, ic, loadAll, load, idb, empty } = WKit;
  const LIBS = ["./vendor/sheet/jsuites.css", "./vendor/sheet/jspreadsheet.css",
                "./vendor/sheet/jsuites.js", "./vendor/sheet/formula.js", "./vendor/sheet/jspreadsheet.js"];
  const XLSX_LIB = "./vendor/sheet/xlsx.full.min.js";
  const MARKET = ["PRICE", "CHG", "CHGPCT", "PREVCLOSE", "HIGH", "LOW", "OPEN", "FEATURE"];
  const MARKET_RE = new RegExp(`\\b(${MARKET.join("|")})\\s*\\(`, "i");
  const REFRESH_MS = 30_000;

  /* ── the market functions: one cache, batched fetches, then a recalc ── */
  const quotes = new Map();     // SYMBOL → { q, at }
  const feats = new Map();      // SYMBOL → { f, at }
  const wantQ = new Set(), wantF = new Set();
  let flushT = 0;
  const sheets = new Set();     // live sheet controllers, for recalc

  function schedule() {
    clearTimeout(flushT);
    flushT = setTimeout(flush, 60);
  }
  async function flush() {
    const qs = [...wantQ], fs = [...wantF];
    wantQ.clear(); wantF.clear();
    try {
      if (qs.length) {
        const r = await Net.get(`${API}/quotes?symbols=` + encodeURIComponent(qs.slice(0, 120).join(",")));
        const d = await r.json();
        for (const q of d.quotes || []) quotes.set(q.symbol, { q, at: Date.now() });
        for (const s of qs) if (!quotes.has(s)) quotes.set(s, { q: { symbol: s, last: null }, at: Date.now() });
      }
      if (fs.length) {
        const r = await Net.get(`${API}/screen/features?symbols=` + encodeURIComponent(fs.slice(0, 120).join(",")));
        const d = await r.json();
        for (const s of fs) feats.set(s, { f: (d.features || {})[s] || null, at: Date.now() });
      }
    } catch (e) {
      console.warn("[sheet] market data", e);
      for (const s of qs) if (!quotes.has(s)) quotes.set(s, { q: { symbol: s, last: null, error: true }, at: Date.now() });
    }
    for (const sh of sheets) sh.recalc();
  }
  const symOf = (v) => String(Array.isArray(v) ? v.flat()[0] : v || "").trim().toUpperCase();
  function quoteFn(field) {
    return (sym) => {
      const s = symOf(sym);
      if (!s) return "#VALUE!";
      const hit = quotes.get(s);
      if (!hit || Date.now() - hit.at > REFRESH_MS) { wantQ.add(s); schedule(); }
      if (!hit) return "…";
      const v = hit.q[field];
      return v == null ? "#N/A" : v;
    };
  }
  function featureFn(sym, name) {
    const s = symOf(sym), k = String(name || "").trim();
    if (!s || !k) return "#VALUE!";
    const hit = feats.get(s);
    if (!hit) { wantF.add(s); schedule(); return "…"; }
    const v = hit.f && hit.f[k];
    return v == null ? "#N/A" : v;
  }

  let ready = null;
  function boot() {
    if (ready) return ready;
    ready = loadAll(LIBS).then(() => {
      window.formula.setFormula({
        PRICE: quoteFn("last"), CHG: quoteFn("change"), CHGPCT: quoteFn("change_pct"),
        PREVCLOSE: quoteFn("prev_close"), HIGH: quoteFn("high"), LOW: quoteFn("low"),
        OPEN: quoteFn("open"), FEATURE: featureFn,
      });
    });
    return ready;
  }

  const FUNCS = [
    { f: '=PRICE("TCS")', d: "Last price" },
    { f: '=CHGPCT("TCS")', d: "Change on the day, %" },
    { f: '=CHG("TCS")', d: "Change on the day" },
    { f: '=PREVCLOSE("TCS")', d: "Previous close" },
    { f: '=FEATURE("TCS", "rsi14")', d: "Any screener feature" },
    { sep: true },
    { f: "=SUM(A1:A10)", d: "Sum" }, { f: "=AVERAGE(A1:A10)", d: "Average" },
    { f: "=MAX(A1:A10)", d: "Largest" }, { f: "=MIN(A1:A10)", d: "Smallest" },
    { f: "=STDEV(A1:A10)", d: "Standard deviation" },
  ];

  function seed() {
    // A first sheet shows what it can do, with formulas, not typed numbers.
    const rows = [["Symbol", "Last", "Chg %", "Prev close", "RSI 14"]];
    for (const s of ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]) {
      const r = rows.length + 1;
      rows.push([s, `=PRICE(A${r})`, `=CHGPCT(A${r})`, `=PREVCLOSE(A${r})`, `=FEATURE(A${r}, "rsi14")`]);
    }
    return { sheets: [{ name: "Watch", data: rows, style: { A1: "font-weight:600", B1: "font-weight:600",
      C1: "font-weight:600", D1: "font-weight:600", E1: "font-weight:600" }, widths: [110, 96, 80, 96, 80] }] };
  }

  function mount(host, ctx) {
    const KEY = `sheet:${ctx.id}`;
    let wb = null, saveT = 0, refreshT = 0, sel = null, pending = [];
    host.innerHTML =
      `<div class="sh-bar" role="toolbar" aria-label="Sheet">` +
        `<button type="button" class="sh-btn" data-sh="file" title="File">${ic("doc")}<span>File</span>${ic("chevronDown")}</button>` +
        `<span class="sh-sep"></span>` +
        `<button type="button" class="sh-btn i" data-sh="bold" title="Bold">${ic("bold")}</button>` +
        `<button type="button" class="sh-btn i" data-sh="italic" title="Italic">${ic("italic")}</button>` +
        `<button type="button" class="sh-btn i" data-sh="align" title="Alignment">${ic("listBullet")}</button>` +
        `<button type="button" class="sh-btn i" data-sh="fill" title="Cell colour"><span class="sh-sw"></span></button>` +
        `<span class="sh-sep"></span>` +
        `<button type="button" class="sh-btn" data-sh="fx" title="Insert a function">${ic("sigma")}<span>Functions</span></button>` +
        `<span class="sh-gap"></span>` +
        `<span class="sh-state" aria-live="polite"></span>` +
        `<button type="button" class="sh-btn i" data-sh="ask" title="Ask in chat about the selection">${ic("chat")}</button>` +
      `</div>` +
      `<div class="sh-fx"><span class="sh-addr">A1</span><span class="sh-fxl">fx</span>` +
        `<input class="sh-in" spellcheck="false" autocomplete="off" aria-label="Cell contents"></div>` +
      `<div class="sh-grid">${WKit.skel(10, "sheet")}</div>` +
      `<input type="file" class="sh-file" accept=".xlsx,.xls,.csv,.tsv" hidden>`;
    const $ = (s) => host.querySelector(s);
    const grid = $(".sh-grid"), fxIn = $(".sh-in"), addr = $(".sh-addr"), state = $(".sh-state");

    const ws = () => wb && wb[wb[0].getWorksheetActive()];
    const colName = (x) => { let s = ""; x++; while (x) { const m = (x - 1) % 26; s = String.fromCharCode(65 + m) + s; x = Math.floor((x - 1) / 26); } return s; };

    function say(t) { state.textContent = t; }
    function persist() {
      clearTimeout(saveT);
      say("Saving…");
      saveT = setTimeout(async () => {
        if (!wb) return;
        // a sheet still being built has no methods yet: save its options' data
        const out = { sheets: wb.map((w) => ({
          name: w.options.worksheetName, data: typeof w.getData === "function" ? w.getData() : (w.options.data || []),
          style: typeof w.getStyle === "function" ? w.getStyle() : {}, widths: typeof w.getWidth === "function" ? w.getWidth() : [] })) };
        await idb.set(KEY, out);
        say("Saved on this device");
      }, 600);
    }

    function wsOptions(sh) {
      const rows = Math.max(40, (sh.data || []).length + 10);
      const cols = Math.max(12, ...((sh.data || []).map((r) => r.length)), 1);
      return {
        worksheetName: sh.name || "Sheet", data: sh.data || [], minDimensions: [cols, rows],
        defaultColWidth: 96, columns: (sh.widths || []).map((w) => ({ width: +w || 96 })),
        style: sh.style || {}, tableOverflow: false,
      };
    }

    async function build() {
      await boot();
      const saved = (await idb.get(KEY)) || null;
      const book = saved && saved.sheets && saved.sheets.length ? saved : seed();
      grid.innerHTML = "";
      const holder = document.createElement("div");
      grid.appendChild(holder);
      const made = jspreadsheet(holder, {
        tabs: true, toolbar: false, contextMenu: undefined,
        worksheets: book.sheets.map(wsOptions),
        onafterchanges: () => persist(),
        onchangestyle: () => persist(),
        onresizecolumn: () => persist(),
        oncreateworksheet: () => persist(),
        ondeleteworksheet: () => persist(),
        oninsertrow: () => persist(), oninsertcolumn: () => persist(),
        ondeleterow: () => persist(), ondeletecolumn: () => persist(),
        onselection: (w, x1, y1, x2, y2) => {
          sel = { w, x1, y1, x2, y2 };
          addr.textContent = colName(x1) + (y1 + 1) + (x2 !== x1 || y2 !== y1 ? ":" + colName(x2) + (y2 + 1) : "");
          const v = w.getValueFromCoords(x1, y1);
          fxIn.value = v == null ? "" : String(v);
        },
      });
      // the spreadsheet's own list, which grows and shrinks with its tabs
      wb = made[0].parent.worksheets;
      for (const p of pending.splice(0)) receive(p);
      ctx.setTitle(book.sheets[0].name);
      say("Saved on this device");
    }

    /** Re-evaluate every cell whose formula reads the market. */
    function recalc() {
      if (!wb) return;
      for (const w of wb) {
        const data = w.options.data || [];
        for (let y = 0; y < data.length; y++) for (let x = 0; x < (data[y] || []).length; x++) {
          const v = data[y][x];
          if (typeof v === "string" && v[0] === "=" && MARKET_RE.test(v)) {
            try { w.setValueFromCoords(x, y, v, true); } catch { /* a bad formula shows its own error */ }
            // a cell that reads the market says so, faintly
            try { const td = w.getCellFromCoords(x, y); if (td) td.classList.toggle("sh-live", ctx.cfg.liveTint !== false); } catch { }
          }
        }
      }
    }
    const ctl = { recalc };

    function refresh() {
      for (const k of quotes.keys()) wantQ.add(k);
      if (wantQ.size) schedule();
    }

    /* ── formula bar ── */
    fxIn.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter" && sel) {
        sel.w.setValueFromCoords(sel.x1, sel.y1, fxIn.value);
        fxIn.blur();
      }
      if (e.key === "Escape") fxIn.blur();
    });
    // keys typed into the grid belong to the grid, not the chart's shortcuts
    grid.addEventListener("keydown", (e) => { if (!e.ctrlKey && !e.metaKey) e.stopPropagation(); });

    /* ── the toolbar ── */
    function styleSel(k, vOn, vOff) {
      if (!sel) return ctx.toast("Select some cells first.");
      const w = sel.w, cur = (w.getStyle(colName(sel.x1) + (sel.y1 + 1)) || "");
      const on = new RegExp(k + "\\s*:\\s*" + vOn).test(cur);
      const o = {};
      for (let y = Math.min(sel.y1, sel.y2); y <= Math.max(sel.y1, sel.y2); y++)
        for (let x = Math.min(sel.x1, sel.x2); x <= Math.max(sel.x1, sel.x2); x++)
          o[colName(x) + (y + 1)] = `${k}:${on ? vOff : vOn}`;
      w.setStyle(o);
    }
    function setStyleAll(k, v) {
      if (!sel) return ctx.toast("Select some cells first.");
      const o = {};
      for (let y = Math.min(sel.y1, sel.y2); y <= Math.max(sel.y1, sel.y2); y++)
        for (let x = Math.min(sel.x1, sel.x2); x <= Math.max(sel.x1, sel.x2); x++)
          o[colName(x) + (y + 1)] = `${k}:${v}`;
      sel.w.setStyle(o);
    }

    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-sh]");
      if (!b) return;
      e.stopPropagation();
      const a = b.dataset.sh;
      if (!wb && a !== "file") return;
      if (a === "bold") return styleSel("font-weight", "600", "400");
      if (a === "italic") return styleSel("font-style", "italic", "normal");
      if (a === "align") {
        return ctx.menu(b, [{ head: "Align" }, { id: "left", label: "Left" }, { id: "center", label: "Centre" }, { id: "right", label: "Right" }],
          (v) => setStyleAll("text-align", v));
      }
      if (a === "fill") {
        const C = [["none", "No fill", "transparent"], ["grey", "Grey", "#f1f2f4"], ["green", "Green", "#e3f4ec"],
                   ["red", "Red", "#fbe6e6"], ["yellow", "Yellow", "#fdf4d7"], ["blue", "Blue", "#e6eefb"]];
        return ctx.menu(b, [{ head: "Cell colour" }, ...C.map(([id, l]) => ({ id, label: l }))],
          (id) => setStyleAll("background-color", C.find((c) => c[0] === id)[2]));
      }
      if (a === "fx") {
        return ctx.menu(b, [{ head: "Market functions" },
          ...FUNCS.map((f, i) => f.sep ? { sep: true } : { id: String(i), label: f.f, hint: f.d })],
          (i) => {
            if (!sel) return ctx.toast("Select a cell first.");
            sel.w.setValueFromCoords(sel.x1, sel.y1, FUNCS[+i].f);
          }, "sh-fxmenu");
      }
      if (a === "ask") return ask(true);
      if (a === "file") {
        return ctx.menu(b, [
          { head: "Workbook" },
          { id: "new", label: "New sheet", icon: "plus" },
          { id: "rename", label: "Rename this sheet", icon: "pen" },
          { id: "import", label: "Open an Excel or CSV file…", icon: "upload" },
          { sep: true },
          { id: "xlsx", label: "Download as Excel (.xlsx)", icon: "download" },
          { id: "csv", label: "Download this sheet as CSV", icon: "download" },
          { sep: true },
          { id: "delete", label: "Delete this sheet", icon: "trash", danger: true },
        ], fileAction);
      }
    });

    async function fileAction(a) {
      if (a === "new") return wb[0].createWorksheet({ worksheetName: `Sheet${wb.length + 1}`, minDimensions: [12, 40] });
      if (a === "import") return $(".sh-file").click();
      if (a === "rename") {
        const w = ws();
        const tab = host.querySelectorAll(".jtabs-headers > div:not(.jtabs-add)")[wb[0].getWorksheetActive()];
        if (!tab) return;
        tab.contentEditable = "true"; tab.focus();
        document.getSelection().selectAllChildren(tab);
        const done = () => {
          tab.contentEditable = "false";
          const name = tab.textContent.trim().slice(0, 40) || w.options.worksheetName;
          w.options.worksheetName = name; tab.textContent = name; persist();
        };
        tab.addEventListener("blur", done, { once: true });
        tab.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); tab.blur(); } });
        return;
      }
      if (a === "delete") {
        if (wb.length < 2) return ctx.toast("A workbook keeps at least one sheet.");
        return wb[0].deleteWorksheet(wb[0].getWorksheetActive());
      }
      if (a === "csv") {
        const w = ws();
        const rows = w.getData(false, true).filter((r) => r.some((c) => c !== "" && c != null));
        const csv = rows.map((r) => r.map((c) => /[",\n]/.test(String(c)) ? `"${String(c).replace(/"/g, '""')}"` : c).join(",")).join("\n");
        return saveFile(new Blob([csv], { type: "text/csv" }), `${w.options.worksheetName}.csv`);
      }
      if (a === "xlsx") {
        await load(XLSX_LIB);
        const book = XLSX.utils.book_new();
        let live = 0;
        for (const w of wb) {
          const raw = w.getData(), shown = w.getData(false, true);
          const aoa = raw.map((r, y) => r.map((c, x) => {
            if (typeof c === "string" && c[0] === "=") {
              if (MARKET_RE.test(c)) { live++; const v = shown[y][x]; return isNaN(+v) || v === "" ? v : +v; }
              return { f: c.slice(1) };
            }
            return c === "" ? null : isNaN(+c) || c === null ? c : +c;
          }));
          const sheet = XLSX.utils.aoa_to_sheet(aoa.map((r) => r.map((c) => (c && c.f ? null : c))));
          aoa.forEach((r, y) => r.forEach((c, x) => {
            if (c && c.f) sheet[XLSX.utils.encode_cell({ r: y, c: x })] = { t: "n", f: c.f };
          }));
          XLSX.utils.book_append_sheet(book, sheet, String(w.options.worksheetName).slice(0, 31));
        }
        const out = XLSX.write(book, { bookType: "xlsx", type: "array" });
        saveFile(new Blob([out], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }), "pivot-workbook.xlsx");
        if (live) ctx.toast(`${live} live market cell${live > 1 ? "s were" : " was"} saved as values — Excel has no PRICE().`);
      }
    }

    function saveFile(blob, name) {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    }

    $(".sh-file").addEventListener("change", async (e) => {
      const f = e.target.files[0];
      e.target.value = "";
      if (!f) return;
      try {
        await load(XLSX_LIB);
        const book = XLSX.read(await f.arrayBuffer(), { cellFormula: true });
        let n = 0;
        for (const name of book.SheetNames.slice(0, 12)) {
          const sh = book.Sheets[name];
          const range = XLSX.utils.decode_range(sh["!ref"] || "A1:A1");
          const data = [];
          for (let r = range.s.r; r <= Math.min(range.e.r, 4999); r++) {
            const row = [];
            for (let c = range.s.c; c <= Math.min(range.e.c, 199); c++) {
              const cell = sh[XLSX.utils.encode_cell({ r, c })];
              row.push(!cell ? "" : cell.f ? "=" + cell.f : cell.w != null && cell.t !== "n" ? cell.w : cell.v);
            }
            data.push(row);
          }
          wb[0].createWorksheet(wsOptions({ name: name.slice(0, 40), data }));
          n++;
        }
        ctx.toast(`Opened ${n} sheet${n > 1 ? "s" : ""} from ${f.name}.`);
      } catch (err) {
        ctx.toast(`That file could not be read: ${err.message || err}`);
      }
    });

    /** A table handed over by another widget becomes a new sheet. */
    function receive(p) {
      if (!wb) { pending.push(p); return; }
      const t = p && p.table;
      if (!t) return;
      const data = [t.columns || [], ...(t.rows || [])];
      const name = String(t.title || "Imported").slice(0, 40);
      wb[0].createWorksheet(wsOptions({ name, data,
        style: Object.fromEntries((t.columns || []).map((_, x) => [colName(x) + "1", "font-weight:600"])) }));
      setTimeout(() => wb[0].openWorksheet && wb[0].openWorksheet(wb.length - 1), 0);
      ctx.toast(`“${name}” added as a sheet.`);
    }

    function ask(explicit) {
      const w = ws();
      if (!w) return "";
      let rows;
      if (sel && (sel.x1 !== sel.x2 || sel.y1 !== sel.y2)) {
        rows = [];
        for (let y = Math.min(sel.y1, sel.y2); y <= Math.max(sel.y1, sel.y2); y++) {
          const r = [];
          for (let x = Math.min(sel.x1, sel.x2); x <= Math.max(sel.x1, sel.x2); x++) r.push(w.getValueFromCoords(x, y, true));
          rows.push(r);
        }
      } else rows = w.getData(false, true).filter((r) => r.some((c) => c !== "" && c != null)).slice(0, 40);
      if (!rows.length) { if (explicit) ctx.toast("This sheet is empty."); return ""; }
      const md = rows.map((r) => "| " + r.map((c) => String(c ?? "").replace(/<[^>]+>/g, "")).join(" | ") + " |");
      md.splice(1, 0, "| " + rows[0].map(() => "---").join(" | ") + " |");
      const text = `Here is a table from my sheet "${w.options.worksheetName}":\n\n${md.join("\n")}\n\nWhat stands out, and what would you check next?`;
      if (explicit) ctx.compose(text);
      return text;
    }

    function prefs() {
      const c = ctx.cfg;
      host.classList.toggle("sh-nofx", c.fxBar === false);
      host.classList.toggle("sh-nogrid", c.grid === false);
      host.classList.toggle("sh-zebra", !!c.zebra);
      host.style.setProperty("--sh-size", { s: "12px", m: "13px", l: "14.5px" }[c.textSize || "m"]);
    }
    function poll() {
      clearInterval(refreshT);
      const ms = Number(ctx.cfg.refresh ?? REFRESH_MS);
      if (ms > 0) refreshT = setInterval(() => { if (document.visibilityState === "visible") refresh(); }, ms);
    }
    prefs();

    return {
      show() {
        sheets.add(ctl);
        if (!wb) build().catch((e) => { grid.innerHTML = empty("sheet", `The spreadsheet could not load: ${esc(e.message || e)}`); });
        else recalc();
        poll();
      },
      config(cfg, patch) {
        prefs();
        if ("refresh" in patch) poll();
        if ("liveTint" in patch) recalc();
      },
      hide() { sheets.delete(ctl); clearInterval(refreshT); },
      receive, ask: () => ask(false),
      duplicate(copyId) { idb.get(KEY).then((v) => v && idb.set(`sheet:${copyId}`, v)); },
    };
  }

  Dock.register({
    type: "sheet", title: "Sheet", icon: "sheet", hue: "green", group: "Tools",
    desc: "A spreadsheet with live market functions and Excel files",
    zone: "bottom", mount,
    settings: [
      { section: "Live cells" },
      { key: "refresh", label: "Market functions update", def: 30000, hint: "PRICE, CHG, FEATURE and the rest",
        options: [{ v: 15000, label: "15s" }, { v: 30000, label: "30s" }, { v: 60000, label: "1m" }, { v: 0, label: "Off" }] },
      { key: "liveTint", label: "Tint cells that read the market", kind: "toggle", def: true },
      { section: "Look" },
      { key: "textSize", label: "Text size", def: "m", options: [{ v: "s", label: "Small" }, { v: "m", label: "Medium" }, { v: "l", label: "Large" }] },
      { key: "grid", label: "Gridlines", kind: "toggle", def: true },
      { key: "zebra", label: "Banded rows", kind: "toggle", def: false },
      { key: "fxBar", label: "Formula bar", kind: "toggle", def: true },
      { kind: "note", label: "Sheets are saved in this browser. Export from File to keep a copy." },
    ],
  });
})();
