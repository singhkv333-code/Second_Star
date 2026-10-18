/* Charto preview — the card a shared workspace unfurls into: the whole
 * platform as it is on screen, sidebar and all.
 *
 * It is a SCREENSHOT, taken by the browser. The chart runs in a frame inside
 * the Pivot shell, so a picture rebuilt from this page's DOM can only ever
 * show the frame: no shell sidebar, a badge the re-render cannot draw, text
 * reflowed into itself, and a crop wherever the frame's box ends. A capture of
 * the tab (getDisplayMedia, current tab preferred) is the real pixels of the
 * real screen instead. The browser asks the author once per publish; the share
 * dialog steps aside while the frame is taken, so the picture is the workspace
 * and not the dialog.
 *
 * Two sizes come out of one capture. A link preview wants a file the
 * messaging apps will actually fetch (WhatsApp drops big ones), so the
 * unfurled image is 2400 x 1260 with the screen whole inside it, nothing
 * cropped; the master is the screenshot itself at the screen's own pixels.
 *
 * What the page keeps private stays out of the picture: with the conversation
 * unshared the chat panel is covered on screen before the frame is taken.
 *
 * A browser that cannot capture the tab, or an author who declines, gets the
 * older picture rebuilt from this page's DOM (the frame only). Returns null
 * when neither works, so the caller falls back to the chart-only ShareCard. */
"use strict";

const WorkspaceCard = (() => {
  const OG = { w: 2400, h: 1260 };
  const MASTER_W = 3840;                 // the screen's own pixels, up to this
  const OG_MAX = 1_350_000;              // under shares.OG_IMAGE_MAX
  const HIRES_MAX = 6_800_000;           // under shares.HIRES_MAX
  // Floating surfaces that are not the workspace.
  const TRANSIENT = ".toast, .select-menu, .dlg-swatch-pop, [data-no-capture]";

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const frames = (n) => new Promise((r) => {
    const step = () => (--n <= 0 ? r() : requestAnimationFrame(step));
    requestAnimationFrame(step);
  });
  const videoFrame = (v) => new Promise((r) => {
    if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(() => r());
    else setTimeout(r, 120);
  });

  /** Halve until the next step lands within 2x of the target, then let the
   *  last drawImage finish; one big downscale aliases thin lines and text. */
  function stepDown(src, tw, th) {
    let cur = src, w = src.width, h = src.height;
    while (w / 2 >= tw && h / 2 >= th) {
      w = Math.round(w / 2); h = Math.round(h / 2);
      const c = document.createElement("canvas");
      c.width = w; c.height = h;
      const x = c.getContext("2d");
      x.imageSmoothingEnabled = true; x.imageSmoothingQuality = "high";
      x.drawImage(cur, 0, 0, w, h);
      cur = c;
    }
    return cur;
  }

  function encode(canvas, qualities, max) {
    for (const q of qualities) {
      const url = canvas.toDataURL("image/jpeg", q);
      if (url.length <= max) return url;
    }
    return "";
  }

  /** The average colour of a one-pixel strip, so the margin the card adds
   *  beside the screen continues the screen's own edge. */
  function edge(src, x, y, w, h) {
    const c = document.createElement("canvas");
    c.width = c.height = 1;
    const cx = c.getContext("2d");
    cx.imageSmoothingEnabled = true; cx.imageSmoothingQuality = "high";
    cx.drawImage(src, x, y, w, h, 0, 0, 1, 1);
    const p = cx.getImageData(0, 0, 1, 1).data;
    return `rgb(${p[0]},${p[1]},${p[2]})`;
  }

  /** The whole screen inside the card, never cropped. Where its shape is not
   *  the card's, the strip left over takes the colour of the screen's edge. */
  function fit(src, W, H) {
    const c = document.createElement("canvas");
    c.width = W; c.height = H;
    const x = c.getContext("2d", { alpha: false });
    const f = Math.min(W / src.width, H / src.height);
    const dw = Math.round(src.width * f), dh = Math.round(src.height * f);
    const dx = Math.round((W - dw) / 2), dy = Math.round((H - dh) / 2);
    if (dx > 0) {
      x.fillStyle = edge(src, 0, 0, 1, src.height); x.fillRect(0, 0, dx + 1, H);
      x.fillStyle = edge(src, src.width - 1, 0, 1, src.height); x.fillRect(dx + dw - 1, 0, W - dx - dw + 1, H);
    }
    if (dy > 0) {
      x.fillStyle = edge(src, 0, 0, src.width, 1); x.fillRect(0, 0, W, dy + 1);
      x.fillStyle = edge(src, 0, src.height - 1, src.width, 1); x.fillRect(0, dy + dh - 1, W, H - dy - dh + 1);
    }
    x.imageSmoothingEnabled = true; x.imageSmoothingQuality = "high";
    x.drawImage(stepDown(src, dw, dh), dx, dy, dw, dh);
    return c;
  }

  /** Clear the screen for the picture: the share dialog out of the way (the
   *  shell stops floating the frame over the window when it goes, so the
   *  workspace returns to its own layout), menus and toasts hidden, and an
   *  unshared conversation covered where it sits. Returns the undo. */
  function stage({ dialog, withChat }) {
    const undo = [];
    if (dialog) {
      const prev = dialog.style.display;
      dialog.style.display = "none";
      undo.push(() => { dialog.style.display = prev; });
    }
    // A cursor resting where the Publish button was is now over the chart,
    // whose crosshair would follow it into the picture. A clear shield over
    // the page is what the cursor rests on instead, so the chart sees it leave.
    const shield = document.createElement("div");
    shield.style.cssText = "position:fixed;inset:0;z-index:2147483646;background:transparent";
    document.body.appendChild(shield);
    undo.push(() => shield.remove());
    for (const el of document.querySelectorAll(".tv-lightweight-charts, .tv-lightweight-charts td")) {
      el.dispatchEvent(new MouseEvent("mouseleave", { bubbles: false }));
    }
    const css = document.createElement("style");
    css.textContent = `${TRANSIENT} { visibility: hidden !important; }`;
    document.head.appendChild(css);
    undo.push(() => css.remove());
    const chat = document.getElementById("chatPanel");
    if (!withChat && chat && chat.getBoundingClientRect().width > 4) {
      const cover = document.createElement("div");
      cover.style.cssText = "position:absolute;inset:0;z-index:2147483000;pointer-events:none;" +
        `background:${getComputedStyle(chat).backgroundColor || "#faf8f2"}`;
      chat.appendChild(cover);
      undo.push(() => cover.remove());
    }
    return () => undo.reverse().forEach((f) => f());
  }

  /** One frame of this tab, as the browser shows it. Null when the browser
   *  cannot capture a tab, the author declines, or something other than a
   *  browser tab was picked (a window or a screen carries more than Pivot). */
  async function grabTab(opts) {
    const md = navigator.mediaDevices;
    // CaptureController ships with the browsers that honour preferCurrentTab;
    // elsewhere the picker opens on whole screens and windows, which is not
    // what anyone asked to share.
    if (!md || !md.getDisplayMedia || typeof window.CaptureController !== "function") return null;
    let top = window;
    try { if (window.top && window.top.innerWidth) top = window.top; } catch { /* another origin */ }
    const dpr = window.devicePixelRatio || 1;
    const restore = stage(opts);
    let stream = null;
    try {
      await frames(2); await sleep(250);              // the shell un-floats the frame
      stream = await md.getDisplayMedia({
        video: {
          width: { ideal: Math.round(top.innerWidth * dpr) },
          height: { ideal: Math.round(top.innerHeight * dpr) },
          frameRate: { ideal: 5 },
        },
        audio: false,
        preferCurrentTab: true,
        selfBrowserSurface: "include",
        surfaceSwitching: "exclude",
        monitorTypeSurfaces: "exclude",
      });
      const track = stream.getVideoTracks()[0];
      const surface = track && track.getSettings ? track.getSettings().displaySurface : "browser";
      if (!track || (surface && surface !== "browser")) return null;
      const video = document.createElement("video");
      video.muted = true; video.playsInline = true; video.srcObject = stream;
      await video.play();
      // The permission prompt closes and the browser's "sharing this tab" bar
      // can shorten the viewport; let the page lay out again before the frame.
      await sleep(500); await frames(2);
      await videoFrame(video); await videoFrame(video);
      if (!video.videoWidth || !video.videoHeight) return null;
      const c = document.createElement("canvas");
      c.width = video.videoWidth; c.height = video.videoHeight;
      c.getContext("2d", { alpha: false }).drawImage(video, 0, 0);
      return c;
    } catch (e) {
      if (!e || e.name !== "NotAllowedError") console.warn("Tab capture failed", e);
      return null;
    } finally {
      if (stream) stream.getTracks().forEach((t) => t.stop());
      restore();
    }
  }

  /** The fallback: this page rebuilt from its DOM. It cannot see past the
   *  frame, so it shows the workspace without the shell around it. */
  async function grabDom(opts) {
    const lib = window.modernScreenshot;
    const root = document.body;
    const box = root.getBoundingClientRect();
    if (!lib || !box.width || !box.height) return null;
    const scale = Math.min(3, Math.max(1.5, MASTER_W / box.width));
    let shot;
    try {
      shot = await lib.domToCanvas(root, {
        scale, timeout: 6000,
        filter: (n) => !(n.nodeType === 1 && n.matches &&
          n.matches(`.ly-back, .su-back, ${TRANSIENT}`)),
      });
    } catch (e) { console.warn("Workspace capture failed", e); return null; }
    if (!shot || !shot.width) return null;
    const chat = document.getElementById("chatPanel");
    if (!opts.withChat && chat) {
      const k = shot.width / box.width, r = chat.getBoundingClientRect();
      if (r.width > 4) {
        const x = shot.getContext("2d");
        x.fillStyle = getComputedStyle(chat).backgroundColor || "#faf8f2";
        x.fillRect((r.left - box.left) * k, (r.top - box.top) * k, r.width * k, r.height * k);
      }
    }
    return shot;
  }

  /* opts: { withChat, dialog }. Call it inside the click that publishes: a
   * browser only offers a screen capture to a user's gesture. */
  async function render(opts = {}) {
    const t0 = performance.now();
    const shot = (await grabTab(opts)) || (await grabDom(opts));
    if (!shot) return null;
    if (window.__cardTiming) window.__cardTiming.capture = Math.round(performance.now() - t0);

    const master = shot.width > MASTER_W
      ? fit(shot, MASTER_W, Math.round(shot.height * MASTER_W / shot.width))
      : shot;
    const hires = encode(master, [0.92, 0.86, 0.8, 0.72], HIRES_MAX);
    const og = encode(fit(shot, OG.w, OG.h), [0.9, 0.85, 0.8, 0.74, 0.68, 0.6], OG_MAX);
    return og ? { og, hires } : null;
  }

  return { render };
})();
