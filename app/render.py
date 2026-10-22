# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Own preview images from the toolpaths of the 3D preview.

Slicers put very different images into the files (PrusaSlicer with print bed, Bambu Studio
without, Cura often none at all). Here every sliced plate gets an image in the same style: at an
angle from the front right like the 3D preview, plus one from above. Drawn with Pillow, path by
path from back to front, walls lighter or darker depending on their direction.
"""
from __future__ import annotations

import gzip
import json
import logging
import math
from array import array
from pathlib import Path

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

VERSION = 5  # new number = old images are redrawn
SIZE = 512          # like the images from Bambu Studio
SUPERSAMPLE = 2     # draw at double size and downscale: smooth edges
MARGIN = 0.07
SPLIT_MM = 3.0
MAX_SEGMENTS = 600_000     # thin out very large prints: an image then takes only seconds
PLATE = (58, 62, 68)       # neutral, dark plate (fits light and dark themes)
GRID = (74, 79, 86)
GRID_STRONG = (92, 98, 106)
EDGE = (110, 117, 126)
DEFAULT_COLOR = "#E0E0E0"
# Like the slicers' images: without wipe tower, skirt and supports, they are not part of the model.
# Sparse infill always lies inside under walls and top layers; leaving it out saves most of the
# segments in large prints.
HIDDEN_FEATURES = {"Reinigungsturm", "Skirt", "Stützen", "Stützen-Kontakt", "Stützen-Übergang", "Infill"}
# View direction like „Ansicht zurücksetzen“ in the 3D preview (camera front right, slightly from above)
_CAM = (1.1, -1.6, 1.1)


MODES = ("eigene", "ohne_platte", "slicer")  # setting „vorschaubilder“, default: own with plate


def mode(settings: dict | None = None) -> str:
    from . import store
    value = (settings if settings is not None else store.get_settings()).get("vorschaubilder")
    return value if value in MODES else MODES[0]


def file_name(kind: str, idx: int, with_plate: bool = True) -> str:
    """File name in the file's preview folder, e.g. render1_iso_2.png (without plate: render1n_…)."""
    return f"render{VERSION}{'' if with_plate else 'n'}_{kind}_{idx}.png"


def rendered(fid: int, idx: int, current: str, kind: str = "iso") -> Path | None:
    """Own image of a plate, if enabled and already drawn."""
    if current == "slicer":
        return None
    path = _thumb_dir(fid) / file_name(kind, idx, current == "eigene")
    return path if path.exists() else None


def apply(plate: dict, current: str) -> None:
    """Switch a plate (from projects) to the own images if they exist: then every
    plate shows an image, even without a slicer preview, and the image URL changes with it."""
    if plate.get("sliced") and rendered(plate["file_id"], plate["idx"], current):
        plate["has_thumb"] = plate["has_top"] = 1
        plate["v"] = f"{plate.get('v', '')}r{VERSION}{current[0]}"


def _hex(color: str) -> tuple[int, int, int]:
    c = (color or DEFAULT_COLOR).lstrip("#")
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return _hex(DEFAULT_COLOR)


def _shade(rgb, f: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(v * f))) for v in rgb)


def load(base: Path) -> tuple[dict, array, array]:
    """Metadata, points (x, y alternating, uint16) and paths (5 values each) of a 3D preview."""
    meta = json.loads(base.with_suffix(".json").read_text())
    with gzip.open(base.with_suffix(".bin.gz"), "rb") as fh:
        n_pts = meta["points"] * 2
        pts = array("H")
        pts.frombytes(fh.read(n_pts * 2))
        if n_pts % 2:
            fh.read(2)
        polys = array("I")
        polys.frombytes(fh.read(meta["polys"] * 5 * 4))
    return meta, pts, polys


class _View:
    """Parallel projection: screen x, screen y (downwards) and depth (larger = closer)."""

    def __init__(self, top: bool):
        if top:
            self.rx, self.ry = 1.0, 0.0     # right = +x
            self.ux, self.uy = 0.0, -1.0    # towards the camera = -y (front), up would be z
            self.ce, self.se = 0.0, 1.0     # looking straight down from above
        else:
            hx, hy, hz = _CAM
            h = math.hypot(hx, hy)
            self.ux, self.uy = hx / h, hy / h
            self.rx, self.ry = -self.uy, self.ux
            e = math.atan2(hz, h)
            self.ce, self.se = math.cos(e), math.sin(e)

    def sx(self, x, y):
        return x * self.rx + y * self.ry

    def sy(self, x, y, z):
        return (x * self.ux + y * self.uy) * self.se - z * self.ce

    def depth(self, x, y, z):
        return (x * self.ux + y * self.uy) * self.ce + z * self.se


def render(base: Path, out: Path, colors: dict[int, str], bed: list[float] | None, *,
           top: bool = False, plate: bool = True, nozzle: float = 0.4) -> None:
    """Draws the preview's toolpaths under base to out (PNG, transparent outside the plate).

    colors: tool (0-based) -> color; without tool changes the first color applies to everything.
    """
    meta, pts, polys = load(base)
    off, scale = meta["offset"], meta["scale"]
    layers = meta["layers"] or [0.0]
    view = _View(top)
    first = next(iter(colors.values()), DEFAULT_COLOR)
    palette = {t: _hex(colors.get(t, first if not meta.get("tools") else DEFAULT_COLOR))
               for t in range(32)}

    hidden = {i for i, name in enumerate(meta.get("features") or []) if name in HIDDEN_FEATURES}
    n_polys = len(polys) // 5
    shown = [i for i in range(n_polys) if polys[i * 5 + 4] not in hidden] or list(range(n_polys))

    # Image crop from the drawn paths themselves (without wipe tower): project every point,
    # for large prints only every n-th. A box around the model would be far too large for stars
    # or round parts in the angled view, and the model would then look small.
    total_pts = sum(polys[i * 5 + 1] for i in shown)
    stride = max(1, total_pts // 200_000)
    xs, ys = [], []
    for i in shown:
        start, count, layer = polys[i * 5], polys[i * 5 + 1], polys[i * 5 + 2]
        z = layers[layer] if layer < len(layers) else layers[-1]
        for j in (*range(start, start + count, stride), start + count - 1):
            x, y = pts[2 * j] / scale - off, pts[2 * j + 1] / scale - off
            xs.append(view.sx(x, y))
            ys.append(view.sy(x, y, z))
    span = max(max(xs) - min(xs), max(ys) - min(ys), 5.0)
    px = SIZE * SUPERSAMPLE
    k = px * (1 - 2 * MARGIN) / span
    ox = px / 2 - (min(xs) + max(xs)) / 2 * k
    oy = px / 2 - (min(ys) + max(ys)) / 2 * k
    minx, maxx = min(pts[0::2]) / scale - off, max(pts[0::2]) / scale - off  # only for the fallback plate
    miny, maxy = min(pts[1::2]) / scale - off, max(pts[1::2]) / scale - off

    def to_px(x, y, z=0.0):
        return view.sx(x, y) * k + ox, view.sy(x, y, z) * k + oy

    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if plate:
        _draw_plate(draw, to_px, bed or [min(0.0, minx), min(0.0, miny), max(256.0, maxx), max(256.0, maxy)], k)

    # Stroke width: path width, but at least wide enough that stacked layers
    # merge into a surface without gaps
    heights = [b - a for a, b in zip(layers, layers[1:], strict=False) if b > a]
    layer_h = sorted(heights)[len(heights) // 2] if heights else 0.2
    # Slightly wider than the nozzle: otherwise gaps remain between paths on smooth surfaces
    width = max(1, round(max(nozzle * 1.3, layer_h * view.ce * 1.25) * k))

    draw_polys = shown
    # Too many segments? Then draw only every n-th layer (always the top ones) instead of dropping
    # points: dropped points cut off curves, and the interior shows through the gaps
    total = sum(polys[i * 5 + 1] - 1 for i in draw_polys)
    layer_step = max(1, math.ceil(total / MAX_SEGMENTS))
    if layer_step > 1:
        top = len(layers) - layer_step
        draw_polys = [i for i in draw_polys if polys[i * 5 + 2] % layer_step == 0 or polys[i * 5 + 2] >= top]
        width = max(width, round(layer_h * layer_step * view.ce * 1.15 * k))
    # Light from the front left above, in the plane: walls facing the light are brighter
    lx, ly = -0.45, -0.89
    # Segments stored compactly in arrays (pixels, depth, color), only an index list is sorted:
    # this keeps memory small even for large prints
    seg_xy, depth, color = array("f"), array("d"), array("I")
    shades: dict[tuple, int] = {}
    shade_list: list[tuple] = []
    sx, sy, dp = view.sx, view.sy, view.depth
    for i in draw_polys:
        start, count, layer, tool, _feat = polys[i * 5:i * 5 + 5]
        z = layers[layer] if layer < len(layers) else layers[-1]
        rgb = palette.get(tool, palette[0])
        last = start + count - 1
        idx = range(start, last + 1)
        x0, y0 = pts[idx[0] * 2] / scale - off, pts[idx[0] * 2 + 1] / scale - off
        for j in idx[1:]:
            x1, y1 = pts[j * 2] / scale - off, pts[j * 2 + 1] / scale - off
            dx, dy = x1 - x0, y1 - y0
            ln = math.hypot(dx, dy)
            if ln > 1e-9:
                key = (rgb, round(8 * abs(dy * lx - dx * ly) / ln))  # 9 brightness levels per color
                c = shades.get(key)
                if c is None:
                    c = shades[key] = len(shade_list)
                    shade_list.append(_shade(rgb, 0.62 + 0.38 * key[1] / 8))
                # Split long segments (infill) into short pieces: sorting is by midpoint, and a long
                # path further down would otherwise end up above shorter ones further up
                parts = max(1, int(ln / SPLIT_MM))
                for m in range(parts):
                    ax, ay = x0 + dx * m / parts, y0 + dy * m / parts
                    bx, by = x0 + dx * (m + 1) / parts, y0 + dy * (m + 1) / parts
                    seg_xy.extend((sx(ax, ay) * k + ox, sy(ax, ay, z) * k + oy,
                                   sx(bx, by) * k + ox, sy(bx, by, z) * k + oy))
                    # First by height, then by distance: seen from above at an angle, only higher parts occlude.
                    # Sorted by distance only, wide strokes of the layer below painted over the
                    # surface and let a grid pattern show through.
                    depth.append(z * 10_000 + dp((ax + bx) / 2, (ay + by) / 2, z))
                    color.append(c)
            x0, y0 = x1, y1
    line = draw.line
    for n in sorted(range(len(depth)), key=depth.__getitem__):
        q = n * 4
        line(seg_xy[q:q + 4].tolist(), fill=shade_list[color[n]], width=width)

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    img.resize((SIZE, SIZE), Image.LANCZOS).save(tmp, format="PNG", optimize=True)
    tmp.replace(out)


def _draw_plate(draw: ImageDraw.ImageDraw, to_px, bed: list[float], k: float) -> None:
    x0, y0, x1, y1 = bed
    rim = 3.0
    draw.polygon([to_px(x, y) for x, y in ((x0 - rim, y0 - rim), (x1 + rim, y0 - rim),
                                            (x1 + rim, y1 + rim), (x0 - rim, y1 + rim))], fill=EDGE)
    draw.polygon([to_px(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))], fill=PLATE)
    w1, w2 = max(1, round(0.25 * k)), max(1, round(0.5 * k))
    for mm, color, w in ((10, GRID, w1), (50, GRID_STRONG, w2)):
        v = math.ceil(x0 / mm) * mm
        while v < x1:
            if v > x0:
                draw.line((to_px(v, y0), to_px(v, y1)), fill=color, width=w)
            v += mm
        v = math.ceil(y0 / mm) * mm
        while v < y1:
            if v > y0:
                draw.line((to_px(x0, v), to_px(x1, v)), fill=color, width=w)
            v += mm


def render_plate(fid: int, idx: int, with_plate: bool = True) -> bool:
    """Both images (angled and from above) of a plate, as soon as its 3D preview is ready."""
    from . import store
    from .routes_preview import bed_area
    base = store.DATA_DIR / "preview" / f"{fid}_{idx}"
    if not base.with_suffix(".json").exists():
        return False
    with store.connect() as con:
        f = con.execute("SELECT path, nozzle FROM files WHERE id = ? AND missing = 0", (fid,)).fetchone()
        pl = con.execute("SELECT filaments_json FROM plates WHERE file_id = ? AND idx = ?", (fid, idx)).fetchone()
    if not f or not pl:
        return False
    filaments = json.loads(pl["filaments_json"] or "[]")
    colors = {int(x["slot"]) - 1: x["color"] for x in filaments if x.get("slot") and x.get("color")}
    if not colors and filaments and filaments[0].get("color"):
        colors = {0: filaments[0]["color"]}
    try:
        nozzle = float(f["nozzle"] or 0.4)
    except ValueError:
        nozzle = 0.4
    bed = bed_area(store.LIBRARY_DIR / f["path"])
    folder = store.THUMB_DIR / str(fid)
    for kind, top in (("iso", False), ("top", True)):
        render(base, folder / file_name(kind, idx, with_plate), colors, bed, top=top, plate=with_plate,
               nozzle=nozzle)
    # Have the project list rebuilt (it is cached until the database changes)
    store.save_settings({"vorschaubilder_stand": f"{fid}.{idx}"})
    return True


def ensure(fid: int, idx: int, current: str | None = None) -> None:
    """Catch up on missing images; errors only go to the log, the slicer image then stays."""
    current = current or mode()
    if current == "slicer" or rendered(fid, idx, current, "top"):
        return
    try:
        render_plate(fid, idx, current == "eigene")
    except Exception:  # noqa: BLE001
        log.exception("Vorschaubild für Datei %s, Platte %s fehlgeschlagen", fid, idx)


def _thumb_dir(fid: int) -> Path:
    from . import store
    return store.THUMB_DIR / str(fid)
