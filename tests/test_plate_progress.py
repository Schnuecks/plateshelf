"""Plate progress: which plates of a project have already been printed successfully."""
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
    """Each plate as its own file (import from the P2S): every print of the file counts for its plate."""
    folder = lib / "Reaper"
    folder.mkdir()
    for n in (1, 2, 3, 4):
        sample(folder / f"Reaper_plate_{n}.gcode.3mf", [{"idx": n, **PLATE}])
    store.scan(wait=True)
    add_job("Reaper/Reaper_plate_2.gcode.3mf", plate=2)
    add_job("Reaper/Reaper_plate_3.gcode.3mf", plate=None)  # old print without plate info
    add_job("Reaper/Reaper_plate_3.gcode.3mf", plate=3)
    add_job("Reaper/Reaper_plate_4.gcode.3mf", status="cancelled")  # does not count
    add_job("Reaper/Reaper_plate_1.gcode.3mf", hidden=1)  # removed from the history, does not count
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
    add_job("Set.gcode.3mf", plate=None)  # cannot be assigned without plate info
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


def test_same_plate_sliced_twice_shows_both_versions(client, lib, sample):
    """Re-sliced and printed again: two versions of plate 2, newest first, with date."""
    import os
    import re
    for folder, stamp, sec in (("Vom Drucker", 1_790_000_000, 60), ("From printer", 1_790_600_000, 90)):
        path = lib / folder / "P2S" / "Eevee_Chibi_plate_2.gcode.3mf"
        path.parent.mkdir(parents=True)
        sample(path, [{**PLATE, "idx": 2, "name": "BODY", "sec": sec}])  # really sliced differently
        os.utime(path, (stamp, stamp))
    sample(lib / "From printer" / "P2S" / "Eevee_Chibi_plate_3.gcode.3mf", [{**PLATE, "idx": 3}])
    store.scan(wait=True)
    page = client.get(f"/p/{project('Eevee Chibi')['id']}").text
    strip = page[page.index('class="plate-strip"'):page.index("</ol>")]
    assert strip.count("Platte 2: BODY") == 2 and strip.count("strip-version") == 2
    dates = re.findall(r'strip-version">([^<]+)<', strip)
    assert dates[0].startswith(time.strftime("%d.%m.%Y", time.localtime(1_790_600_000))), dates
    assert "0,20 mm" in dates[0] and "Platte 3" in strip.split(dates[1])[1]  # Platte 3 without suffix


def test_repeated_slice_of_a_plate_counts_once(client, lib, sample):
    """As on 25.10.: plate 3 was printed from its own file, later all plates were exported again
    (plate 3 sliced anew, 3 minutes longer). The bar shows plate 3 once, the printed version; the
    totals count every plate once, with its latest slice."""
    import os
    folder = lib / "Gnome"
    folder.mkdir()
    old, new = folder / "Gnome_Trio_plate_3.gcode.3mf", folder / "Gnome_Trio.gcode.3mf"
    sample(old, [{"idx": 3, "sec": 15600, "layers": 2, "fil": [(1, "#00AA00FF", 86.2)]}])
    sample(new, [{"idx": 1, "sec": 11900, "layers": 2, "fil": [(1, "#00AA00FF", 77.1)]},
                 {"idx": 2, "sec": 12400, "layers": 2, "fil": [(1, "#00AA00FF", 77.5)]},
                 {"idx": 3, "sec": 15780, "layers": 2, "fil": [(1, "#00AA00FF", 86.2)]}])
    os.utime(old, (1_790_000_000, 1_790_000_000))
    store.scan(wait=True)
    add_job("Gnome/Gnome_Trio_plate_3.gcode.3mf", plate=3)
    add_job("Gnome/Gnome_Trio.gcode.3mf", plate=1)
    p = project("Gnome")  # a subfolder becomes the project
    assert p["plate_count"] == 3 and p["plates_printed"] == 2
    assert p["time_s"] == 11900 + 12400 + 15780  # plate 3 once, the latest slice
    page = client.get(f"/p/{p['id']}").text
    strip = page[page.index('class="plate-strip"'):page.index("</ol>")]
    assert strip.count("Platte 3") == 1 and "strip-version" not in strip
    third = strip[strip.index("Platte 3") - 600:strip.index("Platte 3")]
    old_id = next(f["id"] for f in p["files"] if f["path"].endswith("_plate_3.gcode.3mf"))
    assert "plate-done" in third and f"/thumb/{old_id}/plate/3" in third  # the printed version
