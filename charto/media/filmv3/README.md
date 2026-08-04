# pivot-launch-60s — the capability reel (v3)

`renders/pivot-launch-60s.mp4` · 1920×1080 · 30 fps · **60.5s** · H.264 + AAC ·
−14.2 LUFS · poster at `renders/pivot-launch-poster.jpg`.

A second option alongside v2, not a replacement. v2 is a narrated *story*; v3 is
a **music-cut capability reel** — a card names something the product can do, and
the product immediately does it. No voice-over.

## What changed from v2

| | v2 | v3 |
|---|---|---|
| Audio | ElevenLabs narration + bed | **Epidemic Sound, "The Transmission"** (127 BPM), no VO |
| Cut | to the narration word map | **to the beat grid** — every boundary is an integer beat |
| Prompts | search → ask → ask | five capabilities: why it moved · patterns · trendlines · indicator + reading · trend per interval |
| Symbol search | on camera | **cut** — picking a symbol is not a selling point |
| Zooms | none | **one per beat**, baked into the footage |
| Background | flat black | macOS-style wallpaper |
| Cards | 4 in 57s | **6 in 60.5s**, ~2.8s each |

## The grid

127 BPM → beat 0.472441s, bar 1.889764s, downbeat measured at **0.005s**, so the
grid is `beat × n` from the file's first sample. The film is **128 beats**.

| beats | t | scene |
|---|---|---|
| 0–8 | 0.00–3.78 | Hero — BTC-USD daily |
| 8–14 | 3.78–6.61 | **Why did it _move?_** |
| 14–28 | 6.61–13.23 | Fed/rates narrative, ETF outflows, no earnings event |
| 28–34 | 13.23–16.06 | **Identify the _patterns._** |
| 34–48 | 16.06–22.68 | Engulfing / doji / tweezer top + LL-LH-BOS structure |
| 48–54 | 22.68–25.51 | **Draw the _trendlines._** |
| 54–68 | 25.51–32.13 | Four intact dailies with touch counts |
| 68–74 | 32.13–34.96 | **Add an indicator. _Read it._** |
| 74–88 | 34.96–41.57 | RSI(14) + a plain-English reading |
| 88–94 | 41.57–44.41 | **The trend, _every interval._** |
| 94–108 | 44.41–51.02 | Interval / ADX / directional read table, 5m → 1w |
| 108–114 | 51.02–53.86 | **Nothing is drawn that _wasn't computed._** |
| 114–128 | 53.86–60.47 | Pivot |

The track's own shape carries the cut: quiet for the first 4s (hero), driving
5–35s (the first three capabilities), a breakdown at 36–46s (the indicator beat
and its card), and the final lift from 47s under the interval table and the
close.

## Three things that were not obvious

**1. Zooming the window destroys the mockup.** The window is 1660×1004 in a
1920×1080 frame, so *any* scale past ~1.16× pushes its edges off-frame. Animating
the window meant the browser chrome — the thing that makes the film look
designed — vanished for most of the runtime. The zooms are therefore baked into
the footage by `zoom.py`; the window never moves. Bonus: the capture is 3024×1800
shown in a 1660px box, ~1.8× of headroom, so a 1.46× digital zoom is still a
downscale on screen.

**2. `crop` cannot zoom.** ffmpeg evaluates `crop`'s `w`/`h` once at init — only
`x`/`y` are per-frame — so a moving crop size is impossible there. `zoompan` is
the filter that varies zoom per frame, and its `z` is clamped to ≥ 1, so the
output window (`s=3024x1750`) is sized just under the source rather than trying
to scale down into it.

**3. The renderer stalls on five-plus live `<video>` elements.** Every attempt
died at frame 1176 of 1815 regardless of capture mode, GPU, worker count, or
keyframe density. Frame count is not the trigger — v2 rendered 1710 frames fine.
`reel.py` flattens the six beat clips into one continuous video laid on the
film's own timeline (gaps between beats filled with the next beat's first frame,
always hidden under a card). One `<video>` element: renders in 2m41s, no stall.

**4. Point the zoom at where the content actually is.** The patterns answer
reports its findings in text and draws nothing on the canvas, so an initial zoom
into the chart landed on empty candles. Verify what a beat actually renders
before choosing its focal point.

## Rebuild

```bash
# stack up: charto/data/restart.sh (:5174) + charto/preview/serve.py (:5173)
node ../record-v3.mjs      # ~2m10s — five capability prompts, no search
ffmpeg -i ../out/rec3/session.webm -c:v libx264 -crf 16 -preset medium \
  -pix_fmt yuv420p -r 30 -g 15 -keyint_min 15 -sc_threshold 0 \
  -movflags +faststart ../out/rec3/session.mp4
python3 ../zoom.py         # per-beat digital zooms
python3 ../reel.py         # flatten to one video on the film timeline
python3 build.py           # emit index.html on the beat grid
npm run check && npm run render
ffmpeg -i renders/<raw>.mp4 -c:v copy -af "loudnorm=I=-14:TP=-1.5:LRA=9" \
  -c:a aac -b:a 256k renders/pivot-launch-60s.mp4
```

Music: `../ep.py search|get|grid`. `grid` prints the tempo, the measured
downbeat and a per-second energy map — that is what picks the track and what
`build.py`'s beat constant has to agree with.
