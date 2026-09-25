"""Holt geslicte Drucke per FTPS direkt von der SD-Karte der Bambu-Drucker.

Funktioniert auch ohne LAN-only-Modus, solange der Drucker den Dateizugriff mit
dem Zugangscode erlaubt (Port 990, Benutzer "bblp", implizites TLS).

Die Druckerliste kommt aus einer printers.json im Format des Projekts
bambulab-ams-spoolman-filamentstatus:
    [{"name": "P2S", "id": "<Seriennummer>", "code": "<Zugangscode>", "ip": "192.0.2.20"}]
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


def load_printers() -> list[dict]:
    if not PRINTERS_FILE.exists():
        return []
    try:
        data = json.loads(PRINTERS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("printers.json nicht lesbar: %s", exc)
        return []
    printers = []
    for p in data if isinstance(data, list) else []:
        if all(p.get(k) for k in ("id", "code", "ip")):
            printers.append({"name": str(p.get("name") or p["id"]), "id": str(p["id"]),
                             "code": str(p["code"]), "ip": str(p["ip"])})
    return printers


def init() -> None:
    with store.connect() as con:
        con.executescript(SCHEMA)
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
        for printer in load_printers():
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
