# Charto media bench — what ElevenLabs actually gives us, and what to build with it

> Verified against the live key on **2026-08-02**. Everything in §1 was probed,
> not assumed; §2–§4 is the production plan that follows from it. Sample assets
> produced during the probe live in `out/` (gitignored) — audition them before
> reading further.

---

## 1. What this key can do (probed, not guessed)

**Account:** grant tier, **33,010,000 characters**, 38.6k used (**0.12%**).
Instant *and* professional voice cloning enabled, 660 voice slots.

That budget is the headline. 33M characters is roughly **600+ hours** of
narration. The entire asset set below — a 36s hero VO, a 40s music bed, four
SFX, three designed-voice auditions — cost **2,289 characters**. Cost is not a
constraint on this project; we should be generating variants freely and
throwing most of them away, not rationing takes.

| Capability | Endpoint | Status | What it's for here |
|---|---|---|---|
| TTS, expressive | `/v1/text-to-speech` `eleven_v3` | ✅ | Narration. Supports inline `[audio tags]` — `[pause]`, `[thoughtful]`, `[excited]` — so a script controls delivery without re-recording |
| **TTS + word timings** | `.../with-timestamps` | ✅ | **The load-bearing one — see §3** |
| TTS, fast | `eleven_flash_v2_5` (~75ms) | ✅ | Scratch takes while iterating on a cut |
| Sound effects | `/v1/sound-generation` | ✅ | UI clicks, whooshes, chimes, `loop:true` for ambience |
| Music | `/v1/music` | ✅ | Full instrumental beds from a prose prompt, arbitrary length |
| Voice design | `/v1/text-to-voice/design` | ✅ | Invent a narrator from a description; 3 auditions per call |
| Instant voice clone | `/v1/voices/add` | ✅ | Clone the founder from ~1 min of audio |
| Professional clone | 3 slots | ✅ | Broadcast-grade brand voice (needs ~30min + consent verification) |
| Forced alignment | `/v1/forced-alignment` | ✅ | Word timings for audio we *didn't* generate (a real founder take) |
| Speech-to-text | `/v1/speech-to-text` `scribe_v1` | ✅ | Word-level + diarized transcripts of screen recordings |
| Dubbing | `/v1/dubbing` | ✅ | Same video → Hindi/Tamil/Telugu, voice preserved |
| Conversational agents | `/v1/convai/agents` | ✅ | Real-time voice agent — see §4.5 |
| Studio (long-form projects) | `/v1/studio/projects` | ❌ 403 | Not on this tier. No loss: `el.py` covers the same ground |
| Pronunciation dictionaries | `/v1/pronunciation-dictionaries` | ❌ 401 | Workaround in §3.3 |

**Renderer side is already in place:** `ffmpeg` and **HyperFrames 0.7.88**
(HTML→MP4, Lambda rendering) are installed. HyperFrames ships its own `tts`
command but it's local Kokoro-82M — noticeably worse than `eleven_v3`. Use
`el.py` for voice and hand HyperFrames the finished MP3 + timings.

---

## 2. What Charto has that is worth filming

Charto is far past the "nothing built" state `CHARTO.md` describes. From the
running preview (`:5173`) and `data/dataserver.py`, these are real, and these
are the ones that *demo*:

| Beat | Backed by | Why it lands on camera |
|---|---|---|
| **Chat drives the chart** | scene model, `scene.js`, 23 tools | The whole pitch in one gesture — type, chart redraws |
| **Evidence-on-hover** | `evaluate_pattern`, `evaluate_line`, `evaluate_fib` | *The* differentiator. A hit-rate **with a control arm** — nobody ships this |
| **"Why did it move"** | `explain_move` + `search_news` | India white space; and it says "no clear catalyst" out loud |
| **Draw a line, get a verdict** | `evaluate_drawing` | Universally legible — anyone who has drawn a trendline gets it instantly |
| **Screen 500 symbols** | `screen_universe`, 17-feature matrix, 1.26s | Speed is visual; a 500-row sweep resolving in ~1s is a shot |
| **Volume profile / POC** | `volume_profile` (measured row height) | Recognisable to every trader |
| **Position plan** | `plan_position` (TP/SL/R:R/ATR) | The register-not-execute payoff shot |
| **11.5 years of 1-min bars** | 24.4GB universe DB, 413M bars | A number to put on screen; it justifies every claim above |

The through-line for every script: **"nothing gets drawn that wasn't
computed."** That's the constitution (§2.2) *and* the marketing claim, which is
a rare piece of luck — lead with it.

---

## 3. The non-obvious exploit: word timings drive the visuals

This is the technique that separates a real product film from a screen
recording with music over it.

`el.py say` returns `<name>.words.json` — every word with start/end to the
millisecond:

```json
{"word": "measures", "start": 8.912, "end": 9.401}
```

Because HyperFrames compositions are **HTML on a single deterministic
timeline**, that JSON can *be* the edit:

1. **Frame-exact annotation sync.** The support line draws itself at
   `words["measures"].start`. Nobody hand-times a keyframe; change the script,
   re-run `say`, and every animation re-times itself.
2. **Kinetic captions for free.** Word-by-word highlight straight off the same
   array — the `embedded-captions` skill's identities (`anchor`, `keynote`,
   `terminal`) consume exactly this shape.
3. **Reverse-editing a real demo.** Screen-record Charto being used, run
   `el.py stt` on it, and the word-level transcript tells you where to cut and
   where to drop callouts. No scrubbing.
4. **Scripted → recorded swap.** Record with the synthetic VO as a guide track,
   later replace it with a founder take, run `el.py align`, and the timings
   file has the same shape — the composition re-times without a re-edit.

Two gotchas found during the probe:

- The 86-word hero script ran **36.3s**, not 30 — `eleven_v3` narrates at
  ~140wpm. Budget **~2.3 words/second**, or pass `--speed 1.1`.
- No pronunciation dictionary on this tier. Ticker names will mangle. Spell
  them phonetically in the script file (`NIFTY` → `Nifty`, `NSE` → `N-S-E`) and
  keep a fixed `--seed` so a re-run doesn't shift the delivery under a locked
  edit.

---

## 4. The slate

Five pieces, in build order. Every one uses the same bench.

### 4.1 Launch film — two options ✅ **BUILT**
**v3 · `filmv3/renders/pivot-launch-60s.mp4` (60.5s, music-cut capability reel)**
— Epidemic Sound "The Transmission" at 127 BPM, no narration; six cards name a
capability and the product does it; one baked zoom per beat; macOS-style
wallpaper. Every boundary is an integer beat. Notes + the three non-obvious
failures: `filmv3/README.md`. Toolchain: `ep.py` (music), `record-v3.mjs`,
`zoom.py`, `reel.py`, `filmv3/build.py`.

### 4.1b Narrated cut — 57s (v2)
`filmv2/renders/pivot-demo-57s.mp4` — a **screen recording of a real driven
session** (BTC-USD daily, live patterns, three questions) shown in a macOS
browser mockup on a dark desktop, cut against the narration word map, with four
statement cards. Build notes and the gotchas that cost time: `filmv2/README.md`.
Recorder: `record.mjs`.

### 4.1c First draft — 30s (superseded)
`film/renders/charto-demo-30s.mp4` — 1920×1080, 30fps, −14.3 LUFS, every frame
the real product running on live data. Cold open on a hand-marked chart, the
strokes wipe, chat takes over; the evidence table and the base-rate paragraph
each get a push-in with the spoken number underlined on its own syllable. Build
notes, the cut sheet, and the crop-zoom math: `film/README.md`.

### 4.2 Feature shorts — 15–20s each, vertical 9:16
One per §2 row, cut for X/LinkedIn. These are the volume play: one screen
capture, one VO line, one SFX hit. The evidence-on-hover one is the single most
shareable asset we can make — a hit-rate **next to its control arm** is an
argument nobody else in the category can make.

### 4.3 Motion graphics — no capture needed
Pure HyperFrames, `motion-graphics` skill, renders to transparent MP4 for
overlay:
- **"413 million bars"** count-up sting.
- **Transmission map** — the belief→cause→effect chain animating out
  (`thematic_map.py` has the real data).
- **Evidence ladder** — the §5 hierarchy building rung by rung. This is the
  explainer that makes "honest confidence" concrete instead of a slogan.
- **Logo sting** with the `confirm-chime` SFX.

### 4.4 Hindi cut — the India-first move
`/v1/dubbing` on the finished hero, voice character preserved. Charto is
explicitly built for Indian retail and the product already speaks Hinglish;
shipping a Hindi film at launch is on-brand, and it costs one API call per
language rather than a re-record. Do **Hindi first**, then Tamil/Telugu if it
lands.

### 4.5 The one nobody expects: a talking Charto
`/v1/convai` is live on this key. Wire a conversational agent to the same 23
`dataserver.py` tools and Charto answers **out loud** while the chart redraws.
As a demo-day artifact this is disproportionate — the pitch is "an analyst
standing at your chart," and a voice makes that literal in a way no caption
does. Constitution-safe: the agent is a mouth on the existing tools, it never
computes.

---

## 5. Using the bench

```bash
PY=../../pivot/.venv/bin/python      # `requests` lives in the pivot venv

$PY el.py usage                       # budget check
$PY el.py voices                      # 21 library voices + anything we designed
$PY el.py design "calm Indian male narrator, early thirties, neutral urban accent"
$PY el.py keep <generated_voice_id> "Charto Narrator"
$PY el.py say scripts/hero-30s.txt --voice "Charto Narrator" --seed 4242
$PY el.py sfx "soft muted UI click, tactile, no reverb" -d 0.8 --name ui-click
$PY el.py music "calm ambient fintech bed, no drums" --ms 40000
$PY el.py align founder-take.wav scripts/hero-30s.txt
$PY el.py stt screen-capture.mp4 --diarize
```

Every asset appends to `out/manifest.jsonl` (kind, path, prompt, duration,
voice, model) so a composition references assets by name and the provenance of
every sound in the film is one `grep` away.

**Audio assets are gitignored** — they're regenerable from the prompts recorded
in the manifest, and the repo shouldn't carry MP3s.

### Next concrete step
Audition `out/voices/design-*.mp3` (three takes on the Indian-narrator brief),
pick one, `el.py keep` it as **"Charto Narrator"**, and re-render the hero VO
with it. Then the 4.1 storyboard is unblocked.
