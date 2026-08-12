/* Charto Journal — calm, full-workspace trade review. */
"use strict";

const Journal = (() => {
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
  const el = (id) => document.getElementById(id);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const ico = (n, c="sm") => Icons.svg(n, c);
  let data = { trades: [], playbooks: [], overview: {} };
  let view = "overview", query = "", side = "all", period = "all", active = null;
  const views = [
    ["overview", "Overview", "indicators"], ["trades", "Trades", "candles"],
    ["calendar", "Calendar", "clock"], ["playbooks", "Playbooks", "fileText"],
    ["reviews", "Reviews", "check"],
  ];

  async function call(path, body) {
    const r = await fetch(API + path, {
      method: body === undefined ? "GET" : "POST",
      headers: Auth.headers(body === undefined ? {} : {"Content-Type":"application/json"}),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let out = {}; try { out = await r.json(); } catch {}
    if (!r.ok) throw new Error(out.error || `Request failed (${r.status})`);
    return out;
  }
  function toast(msg) {
    const t = el("journalToast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove("show"), 2800);
  }
  function money(v, currency="INR") {
    if (v == null) return "—";
    return new Intl.NumberFormat("en-IN", {style:"currency", currency, maximumFractionDigits:0}).format(v);
  }
  const date = (ts, full=false) => ts ? new Intl.DateTimeFormat("en-IN", full
    ? {day:"2-digit",month:"short",year:"numeric",hour:"2-digit",minute:"2-digit"}
    : {day:"2-digit",month:"short",year:"2-digit"}).format(new Date(ts*1000)) : "—";
  function filtered() {
    const cut = period === "30" ? Date.now()/1000 - 30*86400 : period === "90" ? Date.now()/1000 - 90*86400 : 0;
    return data.trades.filter((t) => (!query || `${t.symbol} ${t.tags.join(" ")} ${JSON.stringify(t.plan)}`.toLowerCase().includes(query.toLowerCase()))
      && (side === "all" || t.side === side) && t.opened_at >= cut);
  }
  function nav() {
    el("journalNav").innerHTML = views.map(([id,label,icon]) =>
      `<button type="button" data-view="${id}" class="${view===id?"active":""}">${ico(icon)}<span>${label}</span></button>`).join("");
  }
  function controls(title, desc, extra="", showTrade=true) {
    return `<div class="j-shell"><div class="j-head"><div><div class="j-eyebrow">Journal / ${esc(title)}</div><h1>${esc(title)}</h1><p>${esc(desc)}</p></div>`+
      `<div class="j-actions">${extra}<button class="j-action" data-import>${ico("download")}Import CSV</button>${showTrade?`<button class="j-action primary" data-new>${ico("plus")}Add trade</button>`:""}</div></div>`;
  }
  function filters() {
    return `<div class="j-filters"><div class="j-search">${ico("search")}<input data-search value="${esc(query)}" placeholder="Search symbols, tags, notes…" aria-label="Search journal"></div>`+
      `<select class="j-select" data-side aria-label="Filter by side"><option value="all">All sides</option><option value="long" ${side==="long"?"selected":""}>Long</option><option value="short" ${side==="short"?"selected":""}>Short</option></select>`+
      `<select class="j-select" data-period aria-label="Filter by period"><option value="all">All time</option><option value="30" ${period==="30"?"selected":""}>Last 30 days</option><option value="90" ${period==="90"?"selected":""}>Last 90 days</option></select></div>`;
  }
  function metrics(o=data.overview) {
    const adherence = o.adherence == null ? "—" : `${o.adherence}%`;
    return `<div class="j-metrics">
      <div class="j-metric"><label>Net P&amp;L</label><b>${money(o.net_pnl||0)}</b><small>${o.closed||0} closed trades</small></div>
      <div class="j-metric"><label>Expectancy</label><b>${o.expectancy_r==null?"—":`${o.expectancy_r}R`}</b><small>${o.expectancy_r==null?"Add initial risk to calculate":"Average outcome per trade"}</small></div>
      <div class="j-metric"><label>Profit factor</label><b>${o.profit_factor==null?"—":o.profit_factor}</b><small>${o.win_rate==null?"No closed trades":`${o.win_rate}% win rate`}</small></div>
      <div class="j-metric"><label>Rule adherence</label><b>${adherence}</b><small>${o.reviewed||0} reviewed of ${o.count||0}</small></div>
    </div>`;
  }
  function equity(trades) {
    const closed = [...trades].filter(t=>t.net_pnl!=null).sort((a,b)=>a.closed_at-b.closed_at);
    if (!closed.length) return `<div class="j-empty">Your equity story begins after the first closed trade.<br>Nothing is inferred from an open position.</div>`;
    let sum=0; const vals=[0,...closed.map(t=>sum+=t.net_pnl)], min=Math.min(0,...vals), max=Math.max(0,...vals), span=max-min||1;
    const pts=vals.map((v,i)=>`${(i/(vals.length-1||1)*100).toFixed(2)},${(92-(v-min)/span*78).toFixed(2)}`);
    const zero=(92-(0-min)/span*78).toFixed(2);
    return `<div class="j-equity"><svg viewBox="0 0 100 100" preserveAspectRatio="none"><defs><linearGradient id="jArea" x1="0" y1="0" x2="0" y2="1"><stop stop-color="var(--primary)"/><stop offset="1" stop-color="var(--primary)" stop-opacity="0"/></linearGradient></defs><path class="zero" d="M0 ${zero}H100"/><path class="area" d="M${pts.join(" L")} L100 100 L0 100Z"/><path class="line" d="M${pts.join(" L")}"/></svg></div>`;
  }
  function miniCalendar(trades, days=28) {
    const by={}; trades.forEach(t=>{if(t.closed_at&&t.net_pnl!=null){const k=new Date(t.closed_at*1000).toDateString();by[k]=(by[k]||0)+t.net_pnl;}});
    const now=new Date(), cells=[];
    for(let i=days-1;i>=0;i--){const d=new Date(now);d.setDate(now.getDate()-i);const p=by[d.toDateString()];cells.push(`<div class="j-day ${p>0?"up":p<0?"down":""}"><span>${d.getDate()}</span><b>${p==null?"":money(p)}</b></div>`)}
    return `<div class="j-calendar">${cells.join("")}</div>`;
  }
  function rows(trades, limit=999) {
    if (!trades.length) return `<tr><td colspan="7"><div class="j-empty">No trades match this view.<br>Try clearing a filter or add one manually.</div></td></tr>`;
    return trades.slice(0,limit).map(t=>`<tr data-trade="${t.id}"><td><span class="j-symbol">${esc(t.symbol)}</span></td><td><span class="j-side-pill">${t.side}</span></td><td>${date(t.opened_at)}</td><td>${t.quantity}</td><td>${money(t.entry_price,t.currency)}</td><td class="j-pnl ${t.net_pnl>0?"up":t.net_pnl<0?"down":""}">${money(t.net_pnl,t.currency)}</td><td><span class="j-review-dot ${t.reviewed?"done":""}"></span>${t.tags.slice(0,2).map(x=>`<span class="j-tag">${esc(x)}</span>`).join("")|| (t.reviewed?"Reviewed":"Open review")}</td></tr>`).join("");
  }
  function table(trades, limit) {
    return `<div class="j-table-card"><table class="j-table"><thead><tr><th>Instrument</th><th>Side</th><th>Opened</th><th>Qty</th><th>Entry</th><th>Net P&amp;L</th><th>Review</th></tr></thead><tbody>${rows(trades,limit)}</tbody></table></div>`;
  }
  function overview() {
    const ts=filtered();
    return controls("Trading journal","Your performance and process, in one quiet view.") + filters()+metrics()+
      `<div class="j-grid"><div class="j-card"><div class="j-card-head"><strong>Equity curve</strong><span>Net of recorded fees</span></div>${equity(ts)}</div>`+
      `<div class="j-card"><div class="j-card-head"><strong>Last 28 days</strong><span>Daily net P&amp;L</span></div>${miniCalendar(ts)}</div></div>`+
      `<div style="height:12px"></div><div class="j-card-head" style="padding:0 2px;border:0"><strong>Recent trades</strong><span>${ts.length} recorded</span></div>${table(ts,6)}</div>`;
  }
  function tradesView() { const ts=filtered(); return controls("Trades","Every execution fact beside the decisions that shaped it.")+filters()+table(ts)+`</div>`; }
  function calendarView() { const ts=filtered(); return controls("Calendar","See outcomes in time without turning the month into a scoreboard.")+filters()+`<div class="j-card"><div class="j-card-head"><strong>Daily outcomes</strong><span>Colour and value carry the result</span></div>${miniCalendar(ts,70)}</div></div>`; }
  function booksView() {
    const cards=data.playbooks.map(b=>`<article class="j-book" data-book="${b.id}"><div class="mark"></div><h3>${esc(b.name)}</h3><p>${esc(b.description||"A flexible setup. Add the conditions that make it yours.")}</p><small>${Object.keys(b.spec||{}).length} fields · editable</small></article>`).join("");
    return controls("Playbooks","Define what a good trade looks like before judging the result.",`<button class="j-action primary" data-new-book>${ico("plus")}New playbook</button>`,false)+`<div class="j-books">${cards||`<button class="j-book" data-new-book><div class="mark"></div><h3>Create your first playbook</h3><p>Name the setup, then add any checklist or fields that matter to you.</p></button>`}</div></div>`;
  }
  function reviewsView(){const ts=filtered().filter(t=>!t.reviewed);return controls("Reviews","A short queue for turning a trade into a reusable lesson.")+filters()+`<div class="j-card-head" style="padding:0 2px;border:0"><strong>Needs reflection</strong><span>${ts.length} remaining</span></div>${table(ts)}</div>`;}
  function render() {
    nav(); const root=el("journalMain");
    root.innerHTML = ({overview:overview,trades:tradesView,calendar:calendarView,playbooks:booksView,reviews:reviewsView}[view]||overview)();
  }
  async function load() {
    if (!Auth.user) { renderSignedOut(); return; }
    el("journalMain").innerHTML=`<div class="j-shell"><div class="j-empty">Opening your journal…</div></div>`;
    try { data=await call("/journal/bootstrap"); render(); }
    catch(e){el("journalMain").innerHTML=`<div class="j-shell"><div class="j-empty">${esc(e.message)}<br><button class="j-action" data-retry>Try again</button></div></div>`;}
  }
  function renderSignedOut(){nav();el("journalMain").innerHTML=controls("Trading journal","Your trade history belongs to your account.")+`<div class="j-card"><div class="j-empty"><div><strong style="display:block;color:var(--foreground);font-size:16px;margin-bottom:7px">Sign in to begin</strong>Your journal is durable and private, so it is never stored as anonymous browser data.<br><button class="j-action primary" data-signin style="margin:18px auto 0">Sign in</button></div></div></div></div>`;}
  function open() { document.body.classList.add("journal-open"); load(); requestAnimationFrame(()=>el("journalMain").focus()); }
  function close() { closeDrawer(); document.body.classList.remove("journal-open"); }

  /* ── compact chart sheet ───────────────────────────────────────────────
     Summary and capture live here; interpretation stays in the full journal. */
  let quickTab="summary", quickBusy=false, quickSort={key:"opened_at",dir:-1};
  function quickDate(ts){
    if(!ts)return `<span class="jq-date empty">—</span>`;
    const d=new Date(ts*1000);
    const day=new Intl.DateTimeFormat("en-IN",{day:"2-digit",month:"short",year:"2-digit"}).format(d);
    const time=new Intl.DateTimeFormat("en-IN",{hour:"2-digit",minute:"2-digit",hour12:false}).format(d);
    return `<span class="jq-date"><b>${day}</b><small>${time}</small></span>`;
  }
  function quickMetrics(){const o=data.overview||{};return `<div class="jq-metrics">
    <div class="jq-metric"><span>Net P&amp;L</span><b class="${(o.net_pnl||0)>=0?"j-pnl up":"j-pnl down"}">${money(o.net_pnl||0)}</b></div>
    <div class="jq-metric"><span>Expectancy</span><b>${o.expectancy_r==null?"—":`${o.expectancy_r}R`}</b></div>
    <div class="jq-metric"><span>Profit factor</span><b>${o.profit_factor==null?"—":o.profit_factor}</b></div>
    <div class="jq-metric"><span>Win rate</span><b>${o.win_rate==null?"—":`${o.win_rate}%`}</b></div>
    <div class="jq-metric"><span>Rule adherence</span><b>${o.adherence==null?"—":`${o.adherence}%`}</b></div>
    <div class="jq-metric"><span>Reviewed</span><b>${o.reviewed||0} / ${o.count||0}</b></div></div>`}
  function quickRows(){
    const dir=quickSort.dir,key=quickSort.key,rows=[...data.trades].sort((a,b)=>{const av=a[key],bv=b[key];if(av==null)return 1;if(bv==null)return-1;return (av>bv?1:av<bv?-1:0)*dir});
    if(!rows.length)return `<div class="jq-empty">No trades journalled yet.<br>Capture the first one without leaving your chart.</div>`;
    const book=(id)=>data.playbooks.find(b=>b.id===id)?.name||"—";
    const th=(label,k,cls="")=>`<th class="${cls}" data-qsort="${k}" aria-sort="${quickSort.key===k?(quickSort.dir>0?"ascending":"descending"):"none"}">${label}${quickSort.key===k?(quickSort.dir>0?" ↑":" ↓"):""}</th>`;
    return `<div class="jq-table-wrap"><table class="jq-table"><colgroup>${["symbol","side","date","date","qty","money","money","fees","risk","pnl","r","playbook","tags","review"].map(c=>`<col class="jq-col-${c}">`).join("")}</colgroup><thead><tr>${th("Instrument","symbol")}${th("Side","side")}${th("Opened","opened_at")}${th("Closed","closed_at")}${th("Qty","quantity")}${th("Entry","entry_price")}${th("Exit","exit_price")}${th("Fees","fees")}${th("Initial risk","initial_risk")}${th("Net P&L","net_pnl")}${th("R multiple","r_multiple")}${th("Playbook","playbook_id","jq-review")}${th("Tags","tags","jq-review")}${th("Review","reviewed","jq-review")}</tr></thead><tbody>${rows.map(t=>`<tr data-quick-trade="${t.id}"><td class="jq-symbol">${esc(t.symbol)}</td><td><span class="j-side-pill">${t.side}</span></td><td>${quickDate(t.opened_at)}</td><td>${quickDate(t.closed_at)}</td><td>${t.quantity}</td><td>${money(t.entry_price,t.currency)}</td><td>${money(t.exit_price,t.currency)}</td><td>${money(t.fees,t.currency)}</td><td>${money(t.initial_risk,t.currency)}</td><td class="j-pnl ${t.net_pnl>0?"up":t.net_pnl<0?"down":""}">${money(t.net_pnl,t.currency)}</td><td>${t.r_multiple==null?"—":`${t.r_multiple}R`}</td><td class="jq-review">${esc(book(t.playbook_id))}</td><td class="jq-review">${t.tags.map(x=>esc(x)).join(" · ")||"—"}</td><td class="jq-review"><span class="j-review-dot ${t.reviewed?"done":""}"></span>${t.reviewed?"Reviewed":"Pending"}</td></tr>`).join("")}</tbody></table></div>`}
  function quickForm(){const sym=window.__charto?.symbol||"";return `<form class="jq-form" id="journalQuickForm">
    <div class="jq-field"><label for="jq-symbol">Instrument *</label><input id="jq-symbol" name="symbol" value="${esc(sym)}" autocomplete="off" required></div>
    <div class="jq-field"><label for="jq-side">Side *</label><select id="jq-side" name="side"><option value="long">Long</option><option value="short">Short</option></select></div>
    <div class="jq-field"><label for="jq-qty">Quantity *</label><input id="jq-qty" name="quantity" type="number" min="0" step="any" inputmode="decimal" required></div>
    <div class="jq-field"><label for="jq-entry">Entry price *</label><input id="jq-entry" name="entry_price" type="number" min="0" step="any" inputmode="decimal" required></div>
    <div class="jq-field"><label for="jq-exit">Exit price</label><input id="jq-exit" name="exit_price" type="number" min="0" step="any" inputmode="decimal"></div>
    <div class="jq-field"><label for="jq-fees">Fees</label><input id="jq-fees" name="fees" type="number" min="0" step="any" value="0" inputmode="decimal"></div>
    <div class="jq-field"><label for="jq-risk">Initial risk</label><input id="jq-risk" name="initial_risk" type="number" min="0" step="any" inputmode="decimal"></div>
    <button class="jq-submit" type="submit" ${quickBusy?"disabled":""}>${quickBusy?"Saving…":"Add trade"}</button></form>
    <div class="jq-note">${ico("fileText","xs")}Exit is optional for an open trade. Add thesis, playbook and review in Full journal.</div>`}
  const grip=()=>`<div class="jq-grip" data-jq-grip aria-label="Resize journal pane"></div>`;
  function renderQuick(){const q=el("journalQuick");q.innerHTML=grip()+`<div class="jq-head"><div class="jq-title"><strong>Journal</strong><span>${data.overview?.count||0} trades · process over outcome</span></div><div class="jq-tabs" role="tablist"><button class="jq-tab ${quickTab==="summary"?"active":""}" data-qtab="summary" role="tab">Trade log</button><button class="jq-tab ${quickTab==="new"?"active":""}" data-qtab="new" role="tab">New trade</button></div><span class="spacer"></span><button class="jq-full" data-full-journal>${ico("externalLink")}<span>Full journal</span></button><button class="jq-close" data-close-quick aria-label="Close journal">${ico("x")}</button></div><div class="jq-body">${quickTab==="summary"?quickMetrics()+quickRows():quickForm()}</div>`}
  async function loadQuick(){const q=el("journalQuick");if(!Auth.user){q.innerHTML=grip()+`<div class="jq-head"><div class="jq-title"><strong>Journal</strong><span>Your private trading record</span></div><span class="spacer"></span><button class="jq-close" data-close-quick aria-label="Close journal">${ico("x")}</button></div><div class="jq-body"><div class="jq-empty"><div>Sign in to keep trades private and durable.<br><button class="j-action primary" data-signin style="margin:12px auto 0">Sign in</button></div></div></div>`;return}q.innerHTML=grip()+`<div class="jq-empty">Opening journal…</div>`;try{data=await call("/journal/bootstrap");renderQuick()}catch(e){q.innerHTML=grip()+`<div class="jq-empty">${esc(e.message)}<br><button class="j-action" data-quick-retry>Try again</button></div>`}}
  function toggleQuick(force){const q=el("journalQuick"),stage=el("stage"),on=force===undefined?!q.classList.contains("open"):force;q.classList.toggle("open",on);stage.classList.toggle("journal-pane-open",on);q.setAttribute("aria-hidden",String(!on));el("journalBtn").classList.toggle("active",on);if(on)loadQuick()}
  async function saveQuick(form){if(quickBusy)return;const f=new FormData(form),v=(k)=>String(f.get(k)||"").trim(),n=(k)=>v(k)===""?null:Number(v(k));if(!v("symbol")||!n("quantity")||!n("entry_price")){toast("Instrument, quantity and entry price are required");return}quickBusy=true;renderQuick();try{await call("/journal/trades",{symbol:v("symbol"),side:v("side"),opened_at:Math.floor(Date.now()/1000),closed_at:n("exit_price")==null?null:Math.floor(Date.now()/1000),quantity:n("quantity"),entry_price:n("entry_price"),exit_price:n("exit_price"),fees:n("fees")||0,initial_risk:n("initial_risk"),status:n("exit_price")==null?"open":"closed",source:"manual"});quickTab="summary";await loadQuick();toast("Trade added to journal")}catch(e){toast(e.message)}finally{quickBusy=false}}
  async function quickTrade(id){toggleQuick(false);open();await load();const t=data.trades.find(x=>x.id===Number(id));if(t)openTrade(t)}
  function field(label,name,value,type="text",wide=false){return `<div class="j-field ${wide?"wide":""}"><label for="jf-${name}">${label}</label><input id="jf-${name}" name="${name}" type="${type}" value="${esc(value)}"></div>`;}
  function openTrade(t) {
    active=t||null; const isNew=!t, p=t?.plan||{}, r=t?.review||{};
    const dt=(ts)=>ts?new Date(ts*1000).toISOString().slice(0,16):new Date().toISOString().slice(0,16);
    el("journalDrawer").innerHTML=`<div class="j-drawer-head"><div class="j-drawer-title"><b>${isNew?"New trade":esc(t.symbol)}</b><span>${isNew?"Record facts first. Meaning can come later.":`${esc(t.side)} · ${date(t.opened_at,true)}`}</span></div><button class="btn icon" data-close aria-label="Close">${ico("x")}</button></div><form class="j-drawer-body" id="journalForm">
      <section class="j-section"><h3>Execution facts</h3><div class="j-form-grid">
      ${field("Instrument","symbol",t?.symbol||window.__charto?.symbol||"")}
      <div class="j-field"><label for="jf-side">Side</label><select id="jf-side" name="side"><option value="long" ${t?.side!=="short"?"selected":""}>Long</option><option value="short" ${t?.side==="short"?"selected":""}>Short</option></select></div>
      ${field("Opened","opened_at",dt(t?.opened_at),"datetime-local")}${field("Closed","closed_at",dt(t?.closed_at),"datetime-local")}
      ${field("Quantity","quantity",t?.quantity||"","number")}${field("Entry price","entry_price",t?.entry_price||"","number")}
      ${field("Exit price","exit_price",t?.exit_price||"","number")}${field("Fees","fees",t?.fees||0,"number")}
      ${field("Initial risk (money)","initial_risk",t?.initial_risk||"","number")}
      <div class="j-field"><label for="jf-playbook">Playbook</label><select id="jf-playbook" name="playbook_id"><option value="">None</option>${data.playbooks.map(b=>`<option value="${b.id}" ${t?.playbook_id===b.id?"selected":""}>${esc(b.name)}</option>`).join("")}</select></div></div></section>
      <section class="j-section"><h3>Plan · yours to shape</h3><div class="j-form-grid">${field("Thesis","thesis",p.thesis||"","text",true)}${field("Planned stop","stop",p.stop||"","number")}${field("Planned target","target",p.target||"","number")}${field("Tags · comma separated","tags",(t?.tags||[]).join(", "),"text",true)}</div></section>
      <section class="j-section"><h3>Review</h3><div class="j-form-grid"><div class="j-field"><label for="jf-adherence">Followed the plan?</label><select id="jf-adherence" name="adherence"><option value="">Not reviewed</option><option value="yes" ${r.adherence===true?"selected":""}>Yes</option><option value="no" ${r.adherence===false?"selected":""}>No</option></select></div>${field("Emotion","emotion",r.emotion||"")}<div class="j-field wide"><label for="jf-lesson">Lesson</label><textarea id="jf-lesson" name="lesson">${esc(r.lesson||"")}</textarea></div></div></section></form>
      <div class="j-drawer-foot"><div>${isNew?"":`<button class="j-action" data-ask>${ico("chat")}Review with chat</button>`}</div><div style="display:flex;gap:8px">${isNew?"":`<button class="j-action" data-delete>Delete</button>`}<button class="j-action primary" data-save>${isNew?"Add trade":"Save changes"}</button></div></div>`;
    const d=el("journalDrawer");d.classList.add("open");d.setAttribute("aria-hidden","false");
  }
  function payload() {
    const f=new FormData(el("journalForm")), val=(k)=>String(f.get(k)||"").trim(), num=(k)=>val(k)===""?null:Number(val(k));
    const adherence=val("adherence"); return {symbol:val("symbol"),side:val("side"),opened_at:Math.floor(new Date(val("opened_at")).getTime()/1000),closed_at:val("closed_at")?Math.floor(new Date(val("closed_at")).getTime()/1000):null,quantity:num("quantity"),entry_price:num("entry_price"),exit_price:num("exit_price"),fees:num("fees")||0,initial_risk:num("initial_risk"),playbook_id:num("playbook_id"),status:num("exit_price")==null?"open":"closed",tags:val("tags").split(",").map(x=>x.trim()).filter(Boolean),plan:{thesis:val("thesis"),stop:num("stop"),target:num("target")},review:{...(adherence?{adherence:adherence==="yes"}:{}),...(val("emotion")?{emotion:val("emotion")} :{}),...(val("lesson")?{lesson:val("lesson")}:{})}};
  }
  function closeDrawer(){const d=el("journalDrawer");d.classList.remove("open");d.setAttribute("aria-hidden","true");active=null;}
  async function save(){try{const out=await call(active?`/journal/trades/${active.id}`:"/journal/trades",payload());const i=data.trades.findIndex(t=>t.id===out.trade.id);if(i<0)data.trades.unshift(out.trade);else data.trades[i]=out.trade;data.overview=(await call("/journal/bootstrap")).overview;closeDrawer();render();toast(active?"Trade updated":"Trade added");}catch(e){toast(e.message)}}
  async function remove(){if(!active||!confirm(`Delete ${active.symbol} from your journal?`))return;try{await call(`/journal/trades/${active.id}`,{delete:true});data.trades=data.trades.filter(t=>t.id!==active.id);closeDrawer();await load();toast("Trade deleted");}catch(e){toast(e.message)}}
  function ask(){if(!active)return;document.dispatchEvent(new CustomEvent("charto:journal-chat",{detail:{trade:active}}));close();toast("Trade attached to chat");}
  function openBook(b){
    active={kind:"playbook",value:b||null}; const spec=b?.spec||{};
    el("journalDrawer").innerHTML=`<div class="j-drawer-head"><div class="j-drawer-title"><b>${b?"Edit playbook":"New playbook"}</b><span>A living definition of your setup</span></div><button class="btn icon" data-close aria-label="Close">${ico("x")}</button></div>
      <form class="j-drawer-body" id="playbookForm"><section class="j-section"><h3>Identity</h3><div class="j-form-grid">${field("Name","book_name",b?.name||"","text",true)}<div class="j-field wide"><label for="jf-book_desc">What makes this setup yours?</label><textarea id="jf-book_desc" name="book_desc">${esc(b?.description||"")}</textarea></div></div></section>
      <section class="j-section"><h3>Preparation</h3><div class="j-form-grid"><div class="j-field wide"><label for="jf-book_rules">Checklist · one thought per line</label><textarea id="jf-book_rules" name="book_rules">${esc((spec.checklist||[]).join("\n"))}</textarea></div><div class="j-field wide"><label for="jf-book_invalidation">Invalidation</label><textarea id="jf-book_invalidation" name="book_invalidation">${esc(spec.invalidation||"")}</textarea></div></div></section>
      <div class="j-side-note" style="margin:0"><strong>No fixed template.</strong>Chat can add or reshape any extra playbook fields later; these are simply the useful starting points.</div></form>
      <div class="j-drawer-foot"><span></span><button class="j-action primary" data-save-book>Save playbook</button></div>`;
    const d=el("journalDrawer");d.classList.add("open");d.setAttribute("aria-hidden","false");
  }
  async function saveBook(){const f=new FormData(el("playbookForm")),b=active?.value,name=String(f.get("book_name")||"").trim();if(!name){toast("Give the playbook a name");return}const spec={...(b?.spec||{}),checklist:String(f.get("book_rules")||"").split("\n").map(x=>x.trim()).filter(Boolean),invalidation:String(f.get("book_invalidation")||"").trim()};try{await call(b?`/journal/playbooks/${b.id}`:"/journal/playbooks",{name,description:String(f.get("book_desc")||"").trim(),spec});closeDrawer();await load();toast("Playbook saved")}catch(e){toast(e.message)}}
  function importCsv(){const input=document.createElement("input");input.type="file";input.accept=".csv,text/csv";input.onchange=async()=>{const file=input.files?.[0];if(!file)return;const lines=(await file.text()).replace(/\r/g,"").split("\n").filter(Boolean),heads=(lines.shift()||"").split(",").map(x=>x.trim().toLowerCase());let added=0;for(const line of lines){const cells=line.split(",").map(x=>x.trim().replace(/^"|"$/g,"")),row=Object.fromEntries(heads.map((h,i)=>[h,cells[i]]));try{await call("/journal/trades",{symbol:row.symbol||row.instrument,side:(row.side||"long").toLowerCase(),opened_at:Math.floor(new Date(row.opened_at||row.opened||Date.now()).getTime()/1000),closed_at:row.closed_at?Math.floor(new Date(row.closed_at).getTime()/1000):null,quantity:Number(row.quantity||row.qty),entry_price:Number(row.entry_price||row.entry),exit_price:row.exit_price?Number(row.exit_price):null,fees:Number(row.fees||0),initial_risk:row.initial_risk?Number(row.initial_risk):null,source:"csv",external_id:row.id||`${file.name}:${added}:${row.symbol}`});added++}catch{}}await load();toast(`${added} trade${added===1?"":"s"} imported`)};input.click()}

  document.addEventListener("click",(e)=>{
    const qt=e.target.closest("[data-qtab]");if(qt){quickTab=qt.dataset.qtab;renderQuick();return}
    const qs=e.target.closest("[data-qsort]");if(qs){const k=qs.dataset.qsort;quickSort={key:k,dir:quickSort.key===k?-quickSort.dir:-1};renderQuick();return}
    if(e.target.closest("[data-close-quick]")){toggleQuick(false);return}
    if(e.target.closest("[data-full-journal]")){toggleQuick(false);open();return}
    if(e.target.closest("[data-quick-retry]")){loadQuick();return}
    const qtrade=e.target.closest("[data-quick-trade]");if(qtrade){quickTrade(qtrade.dataset.quickTrade);return}
    const v=e.target.closest("[data-view]");if(v){view=v.dataset.view;render();return}
    if(e.target.closest("[data-new]")){openTrade();return}
    if(e.target.closest("[data-new-book]")){openBook();return}
    if(e.target.closest("[data-import]")){importCsv();return}
    const b=e.target.closest("[data-book]");if(b){openBook(data.playbooks.find(x=>x.id===Number(b.dataset.book)));return}
    const tr=e.target.closest("[data-trade]");if(tr){openTrade(data.trades.find(t=>t.id===Number(tr.dataset.trade)));return}
    if(e.target.closest("[data-close]")){closeDrawer();return}
    if(e.target.closest("[data-save]")){save();return}
    if(e.target.closest("[data-save-book]")){saveBook();return}
    if(e.target.closest("[data-delete]")){remove();return}
    if(e.target.closest("[data-ask]")){ask();return}
    if(e.target.closest("[data-retry]")){load();return}
    if(e.target.closest("[data-signin]")){close();el("acctBtn")?.click();return}
  });
  document.addEventListener("submit",(e)=>{if(e.target.id==="journalQuickForm"){e.preventDefault();saveQuick(e.target)}});
  let jqDrag=null;
  document.addEventListener("pointerdown",(e)=>{const h=e.target.closest("[data-jq-grip]");if(!h)return;const q=el("journalQuick");jqDrag={y:e.clientY,h:q.offsetHeight};h.classList.add("dragging");h.setPointerCapture?.(e.pointerId);e.preventDefault()});
  document.addEventListener("pointermove",(e)=>{if(!jqDrag)return;const stage=el("stage"),h=Math.max(150,Math.min(stage.clientHeight*.72,jqDrag.h+(jqDrag.y-e.clientY)));stage.style.setProperty("--journal-pane-h",`${Math.round(h)}px`)});
  document.addEventListener("pointerup",()=>{if(!jqDrag)return;const h=el("journalQuick").offsetHeight;Store.set("journal_pane_h",h);document.querySelector(".jq-grip.dragging")?.classList.remove("dragging");jqDrag=null});
  document.addEventListener("input",(e)=>{if(e.target.matches("[data-search]")){query=e.target.value;clearTimeout(render.timer);render.timer=setTimeout(render,180)}});
  document.addEventListener("change",(e)=>{if(e.target.matches("[data-side]")){side=e.target.value;render()}if(e.target.matches("[data-period]")){period=e.target.value;render()}});
  document.addEventListener("keydown",(e)=>{if(e.key==="Escape"&&el("journalDrawer").classList.contains("open"))closeDrawer()});
  el("journalBtn").innerHTML=ico("fileText")+"<span>Journal</span>";
  el("journalBack").innerHTML=ico("chevronLeft")+"<span>Back to chart</span>";
  el("journalBtn").addEventListener("click",()=>toggleQuick()); el("journalBack").addEventListener("click",close);
  const savedPane=Number(Store.get("journal_pane_h",260));if(savedPane>=150)el("stage").style.setProperty("--journal-pane-h",`${savedPane}px`);
  Auth.onChange(()=>{if(document.body.classList.contains("journal-open"))load();if(el("journalQuick").classList.contains("open"))loadQuick()});
  nav();
  return {open,close,load,toggleQuick,getTrade:(id)=>data.trades.find(t=>t.id===Number(id))};
})();
