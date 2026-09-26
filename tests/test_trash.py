import time

import pytest
from fastapi.testclient import TestClient

from app import store, trash

PLATE = [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}]


@pytest.fixture
def client(lib, monkeypatch):
    from app import main
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as c:
        yield c


def _project(con, name):
    return con.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()


def test_project_restore_brings_back_notes_and_manual_assignment(lib, sample):
    (lib / "Halter").mkdir()
    sample(lib / "Halter" / "Halter.gcode.3mf", PLATE)
    sample(lib / "Clip.gcode.3mf", PLATE)
    store.scan(wait=True)
    with store.connect() as con:
        pid = _project(con, "Halter")["id"]
        con.execute("UPDATE projects SET notes = 'PETG nehmen', tags = 'Werkstatt', status = 'gedruckt' WHERE id = ?",
                    (pid,))
        # Clip von Hand dem Projekt Halter zugeordnet
        con.execute("UPDATE files SET project_id = ?, pinned = 1 WHERE path = 'Clip.gcode.3mf'", (pid,))
        ids = [r["id"] for r in con.execute("SELECT id FROM files WHERE project_id = ?", (pid,))]
        entry = trash.delete(con, ids, "projekt")
    assert not (lib / "Halter").exists() and not (lib / "Clip.gcode.3mf").exists()
    with store.connect() as con:
        assert _project(con, "Halter") is None

    result = trash.restore(entry)
    assert result["restored"] == 2 and not result["renamed"]
    assert (lib / "Halter" / "Halter.gcode.3mf").exists() and (lib / "Clip.gcode.3mf").exists()
    with store.connect() as con:
        p = _project(con, "Halter")
        assert result["project_id"] == p["id"]
        assert (p["notes"], p["tags"], p["status"]) == ("PETG nehmen", "Werkstatt", "gedruckt")
        clip = con.execute("SELECT project_id, pinned FROM files WHERE path = 'Clip.gcode.3mf'").fetchone()
        assert (clip["project_id"], clip["pinned"]) == (p["id"], 1)
        assert con.execute("SELECT COUNT(*) FROM trash").fetchone()[0] == 0
    assert not list(trash.trash_root().iterdir())


def test_restore_never_overwrites(lib, sample):
    sample(lib / "Benchy.gcode.3mf", PLATE)
    store.scan(wait=True)
    with store.connect() as con:
        entry = trash.delete(con, [con.execute("SELECT id FROM files").fetchone()[0]], "datei")
    newer = [{"idx": 1, "sec": 120, "layers": 4, "fil": [(1, "#00FF00FF", 2.0)]}]
    sample(lib / "Benchy.gcode.3mf", newer)  # inzwischen neue Datei mit gleichem Namen
    before = (lib / "Benchy.gcode.3mf").read_bytes()

    result = trash.restore(entry)
    assert result["renamed"] == ["Benchy (2).gcode.3mf"]
    assert (lib / "Benchy.gcode.3mf").read_bytes() == before
    with store.connect() as con:
        # Beide landen über den Namen im selben Projekt
        assert con.execute("SELECT COUNT(DISTINCT project_id) FROM files").fetchone()[0] == 1


def test_file_restore_keeps_current_project_details(lib, sample):
    (lib / "Box").mkdir()
    sample(lib / "Box" / "Deckel.gcode.3mf", PLATE)
    sample(lib / "Box" / "Boden.gcode.3mf", PLATE)
    store.scan(wait=True)
    with store.connect() as con:
        pid = _project(con, "Box")["id"]
        con.execute("UPDATE projects SET notes = 'alt' WHERE id = ?", (pid,))
        fid = con.execute("SELECT id FROM files WHERE path = 'Box/Deckel.gcode.3mf'").fetchone()[0]
        entry = trash.delete(con, [fid], "datei")
        con.execute("UPDATE projects SET notes = '' WHERE id = ?", (pid,))  # danach bewusst geleert
    trash.restore(entry)
    with store.connect() as con:
        p = _project(con, "Box")
        assert p["id"] == pid and p["notes"] == ""
        assert con.execute("SELECT COUNT(*) FROM files WHERE project_id = ?", (pid,)).fetchone()[0] == 2


def test_old_trash_folders_are_adopted_and_expire_later(lib, sample):
    old = lib / ".trash" / "2026-01-02_03-04-05" / "Vase"
    old.mkdir(parents=True)
    sample(old / "Vase.gcode.3mf", PLATE)
    (lib / ".trash" / "2026-01-03_00-00-00").mkdir()  # leerer Ordner wird ignoriert

    entries = trash.entries()
    assert [(e["kind"], e["label"], e["files"]) for e in entries] == [("alt", "Vase", ["Vase/Vase.gcode.3mf"])]
    assert time.localtime(entries[0]["deleted"]).tm_year == 2026
    # Die Frist beginnt mit der Übernahme, alte Einträge verschwinden nicht sofort
    assert trash.purge_expired(days=30) == 0
    assert trash.purge_expired(days=30, now=time.time() + 31 * 86400) == 1
    assert not (lib / ".trash" / "2026-01-02_03-04-05").exists()


def test_manually_emptied_entries_disappear(lib, sample):
    sample(lib / "Teil.gcode.3mf", PLATE)
    store.scan(wait=True)
    with store.connect() as con:
        entry = trash.delete(con, [con.execute("SELECT id FROM files").fetchone()[0]], "datei")
        folder = con.execute("SELECT folder FROM trash WHERE id = ?", (entry,)).fetchone()[0]
    (lib / ".trash" / folder / "Teil.gcode.3mf").rename(lib / "Teil.gcode.3mf")  # von Hand zurückgeschoben
    assert trash.entries() == []


def test_trash_pages_undo_and_purge(client, lib, sample):
    assert "Der Papierkorb ist leer" in client.get("/trash").text
    sample(lib / "Benchy.gcode.3mf", PLATE)
    sample(lib / "Vase.gcode.3mf", PLATE)
    store.scan(wait=True)
    with store.connect() as con:
        pid = _project(con, "Benchy")["id"]

    r = client.post(f"/p/{pid}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/?deleted=")
    page = client.get(r.headers["location"]).text
    assert "„Benchy“ in den Papierkorb verschoben" in page and "Rückgängig" in page
    entry = int(r.headers["location"].split("=")[1])

    listing = client.get("/trash").text
    assert "Benchy" in listing and "Wiederherstellen" in listing and "endgültig weg am" in listing

    r = client.post(f"/trash/{entry}/restore", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/p/")
    assert (lib / "Benchy.gcode.3mf").exists()
    assert "wiederhergestellt" in client.get(r.headers["location"]).text

    with store.connect() as con:
        fids = [r["id"] for r in con.execute("SELECT id FROM files")]
    for fid in fids:
        client.post(f"/f/{fid}/delete")
    with store.connect() as con:
        first = con.execute("SELECT MIN(id) FROM trash").fetchone()[0]
    r = client.post(f"/trash/{first}/delete", follow_redirects=False)
    assert r.headers["location"] == "/trash?purged=1"
    r = client.post("/trash/empty", follow_redirects=False)
    assert r.headers["location"] == "/trash?purged=1"
    assert not list((lib / ".trash").iterdir())
    assert client.post("/trash/999/restore").status_code == 404


def test_legacy_trash_folder_is_renamed(lib, sample):
    """Ein „.papierkorb“ aus älteren Versionen wird zu „.trash“, die Einträge bleiben gültig."""
    sample(lib / "Teil.gcode.3mf", PLATE)
    store.scan(wait=True)
    with store.connect() as con:
        entry = trash.delete(con, [con.execute("SELECT id FROM files").fetchone()[0]], "datei")
    # So hätte es eine ältere Version abgelegt
    (lib / ".trash").rename(lib / ".papierkorb")
    old_only = lib / ".papierkorb" / "2026-01-02_03-04-05" / "Vase"
    old_only.mkdir(parents=True)
    sample(old_only / "Vase.gcode.3mf", PLATE)

    labels = sorted(e["label"] for e in trash.entries())
    assert labels == ["Teil.gcode.3mf", "Vase"]
    assert (lib / ".trash").is_dir() and not (lib / ".papierkorb").exists()
    trash.restore(entry)
    assert (lib / "Teil.gcode.3mf").exists()


def test_legacy_trash_folder_merges_with_existing(lib):
    (lib / ".trash" / "neu").mkdir(parents=True)
    (lib / ".trash" / "neu" / "a.gcode.3mf").write_bytes(b"x")
    (lib / ".papierkorb" / "alt").mkdir(parents=True)
    (lib / ".papierkorb" / "alt" / "b.gcode.3mf").write_bytes(b"y")
    assert trash.trash_root() == lib / ".trash"
    assert sorted(p.name for p in (lib / ".trash").iterdir()) == ["alt", "neu"]
    assert not (lib / ".papierkorb").exists()
