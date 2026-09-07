# pivot-demo-57s — the launch film (v2)

`renders/pivot-demo-57s.mp4` · 1920×1080 · 30 fps · 57.0s · H.264 + AAC ·
**−14.0 LUFS** · poster at `renders/pivot-demo-poster.jpg`.

v2 is not v1 re-cut. v1 animated **still captures**; this is a **screen
recording of a real driven session** — `record.mjs` opens the running product,
searches for the instrument, switches to daily, and asks three questions while
puppeteer screencasts the whole thing. What you see is the app working, not a
reconstruction of it.

## What changed from v1

| | v1 | v2 |
|---|---|---|
| Source | 4 still PNGs + ken-burns | live screencast of a scripted session |
| Symbol | RELIANCE 5m | **BTC-USD daily** (Coinbase) |
| Drawings | 3 support zones | falling wedge, rounding bottom, double bottom, trendline, 52w high/low + LL/LH/BOS structure |
| Framing | UI at 1920 filling the frame — read small | 1512-CSS capture at 2× shown in a 1660px window: UI at **true 100%**, ~1.4× larger on screen |
| Presentation | bare screenshots | **macOS browser mockup** on a dark desktop |
| Brand | Charto | **Pivot** (video-only rename) |
| Statement cards | 1 | **4** + end card |
| Voice | George | **Christian Plasa — Wise and Commanding** (`zlatCM6nK59gyedHFFxn`) |
| Length | 30s | 57s |

## The cut

| t | Beat | Source |
|---|---|---|
| 0.0–6.9 | BTC-USD daily, settled | rec 10.6→17.5 |
| 6.4–9.6 | **CARD** — "Pivot *measures* instead." | — |
| 9.2–11.8 | Instrument search: "BTC" → BTC-USD | rec 3.0→5.6 |
| 11.6–14.2 | The question typed in plain language | rec 16.0→18.6 |
| 14.0–24.1 | Patterns + market structure drawn | rec 40.3→50.4 |
| 23.6–28.4 | **CARD** — "Has this pattern *ever actually worked?*" | — |
| 28.0–35.1 | The answer: no historical base rate available | rec 62.2→69.3 |
| 34.6–38.4 | **CARD** — "It will not validate *its own drawing*." | — |
| 38.0–47.1 | "What it did not show" — the honest attribution | rec 108.0→117.1 |
| 46.6–51.0 | **CARD** — "Nothing is drawn that *wasn't computed*." | — |
| 50.6–57.0 | End — Pivot wordmark | — |

The spine is the middle: Pivot draws three textbook formations, then, asked
whether they have ever worked, **refuses to validate its own drawing**. A demo
where the product declines to oversell itself is a harder trust signal to fake
than any feature montage — and it is the constitution (`CHARTO.md` §2.4) on
camera rather than in a doc.

## Non-obvious things that cost time

- **Screencast captures the compositor surface, and the viewport's
  `deviceScaleFactor` does not reach it.** A viewport at DPR 2 still recorded
  1512×900. Chrome must be launched with `--force-device-scale-factor=2` to get
  a 3024×1800 recording.
- **Dense keyframes are mandatory.** With a default GOP (~250 frames) the
  renderer stalled dead at frame 1168 — the exact frame where the last clip
  seeks to 108s in the source. Re-encoding with `-g 15 -keyint_min 15
  -sc_threshold 0` fixed it. If a render hangs at a fixed frame, suspect a
  video seek, not the composition.
- **Window size is the whole "too zoomed out" complaint.** An inset 1512px
  window in a 1920 frame shrinks the UI right back down. The window has to
  nearly fill the frame (1660×1004) for the product to read at 100%.
- **Crop arithmetic:** source is 1.68:1, the content box is 1.73:1, so
  `object-position: top` clips only the bottom status strip. A 967px box (vs
  960px) sliced the left rail's last icon — 7px of the wrong crop is visible.
- **`eleven_v3` ignores `speed`.** Length is controlled by cutting words, at a
  measured ~2.0 words/sec for this voice.

## Video-only cosmetics

Injected at runtime by `record.mjs`; **the app on disk is never modified**:
the wordmark renders as "Pivot.", the charting library's attribution mark is
hidden, and transient connection chatter is suppressed. Everything else on
screen — every number, pattern, date and refusal — is the real product's real
output.

## Rebuild

```bash
# 1. stack up:  charto/data/restart.sh (:5174) and charto/preview/serve.py (:5173)
node ../record.mjs                       # ~2 min; writes out/rec/session.webm + marks.json
ffmpeg -i ../out/rec/session.webm -c:v libx264 -crf 16 -preset medium \
       -pix_fmt yuv420p -r 30 -g 15 -keyint_min 15 -sc_threshold 0 \
       -movflags +faststart assets/session.mp4
npm run check                            # must be 0 errors
npm run render
ffmpeg -i renders/<raw>.mp4 -c:v copy -af "loudnorm=I=-14:TP=-1.5:LRA=7" \
       -c:a aac -b:a 192k renders/pivot-demo-57s.mp4
```

`marks.json` carries the elapsed-ms milestone for every action in the session —
that is what the `data-media-start` values above are derived from. Re-record and
the marks move; re-derive the cuts from the new file rather than assuming.
