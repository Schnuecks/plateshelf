"""Eigene Fotos, Notizen pro Druck, doppelte Dateien und Ordnerüberwachung."""
import io
import shutil
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import photos, store, trash


@pytest.fixture
def client(lib, monkeypatch):
    from app import main
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def project(lib, sample):
    sample(lib / "Halter.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    with store.connect() as con:
        return dict(con.execute("SELECT * FROM projects").fetchone())


def camera_jpeg(width=4000, height=3000) -> bytes:
    """Querformat mit EXIF: GPS-Position und „90° drehen“, wie ein Handyfoto im Hochformat."""
    img = Image.new("RGB", (width, height), (200, 60, 40))
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: 90° im Uhrzeigersinn anzeigen
    exif[0x010F] = "Kamerahersteller"
    exif[0x8825] = {1: "N", 2: (52.0, 31.0, 12.0)}  # GPS
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    return out.getvalue()


def test_photo_upload_strips_metadata_and_becomes_cover(client, project, lib):
    pid = project["id"]
    r = client.post(f"/p/{pid}/photos", files=[("files", ("IMG_1234.jpg", camera_jpeg(), "image/jpeg"))],
                    follow_redirects=False)
    assert r.status_code == 303 and "photos=1" in r.headers["location"]
    (name,) = photos.list_photos(project["group_key"])
    folder = photos.folder_for(project["group_key"])
    assert folder.is_relative_to(lib / ".photos")
    with Image.open(folder / name) as big:
        assert big.size == (1800, 2400)  # gedreht wie fotografiert, auf 2400 px verkleinert
        assert not big.getexif()  # keine Metadaten, kein GPS
    with Image.open(folder / photos.small_name(name)) as small:
        assert max(small.size) == photos.SMALL_SIDE
    # Erstes Foto = Titelbild, auch in der Übersicht
    with store.connect() as con:
        assert con.execute("SELECT cover_photo FROM projects WHERE id = ?", (pid,)).fetchone()[0] == name
    assert f'/p/{pid}/photos/{name[:-4]}.small.jpg" alt="" loading="lazy" class="is-photo"' in client.get("/").text
    page = client.get(f"/p/{pid}").text
    assert "Fotos vom Druck" in page and "1 Foto hinzugefügt" in client.get(r.headers["location"]).text
    got = client.get(f"/p/{pid}/photos/{name}")
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg" and "immutable" in got.headers["cache-control"]
    assert client.get(f"/p/{pid}/photos/..%2F..%2Fplateshelf.sqlite").status_code == 404
    # Titelbild abwählen und Foto löschen
    client.post(f"/p/{pid}/photos/{name}/cover")
    assert "is-photo" not in client.get("/").text
    client.post(f"/p/{pid}/photos/{name}/delete")
    assert photos.list_photos(project["group_key"]) == [] and not folder.exists()


def test_unsupported_images_are_reported(client, project):
    pid = project["id"]
    png = io.BytesIO()
    Image.new("RGBA", (50, 40), (0, 0, 0, 0)).save(png, "PNG")
    r = client.post(f"/p/{pid}/photos", files=[("files", ("a.heic", b"\x00\x00\x00\x18ftypheic", "image/heic")),
                                               ("files", ("b.png", png.getvalue(), "image/png"))], follow_redirects=False)
    assert "photos=1&photo_errors=1" in r.headers["location"]
    assert "ließ sich nicht verwenden" in client.get(r.headers["location"]).text


def test_photos_come_back_with_the_project_from_the_trash(client, project, lib):
    pid, key = project["id"], project["group_key"]
    client.post(f"/p/{pid}/photos", files=[("files", ("a.jpg", camera_jpeg(800, 600), "image/jpeg"))])
    (name,) = photos.list_photos(key)
    client.post(f"/p/{pid}/delete")
    assert photos.list_photos(key) == [name]  # bleibt liegen, solange der Eintrag im Papierkorb ist
    (entry,) = trash.entries()
    result = trash.restore(entry["id"])
    with store.connect() as con:
        cover = con.execute("SELECT cover_photo FROM projects WHERE id = ?", (result["project_id"],)).fetchone()[0]
    assert cover == name and photos.list_photos(key) == [name]
    # Endgültig gelöscht: die Fotos verschwinden mit
    with store.connect() as con:
        ids = [r[0] for r in con.execute("SELECT id FROM files WHERE project_id = ?", (result["project_id"],))]
        trash.delete(con, ids, "projekt")
    trash.purge_all()
    assert photos.list_photos(key) == []


def test_note_and_rating_per_print(client, project):
    with store.connect() as con:
        path = con.execute("SELECT path FROM files").fetchone()[0]
        job = con.execute("INSERT INTO print_jobs (printer, printer_name, job_key, local_path, status, started, ended) "
                          "VALUES ('S', 'P1S', 'k1', ?, 'completed', ?, ?)", (path, time.time() - 600, time.time())).lastrowid
    pid = project["id"]
    r = client.post(f"/history/{job}/note", data={"note": "Warping an der Ecke", "rating": "-1", "next": f"/p/{pid}"},
                    follow_redirects=False)
    assert r.headers["location"] == f"/p/{pid}#history"
    page = client.get(f"/p/{pid}").text
    assert "Warping an der Ecke" in page and "👎" in page
    history = client.get("/history").text
    assert "Warping an der Ecke" in history and 'aria-label="Misslungen"' in history
    client.post(f"/history/{job}/note", data={"note": "  ", "rating": ""})
    with store.connect() as con:
        assert tuple(con.execute("SELECT note, rating FROM print_jobs WHERE id = ?", (job,)).fetchone()) == (None, None)
    assert client.post(f"/history/{job}/note", data={"rating": "5"}).status_code == 400
    assert client.post("/history/9999/note", data={"note": "x"}).status_code == 404


def test_duplicate_files_are_pointed_out(client, lib, sample):
    sample(lib / "Clip.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    (lib / "Kiste").mkdir()
    shutil.copy2(lib / "Clip.gcode.3mf", lib / "Kiste" / "Clip_Kopie.gcode.3mf")
    sample(lib / "Anders.gcode.3mf", [{"idx": 1, "sec": 90, "layers": 3, "fil": [(1, "#00FF00FF", 2.0)]}])
    store.scan(wait=True)
    with store.connect() as con:
        pids = {r["name"]: r["id"] for r in con.execute("SELECT id, name FROM projects")}
        assert con.execute("SELECT COUNT(*) FROM files WHERE sha256 IS NULL").fetchone()[0] == 0
    page = client.get(f"/p/{pids['Clip']}").text
    assert "Dieselbe Datei liegt auch hier" in page and "Kiste/Clip_Kopie.gcode.3mf" in page and "Projekt Kiste" in page
    assert "Dieselbe Datei" not in client.get(f"/p/{pids['Anders']}").text
    # Geänderte Datei: Hash neu, kein Hinweis mehr
    sample(lib / "Kiste" / "Clip_Kopie.gcode.3mf", [{"idx": 1, "sec": 70, "layers": 2, "fil": [(1, "#0000FFFF", 1.0)]}])
    store.scan(wait=True)
    assert "Dieselbe Datei" not in client.get(f"/p/{pids['Clip']}").text


def test_schema_4_upgrades_version_3_database(lib):
    with store.connect() as con:
        for table, column in (("projects", "cover_photo"), ("print_jobs", "note"), ("print_jobs", "rating"),
                              ("files", "sha256")):
            con.execute("DROP INDEX IF EXISTS idx_files_sha")
            con.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        con.execute("PRAGMA user_version = 3")
    store.init_db()
    with store.connect() as con:
        assert "cover_photo" in {r["name"] for r in con.execute("PRAGMA table_info(projects)")}
        assert {"note", "rating"} <= {r["name"] for r in con.execute("PRAGMA table_info(print_jobs)")}
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name = 'idx_files_sha'").fetchone()


def test_watch_filter():
    lib = store.LIBRARY_DIR
    relevant = store._watch_relevant
    assert relevant(None, str(lib / "Benchy.gcode.3mf")) and relevant(None, str(lib / "Ordner" / "Teil.bgcode"))
    assert not relevant(None, str(lib / ".trash" / "x" / "Teil.gcode.3mf"))
    assert not relevant(None, str(lib / ".photos" / "abc" / "1.jpg"))
    assert not relevant(None, str(lib / "Ordner" / ".upload-123.part"))
    assert not relevant(None, str(lib / "@eaDir" / "Teil.gcode.3mf"))
    assert not relevant(None, "/ganz/woanders/Teil.gcode.3mf")


def test_new_files_are_found_right_away(lib, sample):
    stop = threading.Event()
    watcher = threading.Thread(target=store.watch_loop, args=(stop,), daemon=True)
    watcher.start()
    try:
        time.sleep(0.5)  # Überwachung läuft
        sample(lib / "Sofort.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
        deadline = time.time() + 15
        while time.time() < deadline:
            with store.connect() as con:
                if con.execute("SELECT 1 FROM files WHERE path = 'Sofort.gcode.3mf'").fetchone():
                    break
            time.sleep(0.2)
        else:
            pytest.fail("Neue Datei wurde nicht erkannt")
    finally:
        stop.set()
        watcher.join(timeout=10)
    assert not watcher.is_alive()
