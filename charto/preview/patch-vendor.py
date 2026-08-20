"""Re-apply Charto's patches to the vendored lightweight-charts bundle.

The vendor directory is gitignored, so this script is the deployable source of
the small rendering changes. It accepts both known v5.2.0 minifications and the
older centred-label patch, making upgrades from the bad deploy idempotent.
"""

from __future__ import annotations

import os
import sys

BUNDLE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "vendor", "lightweight-charts.standalone.production.js",
)

# (name, [(before, after), ...]). Each patch makes the native price labels end
# at one right-hand edge, use the full scale width, and share one plate height.
PATCHES = [
    (
        "tick labels right-aligned",
        [
            ('t.textAlign=this.om?"right":"left",t.textBaseline="middle";'
             'const r=this.om?Math.round(e-s.V):Math.round(e+s.C+s.V)',
             't.textAlign="right",t.textBaseline="middle";'
             'const r=this.om?Math.round(e-s.V):Math.round(this.Qv.width-s.C-s.V)'),
            ('t.textAlign="center",t.textBaseline="middle";'
             'const r=this.om?Math.round(e-s.V):Math.round(this.Qv.width/2)',
             't.textAlign="right",t.textBaseline="middle";'
             'const r=this.om?Math.round(e-s.V):Math.round(this.Qv.width-s.C-s.V)'),
            ('t.textAlign=this.Yp?"right":"left",t.textBaseline="middle";'
             'const r=this.Yp?Math.round(e-n.B):Math.round(e+n.C+n.B)',
             't.textAlign="right",t.textBaseline="middle";'
             'const r=this.Yp?Math.round(e-n.B):Math.round(this.Lp.width-n.C-n.B)'),
            ('t.textAlign="center",t.textBaseline="middle";'
             'const r=this.Yp?Math.round(e-n.B):Math.round(this.Lp.width/2)',
             't.textAlign="right",t.textBaseline="middle";'
             'const r=this.Yp?Math.round(e-n.B):Math.round(this.Lp.width-n.C-n.B)'),
        ],
    ),
    (
        "pill text onto that same edge",
        [
            ('let V,B,A;return D?(V=E-C,B=E-y,A=I-o-d-_):(V=E+C,B=E+y,A=I+o+d)',
             'let V,B,A;return D?(V=E-C,B=E-y,A=I-o-d-_):(V=E+C,B=E+y,A=h.width-i.C-d)'),
            ('let V,B,A;return D?(V=E-C,B=E-y,A=I-o-d-_):(V=E+C,B=E+y,A=h.width/2)',
             'let V,B,A;return D?(V=E-C,B=E-y,A=I-o-d-_):(V=E+C,B=E+y,A=h.width-i.C-d)'),
            ('let B,I,A;return D?(B=V-C,I=V-y,A=E-o-d-_):(B=V+C,I=V+y,A=E+o+d)',
             'let B,I,A;return D?(B=V-C,I=V-y,A=E-o-d-_):(B=V+C,I=V+y,A=h.width-i.C-d)'),
            ('let B,I,A;return D?(B=V-C,I=V-y,A=E-o-d-_):(B=V+C,I=V+y,A=h.width/2)',
             'let B,I,A;return D?(B=V-C,I=V-y,A=E-o-d-_):(B=V+C,I=V+y,A=h.width-i.C-d)'),
        ],
    ),
    (
        "pill text alignment",
        [
            ('t.font=i.P,t.textAlign=h.li?"right":"left"', 't.font=i.P,t.textAlign="right"'),
            ('t.font=i.P,t.textAlign="center"', 't.font=i.P,t.textAlign="right"'),
            ('t.font=i.k,t.textAlign=h.li?"right":"left"', 't.font=i.k,t.textAlign="right"'),
            ('t.font=i.k,t.textAlign="center"', 't.font=i.k,t.textAlign="right"'),
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
             'const l=t||34;return Mn(Math.ceil(i.S+i.C+i.V+i.B+5+l+24))'),
            ('const l=t||34;return Mn(Math.ceil(i.S+i.C+i.V+i.B+5+l+12))',
             'const l=t||34;return Mn(Math.ceil(i.S+i.C+i.V+i.B+5+l+24))'),
            ('const l=t||34;return us(Math.ceil(i.S+i.C+i.B+i.I+5+l))',
             'const l=t||34;return us(Math.ceil(i.S+i.C+i.B+i.I+5+l+24))'),
            ('const l=t||34;return us(Math.ceil(i.S+i.C+i.B+i.I+5+l+12))',
             'const l=t||34;return us(Math.ceil(i.S+i.C+i.B+i.I+5+l+24))'),
        ],
    ),
    (
        "one pill height, and it is the crosshair's",
        [
            ('u=i.A+this.ei.Ti,c=i.V+this.ei.Ri,',
             'u=i.A+i.P*2/12,c=i.V+i.P*2/12,'),
            # Build A's second field is not vertical padding. Preserve its
            # native geometry; changing it suppresses series/indicator labels.
            ('u=i.A+i.P*2/12,c=i.I+i.P*2/12,',
             'u=i.A+this.ei.Ti,c=i.I+this.ei.Ri,'),
        ],
    ),
]


def main() -> int:
    if not os.path.exists(BUNDLE):
        print(f"not found: {BUNDLE}\ninstall lightweight-charts v5 first.")
        return 2

    with open(BUNDLE, encoding="utf8") as bundle:
        src = bundle.read()
    applied, already, missing = [], [], []

    for name, variants in PATCHES:
        if any(after in src for _, after in variants):
            already.append(name)
            continue
        hit = next(((before, after) for before, after in variants
                    if src.count(before) == 1), None)
        if hit is None:
            counts = ", ".join(str(src.count(before)) for before, _ in variants)
            missing.append(f"{name} (matches: {counts})")
            continue
        src = src.replace(hit[0], hit[1])
        applied.append(name)

    if missing:
        print("FAILED - the bundle is not a known lightweight-charts v5.2.0 build.")
        for item in missing:
            print("  -", item)
        return 1

    if applied:
        with open(BUNDLE, "w", encoding="utf8") as bundle:
            bundle.write(src)
    for name in applied:
        print("patched  -", name)
    for name in already:
        print("already  -", name)
    print("nothing to do." if not applied else "done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
