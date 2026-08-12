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

# (name, before, after). Every one of them serves a single end: each family of
# price-scale label ends at the same x, `width - tickLength - padding`.
PATCHES = [
    (
        "tick labels right-aligned",
        't.textAlign=this.Yp?"right":"left",t.textBaseline="middle";'
        'const r=this.Yp?Math.round(e-n.B):Math.round(e+n.C+n.B)',
        't.textAlign="right",t.textBaseline="middle";'
        'const r=this.Yp?Math.round(e-n.B):Math.round(this.Lp.width-n.C-n.B)',
    ),
    (
        "pill text onto that same edge",
        'let B,I,A;return D?(B=V-C,I=V-y,A=E-o-d-_):(B=V+C,I=V+y,A=E+o+d)',
        'let B,I,A;return D?(B=V-C,I=V-y,A=E-o-d-_):(B=V+C,I=V+y,A=h.width-i.C-d)',
    ),
    (
        "pill text alignment",
        't.font=i.k,t.textAlign=h.li?"right":"left"',
        't.font=i.k,t.textAlign="right"',
    ),
    (
        "one pill width for all of them",
        'M=i.S+d+f+w+o,',
        'M=i.S+d+f+w+i.C,',
    ),
]


def main() -> int:
    if not os.path.exists(BUNDLE):
        print(f"not found: {BUNDLE}\ninstall lightweight-charts v5 first.")
        return 2

    src = open(BUNDLE, encoding="utf8").read()
    applied, already, missing = [], [], []

    for name, before, after in PATCHES:
        if after in src:
            already.append(name)
        elif src.count(before) == 1:
            src = src.replace(before, after)
            applied.append(name)
        else:
            # Neither shape is there: a different version of the library, and
            # guessing at it would be worse than stopping.
            missing.append(f"{name} (found {src.count(before)} matches)")

    if missing:
        print("FAILED — the bundle does not look like the version these were "
              "written against (5.2.0). Do not force it; re-derive the patches "
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
