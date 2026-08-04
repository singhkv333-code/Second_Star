#!/usr/bin/env python3
"""zoom.py — bake per-beat digital zooms into the recording.

Scaling the whole browser mockup was the obvious way to push in, and it was
wrong: at the window's size any scale past ~1.16 pushes its edges off-frame, so
the mockup — the thing that makes the film look designed — vanishes for most of
the runtime.

Instead the zoom happens INSIDE the footage, the way a screen-recording app
does it. The window stays exactly where it is; the content moves. A bonus:
the capture is 3024x1800 shown in a 1660px box, ~1.8x of headroom, so a 1.45x
digital zoom is still a downscale on screen and stays sharp.

    python3 zoom.py            # writes filmv3/assets/beat-*.mp4
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "out" / "rec3" / "session.mp4"
OUT = ROOT / "filmv3" / "assets"

HOLD = 1.15        # seconds at 1.0x before the push — the mockup gets a beat
RAMP = 3.55        # seconds of movement
DUR = 7.05         # clip length (beat 6.614s + dissolve headroom)

# id, media start, focal x, focal y (normalised in the frame), target zoom
BEATS = [
    ("why",      28.9, 0.865, 0.44, 1.36),   # the reasoning column
    ("pattern",  50.2, 0.865, 0.38, 1.34),   # the signals list (text, not drawn)
    ("trend",    71.4, 0.380, 0.46, 1.32),   # the trendlines
    ("rsi",      92.8, 0.430, 0.80, 1.44),   # the RSI sub-pane
    ("mtf",     120.8, 0.865, 0.24, 1.46),   # the per-interval table
    ("hero",      0.2, 0.500, 0.50, 1.06),   # a barely-there drift
]


# `crop` evaluates w/h ONCE at init — only x/y are per-frame — so a moving
# crop size is impossible there. zoompan is the filter that varies zoom per
# frame. Its z is clamped to >= 1, so the window (s=) is sized just under the
# source instead: at z=1 we see the whole frame minus the status strip, and
# zooming in from there never has to upscale past the capture's own pixels.
OUT_W, OUT_H = 3024, 1750     # 1.728:1, matching the 1660x960 window box
FPS = 30


def vf(cx: float, cy: float, zmax: float) -> str:
    """Smoothstep zoom held on a focal point, in the footage itself."""
    t = f"(on/{FPS})"
    p = f"min(1,max(0,({t}-{HOLD})/{RAMP}))"
    sm = f"({p})*({p})*(3-2*({p}))"
    z = f"1+{zmax - 1:.4f}*{sm}"
    # focal point stays put: crop centre = focal, clamped inside the frame
    x = f"clip(iw*zoom*{cx}-{OUT_W // 2},0,iw*zoom-{OUT_W})"
    y = f"clip(ih*zoom*{cy}-{OUT_H // 2},0,ih*zoom-{OUT_H})"
    return (f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={OUT_W}x{OUT_H}:fps={FPS},"
            f"setsar=1")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, start, cx, cy, zmax in BEATS:
        dst = OUT / f"beat-{name}.mp4"
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", str(start), "-t", str(DUR), "-i", str(SRC),
            "-vf", vf(cx, cy, zmax),
            "-c:v", "libx264", "-crf", "16", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-r", "30",
            "-g", "15", "-keyint_min", "15", "-sc_threshold", "0",
            "-movflags", "+faststart", "-an", str(dst),
        ]
        subprocess.run(cmd, check=True)
        print(f"  -> assets/{dst.name}  ({start}s, {zmax}x @ {cx},{cy})")


if __name__ == "__main__":
    main()
