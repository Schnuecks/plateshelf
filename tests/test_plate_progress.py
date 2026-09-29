"""Platten-Fortschritt: welche Platten eines Projekts schon fertig gedruckt sind."""
import itertools
import time

import pytest
from fastapi.testclient import TestClient

from app import store
from app.projects import load_projects

PLATE = {"sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}
_keys = itertools.count()


@pytest.fixture
def client(lib, monkeypatch):
    from app import main
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as c:
        yield c


def add_job(path, status="completed", plate=None, key=None, hidden=0):
    with store.connect() as con:
        con.execute("INSERT INTO print_jobs (printer, printer_name, job_key, local_path, status, started, ended, plate, "
                    "hidden) VALUES ('S', 'P2S', ?, ?, ?, ?, ?, ?, ?)",
                    (key or f"job-{next(_keys)}", path, status, time.time() - 600, time.time(), plate, hidden))


def project(name):
    with store.connect() as con:
        return next(p for p in load_projects(con) if p["name"] == name)


def test_single_plate_files_like_the_p2s(client, lib, sample):
    """Jede Platte als eigene Datei (Import vom P2S): jeder Druck der Datei zählt für ihre Platte."""
    folder = lib / "Reaper"
    folder.mkdir()
    for n in (1, 2, 3, 4):
        sample(folder / f"Reaper_plate_{n}.gcode.3mf", [{"idx": n, **PLATE}])
    store.scan(wait=True)
    add_job("Reaper/Reaper_plate_2.gcode.3mf", plate=2)
    add_job("Reaper/Reaper_plate_3.gcode.3mf", plate=None)  # alter Druck ohne Plattenangabe
    add_job("Reaper/Reaper_plate_3.gcode.3mf", plate=3)
    add_job("Reaper/Reaper_plate_4.gcode.3mf", status="cancelled")  # zählt nicht
    add_job("Reaper/Reaper_plate_1.gcode.3mf", hidden=1)  # aus dem Verlauf entfernt, zählt nicht
    p = project("Reaper")
    assert {x["idx"]: x["printed"] for x in p["plates"]} == {1: 0, 2: 1, 3: 2, 4: 0}
    assert p["plates_printed"] == 2
    overview = client.get("/").text
    assert "2/4 Platten gedruckt" in overview
    page = client.get(f"/p/{p['id']}").text
    assert "davon 2 gedruckt" in page and page.count('class="plate-done"') == 2 and "Gedruckt (2×)" in page
    # Filter „Teilweise gedruckt“
    assert "Reaper" in client.get("/?printed=teilweise").text
    for n in (1, 4):
        add_job(f"Reaper/Reaper_plate_{n}.gcode.3mf")
    overview = client.get("/").text
    assert "4/4 Platten gedruckt" in overview and "prints is-complete" in overview
    assert "Reaper" not in client.get("/?printed=teilweise").text


def test_several_plates_in_one_file_use_the_reported_plate(client, lib, sample):
    sample(lib / "Set.gcode.3mf", [{"idx": 1, **PLATE}, {"idx": 2, **PLATE}, {"idx": 3, **PLATE}])
    store.scan(wait=True)
    add_job("Set.gcode.3mf", plate=3)
    add_job("Set.gcode.3mf", plate=None)  # ohne Plattenangabe nicht zuzuordnen
    p = project("Set")
    assert {x["idx"]: x["printed"] for x in p["plates"]} == {1: 0, 2: 0, 3: 1}
    assert "1/3 Platten gedruckt" in client.get("/").text


def test_single_plate_projects_keep_the_print_count(client, lib, sample):
    sample(lib / "Clip.gcode.3mf", [{"idx": 1, **PLATE}])
    store.scan(wait=True)
    add_job("Clip.gcode.3mf")
    add_job("Clip.gcode.3mf")
    overview = client.get("/").text
    assert "2× gedruckt" in overview and "Platten gedruckt" not in overview
    assert "Clip" not in client.get("/?printed=teilweise").text
