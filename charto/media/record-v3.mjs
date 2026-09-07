/**
 * record-v3.mjs — the capability reel.
 *
 * v2 recorded a narrative session (search → ask → ask). v3 records a
 * CAPABILITY LIST: five things the product does, each asked cold on an
 * already-loaded chart, so the edit can pair every text card with the answer
 * it promises. No instrument search — picking a symbol is not a selling point.
 *
 * Same rig as record.mjs: a 1512x900 viewport at DPR 2, Chrome launched with
 * --force-device-scale-factor=2 so the screencast surface is 3024x1800.
 * Cosmetics (Pivot wordmark, hidden charting attribution) are injected at
 * runtime; the app on disk is never modified.
 *
 * Output: out/rec3/session.webm + marks.json
 */
import puppeteer from "/Users/karanveersingh/.npm/_npx/4d6048b58950d0e2/node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js";
import { mkdirSync, writeFileSync } from "node:fs";

const OUT = new URL("./out/rec3/", import.meta.url).pathname;
const BASE = "http://127.0.0.1:5173/?symbol=BTC-USD";
const CHROME = "/Users/karanveersingh/.cache/puppeteer/chrome/mac_arm-146.0.7680.31/" +
  "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing";
const VIEW = { width: 1512, height: 900, deviceScaleFactor: 2 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

/** One card, one capability, one answer. The card text in the film is derived
 *  from `promise`; the prompt is what actually gets typed. */
const BEATS = [
  { id: "why",     prompt: "Why did BTC move on 29 July?" },
  { id: "pattern", prompt: "Identify the chart patterns forming here." },
  { id: "trend",   prompt: "Draw the significant trendlines." },
  { id: "rsi",     prompt: "Add RSI and tell me what it is saying." },
  { id: "mtf",     prompt: "What is the trend on each interval?" },
];

const main = async () => {
  mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ["--force-device-scale-factor=2", "--hide-scrollbars",
           "--font-render-hinting=none",
           "--disable-features=IsolateOrigins,site-per-process"],
    defaultViewport: VIEW,
  });
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(DRESS);
  await page.setViewport(VIEW);

  await page.goto(BASE, { waitUntil: "networkidle2", timeout: 90000 });
  await page.evaluate(() => localStorage.setItem("charto_chat_width", "430"));
  await page.goto(BASE, { waitUntil: "networkidle2", timeout: 90000 });
  await sleep(5000);

  // daily — where multi-month structure actually lives
  await page.evaluate(() => {
    const b = [...document.querySelectorAll(".seg button, .seg .item, .seg span")]
      .find((x) => x.textContent.trim() === "D");
    if (b) b.click();
  });
  await sleep(5000);

  const marks = [];
  let t0 = 0;
  const mark = (n) => {
    const at = Date.now() - t0;
    marks.push({ name: n, at });
    console.log(`  ${String(at).padStart(6)}ms  ${n}`);
  };
  const replies = () =>
    page.evaluate(() => (document.body.innerText.match(/ in \/ /g) || []).length);

  const recorder = await page.screencast({ path: `${OUT}session.webm` });
  t0 = Date.now();
  mark("roll");
  await sleep(2500);
  mark("hero");

  for (const b of BEATS) {
    const before = await replies();
    await page.click("textarea");
    await page.type("textarea", b.prompt, { delay: 38 });
    mark(`${b.id}:typed`);
    await sleep(600);
    await page.keyboard.press("Enter");
    mark(`${b.id}:sent`);
    const start = Date.now();
    while (Date.now() - start < 200000) {
      await sleep(1000);
      if ((await replies()) > before) break;
    }
    mark(`${b.id}:answered`);
    await sleep(8000);          // the usable take: long enough to hold a push-in
    mark(`${b.id}:dwell`);
  }

  await sleep(1000);
  mark("end");
  await recorder.stop();
  await browser.close();
  writeFileSync(`${OUT}marks.json`, JSON.stringify({ view: VIEW, marks }, null, 1));
  console.log("\nwrote", OUT + "session.webm");
};

main().catch((e) => { console.error(e); process.exit(1); });
