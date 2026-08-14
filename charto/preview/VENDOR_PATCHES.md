# Local patches to `lightweight-charts.standalone.production.js`

The bundle is vendored and **modified**. `charto/preview/vendor/` is gitignored,
so the patched file does **not** travel with the repo — a fresh clone, and the
deploy box, get the stock library and the ragged price scale back.

    python charto/preview/patch-vendor.py    # idempotent; run after any install

That script carries the same six edits and is the thing to run. This file is
why they exist. (The alternative is to stop ignoring `vendor/` and commit the
patched bundle — one decision, not made here.)

They serve two ends. **§1–2: every value on the price scale ends at the same x.**
Out of the box the library aligns three families of label three different ways,
so a column of prices comes out ragged next to TradingView's own. **§3–5: the
scale is one shape and stays there** — every plate the same width and the same
height, and a constant strip of it at the left kept clear for the alert ⊕ that
rides it. (The sections group the six edits; §2 carries two of them.)

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

## 3 · One pill width — and it is the scale's

`M` is the plate's width. Stock, it is *the text's* width plus padding, anchored
at the scale's LEFT edge and free at its right:

```js
M = i.S + d + f + w + o;    →   M = h.width - _ - 5;
```

Two things were wrong with that, and the first version of this patch
(`… + w + i.C`) only fixed one of them. `o` is `i.C` for a label that draws a
tick nub and `0` for the crosshair label, so the crosshair pill came out 5px
narrower than its neighbours and, once patch 2 pushed the text to an absolute
right edge, that text ran past its own plate. Equalising the families fixed
that — but every plate still breathed with `w`, and `w` is a measured string.
Scroll from 980 to 1,005 and the plate grows by a digit; the ⊕ pinned to its
edge is then either adrift from it or under it. Measured: the same plate came
out 60px and 62px two zoom steps apart.

`h.width` is the scale's own media width and `_` its border, so the plate now
runs the full scale less the 5px `nv()` (§4) reserves at the right — which is
exactly the widest it ever was, so nothing about the right-hand alignment moves.
It is simply the same width always. `o` still positions the tick nub inside it;
`w` no longer decides anything.

## 4 · Room at the left edge for the alert ⊕

The mark that arms an alert at a price is drawn INSIDE the crosshair plate, at
its left end — Groww's placement, and the only one where it needs no opaque disc
of its own (see `.alert-plus` in index.html). The scale sizes itself to its
widest number plus a constant, and that constant leaves ~15px clear at the left:
enough for the 16px ring at a 3px inset in the common case, and 5px short of it
by the time a price runs to six figures, where the ring landed on the leading
digit.

```js
return us(Math.ceil(i.S + i.C + i.B + i.I + 5 + l))
                                  →   … + 5 + l + 12))
```

Twelve, so the strip is ~24px clear at every magnitude. It costs nothing on a
normal chart: `rightPriceScale.minimumWidth` (js/main.js) floors the scale at
84px anyway, and an NSE equity's natural width lands under that either way.

## 5 · One pill height — and it is the crosshair's

§3 made every plate the same width; they were still two different heights. The
plate's height is `fontSize + paddingTop + paddingBottom`, where the two
paddings each take an extra the LABEL supplies — and only some labels supply
one. The crosshair's view (`class N`) and the plugin views (`Ot`) set
`Ti = Ri = 2/12 × fontSize`; the series' last-value label (`ut`) and the price
lines (`vt`, which is what an armed alert draws) leave both at the `0` the
defaults hold. Measured on the axis canvas at dpr 1.25: crosshair 27 device px,
last price 21 — a 4px CSS difference between two plates that stack in the same
column, one directly under the other.

Rather than add the padding to each view that forgot it — two more edits, and
one per view family the library grows later — it is taken in the geometry both
already share (`hi`), where the label's own contribution simply stops being
consulted:

```js
u = i.A + this.ei.Ti,   c = i.V + this.ei.Ri,
                        →   u = i.A + i.P * 2 / 12,   c = i.V + i.P * 2 / 12,
```

`i.P` is the scale's font size, so this is the crosshair's own expression
applied to every family — the taller of the two, deliberately: the ⊕ is a 16px
ring drawn inside the crosshair plate, and levelling down to 17px would leave it
half a pixel of air on each side. `Ti`/`Ri` are now unread; nothing else sets
them.
