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
        con.execute("DELETE FROM plates"); con.execute("DELETE FROM files")
    (lib / "a.gcode.3mf").rename(lib / "b.gcode.3mf")
    store.scan(wait=True)
    with store.connect() as con:
        assert con.execute("SELECT id FROM files").fetchone()[0] > first


def test_broken_file_is_recorded(lib):
    (lib / "kaputt.gcode.3mf").write_text("kein zip")
    assert store.scan(wait=True)["errors"] == 1


def test_database_from_druckarchiv_is_taken_over(lib):
    """Beim Update von Druckarchiv auf PlateShelf wird die alte Datenbank weiterverwendet."""
    from app import store
    import gc
    con = store.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('strompreis', '0.5')")
    con.commit()
    con.close()
    gc.collect()  # Windows: offene Verbindungen früherer Aufrufe freigeben, sonst ist die Datei gesperrt
    old = store.DB_PATH.with_name(store.LEGACY_DB_NAME)
    store.DB_PATH.replace(old)
    for suffix in ("-wal", "-shm"):
        store.DB_PATH.with_name(store.DB_PATH.name + suffix).unlink(missing_ok=True)
    store.init_db()
    assert store.DB_PATH.exists() and not old.exists()
    assert store.get_settings()["strompreis"] == 0.5
