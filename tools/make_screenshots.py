# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Erzeugt die Screenshots für das README (docs/screenshots/*.png).

    pip install -r requirements-browser.txt && python -m playwright install chromium
    python tools/make_screenshots.py

Baut eine Beispielbibliothek mit englischen Namen, startet PlateShelf auf Englisch im
dunklen Design und fotografiert Übersicht, Projektseite, 3D-Vorschau und „About“.
"""
from __future__ import annotations

import math
import os
import socket
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
OUT = ROOT / "docs" / "screenshots"


def planter_gcode(layers: int = 150) -> str:
    """Pflanztopf mit geschwungener Wand, Infill und Deckschichten, mit echten Druckbahnen."""
    epm = 0.0349
    out = ["; HEADER_BLOCK_START", f"; total layer number: {layers}", "; HEADER_BLOCK_END",
           "M83", "M104 S220", "M106 P1 S0"]
    cx = cy = 128.0
    last = [cx, cy]

    def mv(x, y, f):
        d = math.hypot(x - last[0], y - last[1])
        last[:] = [x, y]
        return f"G1 X{x:.3f} Y{y:.3f} E{d * epm:.5f} F{f}"

    for k in range(layers):
        z = round(0.2 * (k + 1), 2)
        out += ["; CHANGE_LAYER", f"; Z_HEIGHT: {z}", f"G1 Z{z}"]
        if k == 3:
            out.append("M106 P1 S255")
        r = 22 + 7 * math.sin(k / layers * math.pi * 1.2) + 0.02 * k
        for w, feat, f in ((r, "Outer wall", 3000), (r - 0.42, "Inner wall", 6000), (r - 0.84, "Inner wall", 6000)):
            out += [f"; FEATURE: {feat}", "G1 E-0.8 F2400", f"G0 X{cx + w:.3f} Y{cy:.3f}", "G1 E0.8 F2400"]
            last[:] = [cx + w, cy]
            for i in range(1, 181):
                a = 2 * math.pi * i / 180
                out.append(mv(cx + w * math.cos(a), cy + w * math.sin(a), f))
        if k < 4:
            feat, step, f, rr = "Bottom surface", 0.42, 3000, r - 1.2
        elif k < 12:
            feat, step, f, rr = "Sparse infill", 5.0, 12000, r - 1.2
        else:
            continue
        out.append(f"; FEATURE: {feat}")
        d, flip, ang = -rr, False, math.pi / 4 * (1 if k % 2 else -1)
        ca, sa = math.cos(ang), math.sin(ang)
        while d < rr:
            h = math.sqrt(max(0.0, rr * rr - d * d))
            p1, p2 = (-h, d), (h, d)
            if flip:
                p1, p2 = p2, p1
            out.append(f"G0 X{cx + p1[0] * ca - p1[1] * sa:.3f} Y{cy + p1[0] * sa + p1[1] * ca:.3f}")
            last[:] = [cx + p1[0] * ca - p1[1] * sa, cy + p1[0] * sa + p1[1] * ca]
            out.append(mv(cx + p2[0] * ca - p2[1] * sa, cy + p2[0] * sa + p2[1] * ca, f))
            d += step
            flip = not flip
    return "\n".join(out) + "\n"


def build_library(lib: Path) -> None:
    from make_sample import make
    (lib / "Wall mount").mkdir(parents=True)
    make(lib / "Wall mount" / "Mount_left.gcode.3mf",
         [{"idx": 1, "sec": 7380, "layers": 210, "fil": [(1, "#2B2B2BFF", 48.3)], "name": "Left side"}],
         colours=("#2B2B2BFF",), types=("PETG",), costs=("22.50",))
    make(lib / "Wall mount" / "Mount_right.gcode.3mf",
         [{"idx": 2, "sec": 7390, "layers": 210, "fil": [(1, "#2B2B2BFF", 48.9)], "name": "Right side"}],
         colours=("#2B2B2BFF",), types=("PETG",), costs=("22.50",))
    make(lib / "Benchy_plate_1.gcode.3mf",
         [{"idx": 1, "sec": 2940, "layers": 240, "fil": [(1, "#F95D73FF", 11.2), (2, "#FFFFFFFF", 2.1)],
           "objects": ["3DBenchy.stl"], "shape": "round"}],
         colours=("#F95D73FF", "#FFFFFFFF"), types=("PLA", "PLA"), costs=("24.99", "24.99"))
    make(lib / "Benchy_plate_2.gcode.3mf",
         [{"idx": 2, "sec": 3120, "layers": 240, "fil": [(1, "#3A7BD5FF", 12.8)], "objects": ["3DBenchy.stl"]}],
         colours=("#3A7BD5FF",), types=("PLA",), costs=("24.99",))
    make(lib / "Cable_clips.gcode.3mf",
         [{"idx": 1, "sec": 1500, "layers": 40, "fil": [(1, "#F2C94CFF", 6.4)], "objects": ["clip.stl"] * 3},
          {"idx": 2, "sec": 1620, "layers": 40, "fil": [(1, "#F2C94CFF", 3.0), (2, "#2D9C74FF", 3.6)]}],
         colours=("#F2C94CFF", "#2D9C74FF"), types=("PLA", "PLA"), costs=("19.99", "19.99"))
    planter = lib / "Planter.gcode.3mf"
    make(planter, [{"idx": 1, "sec": 11400, "layers": 150, "fil": [(1, "#3FA37CFF", 86.0)], "shape": "round",
                    "objects": ["planter.stl"]}], colours=("#3FA37CFF",), types=("PLA",), costs=("24.99",))
    with zipfile.ZipFile(planter) as z:
        items = {n: z.read(n) for n in z.namelist()}
    items["Metadata/plate_1.gcode"] = planter_gcode().encode()
    with zipfile.ZipFile(planter, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in items.items():
            z.writestr(n, d)


def main() -> None:
    from playwright.sync_api import sync_playwright
    import uvicorn

    tmp = Path(tempfile.mkdtemp(prefix="plateshelf-shots-"))
    os.environ.update(DEFAULT_LANGUAGE="en", DEFAULT_THEME="dark", SCAN_INTERVAL="3600", PREVIEW_PREBUILD="false")
    from app import main as app_main, store
    store.LIBRARY_DIR, store.DATA_DIR = tmp / "library", tmp / "data"
    store.DB_PATH, store.THUMB_DIR, store.PREBUILD = tmp / "data" / "plateshelf.sqlite", tmp / "data" / "thumbs", False
    build_library(store.LIBRARY_DIR)
    store.init_db()
    store.scan(wait=True)
    with store.connect() as con:
        fid, pid = con.execute("SELECT id, project_id FROM files WHERE path = 'Planter.gcode.3mf'").fetchone()
        benchy = con.execute("SELECT project_id FROM files WHERE path LIKE 'Benchy%'").fetchone()[0]
        con.execute("UPDATE projects SET status = 'gedruckt', tags = 'boat, calibration', notes = ? WHERE id = ?",
                    ("Printed with 0.2 mm layers. Hull came out perfect.", benchy))
        con.execute("UPDATE projects SET status = 'gedruckt', tags = 'garden' WHERE id = ?", (pid,))

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app_main.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1400, "height": 860}, locale="en-US", color_scheme="dark")
        # 3D-Vorschau: Einfärbung nach Linientyp, Kamera etwas näher
        page.add_init_script(f"""
            localStorage.setItem('plateshelf-color-mode', 'feature');
            localStorage.setItem('plateshelf-cam-{fid}-1', JSON.stringify({{p: [205, 38, 88], t: [128, 128, 14]}}));
        """)

        page.goto(url + "/")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=OUT / "overview.png")

        page.goto(f"{url}/p/{benchy}")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=OUT / "project.png")

        page.goto(f"{url}/view/{fid}/1")
        page.wait_for_selector("#viewer-loading", state="hidden", timeout=120_000)
        page.wait_for_timeout(1500)  # Tiefenschattierung im Stillstand abwarten
        page.screenshot(path=OUT / "preview.png")

        page.goto(url + "/about")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=OUT / "about.png")
        browser.close()
    server.should_exit = True
    for f in sorted(OUT.glob("*.png")):
        print(f"{f.relative_to(ROOT)}  {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
