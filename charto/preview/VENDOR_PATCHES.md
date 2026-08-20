# Lightweight Charts axis-label patches

Charto uses Lightweight Charts' native canvas-rendered crosshair labels. They
remain coupled to the chart's scrolling, pane geometry, scale, and hit testing;
there is no separate DOM badge layered over them.

`patch-vendor.py` makes six small changes to the vendored v5.2.0 production
bundle:

1. Right-align normal price ticks to one shared edge.
2. Put price-pill text on that same edge.
3. Right-align the pill text.
4. Give every price pill the full width of the price-scale column.
5. Reserve a 24px strip at the pill's left for Charto's alert mark and gap.
6. Give series and crosshair pills the same height on the minification whose
   fields represent vertical padding; preserve native geometry on the other.

Together these produce the compact rectangular side badges and native bottom
date badge used by the local chart. The patcher recognizes both known v5.2.0
minifications and can migrate the former centred-label deploy output. It is
idempotent and fails closed if the installed bundle no longer matches a known
shape.

Run after installing or refreshing the vendor bundle:

```sh
python charto/preview/patch-vendor.py
```

Do not patch a different Lightweight Charts release by guesswork. Re-derive
the snippets against that release and add its exact shapes to the script.
