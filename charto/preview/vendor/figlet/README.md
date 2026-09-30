# figlet.js (vendored)

- Source: https://github.com/patorjk/figlet.js, npm `figlet@1.12.0`, MIT
  (see `LICENSE.txt`).
- Files: `figlet.mjs` and `figlet-CP8UBLgW.js`, copied unmodified from the
  package's `dist/`. The browser build has no imports and only fetches fonts
  when `loadFont` is called. We never call it; `js/setups.js` parses one font.
- Font: `calvin-pivot.flf` is the package's `fonts/Calvin S.flf` (a
  box-drawing face) with glyphs added for the digits 0-9, which the original
  leaves blank. Without them, NIFTY 50 would render as "NIFTY". Each digit
  is three rows of the same box-drawing characters as the letters.
- Used by: `js/setups.js` (the ticker heading on the Share dialog and on a
  shared setup's card).
