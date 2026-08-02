#!/usr/bin/env python3
"""ep.py — Epidemic Sound bench: find a track, pull it, read its beat grid.

The film is cut TO the music, so the useful output here is not just an mp3 —
it is the tempo and downbeat offset that every scene boundary snaps to.

    python3 ep.py search "driving corporate technology" --bpm 118 132
    python3 ep.py get <track_id> --name launch-bed
    python3 ep.py grid out/music/launch-bed.mp3      # tempo, downbeat, energy map

Key: EPIDEMICS_API_KEY in pivot/.env.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out" / "music"
API = "https://partner-content-api.epidemicsound.com/v0"


def key() -> str:
    for line in (ROOT.parent.parent / "pivot" / ".env").read_text().splitlines():
        if line.startswith("EPIDEMICS_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("EPIDEMICS_API_KEY not found in pivot/.env")


H = lambda: {"Authorization": f"Bearer {key()}"}


def cmd_search(a) -> None:
    q = urllib.parse.quote(a.term)
    r = requests.get(f"{API}/tracks/search?term={q}&limit={a.limit}", headers=H(), timeout=60)
    r.raise_for_status()
    for t in r.json().get("tracks", []):
        if a.bpm and not (a.bpm[0] <= t["bpm"] <= a.bpm[1]):
            continue
        if a.no_vocals and t.get("hasVocals"):
            continue
        moods = "/".join(x["name"] for x in t.get("moods", []))
        genres = "/".join(x["name"] for x in t.get("genres", []))
        print(f"{t['id']}  {t['bpm']:>4}bpm {t['length']:>4}s  {t['title'][:36]:<36} "
              f"{moods[:28]:<28} {genres[:40]}")


def cmd_get(a) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    r = requests.get(f"{API}/tracks/{a.track_id}/download", headers=H(), timeout=60)
    r.raise_for_status()
    url = r.json()["url"]
    mp3 = OUT / f"{a.name or a.track_id}.mp3"
    with requests.get(url, stream=True, timeout=300) as s:
        s.raise_for_status()
        with mp3.open("wb") as f:
            for chunk in s.iter_content(1 << 16):
                f.write(chunk)
    print(f"  -> {mp3.relative_to(ROOT)}  ({mp3.stat().st_size/1e6:.1f} MB)")


def samples(path: str, sr: int = 22050) -> np.ndarray:
    """Decode to mono float32 via ffmpeg — no audio library needed."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr),
         "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def cmd_grid(a) -> None:
    """Tempo + downbeat from an onset-strength envelope.

    Deliberately simple: a percussive launch bed has a strong, regular
    onset envelope, so autocorrelating it recovers the beat period without
    pulling in a full DSP stack. The number that matters downstream is the
    BAR length — scene cuts land on bars, not beats.
    """
    sr, hop = 22050, 256
    x = samples(a.audio, sr)
    n = len(x) // hop
    frames = x[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    # onset strength = positive change in log energy
    env = np.diff(np.log(rms), prepend=np.log(rms[0]))
    env = np.maximum(env, 0)
    env -= env.mean()
    fps = sr / hop

    # autocorrelation over a plausible tempo range
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 90)     # 90-180 BPM
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lag = lo + int(np.argmax(ac[lo:hi]))
    bpm = 60 * fps / lag
    # fold into a musical range
    while bpm < 100: bpm *= 2
    while bpm > 175: bpm /= 2
    beat = 60 / bpm
    bar = beat * 4

    # downbeat: the bar phase whose beat positions carry the most onset energy
    step = beat * fps
    best, best_off = -1e9, 0.0
    for off in np.arange(0, bar, 0.01):
        idx = np.round((off + np.arange(0, len(env) / fps - bar, beat)) * fps).astype(int)
        idx = idx[idx < len(env)]
        s = env[idx].sum()
        if s > best:
            best, best_off = s, float(off)

    # a coarse energy map so the edit can put the biggest card on the biggest lift
    secs = int(len(x) / sr)
    band = np.array([np.sqrt((x[i * sr:(i + 1) * sr] ** 2).mean() + 1e-12) for i in range(secs)])
    band = band / band.max()

    print(f"tempo      {bpm:.2f} BPM   beat {beat:.4f}s   bar {bar:.4f}s")
    print(f"downbeat   {best_off:.3f}s")
    print(f"bars @     " + " ".join(f"{best_off + i*bar:.2f}" for i in range(12)))
    print("energy/s   " + "".join("▁▂▃▄▅▆▇█"[min(7, int(v * 8))] for v in band[:100]))
    print(json.dumps({"bpm": round(bpm, 3), "beat": beat, "bar": bar,
                      "downbeat": round(best_off, 3),
                      "energy": [round(float(v), 3) for v in band]}))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search"); s.add_argument("term")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--bpm", type=int, nargs=2)
    s.add_argument("--no-vocals", action="store_true")
    s.set_defaults(fn=cmd_search)

    g = sub.add_parser("get"); g.add_argument("track_id"); g.add_argument("--name")
    g.set_defaults(fn=cmd_get)

    d = sub.add_parser("grid"); d.add_argument("audio"); d.set_defaults(fn=cmd_grid)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
