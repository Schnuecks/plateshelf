# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""SQLite-Speicher und Ordner-Scanner.

Die 3MF- und G-Code-Dateien bleiben unangetastet in der Bibliothek. In der Datenbank
landen nur ausgelesene Metadaten, Vorschaubilder und deine eigenen Notizen.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .gcode_file import is_gcode, parse_gcode
from .parser import parse_3mf

log = logging.getLogger("plateshelf")

LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", "/library"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "plateshelf.sqlite"
THUMB_DIR = DATA_DIR / "thumbs"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_key TEXT UNIQUE,
    name TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    project_id INTEGER REFERENCES projects(id),
    pinned INTEGER NOT NULL DEFAULT 0,
    mtime REAL, size INTEGER,
    sliced INTEGER NOT NULL DEFAULT 0,
    slicer TEXT, printer TEXT, nozzle TEXT, layer_height TEXT,
    bed_type TEXT, process TEXT,
    settings_json TEXT,
    model_json TEXT,
    error TEXT,
    missing INTEGER NOT NULL DEFAULT 0,
    added REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plates (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    name TEXT,
    prediction_s INTEGER, weight_g REAL, layers INTEGER, max_z REAL,
    cost REAL, support_used INTEGER,
    filaments_json TEXT, objects_json TEXT, warnings_json TEXT,
    has_thumb INTEGER NOT NULL DEFAULT 0,
    has_top INTEGER NOT NULL DEFAULT 0,
    sliced INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trash (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT UNIQUE NOT NULL,   -- Unterordner in .trash
    kind TEXT NOT NULL,            -- datei, projekt oder alt (vor Version 0.3 gelöscht)
    label TEXT NOT NULL,
    project_json TEXT,             -- Projektangaben zum Wiederherstellen
    files_json TEXT NOT NULL,      -- [{"path": …, "pinned": 0/1}]
    deleted REAL NOT NULL,
    since REAL NOT NULL            -- Beginn der Aufbewahrungsfrist
);
-- Druckverlauf, siehe print_history.py
CREATE TABLE IF NOT EXISTS print_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    printer TEXT NOT NULL,          -- Kennung wie in printer_files
    printer_name TEXT,
    job_key TEXT NOT NULL,          -- Kennung des Drucks auf dem Drucker
    remote_name TEXT,               -- Datei auf dem Drucker
    local_path TEXT,                -- zugeordnete Datei in der Bibliothek
    status TEXT NOT NULL,           -- running, completed, cancelled, failed
    started REAL,
    ended REAL,
    duration_s REAL,                -- reine Druckzeit, sofern der Drucker sie meldet
    filament_mm REAL,
    progress REAL,
    applied INTEGER NOT NULL DEFAULT 0,  -- Projektstatus schon auf „gedruckt“ gesetzt
    hidden INTEGER NOT NULL DEFAULT 0,   -- vom Nutzer aus dem Verlauf entfernt
    remaining_s REAL,               -- Restzeit laut Drucker, solange er druckt
    updated REAL,                   -- letzte Meldung zu diesem Druck
    UNIQUE (printer, job_key)
);
CREATE INDEX IF NOT EXISTS idx_jobs_path ON print_jobs(local_path);
-- Direktimport, siehe printer_import.py
CREATE TABLE IF NOT EXISTS printer_imports (
    local_path TEXT PRIMARY KEY,
    printer_name TEXT,
    remote_name TEXT,
    remote_date TEXT
);
CREATE TABLE IF NOT EXISTS printers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    serial TEXT UNIQUE NOT NULL,
    ip TEXT NOT NULL,
    code TEXT NOT NULL,            -- Zugangscode (Bambu) oder API-Schlüssel (Moonraker)
    kind TEXT NOT NULL DEFAULT 'bambu'
);
CREATE TABLE IF NOT EXISTS printer_files (
    printer TEXT NOT NULL,
    remote_name TEXT NOT NULL,
    size INTEGER NOT NULL,
    remote_date TEXT NOT NULL,
    local_path TEXT,
    printer_name TEXT,
    imported REAL NOT NULL,
    PRIMARY KEY (printer, remote_name)
);
CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);
CREATE INDEX IF NOT EXISTS idx_plates_file ON plates(file_id);
"""

_scan_lock = threading.Lock()
last_scan: dict = {"finished": None, "added": 0, "updated": 0, "errors": 0, "running": False}


def db_signature() -> tuple:
    """Ändert sich mit jedem Schreibzugriff (Datenbank und WAL-Datei), billig abzufragen."""
    sig = [str(DB_PATH)]
    for path in (DB_PATH, DB_PATH.with_name(DB_PATH.name + "-wal")):
        try:
            st = path.stat()
            sig += [st.st_mtime_ns, st.st_size]
        except OSError:
            sig += [0, 0]
    return tuple(sig)


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


_force_next_scan = False


def _ensure_autoincrement(con: sqlite3.Connection, table: str) -> None:
    """Ältere Datenbanken: IDs gelöschter Einträge wurden neu vergeben.

    Mit AUTOINCREMENT bekommt eine neue Datei nie die ID einer gelöschten, damit
    zeigt der Browser auch nie ein zwischengespeichertes Bild der alten Datei.
    """
    row = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    if not row or "AUTOINCREMENT" in row["sql"].upper():
        return
    cols = [r["name"] for r in con.execute(f"PRAGMA table_info({table})")]
    new_sql = row["sql"].replace("id INTEGER PRIMARY KEY,", "id INTEGER PRIMARY KEY AUTOINCREMENT,", 1)
    new_sql = new_sql.replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}_neu", 1)
    col_list = ", ".join(cols)
    con.commit()
    con.executescript(f"""
        PRAGMA foreign_keys = OFF;
        BEGIN;
        {new_sql};
        INSERT INTO {table}_neu ({col_list}) SELECT {col_list} FROM {table};
        DROP TABLE {table};
        ALTER TABLE {table}_neu RENAME TO {table};
        COMMIT;
        PRAGMA foreign_keys = ON;
    """)
    log.info("Tabelle %s auf fortlaufende IDs umgestellt", table)


SCHEMA_VERSION = 1  # 1 = Stand von 0.7.9; spätere Änderungen als Schritte in MIGRATIONS
# Beispiel für später: MIGRATIONS = {2: lambda con: con.execute("ALTER TABLE files ADD COLUMN …")}
MIGRATIONS: dict = {}


def _upgrade_before_versioning(con: sqlite3.Connection) -> None:
    """Datenbanken aus 0.7.x (noch ohne Versionsnummer) auf Schema-Version 1 bringen.

    SCHEMA hat vorher alle fehlenden Tabellen angelegt; hier kommen die Spalten dazu,
    die in 0.2 bis 0.7 nachträglich eingeführt wurden.
    """
    global _force_next_scan
    added = {
        "plates": [("sliced", "INTEGER NOT NULL DEFAULT 1", True)],
        "files": [("model_json", "TEXT", True)],
        "printers": [("kind", "TEXT NOT NULL DEFAULT 'bambu'", False)],
        "print_jobs": [("hidden", "INTEGER NOT NULL DEFAULT 0", False), ("remaining_s", "REAL", False),
                       ("updated", "REAL", False)],
    }
    for table, cols in added.items():
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, decl, rescan in cols:
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                _force_next_scan |= rescan  # neue Angaben aus den Dateien: alles neu einlesen
    for table in ("projects", "files"):
        _ensure_autoincrement(con, table)


def init_db() -> None:
    """Datenbank anlegen oder auf den aktuellen Stand bringen (PRAGMA user_version)."""
    global _force_next_scan
    with connect() as con:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        existed = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projects'").fetchone()
        con.executescript(SCHEMA)
        if existed and version == 0:
            _upgrade_before_versioning(con)
            con.executescript(SCHEMA)  # Indizes nach dem Umbau wiederherstellen
            version = 1
        for step in sorted(v for v in MIGRATIONS if v > version):
            MIGRATIONS[step](con)
            log.info("Datenbank auf Schema-Version %d aktualisiert", step)
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        # Nach dem Wiederherstellen einer Sicherung fehlen die Vorschaubilder: neu einlesen
        if not THUMB_DIR.exists() and con.execute("SELECT 1 FROM files LIMIT 1").fetchone():
            _force_next_scan = True


LIBRARY_EXTENSIONS = (".3mf", ".gcode", ".bgcode")


def is_library_file(name: str) -> bool:
    return name.lower().endswith(LIBRARY_EXTENSIONS) and not name.startswith(".")


def parse_file(path: Path):
    return parse_gcode(path) if is_gcode(path.name) else parse_3mf(path)


# --- Gruppierung ------------------------------------------------------------

_STEM_SUFFIX = re.compile(r"([_\- ]?(plate|platte)[_\- ]?\d+|[_\- ]?\(\d+\)|[_\- ]v\d+|[_\-. ]gcode|[_\-. ](3mf|stl|step|stp|obj))$", re.I)
_MEANINGLESS = re.compile(r"\(?unsaved\)?|untitled|unbenannt|[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
# Ordner, deren Dateien über den Namen gruppiert werden statt als ein Projekt
FLAT_DIRS = {"from printer"} | {os.environ.get("PRINTER_IMPORT_DIR", "").strip().lower()} - {""}


def group_key_for(rel: Path) -> tuple[str, str]:
    """Liefert (Schlüssel, Anzeigename) für die automatische Projektzuordnung.

    Dateien in Unterordnern gehören zum Ordner, Dateien im Wurzelverzeichnis
    werden über ihren Namen ohne '_plate_3', ' (2)' oder '_v2' gruppiert.
    """
    if len(rel.parts) > 1 and rel.parts[0].lower() not in FLAT_DIRS:
        folder = rel.parent
        return f"dir:{folder.as_posix().lower()}", folder.name
    stem = rel.name
    for ext in (".gcode.3mf", "_gcode.3mf", "-gcode.3mf", ".3mf", ".bgcode", ".gcode"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    prev = None
    while prev != stem:
        prev, stem = stem, _STEM_SUFFIX.sub("", stem).strip()
    pretty = re.sub(r"[_]+", " ", stem).strip() or rel.name
    return f"name:{pretty.lower()}", pretty


def _project_for(con: sqlite3.Connection, key: str, name: str) -> int:
    row = con.execute("SELECT id FROM projects WHERE group_key = ?", (key,)).fetchone()
    if row:
        return row["id"]
    now = time.time()
    cur = con.execute(
        "INSERT INTO projects (group_key, name, created, updated) VALUES (?, ?, ?, ?)",
        (key, name, now, now),
    )
    return cur.lastrowid


# --- Import einer Datei -----------------------------------------------------

def _store_file(con: sqlite3.Connection, abs_path: Path, rel: Path, st: os.stat_result) -> None:
    rel_s = rel.as_posix()
    existing = con.execute("SELECT id, project_id, pinned FROM files WHERE path = ?", (rel_s,)).fetchone()
    try:
        parsed = parse_file(abs_path)
        error = None
    except Exception as exc:  # defekte oder fremde Datei
        log.warning("Kann %s nicht lesen: %s", rel_s, exc)
        parsed, error = None, str(exc)

    if existing and existing["pinned"]:
        project_id = existing["project_id"]
    else:
        key, name = group_key_for(rel)
        # Nichtssagende Dateinamen: Titel aus der 3MF verwenden, falls vorhanden
        if _MEANINGLESS.fullmatch(name) and parsed and parsed.model.get("title"):
            name = parsed.model["title"]
        project_id = _project_for(con, key, name)

    values = dict(
        path=rel_s, project_id=project_id, mtime=st.st_mtime, size=st.st_size,
        sliced=int(bool(parsed and parsed.sliced)),
        slicer=parsed.slicer if parsed else None,
        printer=parsed.printer if parsed else None,
        nozzle=parsed.nozzle if parsed else None,
        layer_height=parsed.layer_height if parsed else None,
        bed_type=parsed.bed_type if parsed else None,
        process=parsed.process if parsed else None,
        settings_json=json.dumps(parsed.settings) if parsed else None,
        model_json=json.dumps({**parsed.model, "photos": [f"photo_{i}{ext}" for i, (ext, _) in enumerate(parsed.photos)]})
        if parsed else None,
        error=error, missing=0,
    )
    if existing:
        file_id = existing["id"]
        sets = ", ".join(f"{k} = :{k}" for k in values)
        con.execute(f"UPDATE files SET {sets} WHERE id = :id", {**values, "id": file_id})
        con.execute("DELETE FROM plates WHERE file_id = ?", (file_id,))
    else:
        cols = ", ".join(values) + ", added"
        params = ", ".join(f":{k}" for k in values) + ", :added"
        file_id = con.execute(
            f"INSERT INTO files ({cols}) VALUES ({params})", {**values, "added": time.time()}
        ).lastrowid

    preview_dir = DATA_DIR / "preview"
    if preview_dir.exists():  # alte 3D-Vorschauen dieser Datei verwerfen
        for old in preview_dir.glob(f"{file_id}_*"):
            old.unlink()
    from .gcode_preview import forget
    forget(preview_dir, file_id)
    thumb_dir = THUMB_DIR / str(file_id)
    if thumb_dir.exists():
        for old in thumb_dir.iterdir():
            old.unlink()
    if not parsed:
        return
    thumb_dir.mkdir(parents=True, exist_ok=True)
    for i, (ext, data) in enumerate(parsed.photos):
        (thumb_dir / f"photo_{i}{ext}").write_bytes(data)
    for p in parsed.plates:
        if p.thumb:
            (thumb_dir / f"plate_{p.index}.png").write_bytes(p.thumb)
        if p.top:
            (thumb_dir / f"top_{p.index}.png").write_bytes(p.top)
        con.execute(
            """INSERT INTO plates (file_id, idx, name, prediction_s, weight_g, layers, max_z,
                   cost, support_used, filaments_json, objects_json, warnings_json, has_thumb, has_top, sliced)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                file_id, p.index, p.name, p.prediction_s, p.weight_g, p.layers, p.max_z,
                p.cost, None if p.support_used is None else int(p.support_used),
                json.dumps([asdict(f) for f in p.filaments]),
                json.dumps(p.objects), json.dumps(p.warnings),
                int(bool(p.thumb)), int(bool(p.top)),
                int(p.sliced or not parsed.sliced),
            ),
        )


def scan(force: bool = False, wait: bool = False) -> dict:
    if not _scan_lock.acquire(blocking=wait):
        return last_scan
    last_scan["running"] = True
    added = updated = errors = 0
    try:
        with connect() as con:
            known = {r["path"]: r for r in con.execute("SELECT path, mtime, size, error FROM files")}
            seen: set[str] = set()
            for root, dirs, files in os.walk(LIBRARY_DIR):
                dirs[:] = [d for d in dirs if not d.startswith((".", "@"))]  # @eaDir etc. vom NAS
                for fn in files:
                    if not is_library_file(fn):
                        continue
                    abs_path = Path(root) / fn
                    rel = abs_path.relative_to(LIBRARY_DIR)
                    rel_s = rel.as_posix()
                    seen.add(rel_s)
                    try:
                        st = abs_path.stat()
                    except OSError:
                        continue
                    prev = known.get(rel_s)
                    if prev and not force and prev["mtime"] == st.st_mtime and prev["size"] == st.st_size:
                        continue
                    _store_file(con, abs_path, rel, st)
                    con.commit()
                    added += prev is None
                    updated += prev is not None
            for path in set(known) - seen:
                con.execute("UPDATE files SET missing = 1 WHERE path = ?", (path,))
            # Leere Projekte ohne eigene Angaben aufräumen
            con.execute(
                """DELETE FROM projects WHERE id NOT IN (SELECT DISTINCT project_id FROM files
                   WHERE project_id IS NOT NULL AND missing = 0)
                   AND notes = '' AND tags = '' AND source_url = '' AND status = ''"""
            )
            errors = con.execute("SELECT COUNT(*) FROM files WHERE error IS NOT NULL AND missing = 0").fetchone()[0]
    finally:
        last_scan.update(finished=time.time(), added=added, updated=updated, errors=errors, running=False)
        _scan_lock.release()
    if added or updated:
        log.info("Scan: %d neu, %d aktualisiert, %d Fehler", added, updated, errors)
    if PREBUILD:
        request_prebuild()
    return last_scan


SETTING_DEFAULTS = {
    "strompreis": 0.35,        # Euro pro kWh
    "leistung_standard": 100,  # durchschnittliche Leistung eines Druckers in Watt
}


def get_settings(con: sqlite3.Connection | None = None) -> dict:
    """Einstellungen mit Standardwerten; Druckerleistungen als "leistung:<Drucker>"."""
    own = con is None
    con = con or connect()
    try:
        values = dict(SETTING_DEFAULTS)
        for r in con.execute("SELECT key, value FROM settings"):
            try:
                values[r["key"]] = float(r["value"])
            except ValueError:
                values[r["key"]] = r["value"]
        return values
    finally:
        if own:
            con.close()


def save_settings(values: dict) -> None:
    with connect() as con:
        for key, value in values.items():
            if value is None or value == "":
                con.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


def printer_watts(settings: dict, printer: str | None) -> float:
    return float(settings.get(f"leistung:{printer}", settings["leistung_standard"]) if printer
                 else settings["leistung_standard"])


def unique_path(folder: Path, name: str) -> Path:
    """Nie überschreiben: bei Namensgleichheit ' (2)', ' (3)' … anhängen."""
    target = folder / name
    if not target.exists():
        return target
    ext = next((e for e in (".gcode.3mf", ".3mf", ".bgcode", ".gcode") if name.lower().endswith(e)), Path(name).suffix)
    stem, ext = name[:len(name) - len(ext)], name[len(name) - len(ext):]
    n = 2
    while (folder / f"{stem} ({n}){ext}").exists():
        n += 1
    return folder / f"{stem} ({n}){ext}"


def project_ids_for(paths: list[str]) -> list[int]:
    with connect() as con:
        ids = []
        for path in paths:
            row = con.execute("SELECT project_id FROM files WHERE path = ?", (path,)).fetchone()
            if row and row["project_id"] not in ids:
                ids.append(row["project_id"])
        return ids


PREBUILD = os.environ.get("PREVIEW_PREBUILD", "true").lower() not in ("0", "false", "no")
_prebuild_wakeup = threading.Event()


def request_prebuild() -> None:
    _prebuild_wakeup.set()


def prebuild_loop() -> None:
    """Erstellt fehlende 3D-Vorschauen nacheinander im Hintergrund."""
    from .gcode_preview import status as preview_status
    while True:
        _prebuild_wakeup.wait()
        _prebuild_wakeup.clear()
        try:
            with connect() as con:
                rows = con.execute(
                    """SELECT f.id, f.path, p.idx FROM plates p JOIN files f ON f.id = p.file_id
                       WHERE p.sliced = 1 AND f.missing = 0 AND f.sliced = 1
                       ORDER BY f.added DESC"""
                ).fetchall()
            for r in rows:
                path = LIBRARY_DIR / r["path"]
                if path.exists():
                    preview_status(path, r["idx"], DATA_DIR / "preview" / f"{r['id']}_{r['idx']}", background=False)
        except Exception:
            log.exception("Vorberechnung der 3D-Vorschau fehlgeschlagen")


def scan_loop(interval: int) -> None:
    """Polling statt inotify, damit es auch auf SMB/NFS-Freigaben zuverlässig läuft."""
    global _force_next_scan
    while True:
        try:
            force, _force_next_scan = _force_next_scan, False
            scan(force=force)
        except Exception:
            log.exception("Scan fehlgeschlagen")
        time.sleep(interval)
