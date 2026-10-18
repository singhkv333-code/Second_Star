# Share card: the whole platform, the product's own domain, a real link preview

Follow-up to `2026-09-30-shared-setups.md`. Chart app (`charto/data`,
`charto/preview`) plus the landing metadata in `charto/web`.
**Status: written and tested locally, uncommitted, not pushed, not deployed.**

## What changed

- **Link host.** `linkFor` (`preview/js/setups.js`) and `_share_page`
  (`data/dataserver.py`) always use `https://app.pivotnow.in`, even when the page was
  opened through the Azure hostname. Only a localhost dev host keeps its own origin.
  The share page's canonical, `og:url`, `og:image` and redirect all follow.
- **The card is a real screenshot of the tab.** `preview/js/workspace-card.js` asks
  the browser for this tab (`getDisplayMedia`, `preferCurrentTab`) and takes one
  frame, so the picture is the real pixels: the shell sidebar, header, chart, widgets
  and chat. While the frame is taken the share dialog steps aside (the shell then
  stops floating the chart frame and the layout returns to normal), a clear shield
  takes the cursor off the chart so no crosshair is drawn, and toasts/menus are
  hidden. The capture starts inside the Publish click, because browsers only offer it
  to a gesture. The author sees one browser prompt per publish.
  The first version rebuilt the iframe's DOM (`modern-screenshot`). That could not see
  the shell, so the sidebar was missing, the bottom was cropped, text reflowed into
  itself and the badge came out wrong. That path is now only the fallback, used for
  browsers without `CaptureController` (Safari, Firefox), when the author declines, or
  when anything other than a browser tab is picked. The giant author-name watermark
  is gone.
- **Iframe permission.** `pivot-next/components/chart/ChartFrame.tsx` adds
  `display-capture` to the frame's `allow`. Without it the capture is refused and every
  publish uses the fallback, so the shell has to be rebuilt and deployed with this.
- **Two sizes.** `og_image` is 2400x1260 (about 300 KB, what unfurlers fetch;
  WhatsApp drops big files), with the whole screen fitted inside and the leftover strip
  in the screen's own edge colour. Nothing is cropped. `og_hires` is the screenshot at
  the screen's own pixels (capped at 3840 wide), served at `/share/<token>/image-4k.jpg`
  and offered as "Download screenshot" after publishing. Column `shared_setups.og_hires`;
  caps in `shares.py`: `OG_IMAGE_MAX` 1.4 MB, `HIRES_MAX` 7 MB.
- **Privacy.** A conversation that exists but is not shared is covered on screen (a
  panel-coloured layer inside `#chatPanel`) before the frame is taken. With no
  conversation the real panel (starter prompts) stays. If "what the notes and sheets
  hold" is off, or both captures fail, the old chart-only `share-card.js` card is used.
- **Alien badge.** `.chart-mark` now holds a real `<img>` (`js/main.js`) instead of a
  CSS `::after` background, so any capture draws it.
- **Landing.** `charto/web/app/layout.tsx` adds `og:image` and `twitter:image`
  (`public/og-landing.jpg`, a 2400x1260 capture of the live hero). The landing deploys
  through Vercel (see the pivotnow landing notes), not the VM.

## Before it ships

- `charto/preview/vendor/modern-screenshot.js` is **gitignored**; it needs
  `git add -f` or an ignore exception, or the VM will serve a page without it and
  every publish falls back to the chart-only card.
- Cache stamps are `main.js?v=190`, `workspace-card.js?v=6`, `setups.js?v=25`. `release/chat-v2-2026-10-18`
  already carries `setups.js?v=22` with different content, so the merge conflicts on
  those stamp lines in `index.html`; keep the higher one.
- Publishing waits on the capture: about 2 s after the browser prompt is accepted
  (measured locally in Chromium, tab capture auto-accepted).
- The screenshot is at the screen's pixel density, so a 1x display gives a softer image
  than a Retina one.
- Tests: `charto/data/test_shares.py` (13 pass, including the 4K master).
