# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Eigene Vorschaubilder aus den Druckbahnen der 3D-Vorschau.

Slicer legen sehr unterschiedliche Bilder in die Dateien (PrusaSlicer mit Druckbett, Bambu Studio
ohne, Cura oft gar keins). Hier entsteht für jede geslicte Platte ein Bild im selben Stil: schräg
von vorn rechts wie die 3D-Vorschau, dazu eines von oben. Gezeichnet wird mit Pillow, Bahn für
Bahn von hinten nach vorn, Wände je nach Richtung heller oder dunkler.
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

VERSION = 3  # neue Nummer = alte Bilder werden neu gezeichnet
SIZE = 512          # wie die Bilder von Bambu Studio
SUPERSAMPLE = 2     # doppelt so groß zeichnen und verkleinern: glatte Kanten
MARGIN = 0.07
SPLIT_MM = 3.0
MAX_SEGMENTS = 600_000     # sehr große Drucke ausdünnen: ein Bild dauert so nur Sekunden
PLATE = (58, 62, 68)       # neutrale, dunkle Platte (passt hell wie dunkel)
GRID = (74, 79, 86)
GRID_STRONG = (92, 98, 106)
EDGE = (110, 117, 126)
DEFAULT_COLOR = "#E0E0E0"
# Wie die Bilder der Slicer: ohne Reinigungsturm, Skirt und Stützen, die gehören nicht zum Modell
HIDDEN_FEATURES = {"Reinigungsturm", "Skirt", "Stützen", "Stützen-Kontakt", "Stützen-Übergang"}
# Blickrichtung wie „Ansicht zurücksetzen“ in der 3D-Vorschau (Kamera vorn rechts, leicht von oben)
_CAM = (1.1, -1.6, 1.1)


MODES = ("eigene", "ohne_platte", "slicer")  # Einstellung „vorschaubilder“, Vorgabe: eigene mit Platte


def mode(settings: dict | None = None) -> str:
    from . import store
    value = (settings if settings is not None else store.get_settings()).get("vorschaubilder")
    return value if value in MODES else MODES[0]


def file_name(kind: str, idx: int, with_plate: bool = True) -> str:
    """Dateiname im Vorschaubild-Ordner der Datei, z. B. render1_iso_2.png (ohne Platte: render1n_…)."""
    return f"render{VERSION}{'' if with_plate else 'n'}_{kind}_{idx}.png"


def rendered(fid: int, idx: int, current: str, kind: str = "iso") -> Path | None:
    """Eigenes Bild einer Platte, falls eingeschaltet und schon gezeichnet."""
    if current == "slicer":
        return None
    path = _thumb_dir(fid) / file_name(kind, idx, current == "eigene")
    return path if path.exists() else None


def apply(plate: dict, current: str) -> None:
    """Platte (aus projects) auf die eigenen Bilder umstellen, wenn es sie gibt: dann zeigt jede
    Platte ein Bild, auch ohne Vorschau vom Slicer, und die Bildadresse ändert sich mit."""
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
    """Metadaten, Punkte (x, y abwechselnd, uint16) und Bahnen (je 5 Werte) einer 3D-Vorschau."""
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
    """Parallelprojektion: Bildschirm-x, Bildschirm-y (nach unten) und Tiefe (größer = näher)."""

    def __init__(self, top: bool):
        if top:
            self.rx, self.ry = 1.0, 0.0     # rechts = +x
            self.ux, self.uy = 0.0, -1.0    # zur Kamera = -y (vorn), oben wäre z
            self.ce, self.se = 0.0, 1.0     # Blick senkrecht von oben
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
    """Zeichnet die Druckbahnen der Vorschau unter base nach out (PNG, transparent außerhalb der Platte).

    colors: Werkzeug (0-basiert) -> Farbe; ohne Werkzeugwechsel gilt die erste Farbe für alles.
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

    # Bildausschnitt aus den gezeichneten Bahnen selbst (ohne Reinigungsturm): jeden Punkt projizieren,
    # bei großen Drucken nur jeden n-ten. Eine Box um das Modell wäre bei Sternen oder runden
    # Teilen in der Schrägansicht viel zu groß, das Modell wirkte dann klein.
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
    minx, maxx = min(pts[0::2]) / scale - off, max(pts[0::2]) / scale - off  # nur für die Ersatzplatte
    miny, maxy = min(pts[1::2]) / scale - off, max(pts[1::2]) / scale - off

    def to_px(x, y, z=0.0):
        return view.sx(x, y) * k + ox, view.sy(x, y, z) * k + oy

    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if plate:
        _draw_plate(draw, to_px, bed or [min(0.0, minx), min(0.0, miny), max(256.0, maxx), max(256.0, maxy)], k)

    # Strichbreite: Bahnbreite, aber mindestens so breit, dass übereinanderliegende Schichten
    # ohne Lücke zu einer Fläche werden
    heights = [b - a for a, b in zip(layers, layers[1:], strict=False) if b > a]
    layer_h = sorted(heights)[len(heights) // 2] if heights else 0.2
    # Etwas breiter als die Düse: sonst bleiben auf glatten Oberflächen Lücken zwischen den Bahnen
    width = max(1, round(max(nozzle * 1.3, layer_h * view.ce * 1.25) * k))

    total = sum(polys[i * 5 + 1] - 1 for i in shown)
    step = max(1, math.ceil(total / MAX_SEGMENTS))
    # Licht von vorn links oben, in der Ebene: Wände, die zum Licht zeigen, sind heller
    lx, ly = -0.45, -0.89
    # Strecken kompakt in Arrays (Bildpunkte, Tiefe, Farbe), sortiert wird nur eine Indexliste:
    # so bleibt der Speicher auch bei großen Drucken klein
    seg_xy, depth, color = array("f"), array("d"), array("I")
    shades: dict[tuple, int] = {}
    shade_list: list[tuple] = []
    sx, sy, dp = view.sx, view.sy, view.depth
    for i in shown:
        start, count, layer, tool, _feat = polys[i * 5:i * 5 + 5]
        z = layers[layer] if layer < len(layers) else layers[-1]
        rgb = palette.get(tool, palette[0])
        last = start + count - 1
        idx = list(range(start, last, step)) + [last]
        x0, y0 = pts[idx[0] * 2] / scale - off, pts[idx[0] * 2 + 1] / scale - off
        for j in idx[1:]:
            x1, y1 = pts[j * 2] / scale - off, pts[j * 2 + 1] / scale - off
            dx, dy = x1 - x0, y1 - y0
            ln = math.hypot(dx, dy)
            if ln > 1e-9:
                key = (rgb, round(8 * abs(dy * lx - dx * ly) / ln))  # 9 Helligkeitsstufen je Farbe
                c = shades.get(key)
                if c is None:
                    c = shades[key] = len(shade_list)
                    shade_list.append(_shade(rgb, 0.62 + 0.38 * key[1] / 8))
                # Lange Strecken (Infill) in kurze Stücke teilen: sortiert wird nach der Mitte, und
                # eine lange Bahn weiter unten würde sonst über kürzeren weiter oben landen
                parts = max(1, int(ln / SPLIT_MM))
                for m in range(parts):
                    ax, ay = x0 + dx * m / parts, y0 + dy * m / parts
                    bx, by = x0 + dx * (m + 1) / parts, y0 + dy * (m + 1) / parts
                    seg_xy.extend((sx(ax, ay) * k + ox, sy(ax, ay, z) * k + oy,
                                   sx(bx, by) * k + ox, sy(bx, by, z) * k + oy))
                    # Erst nach Höhe, dann nach Abstand: von schräg oben verdeckt nur, was höher liegt.
                    # Nur nach Abstand sortiert, malten breite Striche der Schicht darunter über die
                    # Oberfläche und ließen ein Gittermuster durchscheinen.
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
    """Beide Bilder (schräg und von oben) einer Platte, sobald ihre 3D-Vorschau fertig ist."""
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
    # Projektliste neu aufbauen lassen (sie wird zwischengespeichert, bis sich die Datenbank ändert)
    store.save_settings({"vorschaubilder_stand": f"{fid}.{idx}"})
    return True


def ensure(fid: int, idx: int, current: str | None = None) -> None:
    """Fehlende Bilder nachholen; Fehler nur ins Log, das Slicer-Bild bleibt dann stehen."""
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
