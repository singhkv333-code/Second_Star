"""Make the welcome cards' art in Pivot's dark palette: ink, greys and bone.

    sunset   an oil painting: Arkhip Kuindzhi, "Red Sunset" (1905-8), toned to
             ink and bone with film grain. The Metropolitan Museum of Art,
             Rogers Fund 1974, object 436833. Public domain; The Met releases
             the image under CC0 (Open Access).
    orb      sand: a planet and its moon rising in the dark, lit low from the
             upper left, every bit of light made of sand grains, with two
             faint orbits. Generated.
    moonlight  something more playful: Ivan Aivazovsky, "Ship by Moonlight",
             printed as a 1-bit dither in bone dots on ink, like the referral
             page's planets. The Met, Bequest of Mary Jane Dastich 1975,
             object 437979. Public domain, CC0.
    moon     the sign-in prompt's art: Caspar David Friedrich, "Two Men
             Contemplating the Moon" (ca. 1825-30), toned like the sunset.
             The Met, Wrightsman Fund 2000, object 438417. Public domain, CC0.

    python3.11 scripts/welcome-art.py          # writes public/welcome/*.webp
    python3.11 scripts/welcome-art.py moon     # only the named ones

The paintings are fetched from The Met's open-access image server each run;
everything else is deterministic.
"""
import io
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

W, H = 1120, 640          # twice the card's art panel, for sharp screens
OUT = Path(__file__).resolve().parent.parent / "public" / "welcome"
INK, BONE = np.array([7, 7, 7], float), np.array([239, 233, 221], float)

SOURCES = {
    "sunset": "https://images.metmuseum.org/CRDImages/ep/original/DT2557.jpg",
    "moonlight": "https://images.metmuseum.org/CRDImages/ep/original/DP-23290-001.jpg",
    "moon": "https://images.metmuseum.org/CRDImages/ep/original/DP-31997-001-NEW.jpg",
}


def fetch(url: str) -> Image.Image:
    # curl, not urllib: it uses the system's certificate store.
    data = subprocess.run(["curl", "-sfL", "--max-time", "120", "-A", "pivot-welcome-art", url],
                          check=True, capture_output=True).stdout
    return Image.open(io.BytesIO(data)).convert("L")


def band(im: Image.Image, top: float) -> Image.Image:
    """Crop a W:H band starting `top` of the way down, scaled to W x H."""
    w, h = im.size
    bh = int(w * H / W)
    y = int((h - bh) * top)
    return im.crop((0, y, w, y + bh)).resize((W, H), Image.LANCZOS)


def tone(gray: np.ndarray, warm: float = 0.0) -> np.ndarray:
    """Map 0..1 grey to ink..bone; `warm` lifts the highlights a touch."""
    g = gray[..., None]
    out = INK * (1 - g) + BONE * g
    out[..., 0] += warm * 10 * gray
    out[..., 2] -= warm * 8 * gray
    return out


def grain(rng, img: np.ndarray, amount: float) -> np.ndarray:
    fine = rng.normal(0, amount, img.shape[:2])
    speck = ndimage.gaussian_filter(rng.normal(0, 1, img.shape[:2]), 0.8) * amount
    return img + (fine + speck)[..., None]


def sunset(rng) -> np.ndarray:
    im = fetch(SOURCES["sunset"])
    w, h = im.size
    im = im.crop((int(w * 0.03), int(h * 0.03), int(w * 0.97), int(h * 0.97)))   # no frame edge
    im = band(im, top=0.34)
    g = np.asarray(ImageOps.autocontrast(im, cutoff=0.5), float) / 255
    g = np.clip((g - 0.04) / 0.94, 0, 1) ** 2.1         # a night sky; the sun and its path stay lit
    yy, xx = np.mgrid[0:H, 0:W]
    vignette = 1 - 0.35 * (((xx / W - 0.5) / 0.7) ** 2 + ((yy / H - 0.45) / 0.8) ** 2)
    return grain(rng, tone(np.clip(g * vignette, 0, 1), warm=0.6), 6)


def orb(rng) -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W].astype(float)

    def sphere(cx, cy, r, light):
        dx, dy = (xx - cx) / r, (yy - cy) / r
        d2 = dx * dx + dy * dy
        inside = d2 < 1
        dz = np.sqrt(np.clip(1 - d2, 0, 1))
        lx, ly, lz = np.array(light) / np.linalg.norm(light)
        lit = np.clip(dx * lx + dy * ly + dz * lz, 0, 1) ** 1.3
        rim = np.clip(1 - dz, 0, 1) ** 4 * 0.12
        return np.where(inside, 0.03 + 0.97 * lit + rim, 0), inside

    light = (-0.85, -0.5, 0.25)                         # low from the upper left: a crescent
    big, in_big = sphere(W * 0.66, H * 1.02, H * 0.78, light)
    moon, in_moon = sphere(W * 0.22, H * 0.3, H * 0.13, light)
    halo = 0.14 * np.exp(-(((xx - W * 0.55) / (W * 0.5)) ** 2 + ((yy - H * 0.3) / (H * 0.6)) ** 2))
    g = np.where(in_big, big, np.where(in_moon, moon * 0.9, halo))
    # Sand: every pixel a grain, its brightness scattered around the shading.
    v = np.clip(g + 0.28 * rng.normal(0, 1, (H, W)) * np.sqrt(np.clip(g, 0.02, 1)), 0, 1)
    v = ndimage.gaussian_filter(v, 0.45)
    # Two faint orbits, drawn in broken grains.
    for radius in (H * 0.95, H * 1.12):
        rr = np.sqrt((xx - W * 0.66) ** 2 + (yy - H * 1.02) ** 2)
        line = np.exp(-((rr - radius) / 1.2) ** 2) * 0.35
        v = np.clip(v + line * (rng.random((H, W)) > 0.3), 0, 1)
    return tone(v, warm=1.0)


def moonlight(rng) -> np.ndarray:
    im = fetch(SOURCES["moonlight"])
    w, h = im.size
    im = im.crop((int(w * 0.04), int(h * 0.04), int(w * 0.96), int(h * 0.96)))   # no frame edge
    im = band(im, top=0.15)
    g = np.asarray(ImageOps.autocontrast(im, cutoff=1), float) / 255
    g = np.clip((g - 0.15) / 0.85, 0, 1) ** 1.8         # night stays black; moon and its path stay lit
    # 1-bit Floyd-Steinberg: the whole picture in bone dots on ink.
    dots = np.asarray(Image.fromarray((g * 255).astype(np.uint8)).convert("1", dither=Image.FLOYDSTEINBERG), float)
    return tone(dots * 0.92)


def moon(rng) -> np.ndarray:
    im = fetch(SOURCES["moon"])
    w, h = im.size
    im = im.crop((int(w * 0.03), int(h * 0.03), int(w * 0.97), int(h * 0.97)))   # no frame edge
    im = band(im, top=0.22)                               # the moon, the tree and the two men
    g = np.asarray(ImageOps.autocontrast(im, cutoff=0.5), float) / 255
    g = np.clip((g - 0.03) / 0.95, 0, 1) ** 1.6
    yy, xx = np.mgrid[0:H, 0:W]
    vignette = 1 - 0.3 * (((xx / W - 0.5) / 0.7) ** 2 + ((yy / H - 0.45) / 0.8) ** 2)
    return grain(rng, tone(np.clip(g * vignette, 0, 1), warm=0.6), 6)


PAINTINGS = {"sunset": (sunset, 5), "orb": (orb, 7), "moonlight": (moonlight, 3), "moon": (moon, 11)}

if __name__ == "__main__":
    import sys
    only = set(sys.argv[1:])
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.webp"):
        if not only or old.stem in only:
            old.unlink()
    for name, (make, seed) in PAINTINGS.items():
        if only and name not in only:
            continue
        img = Image.fromarray(np.clip(make(np.random.default_rng(seed)), 0, 255).astype(np.uint8))
        # A two-colour dither compresses far better without loss.
        lossless = len(np.unique(np.asarray(img).reshape(-1, 3), axis=0)) <= 4
        img.save(OUT / f"{name}.webp", "WEBP", quality=88, method=6, lossless=lossless)
        print(name, (OUT / f"{name}.webp").stat().st_size // 1024, "KB")
