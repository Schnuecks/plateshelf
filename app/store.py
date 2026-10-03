# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""SQLite-Speicher und Ordner-Scanner.

Die 3MF- und G-Code-Dateien bleiben unangetastet in der Bibliothek. In der Datenbank
landen nur ausgelesene Metadaten, Vorschaubilder und deine eigenen Notizen.
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


SCHEMA_VERSION = 6  # 1 = Stand von 0.7.9; spätere Änderungen als Schritte in MIGRATIONS


def _add_column(table: str, column: str, decl: str):
    def step(con: sqlite3.Connection) -> None:
        if column not in {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return step


MIGRATIONS: dict = {
    2: _add_column("print_jobs", "plate", "INTEGER"),  # 0.7.12: Bambu meldet teils nur die Platte
    3: lambda con: None,  # Benutzerkonten: Tabellen users, sessions, api_tokens (legt SCHEMA an)
    4: lambda con: _migrate_4(con),
    5: _add_column("users", "oidc_sub", "TEXT"),  # 0.8.8: Anmeldung über OIDC
    6: lambda con: _rescan_all(),  # 0.8.9: „Vom Drucker“ wieder nach Namen gruppieren
}


def _rescan_all() -> None:
    global _force_next_scan
    _force_next_scan = True


def _migrate_4(con: sqlite3.Connection) -> None:
    """Fotos als Titelbild, Notiz und Bewertung pro Druck, Hash gegen doppelte Dateien."""
    for table, column, decl in (("projects", "cover_photo", "TEXT"), ("print_jobs", "note", "TEXT"),
                                ("print_jobs", "rating", "INTEGER"), ("files", "sha256", "TEXT")):
        _add_column(table, column, decl)(con)


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
        if not existed:
            version = SCHEMA_VERSION  # neu angelegt: schon auf dem aktuellen Stand
        if existed and version == 0:
            _upgrade_before_versioning(con)
            con.executescript(SCHEMA)  # Indizes nach dem Umbau wiederherstellen
            version = 1
        for step in sorted(v for v in MIGRATIONS if v > version):
            MIGRATIONS[step](con)
            log.info("Datenbank auf Schema-Version %d aktualisiert", step)
        # Indizes auf Spalten aus Migrationen erst danach, sonst fehlt die Spalte in alten Datenbanken
        con.execute("CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256)")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_oidc ON users(oidc_sub)")
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
# Ordner, deren Dateien über den Namen gruppiert werden statt als ein Projekt. „Vom Drucker“
# hieß der Importordner bis 0.6 (Druckarchiv); dort liegen bei älteren Installationen noch viele
# Drucke, die sonst beim nächsten Einlesen zu einem einzigen Projekt zusammenfallen.
FLAT_DIRS = {"from printer", "vom drucker"} | {os.environ.get("PRINTER_IMPORT_DIR", "").strip().lower()} - {""}


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


WATCH = os.environ.get("WATCH_LIBRARY", "true").strip().lower() not in ("0", "false", "no", "off")


def _watch_relevant(change, path: str) -> bool:
    """Nur Druckdateien, nichts aus versteckten Ordnern (.trash, .photos) oder Uploads in Arbeit."""
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
    """Liest neu ein, sobald sich eine Druckdatei ändert, statt bis zum nächsten Durchlauf zu warten.

    Auf Netzwerkfreigaben (SMB/NFS) meldet das System fremde Änderungen oft nicht; dort
    bleibt der regelmäßige Durchlauf (SCAN_INTERVAL) der Rückfall.
    """
    from watchfiles import watch
    while not (stop_event and stop_event.is_set()):
        try:
            for _changes in watch(LIBRARY_DIR, watch_filter=_watch_relevant, debounce=2000, raise_interrupt=False,
                                  stop_event=stop_event):
                scan(wait=True)
        except Exception as exc:  # z. B. zu viele Ordner für inotify
            log.warning("Ordnerüberwachung nicht möglich (%s); neue Dateien erscheinen beim nächsten Durchlauf", exc)
            time.sleep(600)


def _fill_hashes(con: sqlite3.Connection) -> None:
    """SHA-256 der Dateien, die noch keinen haben (neu, geändert oder aus älteren Versionen)."""
    for r in con.execute("SELECT id, path FROM files WHERE sha256 IS NULL AND missing = 0").fetchall():
        try:
            with open(LIBRARY_DIR / r["path"], "rb") as fh:
                digest = hashlib.file_digest(fh, "sha256").hexdigest()
        except OSError:
            continue
        con.execute("UPDATE files SET sha256 = ? WHERE id = ?", (digest, r["id"]))
        con.commit()


def duplicates_of(con: sqlite3.Connection, file_ids: list[int]) -> dict[int, list[dict]]:
    """Andere Dateien mit genau demselben Inhalt: {Datei-ID: [{path, project_id, project_name}]}."""
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
            _fill_hashes(con)
            # Leere Projekte ohne eigene Angaben aufräumen
            con.execute(
                """DELETE FROM projects WHERE id NOT IN (SELECT DISTINCT project_id FROM files
                   WHERE project_id IS NOT NULL AND missing = 0)
                   AND notes = '' AND tags = '' AND source_url = '' AND status = '' AND cover_photo IS NULL"""
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


def filament_name(f: dict) -> str:
    """Profilname ohne Drucker-Zusatz: „Panchroma PLA Matte @BBL P2S“ -> „Panchroma PLA Matte“."""
    return (f.get("name") or "").split(" @")[0].strip() or (f.get("type") or "").strip()


def filament_price(settings: dict, f: dict) -> float | None:
    """€/kg: eigener Preis des Filamentprofils, sonst der aus der Datei, sonst der Standardpreis.

    Viele Dateien bringen keinen Preis mit (Cura, oft PrusaSlicer, Profile mit 0 €); mit dem
    Standardpreis haben trotzdem alle Drucke Materialkosten. Mit „Preise aus den Dateien
    ignorieren“ zählen nur eigene Preise und der Standardpreis (Slicer-Profile nie gepflegt).
    """
    own = settings.get(f"filamentpreis:{filament_name(f)}")
    if own not in (None, ""):
        return float(own)
    if f.get("cost_per_kg") and not settings.get("dateipreise_ignorieren"):
        return f["cost_per_kg"]
    default = settings.get("filamentpreis_standard")
    return float(default) if default not in (None, "") else f.get("cost_per_kg")


def material_cost(settings: dict, filaments: list[dict]) -> float | None:
    costs = [(f.get("used_g") or 0) / 1000 * price for f in filaments
             if (price := filament_price(settings, f)) is not None]
    return sum(costs) if costs else None


def filament_profiles(con: sqlite3.Connection) -> dict[str, dict]:
    """Filamentprofile der Bibliothek: Anzahl Platten, davon ohne Preis, häufigster Preis aus den Dateien."""
    out: dict[str, dict] = {}
    for r in con.execute("""SELECT p.filaments_json FROM plates p JOIN files f ON f.id = p.file_id
                            WHERE f.missing = 0 AND p.sliced = 1"""):
        for fil in json.loads(r["filaments_json"] or "[]"):
            if (fil.get("used_g") or 0) <= 0 or not (name := filament_name(fil)):
                continue
            entry = out.setdefault(name, {"plates": 0, "unpriced": 0, "prices": {}})
            entry["plates"] += 1
            if fil.get("cost_per_kg"):
                entry["prices"][fil["cost_per_kg"]] = entry["prices"].get(fil["cost_per_kg"], 0) + 1
            else:
                entry["unpriced"] += 1
    for entry in out.values():
        prices = entry.pop("prices")
        entry["file_price"] = max(prices, key=prices.get) if prices else None
    return out


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
