# Pivot launch video — edit plan v2

Based on a Playwright walkthrough of the real `pivot-next` UI in dark
mode (screenshots in this directory) and a research pass on premium
SaaS launch reels.

## What the real product actually looks like

Reference files in this folder:

- `pivot-dashboard-empty-…png` — full app chrome, no chat thread
- `pivot-dashboard-more-examples-…png` — same with the extra prompt pills expanded
- `pivot-stock-detail-…png` — `/stock/RELIANCE` rich page
- `pivot-widgets-dark-…png` — `StockSnapshotCard` + `WorkflowDraftCard` side by side, dark theme
- `pivot-widgets-side-by-side-…png` — same two widgets in **light** theme for comparison

Concrete things in the real UI that neither current video shows:

1. **Three mode pills sit BELOW the composer** — `⚡ Automation`, `⚙ Agent`, `📈 Backtest`. This is the "modal" of the product and both videos miss it entirely.
2. **Right rail "Active Agents"** — three idle agent cards each with a `Strategy` tag, an `Idle` status pill with a gray dot, and a "Never run" line. The cards are the visual proof that this is an agent system, not a chat app.
3. **Index pill cards** use a **green-tinted soft pill** for the change value (`+277 (+1.18%)` sits on `rgba(16,185,129,0.15)`). My videos render it as flat green text and lose the chip texture.
4. **`StockSnapshotCard` header** includes a `Delayed` / `Live` badge directly under the price + time. Missing from my video's card.
5. **`WorkflowDraftCard` is its own thing** — much richer than the simple "agent card" the launch video drew:
   - Sky-blue `Agent` chip top-left, `Draft` chip + counter top-right
   - Four **pill-row steps** (each `step_type` rendered with its own lucide icon: `CalendarClock`, `Wallet`, `GitBranch`, `ShoppingCart`)
   - `Why this?` sparkle-icon link that expands the rationale
   - **Big white pill `Save & activate ↗`** as the primary CTA (not a green button)
   - Ghost-text secondary actions: `🕒 Backtest · Open in editor`
   - **Amber warning footer**: "This is automation of your instructions, not financial advice."

This last card is the single most product-distinctive widget — it's the one moment that says "we turn a sentence into a structured plan." It should be the climax of any launch reel.

## Color commitment — dark only

The cream + Instrument Serif direction in `pivot-launch/` was a beautiful editorial pass but doesn't match the actual product (which is near-black `#0d0d0e` ink-on-paper inverted). User said "remember the color theme of black and white." Recommendation:

- **Promote `my-video/`** (dark, Quartr-inspired) as the canonical launch reel — all subsequent edits apply here.
- **Park `pivot-launch/`** as a possible secondary "landing page" reel for the marketing site, where the cream/editorial style is acceptable. But until the in-product look matches, don't ship it.

## Gap analysis: what `my-video` should add

| Gap | Real UI does this | My video shows this | Fix |
| --- | --- | --- | --- |
| Composer mode pills | Yes — 3 pills under composer | Missing | Add `[Automation] [Agent] [Backtest]` row below the pill in Scene 3-5 |
| Active Agents right rail | Yes — 3 idle cards always visible | Missing | Add a 280px right rail in `AppShell.tsx` with three idle cards (INFY weekly dip-buy / TCS monthly SIP / RELIANCE 3:55 PM weekday buy) |
| Green soft-pill on index changes | Yes — soft green chip | Flat green text | Change index strip to wrap change pct in a rounded soft-bg chip |
| `Delayed`/`Live` badge | Yes — under price + time | Missing | Add a tiny pill with green dot + "Live" or muted "Delayed" |
| Witty phrase loop | "Pulling up RELIANCE… / Checking last close… / Drawing the 1Y sparkline…" cycles | Single phrase | Already partly there — make sure all three cycle visibly |
| WorkflowDraftCard moment | The product's hero widget | Not in `my-video` at all | **NEW SCENE 7** described below |
| Newsreader serif fidelity | Greeting uses Newsreader, italic-feeling p | Already correct in `my-video` | Keep |
| `Save & activate ↗` button styling | White rounded pill with arrow, full-width | (N/A — scene missing) | Build per spec below |

## Recommended new scene order (24-28 s, dark)

```
┌── 0 ──────────────────────────── 720 ish ──┐
│ 1 LOGO FORMS                  0–110        │
│ 2 SHELL MATERIALIZES        100–180        │
│ 3 GREETING + CHIPS          175–270        │
│ 4 PROMPT TYPING (zoom in)   260–370        │
│ 5 SUBMIT + WITTY THINKING   365–435        │
│ 6 SNAPSHOT CARD             430–540  (NEW: smaller, faster)
│ 7 WORKFLOW DRAFT CARD       540–670  (HERO — new scene)
│ 8 ACTIVE AGENTS 3→4         670–730        │
│ 9 OUTRO                     720–810        │
└────────────────────────────────────────────┘
```

The current video peaks on the Snapshot card. The proposed plan peaks on the WorkflowDraftCard instead, because that's the moment that says "we turn intent into automation" — the actual product thesis.

### Scene 7 — Workflow Draft Card spec (new, hero)

Frames are scene-local.

| Frame range | Beat | Detail |
| --- | --- | --- |
| 0–14 | Card lifts up under the thinking phrase | Spring `{damping:14, mass:0.6, stiffness:120}`. Card slides up 16 px + fades from 0 → 1. Box-shadow grows from `0 0 0 rgba(…)` → `0 30px 80px rgba(0,0,0,0.45)`. |
| 8–22 | Header chips appear | "Agent" sky chip slides from `translateX(-6px)` to 0 with fade. "1 · Draft" counter+chip flip in from `scale(0.6)` with overshoot. |
| 20–34 | Title types in | "RELIANCE Weekday Dip-Buy" — 1 char per ~1.2 frames (faster than the prompt typing). No cursor — looks polished. |
| 30–44 | Description fades | Below title, two short lines fade up. |
| 38–46 | "Why this?" sparkle link draws in | Subtle. |
| 44–96 | Four step pills stagger | Each pill: 12-frame fade + translateX 8 px → 0. Stagger by 8 frames. Icon scales from 0.7 → 1 with a tiny overshoot — feels like the icon is "snapping into" the row. |
| 96–110 | "Save & activate ↗" pill appears | Slide up + soft glow. |
| 110–120 | Backtest · Open in editor links fade | Ghost text. |
| 118–124 | Amber footer slides up | "This is automation of your instructions, not financial advice." |
| 124–150 | Camera pushes into the card | Continuous z-axis zoom from 1.0 → 1.18, anchored on the card center. Background blurs `blur(0)` → `blur(6px)` for depth-of-field. |

### Scene 8 — Active Agents 3 → 4

| Frame range | Beat |
| --- | --- |
| 0–18 | Camera pans right (translateX), Workflow card recedes, right rail centers on screen |
| 18–34 | A new card slides in at the top with a **green Live dot pulsing at 1 Hz** and secondary "Active · Next: Fri 3:55 PM" |
| 34–60 | Counter pill goes `3 → 4` with a 1-frame scale dip at the changeover (mechanical-clock feel) |
| 60–80 | Hold. Gentle backdrop dim begins, prepping outro. |

## Premium-reel techniques to apply (from research)

These are the moves that read as "expensive" without adding many frames:

### Camera

- **One continuous z-axis push per long beat.** 85% of high-performing SaaS videos commit to un-cut zooms instead of stitching scenes with hard cuts ([advids.co](https://advids.co/blog/saas-product-animation-video)). My current `cameraAt()` already does this between Scenes 3-7 — extend to also push into Scene 7's card.
- **Lateral y-axis pan** when moving between left chat / right rail in Scene 8 (Airtable's signature move). Avoids a "cut" and reads as exploration of one connected canvas.
- **Tiny parallax on the bg** — when camera zooms 1.0 → 1.2, sidebar moves at scale 1.12 instead of 1.2 so it lags behind. ~3 lines of math in `AppShell`.

### Depth + focus

- **CSS `filter: blur(N)` on backdrop** when foregrounding a card. Frame 480 of my current video would benefit from `blur(4-8px)` on the chrome behind the Snapshot card so it pops.
- **Card shadow ramps up during push-in.** Shadow alpha + offset interpolated with the same camera scale.

### Typography motion

- **Two-tone serif lines** that stagger in (Muddlenotes / Stripe.press): `ink` line then `gray` line, 8 frames apart, easing `(0.16, 1, 0.3, 1)`. Already in pivot-launch outro; port to my-video.
- **Single-line green underline draw** under the key noun in a tagline (e.g. under "instructed" in "Trading, instructed in plain English") — 60-80 frames, ease-out, anchored to the baseline.

### Text reveal

- **Character-stagger typing** for the prompt (already there). Tweak: pause 6 frames after the last char before submit so the eye lands; right now we push to submit too fast.
- **Word-by-word reveal** for the AI response (instead of char-by-char) — feels more like "the model is forming a thought," not typing. Use the witty-phrase loop during the gap.

### Sound (optional but huge)

ASMR-style UI sound design gets save rates 3.2× higher than silent product video on TikTok/IG in 2026 benchmarks ([influencers-time.com](https://www.influencers-time.com/asmr-and-sensory-content-formats-that-algorithms-reward/)). Adding:

- **Soft key-clack on each typed char** (one short sample, pitched ±2 semitones randomly per char)
- **"Whoosh" on each scene transition** (~200 ms, low-pass filtered)
- **"Tick" on the 3 → 4 counter changeover**
- **"Snap" on Save & activate press**
- **−18 dB ambient pad bed** that fades in over 30 frames and out over 30

All sourceable royalty-free from Pixabay → drop in `my-video/public/audio/`, wire with `<Audio>` from `@remotion/media`.

### Visual cinematic finish

- **Light leak overlay** at the end (`@remotion/light-leaks`) — adds a 200 ms warm gradient sweep that reads as "film light" without going cheesy.
- **HtmlInCanvas + WebGL post-processing** for the logo formation: subtle bloom on each bar as it lands.
- **Lottie equalizer** if the hand-coded one feels stiff at the 6× hero scale. The bars could "audio-react" to a 60 fps imaginary track.

## Concrete first edit pass to make (priority order)

Each is small and unlocks the next.

1. **Add the right rail `Active Agents` to `my-video/src/scenes/AppShell.tsx`** — three idle cards. ~80 lines. (Highest realism gain.)
2. **Add the 3 mode pills under composer** in `my-video/src/scenes/Composer.tsx` — `Automation / Agent / Backtest`. ~30 lines. (Closes the most visible gap.)
3. **Wrap index change pcts in a soft green pill** in `ChatGreeting.tsx`. ~10 lines.
4. **Add `Delayed` badge** to the snapshot card header. ~10 lines.
5. **Build a `WorkflowDraftCardScene.tsx` in `my-video/src/scenes/`** that mirrors the real `WorkflowDraftCard`. Reuse step icons from lucide. ~250 lines.
6. **Insert that scene at frame 540–670** in `PivotShowcase.tsx`, push the existing snapshot card scene shorter (cut from 150 frames to 110), shift outro to 720+. Bump composition `durationInFrames` to 810 or 840.
7. **Add `blur(0)→blur(6px)` on `AppShell` chrome during card push-ins** — drives focus to the card.
8. **Cycle all 3 thinking phrases visibly** by extending Scene 5's duration to ≥36 frames per phrase × 3 phrases.
9. (Optional) **Audio pass** with `@remotion/media` + Pixabay UI samples.
10. (Optional) **Light leak overlay** on the outro.

## Reference assets

Stored at `docs/video-refs/`. Treat these PNGs as the source of truth when matching colors, spacing, and chip styles. Token reference:

- Bg base: `#0d0d0e` · card: `#181a1f` · elevated: `#1f2127`
- Border: `rgba(255,255,255,0.06)` · borderHover: `0.12` · focus: `0.24`
- Text: primary `#fbfcfc` · secondary `#8f98a1` · tertiary `#6b7280`
- Profit `#10b981` · loss `#ef4444` · pivot-blue (dark) `#60a5fa`
- Soft green pill bg: `rgba(16, 185, 129, 0.15)` with text `#10b981`
- Amber warning footer: `rgba(245, 158, 11, 0.04)` bg with `#f59e0b` text + `AlertCircle` icon

## Sources

- [SaaS Product Animation Video: 13 Ways To Show Feature Value (advids)](https://advids.co/blog/saas-product-animation-video) — 85% z-axis push, 71% sweeping reveals, Figma/Airtable camera patterns
- [Ultimate Product Demo Videos Guide For 2026 (whatastory)](https://www.whatastory.agency/blog/product-demo-videos-guide) — UI mockup fidelity, click-path highlights
- [Remotion best practices skill](https://www.skills.sh/remotion-dev/skills/remotion-best-practices) — interpolate / easing / sequencing patterns
- [remocn — shadcn-style Remotion component registry](https://github.com/kapishdima/remocn) — production-ready typewriter, blur reveal, shimmer sweep, demo scaffolds
- [remotion-animate-text (pskd73)](https://github.com/pskd73/remotion-animate-text) — char/word text animation patterns
- [ASMR & Sensory content formats algorithms reward](https://www.influencers-time.com/asmr-and-sensory-content-formats-that-algorithms-reward/) — 3.2× save rate for tactile/audio-first product reels
- [Product Launch Video: The Complete 2026 Guide](https://blog.messagear.com/product-launch-video/) — 30-60 s sweet spot for social, 60-90 s for landing-page hero
