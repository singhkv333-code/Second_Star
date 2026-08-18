# Local patches to `lightweight-charts.standalone.production.js`

The bundle is vendored and **modified**. `charto/preview/vendor/` is gitignored,
so the patched file does **not** travel with the repo — a fresh clone, and the
deploy box, get the stock library and the ragged price scale back.

    python charto/preview/patch-vendor.py    # idempotent; run after any install

That script carries the same five edits and is the thing to run. This file is
why they exist. (Four sections below; §2 carries two of them.) (The alternative is to stop ignoring `vendor/` and commit the
patched bundle — one decision, not made here.)

They serve one end: **the price scale is ONE column.** Out of the box the
library left-aligns three families of label against the scale's inner edge and
sizes each plate to its own text, so a column of prices comes out ragged and
every plate is a different width. Here every family is **centred on the same
axis**, in a plate that is always the **scale's own width**, with a constant
strip at the left kept clear for the alert ⊕ that stands on it.

> **The crosshair's two plates are no longer among them.** They are DOM now
> (`.xh-plate` in index.html, built in js/xhair.js) so they can be glass like the
> rest of the app, and the library is told not to draw them at all
> (`crosshair.{horz,vert}Line.labelVisible: false`). Two patches that existed
> only to make the canvas crosshair plate behave — one levelling every plate to
> the crosshair's height, one pinning the pill text to the same right edge as
> the ticks — went with it.

Measured, not guessed — `_probe.html` (deleted; recreate if needed) wrapped
`CanvasRenderingContext2D.fillText` and dumped the x, alignment and measured
width of every label the chart drew.

> **Identifiers are not stable across builds.** Two different minifications of
> v5.2.0 have been seen in `vendor/`: the price scale's media size is
> `this.Lp` in one and `this.Qv` in the other, the label font `i.k` in one and
> `i.P` in the other. Every patch in the script therefore carries a LIST of
> (before, after) pairs — one per known shape, including the script's own
> earlier output — and the first `before` that appears exactly once decides the
> edit. Identifiers below are quoted from the build currently in `vendor/`.
>
> Note on the branches: `D` / `h.li` is the side the label's ARROW points, not
> the side the scale is on. For a **right-hand** price scale — ours — `D` is
> **false**, and `this.om` ("the scale is on the left") is false too. A first
> attempt patched the true branches, which is what a left-hand scale takes, and
> changed nothing on screen.

## 1 · Tick labels, centred

The plain tick labels were drawn left-aligned from the scale's left edge, so
where they ended depended on how long the number was.

```js
// before                                    // after
t.textAlign = this.om ? "right" : "left";    t.textAlign = "center";
const r = this.om ? Math.round(e - s.V)      const r = this.om ? Math.round(e - s.V)
                  : Math.round(e + s.C + s.V);                 : Math.round(this.Qv.width / 2);
```

`s.C` is the tick length and `s.V` the padding; `this.Qv` is the scale's own
media size. Only the right-hand branch moves.

## 2 · Pill text onto that same centre

The pills — the last-price label, and the price lines an armed alert draws —
were left-aligned at the same inner edge, so they sat in a different place from
the ticks they stack among. In the pill geometry (`hi`), the `D === false`
branch:

```js
A = I + o + d;              →   A = h.width / 2;
```

and the alignment itself:

```js
t.font = i.P, t.textAlign = h.li ? "right" : "left";   →   … t.textAlign = "center";
```

`h` is the media size, `o` the tick length, `d` the padding. Only the TEXT
moves; the pill's background rectangle is patch 3's business.

## 3 · One pill width — and it is the scale's

`M` is the plate's width. Stock, it is *the text's* width plus padding, anchored
at the scale's LEFT edge and free at its right:

```js
M = i.S + d + f + w + o;    →   M = h.width - _ - 5;
```

Two things were wrong with that. Every plate breathed with `w`, and `w` is a
measured string: scroll from 980 to 1,005 and the plate grows by a digit
(measured — the same plate came out 60px and 62px two zoom steps apart). And a
plate that hugs its own text cannot hold centred text in the scale's column —
patch 2 would centre the number in a box that is itself off to one side, which
is how the last-price pill ended up a small red box at the left of an axis whose
ticks were centred.

`h.width` is the scale's own media width and `_` its border, so the plate now
runs the full scale less the 5px patch 4 reserves at the right. It is the same
width always, and its centre is the column's centre. `o` still positions the
tick nub inside it; `w` no longer decides anything.

## 4 · Room at the left edge for the alert ⊕

The mark that arms an alert at a price is drawn at the left end of the price
scale, on the crosshair plate when there is one and on the bare axis when the
pointer has reached for it (see `.alert-plus` in index.html). The scale sizes
itself to its widest number plus a constant, and that constant leaves ~15px
clear at the left: enough for the 16px ring at a 3px inset in the common case,
and 5px short of it by the time a price runs to six figures, where a centred
label grows toward the ring from both sides.

```js
return Mn(Math.ceil(i.S + i.C + i.V + i.B + 5 + l))
                                  →   … + 5 + l + 12))
```

Twelve, so the strip is ~24px clear at every magnitude. It costs nothing on a
normal chart: `rightPriceScale.minimumWidth` (js/main.js) floors the scale at
84px anyway, and an NSE equity's natural width lands under that either way.
