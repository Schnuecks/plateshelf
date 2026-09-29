"""Sicherungen: automatisch, herunterladen, zurückspielen (siehe app/backup.py)."""
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app import backup, store


@pytest.fixture
def client(lib, monkeypatch):
    from app import main
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    monkeypatch.setattr(backup, "KEEP", 3)
    with TestClient(main.app) as c:
        yield c


def project_names():
    with store.connect() as con:
        return sorted(r[0] for r in con.execute("SELECT name FROM projects"))


def one_project(lib, sample, name):
    sample(lib / f"{name}.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)


def test_daily_backups_keep_the_last_ones(lib, monkeypatch):
    monkeypatch.setattr(backup, "KEEP", 3)
    day = 86400
    start = time.mktime((2026, 9, 1, 12, 0, 0, 0, 0, -1))
    for i in range(5):
        path = backup.run_auto(now=start + i * day)
        assert path is not None
        import os
        os.utime(path, (start + i * day, start + i * day))  # Zeitstempel wie am jeweiligen Tag
    assert backup.run_auto(now=start + 4 * day + 3600) is None  # heute schon gesichert
    names = [b["name"] for b in backup.list_backups()]
    assert names == ["plateshelf-2026-09-05.sqlite", "plateshelf-2026-09-04.sqlite", "plateshelf-2026-09-03.sqlite"]
    with sqlite3.connect(backup.backup_dir() / names[0]) as con:
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'projects'").fetchone()[0] == 1


def test_backups_can_be_switched_off(lib, monkeypatch):
    monkeypatch.setattr(backup, "KEEP", 0)
    assert backup.run_auto() is None and backup.list_backups() == []


def test_restore_uploaded_backup(client, lib, sample):
    one_project(lib, sample, "Alt")
    with store.connect() as con:
        con.execute("UPDATE projects SET notes = 'aus der Sicherung'")
    saved = client.get("/settings/backup")
    assert saved.status_code == 200 and saved.content.startswith(b"SQLite format 3")
    # Danach ändert sich einiges …
    with store.connect() as con:
        con.execute("UPDATE projects SET notes = 'neuer Stand'")
    thumbs = store.THUMB_DIR
    assert thumbs.exists()
    # … und die Sicherung wird zurückgespielt
    r = client.post("/settings/backup/restore", files={"file": ("sicherung.sqlite", saved.content)},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/settings?restored=plateshelf-")
    with store.connect() as con:
        assert con.execute("SELECT notes FROM projects").fetchone()[0] == "aus der Sicherung"
        assert con.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    # Der vorherige Stand liegt als Notfall-Kopie bereit, die Vorschaubilder sind neu erzeugt
    (safety,) = [b for b in backup.list_backups() if b["before_restore"]]
    with sqlite3.connect(backup.backup_dir() / safety["name"]) as con:
        assert con.execute("SELECT notes FROM projects").fetchone()[0] == "neuer Stand"
    assert any(thumbs.iterdir())
    page = client.get(r.headers["location"]).text
    assert "Sicherung zurückgespielt" in page and safety["name"] in page


def test_restore_rejects_other_files(client, lib, tmp_path):
    r = client.post("/settings/backup/restore", files={"file": ("x.sqlite", b"kein SQLite")})
    assert r.status_code == 400 and "keine Sicherung von PlateShelf" in r.text
    other = tmp_path / "fremd.sqlite"
    with sqlite3.connect(other) as con:
        con.execute("CREATE TABLE irgendwas (x)")
    r = client.post("/settings/backup/restore", files={"file": ("fremd.sqlite", other.read_bytes())})
    assert r.status_code == 400 and "keine Sicherung von PlateShelf" in r.text
    newer = tmp_path / "neuer.sqlite"
    backup.write(newer)
    with sqlite3.connect(newer) as con:
        con.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + 1}")
    r = client.post("/settings/backup/restore", files={"file": ("neuer.sqlite", newer.read_bytes())})
    assert r.status_code == 400 and "neueren Version" in r.text
    assert backup.list_backups() == []  # nichts angefasst


def test_automatic_backups_in_settings(client, lib, sample):
    one_project(lib, sample, "Teil")
    backup.run_auto()
    (b,) = backup.list_backups()
    page = client.get("/settings").text
    assert f'/settings/backup/file/{b["name"]}' in page and "Automatische Sicherungen" in page
    r = client.get(f"/settings/backup/file/{b['name']}")
    assert r.status_code == 200 and r.content.startswith(b"SQLite format 3")
    # Nur Dateien aus dem Sicherungsordner
    assert client.get("/settings/backup/file/..%2Fplateshelf.sqlite").status_code == 404
    assert client.get("/settings/backup/file/plateshelf.sqlite").status_code == 404
    with store.connect() as con:
        con.execute("UPDATE projects SET name = 'Umbenannt'")
    assert project_names() == ["Umbenannt"]
    r = client.post(f"/settings/backup/file/{b['name']}/restore", follow_redirects=False)
    assert r.status_code == 303 and project_names() == ["Teil"]
    assert client.get("/settings/backup/restore", follow_redirects=False).headers["location"] == "/settings#backup"
