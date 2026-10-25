# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""SQLite storage and folder scanner.

The 3MF and G-code files stay untouched in the library. The database only
holds extracted metadata, preview images and your own notes.
"""
from __future__ import annotations

import hashlib
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
    updated REAL NOT NULL,
    cover_photo TEXT                -- eigenes Foto als Titelbild, siehe photos.py
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
    added REAL NOT NULL,
    sha256 TEXT                     -- Inhalt, zum Erkennen doppelter Dateien
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
    plate INTEGER,                  -- gedruckte Platte, wenn der Drucker sie statt des Dateinamens meldet
    applied INTEGER NOT NULL DEFAULT 0,  -- Projektstatus schon auf „gedruckt“ gesetzt
    hidden INTEGER NOT NULL DEFAULT 0,   -- vom Nutzer aus dem Verlauf entfernt
    remaining_s REAL,               -- Restzeit laut Drucker, solange er druckt
    updated REAL,                   -- letzte Meldung zu diesem Druck
    note TEXT,                      -- Notiz zu diesem Druck
    rating INTEGER,                 -- 1 gelungen, -1 misslungen
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
-- Anmeldung, siehe auth.py
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',   -- admin, user oder viewer
    created REAL NOT NULL,
    last_login REAL,
    oidc_sub TEXT                       -- Aussteller + sub bei Anmeldung über OIDC, siehe oidc.py
);
CREATE TABLE IF NOT EXISTS auth_events (   -- Protokoll: Anmeldungen, Konten, Tokens, Passkeys
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    kind TEXT NOT NULL,
    user_name TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_auth_events_at ON auth_events(at);
CREATE TABLE IF NOT EXISTS passkeys (      -- Passkeys (WebAuthn) zum Anmelden ohne Passwort
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credential_id TEXT NOT NULL UNIQUE,
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    created REAL NOT NULL,
    last_used REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,     -- SHA-256 des Cookie-Werts, nie der Wert selbst
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created REAL NOT NULL,
    last_seen REAL NOT NULL,
    expires REAL NOT NULL,
    agent TEXT,                          -- Browser, zum Wiedererkennen in der Geräteliste
    address TEXT
);
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,     -- SHA-256 des Tokens
    hint TEXT NOT NULL,                  -- letzte vier Zeichen zum Wiedererkennen
    created REAL NOT NULL,
    created_by TEXT,
    last_used REAL
);
CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);
CREATE INDEX IF NOT EXISTS idx_plates_file ON plates(file_id);
"""

_scan_lock = threading.Lock()
last_scan: dict = {"finished": None, "added": 0, "updated": 0, "errors": 0, "running": False}


def db_signature() -> tuple:
    """Changes with every write (database and WAL file), cheap to query."""
    sig = [str(DB_PATH)]
    for path in (DB_PATH, DB_PATH.with_name(DB_PATH.name + "-wal")):
        try:
            st = path.stat()
            sig += [st.st_mtime_ns, st.st_size]
        except OSError:
            sig += [0, 0]
    return tuple(sig)


def library_version() -> str:
    """State of the overview: changes on new, modified or removed files, on projects,
    when a print starts, ends or is assigned, and when new preview images are drawn –
    but not on sessions or the progress of running prints."""
    with connect() as con:
        f = con.execute("SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(MAX(mtime), 0), COALESCE(SUM(size), 0) "
                        "FROM files WHERE missing = 0").fetchone()
        p = con.execute("SELECT COUNT(*), COALESCE(MAX(updated), 0) FROM projects").fetchone()
        j = con.execute("SELECT COUNT(*), COALESCE(SUM(status = 'running'), 0), COALESCE(SUM(status = 'completed'), 0), "
                        "COUNT(local_path), COALESCE(MAX(ended), 0) FROM print_jobs WHERE hidden = 0").fetchone()
        t = con.execute("SELECT GROUP_CONCAT(value, '/') FROM settings "
                        "WHERE key IN ('vorschaubilder', 'vorschaubilder_stand')").fetchone()
    return (f"{f[0]}-{f[1]}-{f[2]:.0f}-{f[3]}-{p[0]}-{p[1]:.0f}"
            f"-{j[0]}.{j[1]}.{j[2]}.{j[3]}.{j[4]:.0f}-{t[0] or ''}")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


_force_next_scan = False


def _ensure_autoincrement(con: sqlite3.Connection, table: str) -> None:
    """Older databases: IDs of deleted entries were reassigned.

    With AUTOINCREMENT a new file never gets the ID of a deleted one, so the
    browser never shows a cached image of the old file either.
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


SCHEMA_VERSION = 7  # 1 = state of 0.7.9; later changes as steps in MIGRATIONS


def _add_column(table: str, column: str, decl: str):
    def step(con: sqlite3.Connection) -> None:
        if column not in {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return step


MIGRATIONS: dict = {
    2: _add_column("print_jobs", "plate", "INTEGER"),  # 0.7.12: Bambu sometimes reports only the plate
    3: lambda con: None,  # User accounts: tables users, sessions, api_tokens (created by SCHEMA)
    4: lambda con: _migrate_4(con),
    5: _add_column("users", "oidc_sub", "TEXT"),  # 0.8.8: login via OIDC
    6: lambda con: _rescan_all(),  # 0.8.9: group „Vom Drucker“ by name again
    7: lambda con: None,  # 0.8.17: tables auth_events and passkeys (created by SCHEMA)
}


def _rescan_all() -> None:
    global _force_next_scan
    _force_next_scan = True


def _migrate_4(con: sqlite3.Connection) -> None:
    """Photos as cover image, note and rating per print, hash against duplicate files."""
    for table, column, decl in (("projects", "cover_photo", "TEXT"), ("print_jobs", "note", "TEXT"),
                                ("print_jobs", "rating", "INTEGER"), ("files", "sha256", "TEXT")):
        _add_column(table, column, decl)(con)


def _upgrade_before_versioning(con: sqlite3.Connection) -> None:
    """Bring databases from 0.7.x (still without a version number) to schema version 1.

    SCHEMA has already created all missing tables; this adds the columns
    that were introduced later in 0.2 to 0.7.
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
                _force_next_scan |= rescan  # new data from the files: re-read everything
    for table in ("projects", "files"):
        _ensure_autoincrement(con, table)


def init_db() -> None:
    """Create the database or bring it up to date (PRAGMA user_version)."""
    global _force_next_scan
    with connect() as con:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        existed = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projects'").fetchone()
        con.executescript(SCHEMA)
        if not existed:
            version = SCHEMA_VERSION  # newly created: already up to date
        if existed and version == 0:
            _upgrade_before_versioning(con)
            con.executescript(SCHEMA)  # restore indexes after the rebuild
            version = 1
        for step in sorted(v for v in MIGRATIONS if v > version):
            MIGRATIONS[step](con)
            log.info("Datenbank auf Schema-Version %d aktualisiert", step)
        # Indexes on columns from migrations only afterwards, otherwise the column is missing in old databases
        con.execute("CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256)")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_oidc ON users(oidc_sub)")
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        # After restoring a backup the preview images are missing: re-read
        if not THUMB_DIR.exists() and con.execute("SELECT 1 FROM files LIMIT 1").fetchone():
            _force_next_scan = True


LIBRARY_EXTENSIONS = (".3mf", ".gcode", ".bgcode")


def is_library_file(name: str) -> bool:
    return name.lower().endswith(LIBRARY_EXTENSIONS) and not name.startswith(".")


def parse_file(path: Path):
    return parse_gcode(path) if is_gcode(path.name) else parse_3mf(path)


# --- Grouping ---------------------------------------------------------------

_STEM_SUFFIX = re.compile(r"([_\- ]?(plate|platte)[_\- ]?\d+|[_\- ]?\(\d+\)|[_\- ]v\d+|[_\-. ]gcode|[_\-. ](3mf|stl|step|stp|obj))$", re.I)
_MEANINGLESS = re.compile(r"\(?unsaved\)?|untitled|unbenannt|[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
# Folders whose files are grouped by name instead of as one project. „Vom Drucker“
# was the name of the import folder up to 0.6 (Druckarchiv); older installations still keep many
# prints there, which would otherwise collapse into a single project on the next scan.
FLAT_DIRS = {"from printer", "vom drucker"} | {os.environ.get("PRINTER_IMPORT_DIR", "").strip().lower()} - {""}


def group_key_for(rel: Path) -> tuple[str, str]:
    """Returns (key, display name) for the automatic project assignment.

    Files in subfolders belong to the folder, files in the root directory
    are grouped by their name without '_plate_3', ' (2)' or '_v2'.
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


# --- Import of a single file ------------------------------------------------

def _store_file(con: sqlite3.Connection, abs_path: Path, rel: Path, st: os.stat_result) -> None:
    rel_s = rel.as_posix()
    existing = con.execute("SELECT id, project_id, pinned FROM files WHERE path = ?", (rel_s,)).fetchone()
    try:
        parsed = parse_file(abs_path)
        error = None
    except Exception as exc:  # broken or foreign file
        log.warning("Kann %s nicht lesen: %s", rel_s, exc)
        parsed, error = None, str(exc)

    if existing and existing["pinned"]:
        project_id = existing["project_id"]
    else:
        key, name = group_key_for(rel)
        # Meaningless file names: use the title from the 3MF, if present
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
        error=error, missing=0, sha256=None,
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
    if preview_dir.exists():  # discard old 3D previews of this file
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


WATCH = os.environ.get("WATCH_LIBRARY", "true").strip().lower() not in ("0", "false", "no", "off")


def _watch_relevant(change, path: str) -> bool:
    """Only print files, nothing from hidden folders (.trash, .photos) or uploads in progress."""
    for base in (LIBRARY_DIR, LIBRARY_DIR.resolve()):
        try:
            parts = Path(path).relative_to(base).parts
            break
        except ValueError:
            continue
    else:
        return False
    return bool(parts) and is_library_file(parts[-1]) and not any(p.startswith((".", "@")) for p in parts[:-1])


def watch_loop(stop_event=None) -> None:
    """Re-scans as soon as a print file changes instead of waiting for the next pass.

    On network shares (SMB/NFS) the system often does not report changes made by others; there
    the regular pass (SCAN_INTERVAL) remains the fallback.
    """
    from watchfiles import watch
    while not (stop_event and stop_event.is_set()):
        try:
            for _changes in watch(LIBRARY_DIR, watch_filter=_watch_relevant, debounce=2000, raise_interrupt=False,
                                  stop_event=stop_event):
                scan(wait=True)
        except Exception as exc:  # e.g. too many folders for inotify
            log.warning("Ordnerüberwachung nicht möglich (%s); neue Dateien erscheinen beim nächsten Durchlauf", exc)
            time.sleep(600)


def _fill_hashes(con: sqlite3.Connection) -> None:
    """SHA-256 of files that do not have one yet (new, modified or from older versions)."""
    for r in con.execute("SELECT id, path FROM files WHERE sha256 IS NULL AND missing = 0").fetchall():
        try:
            with open(LIBRARY_DIR / r["path"], "rb") as fh:
                digest = hashlib.file_digest(fh, "sha256").hexdigest()
        except OSError:
            continue
        con.execute("UPDATE files SET sha256 = ? WHERE id = ?", (digest, r["id"]))
        con.commit()


def duplicates_of(con: sqlite3.Connection, file_ids: list[int]) -> dict[int, list[dict]]:
    """Other files with exactly the same content: {file ID: [{path, project_id, project_name}]}."""
    if not file_ids:
        return {}
    marks = ",".join("?" * len(file_ids))
    rows = con.execute(
        f"""SELECT a.id AS fid, b.path, b.project_id, p.name AS project_name FROM files a
            JOIN files b ON b.sha256 = a.sha256 AND b.id != a.id AND b.missing = 0
            LEFT JOIN projects p ON p.id = b.project_id
            WHERE a.id IN ({marks}) AND a.sha256 IS NOT NULL ORDER BY b.path""", file_ids)
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["fid"], []).append({"path": r["path"], "project_id": r["project_id"],
                                             "project_name": r["project_name"]})
    return out


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
                dirs[:] = [d for d in dirs if not d.startswith((".", "@"))]  # @eaDir etc. from the NAS
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
            _fill_hashes(con)
            # Clean up empty projects without data of their own. Vanished files are detached from the
            # project first (otherwise the foreign key prevents deletion); if they come back,
            # they are reassigned.
            empty = """id NOT IN (SELECT DISTINCT project_id FROM files WHERE project_id IS NOT NULL AND missing = 0)
                       AND notes = '' AND tags = '' AND source_url = '' AND status = '' AND cover_photo IS NULL"""
            con.execute(f"""UPDATE files SET project_id = NULL, pinned = 0
                            WHERE missing = 1 AND project_id IN (SELECT id FROM projects WHERE {empty})""")
            con.execute(f"DELETE FROM projects WHERE {empty}")
            errors = con.execute("SELECT COUNT(*) FROM files WHERE error IS NOT NULL AND missing = 0").fetchone()[0]
    finally:
        last_scan.update(finished=time.time(), added=added, updated=updated, errors=errors, running=False)
        _scan_lock.release()
    if added or updated:
        log.info("Scan: %d neu, %d aktualisiert, %d Fehler", added, updated, errors)
        from . import print_history  # match prints without a file to new files right away (e.g. after an upload)
        try:
            print_history.link_pending()
        except Exception:  # noqa: BLE001  (the scan itself succeeded)
            log.exception("Drucke konnten nicht zugeordnet werden")
    if PREBUILD:
        request_prebuild()
    return last_scan


SETTING_DEFAULTS = {
    "strompreis": 0.35,        # euros per kWh
    "leistung_standard": 100,  # average power of a printer in watts
}


def get_settings(con: sqlite3.Connection | None = None) -> dict:
    """Settings with defaults; printer power values as "leistung:<printer>"."""
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


def price_rules(settings: dict) -> list[tuple[str, float]]:
    """Price rules "keyword -> €/kg", longest (most specific) keyword first."""
    rules = [(k.split(":", 1)[1], float(v)) for k, v in settings.items()
             if k.startswith("filamentpreis:") and v not in (None, "")]
    return sorted(rules, key=lambda r: (-len(r[0]), r[0].lower()))


def matching_rule(rules: list[tuple[str, float]], f: dict) -> tuple[str, float] | None:
    """First (i.e. most specific) rule whose keyword occurs in the profile name or material."""
    text = f"{f.get('name') or ''} {f.get('type') or ''}".lower()
    return next((r for r in rules if r[0].lower() in text), None)


def filament_price(settings: dict, f: dict, rules: list | None = None) -> float | None:
    """€/kg: matching price rule, otherwise the price from the file, otherwise the default price.

    Many files carry no price (Cura, often PrusaSlicer, profiles with 0 €); with the default
    price all prints still have material costs. With „Preise aus den Dateien ignorieren“
    only rules and the default price count (slicer profiles never maintained).
    """
    rule = matching_rule(price_rules(settings) if rules is None else rules, f)
    if rule:
        return rule[1]
    if f.get("cost_per_kg") and not settings.get("dateipreise_ignorieren"):
        return f["cost_per_kg"]
    default = settings.get("filamentpreis_standard")
    return float(default) if default not in (None, "") else f.get("cost_per_kg")


def material_cost(settings: dict, filaments: list[dict], rules: list | None = None) -> float | None:
    rules = price_rules(settings) if rules is None else rules
    costs = [(f.get("used_g") or 0) / 1000 * price for f in filaments
             if (price := filament_price(settings, f, rules)) is not None]
    return sum(costs) if costs else None


def rule_hits(con: sqlite3.Connection, rules: list[tuple[str, float]]) -> dict[str, int]:
    """Per keyword: how many sliced plates the rule actually matches (for the settings)."""
    hits = {keyword: 0 for keyword, _ in rules}
    for r in con.execute("""SELECT p.filaments_json FROM plates p JOIN files f ON f.id = p.file_id
                            WHERE f.missing = 0 AND p.sliced = 1"""):
        used = {rule[0] for fil in json.loads(r["filaments_json"] or "[]")
                if (fil.get("used_g") or 0) > 0 and (rule := matching_rule(rules, fil))}
        for keyword in used:
            hits[keyword] += 1
    return hits


def unique_path(folder: Path, name: str) -> Path:
    """Never overwrite: on a name clash append ' (2)', ' (3)' …"""
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
    """Creates missing 3D previews one after another in the background."""
    from . import render
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
            current = render.mode()
            for r in rows:
                path = LIBRARY_DIR / r["path"]
                if path.exists():
                    st = preview_status(path, r["idx"], DATA_DIR / "preview" / f"{r['id']}_{r['idx']}", background=False)
                    if st["state"] == "ready":
                        render.ensure(r["id"], r["idx"], current)  # own preview image
        except Exception:
            log.exception("Vorberechnung der 3D-Vorschau fehlgeschlagen")


def scan_loop(interval: int) -> None:
    """Polling instead of inotify, so it also runs reliably on SMB/NFS shares."""
    global _force_next_scan
    while True:
        try:
            force, _force_next_scan = _force_next_scan, False
            scan(force=force)
        except Exception:
            log.exception("Scan fehlgeschlagen")
        time.sleep(interval)
