# Home composer and grainy mesh

The home in `app/components/HomeTab.tsx` opens with a dark mesh rectangle
containing Inter chart-introduction text and a shadcn CTA on the left, with a
compact glass composer on the right. Three small decorative identity cards
surround the composer. No separate capability cards or preview captions remain.

## Research and sources

- [ElevenLabs Agents](https://elevenlabs.io/agents) and the user's supplied
  ElevenLabs screenshot guide the grainy, softly blended visual direction.
  No claim is made about which rendering library ElevenLabs uses.
- [Paper Mesh Gradient](https://shaders.paper.design/mesh-gradient) provides
  organic distortion, swirl, grain mixing and grain overlay. Its
  [open-source repository](https://github.com/paper-design/shaders) is Apache-2.0.
  `@paper-design/shaders-react` is pinned to 0.0.81.
- [Prompt Kit](https://github.com/ibelick/prompt-kit) supplies the vendored
  composer primitives, with the MIT license retained alongside the code.
  Enter respects IME composition; Shift+Enter adds a newline.
- [shadcn/ui Button](https://ui.shadcn.com/docs/components/radix/button) supplies
  the existing submit button. Lucide supplies the arrow and plus icons.

## Implementation

The shader uses charcoal, muted blue-grey and silver, with no purple or glow.
It renders a fixed frame (`speed=0`) to avoid continuous animation and caps
pixel count. It loads on the client; CSS mesh gradients provide the initial
background and a fallback if WebGL is unavailable.

The composer uses a translucent gradient, backdrop blur, a fine light border,
and inset highlights. Text and placeholder remain crisp and light. Browsers
without backdrop blur receive a more opaque background.

Explore charts opens the existing chart tab. The composer submits through the
existing chat callback. The home scrolls naturally and the component stacks
when its available container width becomes narrow. Typography is Inter throughout this section
and the greeting.

`/design/home` previews the section without authentication or market data.
Composer submissions there display a local preview question; the authenticated
home sends them to chat.

## Validation

Type checking and three interaction tests pass. Coverage includes chart
navigation, empty submission, trimming/reset, Enter, multiline input, IME
composition and composition handling. Browser review checks desktop and mobile
layout and placeholder contrast. No market data or agent behavior changed.

## Compact component revision

[Catalyst](https://catalyst.app/) was inspected for its compact asset identity
cards and clean borders. Bitcoin and Ethereum logos were resolved read-only
from `charto_bars.db.instrument_logo` (`BTCUSDT`, `ETHUSDT`); Reliance came from
`company_profile` (`RELIANCE`). The image files served at those recorded URLs
are bundled under `app/public/home-assets` so this purely decorative
component does not need authenticated database calls to display its logos.
The cards use recorded candles, with dated values as described below.

## Final chart-widget revision

The prompt and all three cards stay inside the banner. Cards sit above and
beside the composer, with a separate grey chart inset and no endpoint dots.
A faint chart grid and staggered cards create depth without glow.
Native logos render without added containers, padding, borders or shadows.

The asset widgets follow Catalyst's compact identity/change/sparkline structure.
`home-market-snapshots.json` preserves their recorded source values, timestamps,
Python-computed percentage changes and SVG coordinates. BTC/ETH use 25 closed
hourly Binance candles (24 hours, 9 October 2026); Reliance uses 22 cached daily
closes (21 sessions, ending 3 September 2026). Every widget visibly displays its
date, change period and source. These are fixed historical examples, not live
quotes or a claim of current performance.

The plus opens suggested chart questions; the capability shortcut row has
been removed. The main
CTA opens the chart tab. The existing HomeTab mounts this banner before the
greeting. The three interaction tests cover navigation/submission, IME/multiline
input, and selecting a question through the plus menu.

## Layered revision — 10 October

The desktop banner is 296px at the reviewed 1440px container. Four cards
surround the central glass composer; intentional overlap is limited to the
scene. Hover/focus brings a card forward so its interval buttons stay usable.
The grey plot inset, price/change badge, and allocation structure follow the
user's Catalyst references (https://catalyst.app/). Shadcn chart examples
(https://ui.shadcn.com/charts/pie) informed the restrained donut styling; the
small recorded plots use SVG, without adding a chart runtime dependency.

BTC and ETH prices are USD; Reliance is INR. Interval buttons switch between
recorded series with Python-computed changes and coordinates. Source and
as-of metadata stay in hover/accessible descriptions rather than visible
footers. ALL means all bundled historical bars (up to 100), not all-time data.
The portfolio is explicitly an example: ten shares each of Reliance, TCS and
Infosys, valued from their recorded cached daily closes. It is not an account
balance, and no user-state database was read.

## Visibility revision

Cards are reduced to 190px on desktop and spread across the available scene.
The two line cards, candlestick card and portfolio remain exposed around the
composer. Narrow containers use a staggered grid with the composer between
card rows. The allocation ring has a thinner stroke, larger inner opening,
and smaller center labels. Browser review checks full containment and no
horizontal overflow at a 480px viewport.

## Corner layout refinement

The latest desktop arrangement restores the central composer with light edge
overlap: Bitcoin above-left, Ethereum at the top-right, Reliance below-left
and portfolio below-right. Portfolio uses an opaque white surface, stronger
allocation colors and a compact horizontal total/ring layout. The small-screen
grid keeps the prompt between the upper and lower card pairs.
