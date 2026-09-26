# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Holt geslicte Drucke per FTPS direkt von der SD-Karte der Bambu-Drucker.

Funktioniert auch ohne LAN-only-Modus, solange der Drucker den Dateizugriff mit
dem Zugangscode erlaubt (Port 990, Benutzer "bblp", implizites TLS).

Drucker werden unter „Einstellungen“ gepflegt (Tabelle printers) oder über
Umgebungsvariablen vorgegeben:
    PRINTER_1_NAME=P2S  PRINTER_1_IP=192.0.2.20  PRINTER_1_SERIAL=<Seriennummer>  PRINTER_1_CODE=<Zugangscode>
Eine printers.json älterer Versionen wird beim ersten Start einmalig übernommen.
"""
from __future__ import annotations

import ftplib
import json
import logging
import os
import re
import socket
import ssl
import threading
import time
from pathlib import Path

from . import store
from .parser import parse_3mf

log = logging.getLogger("druckarchiv.drucker")

# Nur noch für die einmalige Übernahme in die Einstellungen
PRINTERS_FILE = Path(os.environ.get("PRINTERS_FILE", "/config/printers.json"))
SYNC_INTERVAL = int(os.environ.get("PRINTER_SYNC_INTERVAL", "600"))
IMPORT_DIR = os.environ.get("PRINTER_IMPORT_DIR", "Vom Drucker")
TIMEOUT = 15

# Zeile aus einem Unix-artigen LIST: Rechte, Links, Besitzer, Gruppe, Größe, Datum, Name
_LIST_RE = re.compile(
    r"^(?P<type>[-dl])\S+\s+\d+\s+\S+\s+\S+\s+(?P<size>\d+)\s+"
    r"(?P<date>\w{3}\s+\d{1,2}\s+(?:\d{1,2}:\d{2}|\d{4}))\s+(?P<name>.+)$"
)
_SAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

state: dict = {"enabled": False, "running": False, "last_run": None, "printers": {}}
_lock = threading.Lock()
_wakeup = threading.Event()

SCHEMA = """
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
    code TEXT NOT NULL
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
"""


class ImplicitFTPS(ftplib.FTP_TLS):
    """FTP über implizites TLS (Port 990), wie es Bambu-Drucker verwenden.

    Der Datenkanal muss die TLS-Sitzung des Steuerkanals wiederverwenden,
    sonst lehnt der Drucker die Übertragung ab.
    """

    def __init__(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # Drucker nutzt ein selbstsigniertes Zertifikat
        kwargs.setdefault("context", ctx)
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=self.host)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


_ENV_KEY = re.compile(r"PRINTER_(\d+)_(NAME|IP|SERIAL|CODE)")


def env_printers(environ=None) -> list[dict]:
    """Drucker aus PRINTER_<n>_NAME/_IP/_SERIAL/_CODE, sortiert nach <n>."""
    environ = os.environ if environ is None else environ
    found: dict[int, dict] = {}
    for key, value in environ.items():
        if m := _ENV_KEY.fullmatch(key):
            found.setdefault(int(m[1]), {})[m[2].lower()] = value.strip()
    printers = []
    for n in sorted(found):
        p = found[n]
        missing = [k for k in ("ip", "serial", "code") if not p.get(k)]
        if missing:
            log.warning("PRINTER_%d unvollständig, es fehlt %s", n, ", ".join(f"PRINTER_{n}_{k.upper()}" for k in missing))
            continue
        printers.append({"name": p.get("name") or p["serial"], "id": p["serial"], "code": p["code"],
                         "ip": p["ip"], "source": "env"})
    return printers


def db_printers() -> list[dict]:
    with store.connect() as con:
        return [{"name": r["name"], "id": r["serial"], "code": r["code"], "ip": r["ip"], "source": "db",
                 "db_id": r["id"]} for r in con.execute("SELECT * FROM printers ORDER BY name COLLATE NOCASE, id")]


def load_printers() -> list[dict]:
    """Alle Drucker; bei gleicher Seriennummer gilt die Umgebungsvariable."""
    env = env_printers()
    serials = {p["id"] for p in env}
    return env + [p for p in db_printers() if p["id"] not in serials]


def save_printers(rows: list[dict]) -> None:
    """Speichert die Drucker aus den Einstellungen.

    rows: {"db_id": int|None, "name", "ip", "serial", "code", "delete": bool}.
    Leerer Zugangscode bei einem vorhandenen Drucker behält den gespeicherten.
    Wirft ValueError mit einer lesbaren Meldung, dann wird nichts gespeichert.
    """
    with store.connect() as con:
        existing = {r["id"]: dict(r) for r in con.execute("SELECT * FROM printers")}
        keep, seen = [], set()
        env_serials = {p["id"] for p in env_printers()}
        for row in rows:
            name, ip, serial, code = (str(row.get(k) or "").strip() for k in ("name", "ip", "serial", "code"))
            old = existing.get(row.get("db_id")) if row.get("db_id") else None
            if row.get("delete") or (not old and not any((name, ip, serial, code))):
                continue
            label = name or serial or ip or "Neuer Drucker"
            code = code or (old["code"] if old else "")
            missing = [t for t, v in (("IP-Adresse", ip), ("Seriennummer", serial), ("Zugangscode", code)) if not v]
            if missing:
                raise ValueError(f"{label}: {' und '.join(missing)} fehlt")
            if serial in seen:
                raise ValueError(f"Seriennummer {serial} ist doppelt eingetragen")
            if serial in env_serials:
                raise ValueError(f"{label}: Seriennummer {serial} ist schon über Umgebungsvariablen eingerichtet")
            seen.add(serial)
            keep.append((old["id"] if old else None, name or serial, serial, ip, code))
        kept_ids = {k[0] for k in keep if k[0]}
        for pid in set(existing) - kept_ids:
            con.execute("DELETE FROM printers WHERE id = ?", (pid,))
        # Erst alle Seriennummern freigeben, damit Tauschen zwischen Zeilen klappt
        for pid in kept_ids:
            con.execute("UPDATE printers SET serial = '~' || id WHERE id = ?", (pid,))
        for pid, name, serial, ip, code in keep:
            if pid:
                con.execute("UPDATE printers SET name = ?, serial = ?, ip = ?, code = ? WHERE id = ?",
                            (name, serial, ip, code, pid))
            else:
                con.execute("INSERT INTO printers (name, serial, ip, code) VALUES (?, ?, ?, ?)",
                            (name, serial, ip, code))
    current = {p["id"] for p in load_printers()}
    for serial in set(state["printers"]) - current:
        state["printers"].pop(serial, None)  # Meldungen gelöschter Drucker nicht mehr zeigen
    state["enabled"] = bool(current)
    request_sync()


def _import_printers_json(con) -> None:
    """Übernimmt eine vorhandene printers.json einmalig in die Einstellungen."""
    if con.execute("SELECT 1 FROM settings WHERE key = 'printers_json_imported'").fetchone():
        return
    if not PRINTERS_FILE.exists():
        return
    try:
        data = json.loads(PRINTERS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("printers.json nicht lesbar: %s", exc)
        return
    count = 0
    for p in data if isinstance(data, list) else []:
        if isinstance(p, dict) and all(p.get(k) for k in ("id", "code", "ip")):
            count += con.execute(
                "INSERT OR IGNORE INTO printers (name, serial, ip, code) VALUES (?, ?, ?, ?)",
                (str(p.get("name") or p["id"]), str(p["id"]), str(p["ip"]), str(p["code"])),
            ).rowcount
    con.execute("INSERT INTO settings (key, value) VALUES ('printers_json_imported', ?)", (str(time.time()),))
    log.info("printers.json: %d Drucker in die Einstellungen übernommen, die Datei wird nicht mehr benötigt", count)


def init() -> None:
    with store.connect() as con:
        con.executescript(SCHEMA)
        _import_printers_json(con)
    state["enabled"] = bool(load_printers())


def parse_listing(lines: list[str]) -> list[dict]:
    """Nur Dateien im Hauptverzeichnis, die auf .gcode.3mf enden."""
    out = []
    for line in lines:
        m = _LIST_RE.match(line.strip())
        if not m or m["type"] != "-":
            continue
        name = m["name"].strip()
        if name.lower().endswith(".gcode.3mf"):
            out.append({"name": name, "size": int(m["size"]), "date": " ".join(m["date"].split())})
    return out


def _local_name(remote: str) -> str:
    return _SAFE_NAME.sub("_", remote).strip(" .") or "Unbenannt.gcode.3mf"


def _plate_file_name(remote: str, downloaded: Path) -> str:
    """Name der lokalen Datei, mit der gedruckten Platte im Namen.

    Der Drucker speichert jedes Projekt unter demselben Namen und überschreibt es,
    wenn eine andere Platte gedruckt wird. Mit der Plattennummer im Namen bleibt
    jede gedruckte Platte als eigene Datei erhalten.
    """
    base = _local_name(remote)
    try:
        parsed = parse_3mf(downloaded)
    except Exception:  # noqa: BLE001
        return base
    sliced = [p.index for p in parsed.plates if p.sliced]
    if len(parsed.plates) <= 1 or len(sliced) != 1:
        return base  # einplattig oder mehrere Platten auf einmal: Name unverändert
    if re.search(r"[_\- ](plate|platte)[_\- ]?\d+", base[:-10], re.I):
        return base  # Name enthält schon eine Plattennummer
    return f"{base[:-10]}_plate_{sliced[0]}.gcode.3mf"


def _connect(printer: dict) -> ImplicitFTPS:
    ftp = ImplicitFTPS(timeout=TIMEOUT)
    ftp.connect(printer["ip"], 990)
    ftp.login("bblp", printer["code"])
    ftp.prot_p()
    return ftp


def sync_printer(printer: dict, connect=_connect) -> dict:
    """Holt neue oder geänderte Dateien eines Druckers. Liefert eine kurze Bilanz."""
    result = {"name": printer["name"], "ok": False, "new": 0, "updated": 0, "error": None, "files": 0}
    target_dir = store.LIBRARY_DIR / IMPORT_DIR / _local_name(printer["name"])
    try:
        ftp = connect(printer)
    except ftplib.error_perm as exc:
        result["error"] = f"Anmeldung abgelehnt, Zugangscode prüfen ({exc})"
        return result
    except (OSError, ftplib.Error, socket.timeout) as exc:
        result["error"] = f"nicht erreichbar, ausgeschaltet oder im Standby? ({exc.__class__.__name__})"
        return result
    try:
        lines: list[str] = []
        ftp.retrlines("LIST /", lines.append)
        remote = parse_listing(lines)
        result["files"] = len(remote)
        with store.connect() as con:
            known = {r["remote_name"]: r for r in con.execute(
                "SELECT remote_name, size, remote_date FROM printer_files WHERE printer = ?", (printer["id"],))}
        for f in remote:
            prev = known.get(f["name"])
            if prev and prev["size"] == f["size"] and prev["remote_date"] == f["date"]:
                continue  # schon geholt (oder bewusst gelöscht): nichts tun
            target_dir.mkdir(parents=True, exist_ok=True)
            tmp = target_dir / f".{_local_name(f['name'])}.part"
            try:
                with open(tmp, "wb") as fh:
                    ftp.retrbinary(f"RETR /{f['name']}", fh.write, blocksize=256 * 1024)
                if tmp.stat().st_size != f["size"]:
                    raise OSError(f"unvollständig ({tmp.stat().st_size} von {f['size']} Byte)")
                final = target_dir / _plate_file_name(f["name"], tmp)
                os.replace(tmp, final)
            except (OSError, ftplib.Error) as exc:
                log.warning("%s: %s konnte nicht geholt werden: %s", printer["name"], f["name"], exc)
                tmp.unlink(missing_ok=True)
                continue
            rel = final.relative_to(store.LIBRARY_DIR).as_posix()
            with store.connect() as con:
                con.execute(
                    """INSERT INTO printer_files (printer, remote_name, size, remote_date, local_path, printer_name, imported)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(printer, remote_name) DO UPDATE SET size = excluded.size,
                         remote_date = excluded.remote_date, local_path = excluded.local_path,
                         printer_name = excluded.printer_name, imported = excluded.imported""",
                    (printer["id"], f["name"], f["size"], f["date"], rel, printer["name"], time.time()),
                )
                con.execute(
                    """INSERT INTO printer_imports (local_path, printer_name, remote_name, remote_date)
                       VALUES (?, ?, ?, ?) ON CONFLICT(local_path) DO UPDATE SET
                         printer_name = excluded.printer_name, remote_name = excluded.remote_name,
                         remote_date = excluded.remote_date""",
                    (rel, printer["name"], f["name"], f["date"]),
                )
            result["updated" if prev else "new"] += 1
            log.info("%s: %s übernommen", printer["name"], f["name"])
        result["ok"] = True
    except (OSError, ftplib.Error, socket.timeout) as exc:
        result["error"] = f"Fehler beim Abrufen ({exc.__class__.__name__}: {exc})"
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            ftp.close()
    return result


def sync_all() -> None:
    if not _lock.acquire(blocking=False):
        return
    state["running"] = True
    changed = False
    try:
        printers = load_printers()
        for serial in set(state["printers"]) - {p["id"] for p in printers}:
            state["printers"].pop(serial, None)
        for printer in printers:
            res = sync_printer(printer)
            res["at"] = time.time()
            state["printers"][printer["id"]] = res
            changed |= bool(res["new"] or res["updated"])
            if res["error"]:
                log.info("%s: %s", printer["name"], res["error"])
        state["last_run"] = time.time()
    finally:
        state["running"] = False
        _lock.release()
    if changed:
        store.scan()  # neue Dateien sofort einlesen, danach startet die 3D-Vorberechnung


def request_sync() -> None:
    _wakeup.set()


def sync_loop() -> None:
    while True:
        try:
            if load_printers():
                state["enabled"] = True
                sync_all()
            else:
                state["enabled"] = False
        except Exception:  # noqa: BLE001
            log.exception("Drucker-Import fehlgeschlagen")
        _wakeup.wait(SYNC_INTERVAL)
        _wakeup.clear()


def origin_for(paths: list[str]) -> dict[str, dict]:
    """Welche lokalen Dateien vom Drucker stammen: Pfad -> {printer_name, remote_date}."""
    if not paths:
        return {}
    with store.connect() as con:
        marks = ",".join("?" * len(paths))
        rows = con.execute(
            f"SELECT local_path, printer_name, remote_date FROM printer_imports WHERE local_path IN ({marks})", paths)
        found = {r["local_path"]: dict(r) for r in rows}
        # Importe aus der Zeit vor der Tabelle printer_imports
        rows = con.execute(
            f"SELECT local_path, printer_name, remote_date FROM printer_files WHERE local_path IN ({marks})", paths)
        for r in rows:
            found.setdefault(r["local_path"], dict(r))
        return found
