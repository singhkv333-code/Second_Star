"""Paint the welcome cards' art in Pivot's dark palette: ink, greys and bone,
with only a breath of colour in each.

    smoke  a grayscale oil-painted sky, wisps of bone light in black (welcome)
    silk   folds of liquid steel, a cool blue-grey sheen (watchlist)
    glow   a soft spotlight on black, warm grey, heavy grain (appearance)

All three start from domain warping (Inigo Quilez,
https://iquilezles.org/articles/warp): fBm noise read at coordinates that
fBm itself pushes around. That folds plain noise into painted clouds and
draped cloth. Each ends with the same fine film grain the referral page uses.

    python3.11 scripts/welcome-art.py      # writes public/welcome/*.webp

Deterministic: the same seed gives the same picture.
"""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

W, H = 1120, 640          # twice the card's art panel, for sharp screens
OUT = Path(__file__).resolve().parent.parent / "public" / "welcome"


def fbm_field(rng: np.random.Generator, size: int, octaves: int) -> np.ndarray:
    """A smooth, wrapping noise field: random grids, upsampled and summed."""
    out = np.zeros((size, size))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        cells = 4 * 2 ** o
        grid = rng.standard_normal((cells, cells))
        out += amp * ndimage.zoom(np.tile(grid, (2, 2)), size * 2 / (cells * 2), order=3,
                                  mode="grid-wrap")[:size, :size]
        total += amp
        amp *= 0.5
    out /= total
    return (out - out.min()) / (out.max() - out.min())


def sample(field: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    n = field.shape[0]
    return ndimage.map_coordinates(field, [(y * n) % n, (x * n) % n], order=1, mode="grid-wrap")


def rgb(h: str) -> np.ndarray:
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)


def ramp(t: np.ndarray, stops: list[tuple[float, str]]) -> np.ndarray:
    """Piecewise colour ramp over t in [0, 1]."""
    t = np.clip(t, 0, 1)
    out = np.zeros(t.shape + (3,))
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        m = (t >= p0) & (t <= p1)
        f = ((t[m] - p0) / max(p1 - p0, 1e-6))[:, None]
        f = f * f * (3 - 2 * f)
        out[m] = rgb(c0) * (1 - f) + rgb(c1) * f
    return out


def norm(a: np.ndarray) -> np.ndarray:
    return (a - a.min()) / (a.max() - a.min())


def warped(rng, scale: float, fold: float, octaves: int):
    a, b, c = (fbm_field(rng, 512, octaves) for _ in range(3))
    yy, xx = np.mgrid[0:H, 0:W] / W * scale
    qx, qy = sample(a, xx, yy), sample(b, xx + 0.52, yy + 0.13)
    rx = sample(c, xx + fold * qx + 0.17, yy + fold * qy + 0.92)
    ry = sample(a, xx + fold * qx + 0.83, yy + fold * qy + 0.28)
    f = sample(b, xx + fold * 1.2 * rx, yy + fold * 1.2 * ry)
    return norm(f), qx, rx, xx / xx.max(), yy / yy.max()


def grain(rng, img: np.ndarray, amount: float) -> np.ndarray:
    fine = rng.normal(0, amount, (H, W))
    speck = ndimage.gaussian_filter(rng.normal(0, 1, (H, W)), 0.8) * amount * 1.1
    return img + (fine + speck)[..., None]


def smoke(rng) -> np.ndarray:
    f, q, r, x, y = warped(rng, scale=0.3, fold=1.1, octaves=5)
    t = norm(0.7 * f + 0.3 * r)
    # Broad dark masses; light gathers along the cloud banks, strongest in
    # one region, as in a night-sky oil sketch.
    light = np.clip((t - 0.3) / 0.62, 0, 1) ** 1.25
    light *= 0.45 + 0.55 * np.exp(-(((x - 0.66) / 0.5) ** 2 + ((y - 0.32) / 0.7) ** 2))
    img = ramp(light, [(0, "#070707"), (0.3, "#1d1c19"), (0.62, "#6f6a60"), (1, "#ece6da")])
    img = ndimage.gaussian_filter(img, sigma=(5.5, 5.5, 0))
    return grain(rng, img, 7.5)


def silk(rng) -> np.ndarray:
    f, q, r, x, y = warped(rng, scale=0.2, fold=0.7, octaves=3)
    phase = f * 4.2 + r * 1.6
    sheen = (0.5 + 0.5 * np.sin(phase * np.pi)) ** 3     # a few broad crests on the folds
    body = norm(0.6 * f + 0.4 * q)
    img = ramp(body, [(0, "#08090b"), (0.55, "#171a1e"), (1, "#323941")])
    img += sheen[..., None] * (rgb("#aab4be") - 30) * (0.25 + 0.75 * body)[..., None] * 0.8
    img = ndimage.gaussian_filter(img, sigma=(5, 12, 0))
    img = ndimage.gaussian_filter(img, sigma=(8, 8, 0))
    return grain(rng, img, 6.5)


def glow(rng) -> np.ndarray:
    f, q, r, x, y = warped(rng, scale=0.5, fold=1.0, octaves=4)
    d = ((x - 0.7) / 0.5) ** 2 + ((y - 0.15) / 0.75) ** 2
    spot = np.exp(-d * 1.4) * (0.94 + 0.12 * (f - 0.5))  # light on a faintly textured wall
    img = ramp(spot, [(0, "#060606"), (0.3, "#171614"), (0.7, "#5a5751"), (1, "#b3ada2")])
    img = ndimage.gaussian_filter(img, sigma=(10, 10, 0))
    return grain(rng, img, 10)


PAINTINGS = {"smoke": (smoke, 7), "silk": (silk, 41), "glow": (glow, 3)}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.webp"):
        old.unlink()
    for name, (paint, seed) in PAINTINGS.items():
        img = Image.fromarray(np.clip(paint(np.random.default_rng(seed)), 0, 255).astype(np.uint8))
        img.save(OUT / f"{name}.webp", "WEBP", quality=86, method=6)
        print(name, (OUT / f"{name}.webp").stat().st_size // 1024, "KB")
