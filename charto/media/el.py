#!/usr/bin/env python3
"""el.py — the ElevenLabs bench for Charto demo/launch video production.

One CLI over every endpoint this key can actually reach (verified 2026-08-02,
grant tier: 33.0M chars, 36k used, instant + professional cloning on, Studio
403). Everything writes into media/out/ and appends a line to
media/out/manifest.jsonl so a HyperFrames composition can consume assets by
name instead of by guesswork.

    python3 el.py voices                          # list library + designed voices
    python3 el.py design "calm Indian male ..."   # 3 auditionable previews
    python3 el.py keep <generated_voice_id> NAME  # persist a preview to the library
    python3 el.py say script.txt --voice NAME     # VO + word-level timings JSON
    python3 el.py sfx "ui click, soft, short" -d 1.2
    python3 el.py music "calm ambient tech bed" --ms 30000
    python3 el.py align vo.mp3 script.txt         # timings for audio you already have
    python3 el.py stt clip.mp4                    # transcript (screen-capture VO)
    python3 el.py usage

Key is read from pivot/.env (ELEVENLABS_API_KEY) — never hardcode it.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
API = "https://api.elevenlabs.io/v1"

# Verified-good defaults for this project.
NARRATION_MODEL = "eleven_v3"            # most expressive; supports [audio tags]
FAST_MODEL = "eleven_flash_v2_5"         # ~75ms, for iterating on scratch takes
DESIGN_MODEL = "eleven_ttv_v3"


def key() -> str:
    env = ROOT.parent.parent / "pivot" / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("ELEVENLABS_API_KEY="):
            return line.split("=", 1)[1].strip()
    k = os.environ.get("ELEVENLABS_API_KEY")
    if k:
        return k
    sys.exit("ELEVENLABS_API_KEY not found in pivot/.env or environment")


H = lambda: {"xi-api-key": key()}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48]


def record(kind: str, path: Path, **meta) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "manifest.jsonl").open("a") as f:
        f.write(json.dumps({
            "kind": kind,
            "path": str(path.relative_to(ROOT)),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **meta,
        }) + "\n")
    print(f"  -> {path.relative_to(ROOT)}")


def voice_id_for(name_or_id: str) -> str:
    """Accept a voice_id verbatim, or resolve a (case-insensitive) name prefix."""
    r = requests.get(f"{API}/voices", headers=H(), timeout=30)
    r.raise_for_status()
    for v in r.json()["voices"]:
        if v["voice_id"] == name_or_id:
            return v["voice_id"]
    lowered = name_or_id.lower()
    for v in r.json()["voices"]:
        if v["name"].lower().startswith(lowered):
            return v["voice_id"]
    sys.exit(f"no voice matching {name_or_id!r} — run `el.py voices`")


# ---------------------------------------------------------------- commands


def cmd_voices(a) -> None:
    r = requests.get(f"{API}/voices", headers=H(), timeout=30)
    r.raise_for_status()
    for v in r.json()["voices"]:
        lb = v.get("labels") or {}
        tags = "/".join(x for x in (lb.get("accent"), lb.get("gender"), lb.get("age")) if x)
        print(f"{v['voice_id']:<24} {v['name'][:34]:<34} {v.get('category',''):<10} {tags}")


def cmd_design(a) -> None:
    """Prompt-designed voices. Three previews land on disk to audition."""
    body = {
        "voice_description": a.description,
        "model_id": DESIGN_MODEL,
        "text": a.text,
    }
    r = requests.post(f"{API}/text-to-voice/design", headers=H(), json=body, timeout=180)
    r.raise_for_status()
    d = OUT / "voices"
    d.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(r.json()["previews"], 1):
        f = d / f"design-{slug(a.description)[:24]}-{i}-{p['generated_voice_id']}.mp3"
        f.write_bytes(base64.b64decode(p["audio_base_64"]))
        record("voice_preview", f, generated_voice_id=p["generated_voice_id"],
               duration=p.get("duration_secs"), description=a.description)
    print("\naudition them, then: el.py keep <generated_voice_id> \"Charto Narrator\"")


def cmd_keep(a) -> None:
    body = {
        "voice_name": a.name,
        "voice_description": a.description or f"Charto demo narrator — {a.name}",
        "generated_voice_id": a.generated_voice_id,
    }
    r = requests.post(f"{API}/text-to-voice", headers=H(), json=body, timeout=120)
    r.raise_for_status()
    v = r.json()
    vid = v.get("voice_id") or (v.get("voice") or {}).get("voice_id")
    print(f"saved: {a.name} -> {vid}")


def cmd_say(a) -> None:
    """VO with character-level alignment, folded up into word timings.

    The word timings are the whole point: they let a HyperFrames composition
    put a caption, a highlight, or a chart annotation on the exact frame the
    word is spoken, instead of hand-timing every beat.
    """
    text = Path(a.text).read_text().strip() if Path(a.text).exists() else a.text
    vid = voice_id_for(a.voice)
    model = FAST_MODEL if a.fast else NARRATION_MODEL
    body = {"text": text, "model_id": model}
    if a.stability is not None:
        body["voice_settings"] = {"stability": a.stability, "similarity_boost": 0.75,
                                  "speed": a.speed}
    if a.seed is not None:
        body["seed"] = a.seed
    r = requests.post(f"{API}/text-to-speech/{vid}/with-timestamps",
                      headers=H(), json=body, timeout=300)
    r.raise_for_status()
    d = r.json()
    name = a.name or slug(text[:40])
    OUT.joinpath("vo").mkdir(parents=True, exist_ok=True)
    mp3 = OUT / "vo" / f"{name}.mp3"
    mp3.write_bytes(base64.b64decode(d["audio_base64"]))

    al = d["alignment"]
    words, cur, start = [], "", None
    for ch, s, e in zip(al["characters"], al["character_start_times_seconds"],
                        al["character_end_times_seconds"]):
        if ch.isspace():
            if cur:
                words.append({"word": cur, "start": round(start, 3), "end": round(prev_e, 3)})
                cur = ""
            continue
        if not cur:
            start = s
        cur += ch
        prev_e = e
    if cur:
        words.append({"word": cur, "start": round(start, 3), "end": round(prev_e, 3)})

    js = OUT / "vo" / f"{name}.words.json"
    js.write_text(json.dumps({"text": text, "voice_id": vid, "model": model,
                              "duration": words[-1]["end"] if words else 0,
                              "words": words}, indent=1))
    record("vo", mp3, words_json=str(js.relative_to(ROOT)), voice=a.voice, model=model,
           duration=words[-1]["end"] if words else 0, chars=len(text))
    print(f"  {len(words)} words, {words[-1]['end'] if words else 0:.1f}s")


def cmd_sfx(a) -> None:
    body = {"text": a.prompt, "duration_seconds": a.duration,
            "prompt_influence": a.influence}
    if a.loop:
        body["loop"] = True
    r = requests.post(f"{API}/sound-generation", headers=H(), json=body, timeout=180)
    r.raise_for_status()
    OUT.joinpath("sfx").mkdir(parents=True, exist_ok=True)
    f = OUT / "sfx" / f"{a.name or slug(a.prompt)}.mp3"
    f.write_bytes(r.content)
    record("sfx", f, prompt=a.prompt, duration=a.duration, loop=a.loop)


def cmd_music(a) -> None:
    body = {"prompt": a.prompt, "music_length_ms": a.ms}
    r = requests.post(f"{API}/music", headers=H(), json=body, timeout=600)
    r.raise_for_status()
    OUT.joinpath("music").mkdir(parents=True, exist_ok=True)
    f = OUT / "music" / f"{a.name or slug(a.prompt)}.mp3"
    f.write_bytes(r.content)
    record("music", f, prompt=a.prompt, ms=a.ms)


def cmd_align(a) -> None:
    """Word timings for audio we did NOT generate (a founder take, a screen recording)."""
    text = Path(a.text).read_text().strip() if Path(a.text).exists() else a.text
    with open(a.audio, "rb") as fh:
        r = requests.post(f"{API}/forced-alignment", headers=H(),
                          files={"file": fh}, data={"text": text}, timeout=600)
    r.raise_for_status()
    f = Path(a.audio).with_suffix(".words.json")
    f.write_text(json.dumps(r.json(), indent=1))
    print(f"  -> {f}")


def cmd_stt(a) -> None:
    with open(a.audio, "rb") as fh:
        r = requests.post(f"{API}/speech-to-text", headers=H(), files={"file": fh},
                          data={"model_id": "scribe_v1", "timestamps_granularity": "word",
                                "diarize": str(a.diarize).lower()}, timeout=900)
    r.raise_for_status()
    f = Path(a.audio).with_suffix(".transcript.json")
    f.write_text(json.dumps(r.json(), indent=1))
    print(r.json().get("text", "")[:400])
    print(f"  -> {f}")


def cmd_usage(a) -> None:
    r = requests.get(f"{API}/user/subscription", headers=H(), timeout=30)
    d = r.json()
    used, lim = d["character_count"], d["character_limit"]
    print(f"tier {d['tier']}  {used:,} / {lim:,} chars ({100*used/lim:.2f}%)  "
          f"voices {d['voice_slots_used']}/{d['voice_limit']}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("voices").set_defaults(fn=cmd_voices)
    sub.add_parser("usage").set_defaults(fn=cmd_usage)

    d = sub.add_parser("design")
    d.add_argument("description")
    d.add_argument("--text", default=(
        "Most traders mark up their charts by hand. Charto does the measuring, "
        "shows the evidence, and never draws a level it cannot prove."))
    d.set_defaults(fn=cmd_design)

    k = sub.add_parser("keep")
    k.add_argument("generated_voice_id")
    k.add_argument("name")
    k.add_argument("--description", default="")
    k.set_defaults(fn=cmd_keep)

    s = sub.add_parser("say")
    s.add_argument("text", help="text, or a path to a .txt script")
    s.add_argument("--voice", default="George")
    s.add_argument("--name")
    s.add_argument("--fast", action="store_true", help="flash model for scratch takes")
    s.add_argument("--stability", type=float, default=None)
    s.add_argument("--speed", type=float, default=1.0)
    s.add_argument("--seed", type=int, default=None, help="repeatable takes")
    s.set_defaults(fn=cmd_say)

    x = sub.add_parser("sfx")
    x.add_argument("prompt")
    x.add_argument("-d", "--duration", type=float, default=2.0)
    x.add_argument("--influence", type=float, default=0.4)
    x.add_argument("--loop", action="store_true")
    x.add_argument("--name")
    x.set_defaults(fn=cmd_sfx)

    m = sub.add_parser("music")
    m.add_argument("prompt")
    m.add_argument("--ms", type=int, default=30000)
    m.add_argument("--name")
    m.set_defaults(fn=cmd_music)

    a2 = sub.add_parser("align")
    a2.add_argument("audio")
    a2.add_argument("text")
    a2.set_defaults(fn=cmd_align)

    t = sub.add_parser("stt")
    t.add_argument("audio")
    t.add_argument("--diarize", action="store_true")
    t.set_defaults(fn=cmd_stt)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
