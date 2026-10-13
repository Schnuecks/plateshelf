# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Trash: deleted files live under .trash/<entry>/<original path>.

For each entry the database remembers what is needed to restore it:
project name, notes, tags, status, source and the manual assignment of the files.
After TRASH_DAYS days entries are deleted permanently (0 = never).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from . import store

log = logging.getLogger("plateshelf")

TRASH_DIR = ".trash"
TRASH_DAYS = int(os.environ.get("TRASH_DAYS", "30"))
PROJECT_FIELDS = ("group_key", "name", "notes", "tags", "source_url", "status", "cover_photo")
_FOLDER = re.compile(r"[\w.\- ]+")


def trash_root() -> Path:
    """Trash folder of the library."""
    return store.LIBRARY_DIR / TRASH_DIR


def _folder_path(folder: str) -> Path:
    if not _FOLDER.fullmatch(folder) or folder in (".", ".."):
        raise ValueError(f"Ungültiger Papierkorb-Eintrag: {folder}")
    return trash_root() / folder


def _library_path(rel_path: str) -> Path:
    lib = store.LIBRARY_DIR.resolve()
    path = (lib / rel_path).resolve()
    if not path.is_relative_to(lib) or path == lib:
        raise ValueError(f"Ungültiger Pfad: {rel_path}")
    return path


def _remove_empty_parents(path: Path, stop: Path) -> None:
    parent = path.parent
    while parent != stop and parent.is_relative_to(stop):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def forget_file(con: sqlite3.Connection, fid: int) -> None:
    """Removes a file including preview images and 3D previews from the database."""
    from . import gcode_preview
    con.execute("DELETE FROM plates WHERE file_id = ?", (fid,))
    con.execute("DELETE FROM files WHERE id = ?", (fid,))
    shutil.rmtree(store.THUMB_DIR / str(fid), ignore_errors=True)
    for old in (store.DATA_DIR / "preview").glob(f"{fid}_*"):
        old.unlink(missing_ok=True)
    gcode_preview.forget(store.DATA_DIR / "preview", fid)


# --- Delete ------------------------------------------------------------------

def delete(con: sqlite3.Connection, file_ids: list[int], kind: str) -> int:
    """Moves files into a new trash entry. Returns: ID of the entry.

    kind "projekt" deletes the whole project of the files, "datei" only the files;
    if the project becomes empty in the process, it disappears too. Its data is
    stored in the entry in both cases and comes back on restore.
    """
    rows = [con.execute("SELECT id, path, project_id, pinned, missing FROM files WHERE id = ?", (fid,)).fetchone()
            for fid in file_ids]
    rows = [r for r in rows if r]
    if not rows:
        raise LookupError("Keine Datei gefunden")
    pid = rows[0]["project_id"]
    project = con.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    now = time.time()
    folder = f"{time.strftime('%Y-%m-%d_%H-%M-%S', time.localtime(now))}_{uuid.uuid4().hex[:6]}"
    base = _folder_path(folder)
    lib = store.LIBRARY_DIR.resolve()

    moved = []
    for r in rows:
        if not r["missing"]:
            src = _library_path(r["path"])
            if src.exists():
                dst = base / r["path"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dst)
                _remove_empty_parents(src, lib)  # clean up project folders that became empty
                moved.append({"path": r["path"], "pinned": r["pinned"]})
        forget_file(con, r["id"])

    left = con.execute("SELECT COUNT(*) FROM files WHERE project_id = ? AND missing = 0", (pid,)).fetchone()[0]
    removed = kind == "projekt" or not left
    if removed:
        con.execute("DELETE FROM files WHERE project_id = ?", (pid,))
        con.execute("DELETE FROM projects WHERE id = ?", (pid,))

    if kind == "projekt" or len(moved) != 1:
        label = project["name"] if project else ""  # empty: „Unbekanntes Projekt“ in the display language
    else:
        label = moved[0]["path"].rsplit("/", 1)[-1]
    snapshot = {**{k: project[k] for k in PROJECT_FIELDS}, "removed": removed} if project else None
    return con.execute(
        "INSERT INTO trash (folder, kind, label, project_json, files_json, deleted, since) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (folder, kind, label, json.dumps(snapshot) if snapshot else None, json.dumps(moved), now, now),
    ).lastrowid


# --- Restore -----------------------------------------------------------------

def _restore_project(con: sqlite3.Connection, snapshot: dict) -> int:
    """Recreate the project or, if it exists again by now, fill in empty fields.

    If the project still existed after the deletion, its current data stays untouched.
    """
    row = con.execute("SELECT * FROM projects WHERE group_key = ?", (snapshot["group_key"],)).fetchone()
    if row:
        if not snapshot.get("removed"):
            return row["id"]
        fill = {k: snapshot[k] for k in ("notes", "tags", "source_url", "status") if snapshot.get(k) and not row[k]}
        if fill:
            sets = ", ".join(f"{k} = ?" for k in fill)
            con.execute(f"UPDATE projects SET {sets}, updated = ? WHERE id = ?", (*fill.values(), time.time(), row["id"]))
        return row["id"]
    now = time.time()
    return con.execute(
        f"INSERT INTO projects ({', '.join(PROJECT_FIELDS)}, created, updated) "
        f"VALUES ({', '.join('?' * (len(PROJECT_FIELDS) + 2))})",
        (*(snapshot.get(k) or ("" if k != "cover_photo" else None) for k in PROJECT_FIELDS), now, now),
    ).lastrowid


def restore(entry_id: int) -> dict:
    """Brings an entry back. Returns: {"project_id", "restored", "renamed", "missing"}.

    If another file now occupies the original location, the restored one
    is renamed (" (2)"); nothing is ever overwritten.
    """
    result = {"project_id": None, "restored": 0, "renamed": [], "missing": 0}
    # While moving files back, no scan may clean up the half-created project as empty
    with store._scan_lock:
        with store.connect() as con:
            entry = con.execute("SELECT * FROM trash WHERE id = ?", (entry_id,)).fetchone()
            if not entry:
                raise LookupError("Eintrag nicht gefunden")
            base = _folder_path(entry["folder"])
            snapshot = json.loads(entry["project_json"]) if entry["project_json"] else None
            pid = _restore_project(con, snapshot) if snapshot else None
            for f in json.loads(entry["files_json"]):
                src = base / f["path"]
                if not src.exists():
                    result["missing"] += 1
                    continue
                dst = _library_path(f["path"])
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst = store.unique_path(dst.parent, dst.name)
                os.replace(src, dst)
                rel = dst.relative_to(store.LIBRARY_DIR.resolve()).as_posix()
                if rel != f["path"]:
                    result["renamed"].append(rel)
                result["restored"] += 1
                if pid and f.get("pinned"):
                    # Placeholder: the scan takes over the manual assignment from here
                    con.execute(
                        "INSERT INTO files (path, project_id, pinned, added) VALUES (?, ?, 1, ?) "
                        "ON CONFLICT(path) DO UPDATE SET project_id = excluded.project_id, pinned = 1, mtime = NULL",
                        (rel, pid, time.time()),
                    )
            con.execute("DELETE FROM trash WHERE id = ?", (entry_id,))
            shutil.rmtree(base, ignore_errors=True)
    store.scan(wait=True)
    with store.connect() as con:
        if pid and con.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
            result["project_id"] = pid
        elif snapshot:
            row = con.execute("SELECT id FROM projects WHERE group_key = ?", (snapshot["group_key"],)).fetchone()
            result["project_id"] = row["id"] if row else None
    return result


# --- Delete permanently -------------------------------------------------------

def purge(entry_id: int) -> None:
    from . import photos
    with store.connect() as con:
        entry = con.execute("SELECT folder, project_json FROM trash WHERE id = ?", (entry_id,)).fetchone()
        if not entry:
            raise LookupError("Eintrag nicht gefunden")
        shutil.rmtree(_folder_path(entry["folder"]), ignore_errors=True)
        con.execute("DELETE FROM trash WHERE id = ?", (entry_id,))
        # Also remove photos of a permanently deleted project, unless it exists again
        snapshot = json.loads(entry["project_json"] or "null")
        key = snapshot.get("group_key") if snapshot and snapshot.get("removed") else None
        if key and not con.execute("SELECT 1 FROM projects WHERE group_key = ?", (key,)).fetchone():
            photos.remove_all(key)


def purge_all() -> int:
    with store.connect() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM trash")]
    for i in ids:
        purge(i)
    return len(ids)


def purge_expired(days: int | None = None, now: float | None = None) -> int:
    days = TRASH_DAYS if days is None else days
    if days <= 0:
        return 0
    limit = (now or time.time()) - days * 86400
    with store.connect() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM trash WHERE since < ?", (limit,))]
    for i in ids:
        purge(i)
    if ids:
        log.info("Papierkorb: %d abgelaufene Einträge endgültig gelöscht", len(ids))
    return len(ids)


# --- Sync with the folder -----------------------------------------------------

def _parse_stamp(folder: str) -> float | None:
    try:
        return time.mktime(time.strptime(folder[:19], "%Y-%m-%d_%H-%M-%S"))
    except ValueError:
        return None


def sync() -> None:
    """Syncs the database with the folder.

    Folders from older versions (without a database entry) are adopted; their
    retention period only starts now, so nothing disappears immediately.
    Entries whose folder was emptied or removed by hand are dropped.
    """
    root = trash_root()
    with store.connect() as con:
        known = {r["folder"]: r["id"] for r in con.execute("SELECT id, folder FROM trash")}
        for folder, entry_id in known.items():
            path = _folder_path(folder)
            if not path.is_dir() or not any(p.is_file() for p in path.rglob("*")):
                shutil.rmtree(path, ignore_errors=True)
                con.execute("DELETE FROM trash WHERE id = ?", (entry_id,))
        if not root.is_dir():
            return
        now = time.time()
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name in known or not _FOLDER.fullmatch(d.name):
                continue
            files = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())
            if not files:
                continue
            names = {f.split("/", 1)[0] for f in files}  # project folder or file name
            label = names.pop() if len(names) == 1 else ""  # empty: number of files in the display language
            con.execute(
                "INSERT INTO trash (folder, kind, label, project_json, files_json, deleted, since) "
                "VALUES (?, 'alt', ?, NULL, ?, ?, ?)",
                (d.name, label, json.dumps([{"path": f, "pinned": 0} for f in files]),
                 _parse_stamp(d.name) or d.stat().st_mtime, now),
            )


def entries() -> list[dict]:
    sync()
    out = []
    with store.connect() as con:
        for r in con.execute("SELECT * FROM trash ORDER BY deleted DESC"):
            e = dict(r)
            files = json.loads(e.pop("files_json"))
            base = _folder_path(e["folder"])
            present = [f for f in files if (base / f["path"]).is_file()]
            e["files"] = [f["path"] for f in present]
            e["size"] = sum((base / f["path"]).stat().st_size for f in present)
            project = json.loads(e.pop("project_json") or "null")
            e["has_notes"] = bool(project and (project.get("notes") or project.get("tags")))
            e["expires"] = e["since"] + TRASH_DAYS * 86400 if TRASH_DAYS > 0 else None
            out.append(e)
    return out


def purge_loop(interval: int = 3600) -> None:
    while True:
        try:
            sync()
            purge_expired()
        except Exception:
            log.exception("Aufräumen des Papierkorbs fehlgeschlagen")
        time.sleep(interval)
