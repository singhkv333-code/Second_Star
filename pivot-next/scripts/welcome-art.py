"""Paint the welcome cards' art: blurred oil-paint swirls with a sandy grain.

Domain warping (Inigo Quilez, https://iquilezles.org/articles/warp): fBm noise
read at coordinates that are themselves pushed around by fBm, twice. That is
what folds smooth colour into the marbled, brushed swirls. Then a soft blur,
and a fine monochrome grain on top for the sand.

    python3.11 scripts/welcome-art.py      # writes public/welcome/*.webp

Deterministic: the same seed gives the same picture.
"""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

W, H = 1120, 640          # twice the card's art panel, for sharp screens
OUT = Path(__file__).resolve().parent.parent / "public" / "welcome"

PALETTES = {
    # name: colours from the quiet end of the ramp to the deep end
    "dune": ["#f6ead9", "#f1c9a5", "#e49a74", "#c9785f", "#b98aa0", "#7a5470"],
    "tide": ["#eef3ef", "#d3e6e3", "#a8cfd0", "#ead6b6", "#7fb0b8", "#5d8fa3"],
    "dusk": ["#f4e4dc", "#f2c4ab", "#e7a0b4", "#b9a3dc", "#8577c4", "#3a3566"],
}

# Chosen by eye: each gives one or two broad folds and no lone dark blot.
SEEDS = {"dune": 11, "tide": 41, "dusk": 25}


def fbm_field(rng: np.random.Generator, size: int, octaves: int = 5) -> np.ndarray:
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


def hex_rgb(h: str) -> np.ndarray:
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)


def ramp(t: np.ndarray, colours: list[str]) -> np.ndarray:
    stops = np.array([hex_rgb(c) for c in colours])
    pos = np.clip(t, 0, 1) * (len(stops) - 1)
    i = np.clip(pos.astype(int), 0, len(stops) - 2)
    f = (pos - i)[..., None]
    f = f * f * (3 - 2 * f)                       # smoothstep between stops
    return stops[i] * (1 - f) + stops[i + 1] * f


def paint(name: str, colours: list[str], seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    a, b, c = (fbm_field(rng, 512, octaves=3) for _ in range(3))
    yy, xx = np.mgrid[0:H, 0:W] / W * 0.38       # a small window: large, slow swirls
    # q pushes the coordinates, r pushes them again: two folds of the paint.
    qx, qy = sample(a, xx, yy), sample(b, xx + 0.52, yy + 0.13)
    rx = sample(c, xx + 0.9 * qx + 0.17, yy + 0.9 * qy + 0.92)
    ry = sample(a, xx + 0.9 * qx + 0.83, yy + 0.9 * qy + 0.28)
    f = sample(b, xx + 1.1 * rx, yy + 1.1 * ry)
    t = 0.7 * f + 0.2 * rx + 0.1 * qy
    t = (t - t.min()) / (t.max() - t.min())
    rgb = ramp(t, colours)
    # Lift the light end a touch so the swirls read as light on paint.
    rgb += (np.clip(qx - 0.5, 0, 1) * 70)[..., None]
    # Light falls from the top left.
    rgb += ((1 - yy / yy.max()) * (1 - xx / xx.max()) * 26)[..., None]
    # Blur along the paint, then a second soft pass: brushed, then out of focus.
    rgb = ndimage.gaussian_filter(rgb, sigma=(4, 14, 0))
    rgb = ndimage.gaussian_filter(rgb, sigma=(10, 10, 0))
    # Sand: fine monochrome grain plus a sparse coarser speckle.
    grain = rng.normal(0, 7.5, (H, W))
    speck = ndimage.gaussian_filter(rng.normal(0, 1, (H, W)), 0.8) * 9
    rgb += (grain + speck)[..., None]
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, colours in PALETTES.items():
        img = paint(name, colours, seed=SEEDS[name])
        img.save(OUT / f"{name}.webp", "WEBP", quality=84, method=6)
        print(name, (OUT / f"{name}.webp").stat().st_size // 1024, "KB")
