/* The link-preview card (1200 x 630) a pasted setup link unfurls into.
 *
 * One canvas in the author's chart theme, so the snapshot sits on its own
 * background instead of in a box on a foreign one. The chart is the subject:
 * it takes the right of the card and bleeds off the edge like the app's own
 * chart pane. The left carries what the app's header and Share dialog carry —
 * the Pivot lockup (the slashed-circle mark, the Newsreader wordmark), the
 * symbol and interval, the title, the note, and who published it when.
 *
 * Every value on the card is the author's own: their chart, their words,
 * their display name. No market data is invented for it. */
"use strict";

const SharePreview = (() => {
  const W = 1200, H = 630, M = 56;
  const COL_W = 408;                          // text column: 56 → 464
  const PANEL_X = 512, PANEL_Y = 128;         // chart panel bleeds right + bottom
  const MAX_URL = 640_000;                    // under shares.OG_IMAGE_MAX

  // The app's tokens (index.html :root[data-theme]), per theme.
  const THEMES = {
    dark: {
      bg: "#000000", fg: "#fbfcfc", muted: "#8f98a1", faint: "#6b7280",
      border: "#242424", chartBg: "#0d0e12", chip: "#1c1c1d", shadow: null,
    },
    light: {
      bg: "#ffffff", fg: "#0d0d0e", muted: "#4d555c", faint: "#6b7280",
      border: "#e6e6e6", chartBg: "#ffffff", chip: "#f5f5f5",
      shadow: "rgba(15, 18, 22, .08)",
    },
  };
  const AVATAR = "#089981";                   // --avatar, both themes

  const SANS = "Inter, ui-sans-serif, -apple-system, Arial, sans-serif";
  const SERIF = "Newsreader, Georgia, 'Times New Roman', serif";

  function font(ctx, spec, tracking = 0) {
    ctx.font = spec;
    if ("letterSpacing" in ctx) ctx.letterSpacing = `${tracking}px`;
  }

  function lines(ctx, value, x, y, maxWidth, lineHeight, maxLines) {
    const out = wrap(ctx, value, maxWidth, maxLines);
    out.forEach((text, i) => ctx.fillText(text, x, y + i * lineHeight));
    return out.length;
  }

  function wrap(ctx, value, maxWidth, maxLines) {
    const words = String(value || "").replace(/\s+/g, " ").trim().split(" ");
    const out = [];
    let line = "";
    for (const word of words) {
      if (ctx.measureText(word).width > maxWidth) {
        if (line) { out.push(line); line = ""; }
        let part = "";
        for (const char of word) {
          if (part && ctx.measureText(part + char).width > maxWidth) {
            out.push(part); part = "";
          }
          part += char;
        }
        line = part;
        continue;
      }
      const next = line ? `${line} ${word}` : word;
      if (line && ctx.measureText(next).width > maxWidth) {
        out.push(line);
        line = word;
      } else line = next;
    }
    if (line) out.push(line);
    if (maxLines < 1) return [];
    if (out.length > maxLines) {
      out.length = maxLines;
      let last = out[maxLines - 1];
      while (last && ctx.measureText(`${last}…`).width > maxWidth) {
        last = last.includes(" ") ? last.slice(0, last.lastIndexOf(" ")) : last.slice(0, -1);
      }
      out[maxLines - 1] = `${last || ""}…`;
    }
    return out;
  }

  function fit(ctx, text, maxWidth) {
    let s = String(text || "");
    if (ctx.measureText(s).width <= maxWidth) return s;
    while (s && ctx.measureText(`${s}…`).width > maxWidth) s = s.slice(0, -1);
    return `${s.trimEnd()}…`;
  }

  const loadImage = (src) => new Promise((resolve) => {
    if (!src) return resolve(null);
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;
  });

  // The canonical mark is a raster the header masks with currentColor; do the
  // same here so the card never carries a second drawing of the logo.
  function mark(ctx, img, x, y, size, color) {
    if (!img) {
      ctx.beginPath(); ctx.arc(x + size / 2, y + size / 2, size / 2, 0, Math.PI * 2);
      ctx.fillStyle = color; ctx.fill();
      return;
    }
    const tint = document.createElement("canvas");
    tint.width = tint.height = size * 2;
    const t = tint.getContext("2d");
    t.drawImage(img, 0, 0, size * 2, size * 2);
    t.globalCompositeOperation = "source-in";
    t.fillStyle = color; t.fillRect(0, 0, size * 2, size * 2);
    ctx.drawImage(tint, x, y, size, size);
  }

  function lockup(ctx, img, c) {
    mark(ctx, img, M, 45, 28, c.fg);
    ctx.fillStyle = c.fg;
    font(ctx, `550 36px ${SERIF}`, -1.1);
    ctx.fillText("Pivot", M + 37, 71);
  }

  function chartPanel(ctx, image, c) {
    const x = PANEL_X, y = PANEL_Y, w = W - PANEL_X, h = H - PANEL_Y, r = 14;
    const shape = () => {
      ctx.beginPath();
      ctx.moveTo(x, H + 1); ctx.lineTo(x, y + r);
      ctx.arcTo(x, y, x + r, y, r); ctx.lineTo(W + 1, y);
      ctx.lineTo(W + 1, H + 1); ctx.closePath();
    };
    ctx.save();
    if (c.shadow) { ctx.shadowColor = c.shadow; ctx.shadowBlur = 40; ctx.shadowOffsetY = 8; }
    shape(); ctx.fillStyle = c.chartBg; ctx.fill();
    ctx.restore();

    ctx.save();
    shape(); ctx.clip();
    // Cover the panel, anchored right: the price axis and the latest bars —
    // where a setup's drawings almost always sit — are kept; the oldest bars
    // on the left are what gets cropped.
    const s = Math.max(w / image.width, h / image.height);
    const dw = image.width * s, dh = image.height * s;
    ctx.drawImage(image, x + w - dw, y + Math.min(0, (h - dh) / 2), dw, dh);
    ctx.restore();

    ctx.save();
    shape(); ctx.strokeStyle = c.border; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.restore();
  }

  function dateLabel(ts) {
    const d = ts ? new Date(ts) : new Date();
    try {
      return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
    } catch {
      // A browser that reports an invalid locale throws inside Intl.
      return d.toISOString().slice(0, 10);
    }
  }

  function byline(ctx, by, when, colW, c) {
    const y = 566;
    ctx.strokeStyle = c.border; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(M, 522); ctx.lineTo(M + colW, 522); ctx.stroke();

    const name = String(by || "").trim() || "A Pivot trader";
    ctx.beginPath(); ctx.arc(M + 16, y - 6, 16, 0, Math.PI * 2);
    ctx.fillStyle = AVATAR; ctx.fill();
    ctx.fillStyle = "#ffffff";
    font(ctx, `600 15px ${SANS}`);
    ctx.textAlign = "center";
    ctx.fillText(name[0].toUpperCase(), M + 16, y - 0.5);
    ctx.textAlign = "left";

    const date = dateLabel(when);
    font(ctx, `400 17px ${SANS}`);
    const dateW = ctx.measureText(`  ·  ${date}`).width;
    font(ctx, `500 17px ${SANS}`, -0.2);
    const shown = fit(ctx, name, colW - 44 - dateW);
    ctx.fillStyle = c.fg;
    ctx.fillText(shown, M + 44, y);
    const nameW = ctx.measureText(shown).width;
    font(ctx, `400 17px ${SANS}`);
    ctx.fillStyle = c.faint;
    ctx.fillText(`  ·  ${date}`, M + 44 + nameW, y);
  }

  function symbolRow(ctx, symbol, interval, c) {
    const y = 172;
    ctx.fillStyle = c.fg;
    font(ctx, `700 18px ${SANS}`, 0.2);
    const sym = String(symbol || "").toUpperCase().slice(0, 24);
    ctx.fillText(sym, M, y);
    if (!interval) return;
    const x = M + ctx.measureText(sym).width + 12;
    font(ctx, `600 13px ${SANS}`, 0.6);
    // As the app writes it: "15m" upper-cased reads as fifteen months.
    const label = String(interval).slice(0, 8);
    const w = ctx.measureText(label).width + 18;
    ctx.beginPath(); ctx.roundRect(x, y - 18, w, 24, 6);
    ctx.fillStyle = c.chip; ctx.fill();
    ctx.strokeStyle = c.border; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = c.muted;
    ctx.fillText(label, x + 9, y - 1.5);
  }

  function encode(canvas) {
    for (const q of [0.9, 0.84, 0.76, 0.66]) {
      const url = canvas.toDataURL("image/jpeg", q);
      if (url.length <= MAX_URL) return url;
    }
    return "";
  }

  /* chart: a full-resolution capture (Layouts.capture); thumb is the small
   * layout picture, used only when the full capture is unavailable. */
  async function render({ title, note, chart, thumb, symbol, interval,
                          chatTurns = 0, by = "", when = null, theme } = {}) {
    if (document.fonts && document.fonts.load) {
      await Promise.all([
        `550 36px ${SERIF}`, `600 44px ${SANS}`, `400 19px ${SANS}`,
        `700 18px ${SANS}`, `500 17px ${SANS}`,
      ].map((f) => document.fonts.load(f).catch(() => null)));
    }
    const mode = theme || document.documentElement.getAttribute("data-theme");
    const c = THEMES[mode === "light" ? "light" : "dark"];
    const canvas = document.createElement("canvas");
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return "";
    ctx.fillStyle = c.bg; ctx.fillRect(0, 0, W, H);

    const [markImg, image] = await Promise.all([
      loadImage("assets/pivot-mark.png"), loadImage(chart || thumb),
    ]);
    lockup(ctx, markImg, c);

    ctx.fillStyle = c.faint;
    font(ctx, `600 13px ${SANS}`, 1.3);
    ctx.textAlign = "right";
    ctx.fillText(chatTurns ? "SHARED SETUP  ·  WITH CONVERSATION" : "SHARED SETUP", W - M, 66);
    ctx.textAlign = "left";

    // Without a picture the words take the whole width, at display size.
    const colW = image ? COL_W : W - 2 * M;
    if (image) chartPanel(ctx, image, c);

    const summary = String(note || "").trim();
    if (image) {
      symbolRow(ctx, symbol, interval, c);
      ctx.fillStyle = c.fg;
      font(ctx, `600 44px ${SANS}`, -1.7);
      const titleTop = 238;
      const n = lines(ctx, title || "Shared setup", M, titleTop, colW, 50, 3);
      if (summary) {
        const y = titleTop + (n - 1) * 50 + 44;
        ctx.fillStyle = c.muted;
        font(ctx, `400 19px ${SANS}`, -0.1);
        lines(ctx, summary, M, y, colW, 28, Math.floor((490 - y) / 28) + 1);
      }
    } else {
      // Words only: set low, like the auth brand panel's pitch, so the card
      // is not a headline floating over an empty field.
      font(ctx, `400 21px ${SANS}`, -0.1);
      const noteLines = summary ? wrap(ctx, summary, 760, 2) : [];
      font(ctx, `600 60px ${SANS}`, -2.4);
      const titleLines = wrap(ctx, title || "Shared setup", colW, 2);
      const floor = by === null ? 548 : 478;           // last baseline
      const noteTop = floor - (noteLines.length - 1) * 31;
      const titleTop = (noteLines.length ? noteTop - 52 : floor)
        - (titleLines.length - 1) * 66;
      ctx.fillStyle = c.fg;
      titleLines.forEach((t, i) => ctx.fillText(t, M, titleTop + i * 66));
      ctx.fillStyle = c.muted;
      font(ctx, `400 21px ${SANS}`, -0.1);
      noteLines.forEach((t, i) => ctx.fillText(t, M, noteTop + i * 31));
      if (symbol) {
        const save = ctx.getTransform();
        ctx.translate(0, titleTop - 66 - 172);
        symbolRow(ctx, symbol, interval, c);
        ctx.setTransform(save);
      }
    }

    // by: null is the generic card (assets/share-preview.jpg), which has no author.
    if (by !== null) byline(ctx, by, when, colW, c);
    return encode(canvas);
  }

  return { render };
})();
