# Pivot — Launch Video

A 24-second editorial product video for Pivot's prelaunch announcement,
built with [Remotion](https://www.remotion.dev). Cream-and-ink palette
inspired by Public.com × Linear × Muddlenotes. All chat / agent /
backtest content is mocked — no API calls, no live data.

## Outputs

After running the render commands below you'll get two files in `out/`:

| File | Resolution | Use |
| --- | --- | --- |
| `out/pivot-launch.mp4` | 1920×1080 @ 30 fps · 24 s · H.264 (CRF 18) | Hero hero / landing page embed |
| `out/pivot-launch-vertical.mp4` | 1080×1920 @ 30 fps · 24 s · H.264 (CRF 18) | Instagram / TikTok / Shorts social cut (the master composition is letterboxed onto cream, content stays vertically centered) |

## Scenes (24 s total)

| # | Frames | Time | Beat |
| - | --- | --- | --- |
| 1 | 0–90 | 0–3 s | **LogoIntro.** Four equalizer bars build sequentially; `pivot` wordmark slides in; hero scale-down to outro size. |
| 2 | 90–180 | 3–6 s | **Tagline.** "One message." (Instrument Serif) + "That's all investing takes." (italic, gray). Green underline draws under the headline. |
| 3 | 180–300 | 6–10 s | **ChatInputZoom.** Centered Pivot input pill with ambient green glow. Prompt types in italic serif: *"Buy ₹10k of Reliance every Friday at 3:55 PM if it's down 1% or more"*. Send button pulses. |
| 4 | 300–420 | 10–14 s | **FullChatUI.** Camera zooms out to the full app: topbar (logo, search, portfolio metrics), left nav, right Active Agents rail. User bubble, typing dots, AI response, then the **Strategy Agent draft card** (Trigger / Condition / Action / Product + Backtest & Activate). |
| 5 | 420–540 | 14–18 s | **AgentBacktest.** Fake cursor moves to **Backtest**, side panel slides in from the right (Strategy · Backtest · Code · Logs tabs, with Backtest active). Chart line draws left-to-right; three stat tiles fade in (Return +12.4%, Win rate 68%, Max drawdown −3.2%); cursor clicks Activate. |
| 6 | 540–630 | 18–21 s | **ActiveAgents.** Camera pans to the Active Agents rail; a new "Live" card with a pulsing green dot slides in; the counter ticks **3 → 4**. |
| 7 | 630–720 | 21–24 s | **Outro.** "No charts." / "No clicks." / "Just conversation." stagger in; large `pivot` wordmark; "Visit **pivot.so**" with a green underline draw. |

Scenes overlap with a 6-frame cross-fade.

## File structure

```
pivot-launch/
├── src/
│   ├── Root.tsx                    # Registers PivotLaunch + PivotLaunchVertical
│   ├── PivotLaunch.tsx             # Master timeline + cross-fades
│   ├── theme.ts                    # Cream/ink/green design tokens
│   ├── fonts.ts                    # Loads Inter + Instrument Serif + JetBrains Mono
│   ├── mock.ts                     # Mocked Indian-market data
│   ├── components/
│   │   ├── PivotLogo.tsx           # Animated 4-bar equalizer + wordmark
│   │   ├── ChatBubble.tsx          # User / assistant bubbles + typing dots
│   │   ├── AgentCard.tsx           # Strategy Agent draft widget
│   │   ├── BacktestChart.tsx       # Animated SVG line chart
│   │   ├── ActiveAgentItem.tsx     # Right-rail agent card
│   │   └── AnimatedCursor.tsx      # Fake macOS-style cursor + click ripple
│   └── scenes/
│       ├── Scene1_LogoIntro.tsx
│       ├── Scene2_Tagline.tsx
│       ├── Scene3_ChatInput.tsx
│       ├── Scene4_FullChatUI.tsx
│       ├── Scene5_AgentBacktest.tsx
│       ├── Scene6_ActiveAgents.tsx
│       └── Scene7_Outro.tsx
├── public/                         # (empty — no static assets needed)
├── out/                            # Rendered MP4s land here
├── package.json
└── remotion.config.ts
```

## Commands

Install:

```bash
npm install
```

Preview in the Remotion Studio (live scrubbing):

```bash
npm run dev
```

Render the master 1920×1080:

```bash
npx remotion render PivotLaunch out/pivot-launch.mp4 --codec=h264 --crf=18
```

Render the vertical 1080×1920 social cut:

```bash
npx remotion render PivotLaunchVertical out/pivot-launch-vertical.mp4 --codec=h264 --crf=18
```

## Animation principles

All entrances follow the timing curves laid out in the build spec:

- **Spring** for "appears" — `{ damping: 18, mass: 0.6, stiffness: 120 }`
- **Bezier** for fades — `Easing.bezier(0.16, 1, 0.3, 1)`
- **Stagger** related elements by 4–8 frames
- No linear easing on visible motion
- Subtle shadow only: `0 1px 2px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.04)`

Per Remotion's rules, no CSS transitions / animations or Tailwind animate
utilities are used — everything is driven by `useCurrentFrame()`,
`interpolate()`, and `spring()` so frames are deterministic.

## Audio

The base render ships silent. To add the optional ambient piano bed
described in the build spec:

1. Drop a royalty-free MP3 at `public/audio/ambient.mp3` (e.g. a minimal
   piano loop from Pixabay).
2. Add this to the top of `PivotLaunch.tsx`:

   ```tsx
   import { Audio, staticFile, useVideoConfig, interpolate } from "remotion";

   // inside the composition, before the Sequences:
   const { durationInFrames, fps } = useVideoConfig();
   const fadeFrames = 30;
   const volume = (f: number) =>
     interpolate(
       f,
       [0, fadeFrames, durationInFrames - fadeFrames, durationInFrames],
       [0, 0.4, 0.4, 0],
       { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
     );

   <Audio src={staticFile("audio/ambient.mp3")} volume={volume} />
   ```

## Mock data

All copy lives in `src/mock.ts` — Indian-market only, English-only,
deterministic numbers (no Lorem ipsum):

- Portfolio value `₹77,945`, Day P&L `+₹294`, Total P&L `+₹3,355 (+4.50%)`
- Agent: `RELIANCE Weekday Dip-Buy` — Friday 3:55 PM IST, dip ≥ 1%, market buy ₹10,000, CNC
- Backtest stats: Return `+12.4%`, Win rate `68%`, Max drawdown `−3.2%`
- Existing agents: `INFY weekly dip-buy`, `TCS monthly SIP`, `RELIANCE 3:55 PM weekday buy`

## License

This project uses Remotion. For commercial use, see the [Remotion license](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md).
