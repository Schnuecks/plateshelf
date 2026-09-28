# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Lasttest mit einer großen Beispielbibliothek: Einlesen, Seiten und Abfragen messen.

    python tools/load_test.py            # 2000 Dateien
    python tools/load_test.py 5000       # andere Anzahl

Legt alles in einem temporären Ordner an und gibt die Zeiten aus. Die Dateien sind
klein (ohne echten G-Code), gemessen wird also PlateShelf selbst, nicht das Entpacken.
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

COLOURS = ("#E0404AFF", "#3A7BD5FF", "#F2C94CFF", "#2D9C74FF", "#FFFFFFFF", "#2B2B2BFF")
MATERIALS = ("PLA", "PETG", "ABS", "TPU", "ASA")


def build(lib: Path, count: int) -> None:
    from make_sample import make
    rng = random.Random(7)
    made = 0
    project = 0
    while made < count:
        project += 1
        folder = lib / f"Projekt {project:04d}" if project % 3 else lib
        folder.mkdir(parents=True, exist_ok=True)
        for part in range(rng.randint(1, 4)):
            plates = [{"idx": i + 1, "sec": rng.randint(600, 30000), "layers": rng.randint(20, 400),
                       "fil": [(1, rng.choice(COLOURS), rng.uniform(2, 120))]} for i in range(rng.randint(1, 3))]
            name = f"Teil_{project:04d}_{part}.gcode.3mf" if folder != lib else f"Modell_{project:04d}_plate_{part + 1}.gcode.3mf"
            mat = rng.choice(MATERIALS)
            make(folder / name, plates, types=(mat,), colours=(plates[0]["fil"][0][1],), costs=("24.99",))
            made += 1
            if made >= count:
                break


def add_prints(count: int) -> None:
    from app import print_history as ph, printer_import as pi, store
    pi.init()
    printer = {"name": "P2S", "id": "SER1", "code": "", "ip": "x", "kind": "bambu"}
    with store.connect() as con:
        paths = [r[0] for r in con.execute("SELECT path FROM files")]
        for p in paths:
            con.execute("INSERT INTO printer_files (printer, remote_name, size, remote_date, local_path, printer_name, "
                        "imported) VALUES ('SER1', ?, 1, 'x', ?, 'P2S', 0)", (p, p))
    rng = random.Random(3)
    now = time.time()
    for i in range(count):
        start = now - rng.uniform(0, 365 * 86400)
        status = rng.choices(("completed", "cancelled", "failed"), (85, 10, 5))[0]
        ph.record(printer, f"k{i}", status, remote_name=rng.choice(paths), started=start,
                  ended=start + rng.uniform(600, 20000))


def timed(label, fn, repeat=1):
    best = None
    for _ in range(repeat):
        t = time.perf_counter()
        out = fn()
        took = time.perf_counter() - t
        best = took if best is None else min(best, took)
    print(f"{label:<42} {best * 1000:8.0f} ms")
    return out


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    tmp = Path(tempfile.mkdtemp(prefix="plateshelf-load-"))
    os.environ.update(AUTH="off", BACKGROUND_TASKS="false", PRINT_HISTORY="true", SCAN_INTERVAL="3600", PREVIEW_PREBUILD="false")
    from app import store
    store.LIBRARY_DIR, store.DATA_DIR = tmp / "library", tmp / "data"
    store.DB_PATH, store.THUMB_DIR, store.PREBUILD = tmp / "data" / "plateshelf.sqlite", tmp / "data" / "thumbs", False
    store.LIBRARY_DIR.mkdir(parents=True)
    timed(f"Bibliothek mit {count} Dateien erzeugen", lambda: build(store.LIBRARY_DIR, count))
    store.init_db()
    tracemalloc.start()
    timed("Erstes Einlesen", lambda: store.scan(wait=True))
    timed("Erneutes Einlesen ohne Änderung", lambda: store.scan(wait=True), repeat=3)
    timed("3000 Drucke im Verlauf anlegen", lambda: add_prints(3000))

    from fastapi.testclient import TestClient
    from app import main as app_main
    with TestClient(app_main.app) as client:
        with store.connect() as con:
            projects = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            pid = con.execute("SELECT project_id FROM files LIMIT 1").fetchone()[0]
        print(f"{'Projekte':<42} {projects:8d}")
        page = timed("Übersicht /", lambda: client.get("/"), repeat=3)
        print(f"{'  Größe der Seite':<42} {len(page.content) // 1024:8d} KB")
        timed("Suche /?q=Teil_0100", lambda: client.get("/?q=Teil_0100"), repeat=3)
        timed("Sortiert nach Druckverlauf", lambda: client.get("/?sort=gedruckt"), repeat=3)
        timed("Projektseite", lambda: client.get(f"/p/{pid}"), repeat=3)
        timed("Verlauf /history", lambda: client.get("/history"), repeat=3)
        timed("Einstellungen /settings", lambda: client.get("/settings"), repeat=3)
        timed("Kennzahlen /api/stats", lambda: client.get("/api/stats"), repeat=3)
        for extra in sys.argv[2:]:
            timed(extra, lambda: client.get(extra), repeat=3)
    current, peak = tracemalloc.get_traced_memory()
    print(f"{'Speicher Python (Spitze)':<42} {peak // 1024 // 1024:8d} MB")
    print(f"{'Datenbank':<42} {store.DB_PATH.stat().st_size // 1024:8d} KB")


if __name__ == "__main__":
    main()
