# Workspace colors

Profile → Workspace colors replaces appearance mode controls. The eight built-in
pairs are Ivory, Charcoal, Nord, Midnight, Evergreen, Dune, Mist and Plum. Users
can edit the chart/widget surface, outer frame, borders/grid, accent and candle
colors, then save up to 24 named copies. Edits and active selection are stored in
`pivot-workspace-colors-v1` in localStorage, per browser origin. They do not sync
with an account. An explicit old light/dark preference is migrated on first use;
system preference is consulted only when choosing the initial default.

The chart drawing rail and chart/widget toolbars use the inner surface color.
The outer shell frame and far-left app navigation sidebar use the contrasting
frame color. Optional soft wash and fine
grain finishes sit under widget contents; chart canvases become transparent so
those finishes show through. Explicit chart background overrides in Chart
settings still take precedence. The embedded charts preserve their geometry, viewport and data
when the workspace palette changes. Text contrast is computed for each surface.

## Files and bridge

- Canonical runtime: `charto/preview/js/workspace-theme.js`.
- Canonical chooser CSS: `charto/preview/css/workspace-theme.css`.
- Mirror both into `pivot-next/public` and `charto/web/public` after editing.
  Root preview symlinks expose the same filenames through nginx's static root.
- Layouts load the runtime before paint. Increment all corresponding asset query
  versions when editing the runtime/CSS; keep preview script versions current.
- `pivot:workspace-colors` is the local browser event. ChartFrame forwards the
  full validated palette with `pivot:workspace-theme`; embed.js only accepts the
  same-origin parent. `theme.js` notifies all native chart subscribers, including
  changes between two palettes with the same contrast mode. Legacy mode is an
  internal contrast bridge, not a user preference control.
- Solid chart background when flat; transparent native canvas when textured.
  ChartSettings preserves explicit per-chart background/gradient choices.

## Verification

`node --test charto/preview/tests/workspace-theme.test.cjs`

`cd pivot-next && pnpm exec vitest run tests/workspace-theme-bridge.test.tsx`

The tests cover preset contrast, old preference migration, persistence, unsafe
color rejection, saved palette names, edits/reset/delete, keyboard dismissal,
short viewports, blocked storage, native chart notifications and iframe updates
without reload. Comparison, widget migration and sizing tests are also relevant
when changing native chart background handling.

## Design references

- [Slack theme customization](https://slack.com/help/articles/205166337-Change-your-Slack-theme): presets, editable colors and optional finishes.
- [TradingView chart gradients](https://www.tradingview.com/blog/en/new-chart-gradients-20981/): chart surfaces independent of surrounding chrome.
- [Apple Materials](https://developer.apple.com/design/human-interface-guidelines/materials): translucent controls with readable content and solid fallbacks.
- [Nord](https://github.com/nordtheme/nord): restrained cool colors inspired the Nord pair.

The chooser uses native color controls and CSS glass effects. No added package,
external theme dependency, database migration or remote asset fetch is required.
