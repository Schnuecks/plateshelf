# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Holt geslicte Drucke direkt von den Druckern.

Bambu Lab: per FTPS von der SD-Karte. Funktioniert auch ohne LAN-only-Modus,
solange der Drucker den Dateizugriff mit dem Zugangscode erlaubt (Port 990,
Benutzer "bblp", implizites TLS).

Klipper (Moonraker): per HTTP aus dem G-Code-Ordner des Druckers, etwa bei
Creality K1/K2, Sovol SV08, Qidi, Elegoo Neptune 4 oder Voron. Ein API-Schlüssel
ist nur nötig, wenn Moonraker die Adresse von PlateShelf nicht als vertrauenswürdig kennt.

Prusa (PrusaLink): per HTTP vom USB-Stick oder internen Speicher, bei MK4, MK3.9,
XL, Mini und Core One. Anmeldung mit Benutzer "maker" und dem PrusaLink-Passwort.

Drucker werden unter „Einstellungen“ gepflegt (Tabelle printers) oder über
Umgebungsvariablen vorgegeben:
    PRINTER_1_NAME=P2S  PRINTER_1_IP=192.0.2.20  PRINTER_1_SERIAL=<Seriennummer>  PRINTER_1_CODE=<Zugangscode>
    PRINTER_2_TYPE=moonraker  PRINTER_2_NAME=K1  PRINTER_2_IP=192.0.2.21  PRINTER_2_API_KEY=<optional>
    PRINTER_3_TYPE=prusalink  PRINTER_3_NAME=MK4S  PRINTER_3_IP=192.0.2.22  PRINTER_3_PASSWORD=<Passwort>
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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import store
from .gcode_file import is_gcode
from .i18n import UserError
from .parser import parse_3mf

log = logging.getLogger("plateshelf.printers")

SYNC_INTERVAL = int(os.environ.get("PRINTER_SYNC_INTERVAL", "600"))
LEGACY_IMPORT_DIR = "Vom Drucker"  # Standard bis Version 0.6


def import_dir() -> str:
    """Zielordner für importierte Drucke.

    PRINTER_IMPORT_DIR, sonst „From printer“. Gibt es aus älteren Versionen schon den
    Ordner „Vom Drucker“, wird er weiter benutzt, damit nichts auseinanderfällt.
    """
    env = os.environ.get("PRINTER_IMPORT_DIR", "").strip()
    if env:
        return env
    return LEGACY_IMPORT_DIR if (store.LIBRARY_DIR / LEGACY_IMPORT_DIR).is_dir() else "From printer"
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


_ENV_KEY = re.compile(r"PRINTER_(\d+)_(NAME|IP|SERIAL|CODE|TYPE|API_KEY|PASSWORD)")
KINDS = ("bambu", "moonraker", "prusalink")
MOONRAKER_PORT = 7125


def moonraker_url(address: str, default_port: int | None = MOONRAKER_PORT) -> str:
    """'192.0.2.21' -> 'http://192.0.2.21:7125'; eigene Ports und https bleiben erhalten."""
    address = address.strip().rstrip("/")
    if not re.match(r"^https?://", address, re.I):
        address = "http://" + address
    parts = urllib.parse.urlsplit(address)
    if default_port and parts.port is None and parts.hostname and not parts.path:
        address = f"{parts.scheme}://{parts.netloc}:{default_port}"
    return address


def printer_url(kind: str, ip: str) -> str:
    """Adresse der HTTP-Schnittstelle: Moonraker auf Port 7125, PrusaLink auf dem Webport."""
    return moonraker_url(ip, MOONRAKER_PORT if kind == "moonraker" else None)


def printer_id(kind: str, ip: str, serial: str) -> str:
    """Eindeutige Kennung: Seriennummer bei Bambu, sonst Art und Adresse."""
    return serial if kind == "bambu" else f"{kind}:{printer_url(kind, ip)}"


def env_printers(environ=None) -> list[dict]:
    """Drucker aus PRINTER_<n>_NAME/_IP/_SERIAL/_CODE/_TYPE/_API_KEY/_PASSWORD, sortiert nach <n>."""
    environ = os.environ if environ is None else environ
    found: dict[int, dict] = {}
    for key, value in environ.items():
        if m := _ENV_KEY.fullmatch(key):
            found.setdefault(int(m[1]), {})[m[2].lower()] = value.strip()
    printers = []
    for n in sorted(found):
        p = found[n]
        kind = (p.get("type") or "bambu").lower()
        if kind not in KINDS:
            log.warning("PRINTER_%d_TYPE=%s unbekannt, möglich sind %s", n, kind, ", ".join(KINDS))
            continue
        needed = {"bambu": ("ip", "serial", "code"), "moonraker": ("ip",), "prusalink": ("ip", "password")}[kind]
        missing = [k for k in needed if not p.get(k)]
        if missing:
            log.warning("PRINTER_%d unvollständig, es fehlt %s", n, ", ".join(f"PRINTER_{n}_{k.upper()}" for k in missing))
            continue
        pid = printer_id(kind, p["ip"], p.get("serial", ""))
        code = p.get({"bambu": "code", "moonraker": "api_key", "prusalink": "password"}[kind], "")
        printers.append({"name": p.get("name") or p.get("serial") or p["ip"], "id": pid, "code": code,
                         "ip": p["ip"], "kind": kind, "source": "env"})
    return printers


def db_printers() -> list[dict]:
    with store.connect() as con:
        return [{"name": r["name"], "id": r["serial"], "code": r["code"], "ip": r["ip"], "kind": r["kind"],
                 "source": "db", "db_id": r["id"]}
                for r in con.execute("SELECT * FROM printers ORDER BY name COLLATE NOCASE, id")]


def load_printers() -> list[dict]:
    """Alle Drucker; bei gleicher Seriennummer gilt die Umgebungsvariable."""
    env = env_printers()
    serials = {p["id"] for p in env}
    return env + [p for p in db_printers() if p["id"] not in serials]


def save_printers(rows: list[dict]) -> None:
    """Speichert die Drucker aus den Einstellungen.

    rows: {"db_id": int|None, "kind", "name", "ip", "serial", "code", "delete": bool}.
    Bei Moonraker ist "code" der optionale API-Schlüssel, bei PrusaLink das Passwort;
    die Seriennummer braucht nur Bambu.
    Leerer Zugangscode bei einem vorhandenen Drucker behält den gespeicherten.
    Wirft UserError mit einer übersetzbaren Meldung, dann wird nichts gespeichert.
    """
    with store.connect() as con:
        existing = {r["id"]: dict(r) for r in con.execute("SELECT * FROM printers")}
        keep, seen = [], set()
        env_ids = {p["id"] for p in env_printers()}
        for row in rows:
            name, ip, serial, code = (str(row.get(k) or "").strip() for k in ("name", "ip", "serial", "code"))
            kind = row.get("kind") if row.get("kind") in KINDS else "bambu"
            old = existing.get(row.get("db_id")) if row.get("db_id") else None
            if row.get("delete") or (not old and not any((name, ip, serial, code))):
                continue
            label = name or serial or ip
            if not code and old and old["kind"] == kind:
                code = old["code"]
            needed = {
                "bambu": (("IP-Adresse", ip), ("Seriennummer", serial), ("Zugangscode", code)),
                "moonraker": (("IP-Adresse", ip),),
                "prusalink": (("IP-Adresse", ip), ("Passwort", code)),
            }[kind]
            for field, value in needed:
                if not value:
                    raise UserError("{label}: {field} fehlt", label=label, field=field) if label else \
                        UserError("Neuer Drucker: {field} fehlt", field=field)
            pid = printer_id(kind, ip, serial)
            if pid in seen:
                if kind != "bambu":
                    raise UserError("Adresse {address} ist doppelt eingetragen", address=printer_url(kind, ip))
                raise UserError("Seriennummer {serial} ist doppelt eingetragen", serial=serial)
            if pid in env_ids:
                if kind != "bambu":
                    raise UserError("{label}: Adresse {address} ist schon über Umgebungsvariablen eingerichtet",
                                    label=label, address=printer_url(kind, ip))
                raise UserError("{label}: Seriennummer {serial} ist schon über Umgebungsvariablen eingerichtet",
                                label=label, serial=serial)
            seen.add(pid)
            keep.append((old["id"] if old else None, name or serial or ip, pid, ip, code, kind))
        kept_ids = {k[0] for k in keep if k[0]}
        for pid in set(existing) - kept_ids:
            con.execute("DELETE FROM printers WHERE id = ?", (pid,))
        # Erst alle Seriennummern freigeben, damit Tauschen zwischen Zeilen klappt
        for pid in kept_ids:
            con.execute("UPDATE printers SET serial = '~' || id WHERE id = ?", (pid,))
        for pid, name, serial, ip, code, kind in keep:
            if pid:
                con.execute("UPDATE printers SET name = ?, serial = ?, ip = ?, code = ?, kind = ? WHERE id = ?",
                            (name, serial, ip, code, kind, pid))
            else:
                con.execute("INSERT INTO printers (name, serial, ip, code, kind) VALUES (?, ?, ?, ?, ?)",
                            (name, serial, ip, code, kind))
    current = {p["id"] for p in load_printers()}
    for serial in set(state["printers"]) - current:
        state["printers"].pop(serial, None)  # Meldungen gelöschter Drucker nicht mehr zeigen
    state["enabled"] = bool(current)
    request_sync()


def init() -> None:
    with store.connect() as con:
        con.executescript(SCHEMA)
        if "kind" not in {r["name"] for r in con.execute("PRAGMA table_info(printers)")}:
            con.execute("ALTER TABLE printers ADD COLUMN kind TEXT NOT NULL DEFAULT 'bambu'")  # vor 0.7.2
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
    if is_gcode(base):
        return base  # einfacher G-Code hat nur eine Platte
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


class FtpSource:
    """Dateiliste und Download über FTPS (Bambu Lab)."""

    def __init__(self, ftp):
        self.ftp = ftp

    def list(self) -> list[dict]:
        lines: list[str] = []
        self.ftp.retrlines("LIST /", lines.append)
        return parse_listing(lines)

    def fetch(self, name: str, fh) -> None:
        self.ftp.retrbinary(f"RETR /{name}", fh.write, blocksize=256 * 1024)

    def close(self) -> None:
        try:
            self.ftp.quit()
        except Exception:  # noqa: BLE001
            self.ftp.close()


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _remote_date(ts: float) -> str:
    """Zeitstempel im Format der Bambu-Dateiliste ('Aug 22 07:06', ältere Jahre 'Nov 29 2025')."""
    t, now = time.localtime(ts), time.localtime()
    rest = f"{t.tm_hour:02d}:{t.tm_min:02d}" if t.tm_year == now.tm_year else str(t.tm_year)
    return f"{_MONTHS[t.tm_mon - 1]} {t.tm_mday:02d} {rest}"


class MoonrakerSource:
    """Dateiliste und Download über die HTTP-Schnittstelle von Moonraker (Klipper)."""

    def __init__(self, printer: dict, opener=urllib.request.urlopen):
        self.base = moonraker_url(printer["ip"])
        self.key = printer.get("code") or ""
        self.opener = opener

    def _get(self, path: str):
        headers = {"X-Api-Key": self.key} if self.key else {}
        return self.opener(urllib.request.Request(self.base + path, headers=headers), timeout=TIMEOUT)

    def list(self) -> list[dict]:
        with self._get("/server/files/list?root=gcodes") as resp:
            data = json.load(resp)
        out = []
        for f in data.get("result") or []:
            path = str(f.get("path") or "")
            if not is_gcode(path) or any(part.startswith(".") for part in path.split("/")):
                continue
            out.append({"name": path, "size": int(f.get("size") or 0),
                        "date": _remote_date(float(f.get("modified") or 0))})
        return out

    def fetch(self, name: str, fh) -> None:
        with self._get("/server/files/gcodes/" + urllib.parse.quote(name)) as resp:
            while block := resp.read(256 * 1024):
                fh.write(block)

    def close(self) -> None:
        pass


class PrusaLinkSource:
    """Dateiliste und Download über PrusaLink (Prusa MK4, MK3.9, XL, Mini, Core One).

    Anmeldung per HTTP-Digest mit Benutzer "maker" und dem Passwort aus den
    Netzwerkeinstellungen des Druckers. Ältere Firmware nutzt stattdessen einen
    API-Schlüssel; das Passwortfeld wird deshalb auch als X-Api-Key mitgeschickt.
    """

    USER = "maker"
    MAX_DEPTH = 4

    def __init__(self, printer: dict, opener=None):
        self.base = printer_url("prusalink", printer["ip"])
        self.secret = printer.get("code") or ""
        if opener is None:
            passwords = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            passwords.add_password(None, self.base, self.USER, self.secret)
            opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(passwords))
        self.opener = opener
        self.downloads: dict[str, str] = {}

    def _get(self, path: str):
        request = urllib.request.Request(urllib.parse.urljoin(self.base + "/", path.lstrip("/")),
                                         headers={"X-Api-Key": self.secret, "Accept": "application/json"})
        return self.opener.open(request, timeout=TIMEOUT)

    def _json(self, path: str) -> dict:
        with self._get(path) as resp:
            return json.load(resp)

    def list(self) -> list[dict]:
        out: list[dict] = []
        for storage in self._json("/api/v1/storage").get("storage_list") or []:
            if storage.get("available") and storage.get("path"):
                self._walk(storage["path"].strip("/"), "", "", out, 0)
        return out

    def _walk(self, storage: str, sub: str, shown: str, out: list, depth: int) -> None:
        folder = self._json("/api/v1/files/" + urllib.parse.quote(f"{storage}/{sub}".rstrip("/")))
        for item in folder.get("children") or []:
            name = str(item.get("name") or "")
            display = str(item.get("display_name") or name)
            if not name or name.startswith("."):
                continue
            if item.get("type") == "FOLDER":
                if depth < self.MAX_DEPTH:
                    self._walk(storage, f"{sub}/{name}".strip("/"), f"{shown}{display}/", out, depth + 1)
                continue
            download = (item.get("refs") or {}).get("download")
            if not is_gcode(display) or not download:
                continue
            self.downloads[shown + display] = download
            out.append({"name": shown + display, "size": int(item.get("size") or 0),
                        "date": _remote_date(float(item.get("m_timestamp") or 0))})

    def fetch(self, name: str, fh) -> None:
        with self._get(self.downloads[name]) as resp:
            while block := resp.read(256 * 1024):
                fh.write(block)

    def close(self) -> None:
        pass


SOURCES = {"moonraker": MoonrakerSource, "prusalink": PrusaLinkSource}


def sync_printer(printer: dict, connect=None) -> dict:
    """Holt neue oder geänderte Dateien eines Druckers. Liefert eine kurze Bilanz."""
    kind = printer.get("kind", "bambu")
    result = {"name": printer["name"], "kind": kind, "ok": False, "new": 0, "updated": 0, "error": None,
              "files": 0, "error_msg": None, "error_args": {}, "auth_failed": False}

    def fail(msg: str, **kwargs) -> None:
        # error: deutscher Text für Log und Tests, error_msg/error_args zum Übersetzen
        result.update(error=msg.format(**kwargs), error_msg=msg, error_args=kwargs)

    def auth_failed(exc) -> None:
        if kind == "moonraker":
            fail("Anmeldung abgelehnt, API-Schlüssel prüfen ({detail})", detail=exc)
        elif kind == "prusalink":
            fail("Anmeldung abgelehnt, Passwort prüfen ({detail})", detail=exc)
        else:
            fail("Anmeldung abgelehnt, Zugangscode prüfen ({detail})", detail=exc)
        result["auth_failed"] = True

    def unreachable(exc) -> None:
        fail("nicht erreichbar, ausgeschaltet oder im Standby? ({detail})", detail=exc.__class__.__name__)

    target_dir = store.LIBRARY_DIR / import_dir() / _local_name(printer["name"])
    connect = connect or SOURCES.get(kind, _connect)
    try:
        source = connect(printer)
        if not hasattr(source, "list"):
            source = FtpSource(source)
    except ftplib.error_perm as exc:
        auth_failed(exc)
        return result
    except (OSError, ftplib.Error, socket.timeout) as exc:
        unreachable(exc)
        return result
    try:
        try:
            remote = source.list()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                auth_failed(exc)
            else:
                fail("Fehler beim Abrufen ({detail})", detail=f"HTTP {exc.code}")
            return result
        except urllib.error.URLError as exc:
            unreachable(exc.reason if isinstance(exc.reason, Exception) else exc)
            return result
        except ValueError as exc:  # keine Moonraker-Antwort, etwa falscher Port
            fail("Fehler beim Abrufen ({detail})", detail=f"{exc.__class__.__name__}: {exc}")
            return result
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
                    source.fetch(f["name"], fh)
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
        fail("Fehler beim Abrufen ({detail})", detail=f"{exc.__class__.__name__}: {exc}")
    finally:
        source.close()
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
    from . import print_history
    print_history.link_pending()  # Drucke den eben geholten Dateien zuordnen


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
