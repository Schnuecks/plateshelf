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
    assert store.scan(wait=True)["added"] == 0  # unverändert: nichts neu einlesen
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
    """Datenbank aus 0.7.x: fehlende Spalten kommen dazu, danach wird einmal alles neu eingelesen."""
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
    """Nach dem Zurückspielen einer Sicherung fehlen die Vorschaubilder: alles neu einlesen."""
    import shutil
    sample(lib / "Teil.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    shutil.rmtree(store.THUMB_DIR)
    monkeypatch.setattr(store, "_force_next_scan", False)
    store.init_db()
    assert store._force_next_scan


def test_old_printer_folder_is_grouped_by_name(lib, sample, monkeypatch):
    """„Vom Drucker“ (Importordner bis 0.6): ein Projekt je Druck, nicht eines für den ganzen Ordner.

    0.7.8 bis 0.8.8 kannten den Ordner nicht mehr; sobald Dateien darin neu eingelesen wurden,
    fielen alle zu einem Projekt zusammen. Schema 6 liest deshalb einmal alles neu ein.
    """
    folder = lib / "Vom Drucker" / "P2S"
    folder.mkdir(parents=True)
    for name in ("Grim_Reaper_plate_1", "Grim_Reaper_plate_2", "Garfield", "Easter_bunny_set"):
        sample(folder / f"{name}.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    expected = {"Grim Reaper", "Garfield", "Easter bunny set"}

    with monkeypatch.context() as m:
        m.setattr(store, "FLAT_DIRS", {"from printer"})  # Stand 0.8.8
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
    # Später geänderte Dateien bleiben in ihrem Projekt
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
