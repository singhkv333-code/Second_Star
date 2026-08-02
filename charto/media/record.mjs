/**
 * record.mjs — screen-records a REAL session of the live app.
 *
 * Not stills with ken-burns over them: this drives the running product
 * (dataserver :5174, preview :5173) through a scripted session — search for the
 * instrument, switch interval, ask three questions — and screencasts the whole
 * thing. What lands on camera is the actual UI doing the actual work, including
 * the drawings appearing on the canvas as the model emits them.
 *
 * Viewport is a MacBook-sized 1512×900 at DPR 2 → a 3024×1800 recording. Shown
 * inside a ~1500px browser mockup in a 1920 frame that is a 2× downscale, so
 * the UI reads at true 100% and stays sharp.
 *
 * Cosmetics are VIDEO-ONLY, injected at runtime; the app on disk is untouched.
 *
 * Output: out/rec/session.webm + out/rec/marks.json (elapsed ms per milestone,
 * which is what the edit cuts against — the LLM's 12-18s thinking pauses are
 * cut out later, they are not part of the film).
 */
import puppeteer from "/Users/karanveersingh/.npm/_npx/4d6048b58950d0e2/node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js";
import { mkdirSync, writeFileSync } from "node:fs";

const OUT = new URL("./out/rec/", import.meta.url).pathname;
const BASE = "http://127.0.0.1:5173/";
const CHROME = "/Users/karanveersingh/.cache/puppeteer/chrome/mac_arm-146.0.7680.31/" +
  "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing";
const VIEW = { width: 1512, height: 900, deviceScaleFactor: 2 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Runs before every document: the film's name for the product, minus the
 *  charting library's attribution mark and any transient connection chatter. */
const DRESS = () => {
  const css = document.createElement("style");
  css.textContent = `
    #tv-attr-logo, a[href*="tradingview.com"] { display: none !important; }
    #status, .status, #statusLeft { visibility: hidden !important; }
  `;
  const put = () => (document.head || document.documentElement).appendChild(css);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", put) : put();

  const rename = () => {
    const b = document.querySelector(".brand");
    if (b && !b.dataset.filmed) {
      b.dataset.filmed = "1";
      const dot = b.querySelector(".dot");
      b.textContent = "Pivot";
      if (dot) b.appendChild(dot);
      else { const d = document.createElement("span"); d.className = "dot"; d.textContent = "."; b.appendChild(d); }
    }
    document.title = "BTC-USD — Pivot";
  };
  setInterval(rename, 250);
};

const main = async () => {
  mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    // screencast captures the compositor SURFACE, and the surface is only
    // retina-sized when Chrome is launched with the scale factor — the
    // viewport's own deviceScaleFactor does NOT reach it (measured: viewport
    // DPR 2 alone still recorded 1512x900; this flag gives 3024x1800).
    args: ["--force-device-scale-factor=2", "--hide-scrollbars",
           "--font-render-hinting=none",
           "--disable-features=IsolateOrigins,site-per-process"],
    defaultViewport: VIEW,
  });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(DRESS);
  await page.setViewport(VIEW);

  // fresh session, chat panel sized for a wide frame
  await page.goto(BASE, { waitUntil: "networkidle2", timeout: 90000 });
  await page.evaluate(() => localStorage.setItem("charto_chat_width", "430"));
  await page.goto(BASE, { waitUntil: "networkidle2", timeout: 90000 });
  await sleep(5000); // let the default symbol's bars land before rolling

  const marks = [];
  let t0 = 0;
  const mark = (name) => {
    const at = Date.now() - t0;
    marks.push({ name, at });
    console.log(`  ${String(at).padStart(6)}ms  ${name}`);
  };

  const replies = () =>
    page.evaluate(() => (document.body.innerText.match(/ in \/ /g) || []).length);

  /** Type into the composer at a human cadence, send, wait for the reply. */
  const ask = async (text, label, timeoutMs = 180000) => {
    const before = await replies();
    await page.click("textarea");
    await page.type("textarea", text, { delay: 42 });
    mark(`${label}:typed`);
    await sleep(700);
    await page.keyboard.press("Enter");
    mark(`${label}:sent`);
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      await sleep(1000);
      if ((await replies()) > before) break;
    }
    mark(`${label}:answered`);
    // The dwell IS the usable take — everything before it is the model
    // thinking, which the edit cuts. Long enough to hold a push-in and let a
    // viewer actually read the panel.
    await sleep(7500);
    mark(`${label}:dwell`);
  };

  const recorder = await page.screencast({ path: `${OUT}session.webm` });
  t0 = Date.now();
  mark("roll");

  await sleep(1800);

  // ── the instrument search, done for real ────────────────────────────
  await page.click("#symbolPill");
  mark("search:open");
  await sleep(900);
  await page.type("#symSearch", "BTC", { delay: 130 });
  mark("search:typed");
  await sleep(1600);
  const picked = await page.evaluate(() => {
    const it = document.querySelector('#symList .item[data-sym*="BTC"]') ||
               document.querySelector("#symList .item[data-sym]");
    if (!it) return null;
    it.click();
    return it.getAttribute("data-sym");
  });
  mark(`search:picked:${picked}`);
  await sleep(5200); // bars load
  mark("chart:loaded");

  // ── daily, where the multi-month formations live ────────────────────
  await page.evaluate(() => {
    const btn = [...document.querySelectorAll(".seg button, .seg .item, .seg span")]
      .find((b) => b.textContent.trim() === "D");
    if (btn) btn.click();
  });
  mark("interval:daily");
  await sleep(4200);

  await ask("Mark the chart patterns and the market structure.", "q1");
  await ask("Has this falling wedge actually worked on BTC before? Give me the base rate against a control.", "q2");
  await ask("Why did it move on 29 July?", "q3");

  await sleep(1200);
  mark("end");
  await recorder.stop();
  await browser.close();

  writeFileSync(`${OUT}marks.json`, JSON.stringify({ view: VIEW, marks }, null, 1));
  console.log("\nwrote", OUT + "session.webm", "and marks.json");
};

main().catch((e) => { console.error(e); process.exit(1); });
