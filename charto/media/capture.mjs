/**
 * capture.mjs — high-DPI capture rig for the demo film.
 *
 * Why this exists instead of the Playwright MCP: the MCP screenshots at
 * deviceScaleFactor 1, so a 1920-wide capture puts the UI at ~1920 CSS px and
 * every label lands tiny inside the frame. Here the viewport is a MacBook-sized
 * 1512×900 at DPR 2 → a 3024×1800 physical PNG. Displayed inside a browser
 * mockup ~1500px wide in a 1920 frame, that is a 2× DOWNSCALE: the UI reads at
 * its true 100% size and the text is sharper than a 1:1 capture.
 *
 * Everything cosmetic here is VIDEO-ONLY and injected at runtime — the app on
 * disk is never modified. Rebrand to Pivot, hide the charting library's
 * attribution mark, quiet transient status text.
 *
 *   node capture.mjs probe          # dump the DOM hooks it relies on
 *   node capture.mjs shoot          # run the scripted session, write out/capture2x/
 */
import puppeteer from "/Users/karanveersingh/.npm/_npx/702923228c2ce1e6/node_modules/puppeteer-core/lib/esm/puppeteer/puppeteer-core.js";
import { mkdirSync } from "node:fs";
import { execSync } from "node:child_process";

const OUT = new URL("./out/capture2x/", import.meta.url).pathname;
const URL_BASE = "http://127.0.0.1:5173/?symbol=BTC-USD";
const VIEW = { width: 1512, height: 900, deviceScaleFactor: 2 };

const CHROME = execSync("npx hyperframes browser path", { encoding: "utf8" })
  .trim().split("\n").pop().trim();

/** Video-only dressing. Injected after every navigation. */
const DRESS = `
  (() => {
    const st = document.createElement("style");
    st.id = "film-dress";
    st.textContent = \`
      /* the charting library's attribution mark — out for the film */
      #tv-attr-logo, a[href*="tradingview.com"] { display: none !important; }
      /* transient connection chatter reads as breakage on camera */
      #status, .status, #statusLeft { visibility: hidden !important; }
    \`;
    document.head.appendChild(st);
    const brand = document.querySelector(".brand");
    if (brand) {
      // keep the accented full stop the wordmark already uses
      const dot = brand.querySelector(".dot");
      brand.textContent = "Pivot";
      if (dot) brand.appendChild(dot);
      else { const d = document.createElement("span"); d.className = "dot"; d.textContent = "."; brand.appendChild(d); }
    }
    document.title = "BTC-USD — Pivot";
  })();
`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** The token-meta line ("12.2s · 25,597 in / 467 out") appears once per
 *  completed reply, so its count is a reliable done-signal. */
const replyCount = (page) =>
  page.evaluate(() => (document.body.innerText.match(/ in \/ /g) || []).length);

async function ask(page, text, timeoutMs = 150000) {
  const before = await replyCount(page);
  await page.click("textarea");
  await page.evaluate(() => { document.querySelector("textarea").value = ""; });
  await page.type("textarea", text, { delay: 12 });
  await sleep(400);
  const shotTyped = text;
  await page.keyboard.press("Enter");
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    await sleep(1500);
    if ((await replyCount(page)) > before) { await sleep(1800); return true; }
  }
  console.warn("  ! timed out waiting for:", shotTyped);
  return false;
}

async function shot(page, name) {
  await page.screenshot({ path: `${OUT}${name}.png` });
  console.log("  ->", name + ".png");
}

const main = async () => {
  mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "shell",
    args: ["--force-device-scale-factor=2", "--hide-scrollbars", "--font-render-hinting=none"],
    defaultViewport: VIEW,
  });
  const page = await browser.newPage();
  await page.setViewport(VIEW);

  // a fresh session, then the panel width the film wants
  await page.goto(URL_BASE, { waitUntil: "networkidle2", timeout: 60000 });
  await page.evaluate(() => localStorage.setItem("charto_chat_width", "430"));
  await page.goto(URL_BASE, { waitUntil: "networkidle2", timeout: 60000 });
  await page.evaluate(DRESS);
  await sleep(3500);

  if (process.argv[2] === "probe") {
    const info = await page.evaluate(() => ({
      dpr: devicePixelRatio,
      w: innerWidth, h: innerHeight,
      brand: document.querySelector(".brand")?.textContent,
      tv: !!document.querySelector('a[href*="tradingview.com"], #tv-attr-logo'),
      statusText: document.querySelector("#status,.status")?.textContent?.slice(0, 60),
      tail: document.body.innerText.slice(-260),
    }));
    console.log(info);
    await shot(page, "probe");
    await browser.close();
    return;
  }

  await shot(page, "A-clean");

  // Beat 1 — structure and formations
  const q1 = "Mark the chart patterns and the market structure.";
  await page.click("textarea");
  await page.type("textarea", q1, { delay: 14 });
  await sleep(500);
  await shot(page, "B-typed");
  await page.keyboard.press("Enter");
  {
    const t0 = Date.now(); const before = 0;
    while (Date.now() - t0 < 150000) {
      await sleep(1500);
      if ((await replyCount(page)) > before) { await sleep(2200); break; }
    }
  }
  await shot(page, "C-patterns");

  // Beat 2 — does the formation have a record on THIS symbol
  await ask(page, "Has this falling wedge actually worked on BTC before? Show the base rate against a control.");
  await shot(page, "D-evidence");

  // Beat 3 — the honest attribution answer
  await ask(page, "Why did it move on 29 July?");
  await shot(page, "E-why");

  await browser.close();
  console.log("done");
};

main().catch((e) => { console.error(e); process.exit(1); });
