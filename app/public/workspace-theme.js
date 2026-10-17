/* Shared workspace colors for the app shell and chart canvas. No dependencies.
 * Canonical source: chart/js/workspace-theme.js. Mirror in both Next public folders.
 * Nord-inspired hues: github.com/nordtheme/nord; glass: Apple Materials HIG.
 */
(function (global) {
  "use strict";
  const KEY = "pivot-workspace-colors-v1";
  const HEX = /^#[0-9a-f]{6}$/i;
  const fields = ["surface", "chrome", "border", "accent", "up", "down"];
  const finishes = ["flat", "wash", "grain"];
  const presets = [
    ["ivory", "Ivory", "#ffffff", "#f3f3f4", "#e2e2e5", "#089981", "#089981", "#f23645"],
    ["charcoal", "Charcoal", "#0d0e12", "#151516", "#29292c", "#8bb8b0", "#089981", "#f23645"],
    ["nord", "Nord", "#2e3440", "#232933", "#485364", "#88c0d0", "#a3be8c", "#e1838b"],
    ["midnight", "Midnight", "#141d2e", "#0c1322", "#2b3b54", "#91b3f2", "#57c4ad", "#f17d91"],
    ["evergreen", "Evergreen", "#182824", "#101c19", "#354c44", "#a9c8ac", "#70c6a3", "#e99b8a"],
    ["dune", "Dune", "#faf7ef", "#eae3d5", "#d4cbb9", "#8a633c", "#357c66", "#c45145"],
    ["mist", "Mist", "#f1f6f7", "#dce7ea", "#bdcfd4", "#356e88", "#227f72", "#c74e66"],
    ["plum", "Plum", "#282230", "#1b1722", "#4b4059", "#c6aedb", "#94cbb2", "#ed91a5"],
  ].map(([id, name, surface, chrome, border, accent, up, down]) => ({id, name, surface, chrome, border, accent, up, down, finish: "flat"}));
  const doc = global.document;
  const target = doc?.currentScript?.dataset.surface === "chart" ? "chart" : "shell";
  let state;
  let panel = null;
  let closePanel = null;
  const rgb = color => color.slice(1).match(/../g).map(v => parseInt(v, 16));
  const mix = (a, b, w) => "#" + rgb(a).map((v, i) => Math.round(v * (1-w) + rgb(b)[i] * w).toString(16).padStart(2, "0")).join("");
  const rgba = (c, a) => `rgba(${rgb(c).join(",")},${a})`;
  function luminance(c) {
    const v = rgb(c).map(n => { const s = n/255; return s <= .04045 ? s/12.92 : ((s+.055)/1.055)**2.4; });
    return .2126*v[0]+.7152*v[1]+.0722*v[2];
  }
  const contrast = (a,b) => (Math.max(luminance(a),luminance(b))+.05)/(Math.min(luminance(a),luminance(b))+.05);
  const ink = c => contrast(c,"#17191e") >= 4.5 ? "#17191e" : contrast(c,"#f5f5f6") >= 4.5 ? "#f5f5f6" : luminance(c) > .179 ? "#000000" : "#ffffff";
  // CSS HSL triplets are needed by shadcn; the chart consumes ordinary CSS colors.
  function hsl(c) {
    const [r,g,b] = rgb(c).map(v => v/255), max = Math.max(r,g,b), min = Math.min(r,g,b), d = max-min;
    let h = 0; const l = (max+min)/2, s = d === 0 ? 0 : d/(1-Math.abs(2*l-1));
    if (d) { h = max===r ? ((g-b)/d)%6 : max===g ? (b-r)/d+2 : (r-g)/d+4; h = (h*60+360)%360; }
    return `${h.toFixed(1)} ${(s*100).toFixed(1)}% ${(l*100).toFixed(1)}%`;
  }
  function validate(value) {
    if (!value || typeof value !== "object" || !fields.every(k => typeof value[k] === "string" && HEX.test(value[k]))) return null;
    if (!finishes.includes(value.finish)) return null;
    if (typeof value.id !== "string" || !/^[a-z0-9-]{1,60}$/.test(value.id)) return null;
    if (typeof value.name !== "string" || !value.name.trim() || value.name.length > 32) return null;
    return Object.fromEntries([...["id", "name", "finish"].map(k => [k,value[k]]), ...fields.map(k => [k,value[k].toLowerCase()])]);
  }
  function read() {
    try {
      const data = JSON.parse(global.localStorage.getItem(KEY));
      const active = validate(data?.active);
      if (active) return {active, saved: Array.isArray(data.saved) ? data.saved.map(validate).filter(t => t && t.id.startsWith("custom-")).slice(0,24) : []};
    } catch { /* malformed or unavailable storage uses a usable default */ }
    let legacy;
    try { legacy = global.localStorage.getItem("pivot-theme") || global.localStorage.getItem("charto_theme"); } catch {}
    const dark = legacy === "dark" || ((!legacy || legacy === "system") && !!global.matchMedia?.("(prefers-color-scheme: dark)").matches);
    return {active: {...presets[dark ? 1 : 0]}, saved: []};
  }
  function chartPalette(t = state.active) {
    const text = ink(t.surface), dark = luminance(text) > .5;
    return { dark, chartBg: t.surface, canvasBg: t.finish === "flat" ? t.surface : "transparent", grid: rgba(t.border,.62), border: t.border,
      separator: t.border, axisText: text, crosshair: mix(t.surface,text,.55), crosshairLabel: mix(t.surface,text,.8),
      up: t.up, down: t.down, accent: t.accent, legend: mix(t.surface,text,.8), chipBg: rgba(t.surface,.88),
      volUp: rgba(t.up,.4), volDown: rgba(t.down,.4), histUp: rgba(t.up,.55), histDown: rgba(t.down,.55),
      guide: rgba(text,.22), measureText: t.surface };
  }
  function apply() {
    if (!doc) return;
    const t = state.active, text = ink(t.surface), chromeText = ink(t.chrome), hover = mix(t.surface,text,.065);
    const dark = luminance(text) > .5;
    const set = (k,v) => doc.documentElement.style.setProperty("--"+k,v);
    const secondary = mix(t.surface,text,.75), tertiary = mix(t.surface,text,.65);
    const tokens = {
      "workspace-surface":t.surface, "workspace-chrome":t.chrome, "workspace-border":t.border,
      "workspace-accent":t.accent, "workspace-ink":text, "workspace-chrome-ink":chromeText,
      "bg-base":t.surface,"bg-primary":t.surface,"bg-secondary":t.surface,"bg-card":t.surface,"bg-inset":t.surface,
      "bg-elevated":hover,"surface-track":mix(t.surface,text,.13),"surface-hover":rgba(text,.045),"surface-active":rgba(text,.08),
      "text-primary":text,"text-secondary":secondary,"text-tertiary":tertiary,"text-disabled":mix(t.surface,text,.4),
      "metric-label":chromeText,"color-profit":t.up,"color-loss":t.down,"pivot-blue":t.accent,"price-line":text,
      "shell-chrome":t.chrome,"shell-sidebar-bg":t.chrome,"shell-seam":t.border,"shell-window-border":t.border,
      "glass-paper":rgba(t.surface,.8),"glass-rim":rgba(t.border,.8),"glass-bg":t.surface,"glass-bg-hover":hover,"glass-bg-active":hover,"glass-border":rgba(t.border,.85),
      "glass-border-hover":t.border,"glass-border-focus":t.accent,"border-color":t.border,
      "accent-border":rgba(t.accent,.45),"accent-wash":rgba(t.accent,.08),"chart-bg":t.surface,
      "workspace-glass":rgba(t.surface,.84),"workspace-glass-strong":rgba(t.surface,.94),
      "workspace-finish-image":t.finish === "wash" ? `linear-gradient(125deg,${rgba(t.accent,.09)},transparent 58%,${rgba(t.accent,.035)})` : t.finish === "grain" ? 'url("data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' width=\'160\' height=\'160\'%3E%3Cfilter id=\'n\'%3E%3CfeTurbulence type=\'fractalNoise\' baseFrequency=\'.9\' numOctaves=\'3\' stitchTiles=\'stitch\'/%3E%3C/filter%3E%3Crect width=\'100%25\' height=\'100%25\' opacity=\'.035\' filter=\'url(%23n)\'/%3E%3C/svg%3E")' : "none",
    };
    const semantic = {background:t.surface,foreground:text,card:t.surface,"card-foreground":text,popover:t.surface,"popover-foreground":text,
      primary:text,"primary-foreground":t.surface,secondary:hover,"secondary-foreground":text,muted:hover,"muted-foreground":secondary,
      accent:hover,"accent-foreground":text,border:t.border,input:t.border,ring:t.accent};
    Object.entries(tokens).forEach(([k,v])=>set(k,v));
    Object.entries(semantic).forEach(([k,v])=>set(k,target === "shell" ? hsl(v) : v));
    if (target === "chart") Object.entries({"border-soft":rgba(t.border,.55),"muted-foreground":secondary,faint:tertiary,
      "hover-fill":rgba(text,.07),"menu-bg":rgba(t.surface,.94),"up-soft":rgba(t.up,.12),"down-soft":rgba(t.down,.12),
      "scroll-thumb":rgba(text,.22)}).forEach(([k,v])=>set(k,v));
    doc.documentElement.classList.toggle("dark",dark);
    doc.documentElement.dataset.theme = dark ? "dark" : "light";
    doc.documentElement.dataset.workspacePalette = t.id;
    doc.documentElement.dataset.workspaceFinish = t.finish;
    doc.documentElement.style.colorScheme = dark ? "dark" : "light";
  }
  function announce(persist = true) {
    apply();
    let stored = true;
    if (persist) { try { global.localStorage.setItem(KEY,JSON.stringify(state)); } catch { stored = false; } }
    global.dispatchEvent(new global.CustomEvent("pivot:workspace-colors",{detail:{theme:getTheme(), stored}}));
    return stored;
  }
  const getTheme = () => ({...state.active});
  const getState = () => ({active:getTheme(),saved:state.saved.map(t=>({...t}))});
  function setTheme(value, persist = true) { const t = validate(value); if (!t) return false; state.active=t; announce(persist); return true; }
  function save(name) {
    const clean = String(name).trim().slice(0,32);
    if (!clean || state.saved.length >= 24) return false;
    const theme = {...state.active,name:clean,id:"custom-"+Date.now().toString(36)+Math.random().toString(36).slice(2,7)};
    state.saved.push(theme); state.active=theme; return announce();
  }
  function remove(id) {
    if (!id.startsWith("custom-")) return;
    state.saved = state.saved.filter(t=>t.id !== id);
    if (state.active.id === id) state.active = {...presets[0]};
    announce();
  }
  const escape = str => String(str).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const svg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="m6 6 12 12M18 6 6 18"/></svg>';
  function preview(t) {
    return `<span class="workspace-pair" aria-hidden="true"><i style="background:${t.surface}"></i><i style="background:${t.chrome}"></i></span>`;
  }
  function openPicker(anchor) {
    closePanel?.();
    const returnTo = anchor || doc.activeElement;
    panel = doc.createElement("section"); panel.className="workspace-colors";
    panel.setAttribute("role","dialog"); panel.setAttribute("aria-label","Workspace colors");
    panel.innerHTML = `<header class="workspace-colors-head"><div><h2>Workspace colors</h2><p>Choose a pair. Make it yours.</p></div><button type="button" data-close aria-label="Close workspace colors">${svg}</button></header>
      <div class="workspace-colors-scroll"><div class="workspace-palettes"></div>
      <div class="workspace-editor"><h3>Edit colors</h3><div class="workspace-fields">${fields.slice(0,4).map((key,i)=>`<label><span>${["Charts & widgets","Outer frame","Borders & grid","Accent","Rising candles","Falling candles"][i]}</span><div class="workspace-color-field"><input type="color" data-color="${key}" aria-label="${key} color"><input type="text" data-hex="${key}" aria-label="${key} hex" maxlength="7" spellcheck="false"></div></label>`).join("")}</div>
      <details class="workspace-candle-colors"><summary>Candle colors</summary><div class="workspace-fields">${fields.slice(4).map((key,i)=>`<label><span>${i===0?"Rising candles":"Falling candles"}</span><div class="workspace-color-field"><input type="color" data-color="${key}" aria-label="${key} color"><input type="text" data-hex="${key}" aria-label="${key} hex" maxlength="7" spellcheck="false"></div></label>`).join("")}</div></details>
      <fieldset class="workspace-finishes"><legend>Widget finish</legend>${finishes.map(f=>`<button type="button" data-finish="${f}" aria-pressed="false">${{flat:"Flat",wash:"Soft wash",grain:"Fine grain"}[f]}</button>`).join("")}</fieldset>
      <div class="workspace-save"><input type="text" placeholder="Name your palette" aria-label="Palette name" maxlength="32"><button type="button" data-save>Save copy</button></div>
      <div class="workspace-editor-actions"><button type="button" data-reset>Reset colors</button><button type="button" data-delete>Delete saved palette</button></div>
      </div></div><footer><span class="workspace-status" role="status">Saved in this browser.</span></footer>`;
    doc.body.appendChild(panel);
    function position() {
      const box = returnTo?.getBoundingClientRect?.();
      panel.style.top = `${Math.max(12,Math.min(box ? box.bottom+10 : 72,global.innerHeight-180))}px`;
      panel.style.right = `${Math.max(12,Math.min(box ? global.innerWidth-box.right : 16,global.innerWidth-350))}px`;
      panel.style.maxHeight = `${Math.max(160,global.innerHeight-parseInt(panel.style.top)-12)}px`;
    }
    function render() {
      const active = state.active;
      panel.querySelector(".workspace-palettes").innerHTML = [...presets,...state.saved].map(t=>`<button type="button" data-palette="${t.id}" aria-label="${escape(t.name)} palette" aria-pressed="${t.id===active.id}">${preview(t)}<span>${escape(t.name)}</span></button>`).join("");
      fields.forEach(k=>{ panel.querySelector(`[data-color="${k}"]`).value=active[k]; panel.querySelector(`[data-hex="${k}"]`).value=active[k].toUpperCase(); });
      panel.querySelectorAll("[data-finish]").forEach(el=>el.setAttribute("aria-pressed",String(el.dataset.finish===active.finish)));
      panel.querySelector("[data-delete]").hidden=!active.id.startsWith("custom-");
    }
    function syncInputs() {
      fields.forEach(k=>{ panel.querySelector(`[data-color="${k}"]`).value=state.active[k]; panel.querySelector(`[data-hex="${k}"]`).value=state.active[k].toUpperCase(); });
    }
    function status(text) { panel.querySelector(".workspace-status").textContent=text; }
    const storageStatus = e => status(e.detail.stored ? "Saved in this browser." : "Colors applied. Browser storage is unavailable.");
    global.addEventListener("pivot:workspace-colors",storageStatus);
    panel.addEventListener("click",e=>{
      const button = e.target.closest("button"); if (!button) return;
      if (button.hasAttribute("data-close")) return closePanel();
      if (button.dataset.palette) { setTheme([...presets,...state.saved].find(t=>t.id===button.dataset.palette)); render(); panel.querySelector(`[data-palette="${state.active.id}"]`)?.focus(); }
      if (button.dataset.finish) { setTheme({...state.active,finish:button.dataset.finish}); panel.querySelectorAll("[data-finish]").forEach(el=>el.setAttribute("aria-pressed",String(el.dataset.finish===state.active.finish))); }
      if (button.hasAttribute("data-reset")) { const original = [...presets,...state.saved].find(t=>t.id===state.active.id); setTheme(original || presets[0]); render(); panel.querySelector("[data-reset]").focus(); }
      if (button.hasAttribute("data-save")) {
        const input = panel.querySelector('[aria-label="Palette name"]');
        if (!input.value.trim()) { status("Give your palette a name first."); input.focus(); return; }
        if (state.saved.length>=24) { status("You have 24 saved palettes. Delete one to save another."); return; }
        save(input.value); input.value=""; render(); button.focus();
      }
      if (button.hasAttribute("data-delete")) { remove(state.active.id); render(); panel.querySelector("[data-palette]")?.focus(); }
    });
    panel.addEventListener("input",e=>{
      const k=e.target.dataset.color || e.target.dataset.hex; if (!k) return;
      const value=e.target.value.trim();
      if (!HEX.test(value)) return;
      setTheme({...state.active,[k]:value});
      if (e.target.dataset.color) syncInputs();
      else panel.querySelector(`[data-color="${k}"]`).value=state.active[k];
    });
    panel.addEventListener("keydown",e=>{
      if (e.key === "Enter" && e.target.matches('[aria-label="Palette name"]')) {
        e.preventDefault(); panel.querySelector("[data-save]").click();
      }
    });
    panel.addEventListener("change",e=>{
      const k=e.target.dataset.hex; if (!k) return;
      const value=e.target.value.trim();
      if (!HEX.test(value)) { status("Use a six-digit hex color, such as #182824."); syncInputs(); return; }
      setTheme({...state.active,[k]:value}); syncInputs();
    });
    const outside = e=>{ if (!panel.contains(e.target) && !returnTo?.contains?.(e.target)) closePanel(); };
    const keydown = e=>{
      if (e.key==="Escape") { e.preventDefault(); e.stopPropagation(); closePanel(); return; }
      if (e.key!=="Tab") return;
      const focusable=[...panel.querySelectorAll("button,input")].filter(el=>!el.hidden && el.getClientRects().length);
      const first=focusable[0],last=focusable[focusable.length-1];
      if (e.shiftKey && doc.activeElement===first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && doc.activeElement===last) { e.preventDefault(); first.focus(); }
    };
    closePanel=()=>{ doc.removeEventListener("pointerdown",outside); doc.removeEventListener("keydown",keydown,true); global.removeEventListener("resize",position); global.removeEventListener("pivot:workspace-colors",storageStatus); panel.remove(); panel=null; closePanel=null; returnTo?.focus?.(); };
    doc.addEventListener("pointerdown",outside); doc.addEventListener("keydown",keydown,true); global.addEventListener("resize",position);
    render(); position(); panel.querySelector(`[data-palette="${state.active.id}"]`)?.focus();
  }
  state = read();
  global.PivotWorkspaceTheme = {getTheme,getState,setTheme,save,remove,openPicker,close:()=>closePanel?.(),presets:presets.map(t=>({...t})),chartPalette,validate,apply};
  apply();
  global.addEventListener("storage",e=>{ if (e.key===KEY) { state=read(); announce(false); } });
})(window);
