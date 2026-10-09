/* Charto preview — a custom indicator's code, in the sidebar.
 *
 * The chat prints a file card when it builds an indicator; Open lands here.
 * The sidebar then carries editor tabs above the conversation — "Chat" and
 * each open file — and switching never unmounts the thread.
 *
 * The file is shown the way a web IDE shows it: a dark editor surface, a
 * gutter of line numbers, long lines wrapped under their own number, and
 * highlight.js colouring. Edit makes the same surface editable in place
 * (CodeJar over a contenteditable — vendor/codeedit/README.md); Save runs the
 * server's validator on the new code and only a pass becomes a new version
 * on the chart. Both libraries load on first open, never with the page.
 *
 * Everything shown is the stored build record (GET /custom_indicators/<id>).
 */
"use strict";

const CodeView = (() => {
  const API = location.port === "5173"
    ? "http://127.0.0.1:5174" : "";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const headers = (json) => ({
    ...(typeof Auth !== "undefined" ? Auth.headers() : {}),
    ...(json ? { "Content-Type": "application/json" } : {}),
  });

  let panel, bar, view, files = [], current = null, delArmed = false;

  const slug = (t) => (String(t || "indicator").toLowerCase()
    .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "indicator") + ".py";

  // ── the highlighter, loaded once on first use ─────────────────────────
  let libs = null;
  function loadLibs() {
    if (!libs) {
      libs = Promise.all([
        import("../vendor/codeedit/hljs-core.js"),
        import("../vendor/codeedit/hljs-python.js"),
        import("../vendor/codeedit/codejar.js"),
      ]).then(([core, py, jar]) => {
        const hljs = core.default;
        hljs.registerLanguage("python", py.default);
        return { hljs, CodeJar: jar.CodeJar };
      });
    }
    return libs;
  }
  const highlight = (hljs, code) => hljs.highlight(code, { language: "python", ignoreIllegals: true }).value;

  /** Highlighted HTML → one HTML string per source line. A token can span
   *  lines (a docstring), so every open span is closed at a newline and
   *  reopened on the next line — each row stays well-formed on its own. */
  function splitLines(html) {
    const out = [];
    let line = "", stack = [];
    const re = /(<span[^>]*>)|(<\/span>)|([^<]+)/g;
    let m;
    while ((m = re.exec(html))) {
      if (m[1]) { stack.push(m[1]); line += m[1]; }
      else if (m[2]) { stack.pop(); line += m[2]; }
      else {
        const parts = m[3].split("\n");
        parts.forEach((p, i) => {
          if (i > 0) {
            out.push(line + "</span>".repeat(stack.length));
            line = stack.join("");
          }
          line += p;
        });
      }
    }
    out.push(line + "</span>".repeat(stack.length));
    return out;
  }

  // ── the sidebar's tabs ─────────────────────────────────────────────────
  function mount() {
    if (panel) return;
    panel = document.getElementById("chatPanel");
    bar = document.createElement("div");
    bar.className = "cv-tabs";
    bar.hidden = true;
    bar.setAttribute("role", "tablist");
    panel.insertBefore(bar, panel.firstChild);
    view = document.createElement("section");
    view.className = "cv-view";
    view.hidden = true;
    panel.insertBefore(view, panel.querySelector(".thread"));
    bar.addEventListener("click", (e) => {
      const x = e.target.closest("[data-cv-close]");
      if (x) { e.stopPropagation(); close(x.dataset.cvClose); return; }
      const t = e.target.closest("[data-cv-tab]");
      if (t) show(t.dataset.cvTab === "chat" ? null : t.dataset.cvTab);
    });
    view.addEventListener("click", onAction);
    // asking something is a chat act: the answer lands in the thread
    const form = document.getElementById("chatForm");
    if (form) form.addEventListener("submit", () => show(null), true);
    const input = document.getElementById("chatInput");
    if (input) input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && input.value.trim()) show(null);
    }, true);
  }

  function renderTabs() {
    bar.hidden = !files.length;
    bar.innerHTML = `<button type="button" class="cv-tab${current ? "" : " on"}" data-cv-tab="chat"
        role="tab" aria-selected="${!current}">${Icons.svg("chat", "xs")}Chat</button>`
      + files.map((f) => `<button type="button" class="cv-tab${current === f.id ? " on" : ""}"
        data-cv-tab="${esc(f.id)}" role="tab" aria-selected="${current === f.id}"
        title="${esc(f.title)}">${Icons.svg("code", "xs")}<span>${esc(f.name)}</span>
        ${f.dirty ? '<b class="cv-dot" title="Unsaved changes"></b>' : ""}
        <i data-cv-close="${esc(f.id)}" title="Close" aria-label="Close">${Icons.svg("x", "xs")}</i></button>`).join("");
  }

  /** null = the conversation; an id = that file. */
  function show(id) {
    mount();
    current = id && files.find((f) => f.id === id) ? id : null;
    delArmed = false;
    [panel.querySelector(".chat-actions"), panel.querySelector(".thread")]
      .forEach((el) => { if (el) el.hidden = !!current; });
    const tb = document.getElementById("toBottom");
    if (tb && current) tb.classList.remove("show");
    view.hidden = !current;
    renderTabs();
    if (current) render();
  }

  function close(id) {
    files = files.filter((f) => f.id !== id);
    show(current === id ? (files.length ? files[files.length - 1].id : null) : current);
  }

  async function open(id, opts = {}) {
    mount();
    if (panel.classList.contains("hidden")) {
      const t = document.getElementById("chatToggle");
      if (t) t.click();
    }
    let f = files.find((x) => x.id === id);
    if (!f) {
      f = { id, name: "loading…", title: "", rec: null, error: "", editing: false };
      files.push(f);
    }
    if (opts.edit) f.editing = true;
    show(id);
    try {
      const [r] = await Promise.all([
        fetch(`${API}/custom_indicators/${encodeURIComponent(id)}`, { headers: headers() }),
        loadLibs(),
      ]);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      Object.assign(f, { rec: d, title: d.spec.title, name: slug(d.spec.short || d.spec.title), error: "" });
    } catch (e) {
      f.error = e.message;
      f.name = "unavailable";
    }
    if (current === id) { renderTabs(); render(); }
  }

  // ── the file ───────────────────────────────────────────────────────────
  async function render() {
    const f = files.find((x) => x.id === current);
    if (!f) return;
    if (f.error) { view.innerHTML = `<p class="cv-empty">Could not open this file: ${esc(f.error)}</p>`; return; }
    if (!f.rec) { view.innerHTML = `<div class="cv-editor"><p class="cv-empty">Opening…</p></div>`; return; }
    const { hljs, CodeJar } = await loadLibs();
    const d = f.rec, sp = d.spec || {}, rep = d.report || {};
    const checks = rep.checks || [];
    const code = String(f.draft != null ? f.draft : (d.code || "")).replace(/\s+$/, "");
    const rows = checks.map((c) => `<li class="is-${esc(c.status)}"><b>${esc(c.label)}</b>`
      + (c.status !== "pass" && c.detail ? `<span>${esc(c.detail)}</span>` : "") + `</li>`).join("");
    const srcs = (sp.sources || []).map((s) => `<li><a href="${esc(s.url)}" target="_blank"
      rel="noopener noreferrer">${esc(s.title || s.url)}</a></li>`).join("");
    const fails = (f.result && !f.result.ok)
      ? (f.result.report.checks || []).filter((c) => c.status === "fail")
        .map((c) => `<li><b>${esc(c.label)}</b>${c.detail ? `<span>${esc(c.detail)}</span>` : ""}</li>`).join("")
      : "";

    view.innerHTML = `
      <div class="cv-editor${f.editing ? " is-editing" : ""}">
        <header class="cv-bar">
          <span class="cv-crumb">${Icons.svg("code", "xs")}<b title="${esc(f.name)}">${esc(f.name)}</b></span>
          <span class="cv-acts">${f.editing ? `
            <button type="button" class="cv-btn" data-cv="cancel">Cancel</button>
            <button type="button" class="cv-btn primary" data-cv="save"${f.saving ? " disabled" : ""}>
              ${f.saving ? "Testing…" : "Save &amp; test"}</button>` : `
            <button type="button" class="cv-icon" data-cv="copy" title="Copy code" aria-label="Copy code">${Icons.svg("copy", "sm")}</button>
            <button type="button" class="cv-icon" data-cv="edit" title="Edit code" aria-label="Edit code">${Icons.svg("pen", "sm")}</button>
            <button type="button" class="cv-icon danger" data-cv="delete" title="${delArmed ? "Press again to delete" : "Delete"}"
              aria-label="Delete">${delArmed ? "Delete?" : Icons.svg("trash", "sm")}</button>`}</span>
        </header>
        ${f.editing
          ? `<div class="cv-edit"><div class="cv-gutter" aria-hidden="true"></div>
               <code class="cv-input hljs" spellcheck="false"></code></div>`
          : `<div class="cv-lines">${splitLines(highlight(hljs, code)).map((h, i) =>
              `<div class="cv-row"><span class="cv-ln">${i + 1}</span><span class="cv-src">${h || " "}</span></div>`).join("")}</div>`}
      </div>
      ${fails ? `<div class="cv-fail"><b>Not saved — ${esc(f.result.report.summary)}</b><ul>${fails}</ul></div>` : ""}
      ${f.result && f.result.ok ? `<div class="cv-saved">Saved as v${esc(f.result.version)} · ${esc(f.result.report.summary)} · chart updated</div>` : ""}
      <details class="cv-more"><summary>Validation · ${esc(rep.summary || "")}</summary><ul class="cv-checks">${rows}</ul></details>
      ${srcs ? `<details class="cv-more"><summary>Sources</summary><ul class="cv-srcs">${srcs}</ul></details>` : ""}`;

    if (f.editing) {
      const input = view.querySelector(".cv-input");
      const gutter = view.querySelector(".cv-gutter");
      const paintGutter = (text) => {
        const n = text.split("\n").length;
        gutter.innerHTML = Array.from({ length: n }, (_, i) => `<span>${i + 1}</span>`).join("");
      };
      const jar = CodeJar(input, (el) => { el.innerHTML = highlight(hljs, el.textContent); },
        { tab: "    ", addClosing: true });
      jar.updateCode(code);
      paintGutter(code);
      jar.onUpdate((text) => {
        paintGutter(text);
        const dirty = text.replace(/\s+$/, "") !== String(d.code || "").replace(/\s+$/, "");
        if (dirty !== !!f.dirty) { f.dirty = dirty; renderTabs(); }
        f.draft = text;
      });
      f.jar = jar;
      input.focus();
    }
  }

  async function onAction(e) {
    const b = e.target.closest("[data-cv]");
    if (!b) return;
    const f = files.find((x) => x.id === current);
    if (!f || !f.rec) return;
    const what = b.dataset.cv;
    if (what === "copy") {
      try {
        await navigator.clipboard.writeText(f.rec.code || "");
        b.classList.add("is-done");
        setTimeout(() => b.classList.remove("is-done"), 1200);
      } catch { }
      return;
    }
    if (what === "edit") {
      f.editing = true; f.result = null; f.draft = null;
      render();
      return;
    }
    if (what === "cancel") {
      f.editing = false; f.draft = null; f.dirty = false; f.result = null;
      renderTabs(); render();
      return;
    }
    if (what === "save") {
      const code = f.jar ? f.jar.toString() : f.draft;
      f.saving = true; f.draft = code; render();
      let res;
      try {
        const r = await fetch(`${API}/custom_indicators/${encodeURIComponent(f.id)}/code`, {
          method: "POST", headers: headers(true),
          // the chart the user is looking at is what the edit is tested on first
          body: JSON.stringify({ code, ...(typeof window.__chartoChart === "function" ? window.__chartoChart() : {}) }),
        });
        res = await r.json();
        if (!r.ok) throw new Error(res.error || `HTTP ${r.status}`);
      } catch (err) {
        res = { ok: false, report: { summary: err.message, checks: [] } };
      }
      f.saving = false; f.result = res;
      if (res.ok) {
        f.editing = false; f.dirty = false; f.draft = null;
        f.rec = { ...f.rec, code, version: res.version, report: res.report };
        document.dispatchEvent(new CustomEvent("charto:custom-indicator",
          { detail: { action: "updated", id: f.id, def: res.def } }));
      }
      renderTabs(); render();
      return;
    }
    if (what === "delete") {
      // two presses rather than a browser confirm(), which would block the page
      if (!delArmed) { delArmed = true; render(); return; }
      const r = await fetch(`${API}/custom_indicators/${encodeURIComponent(f.id)}/delete`,
        { method: "POST", headers: headers() });
      if (!r.ok) { delArmed = false; render(); return; }
      document.dispatchEvent(new CustomEvent("charto:custom-indicator",
        { detail: { action: "deleted", id: f.id } }));
      close(f.id);
    }
  }

  return { open, show, close };
})();
