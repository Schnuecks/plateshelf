# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Sicherungen der Datenbank: Download, tägliche automatische Sicherung, Zurückspielen.

Eine Sicherung ist die SQLite-Datenbank als einzelne Datei: Projekte mit Notizen, Tags
und Status, Zuordnungen, Einstellungen, Drucker, Benutzer und der Druckverlauf. Die
Druckdateien liegen ohnehin im Bibliotheksordner; Vorschaubilder und 3D-Vorschauen
entstehen nach dem Zurückspielen neu.

Automatisch: einmal am Tag unter <Datenordner>/backups/, die letzten BACKUP_KEEP
(Standard 7) bleiben. BACKUP_KEEP=0 schaltet das ab.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from . import store
from .i18n import UserError

log = logging.getLogger("plateshelf")

KEEP = max(0, int(os.environ.get("BACKUP_KEEP", "7")))
INTERVAL = 24 * 3600
AUTO_NAME = re.compile(r"^plateshelf-\d{4}-\d{2}-\d{2}(-vor-wiederherstellung-\d{6})?\.sqlite$")
_restore_lock = threading.Lock()


def backup_dir() -> Path:
    return store.DATA_DIR / "backups"


def write(target: Path) -> None:
    """Datenbank konsistent in eine einzelne Datei schreiben, auch während PlateShelf schreibt."""
    src = store.connect()
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode = DELETE")  # eine einzelne Datei, ohne -wal
    finally:
        dst.close()
        src.close()


def list_backups() -> list[dict]:
    folder = backup_dir()
    if not folder.exists():
        return []
    items = []
    for path in folder.iterdir():
        if AUTO_NAME.match(path.name):
            st = path.stat()
            items.append({"name": path.name, "size": st.st_size, "time": st.st_mtime,
                          "before_restore": "-vor-wiederherstellung-" in path.name})
    return sorted(items, key=lambda b: b["time"], reverse=True)


def path_of(name: str) -> Path | None:
    """Nur Dateien aus dem Sicherungsordner, keine beliebigen Pfade."""
    if not AUTO_NAME.match(name):
        return None
    path = backup_dir() / name
    return path if path.is_file() else None


def run_auto(now: float | None = None) -> Path | None:
    """Tägliche Sicherung anlegen, wenn die letzte älter als einen Tag ist; alte aufräumen."""
    if not KEEP:
        return None
    now = now or time.time()
    daily = [b for b in list_backups() if not b["before_restore"]]
    if daily and now - daily[0]["time"] < INTERVAL - 600:
        return None
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"plateshelf-{time.strftime('%Y-%m-%d', time.localtime(now))}.sqlite"
    tmp = target.with_suffix(".part")
    write(tmp)
    os.replace(tmp, target)
    log.info("Automatische Sicherung angelegt: %s", target.name)
    for old in [b for b in list_backups() if not b["before_restore"]][KEEP:]:
        (folder / old["name"]).unlink(missing_ok=True)
    for old in [b for b in list_backups() if b["before_restore"]][3:]:  # höchstens drei Notfall-Kopien
        (folder / old["name"]).unlink(missing_ok=True)
    return target


def loop() -> None:
    time.sleep(120)  # nicht gleich beim Start, während eingelesen wird
    while True:
        try:
            run_auto()
        except Exception:
            log.exception("Automatische Sicherung fehlgeschlagen")
        time.sleep(3600)


def check(path: Path) -> None:
    """Prüft, ob die Datei eine PlateShelf-Sicherung ist, die sich zurückspielen lässt."""
    with open(path, "rb") as fh:
        if fh.read(16) != b"SQLite format 3\x00":
            raise UserError("Das ist keine Sicherung von PlateShelf (keine SQLite-Datenbank).")
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            version = con.execute("PRAGMA user_version").fetchone()[0]
            ok = con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            con.close()
    except sqlite3.DatabaseError:
        raise UserError("Die Sicherung ist beschädigt.") from None
    if not {"projects", "files", "settings"} <= tables:
        raise UserError("Das ist keine Sicherung von PlateShelf (keine SQLite-Datenbank).")
    if not ok:
        raise UserError("Die Sicherung ist beschädigt.")
    if version > store.SCHEMA_VERSION:
        raise UserError("Die Sicherung stammt aus einer neueren Version von PlateShelf. Aktualisiere zuerst.")


def restore(path: Path) -> str:
    """Spielt eine Sicherung zurück; vorher wird der aktuelle Stand gesichert. Gibt dessen Namen zurück."""
    check(path)
    with _restore_lock, store._scan_lock:  # kein Einlesen während des Austauschs
        folder = backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        safety = folder / time.strftime("plateshelf-%Y-%m-%d-vor-wiederherstellung-%H%M%S.sqlite")
        write(safety)
        src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        dst = store.connect()
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        # Vorschaubilder und 3D-Vorschauen gehören zu den Datei-IDs der alten Datenbank
        for cache in (store.THUMB_DIR, store.DATA_DIR / "preview"):
            shutil.rmtree(cache, ignore_errors=True)
        store.init_db()  # ältere Sicherung auf den aktuellen Stand bringen, erzwingt neues Einlesen
    log.warning("Sicherung zurückgespielt; der vorherige Stand liegt als %s im Sicherungsordner", safety.name)
    return safety.name
