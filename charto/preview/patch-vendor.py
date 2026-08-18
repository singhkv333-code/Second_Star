"""Re-apply Charto's patches to the vendored lightweight-charts bundle.

`vendor/` is gitignored, so the patched file cannot travel with the repo — the
edits live HERE instead, and this script puts them back onto a freshly
installed copy of the library. Idempotent: running it twice is a no-op, and it
says so.

Run:  python charto/preview/patch-vendor.py
Why:  see VENDOR_PATCHES.md, which explains each one and how it was measured.
"""

from __future__ import annotations

import os
import sys

BUNDLE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "vendor", "lightweight-charts.standalone.production.js",
)

# (name, [(before, after), ...]). Every one of them serves a single end: the
# price scale is ONE column — every family of label centred on the same axis,
# in a plate that is always the scale's own width, with a constant strip at the
# left kept clear for the alert mark that stands on it.
#
# Each patch carries a LIST of (before, after) pairs because the same edit has
# to be expressed against more than one shape of the file. Two of them are
# different minifications of v5.2.0 — the identifiers are not stable across
# builds, so `this.Lp.width` in one is `this.Qv.width` in the next — and the
# rest are this script's own earlier output, which a re-run has to recognise as
# something to rewrite rather than stop on. The FIRST pair whose `before`
# appears exactly once decides the edit; every pair's `after` counts as "already
# applied", so a bundle patched by an older release of this script is left alone
# only when that older shape is still the intended one.
PATCHES = [
    (
        "tick labels centred on the scale",
        [
            # build A (identifiers om / Qv / s.C,s.V) — stock
            ('t.textAlign=this.om?"right":"left",t.textBaseline="middle";'
             'const r=this.om?Math.round(e-s.V):Math.round(e+s.C+s.V)',
             't.textAlign="center",t.textBaseline="middle";'
             'const r=this.om?Math.round(e-s.V):Math.round(this.Qv.width/2)'),
            # build B (identifiers Yp / Lp / n.B,n.C) — stock
            ('t.textAlign=this.Yp?"right":"left",t.textBaseline="middle";'
             'const r=this.Yp?Math.round(e-n.B):Math.round(e+n.C+n.B)',
             't.textAlign="center",t.textBaseline="middle";'
             'const r=this.Yp?Math.round(e-n.B):Math.round(this.Lp.width/2)'),
            # build B, right-aligned by an earlier release of this script
            ('t.textAlign="right",t.textBaseline="middle";'
             'const r=this.Yp?Math.round(e-n.B):Math.round(this.Lp.width-n.C-n.B)',
             't.textAlign="center",t.textBaseline="middle";'
             'const r=this.Yp?Math.round(e-n.B):Math.round(this.Lp.width/2)'),
        ],
    ),
    (
        "pill text onto that same centre",
        [
            ('let V,B,A;return D?(V=E-C,B=E-y,A=I-o-d-_):(V=E+C,B=E+y,A=I+o+d)',
             'let V,B,A;return D?(V=E-C,B=E-y,A=I-o-d-_):(V=E+C,B=E+y,A=h.width/2)'),
            ('let B,I,A;return D?(B=V-C,I=V-y,A=E-o-d-_):(B=V+C,I=V+y,A=E+o+d)',
             'let B,I,A;return D?(B=V-C,I=V-y,A=E-o-d-_):(B=V+C,I=V+y,A=h.width/2)'),
            ('let B,I,A;return D?(B=V-C,I=V-y,A=E-o-d-_):(B=V+C,I=V+y,A=h.width-i.C-d)',
             'let B,I,A;return D?(B=V-C,I=V-y,A=E-o-d-_):(B=V+C,I=V+y,A=h.width/2)'),
        ],
    ),
    (
        "pill text alignment",
        [
            ('t.font=i.P,t.textAlign=h.li?"right":"left"', 't.font=i.P,t.textAlign="center"'),
            ('t.font=i.k,t.textAlign=h.li?"right":"left"', 't.font=i.k,t.textAlign="center"'),
            ('t.font=i.k,t.textAlign="right"', 't.font=i.k,t.textAlign="center"'),
        ],
    ),
    (
        "one pill width, and it is the scale's",
        [
            ('M=i.S+d+f+w+o,', 'M=h.width-_-5,'),
            ('M=i.S+d+f+w+i.C,', 'M=h.width-_-5,'),
        ],
    ),
    (
        "room at the scale's left edge for the alert mark",
        [
            ('const l=t||34;return Mn(Math.ceil(i.S+i.C+i.V+i.B+5+l))',
             'const l=t||34;return Mn(Math.ceil(i.S+i.C+i.V+i.B+5+l+12))'),
            ('const l=t||34;return us(Math.ceil(i.S+i.C+i.B+i.I+5+l))',
             'const l=t||34;return us(Math.ceil(i.S+i.C+i.B+i.I+5+l+12))'),
        ],
    ),
]


def main() -> int:
    if not os.path.exists(BUNDLE):
        print(f"not found: {BUNDLE}\ninstall lightweight-charts v5 first.")
        return 2

    src = open(BUNDLE, encoding="utf8").read()
    applied, already, missing = [], [], []

    for name, variants in PATCHES:
        if any(after in src for _, after in variants):
            already.append(name)
            continue
        hit = next(((b, a) for b, a in variants if src.count(b) == 1), None)
        if hit is not None:
            src = src.replace(hit[0], hit[1])
            applied.append(name)
        else:
            # No shape this patch knows how to rewrite: a different build of
            # the library, and guessing at it would be worse than stopping.
            counts = ", ".join(str(src.count(b)) for b, _ in variants)
            missing.append(f"{name} (matches: {counts})")

    if missing:
        print("FAILED — the bundle does not look like any build these were "
              "written against (v5.2.0). Do not force it; re-derive the patches "
              "against the new source. See VENDOR_PATCHES.md.")
        for m in missing:
            print("  -", m)
        return 1

    if applied:
        open(BUNDLE, "w", encoding="utf8").write(src)
    for n in applied:
        print("patched  -", n)
    for n in already:
        print("already  -", n)
    print("nothing to do." if not applied else "done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
