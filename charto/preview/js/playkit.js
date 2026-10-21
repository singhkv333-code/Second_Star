/* Pivot Plays — the component kit (plays.html).
 *
 * Everything a play is drawn with, in the shapes traders already read from
 * Catalyst and Instinct: a canvas of Signal → Trigger → Action cards on a
 * dotted grid, value chips (what is read, the comparison, the level), Met
 * pills, an action card with its own price chart, an alert stack, and a
 * strategy tile. Every figure comes off the card or the event the server
 * built from the stored row; nothing here computes a level or a decision.
 */
"use strict";

const Kit = (() => {
  const API = location.port === "5173" ? "http://127.0.0.1:5174" : "";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  async function call(path, body) {
    const res = await fetch(API + path, {
      method: body === undefined ? "GET" : "POST",
      headers: Auth.headers(body === undefined ? {} : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let d = {};
    try { d = await res.json(); } catch { /* not json */ }
    if (!res.ok) throw new Error(d.error || `request failed (${res.status})`);
    return d;
  }

  /* ── numbers and time ─────────────────────────────────────────────────── */
  const num = (v, dp) => {
    if (v == null || !Number.isFinite(+v)) return "";
    const a = Math.abs(+v);
    const d = dp != null ? dp : a >= 1000 ? 2 : a >= 100 ? 2 : a >= 1 ? 2 : 4;
    return (+v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: a >= 100 && dp == null ? 0 : 0 });
  };
  const inr = (v, dp = 0) => v == null || !Number.isFinite(+v) ? "" :
    `${+v < 0 ? "−" : ""}₹${Math.abs(+v).toLocaleString("en-IN", { maximumFractionDigits: dp, minimumFractionDigits: dp })}`;
  const signed = (v, suffix = "%") => v == null || !Number.isFinite(+v) ? "" :
    `${+v > 0 ? "+" : +v < 0 ? "−" : ""}${Math.abs(+v).toFixed(2)}${suffix}`;
  /** "₹14.29" drawn with its paise lighter, as Catalyst draws "$14.29". */
  function bigMoney(v, prefix = "₹") {
    if (v == null || !Number.isFinite(+v)) return "–";
    const s = Math.abs(+v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const [w, d] = s.split(".");
    return `${+v < 0 ? "−" : ""}${prefix}${w}<span class="dec">.${d}</span>`;
  }
  function bigNum(v) {
    if (v == null || !Number.isFinite(+v)) return "–";
    const s = num(v);
    const i = s.indexOf(".");
    return i < 0 ? s : `${s.slice(0, i)}<span class="dec">${s.slice(i)}</span>`;
  }
  const ago = (ts) => {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return `${Math.max(1, Math.floor(s))}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return day(ts);
  };
  const day = (ts) => ts ? new Date(ts * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "";
  const dayY = (ts) => ts ? new Date(ts * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "";

  /* ── icons (stroke, 24 grid) ──────────────────────────────────────────── */
  const P = {
    signal: '<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2M4.9 19.1a10 10 0 0 1 0-14.2"/>',
    trigger: '<path d="M6 2h12M6 22h12M7 2v4a5 5 0 0 0 10 0V2M7 22v-4a5 5 0 0 1 10 0v4"/>',
    action: '<path d="M4 6h6M4 12h4M4 18h6"/><path d="M15 3l-3 9h5l-3 9 7-11h-5l3-7z"/>',
    exit: '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4"/><path d="M9 8l-4 4 4 4M5 12h11"/>',
    bell: '<path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4l2-2z"/><path d="M10 21h4"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    check: '<path d="M4 12.5l5 5L20 7"/>',
    checkc: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    ban: '<circle cx="12" cy="12" r="9"/><path d="M6 6l12 12"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    chart: '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 6-7"/>',
    news: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 8h10M7 12h10M7 16h6"/>',
    file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
    scale: '<path d="M12 3v18M5 7h14M5 7l-3 7a4 4 0 0 0 6 0zM19 7l-3 7a4 4 0 0 0 6 0zM8 21h8"/>',
    flow: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><path d="M6.5 10v3a2 2 0 0 0 2 2H14"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    pencil: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
    play: '<path d="M7 5l12 7-12 7z"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    up: '<path d="M12 19V5M5 12l7-7 7 7"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    wallet: '<rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M16 15h2"/>',
    pie: '<path d="M12 3a9 9 0 1 0 9 9h-9z"/><path d="M15 3.5A9 9 0 0 1 20.5 9H15z"/>',
    list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    chevron: '<path d="M6 15l6-6 6 6"/>',
    target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
  };
  const ic = (n, cls = "") => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[n] || ""}</svg>`;

  /* ── logos: the store's marks, else a lettered square ────────────────── */
  let LOGOS = {}, NAMES = {};
  let logosReady = null;
  function loadLogos() {
    if (logosReady) return logosReady;
    logosReady = fetch(`${API}/symbols`).then((r) => r.json()).then((d) => {
      LOGOS = d.logos || {};
      NAMES = d.long || d.names || {};
    }).catch(() => {});
    return logosReady;
  }
  const HUES = ["#f0570f", "#2563eb", "#16a34a", "#7c4dff", "#d63d8f", "#0a86c4", "#b26b00", "#475569"];
  const hue = (s) => HUES[[...String(s)].reduce((a, c) => a + c.charCodeAt(0), 0) % HUES.length];
  function logo(sym, cls = "") {
    const s = String(sym || "").toUpperCase();
    const base = s.replace(/USDT$|-USD$/, "");
    const url = LOGOS[s] || LOGOS[base] || LOGOS[`${base}USDT`];
    if (url) return `<img class="logo ${cls}" src="${esc(url)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'logo mono-mark ${cls}',textContent:'${esc(base.replace(/[^A-Z0-9]/g, "").slice(0, 1))}',style:'background:${hue(s)}'}))" />`;
    const letter = s.startsWith("@") ? s.slice(1, 2) : s.startsWith("NIFTY") ? "N" : base.replace(/[^A-Z0-9]/g, "").slice(0, 1) || "?";
    return `<span class="logo mono-mark ${cls}" style="background:${hue(s)}">${esc(letter)}</span>`;
  }
  const name = (sym) => NAMES[String(sym || "").toUpperCase()] || "";
  /** A news outlet's mark: its initials on its own colour. */
  function outlet(who) {
    const w = String(who || "").trim();
    const known = { "Mint": ["M", "#e8501f"], "Economic Times": ["ET", "#b4001e"], "CNBC-TV18": ["CN", "#0b4ea2"],
      "BusinessLine": ["BL", "#1f3a68"], "BBC": ["BBC", "#111"], "CoinDesk": ["CD", "#2563eb"] };
    const k = Object.keys(known).find((x) => w.toLowerCase().includes(x.toLowerCase()));
    const [t, c] = k ? known[k] : [w.split(/\s+/).map((x) => x[0]).join("").slice(0, 2).toUpperCase() || "?", hue(w)];
    return `<span class="logo mono-mark" style="background:${c};font-size:${t.length > 2 ? 8 : 10}px">${esc(t)}</span>`;
  }

  /* ── charts ───────────────────────────────────────────────────────────── */
  let gid = 0;
  /** A line (or area) chart over [{t, v}]: right-hand price labels, a few
   *  dates underneath, a hover read-out, and optional markers. */
  function lineChart(host, pts, o = {}) {
    const W = Math.max(160, host.clientWidth || o.width || 300), H = o.height || 90;
    const padR = o.axis === false ? 4 : 44, padB = o.dates === false ? 4 : 18, padT = 6;
    const v = pts.map((p) => p.v).filter((x) => x != null && Number.isFinite(x));
    if (v.length < 2) { host.innerHTML = `<div class="muted" style="font-size:12px;padding:16px 0">No price history here.</div>`; return; }
    let lo = Math.min(...v), hi = Math.max(...v);
    if (o.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (hi === lo) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
    const x = (i) => (i / (pts.length - 1)) * (W - padR);
    const y = (val) => padT + (1 - (val - lo) / (hi - lo)) * (H - padT - padB);
    let d = "", pen = false;
    pts.forEach((p, i) => {
      if (p.v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(p.v).toFixed(1)}`; pen = true;
    });
    const up = v[v.length - 1] >= v[0];
    const color = o.color || (o.area ? (up ? "#5aa62b" : "#d42a2a") : "#2563eb");
    const id = `g${++gid}`;
    const ticks = 3, fmt = o.yfmt || ((t) => num(t, Math.abs(hi) < 10 ? 2 : 0));
    let axis = "";
    if (o.axis !== false) {
      for (let k = 0; k < ticks; k++) {
        const val = hi - pad - (k / (ticks - 1)) * (hi - lo - 2 * pad);
        axis += `<text class="ax" x="${W - padR + 6}" y="${y(val) + 3.5}">${esc(fmt(val))}</text>`;
      }
    }
    let dates = "";
    if (o.dates !== false && pts[0].t) {
      const n = Math.min(W < 260 ? 2 : W < 420 ? 3 : 4, pts.length);
      for (let k = 0; k < n; k++) {
        const i = Math.round(k / (n - 1 || 1) * (pts.length - 1));
        const t = pts[i].t;
        const lab = o.intraday ? new Date(t * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false })
          : new Date(t * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
        const anchor = k === 0 ? "start" : k === n - 1 ? "end" : "middle";
        dates += `<text class="ax" x="${Math.min(W - padR, Math.max(0, x(i)))}" y="${H - 3}" text-anchor="${anchor}">${esc(lab)}</text>`;
      }
    }
    const marks = (o.markers || []).map((t) => {
      let best = -1, bd = Infinity;
      pts.forEach((p, i) => { const dd = Math.abs(p.t - t); if (dd < bd && p.v != null) { bd = dd; best = i; } });
      return best < 0 || bd > (o.markWithin || 86400 * 4) ? "" : `<circle class="mark" cx="${x(best)}" cy="${y(pts[best].v)}" r="4.5"/>`;
    }).join("");
    const lastI = pts.length - 1;
    const area = o.area ? `<path d="${d} L${x(lastI)} ${H - padB} L0 ${H - padB} Z" fill="url(#${id})"/>` : "";
    const zero = o.zero ? `<line class="zero" x1="0" x2="${W - padR}" y1="${y(0)}" y2="${y(0)}"/>` : "";
    host.innerHTML = `<div class="chart"><svg viewBox="0 0 ${W} ${H}" height="${H}" preserveAspectRatio="none">` +
      `<defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".22"/><stop offset="1" stop-color="${color}" stop-opacity=".02"/></linearGradient></defs>` +
      `${zero}${area}<path d="${d}" fill="none" stroke="${color}" stroke-width="1.7" stroke-linejoin="round"/>` +
      (o.dot !== false ? `<circle cx="${x(lastI)}" cy="${y(v[v.length - 1])}" r="3" fill="${color}"/>` : "") +
      `${marks}${axis}${dates}<line class="hover-line" x1="0" x2="0" y1="${padT}" y2="${H - padB}" visibility="hidden"/></svg></div>`;
    const svg = host.querySelector("svg"), hl = svg.querySelector(".hover-line");
    const tip = document.createElement("div");
    tip.className = "chart-tip"; tip.hidden = true;
    host.querySelector(".chart").appendChild(tip);
    svg.addEventListener("mousemove", (e) => {
      const r = svg.getBoundingClientRect();
      const fx = (e.clientX - r.left) / r.width * W;
      const i = Math.max(0, Math.min(lastI, Math.round(fx / (W - padR) * lastI)));
      if (pts[i].v == null) return;
      hl.setAttribute("x1", x(i)); hl.setAttribute("x2", x(i)); hl.setAttribute("visibility", "visible");
      tip.hidden = false;
      tip.style.left = `${x(i) / W * 100}%`;
      tip.style.top = `${y(pts[i].v) / H * 100}%`;
      const when = pts[i].t ? (o.intraday ? new Date(pts[i].t * 1000).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : dayY(pts[i].t)) : "";
      tip.textContent = `${(o.tipfmt || fmt)(pts[i].v)}${when ? ` · ${when}` : ""}`;
    });
    svg.addEventListener("mouseleave", () => { hl.setAttribute("visibility", "hidden"); tip.hidden = true; });
  }

  const TF = { "1D": ["5m", 76, true], "1W": ["30m", 66, true], "1M": ["1d", 23, false], "3M": ["1d", 64, false],
               "1Y": ["1d", 250, false], "ALL": ["1d", 750, false] };
  const barCache = new Map();
  async function bars(sym, tf) {
    const k = `${sym}|${tf}`;
    if (barCache.has(k)) return barCache.get(k);
    const [iv, lim] = TF[tf];
    const p = fetch(`${API}/bars?symbol=${encodeURIComponent(sym)}&interval=${iv}&limit=${lim}`, { headers: Auth.headers() })
      .then((r) => r.ok ? r.json() : { bars: [] }).then((d) => {
        let b = d.bars || [];
        if (tf === "1D" && b.length) {           // the latest session only
          const last = new Date((b[b.length - 1].t + 19800) * 1000).toISOString().slice(0, 10);
          b = b.filter((x) => new Date((x.t + 19800) * 1000).toISOString().slice(0, 10) === last);
        }
        return b;
      }).catch(() => []);
    barCache.set(k, p);
    return p;
  }

  /** Catalyst's action chart: the price, its move over the window, the
   *  window tabs, and the line. Draws into `host`; returns nothing. */
  function priceBlock(host, sym, o = {}) {
    const tfs = o.tfs || ["1D", "1W", "1M", "ALL"];
    let tf = o.tf || "1M";
    host.innerHTML = `<div class="pricebar"><span class="p num">–</span><span class="pill">–</span><span class="grow"></span>` +
      `<div class="tf">${tfs.map((t) => `<button type="button" data-tf="${t}" class="${t === tf ? "on" : ""}">${t}</button>`).join("")}</div></div>` +
      `<div class="cbody" style="margin-top:6px"></div>`;
    const draw = async () => {
      host.querySelectorAll("[data-tf]").forEach((b) => b.classList.toggle("on", b.dataset.tf === tf));
      const b = await bars(sym, tf);
      const body = host.querySelector(".cbody");
      if (!b.length) {
        host.querySelector(".p").textContent = o.price != null ? num(o.price) : "–";
        host.querySelector(".pill").replaceWith(Object.assign(document.createElement("span"), { className: "pill", textContent: "no history" }));
        body.innerHTML = `<div class="muted" style="font-size:12px;padding:10px 0">No stored bars for ${esc(sym)} on this server.</div>`;
        return;
      }
      const first = b[0].o || b[0].c, last = b[b.length - 1].c;
      const ch = first ? (last / first - 1) * 100 : 0;
      host.querySelector(".p").innerHTML = bigNum(last);
      const pill = host.querySelector(".pricebar .pill");
      pill.className = `pill ${ch >= 0 ? "up" : "down"}`;
      pill.textContent = signed(ch);
      lineChart(body, b.map((x) => ({ t: x.t, v: x.c })), { height: o.height || 86, width: o.width || 300,
        intraday: TF[tf][2], markers: o.markers, area: o.area, color: o.color });
    };
    host.addEventListener("click", (e) => {
      const t = e.target.closest("[data-tf]");
      if (t) { e.stopPropagation(); tf = t.dataset.tf; draw(); }
    });
    draw();
  }

  /* ── the canvas: Signal → Trigger → Action ────────────────────────────── */
  const OPS = { above: ">", below: "<", cross_up: "crosses ↑", cross_down: "crosses ↓", between: "between",
                outside: "outside", rises_pct: "rises", falls_pct: "falls", is_true: "is true" };

  function condRow(p, now) {
    if (!p) return now && now.text ? `<div class="cond"><span>${esc(now.text)}</span></div>` : "";
    if (p.kind === "time") return `<div class="cond"><span class="chip vio">${ic("clock")}${esc(p.text)}</span></div>`;
    if (p.kind === "group") return p.items.map((x) => condRow(x)).join(`<div class="join"><span>${p.join}</span></div>`);
    const L = esc(p.left), R = p.right != null ? esc(p.right) : "";
    const lnow = now && now.now != null ? `<span class="chip sig">${ic("signal")}${esc(num(now.now))}</span>` : "";
    const rnum = now && now.target != null && p.right_live ? `<span class="chip val">${ic("signal")}${esc(num(now.target))}</span>` : "";
    const rconst = !p.right_live && R ? `<span class="chip val">${R}</span>` : "";
    return `<div class="cond"><span>If ${L}</span>${lnow}<span class="chip op">${esc(OPS[p.op] || p.op)}</span>` +
      (p.right_live && R ? `<span>${R}</span>${rnum}` : rconst) +
      (p.for_bars ? `<span class="chip plain">for ${p.for_bars} bars</span>` : "") + `</div>`;
  }

  function sliderHTML(vals, now) {
    const v = (vals || []).filter((x) => x != null && Number.isFinite(+x));
    if (v.length < 2 || now == null) return "";
    const lo = Math.min(...v), hi = Math.max(...v);
    const at = hi === lo ? 50 : (now - lo) / (hi - lo) * 100;
    return `<div class="slider"><i style="left:${Math.max(0, Math.min(100, at))}%"></i></div>` +
      `<div class="slider-lab" style="display:flex;justify-content:space-between;font-size:10.5px;color:var(--ink-3)">` +
      `<span>${esc(num(lo))}</span><span>${esc(num(now))}</span><span>${esc(num(hi))}</span></div>`;
  }

  const TAGS = ["#f7a8cf", "#ffb27d", "#9cc0ff", "#a6e3b5"];

  function signalNode(sig, i) {
    return `<div class="node signal" data-node="s${i}"><div class="tabhead">${ic("signal")}Signal</div>` +
      `<span class="tag" style="background:${TAGS[i % TAGS.length]}"></span>` +
      `<div class="tt">${esc(sig.title)}</div>${sig.cap ? `<div class="cap">${esc(sig.cap)}</div>` : ""}<div class="inner">${sig.body}</div></div>`;
  }

  function signals(c) {
    const out = [];
    const measures = (c.measures || []).filter((m) => m.title);
    measures.slice(0, 3).forEach((m) => {
      out.push({ title: m.title, cap: m.formula && m.formula !== m.title ? m.formula : "", body:
        `<div class="src">${ic("sparkle")}` +
        `<span class="grow"></span><span class="pill live">${ic("grid")}Live</span></div>` +
        `<div class="big">${m.now == null ? "<span class=muted>no value</span>" : bigNum(m.now)}</div>` + sliderHTML(m.spark, m.now) });
    });
    if (c.on) {
      const items = (c.on.recent || []).slice(-3).reverse();
      out.push({ title: c.on.reads ? c.on.reads.charAt(0).toUpperCase() + c.on.reads.slice(1) : "Filings and news", body:
        `<div class="src">${ic(c.on.reads && c.on.reads.includes("filing") ? "file" : "news")}<span>Read live, every 30 seconds</span>` +
        `<span class="grow"></span><span class="pill live">${ic("grid")}Live</span></div>` +
        (items.length ? `<div class="items" style="margin-top:8px">${items.map((r) =>
          `<div class="item">${outlet(r.who)}<div><a href="${esc(r.url)}" target="_blank" rel="noopener" style="text-decoration:none">${esc(r.title)}</a>` +
          `<div class="when">${esc(r.who)} · ${esc(day(r.ts))}</div></div></div>`).join("")}</div>`
          : `<div class="muted" style="font-size:12.5px;margin-top:8px">No matching item in what is stored yet.</div>`) });
    }
    const priceCond = (c.conditions || []).some((x) => /\b(close|open|high|low|volume)\b/i.test(x.text));
    if (c.do && c.do.kind === "rebalance") {
      const held = Object.entries(c.held || {}).filter(([, q]) => q);
      out.push({ title: c.do.of === "portfolio" ? "Your paper portfolio" : "What this play holds", body:
        `<div class="src">${ic("wallet")}<span>${held.length ? `${held.length} holdings` : "Read at each run"}</span><span class="grow"></span><span class="pill live">${ic("grid")}Live</span></div>` +
        (held.length ? `<div class="items" style="margin-top:8px">${held.slice(0, 6).map(([s, q]) =>
          `<div class="item" style="align-items:center">${logo(s, "sm")}<span style="flex:1">${esc(s)}</span><b class="num">${esc(num(q, 4))}</b></div>`).join("")}</div>` : "") });
    } else if ((priceCond || (!measures.length && !c.on)) && c.symbol) {
      out.push({ title: `${c.symbol} price`, body:
        `<div class="src">${logo(c.symbol, "sm")}<span>${esc(c.symbol.startsWith("@") ? "Your security" : c.symbol.endsWith("USDT") ? "Bybit" : "NSE")}</span><span class="grow"></span><span class="pill live">${ic("grid")}Live</span></div>` +
        `<div class="big">${c.history && c.history.price != null ? bigNum(c.history.price) : "–"}</div>` +
        `<div class="sig-chart" data-sym="${esc(c.symbol)}"></div>` });
    }
    return out;
  }

  function triggerNode(c) {
    const conds = c.conditions || [];
    let met = null, body = "", head = "Trigger", icon = "trigger";
    if (c.do && c.do.kind === "rebalance" && c.lifespan === "policy") {
      head = "Schedule"; icon = "calendar";
      body = `<div class="cond"><span>Every</span><span class="chip val">${esc((c.policy || {}).every || "week")}</span></div>` +
        `<div class="join"><span>or</span></div><div class="cond"><span>If a holding drifts</span><span class="chip op">&gt;</span>` +
        `<span class="chip val">${esc(num((c.policy || {}).drift_pct || 5))} pts</span><span>from target</span></div>`;
    } else if (c.on) {
      met = null;
      const words = (c.on.test || "").match(/“([^”]+)”/g) || [];
      body = `<div class="cond"><span>When an item mentions</span>${words.slice(0, 4).map((w) => `<span class="chip sig2">${esc(w.replace(/[“”]/g, ""))}</span>`).join("")}</div>` +
        (c.on.test && c.on.test.includes("answers yes") ? `<div class="join"><span>and</span></div><div class="cond" style="font-size:13.5px">` +
          `<span class="chip vio">${ic("sparkle")}judge</span><span>${esc((c.on.test.split("answers yes to")[1] || "").replace(/[“”]/g, "").trim())}</span></div>` : "");
      if (conds.length) body += `<div class="join"><span>and while</span></div>` + conds.map((x) => condRow(x.parts, x)).join(`<div class="join"><span>${c.join === "any" ? "or" : "and"}</span></div>`);
    } else if (conds.length) {
      met = c.join === "any" ? conds.some((x) => x.met) : conds.every((x) => x.met);
      body = conds.map((x) => condRow(x.parts, x)).join(`<div class="join"><span>${c.join === "any" ? "or" : "and"}</span></div>`);
      const away = conds.find((x) => x.away_pct != null && !x.met);
      if (away) body += `<div class="nfoot"><span>${ic("target")} ${esc(num(away.away_pct))}% away</span><span></span></div>`;
    } else {
      head = "Now"; icon = "bolt"; met = true;
      body = `<div class="cond"><span>Runs once, as soon as you asked</span></div>`;
    }
    const pill = c.on ? `<span class="pill armed">Listening</span>` : met == null ? "" :
      `<span class="pill ${met ? "met" : ""}">${met ? "Met" : "Not met"}</span>`;
    return `<div class="node" data-node="t"><div class="nh">${ic(icon)}<span>${head}</span><span class="grow"></span>${pill}</div>${body}</div>`;
  }

  const STATE = { armed: ["Armed", "armed"], paused: ["Paused", ""], done: ["Completed", "done"], draft: ["Draft", ""] };
  const VERB = { buy: ["Buy", "buy"], sell: ["Sell", "sell"], short: ["Short", "sell"], cover: ["Cover", "buy"] };
  const COLORS = ["#2563eb", "#f0570f", "#16a34a", "#7c4dff", "#d63d8f", "#0a86c4", "#b26b00", "#64748b"];

  function actionNode(c) {
    const d = c.do || {}, mg = c.manage || {}, st = STATE[c.state] || [c.state, ""];
    const pill = `<span class="pill ${st[1]}">${esc(st[0])}</span>`;
    const head = (label, icon = "action") => `<div class="nh">${ic(icon)}<span>${label}</span><span class="grow"></span>${pill}</div>`;
    const takeStop = () => {
      const bits = [];
      if (d.size && d.size.inr) bits.push(`<span>Allocate</span><span class="edit">${esc(inr(d.size.inr))}${ic("pencil")}</span>`);
      if (d.size && d.size.qty) bits.push(`<span>Qty</span><span class="edit">${esc(num(d.size.qty))}${ic("pencil")}</span>`);
      if (mg.target_pct) bits.push(`<span>Take</span><span class="edit">+${esc(num(mg.target_pct))}%${ic("pencil")}</span>`);
      if (mg.stop_pct) bits.push(`<span class="sep"></span><span>Stop</span><span class="edit">−${esc(num(mg.stop_pct))}%${ic("pencil")}</span>`);
      if (mg.trail_pct) bits.push(`<span>Trail</span><span class="edit">${esc(num(mg.trail_pct))}%${ic("pencil")}</span>`);
      return bits.length ? `<div class="params">${bits.join("")}</div>` : "";
    };
    const actsWord = c.authority === "notify" ? "Tells you" : c.authority === "propose" ? "You confirm each order" :
      c.authority === "manage" ? "Places and manages it" : "Places it on its own";
    if (d.kind === "notify") {
      return `<div class="node" data-node="a">${head("Action")}<div class="act-title">${ic("bell", "mark-ic")}<span>Alert you in the Inbox</span></div>` +
        `<div class="inner"><div class="src">${ic("bell")}<span>${esc((c.contract || {}).runs || "")}</span></div>` +
        `<div style="font-size:13px;margin-top:6px;line-height:1.45">Each alert says what it saw, with the value that decided it.</div></div></div>`;
    }
    if (d.kind === "rebalance") {
      const t = Object.entries(d.targets || {}).sort((a, b) => b[1] - a[1]);
      const cash = Math.max(0, 1 - t.reduce((a, [, w]) => a + w, 0));
      const bar = t.map(([s, w], i) => `<i style="width:${w * 100}%;background:${COLORS[i % COLORS.length]}" title="${esc(s)}"></i>`).join("") +
        (cash > 0.005 ? `<i style="width:${cash * 100}%;background:#d4d4d4"></i>` : "");
      return `<div class="node" data-node="a">${head("Action")}<div class="act-title"><span class="chip plain">${ic("scale")}Rebalance</span>` +
        `<span>${d.of === "portfolio" ? "the paper portfolio" : "its holdings"}</span></div>` +
        `<div class="params"><span>Max</span><span class="edit">${esc(num(((c.mandate || {}).max_weight || 0.4) * 100))}%${ic("pencil")}</span><span>per name</span>` +
        (d.size && d.size.inr ? `<span class="sep"></span><span>New money</span><span class="edit">${esc(inr(d.size.inr))}</span>` : "") + `</div>` +
        (c.discretion === "plan" ? `<div class="params"><span class="chip vio">${ic("sparkle")}Planner</span><span>${esc(d.objective)}</span></div>` : "") +
        (t.length ? `<div class="inner"><div class="alloc">${bar}</div><div class="alloc-legend">${t.map(([s, w], i) =>
          `<div><span class="sw" style="background:${COLORS[i % COLORS.length]}"></span>${logo(s, "sm")}<span>${esc(s)}</span><b>${esc(num(w * 100, 1))}%</b></div>`).join("")}` +
          (cash > 0.005 ? `<div><span class="sw" style="background:#d4d4d4"></span><span>Cash</span><b>${esc(num(cash * 100, 1))}%</b></div>` : "") + `</div></div>`
          : `<div class="inner" style="font-size:13px">The planner sets the weights at each run.</div>`) +
        `<div class="nfoot"><span>${ic("link")} Delivery · whole shares</span><span>${esc(actsWord)}</span></div></div>`;
    }
    if (d.kind === "options") {
      return `<div class="node" data-node="a">${head("Action")}<div class="act-title"><span>Options on</span>${logo(d.underlying, "sm")}<span class="chip plain">${esc(d.underlying)}</span></div>` +
        takeStop() + `<div class="legs">${(d.legs || []).map((lg) =>
          `<div class="leg"><span class="chip ${lg.side === "buy" ? "buy" : "sell"}">${lg.side === "buy" ? "Buy" : "Sell"}</span>` +
          `<b>${esc(lg.strike)} ${esc(lg.opt)}</b><span class="grow"></span><span class="muted">${esc(lg.lots)} lot${lg.lots == 1 ? "" : "s"} · ${esc(lg.expiry || "weekly")}</span></div>`).join("")}</div>` +
        `<div class="inner" style="margin-top:8px"><div class="price-host" data-sym="${esc(d.underlying)}"></div></div>` +
        `<div class="nfoot"><span>${ic("link")} ${d.product === "MIS" ? "Intraday" : "Carry (NRML)"}</span><span>${esc(actsWord)}</span></div></div>`;
    }
    if (d.kind === "future") {
      return `<div class="node" data-node="a">${head("Action")}<div class="act-title"><span class="chip ${d.side === "sell" ? "sell" : "buy"}">${d.side === "sell" ? "Sell" : "Buy"}</span>` +
        `<span>${esc(d.lots || 1)} lot of</span>${logo(d.underlying, "sm")}<span class="chip plain">${esc(d.underlying)} future</span></div>` + takeStop() +
        `<div class="inner"><div class="price-host" data-sym="${esc(d.underlying)}"></div></div>` +
        `<div class="nfoot"><span>${ic("link")} Futures</span><span>${esc(actsWord)}</span></div></div>`;
    }
    const [v, cls] = VERB[d.kind] || ["Buy", "buy"];
    const sym = d.symbol || c.symbol;
    return `<div class="node" data-node="a">${head("Action")}<div class="act-title"><span>${v === "Short" || v === "Cover" ? "" : ""}</span>` +
      `<span class="chip ${cls}">${v}</span><span>${d.size && d.size.all ? "all of" : ""}</span>${logo(sym, "sm")}<span class="chip plain">${esc(sym)}</span><span>on</span>` +
      `<span class="chip plain">${ic("shield")}Paper</span></div>` + takeStop() +
      `<div class="inner"><div class="price-host" data-sym="${esc(sym)}"></div></div>` +
      `<div class="nfoot"><span>${ic("link")} ${d.product === "MIS" ? "Intraday · squared off 15:20" : "Delivery"}</span><span>${esc(actsWord)}</span></div></div>`;
  }

  function exitNode(c) {
    const ex = (c.exits || []).filter((e) => !/^stop |^target /.test(e) || (c.do && ["options", "short"].includes(c.do.kind)));
    const mg = c.manage || {};
    const lines = [];
    if (mg.exit_at) lines.push(`Close everything at ${mg.exit_at}`);
    if (mg.on_leg_stop) lines.push(mg.on_leg_stop === "exit_all" ? "One leg stopped: exit all" : "One leg stopped: move the others to cost");
    if (mg.max_loss_inr) lines.push(`Exit all at a ${inr(mg.max_loss_inr)} loss`);
    if (mg.target_inr) lines.push(`Exit all at a ${inr(mg.target_inr)} profit`);
    if (!lines.length && !ex.some((e) => e.includes("squared off"))) return "";
    if (!lines.length) lines.push("Squared off at 15:20 if still open");
    return `<div class="node exit" data-node="x"><div class="nh">${ic("action")}<span>Action</span></div>` +
      `<div class="tt">${ic("exit")}<span>Exit</span></div><div style="font-size:13px;line-height:1.55">${lines.map(esc).join("<br/>")}</div></div>`;
  }

  /** The canvas for one play. Returns an element; wires draw once it is in
   *  the page and redraw when it changes size. */
  function canvas(c) {
    const el = document.createElement("div");
    el.className = "canvas dots";
    const sigs = signals(c);
    el.innerHTML = `<svg class="wires"></svg>` +
      `<div class="col">${sigs.map(signalNode).join("")}</div>` +
      `<div class="col">${triggerNode(c)}</div>` +
      `<div class="col">${actionNode(c)}${exitNode(c)}</div>`;
    const draw = () => wires(el, c);
    new ResizeObserver(draw).observe(el);
    requestAnimationFrame(() => requestAnimationFrame(draw));
    setTimeout(draw, 600);
    el.querySelectorAll(".price-host").forEach((h) => priceBlock(h, h.dataset.sym, { width: 300, height: 78,
      markers: (c.history || {}).fire_ts, price: (c.history || {}).price }));
    el.querySelectorAll(".sig-chart").forEach(async (h) => {
      const b = await bars(h.dataset.sym, "3M");
      if (b.length) lineChart(h, b.map((x) => ({ t: x.t, v: x.c })), { height: 46, width: 260, axis: false, dates: false,
        markers: (c.history || {}).fire_ts });
      setTimeout(draw, 50);
    });
    return el;
  }

  function wires(el, c) {
    const svg = el.querySelector("svg.wires");
    if (!svg || !el.isConnected) return;
    if (getComputedStyle(svg).display === "none") return;
    const box = el.getBoundingClientRect();
    const at = (n) => { const r = n.getBoundingClientRect(); return { l: r.left - box.left, r: r.right - box.left, t: r.top - box.top, b: r.bottom - box.top, m: (r.top + r.bottom) / 2 - box.top }; };
    const t = el.querySelector('[data-node="t"]'), a = el.querySelector('[data-node="a"]'), x = el.querySelector('[data-node="x"]');
    if (!t || !a) return;
    const T = at(t), A = at(a);
    const path = (x1, y1, x2, y2, hot) => {
      const mx = (x1 + x2) / 2, r = Math.min(10, Math.abs(y2 - y1) / 2);
      const dir = y2 > y1 ? 1 : -1;
      const d = Math.abs(y2 - y1) < 2 ? `M${x1} ${y1} H${x2}` :
        `M${x1} ${y1} H${mx - r} Q${mx} ${y1} ${mx} ${y1 + dir * r} V${y2 - dir * r} Q${mx} ${y2} ${mx + r} ${y2} H${x2 - 1}`;
      return `<path class="${hot ? "hot" : ""}" d="${d}"/><circle cx="${x1}" cy="${y1}" r="3"/><circle cx="${x2}" cy="${y2}" r="3"/>`;
    };
    let out = "";
    const metAll = (c.conditions || []).length && (c.join === "any" ? c.conditions.some((q) => q.met) : c.conditions.every((q) => q.met));
    el.querySelectorAll('[data-node^="s"]').forEach((s, i, all) => {
      const S = at(s);
      const ty = T.m + (all.length > 1 ? (i - (all.length - 1) / 2) * 14 : 0);
      out += path(S.r, S.m, T.l, ty, false);
    });
    out += path(T.r, T.m, A.l, A.m, metAll);
    if (x) { const X = at(x); out += path(T.r, T.m + 10, X.l, X.m, false); }
    svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
    svg.innerHTML = out;
  }

  /* ── the strategy card: head, canvas, the offer, the facts ───────────── */
  function strategy(c, o = {}) {
    const el = document.createElement("div");
    el.className = "strat";
    el.dataset.play = c.id;
    const st = STATE[c.state] || [c.state, ""];
    const syms = [...new Set([c.symbol, (c.do || {}).symbol, (c.do || {}).underlying, ...Object.keys((c.do || {}).targets || {})].filter(Boolean))].slice(0, 4);
    const h = c.history || {};
    const fires = h.bars && (c.conditions || []).length && !c.on
      ? `Would have fired <b>${esc(h.fires)}×</b> in ${esc(h.bars)} ${c.interval === "1d" ? "days" : `${c.interval} bars`}` +
        (h.last_fire ? `, last on <b>${esc(dayY(h.last_fire))}</b>` : "") : "";
    el.innerHTML = `<div class="strat-head"><div style="flex:1;min-width:0"><div class="row" style="gap:8px;margin-bottom:3px">` +
      `<h3>${esc(c.title)}</h3><span class="pill ${st[1]}"><i class="dot"></i>${esc(st[0])}</span></div><p>${esc(c.summary || "")}</p></div>` +
      `<div class="logos">${syms.map((s) => logo(s, "lg")).join("")}</div></div>`;
    el.appendChild(canvas(c));
    if (c.offer) {
      const off = document.createElement("div");
      off.className = "offer";
      off.innerHTML = `${ic("refresh")}<span class="grow">${esc(c.offer.label)}</span><button type="button" class="btn sm dark" data-act="${esc(c.offer.action)}">Keep it</button>`;
      el.appendChild(off);
    }
    if ((c.notes || []).length) {
      const n = document.createElement("ul");
      n.className = "notes";
      n.innerHTML = c.notes.slice(0, 3).map((x) => `<li>${esc(x)}</li>`).join("");
      el.appendChild(n);
    }
    const foot = document.createElement("div");
    foot.className = "strat-foot";
    const k = c.contract || {};
    foot.innerHTML = `<div class="facts"><span>${esc(k.runs || "")}</span><span>${esc(k.acts || "")}</span>` +
      (k.limit ? `<span>Limit <b>${esc(k.limit)}</b></span>` : "") + (fires ? `<span>${fires}</span>` : "") +
      (c.fires ? `<span>Fired <b>${esc(c.fires)}×</b></span>` : "") + `</div>` +
      (c.state === "armed" ? `<button type="button" class="btn sm" data-act="pause">${ic("pause")}Pause</button>` : "") +
      (c.state === "paused" ? `<button type="button" class="btn sm" data-act="resume">${ic("play")}Resume</button>` : "") +
      `<button type="button" class="btn sm ghost danger" data-act="delete">${ic("trash")}Delete</button>` +
      (o.open !== false ? `<button type="button" class="btn sm" data-open="${esc(c.id)}">Open</button>` : "");
    el.appendChild(foot);
    el.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-act]");
      if (!b) return;
      const act = b.dataset.act;
      if (act === "delete" && !b.dataset.sure) { b.dataset.sure = "1"; b.lastChild.textContent = "Delete for good"; return; }
      b.disabled = true;
      try {
        const got = await call(`/plays/${c.id}`, { action: act });
        if (got.deleted) { el.innerHTML = `<div class="strat-head"><p>Deleted. It will not fire again.</p></div>`; }
        else if (got.play) { const fresh = strategy(got.play, o); el.replaceWith(fresh); }
        window.dispatchEvent(new CustomEvent("plays:changed"));
      } catch (err) { b.disabled = false; toast(err.message); }
    });
    return el;
  }

  /** The grid tile: name, the sentence, a small picture of its canvas, two
   *  figures. */
  function tile(c) {
    const st = STATE[c.state] || [c.state, ""];
    const syms = [...new Set([c.symbol, (c.do || {}).symbol, ...Object.keys((c.do || {}).targets || {})].filter(Boolean))].slice(0, 3);
    const sig = (c.measures || [])[0] ? c.measures[0].title : c.on ? "Filings and news" : `${c.symbol} price`;
    const trig = (c.conditions || [])[0] ? c.conditions[0].text : c.on ? "An item matches" : c.lifespan === "policy" ? "Weekly, or on drift" : "Now";
    const act = c.action ? (c.action.text || `${c.action.side} ${c.action.size || ""} ${c.action.symbol || ""}`) : "Alert you";
    const h = c.history || {};
    return `<button type="button" class="sc" data-open="${esc(c.id)}"><div class="top2"><span class="pill ${st[1]}"><i class="dot"></i>${esc(st[0])}</span>` +
      `<b>${esc(c.title)}</b><span class="logos">${syms.map((s) => logo(s, "sm")).join("")}</span></div>` +
      `<div class="desc">${esc(c.summary || "")}</div>` +
      `<div class="mini dots"><svg viewBox="0 0 300 120" preserveAspectRatio="none"><path d="M88 40 H110 Q118 40 118 48 V56 Q118 62 126 62 H150"/><path d="M190 62 H205 Q212 62 212 54 V42 Q212 36 220 36 H232"/></svg>` +
      `<div class="mn" style="left:8px;top:22px"><b>Signal</b>${esc(sig)}</div>` +
      `<div class="mn" style="left:36%;top:46px"><b>Trigger</b>${esc(trig)}</div>` +
      `<div class="mn" style="right:8px;top:20px"><b>Action</b>${esc(act)}</div></div>` +
      `<div class="stats"><div><span>${c.fires ? "Fired" : "On history"}</span><b>${c.fires ? `${esc(c.fires)}×` : h.bars && !c.on && (c.conditions || []).length ? `${esc(h.fires)}×` : "–"}</b></div>` +
      `<div><span>${c.spent_inr ? "Put in" : "Runs"}</span><b>${c.spent_inr ? esc(inr(c.spent_inr)) : esc(c.lifespan === "policy" ? "Policy" : c.lifespan === "now" ? "Once" : c.lifespan === "standing" ? "Every time" : "Once")}</b></div></div></button>`;
  }

  /* ── the inbox: Catalyst's alert ─────────────────────────────────────── */
  const HEAD_IC = { fired: "bell", proposed: "clock", filled: "checkc", rejected: "ban", exited: "exit", note: "refresh" };
  function alertCard(ev) {
    const a = ev.alert || { label: ev.title, line: [] };
    const open = ev.kind === "proposed" && ev.status === "open";
    const t = (ev.body || {}).ticket || {};
    const line = (a.line || []).map((x) => x.b != null ? `<b>${esc(x.b)}</b>` : esc(x.t)).join("");
    const lead = a.symbol ? logo(a.symbol, "sm") : `<span class="dash"></span>`;
    const legs = (t.legs || []).length > 1 ? `<div class="legs-mini">${t.legs.slice(0, 8).map((lg) => {
      const side = lg.side === "BUY" || lg.side === "long" ? "buy" : "sell";
      const w = lg.side === "BUY" ? "Buy" : lg.side === "SELL" ? "Sell" : lg.side === "long" ? "Long" : "Short";
      return `<span class="chip ${side}">${w} ${esc(num(lg.qty, 4))} ${esc(lg.symbol)}</span>`;
    }).join("")}</div>` : "";
    const src = a.source ? `<div class="srccard"><div class="who">${a.source.kind === "filings" ? logo(a.symbol, "sm") : outlet(a.source.who)}` +
      `<span>${esc(a.source.who)}${a.source.kind === "filings" ? " · NSE filing" : ""}</span></div>` +
      `<div class="hl">${esc(a.source.title)}</div>` + (a.source.quote ? `<q>${esc(a.source.quote)}</q>` : "") +
      (a.source.url ? `<a class="rm" href="${esc(a.source.url)}" target="_blank" rel="noopener">Read more</a>` : "") + `</div>` : "";
    return `<article class="alert${open ? " wait" : ""}" data-ev="${esc(ev.id)}">` +
      `<div class="ah">${ic(HEAD_IC[ev.kind] || "bell")}<h4>${esc(a.label)}</h4><span class="of">· ${esc(ev.play || ev.title)}</span>` +
      `<span class="grow"></span>${a.late ? `<span class="pill wait">Missed while offline</span>` : ""}` +
      `<button type="button" class="x" data-x="${esc(ev.id)}" aria-label="${open ? "Dismiss" : "Hide"}">${ic("x")}</button></div>` +
      (src || (line ? `<div class="line">${lead}<span>${line}</span></div>` : "")) + (src && line ? `<div class="line">${lead}<span>${line}</span></div>` : "") +
      legs +
      `<div class="af"><button type="button" class="see" data-open="${esc(ev.play_id)}">${ic("eye")}See more</button>` +
      (open ? `<button type="button" class="btn sm dark" data-evact="confirm">Confirm on paper</button>` +
              `<button type="button" class="btn sm" data-evact="dismiss">Dismiss</button>` : "") +
      `<span class="grow"></span><time>${esc(ago(ev.created))}</time></div></article>`;
  }

  /* ── markdown: the subset the model writes ───────────────────────────── */
  function inline(s) {
    return s.replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }
  function md(src) {
    const lines = esc(src).replace(/\r/g, "").split("\n");
    const out = [];
    let para = [], list = null, tbl = [], code = null;
    const fP = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
    const fL = () => { if (list) { out.push(`<${list.tag}>${list.items.map((x) => `<li>${inline(x)}</li>`).join("")}</${list.tag}>`); list = null; } };
    const fT = () => {
      if (!tbl.length) return;
      const rows = tbl.filter((r) => !/^\|?\s*:?-{2,}/.test(r)).map((r) => r.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim())));
      const [head, ...rest] = rows;
      out.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rest.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      tbl = [];
    };
    const flush = () => { fP(); fL(); fT(); };
    for (const raw of lines) {
      const t = raw.trim();
      if (code !== null) { if (t.startsWith("```")) { out.push(`<pre><code>${code.join("\n")}</code></pre>`); code = null; } else code.push(raw); continue; }
      if (t.startsWith("```")) { flush(); code = []; continue; }
      if (!t) { flush(); continue; }
      if (t.startsWith("|")) { fP(); fL(); tbl.push(t); continue; }
      fT();
      const h = t.match(/^(#{1,3})\s+(.*)$/);
      if (h) { flush(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }
      const q = t.match(/^&gt;\s?(.*)$/);
      if (q) { flush(); out.push(`<blockquote>${inline(q[1])}</blockquote>`); continue; }
      const ul = t.match(/^[-*•]\s+(.*)$/), ol = t.match(/^\d+[.)]\s+(.*)$/);
      if (ul || ol) { fP(); const tag = ul ? "ul" : "ol"; if (!list || list.tag !== tag) { fL(); list = { tag, items: [] }; } list.items.push((ul || ol)[1]); continue; }
      if (list && /^\s{2,}/.test(raw)) { list.items[list.items.length - 1] += " " + t; continue; }
      fL(); para.push(t);
    }
    if (code !== null) out.push(`<pre><code>${code.join("\n")}</code></pre>`);
    flush();
    return out.join("");
  }

  function toast(msg) {
    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("show"), 3200);
  }

  return { API, esc, call, num, inr, signed, bigMoney, bigNum, ago, day, dayY, ic, logo, name, outlet, loadLogos,
           lineChart, priceBlock, bars, canvas, strategy, tile, alertCard, md, toast, COLORS };
})();
