// Shared setups, end to end, in a real browser. `node charto/preview/e2e_shares.mjs [outdir]`
//
// Needs: the dataserver on :5174 and serve.py on :5173, the vendored chart
// bundle in place (patch-vendor.py), and Playwright (`npm i playwright`; set
// PLAYWRIGHT_BROWSERS_PATH if the browsers live elsewhere). THEME=light for
// the other theme. The dataserver's store needs TCS daily bars.
//
// An author publishes a TCS desk with drawings, indicators and a conversation;
// a signed-out reader opens it view-only (and nothing of theirs is touched),
// signs up through "Make it mine" and lands on an editable copy with the
// conversation; the author's list shows the view and the copy; an unpublished
// link says so. Screenshots of every surface and hover land in outdir.
//
// locale is pinned to en-IN: a headless container that reports "en-US@posix"
// makes Intl throw inside the chart's paint and the candles vanish, which is
// an artefact of the container, not of the app.
import { chromium } from "playwright";
import fs from "node:fs";

const APP = "http://127.0.0.1:5173";
const API = "http://127.0.0.1:5174";
const OUT = process.argv[2] || "./shots";
fs.mkdirSync(OUT, { recursive: true });
const stamp = Date.now().toString(36);
const log = (...a) => console.log("•", ...a);
const results = [];
const check = (name, ok, detail = "") => { results.push({ name, ok, detail }); console.log(ok ? "PASS" : "FAIL", name, detail); };

async function api(path, body, tok) {
  const r = await fetch(API + path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json", ...(tok ? { Authorization: `Bearer ${tok}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  return [r.status, await r.json()];
}

const author = { email: `asha.${stamp}@pivot.test`, password: "asha-pass-123", name: "Asha Rao" };
const reader = { email: `bhavin.${stamp}@pivot.test`, password: "bhavin-pass-123", name: "Bhavin Shah" };
const [, a] = await api("/auth/signup", author);
const theme = process.env.THEME || "dark";

const browser = await chromium.launch();
const chat = [
  { role: "user", content: "Is TCS building a base above 3,000?", symbol: "TCS", ts: Date.now() - 600000 },
  { role: "assistant", content: "## Read\nTCS has printed **higher lows** for six weeks while volume dried up — the shape of a base, not a breakdown.\n\n| Level | Role in this read |\n|---|---|\n| 3,000 | The shelf the lows keep holding |\n| 3,250 | Where the last two rallies stalled |\n\n- A daily close above the stall zone would confirm it.\n- A close back under the shelf would prove it wrong.\n\nThis is analysis, not financial advice.", ts: Date.now() - 590000 },
  { role: "user", content: "What would invalidate it?", symbol: "TCS", ts: Date.now() - 300000 },
  { role: "assistant", content: "A daily close below the shelf on rising volume. Until then the base is intact.", ts: Date.now() - 290000 },
];

// ── the author ───────────────────────────────────────────────────────────
const ctxA = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: theme, locale: "en-IN" });
await ctxA.addInitScript(([tok, chat, theme]) => {
  if (sessionStorage.getItem("seeded")) return;
  sessionStorage.setItem("seeded", "1");
  localStorage.setItem("charto:auth:token", tok);
  localStorage.setItem("charto:auth:skipped", "1");
  localStorage.setItem("charto_theme", theme);
  localStorage.setItem("charto:TCS:chats", "[]");
  localStorage.setItem("charto:chats", JSON.stringify([{ id: "cdemo", created: Date.now(), updated: Date.now(), turns: chat }]));
  localStorage.setItem("charto:chatid", JSON.stringify("cdemo"));
}, [a.token, chat, theme]);
const pa = await ctxA.newPage();
pa.on("pageerror", (e) => log("author pageerror:", e.message));
await pa.goto(`${APP}/?symbol=TCS`);
await pa.waitForFunction(() => window.__charto && window.__charto.workspace, null, { timeout: 30000 });
await pa.waitForTimeout(1500);                 // let boot's own 5m load finish
await pa.click("#intervalBtn");
await pa.click('#intervalMenu [data-iv="1d"]');
await pa.waitForFunction(() => window.__charto.interval === "1d" && !window.__charto.state.switching);
await pa.waitForTimeout(1200);
await pa.evaluate(async () => {
  const c = window.__charto;
  const bars = c.state.bars;
  const n = bars.length;
  const lows = bars.slice(-60).map((b) => b.low), highs = bars.slice(-60).map((b) => b.high);
  const shelf = Math.min(...lows), stall = Math.max(...highs);
  const a = bars[n - 55], b = bars[n - 8];
  await c.workspace.write({
    drawings: [
      { id: "s1", ref: "D1", type: "hline", pane: "price", pts: [{ t: bars[n - 40].time, v: shelf }], text: "Shelf" },
      { id: "s2", ref: "D2", type: "hline", pane: "price", pts: [{ t: bars[n - 40].time, v: stall }], text: "Stall zone" },
      { id: "s3", ref: "D3", type: "trend", pane: "price", pts: [{ t: a.time, v: a.low }, { t: b.time, v: b.low }] },
      { id: "s4", ref: "D4", type: "rect", pane: "price", pts: [{ t: bars[n - 30].time, v: stall * 0.995 }, { t: bars[n - 1].time, v: stall * 1.01 }] },
    ],
    scene: [], indicators: ["rsi14", "ema20"], vp: null,
  });
});
await pa.waitForTimeout(1200);
await pa.evaluate(() => Layouts.save({ name: "TCS base desk" }));
await pa.waitForTimeout(800);
await pa.screenshot({ path: `${OUT}/01-author-desk.png` });

// header Share button hover
await pa.hover("#shareBtn");
await pa.waitForTimeout(250);
await pa.screenshot({ path: `${OUT}/02-share-button-hover.png`, clip: { x: 760, y: 0, width: 680, height: 60 } });

await pa.click("#shareBtn");
await pa.waitForSelector(".su-publish");
await pa.waitForFunction(() => /[╔╗╚╝║═]/.test(document.querySelector(".su-publish .su-fig").textContent), null, { timeout: 8000 });
await pa.fill("#suTitle", "TCS: a base above the shelf");
await pa.fill("#suNote", "Six weeks of higher lows on falling volume.\nConfirmation: a daily close above the stall zone.\nInvalidation: a close back under the shelf.");
await pa.mouse.move(5, 5);
await pa.waitForTimeout(300);
const dlg = await pa.$(".su-publish");
await dlg.screenshot({ path: `${OUT}/03-publish-dialog.png` });
const manifest = await pa.textContent("#suManifest");
check("manifest counts the desk", /4 drawings/.test(manifest) && /2 indicators/.test(manifest) && /4 turns/.test(manifest), manifest.split("\n").slice(1, -1).join(" | "));
await pa.hover(".su-shot");
await pa.hover(".su-switch[data-sw='allow_copy']");
await pa.hover(".su-shot");
await pa.waitForTimeout(400);
await dlg.screenshot({ path: `${OUT}/04-publish-dialog-hover.png` });
await pa.click(".su-publish [data-ok]");
await pa.waitForSelector(".su-done .su-link");
const link = await pa.inputValue(".su-link");
check("publish returns a view link", /\?symbol=TCS&view=[\w-]{20,}/.test(link), link);
await pa.hover(".su-done [data-copy]");
await pa.waitForTimeout(250);
await (await pa.$(".su-done")).screenshot({ path: `${OUT}/05-published.png` });

// ── the reader, signed out ───────────────────────────────────────────────
const ctxB = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: theme, locale: "en-IN" });
await ctxB.addInitScript((theme) => {
  if (sessionStorage.getItem("seeded")) return;
  sessionStorage.setItem("seeded", "1");
  localStorage.setItem("charto_theme", theme);
  // the reader's OWN work on TCS, which a view must never touch
  localStorage.setItem("charto_drawings_v2_TCS", JSON.stringify([{ id: "mine", ref: "D1", type: "hline", pane: "price", pts: [{ t: 1, v: 1 }] }]));
  localStorage.setItem("charto:TCS:scene", JSON.stringify([]));
  localStorage.setItem("charto:chats", JSON.stringify([{ id: "cmine", created: 1, updated: 1, turns: [{ role: "user", content: "my own question" }] }]));
}, theme);
const pb = await ctxB.newPage();
pb.on("pageerror", (e) => log("reader pageerror:", e.message));
await pb.goto(link);
await pb.waitForSelector("#suCard", { timeout: 30000 });
await pb.waitForFunction(() => /[╔╗╚╝║═]/.test(document.querySelector("#suCard .su-fig").textContent), null, { timeout: 8000 });
await pb.waitForTimeout(1500);
check("no sign-in wall for a reader", !(await pb.isVisible("#authScreen.show, #authScreen.open")), "");
const view = await pb.evaluate(() => ({
  drawings: window.__charto.draw.state.drawings.length,
  locked: window.__charto.draw.state.drawings.every((d) => d.locked),
  inds: [...window.__charto.ind.active.keys()],
  interval: window.__charto.interval,
  bubbles: document.querySelectorAll("#chatMsgs .turn, #chatMsgs .msg, #chatMsgs > div").length,
  composerHidden: getComputedStyle(document.querySelector(".composer-inner")).display === "none",
  title: document.querySelector(".su-bar-title").textContent,
}));
check("reader sees the author's drawings, locked", view.drawings === 4 && view.locked, JSON.stringify(view));
check("reader sees indicators and interval", view.inds.length === 2 && view.interval === "1d", view.inds.join(","));
check("composer is read-only", view.composerHidden, "");
await pb.mouse.move(700, 500);
await pb.screenshot({ path: `${OUT}/06-reader-view.png` });
await pb.hover("#suCard");
await pb.waitForTimeout(900);
await pb.screenshot({ path: `${OUT}/07-reader-card-hover.png`, clip: { x: 1440 - 480, y: 40, width: 480, height: 860 } });
await pb.hover("#suBar [data-act='mine']");
await pb.waitForTimeout(300);
const bar = await pb.$("#suBar");
const bb = await bar.boundingBox();
await pb.screenshot({ path: `${OUT}/08-view-bar-hover.png`, clip: { x: bb.x - 20, y: bb.y - 14, width: bb.width + 40, height: bb.height + 28 } });
const untouched = await pb.evaluate(() => ({
  drawings: localStorage.getItem("charto_drawings_v2_TCS"),
  chats: JSON.parse(localStorage.getItem("charto:chats") || "[]").map((c) => c.id),
}));
check("reader's own TCS drawings untouched by the view", JSON.parse(untouched.drawings).length === 1 && JSON.parse(untouched.drawings)[0].id === "mine", untouched.drawings.slice(0, 60));
check("reader's chat archive untouched by the view", untouched.chats.join() === "cmine", untouched.chats.join());

// Make it mine → sign up → lands on the copy
await pb.click("#suBar [data-act='mine']");
await pb.waitForTimeout(600);
await pb.screenshot({ path: `${OUT}/09-make-it-mine-signin.png` });
await pb.evaluate(() => window.CHARTO_AUTH_OPEN && window.CHARTO_AUTH_OPEN("signup"));
await pb.fill("#authName", reader.name).catch(() => {});
await pb.fill("#authEmail", reader.email);
await pb.fill("#authPassword", reader.password);
await pb.fill("#authConfirm", reader.password).catch(() => {});
await Promise.all([pb.waitForURL(/layout=\d+/, { timeout: 30000 }), pb.click("#authSubmit")]);
await pb.waitForFunction(() => window.__charto && window.__charto.workspace && Layouts.current && Layouts.current.id, null, { timeout: 30000 });
await pb.waitForTimeout(2500);
const copied = await pb.evaluate(() => ({
  url: location.search,
  viewOnly: document.body.classList.contains("view-only"),
  layout: Layouts.current && Layouts.current.name,
  drawings: window.__charto.draw.state.drawings.length,
  anyLocked: window.__charto.draw.state.drawings.some((d) => d.locked),
  chatActive: window.Chat.activeId(),
  chatText: document.getElementById("chatMsgs").innerText.slice(0, 80),
  ownDrawingsKey: JSON.parse(localStorage.getItem("charto_drawings_v2_TCS") || "[]").length,
}));
check("copy opens as an editable layout", !copied.viewOnly && copied.drawings === 4 && /TCS: a base/.test(copied.layout || ""), JSON.stringify(copied));
check("copy carries the conversation", /building a base/.test(copied.chatText), copied.chatText);
await pb.screenshot({ path: `${OUT}/10-reader-copy.png` });
const [, lays] = await api("/layouts", null, await pb.evaluate(() => localStorage.getItem("charto:auth:token")));
check("reader's own desk kept as a layout before the copy", lays.layouts.some((L) => /before copy/.test(L.name)), lays.layouts.map((L) => L.name).join(" | "));

// ── the author's list ───────────────────────────────────────────────────
await pa.keyboard.press("Escape");
await pa.evaluate(() => Setups.manage());
await pa.waitForSelector(".su-manage .su-row");
await pa.waitForTimeout(300);
const stats = await pa.textContent(".su-manage #suTotals");
check("author sees views and copies", /1 views · 1 copies/.test(stats), stats);
await pa.hover(".su-manage .su-row");
await pa.waitForTimeout(300);
await (await pa.$(".su-manage")).screenshot({ path: `${OUT}/11-my-setups-hover.png` });

// ── an unpublished link ─────────────────────────────────────────────────
const tok = new URL(link).searchParams.get("view");
await api("/setups", { token: tok, delete: true }, a.token);
const pc = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: theme, locale: "en-IN" })).newPage();
await pc.addInitScript((theme) => localStorage.setItem("charto_theme", theme), theme);
await pc.goto(link);
await pc.waitForSelector(".su-bar.gone", { timeout: 30000 });
await pc.waitForTimeout(500);
await pc.screenshot({ path: `${OUT}/12-link-closed.png` });
check("closed link says so", await pc.isVisible(".su-bar.gone"), "");

await browser.close();
fs.writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 2));
const bad = results.filter((r) => !r.ok);
console.log(`\n${results.length - bad.length}/${results.length} checks passed`);
process.exit(bad.length ? 1 : 0);
