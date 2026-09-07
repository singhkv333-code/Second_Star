#!/usr/bin/env python3
"""reel.py — flatten the six beat clips into ONE continuous video.

Why: the renderer stalls at ~frame 1176 of a 1815-frame job whenever six
separate <video> elements are live in the composition — the same wall showed up
in v2 with five. Frame count is not the trigger (v2 rendered 1710 frames fine
once its clips were re-encoded); the number of concurrently decoded media
elements is. Collapsing to a single element removes the failure mode entirely.

The reel is built ON the film's own timeline: each beat sits exactly where the
composition expects it, and the gaps between beats — which are always covered
by a full-screen card — are filled with the NEXT beat's first frame, so the
moment a card dissolves out the next beat is already on screen.

    python3 reel.py     # -> filmv3/assets/reel.mp4
"""
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
A = ROOT / "filmv3" / "assets"
BEAT = 60.0 / 127.0

# (clip, film-start beat, film-end beat) — mirrors build.py's layout
PLAN = [
    ("hero",      0,   8),
    ("why",      14,  28),
    ("pattern",  34,  48),
    ("trend",    54,  68),
    ("rsi",      74,  88),
    ("mtf",      94, 108),
]
ENC = ["-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p",
       "-r", "30", "-g", "15", "-keyint_min", "15", "-sc_threshold", "0",
       "-video_track_timescale", "30000", "-an"]


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    parts, cursor = [], 0.0

    for i, (name, b0, b1) in enumerate(PLAN):
        start, end = b0 * BEAT, b1 * BEAT
        if start > cursor + 0.001:                    # a card gap to fill
            still = tmp / f"still{i}.png"
            run(["ffmpeg", "-y", "-v", "error", "-i", str(A / f"beat-{name}.mp4"),
                 "-frames:v", "1", str(still)])
            hold = tmp / f"hold{i}.mp4"
            run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(still),
                 "-t", f"{start - cursor:.3f}", *ENC, str(hold)])
            parts.append(hold)
        seg = tmp / f"seg{i}.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(A / f"beat-{name}.mp4"),
             "-t", f"{end - start:.3f}", *ENC, str(seg)])
        parts.append(seg)
        cursor = end

    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    out = A / "reel.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)])

    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    print(f"  -> assets/reel.mp4   {dur}s (expected {cursor:.3f}s)")
    for name, b0, b1 in PLAN:
        print(f"     {name:<8} {b0*BEAT:6.2f}s → {b1*BEAT:6.2f}s")


if __name__ == "__main__":
    main()
