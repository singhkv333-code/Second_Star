/* Charto preview — which model answers the chat: Pivot's, or one the user
 * brings.
 *
 * The server (data/byok.py) holds every key, encrypted, and never sends one
 * back; this file only ever sees a provider, the last four characters and the
 * model list the key was checked against. The choice of model is per browser
 * (localStorage) because it is a preference, not data: the key it points at
 * lives on the account, and a choice that names a provider the account no
 * longer has falls back to Pivot on its own.
 *
 * Three surfaces:
 *   the chip    in the composer row — what will answer the next message
 *   the menu    above the chip — switch between connected models
 *   the dialog  "Your models" — connect, check, choose a model, remove
 */
"use strict";

const Models = (() => {
  const API = location.port === "5173"
    ? "http://127.0.0.1:5174" : "";
  const PICK = "charto:engine";
  const CARET = '<svg class="mk-caret" viewBox="0 0 10 10" aria-hidden="true"><path d="M2.6 3.9 5 6.3l2.4-2.4"/></svg>';
  const ORDER = ["openai", "anthropic", "google", "openrouter"];
  const BLURB = {
    openai: "GPT models on your OpenAI API key.",
    anthropic: "Claude models on your Anthropic API key.",
    google: "Gemini models on your Google AI Studio key.",
    openrouter: "Hundreds of models through one OpenRouter key.",
    chatgpt: "Your ChatGPT Plus or Pro plan, signed in with OpenAI.",
  };

  let st = null;            // GET /byok, or null when signed out / not loaded
  let pick = null;          // { provider, model } — null means Pivot
  try { pick = JSON.parse(localStorage.getItem(PICK) || "null"); } catch { pick = null; }

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const icon = (n, sz) => (typeof Icons !== "undefined" ? Icons.svg(n, sz || "xs") : "");
  const logo = (id, cls) => `<span class="mk-logo ${cls || ""}" data-p="${id}">${ModelLogos.html(id)}</span>`;
  const toast = (m) => (typeof Layouts !== "undefined" && Layouts.toast ? Layouts.toast(m) : null);

  async function call(path, body) {
    const res = await fetch(API + path, {
      method: body === undefined ? "GET" : "POST",
      headers: typeof Auth !== "undefined"
        ? Auth.headers(body === undefined ? {} : { "Content-Type": "application/json" })
        : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch {}
    if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
    return data;
  }

  const prov = (id) => (st && st.providers || []).find((p) => p.id === id) || null;
  const signedIn = () => typeof Auth !== "undefined" && !!Auth.user;
  const mode = () => (document.getElementById("chatPanel") || {}).dataset?.chatMode || "chat";

  function setPick(p) {
    pick = p && p.provider !== "pivot" ? p : null;
    try { pick ? localStorage.setItem(PICK, JSON.stringify(pick)) : localStorage.removeItem(PICK); } catch {}
    paintChip();
  }

  /** What the next message is sent with, or null for Pivot. Execution mode is
   *  always Pivot: strategy building runs on Pivot's own model. */
  function engine() {
    if (!pick || mode() === "execution") return null;
    const p = prov(pick.provider);
    if (!p || !p.connected) return null;
    const model = (p.models || []).some((m) => m.id === pick.model) ? pick.model : p.model;
    return model ? { provider: p.id, model } : null;
  }

  function modelLabel(p, id) {
    const m = (p.models || []).find((x) => x.id === id);
    let s = (m && m.label) || id || "";
    // OpenRouter names carry their maker ("Anthropic: Claude …") — the logo says it
    return s.replace(/^[A-Za-z0-9 .]+:\s*/, "");
  }

  async function load() {
    if (!signedIn()) { st = null; paintChip(); return; }
    try { st = await call("/byok"); } catch { st = null; }
    paintChip();
  }

  /* ── the chip ──────────────────────────────────────────────────────── */
  let chip = null;
  function mountChip() {
    const row = document.querySelector("#chatForm .composer-row");
    if (!row || chip) return;
    chip = document.createElement("div");
    chip.className = "menu-wrap mk-chip-wrap";
    chip.innerHTML = '<button type="button" class="mk-chip" id="engineChip" aria-haspopup="menu" '
      + 'aria-expanded="false"></button><div class="dropdown up mk-menu" id="engineMenu" role="menu"></div>';
    const flag = row.querySelector("#ctxFlag");
    flag ? flag.after(chip) : row.prepend(chip);
    chip.querySelector(".mk-chip").addEventListener("click", (e) => {
      e.stopPropagation();
      const menu = chip.querySelector(".mk-menu");
      if (menu.classList.contains("open")) return closeMenu();
      paintMenu();
      menu.classList.add("open");
      e.currentTarget.setAttribute("aria-expanded", "true");
    });
    document.addEventListener("click", (e) => { if (!chip.contains(e.target)) closeMenu(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
    const panel = document.getElementById("chatPanel");
    if (panel && window.MutationObserver) {
      new MutationObserver(paintChip).observe(panel, { attributes: true, attributeFilter: ["data-chat-mode"] });
    }
    paintChip();
  }

  function closeMenu() {
    if (!chip) return;
    chip.querySelector(".mk-menu").classList.remove("open");
    chip.querySelector(".mk-chip").setAttribute("aria-expanded", "false");
  }

  function paintChip() {
    if (!chip) return;
    const b = chip.querySelector(".mk-chip");
    const e = engine();
    const p = e && prov(e.provider);
    const exec = mode() === "execution";
    b.classList.toggle("own", !!e);
    b.innerHTML = e
      ? `${logo(e.provider)}<span class="mk-chip-t">${esc(modelLabel(p, e.model))}</span>${CARET}`
      : `${logo("pivot")}<span class="mk-chip-t">Pivot</span>${CARET}`;
    b.title = e ? `Answering with ${modelLabel(p, e.model)} on your ${p.name}${p.kind === "oauth" ? " plan" : " key"}`
      : exec && pick ? "Strategy building runs on Pivot's model" : "Answering with Pivot's model";
  }

  function paintMenu() {
    const menu = chip.querySelector(".mk-menu");
    const e = engine();
    const exec = mode() === "execution";
    const rows = [];
    rows.push('<div class="head">Model</div>');
    rows.push(item("pivot", "", "Pivot", "Included · uses Pivot credits", !e));
    const conn = (st && st.providers || []).filter((p) => p.connected && p.model);
    for (const p of conn) {
      const own = e && e.provider === p.id;
      rows.push(item(p.id, own ? e.model : p.model, modelLabel(p, own ? e.model : p.model),
        `${p.name} · ${p.kind === "oauth" ? esc(p.hint) : `key ••${esc(p.hint)}`}`, own, exec));
    }
    if (exec && conn.length) {
      rows.push('<div class="mk-note">Strategy building runs on Pivot\'s model. Your models answer in Research.</div>');
    }
    rows.push('<div class="sep"></div>');
    rows.push(`<div class="item" data-mk="manage" role="menuitem"><span class="lead">${icon("settings")}`
      + `<span>${conn.length ? "Manage your models" : "Connect your own model"}</span></span></div>`);
    menu.innerHTML = rows.join("");
    menu.onclick = (ev) => {
      const it = ev.target.closest("[data-mk]");
      if (!it || it.getAttribute("aria-disabled") === "true") return;
      closeMenu();
      if (it.dataset.mk === "manage") return open();
      setPick(it.dataset.mk === "pivot" ? null : { provider: it.dataset.mk, model: it.dataset.model });
    };
  }

  function item(id, model, title, sub, on, off) {
    return `<div class="item mk-item${on ? " on" : ""}" role="menuitemradio" aria-checked="${!!on}"`
      + ` data-mk="${id}" data-model="${esc(model)}"${off ? ' aria-disabled="true"' : ""}>`
      + `<span class="lead">${logo(id)}<span class="mk-item-t"><span>${esc(title)}</span><small>${sub}</small></span></span>`
      + `${on ? icon("check") : ""}</div>`;
  }

  /* ── the dialog ────────────────────────────────────────────────────── */
  let dlgOpen = null;

  async function open(focus) {
    if (dlgOpen) dlgOpen.close();
    if (signedIn() && !st) await load();
    const back = document.createElement("div");
    back.className = "ly-back mk-back";
    back.innerHTML = '<div class="ly-dlg mk-dlg" role="dialog" aria-modal="true" aria-labelledby="mkTitle"></div>';
    document.body.appendChild(back);
    const dlg = back.firstElementChild;
    const onKey = (e) => { if (e.key === "Escape") close(); };
    const onMsg = (e) => {
      if (e.origin !== location.origin && !(API && e.origin === new URL(API).origin)) return;
      if (!e.data || e.data.type !== "pivot-byok") return;
      if (e.data.ok) {
        load().then(() => { sel = "chatgpt"; paint(); });
        setPick({ provider: "chatgpt", model: "" });
        toast("ChatGPT is connected");
      } else {
        msg = { bad: true, text: e.data.error || "ChatGPT sign-in did not complete." };
        paint();
      }
    };
    const close = () => {
      back.remove();
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("message", onMsg);
      dlgOpen = null;
    };
    back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
    document.addEventListener("keydown", onKey);
    window.addEventListener("message", onMsg);
    dlgOpen = { close };

    let sel = focus || (engine() && engine().provider) || "openai";
    let msg = null;          // { bad, text } under the form
    let busy = false;
    let editing = false;     // replacing a connected key

    function rail() {
      const r = (id, name, status, on, conn) =>
        `<button type="button" class="mk-prov${on ? " on" : ""}" data-sel="${id}" role="tab" aria-selected="${on}">`
        + `${logo(id, "lg")}<span class="mk-prov-t"><span>${esc(name)}</span>`
        + `<small class="${conn ? "ok" : ""}">${conn ? '<i class="mk-dot"></i>' : ""}${status}</small></span></button>`;
      const rows = [];
      for (const id of ORDER) {
        const p = prov(id) || { name: { openai: "OpenAI", anthropic: "Anthropic", google: "Google Gemini", openrouter: "OpenRouter" }[id] };
        rows.push(r(id, p.name, p.connected ? `Key ••${esc(p.hint)}` : "Not connected", sel === id, p.connected));
      }
      const g = prov("chatgpt");
      return `<nav class="mk-rail" role="tablist" aria-label="Providers">
          ${r("pivot", "Pivot", "Included", sel === "pivot", false)}
          <div class="mk-kick">Your API keys</div>${rows.join("")}
          <div class="mk-kick">Plans</div>
          ${r("chatgpt", "ChatGPT", g && g.connected ? esc(g.hint) : (st && st.chatgpt && st.chatgpt.ready ? "Not connected" : "Not enabled yet"), sel === "chatgpt", g && g.connected)}
        </nav>`;
    }

    function wall() {
      return `<div class="mk-wall">
          <div><h4>Your model receives</h4><ul>
            <li>${icon("check")}Your messages and any screenshot you attach</li>
            <li>${icon("check")}The chart you have in context, as numbers</li>
            <li>${icon("check")}Results of the Pivot tools it asks for</li></ul></div>
          <div><h4>It never receives</h4><ul class="no">
            <li>${icon("x")}Pivot's databases, servers or keys</li>
            <li>${icon("x")}Anyone else's data, or your email and password</li>
            <li>${icon("x")}A way to place orders. It can draw, read and alert.</li></ul></div>
        </div>`;
    }

    function modelField(p) {
      const ms = p.models || [];
      const cur = engine() && engine().provider === p.id ? engine().model : p.model;
      if (!ms.length) {
        return `<label class="ly-lab" for="mkModel">Model</label>
          <input class="textfield mk-mono" id="mkModel" value="${esc(cur)}" placeholder="model id" spellcheck="false" autocomplete="off">`;
      }
      if (ms.length > 40) {
        return `<label class="ly-lab" for="mkModel">Model <span class="mk-count">${ms.length} with tool use</span></label>
          <input class="textfield mk-mono" id="mkModel" list="mkModels" value="${esc(cur)}" spellcheck="false" autocomplete="off">
          <datalist id="mkModels">${ms.map((m) => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join("")}</datalist>`;
      }
      return `<label class="ly-lab" for="mkModel">Model</label>
        <select class="textfield" id="mkModel">${ms.map((m) =>
          `<option value="${esc(m.id)}"${m.id === cur ? " selected" : ""}>${esc(m.label)}</option>`).join("")}</select>`;
    }

    function pane() {
      if (!signedIn()) {
        return `<section class="mk-pane"><div class="mk-empty">${logo("pivot", "xl")}
            <h3 id="mkTitle">Bring your own model</h3>
            <p class="sx-lede">Sign in, then connect an OpenAI, Anthropic, Gemini or OpenRouter key, or your ChatGPT plan.
               Pivot's tools and data come with it.</p>
            <button class="btn cta" data-act="signin">Sign in</button></div></section>`;
      }
      if (st && st.available === false) {
        return `<section class="mk-pane"><h3 id="mkTitle">Your models</h3>
          <p class="sx-lede">This server cannot store keys securely right now, so connecting one is switched off.</p></section>`;
      }
      if (sel === "pivot") {
        const on = !engine();
        return `<section class="mk-pane">
          <header class="mk-head">${logo("pivot", "xl")}<div><h3 id="mkTitle">Pivot</h3>
            <p>The model Pivot is tuned on. Included with your plan.</p></div></header>
          <p class="sx-lede">Every turn uses one Pivot AI credit. It is the only model that builds strategies,
             browses news for a move, and writes custom indicators.</p>
          ${wall()}
          <div class="mk-actions"><span></span>
            <button class="btn ${on ? "" : "cta"}" data-act="use-pivot" ${on ? "disabled" : ""}>${on ? "In use" : "Use Pivot"}</button></div>
        </section>`;
      }
      if (sel === "chatgpt") return chatgptPane();
      const p = prov(sel);
      if (!p) return "";
      const connected = p.connected && !editing;
      const inUse = engine() && engine().provider === p.id;
      const when = p.used ? `last answered ${ago(p.used)}` : `checked ${ago(p.verified)}`;
      return `<section class="mk-pane">
        <header class="mk-head">${logo(p.id, "xl")}<div><h3 id="mkTitle">${esc(p.name)}</h3>
          <p>${esc(BLURB[p.id] || "")} Billed by ${esc(p.name)}, not Pivot.</p></div></header>
        ${connected ? `
          <label class="ly-lab">API key</label>
          <div class="mk-key-on">${icon("lock")}<span class="mk-mono">••••••••••••${esc(p.hint)}</span>
            <small>${esc(when)}${p.turns ? ` · ${p.turns} turns` : ""}</small>
            <button type="button" class="btn" data-act="replace">Replace</button></div>
          ${modelField(p)}` : `
          <label class="ly-lab" for="mkKey">API key
            <a class="mk-get" href="${esc(p.console)}" target="_blank" rel="noopener noreferrer">Get a key ${icon("link")}</a></label>
          <div class="mk-key">
            <input class="textfield mk-mono" id="mkKey" type="password" placeholder="${esc(p.example)}"
                   autocomplete="off" autocapitalize="off" spellcheck="false" data-1p-ignore data-lpignore="true">
            <button type="button" class="mk-eye" data-act="peek" title="Show key" aria-label="Show key">${icon("eye")}</button>
          </div>
          <p class="mk-help">Pivot checks it with ${esc(p.name)}, then stores it encrypted. It is never shown again.</p>`}
        <div class="mk-msg${msg ? (msg.bad ? " bad" : " good") : ""}" role="status">${msg ? esc(msg.text) : ""}</div>
        ${wall()}
        <div class="mk-actions">
          ${p.connected ? `<button class="btn mk-remove" data-act="remove">Remove key</button>` : "<span></span>"}
          <div class="ly-actions">
            ${editing ? `<button class="btn" data-act="cancel-edit">Cancel</button>` : ""}
            ${connected
              ? `<button class="btn ${inUse ? "" : "cta"}" data-act="use">${inUse ? "Save model" : "Use in chat"}</button>`
              : `<button class="btn cta" data-act="save" ${busy ? "disabled" : ""}>${busy ? '<span class="mk-spin"></span>Checking' : "Check and save"}</button>`}
          </div>
        </div>
      </section>`;
    }

    function chatgptPane() {
      const g = prov("chatgpt") || {};
      const ready = st && st.chatgpt && st.chatgpt.ready;
      const inUse = engine() && engine().provider === "chatgpt";
      return `<section class="mk-pane">
        <header class="mk-head">${logo("chatgpt", "xl")}<div><h3 id="mkTitle">ChatGPT</h3>
          <p>${esc(BLURB.chatgpt)}</p></div></header>
        <p class="sx-lede">Answers count against your ChatGPT plan's limits, not Pivot credits. Pivot never sees
           your ChatGPT conversations, and OpenAI never sees your Pivot account.</p>
        ${g.connected ? `
          <div class="mk-key-on">${logo("chatgpt")}<span>${esc(g.hint)}</span>
            <small>connected ${esc(ago(g.verified))}</small></div>
          ${modelField(g)}` : `
          <button type="button" class="mk-siwc" data-act="chatgpt" ${ready ? "" : "disabled"}>
            ${ModelLogos.html("chatgpt")}<span>Continue with ChatGPT</span></button>
          ${ready ? "" : `<p class="mk-help">Not enabled on this server yet. OpenAI admits hosted apps to
             Sign in with ChatGPT by application. Until then, an OpenAI API key works the same way.</p>`}`}
        <div class="mk-msg${msg ? (msg.bad ? " bad" : " good") : ""}" role="status">${msg ? esc(msg.text) : ""}</div>
        ${wall()}
        <div class="mk-actions">
          ${g.connected ? `<button class="btn mk-remove" data-act="remove">Disconnect</button>` : "<span></span>"}
          ${g.connected ? `<button class="btn ${inUse ? "" : "cta"}" data-act="use">${inUse ? "Save model" : "Use in chat"}</button>` : ""}
        </div>
      </section>`;
    }

    function paint() {
      dlg.innerHTML = `<div class="mk">${rail()}${pane()}</div>
        <div class="sx-foot mk-foot">
          <span class="su-privacy">${icon("lock")} Keys are encrypted on Pivot's server, sent only to their own provider, and never shown again.</span>
          <button class="btn" data-act="close">Done</button>
        </div>`;
      const k = dlg.querySelector("#mkKey");
      if (k) k.focus();
    }

    dlg.addEventListener("click", async (e) => {
      const s = e.target.closest("[data-sel]");
      if (s) { sel = s.dataset.sel; msg = null; editing = false; return paint(); }
      const a = e.target.closest("[data-act]");
      if (!a || a.disabled) return;
      const act = a.dataset.act;
      if (act === "close") return close();
      if (act === "signin") { close(); if (window.CHARTO_AUTH_OPEN) window.CHARTO_AUTH_OPEN(); return; }
      if (act === "peek") {
        const k = dlg.querySelector("#mkKey");
        k.type = k.type === "password" ? "text" : "password";
        a.classList.toggle("on", k.type === "text");
        return;
      }
      if (act === "replace") { editing = true; msg = null; return paint(); }
      if (act === "cancel-edit") { editing = false; msg = null; return paint(); }
      if (act === "use-pivot") { setPick(null); msg = null; toast("Chat answers with Pivot"); return paint(); }
      if (act === "save") return save();
      if (act === "use") {
        const m = dlg.querySelector("#mkModel");
        const model = m ? m.value.trim() : "";
        try {
          st = await call("/byok/model", { provider: sel, model });
          setPick({ provider: sel, model });
          msg = { bad: false, text: `Chat now answers with ${modelLabel(prov(sel), model)}.` };
        } catch (err) { msg = { bad: true, text: err.message }; }
        return paint();
      }
      if (act === "remove") {
        if (!a.classList.contains("confirm")) {
          a.classList.add("confirm");
          a.textContent = sel === "chatgpt" ? "Disconnect ChatGPT?" : "Remove this key?";
          setTimeout(() => { if (a.isConnected) { a.classList.remove("confirm"); paint(); } }, 3500);
          return;
        }
        try {
          st = await call("/byok/remove", { provider: sel });
          if (pick && pick.provider === sel) setPick(null);
          editing = false;
          msg = { bad: false, text: sel === "chatgpt" ? "ChatGPT is disconnected." : "The key is deleted from Pivot." };
        } catch (err) { msg = { bad: true, text: err.message }; }
        paintChip();
        return paint();
      }
      if (act === "chatgpt") {
        // The popup has to open inside the click, or it is blocked; the URL
        // arrives a moment later.
        const w = window.open("about:blank", "pivot-chatgpt", "width=520,height=680");
        try {
          const { url } = await call("/byok/chatgpt/start");
          if (w) w.location = url; else location.href = url;
        } catch (err) {
          if (w) w.close();
          msg = { bad: true, text: err.message };
          paint();
        }
      }
    });
    dlg.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.id === "mkKey") { e.preventDefault(); save(); }
    });

    async function save() {
      const k = dlg.querySelector("#mkKey");
      const key = k ? k.value.trim() : "";
      if (!key) { msg = { bad: true, text: "Paste the key first." }; return paint(); }
      busy = true; msg = null; paint();
      try {
        st = await call("/byok/key", { provider: sel, key });
        const p = prov(sel);
        setPick({ provider: sel, model: p.model });
        editing = false;
        msg = { bad: false, text: `Connected. ${p.models.length} model${p.models.length === 1 ? "" : "s"} available; chat now answers with ${modelLabel(p, p.model)}.` };
      } catch (err) {
        msg = { bad: true, text: err.message };
      } finally {
        busy = false;
      }
      paint();
    }

    paint();
  }

  function ago(ts) {
    if (!ts) return "";
    const s = Date.now() / 1000 - ts;
    if (s < 90) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  }

  function boot() {
    mountChip();
    if (typeof Auth !== "undefined" && Auth.onChange) Auth.onChange(() => load());
    else load();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  return { engine, open, load, refresh: paintChip };
})();
