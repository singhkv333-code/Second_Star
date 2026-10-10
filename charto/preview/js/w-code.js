/* Charto preview — the Code widget: your custom indicators, as code.
 *
 * The same studies the chat builds (custom_indicator) and the sidebar's code
 * view opens, edited in a proper editor surface (CodeJar + highlight.js,
 * vendor/codeedit). Save sends the code to the server's validator — the same
 * one a build runs, on the bars of the chart you are looking at — and only a
 * pass becomes a new version; a failed save says why and leaves the chart on
 * the old one. "On chart" adds or removes the study from the main chart.
 *
 * The code runs only in the server's sandbox, never in this page. A new
 * study is born in the chat (it needs a specification as well as code), so
 * "New" drafts that request for you.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const { API, esc, ic, empty, skel } = WKit;
  const hdrs = (json) => ({ ...(typeof Auth !== "undefined" ? Auth.headers() : {}),
                            ...(json ? { "Content-Type": "application/json" } : {}) });
  let libs = null;
  const loadLibs = () => libs || (libs = Promise.all([
    import("../vendor/codeedit/hljs-core.js"), import("../vendor/codeedit/hljs-python.js"),
    import("../vendor/codeedit/codejar.js"),
  ]).then(([core, py, jar]) => { core.default.registerLanguage("python", py.default); return { hljs: core.default, CodeJar: jar.CodeJar }; }));

  const CTL = /<span class="hljs-keyword">(if|elif|else|for|while|return|break|continue|import|from|try|except|finally|raise|with|yield|pass|as|match|case)<\/span>/g;

  function mount(host, ctx) {
    let list = [], cur = null, jar = null, dirty = false, busy = false, lines = () => {};
    let rw = 0;
    const ro = new ResizeObserver(() => { cancelAnimationFrame(rw); rw = requestAnimationFrame(() => { if (ctx.cfg.wrap) lines(); }); });
    host.innerHTML =
      `<div class="cd-bar">` +
        `<button type="button" class="side-pick cd-pick" data-cd="pick" title="Open a study"></button>` +
        `<span class="sh-gap"></span>` +
        `<span class="cd-state"></span>` +
        `<button type="button" class="sh-btn" data-cd="chart" title="Add to the chart">${ic("lineChart")}<span>Add to chart</span></button>` +
        `<button type="button" class="sh-btn cd-save" data-cd="save" title="Validate and save (Ctrl S)">${ic("save")}<span>Save</span></button>` +
        `<button type="button" class="sh-btn i" data-cd="new" title="New study (in chat)">${ic("plus")}</button>` +
      `</div>` +
      `<div class="cd-body"></div>` +
      `<div class="cd-report" hidden></div>`;
    const $ = (s) => host.querySelector(s);
    const body = $(".cd-body"), report = $(".cd-report");

    async function loadList() {
      cur = null; paintState();
      if (!Auth.user) {
        body.innerHTML = empty("code", "Custom indicators belong to your account. Sign in to edit yours here.", "Sign in", 'data-cd="signin"');
        return;
      }
      body.innerHTML = skel(10);
      try {
        const r = await fetch(`${API}/custom_indicators`, { headers: hdrs() });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
        list = d.custom_indicators || [];
      } catch (e) { body.innerHTML = empty("code", esc(e.message)); return; }
      if (!list.length) {
        body.innerHTML = empty("code", "No custom indicators yet. Describe one in the chat and its code opens here.", "Build one in chat", 'data-cd="new"');
        $(".cd-pick").textContent = "No studies";
        return;
      }
      const want = ctx.cfg.cid && list.find((x) => x.id === ctx.cfg.cid) ? ctx.cfg.cid : list[0].id;
      openStudy(want);
    }

    async function openStudy(id) {
      if (dirty && cur && !confirmDiscard()) return;
      body.innerHTML = skel(10);
      report.hidden = true;
      try {
        const [r, { hljs, CodeJar }] = await Promise.all([
          fetch(`${API}/custom_indicators/${encodeURIComponent(id)}`, { headers: hdrs() }).then((x) => x.json()), loadLibs()]);
        if (r.error) throw new Error(r.error);
        cur = r; dirty = false;
        ctx.setCfg({ cid: id });
        ctx.setTitle(r.spec && (r.spec.short || r.spec.title) || id);
        $(".cd-pick").innerHTML = `${esc(r.spec && r.spec.title || id)}<em>v${r.version}</em>${ic("chevronDown", "")}`;
        body.innerHTML = `<div class="cd-ed"><div class="cd-gutter" aria-hidden="true"></div><code class="cd-in hljs" spellcheck="false"></code>` +
          `<div class="cd-mirror" aria-hidden="true"></div></div>`;
        const input = body.querySelector(".cd-in"), gutter = body.querySelector(".cd-gutter"), mirror = body.querySelector(".cd-mirror");
        // as VS Code does, flow-control keywords take their own colour
        jar = CodeJar(input, (el) => { el.innerHTML = hljs.highlight(el.textContent, { language: "python", ignoreIllegals: true }).value.replace(CTL, '<span class="hljs-keyword cd-k-ctl">$1</span>'); },
                      { tab: " ".repeat(Number(ctx.cfg.tab) || 4), indentOn: /:$/ });
        jar.updateCode(r.code || "");
        /* One number per line of code. Wrapped, a line of code can take two
         * rows on screen, so each number is given the height its line really
         * takes — measured on a hidden copy laid out at the editor's width. */
        lines = () => {
          const src = jar.toString().split("\n");
          let hs = null;
          if (ctx.cfg.wrap) {
            mirror.style.width = input.clientWidth + "px";
            mirror.innerHTML = src.map((l) => `<div>${esc(l) || " "}</div>`).join("");
            hs = [...mirror.children].map((d) => d.getBoundingClientRect().height);
            mirror.textContent = "";
          }
          gutter.innerHTML = src.map((_, i) => `<span${hs ? ` style="height:${hs[i]}px"` : ""}>${i + 1}</span>`).join("");
        };
        lines();
        ro.disconnect(); ro.observe(input);
        jar.onUpdate(() => { dirty = jar.toString() !== cur.code; paintState(); lines(); });
        input.addEventListener("keydown", (e) => {
          e.stopPropagation();
          if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); }
        });
        paintState();
      } catch (e) { body.innerHTML = empty("code", `That study could not be opened: ${esc(e.message)}`); }
    }

    const confirmDiscard = () => { ctx.toast("Save or undo your edits first — they would be lost."); return false; };
    function paintState() {
      const on = cur && typeof window.__chartoCustomActive === "function" && window.__chartoCustomActive(cur.id);
      const cb = $('[data-cd="chart"]');
      cb.classList.toggle("on", !!on);
      cb.title = on ? "Remove from the chart" : "Add to the chart";
      cb.innerHTML = `${ic(on ? "check" : "lineChart")}<span>${on ? "On chart" : "Add to chart"}</span>`;
      $('[data-cd="chart"]').hidden = !cur || cur.status !== "validated";
      $(".cd-save").hidden = !cur;
      $(".cd-pick").hidden = !list.length;
      $(".cd-state").textContent = busy ? "Validating…" : dirty ? "Edited" : cur ? (cur.status === "validated" ? "Validated" : "Not validated") : "";
      $(".cd-save").disabled = !dirty || busy;
    }

    async function save() {
      if (!cur || !dirty || busy) return;
      busy = true; paintState();
      const code = jar.toString();
      let res;
      try {
        const r = await fetch(`${API}/custom_indicators/${encodeURIComponent(cur.id)}/code`, {
          method: "POST", headers: hdrs(true),
          body: JSON.stringify({ code, ...(typeof window.__chartoChart === "function" ? window.__chartoChart() : {}) }),
        });
        res = await r.json();
        if (!r.ok) throw new Error(res.error || `HTTP ${r.status}`);
      } catch (e) { res = { ok: false, report: { summary: e.message } }; }
      busy = false;
      const rep = res.report || {};
      report.hidden = false;
      report.className = "cd-report " + (res.ok ? "ok" : "bad");
      report.innerHTML = `<b>${res.ok ? `Saved as version ${res.version}` : "Not saved — the validator refused it"}</b>` +
        `<span>${esc(rep.summary || "")}</span>` +
        ((rep.checks || []).filter((c) => !c.passed).slice(0, 4).map((c) => `<em>${esc(c.name || "")}: ${esc(c.detail || c.message || "")}</em>`).join(""));
      if (res.ok) {
        cur = { ...cur, code, version: res.version, status: "validated" };
        dirty = false;
        $(".cd-pick").innerHTML = `${esc(cur.spec && cur.spec.title || cur.id)}<em>v${cur.version}</em>${ic("chevronDown", "")}`;
        document.dispatchEvent(new CustomEvent("charto:custom-indicator", { detail: { action: "updated", id: cur.id, def: res.def } }));
      }
      paintState();
    }

    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-cd]");
      if (!b) return;
      e.stopPropagation();
      const a = b.dataset.cd;
      if (a === "signin") { const s = document.getElementById("authBtn") || document.getElementById("acctBtn"); return s && s.click(); }
      if (a === "new") return ctx.compose("Build me a custom indicator that ");
      if (a === "save") return save();
      if (a === "chart" && cur) {
        const on = window.__chartoCustomActive && window.__chartoCustomActive(cur.id);
        document.dispatchEvent(new CustomEvent("charto:custom-indicator",
          { detail: { action: on ? "remove" : "add", id: cur.id, def: cur.def } }));
        return setTimeout(paintState, 300);
      }
      if (a === "pick" && list.length) {
        return ctx.menu(b, [{ head: "Your studies" }, ...list.map((x) => ({ id: x.id, label: `${x.title || x.id}`,
          hint: x.status === "validated" ? `v${x.version}` : "failed", on: cur && cur.id === x.id }))], (id) => openStudy(id));
      }
    });
    report.addEventListener("click", () => { report.hidden = true; });
    function prefs() {
      const c = ctx.cfg;
      host.style.setProperty("--cd-size", (c.fontSize || 12.5) + "px");
      host.classList.toggle("cd-wrap", !!c.wrap);
      host.classList.toggle("cd-nonum", c.numbers === false);
      host.dataset.theme = c.theme || "auto";
    }
    prefs();
    if (typeof Auth !== "undefined" && Auth.onChange) Auth.onChange(() => { if (ctx.visible()) loadList(); });

    let booted = false;
    return {
      show() { if (!booted) { booted = true; loadList(); } else paintState(); },
      config(cfg, patch) { prefs(); if ("wrap" in patch || "fontSize" in patch) requestAnimationFrame(() => lines()); if ("tab" in patch && cur && !dirty) openStudy(cur.id); },
      /** The chat opens a study here, puts code in it, or shows the version
       *  it just validated and saved on the server. Unsaved edits the user
       *  made are never overwritten. */
      async write(c) {
        if (dirty && cur && (!c.study || c.study === cur.id) && !c.saved) {
          return { ok: false, error: "the Code widget has unsaved edits by the user" };
        }
        if (c.saved || !list.length) await loadList();
        if (c.study && (!cur || cur.id !== c.study || c.saved)) { dirty = false; await openStudy(c.study); }
        if (c.code && !c.saved && jar) {
          jar.updateCode(String(c.code));
          dirty = cur && jar.toString() !== cur.code;
          paintState(); lines();
        }
        return { ok: true };
      },
      ask: () => cur ? { sub: `${(cur.spec && cur.spec.title) || "Custom indicator"} · Python`,
        context: `My custom indicator "${cur.spec && cur.spec.title}":\n\`\`\`python\n${(jar ? jar.toString() : cur.code).slice(0, 4000)}\n\`\`\``,
        question: "Explain what this computes, line by line, and how to read it on the chart." } : null,
    };
  }

  Dock.register({
    type: "code", title: "Code", icon: "code", hue: "lime", group: "Tools",
    desc: "Edit your custom indicators as code", zone: "right", minW: 340, mount,
    settings: [
      { section: "Editor" },
      { key: "fontSize", label: "Font size", def: 12.5, options: [{ v: 11, label: "11" }, { v: 12.5, label: "12.5" }, { v: 14, label: "14" }, { v: 16, label: "16" }] },
      { key: "tab", label: "Tab inserts", def: 4, options: [{ v: 2, label: "2 spaces" }, { v: 4, label: "4 spaces" }] },
      { key: "numbers", label: "Line numbers", kind: "toggle", def: true },
      { key: "wrap", label: "Wrap long lines", kind: "toggle", def: false },
      { key: "theme", label: "Theme", def: "auto", options: [{ v: "auto", label: "Match the app" }, { v: "dark", label: "Dark+" }, { v: "light", label: "Light+" }],
        hint: "The colours of VS Code's default themes" },
      { kind: "note", label: "Saving runs the code in the server's sandbox on the chart's bars; only a pass becomes a new version." },
    ],
    agent: { writes: {
      study: "the custom indicator id (cx_…) to open",
      code: "full Python source for compute(bars, params) — shown in the editor",
      save: "true: validate in the sandbox and save as a new version (the result comes back to you)",
    } },
  });
})();
