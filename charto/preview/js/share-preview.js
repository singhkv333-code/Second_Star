/* A social image made from the published setup's real chart and words.
 * Its split canvas, wave field and Pivot lockup mirror the auth brand panel;
 * the chart is the same snapshot used in the Share dialog. No market data is
 * invented for the card. */
"use strict";

const SharePreview = (() => {
  const W = 1200, H = 630, CUT = 690;

  function rounded(ctx, x, y, w, h, r, fill, stroke) {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, r);
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1; ctx.stroke(); }
  }

  function lines(ctx, value, x, y, maxWidth, lineHeight, maxLines) {
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
    if (out.length > maxLines) {
      out.length = maxLines;
      let last = out[maxLines - 1];
      while (last && ctx.measureText(`${last}…`).width > maxWidth) {
        last = last.slice(0, last.lastIndexOf(" "));
      }
      out[maxLines - 1] = `${last || ""}…`;
    }
    out.forEach((text, i) => ctx.fillText(text, x, y + i * lineHeight));
    return out.length;
  }

  function waveField(ctx) {
    ctx.save();
    ctx.beginPath(); ctx.rect(0, 0, CUT, H); ctx.clip();
    for (let i = 0; i < 30; i++) {
      const g = Math.exp(-((i - 15) ** 2) / (2 * 8 * 8));
      const amp = 6 + 26 * g;
      ctx.beginPath();
      for (let x = -40; x <= 730; x += 8) {
        const t = x / 600;
        const y = (60 + i * 23) * .82 + 36 +
          Math.sin(t * Math.PI * 2 + i * .42) * amp +
          Math.sin(t * Math.PI * 5 + i * .714) * amp * .22;
        if (x === -40) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = `rgba(255,255,255,${(.035 + .18 * g).toFixed(3)})`;
      ctx.lineWidth = 1;
      ctx.stroke();
    }
    ctx.restore();
  }

  function mark(ctx, x, y) {
    ctx.fillStyle = "#fbfcfc";
    const rects = [[0, 16, 6, 11], [9, 8, 6, 9], [18, 0, 6, 9], [27, 0, 6, 27]];
    for (const [dx, dy, w, h] of rects) rounded(ctx, x + dx, y + dy, w, h, 1, "#fbfcfc");
  }

  const loadImage = (src) => new Promise((resolve) => {
    if (!src) return resolve(null);
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;
  });

  async function render({ title, note, thumb, symbol, interval, chatTurns = 0 }) {
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    const canvas = document.createElement("canvas");
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return "";
    ctx.fillStyle = "#090a0b"; ctx.fillRect(0, 0, CUT, H);
    waveField(ctx);
    ctx.fillStyle = "#ffffff"; ctx.fillRect(CUT, 0, W - CUT, H);

    mark(ctx, 56, 46);
    ctx.fillStyle = "#fbfcfc";
    ctx.font = "700 31px Inter, Arial, sans-serif";
    ctx.fillText("Pivot", 99, 70);
    ctx.font = "600 13px Inter, Arial, sans-serif";
    ctx.fillStyle = "#a8adb0";
    ctx.fillText("SHARED RESEARCH", 516, 68);

    const image = await loadImage(thumb);
    if (image) {
      rounded(ctx, 55, 131, 580, 354, 11, "#0d0e12", "#34363a");
      ctx.save();
      ctx.beginPath(); ctx.roundRect(57, 133, 576, 350, 9); ctx.clip();
      const scale = Math.max(576 / image.width, 350 / image.height);
      ctx.drawImage(image, 57, 133, image.width * scale, image.height * scale);
      ctx.restore();
    } else {
      ctx.fillStyle = "#fbfcfc";
      ctx.font = "500 51px Inter, Arial, sans-serif";
      lines(ctx, "A chart with its context.", 56, 255, 560, 63, 2);
    }
    ctx.strokeStyle = "#3c3e40";
    ctx.beginPath(); ctx.moveTo(56, 529); ctx.lineTo(634, 529); ctx.stroke();
    ctx.fillStyle = "#aab0b3";
    ctx.font = "500 16px Inter, Arial, sans-serif";
    ctx.fillText("CHART  /  SHARED SETUP", 56, 561);

    ctx.fillStyle = "#111312";
    ctx.font = "700 15px Inter, Arial, sans-serif";
    ctx.fillText(`${String(symbol || "CHART").slice(0, 24)}${interval ? `  ·  ${String(interval).toUpperCase().slice(0, 8)}` : ""}`, 747, 78);
    ctx.fillStyle = "#111312";
    ctx.font = "600 43px Inter, Arial, sans-serif";
    const titleLines = lines(ctx, title || "Shared research", 747, 171, 397, 51, 3);
    const summary = String(note || "").trim();
    if (summary) {
      const y = Math.max(333, 171 + titleLines * 51 + 43);
      ctx.fillStyle = "#545b60";
      ctx.font = "400 20px Inter, Arial, sans-serif";
      lines(ctx, summary, 747, y, 395, 30, Math.max(1, Math.floor((490 - y) / 30)));
    }
    ctx.strokeStyle = "#e5e7e8";
    ctx.beginPath(); ctx.moveTo(747, 529); ctx.lineTo(1145, 529); ctx.stroke();
    ctx.fillStyle = "#111312";
    ctx.font = "600 18px Inter, Arial, sans-serif";
    ctx.fillText(chatTurns ? "Open the conversation" : "Open the shared chart", 747, 567);
    ctx.font = "400 26px Inter, Arial, sans-serif";
    ctx.fillText("↗", 1118, 567);
    return canvas.toDataURL("image/jpeg", .86);
  }

  return { render };
})();
