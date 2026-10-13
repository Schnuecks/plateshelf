from app import store


def _projects(con):
    return {r["path"]: r["name"] for r in con.execute(
        "SELECT f.path, p.name FROM files f JOIN projects p ON p.id = f.project_id WHERE f.missing = 0")}


def test_scan_groups_and_detects_changes(lib, sample):
    for i in (1, 2):
        sample(lib / f"Benchy_plate_{i}.gcode.3mf", [{"idx": i, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    res = store.scan(wait=True)
    assert res["added"] == 2
    with store.connect() as con:
        assert set(_projects(con).values()) == {"Benchy"}
    assert store.scan(wait=True)["added"] == 0  # unchanged: nothing is re-read
    (lib / "Benchy_plate_2.gcode.3mf").unlink()
    store.scan(wait=True)
    with store.connect() as con:
        assert list(_projects(con)) == ["Benchy_plate_1.gcode.3mf"]


def test_ids_are_never_reused(lib, sample):
    sample(lib / "a.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 1, "fil": [(1, "#000000FF", 1.0)]}])
    store.scan(wait=True)
    with store.connect() as con:
        first = con.execute("SELECT id FROM files").fetchone()[0]
        con.execute("DELETE FROM plates")
        con.execute("DELETE FROM files")
    (lib / "a.gcode.3mf").rename(lib / "b.gcode.3mf")
    store.scan(wait=True)
    with store.connect() as con:
        assert con.execute("SELECT id FROM files").fetchone()[0] > first


def test_broken_file_is_recorded(lib):
    (lib / "kaputt.gcode.3mf").write_text("kein zip")
    assert store.scan(wait=True)["errors"] == 1


def test_schema_version(lib):
    with store.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION


def test_database_without_version_is_upgraded(lib, monkeypatch):
    """Database from 0.7.x: missing columns are added, then everything is re-read once."""
    with store.connect() as con:
        con.execute("ALTER TABLE files DROP COLUMN model_json")
        con.execute("ALTER TABLE print_jobs DROP COLUMN remaining_s")
        con.execute("PRAGMA user_version = 0")
    monkeypatch.setattr(store, "_force_next_scan", False)
    store.init_db()
    with store.connect() as con:
        assert "model_json" in {r["name"] for r in con.execute("PRAGMA table_info(files)")}
        assert "remaining_s" in {r["name"] for r in con.execute("PRAGMA table_info(print_jobs)")}
        assert con.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    assert store._force_next_scan


def test_missing_thumbnails_trigger_full_scan(lib, sample, monkeypatch):
    """After restoring a backup the thumbnails are missing: re-read everything."""
    import shutil
    sample(lib / "Teil.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    shutil.rmtree(store.THUMB_DIR)
    monkeypatch.setattr(store, "_force_next_scan", False)
    store.init_db()
    assert store._force_next_scan


def test_old_printer_folder_is_grouped_by_name(lib, sample, monkeypatch):
    """„Vom Drucker“ (import folder up to 0.6): one project per print, not one for the whole folder.

    0.7.8 to 0.8.8 no longer knew the folder; as soon as files in it were re-read,
    they all collapsed into one project. Schema 6 therefore re-reads everything once.
    """
    folder = lib / "Vom Drucker" / "P2S"
    folder.mkdir(parents=True)
    for name in ("Grim_Reaper_plate_1", "Grim_Reaper_plate_2", "Garfield", "Easter_bunny_set"):
        sample(folder / f"{name}.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    expected = {"Grim Reaper", "Garfield", "Easter bunny set"}

    with monkeypatch.context() as m:
        m.setattr(store, "FLAT_DIRS", {"from printer"})  # as of 0.8.8
        store.scan(force=True, wait=True)
    with store.connect() as con:
        assert set(_projects(con).values()) == {"P2S"}
        collapsed = con.execute("SELECT id FROM projects WHERE name = 'P2S'").fetchone()[0]
        con.execute("PRAGMA user_version = 5")

    monkeypatch.setattr(store, "_force_next_scan", False)
    store.init_db()
    assert store._force_next_scan
    store.scan(force=True, wait=True)
    with store.connect() as con:
        assert set(_projects(con).values()) == expected
        assert not con.execute("SELECT 1 FROM projects WHERE id = ?", (collapsed,)).fetchone()
    # Files changed later stay in their project
    import os
    os.utime(folder / "Garfield.gcode.3mf", (1_700_000_000, 1_700_000_000))
    store.scan(wait=True)
    with store.connect() as con:
        assert set(_projects(con).values()) == expected


def test_project_with_own_cover_photo_is_kept(lib, sample):
    sample(lib / "Vase.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    with store.connect() as con:
        con.execute("UPDATE projects SET cover_photo = 'photo_1.jpg'")
    (lib / "Vase.gcode.3mf").unlink()
    store.scan(wait=True)
    with store.connect() as con:
        assert con.execute("SELECT cover_photo FROM projects").fetchone()[0] == "photo_1.jpg"


def test_project_whose_files_all_vanished(lib, sample):
    """All files deleted outside PlateShelf: project disappears, scanning continues."""
    sample(lib / "Weg.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    data = (lib / "Weg.gcode.3mf").read_bytes()
    (lib / "Weg.gcode.3mf").unlink()
    store.scan(wait=True)
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    (lib / "Weg.gcode.3mf").write_bytes(data)  # comes back, e.g. share reachable again
    store.scan(wait=True)
    with store.connect() as con:
        assert set(_projects(con).values()) == {"Weg"}
