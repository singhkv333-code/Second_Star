/* Charto preview — a custom indicator's code, in the sidebar.
 *
 * The chat prints a small file card when it builds an indicator; Open lands
 * here. The sidebar then carries editor-style tabs above the conversation —
 * "Chat" and the file — and either can be switched to without losing the
 * other: the thread stays mounted underneath, scrolled where it was.
 *
 * Everything shown is the build record the server stored
 * (GET /custom_indicators/<id>): the code exactly as validated, its version,
 * and the validator's checks. Nothing is recomputed here.
 */
"use strict";

const CodeView = (() => {
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const headers = () => (typeof Auth !== "undefined" ? Auth.headers() : {});

  let panel, bar, view, files = [], current = null, delArmed = false;

  const slug = (t) => (String(t || "indicator").toLowerCase()
    .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "indicator") + ".py";

  // ── a deliberately small Python highlighter: comments, strings, numbers,
  // keywords and the ta.* calls — enough to read the code, nothing to parse
  const TOKEN = /(#.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(\b\d+(?:\.\d+)?\b)|(\b(?:def|return|for|in|if|elif|else|while|and|or|not|is|None|True|False|break|continue|lambda|try|except|raise|pass|assert)\b)|(\b(?:ta|math)\.\w+)/g;
  function highlight(line) {
    let out = "", last = 0;
    line.replace(TOKEN, (m, com, str, num, kw, call, at) => {
      out += esc(line.slice(last, at));
      const cls = com ? "c" : str ? "s" : num ? "n" : kw ? "k" : "f";
      out += `<span class="cv-${cls}">${esc(m)}</span>`;
      last = at + m.length;
      return m;
    });
    return out + esc(line.slice(last));
  }

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
    // asking something is a chat act: the answer is in the thread
    const form = document.getElementById("chatForm");
    if (form) form.addEventListener("submit", () => show(null), true);
    const input = document.getElementById("chatInput");
    if (input) input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) show(null);
    }, true);
  }

  function renderTabs() {
    bar.hidden = !files.length;
    bar.innerHTML = `<button type="button" class="cv-tab${current ? "" : " on"}" data-cv-tab="chat"
        role="tab" aria-selected="${!current}">${Icons.svg("chat", "xs")}Chat</button>`
      + files.map((f) => `<button type="button" class="cv-tab${current === f.id ? " on" : ""}"
        data-cv-tab="${esc(f.id)}" role="tab" aria-selected="${current === f.id}"
        title="${esc(f.title)}">${Icons.svg("code", "xs")}<span>${esc(f.name)}</span>
        <i data-cv-close="${esc(f.id)}" title="Close" aria-label="Close">${Icons.svg("x", "xs")}</i></button>`).join("");
  }

  /** null = the conversation; an id = that file. */
  function show(id) {
    mount();
    current = id && files.find((f) => f.id === id) ? id : null;
    delArmed = false;
    const chatEls = [panel.querySelector(".chat-actions"), panel.querySelector(".thread")];
    chatEls.forEach((el) => { if (el) el.hidden = !!current; });
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

  async function open(id) {
    mount();
    if (panel.classList.contains("hidden")) {
      const t = document.getElementById("chatToggle");
      if (t) t.click();
    }
    let f = files.find((x) => x.id === id);
    if (!f) {
      f = { id, name: "loading…", title: "", rec: null, error: "" };
      files.push(f);
    }
    show(id);
    try {
      const r = await fetch(`${API}/custom_indicators/${encodeURIComponent(id)}`, { headers: headers() });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      Object.assign(f, { rec: d, title: d.spec.title, name: slug(d.spec.short || d.spec.title), error: "" });
    } catch (e) {
      f.error = e.message;
      f.name = "unavailable";
    }
    if (current === id) { renderTabs(); render(); }
  }

  function render() {
    const f = files.find((x) => x.id === current);
    if (!f) return;
    if (f.error) { view.innerHTML = `<p class="cv-empty">Could not open this file: ${esc(f.error)}</p>`; return; }
    if (!f.rec) { view.innerHTML = `<p class="cv-empty">Opening…</p>`; return; }
    const d = f.rec, sp = d.spec || {}, rep = d.report || {};
    const checks = rep.checks || [];
    const passed = checks.filter((c) => c.status === "pass").length;
    const kind = { standard: "Standard indicator", variant: "Variant", custom: "Custom method" }[sp.classification] || "Custom";
    const state = d.status === "validated" ? `${passed}/${checks.length} checks` : "Failed validation";
    const lines = String(d.code || "").replace(/\s+$/, "").split("\n");
    const rows = checks.map((c) => `<li class="is-${esc(c.status)}"><b>${esc(c.label)}</b>`
      + (c.status !== "pass" && c.detail ? `<span>${esc(c.detail)}</span>` : "") + `</li>`).join("");
    const srcs = (sp.sources || []).map((s) => `<li><a href="${esc(s.url)}" target="_blank"
      rel="noopener noreferrer">${esc(s.title || s.url)}</a></li>`).join("");
    view.innerHTML = `
      <header class="cv-head">
        <div class="cv-title"><b>${esc(f.name)}</b>
          <span>${esc(sp.title)} · ${esc(kind)} · v${esc(d.version)} ·
            <em class="${d.status === "validated" ? "ok" : "bad"}">${esc(state)}</em></span></div>
        <div class="cv-acts">
          <button type="button" class="chat-action" data-cv="copy" title="Copy code" aria-label="Copy code">${Icons.svg("copy", "sm")}</button>
          <button type="button" class="chat-action" data-cv="edit" title="Edit with AI" aria-label="Edit with AI">${Icons.svg("pen", "sm")}</button>
          <button type="button" class="chat-action cv-danger" data-cv="delete" title="${delArmed ? "Press again to delete" : "Delete"}"
            aria-label="Delete">${delArmed ? "Delete?" : Icons.svg("trash", "sm")}</button>
        </div>
      </header>
      <pre class="cv-code"><code>${lines.map((l) => `<span class="cv-line">${highlight(l) || " "}</span>`).join("")}</code></pre>
      <details class="cv-more"><summary>Validation · ${esc(rep.summary || state)}</summary><ul class="cv-checks">${rows}</ul></details>
      ${srcs ? `<details class="cv-more"><summary>Sources</summary><ul class="cv-srcs">${srcs}</ul></details>` : ""}`;
  }

  async function onAction(e) {
    const b = e.target.closest("[data-cv]");
    if (!b) return;
    const f = files.find((x) => x.id === current);
    if (!f || !f.rec) return;
    const what = b.dataset.cv;
    if (what === "copy") {
      try { await navigator.clipboard.writeText(f.rec.code || ""); b.classList.add("is-done"); setTimeout(() => b.classList.remove("is-done"), 1200); } catch { }
      return;
    }
    if (what === "edit") {
      show(null);
      if (window.Chat) Chat.compose(`Edit my custom indicator "${f.title}" (${f.id}): `);
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
