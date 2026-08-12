# Local patches to `lightweight-charts.standalone.production.js`

The bundle is vendored and **modified**. `charto/preview/vendor/` is gitignored,
so the patched file does **not** travel with the repo — a fresh clone, and the
deploy box, get the stock library and the ragged price scale back.

    python charto/preview/patch-vendor.py    # idempotent; run after any install

That script carries the same four edits and is the thing to run. This file is
why they exist. (The alternative is to stop ignoring `vendor/` and commit the
patched bundle — one decision, not made here.)

All three serve one end: **every value on the price scale ends at the same x.**
Out of the box the library aligns three families of label three different ways,
so a column of prices comes out ragged next to TradingView's own.

Measured, not guessed — `_probe.html` (deleted; recreate if needed) wrapped
`CanvasRenderingContext2D.fillText` and dumped the x, alignment and measured
width of every label the chart drew. On a 64px scale the answer must be **54**
for all of them: `width − tickLength(5) − padding(5)`.

> Note on the branches below: `D` / `h.li` is the side the label's ARROW points,
> not the side the scale is on. For a **right-hand** price scale — ours — `D` is
> **false**. The first attempt patched the `D === true` branch, which is the one
> a left-hand scale takes, and changed nothing on screen.

## 1 · Tick labels, right-aligned

The plain tick labels were drawn left-aligned from the scale's left edge, so
where they ended depended on how long the number was.

```js
// before                                    // after
t.textAlign = this.Yp ? "right" : "left";    t.textAlign = "right";
const r = this.Yp ? Math.round(e - n.B)      const r = this.Yp ? Math.round(e - n.B)
                  : Math.round(e + n.C + n.B);                 : Math.round(this.Lp.width - n.C - n.B);
```

`this.Yp` is "the scale is on the left"; only the right-hand branch moves.
Probe: `align=left x=10` → `align=right x=54`.

## 2 · Pill labels, onto that same edge

The pills — crosshair, last price, price lines — were left-aligned at x=10 and
ended at 52.16 for a 42px number, i.e. 2px short of the ticks and further short
the shorter the number. In the pill geometry (`hi`), the `D === false` branch:

```js
A = E + o + d;              →   A = h.width - i.C - d;
```

and the alignment itself:

```js
t.textAlign = h.li ? "right" : "left";   →   t.textAlign = "right";
```

`h` is the media size, `i.C` the tick length, `d` the padding — the same
`width − tickLength − padding` patch 1 gives the ticks. Only the TEXT moves;
the pill's background rectangle is patch 3's business.

## 3 · One pill width for all of them

`o` is `i.C` for a label that draws a tick nub and `0` for the crosshair label,
and it sized the plate: the crosshair pill came out 5px narrower than its
neighbours, so once patch 2 pushed the text to an absolute right edge that text
ran past its own plate.

```js
M = i.S + d + f + w + o;    →   M = i.S + d + f + w + i.C;
```

`o` still positions the tick nub inside the plate; it just no longer decides how
wide the plate is. Every pill is now the same width, which is also what
TradingView draws.
