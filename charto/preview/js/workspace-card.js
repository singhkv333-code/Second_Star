/* Charto preview — the card a shared workspace unfurls into, made in the page.
 *
 * The server normally takes this picture: it opens the published link in its
 * own browser and screenshots it, shell sidebar and all (dataserver
 * _setup_screenshot, charto/browser /shot). This module is the fallback for
 * when it cannot: the page rebuilt from its DOM. It can only see this frame,
 * so the shell around it is not in the picture, but it never asks the author
 * for anything.
 *
 * Two sizes come out of one capture: the 2400 x 1260 link preview, with the
 * frame whole inside it, and the capture itself for anyone who wants the
 * picture.
 *
 * An unshared conversation is covered where it sits before anything is
 * encoded. Returns null when the capture fails. */
"use strict";

const WorkspaceCard = (() => {
  const OG = { w: 2400, h: 1260 };
  const MASTER_W = 3840;                 // the screen's own pixels, up to this
  const OG_MAX = 1_350_000;              // under shares.OG_IMAGE_MAX
  const HIRES_MAX = 6_800_000;           // under shares.HIRES_MAX
  // Floating surfaces that are not the workspace.
  const TRANSIENT = ".toast, .select-menu, .dlg-swatch-pop, [data-no-capture]";

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

  async function grab(opts) {
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

  /* opts: { withChat } */
  async function render(opts = {}) {
    const t0 = performance.now();
    const shot = await grab(opts);
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
