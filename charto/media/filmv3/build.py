#!/usr/bin/env python3
"""build.py — emit filmv3/index.html on the music's beat grid.

The film is cut TO "The Transmission" (Epidemic Sound, 127 BPM, downbeat at
0.005s — measured, see ep.py grid). Every scene boundary is an integer number
of BEATS, so the edit lands on the music instead of near it. Editing timings by
hand in HTML would drift; this file is the source of truth and index.html is
its output.

Layout is a capability reel: a card states what the product can do, then the
product does it. Six cards, five browser beats, ~60.5s.

    python3 build.py && npm run check
"""
from pathlib import Path

BPM = 127.0
BEAT = 60.0 / BPM              # 0.472441s
ROOT = Path(__file__).resolve().parent

def t(beats: float) -> float:
    """Beat index -> seconds, rounded to ms so the HTML stays readable."""
    return round(beats * BEAT, 3)

TOTAL_BEATS = 128              # 60.472s
DISSOLVE = 0.34                # scene cross-fade, ~0.7 beat

# ── window geometry (unchanged from v2 — the user signed off on this) ────
WIN_X, WIN_Y, WIN_W = 130, 38, 1660
CHROME_H = 44
VID_Y, VID_H = 82, 960
WIN_H = 1004

# ── the reel ────────────────────────────────────────────────────────────
# Zooms are baked into the footage by zoom.py, NOT animated here. Scaling the
# window itself would push its edges off-frame at anything past ~1.16x and the
# mockup — the thing that makes this look designed — would vanish for most of
# the runtime. So the window is locked and the content moves inside it.
#
# And the six beat clips are then flattened into ONE reel by reel.py: the
# renderer stalls partway through any job carrying five or six live <video>
# elements. reel.py lays every beat at its exact film time, so a single video
# element spanning 0 -> REEL_END plays the whole thing. Cards cover the joins.
REEL_END_BEAT = 108

CARDS = [
    ( 8,  14, 'Why did it <em>move?</em>'),
    (28,  34, 'Identify the <em>patterns.</em>'),
    (48,  54, 'Draw the <em>trendlines.</em>'),
    (68,  74, 'Add an indicator. <em>Read it.</em>'),
    (88,  94, 'The trend, <em>every interval.</em>'),
    (108,114, "Nothing is drawn that <em>wasn't computed.</em>"),
]
END = (114, TOTAL_BEATS)


CSS = f"""
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: 1920px; height: 1080px; overflow: hidden; background: #05070c; }}
      body {{ font-family: "Inter", system-ui, sans-serif; -webkit-font-smoothing: antialiased; }}
      :root {{ --accent: #5ad1ea; --paper: #f4f8fb; }}
      #root {{ position: relative; width: 1920px; height: 1080px; overflow: hidden; }}

      /* ── the desktop ────────────────────────────────────────────────
         A macOS-style wallpaper built from layered gradients rather than a
         bitmap: it resolves at any output size, carries no licence, and
         costs nothing to re-tint. Deep indigo base, two warm/cool blooms,
         and a soft diagonal sweep for the light direction. */
      .bg {{ position: absolute; inset: 0; background: #070a14; }}
      .bg-a {{
        position: absolute; inset: 0;
        background:
          radial-gradient(120% 92% at 12% 8%,   #2b3f8f 0%, rgba(43,63,143,0) 58%),
          radial-gradient(105% 85% at 88% 14%,  #7a3a9c 0%, rgba(122,58,156,0) 55%),
          radial-gradient(120% 96% at 78% 96%,  #1a6f9e 0%, rgba(26,111,158,0) 60%),
          radial-gradient(96% 80% at 22% 92%,   #3d1e6b 0%, rgba(61,30,107,0) 58%),
          linear-gradient(155deg, #0a1030 0%, #12103a 42%, #0b1830 100%);
      }}
      .bg-b {{
        position: absolute; inset: 0;
        background:
          radial-gradient(58% 40% at 50% 34%, rgba(140,190,255,.20), transparent 70%),
          conic-gradient(from 210deg at 62% 40%, rgba(255,255,255,.07), rgba(255,255,255,0) 28%,
                         rgba(120,220,255,.09) 52%, rgba(255,255,255,0) 78%);
      }}
      .bg-vig {{
        position: absolute; inset: 0;
        background: radial-gradient(78% 68% at 50% 46%, transparent 38%, rgba(2,4,10,.72) 100%);
      }}

      /* ── the browser window ─────────────────────────────────────────
         Fixed for the whole film. Every beat gets its own chrome/video set so
         they can cross-dissolve, but the geometry never changes — the zoom
         lives in the footage (zoom.py), which keeps the mockup on screen and
         avoids upscaling the composite. */
      .win-shadow {{
        position: absolute; left: {WIN_X}px; top: {WIN_Y}px; width: {WIN_W}px; height: {WIN_H}px;
        border-radius: 18px; background: #0a0e13;
        box-shadow: 0 54px 130px rgba(0,0,0,.66), 0 16px 40px rgba(0,0,0,.5);
      }}
      .chrome {{
        position: absolute; left: {WIN_X}px; top: {WIN_Y}px; width: {WIN_W}px; height: {CHROME_H}px;
        border-radius: 18px 18px 0 0;
        background: linear-gradient(#fbfbfc, #f1f2f4);
        border-bottom: 1px solid #dfe2e6;
        display: flex; align-items: center; padding: 0 16px;
      }}
      .lights {{ display: flex; gap: 8px; align-items: center; }}
      .light {{ display: block; width: 12px; height: 12px; border-radius: 50%; }}
      .l1 {{ background: #ff5f57; }} .l2 {{ background: #febc2e; }} .l3 {{ background: #28c840; }}
      .omni {{
        position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
        width: 360px; height: 26px; border-radius: 13px; background: #e8eaed;
        display: flex; align-items: center; justify-content: center; gap: 7px;
      }}
      .omni-lock {{ display: block; width: 9px; height: 10px; border-radius: 1px;
                   border: 1.6px solid #7b838d; border-top-width: 4px; }}
      .omni-txt {{ font-size: 13px; color: #5f666e; }}
      .win-edge {{
        position: absolute; left: {WIN_X}px; top: {WIN_Y}px; width: {WIN_W}px; height: {WIN_H}px;
        border-radius: 18px;
        box-shadow: inset 0 1px 0 rgba(255,255,255,.55), inset 0 0 0 1px rgba(255,255,255,.10);
      }}
      video.shot {{
        position: absolute; left: {WIN_X}px; top: {VID_Y}px; width: {WIN_W}px; height: {VID_H}px;
        border-radius: 0 0 18px 18px; object-fit: cover; object-position: top center; background: #fff;
      }}

      /* ── cards ──────────────────────────────────────────────────────
         No narration in this cut, so the card IS the voice. Short imperative
         lines only — anything longer than a glance cannot be read in the
         ~2.8s a card gets. */
      .card {{ position: absolute; inset: 0; display: grid; place-items: center; }}
      .card-in {{ position: relative; width: 1560px; text-align: center; }}
      .card-txt {{
        font-size: 96px; line-height: 1.18; font-weight: 400;
        letter-spacing: -.028em; color: var(--paper);
        text-shadow: 0 6px 40px rgba(0,0,0,.42);
      }}
      .card-txt em {{ font-style: normal; color: var(--accent); }}

      .end-mark {{ font-family: "EB Garamond", serif; font-size: 168px; letter-spacing: -.03em;
                  color: #fff; line-height: 1; text-shadow: 0 8px 50px rgba(0,0,0,.45); }}
      .end-rule {{ width: 124px; height: 2px; background: var(--accent); margin: 42px auto 34px;
                  transform-origin: 50% 50%; }}
      .end-tag {{ font-size: 38px; color: #d3dfeb; }}
      .end-foot {{ position: absolute; left: 0; bottom: 76px; width: 1920px; text-align: center;
                  font-size: 19px; letter-spacing: .22em; text-transform: uppercase; color: #9fb0c0; }}
"""

CHROME_INNER = ('<div class="lights"><span class="light l1"></span>'
                '<span class="light l2"></span><span class="light l3"></span></div>'
                '<div class="omni"><span class="omni-lock"></span>'
                '<span class="omni-txt">127.0.0.1:5173/?symbol=BTC-USD</span></div>')


def window(dur: float) -> str:
    """The browser window: one shadow, one reel, one chrome, one edge, all
    spanning every browser beat. Nothing here moves for the whole film."""
    a = f'data-start="0" data-duration="{dur}"'
    return f"""
      <div id="win-sh" class="win-shadow clip" {a} data-track-index="2"
           style="z-index:1;opacity:0"></div>
      <video id="win-vid" class="shot" src="assets/reel.mp4" muted {a}
             data-media-start="0" data-track-index="3" style="z-index:2;opacity:0"></video>
      <div id="win-ch" class="chrome clip" {a} data-track-index="4"
           data-layout-allow-occlusion="true"
           style="z-index:3;opacity:0">{CHROME_INNER}</div>
      <div id="win-ed" class="win-edge clip" {a} data-track-index="5"
           style="z-index:4;opacity:0"></div>"""


def main() -> None:
    total = t(TOTAL_BEATS)
    body, js = [], []

    body.append(f'      <audio id="soundtrack" src="assets/track.mp3" data-start="0" '
                f'data-duration="{total}" data-track-index="0"></audio>')
    body.append(f'      <div id="bg" class="bg clip" data-start="0" data-duration="{total}" '
                f'data-track-index="1" style="z-index:0">'
                f'<div class="bg-a"></div><div class="bg-b"></div><div class="bg-vig"></div></div>')

    reel_end = round(t(REEL_END_BEAT) + DISSOLVE, 3)
    body.append(window(reel_end))
    js.append(f'      settle(["#win-sh","#win-vid","#win-ch","#win-ed"], {t(REEL_END_BEAT)});')

    for i, (b0, b1, text) in enumerate(CARDS, 1):
        start, dur = t(b0), round(t(b1) - t(b0) + DISSOLVE, 3)
        body.append(f"""
      <div id="card{i}" class="card clip" data-start="{start}" data-duration="{dur}"
           data-track-index="20" style="z-index:8">
        <div class="bg"></div><div class="bg-a"></div><div class="bg-b"></div><div class="bg-vig"></div>
        <div class="card-in"><div class="card-txt" id="c{i}t">{text}</div></div>
      </div>""")
        js.append(f'      card("card{i}", "#c{i}t", {start}, {round(t(b1)-t(b0),3)});')

    es, ee = END
    body.append(f"""
      <div id="endcard" class="card clip" data-start="{t(es)}" data-duration="{round(total-t(es),3)}"
           data-track-index="21" style="z-index:9">
        <div class="bg"></div><div class="bg-a"></div><div class="bg-b"></div><div class="bg-vig"></div>
        <div class="card-in">
          <div class="end-mark" id="wordmark">Pivot.</div>
          <div class="end-rule" id="endrule"></div>
          <div class="end-tag" id="tagline">The analyst at your chart.</div>
        </div>
        <div class="end-foot" id="endfoot">NSE &amp; BSE &amp; MCX · Register, not execute</div>
      </div>""")

    e0 = t(es)
    js.append(f"""
      gsap.set("#endcard", {{ opacity: 0 }});
      gsap.set("#wordmark", {{ opacity: 0, y: 28 }});
      gsap.set("#endrule", {{ scaleX: 0 }});
      gsap.set("#tagline", {{ opacity: 0, y: 16 }});
      gsap.set("#endfoot", {{ opacity: 0 }});
      tl.to("#endcard", {{ opacity: 1, duration: {DISSOLVE}, ease: "power1.inOut" }}, {e0});
      tl.to("#wordmark", {{ opacity: 1, y: 0, duration: 1.0, ease: "power3.out" }}, {round(e0+0.18,3)});
      tl.to("#endrule", {{ scaleX: 1, duration: .8, ease: "power2.out" }}, {round(e0+0.95,3)});
      tl.to("#tagline", {{ opacity: 1, y: 0, duration: .9, ease: "power3.out" }}, {round(e0+1.4,3)});
      tl.to("#endfoot", {{ opacity: 1, duration: .9, ease: "power2.out" }}, {round(e0+2.5,3)});""")

    html = f"""<!doctype html>
<html lang="en" data-resolution="landscape">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <title>Pivot — the analyst at your chart</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>{CSS}    </style>
  </head>
  <body>
    <!-- GENERATED BY build.py — edit build.py, not this file. -->
    <div id="root" data-composition-id="main" data-start="0"
         data-width="1920" data-height="1080" data-duration="{total}" data-fps="30"
         data-layout-allow-overflow="true">
{"".join(body)}
    </div>

    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
      const D = {DISSOLVE};

      /* The window arrives once, holds for the whole reel, and leaves under
         the closing card. Every push-in lives in the footage, so the mockup
         itself never moves — which is the only reason it stays on screen. */
      function settle(sel, out) {{
        gsap.set(sel, {{ opacity: 0, scale: 0.985 }});
        tl.to(sel, {{ opacity: 1, scale: 1, duration: 1.0, ease: "power3.out" }}, 0.12);
        tl.to(sel, {{ opacity: 0, duration: D, ease: "power1.inOut" }}, out);
      }}

      /* Cards cut hard IN on the downbeat and dissolve OUT — the hard entry is
         what makes them feel locked to the music. */
      function card(id, txt, at, dur) {{
        gsap.set("#" + id, {{ opacity: 0 }});
        gsap.set(txt, {{ opacity: 0, y: 26, scale: 0.985 }});
        tl.to("#" + id, {{ opacity: 1, duration: 0.10, ease: "none" }}, at);
        tl.to(txt, {{ opacity: 1, y: 0, scale: 1, duration: 0.62, ease: "power3.out" }}, at + 0.04);
        tl.to("#" + id, {{ opacity: 0, duration: D, ease: "power1.inOut" }}, at + dur);
        tl.set("#" + id, {{ opacity: 0 }}, at + dur + D);
      }}

{chr(10).join(js)}

      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""
    (ROOT / "index.html").write_text(html)
    print(f"wrote index.html — {total}s, {TOTAL_BEATS} beats @ {BPM} BPM")
    print(f"  reel 0 → {t(REEL_END_BEAT)}s (assets/reel.mp4), cards over the joins")


if __name__ == "__main__":
    main()
