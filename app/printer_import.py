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

OctoPrint: per HTTP aus dem Dateispeicher von OctoPrint (z. B. OctoPi am Ender, Anycubic
oder Prusa MK3). Anmeldung mit einem Application Key aus den OctoPrint-Einstellungen.

Drucker werden unter „Einstellungen“ gepflegt (Tabelle printers) oder über
Umgebungsvariablen vorgegeben:
    PRINTER_1_NAME=P2S  PRINTER_1_IP=192.0.2.20  PRINTER_1_SERIAL=<Seriennummer>  PRINTER_1_CODE=<Zugangscode>
    PRINTER_2_TYPE=moonraker  PRINTER_2_NAME=K1  PRINTER_2_IP=192.0.2.21  PRINTER_2_API_KEY=<optional>
    PRINTER_3_TYPE=prusalink  PRINTER_3_NAME=MK4S  PRINTER_3_IP=192.0.2.22  PRINTER_3_PASSWORD=<Passwort>
    PRINTER_4_TYPE=octoprint  PRINTER_4_NAME=Ender  PRINTER_4_IP=192.0.2.23  PRINTER_4_API_KEY=<Schlüssel>
"""
from __future__ import annotations

import ftplib
import hashlib
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

from . import crypto, store
from .gcode_file import is_gcode
from .i18n import UserError
from .parser import parse_3mf

log = logging.getLogger("plateshelf.printers")

SYNC_INTERVAL = int(os.environ.get("PRINTER_SYNC_INTERVAL", "600"))


def import_dir() -> str:
    """Zielordner für importierte Drucke: PRINTER_IMPORT_DIR, sonst „From printer“."""
    return os.environ.get("PRINTER_IMPORT_DIR", "").strip() or "From printer"


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


_ENV_KEY = re.compile(r"PRINTER_(\d+)_(NAME|IP|SERIAL|CODE|TYPE|API_KEY|PASSWORD)(_FILE)?")
KINDS = ("bambu", "moonraker", "prusalink", "octoprint")
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


def printer_address(kind: str, ip: str) -> str:
    """Kurze Anzeige ohne http:// und ohne Standardport: „192.0.2.21“ oder „k1.local:8080“."""
    if kind == "bambu":
        return ip.strip()
    parts = urllib.parse.urlsplit(printer_url(kind, ip))
    default = MOONRAKER_PORT if kind == "moonraker" else (443 if parts.scheme == "https" else 80)
    host = parts.hostname or ip.strip()
    if ":" in host:  # IPv6
        host = f"[{host}]"
    return host if parts.port in (None, default) else f"{host}:{parts.port}"


PROBE_TIMEOUT = 2.0
PROBE_CACHE_SECONDS = 5
_probe_cache: dict = {"at": 0.0, "result": {}}
_probe_lock = threading.Lock()
_last_resync = 0.0


def _probe_target(printer: dict) -> tuple[str, int]:
    """Host und Port für die schnelle Erreichbarkeitsprüfung: FTPS bei Bambu, sonst die HTTP-Schnittstelle."""
    if printer.get("kind", "bambu") == "bambu":
        return printer["ip"].strip(), 990
    parts = urllib.parse.urlsplit(printer_url(printer["kind"], printer["ip"]))
    return parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)


def probe(printer: dict) -> bool:
    """Antwortet der Drucker? Nur ein Verbindungsaufbau, ohne Anmeldung – dauert höchstens zwei Sekunden."""
    host, port = _probe_target(printer)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def reachability() -> dict[str, bool]:
    """Erreichbarkeit aller Drucker, parallel geprüft und kurz zwischengespeichert (Einstellungsseite
    fragt alle paar Sekunden). Kommt ein Drucker wieder, der beim letzten Abruf fehlte, wird gleich
    abgeglichen, damit Status und Dateien nicht bis zum nächsten Intervall warten."""
    global _last_resync
    with _probe_lock:
        if time.time() - _probe_cache["at"] < PROBE_CACHE_SECONDS:
            return dict(_probe_cache["result"])
        printers = load_printers()
        result: dict[str, bool] = {}

        def check(p):
            result[p["id"]] = probe(p)

        threads = [threading.Thread(target=check, args=(p,), daemon=True) for p in printers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(PROBE_TIMEOUT + 1)
        _probe_cache.update(at=time.time(), result=dict(result))
    back = [pid for pid, ok in result.items()
            if ok and (state["printers"].get(pid) or {}).get("error")]
    if back and not state["running"] and time.time() - _last_resync > 30:
        _last_resync = time.time()
        request_sync()
    return result


def printer_id(kind: str, ip: str, serial: str) -> str:
    """Eindeutige Kennung: Seriennummer bei Bambu, sonst Art und Adresse."""
    return serial if kind == "bambu" else f"{kind}:{printer_url(kind, ip)}"


def env_printers(environ=None) -> list[dict]:
    """Drucker aus PRINTER_<n>_NAME/_IP/_SERIAL/_CODE/_TYPE/_API_KEY/_PASSWORD, sortiert nach <n>.

    Jeder Wert geht auch als Datei: PRINTER_<n>_CODE_FILE=/run/secrets/… (Docker-Secrets).
    """
    environ = os.environ if environ is None else environ
    found: dict[int, dict] = {}
    for key in environ:
        if m := _ENV_KEY.fullmatch(key):
            name = key[:-5] if m[3] else key
            value = crypto.env_value(name, environ)
            if value:
                found.setdefault(int(m[1]), {})[m[2].lower()] = value
    printers = []
    for n in sorted(found):
        p = found[n]
        kind = (p.get("type") or "bambu").lower()
        if kind not in KINDS:
            log.warning("PRINTER_%d_TYPE=%s unbekannt, möglich sind %s", n, kind, ", ".join(KINDS))
            continue
        needed = {"bambu": ("ip", "serial", "code"), "moonraker": ("ip",), "prusalink": ("ip", "password"),
                  "octoprint": ("ip", "api_key")}[kind]
        missing = [k for k in needed if not p.get(k)]
        if missing:
            log.warning("PRINTER_%d unvollständig, es fehlt %s", n, ", ".join(f"PRINTER_{n}_{k.upper()}" for k in missing))
            continue
        pid = printer_id(kind, p["ip"], p.get("serial", ""))
        code = p.get({"bambu": "code", "moonraker": "api_key", "prusalink": "password", "octoprint": "api_key"}[kind], "")
        printers.append({"name": p.get("name") or p.get("serial") or p["ip"], "id": pid, "code": code,
                         "ip": p["ip"], "kind": kind, "source": "env"})
    return printers


def db_printers() -> list[dict]:
    """Drucker aus den Einstellungen; Zugangsdaten entschlüsselt (code_error, wenn das nicht geht)."""
    out = []
    with store.connect() as con:
        for r in con.execute("SELECT * FROM printers ORDER BY name COLLATE NOCASE, id"):
            p = {"name": r["name"], "id": r["serial"], "ip": r["ip"], "kind": r["kind"], "source": "db",
                 "db_id": r["id"], "code_error": None}
            try:
                p["code"] = crypto.decrypt(r["code"])
            except crypto.SecretError as exc:
                p["code"], p["code_error"] = "", str(exc)
            out.append(p)
    return out


def stored_plaintext() -> int:
    """Wie viele Zugangsdaten noch unverschlüsselt gespeichert sind (ohne SECRET_KEY)."""
    with store.connect() as con:
        return sum(1 for (code,) in con.execute("SELECT code FROM printers") if code and not crypto.is_encrypted(code))


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
            stored = crypto.encrypt(code)
            if not code and old and old["kind"] == kind:
                code = stored = old["code"]  # unverändert übernehmen, wie gespeichert
            needed = {
                "bambu": (("IP-Adresse", ip), ("Seriennummer", serial), ("Zugangscode", code)),
                "moonraker": (("IP-Adresse", ip),),
                "prusalink": (("IP-Adresse", ip), ("Passwort", code)),
                "octoprint": (("IP-Adresse", ip), ("API-Schlüssel", code)),
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
            keep.append((old["id"] if old else None, name or serial or ip, pid, ip, stored, kind))
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
    """Tabellen liegen im gemeinsamen Schema (store.SCHEMA); hier nur Zugangsdaten verschlüsseln."""
    store.init_db()
    with store.connect() as con:
        if crypto.secret_key():  # Klartext aus älteren Versionen oder ohne SECRET_KEY gespeichert
            plain = [(r["id"], r["code"]) for r in con.execute("SELECT id, code FROM printers")
                     if r["code"] and not crypto.is_encrypted(r["code"])]
            for pid, code in plain:
                con.execute("UPDATE printers SET code = ? WHERE id = ?", (crypto.encrypt(code), pid))
            if plain:
                log.info("%d Zugangsdaten von Druckern verschlüsselt", len(plain))
    state["enabled"] = bool(load_printers())


def parse_listing(lines: list[str], sliced_3mf: bool = False) -> list[dict]:
    """Dateien, die auf .gcode.3mf enden; mit sliced_3mf auch .3mf (im Ordner cache sind das
    geslicte Projekte, die Bambu Studio zum Drucken geschickt hat)."""
    out = []
    for line in lines:
        m = _LIST_RE.match(line.strip())
        if not m or m["type"] != "-":
            continue
        name = m["name"].strip()
        if name.lower().endswith(".gcode.3mf") or (sliced_3mf and name.lower().endswith(".3mf")):
            out.append({"name": name, "size": int(m["size"]), "date": " ".join(m["date"].split())})
    return out


def same_date(stored: str | None, listed: str) -> bool:
    """Gleiches Datum, auch wenn die Dateiliste inzwischen das Jahr statt der Uhrzeit zeigt.

    FTP-Listen (und _remote_date) zeigen bei Dateien aus den letzten Monaten bzw. dem
    laufenden Jahr die Uhrzeit, danach das Jahr: aus „Apr 03 18:18“ wird „Apr 03 2026“.
    Das ist keine neue Datei und darf sie nicht noch einmal holen.
    """
    if stored == listed:
        return True
    a, b = (stored or "").split(), listed.split()
    if len(a) != 3 or len(b) != 3 or a[:2] != b[:2]:
        return False
    return {":" in a[2], ":" in b[2]} == {True, False}


def parse_remote_date(text: str, now: float | None = None) -> float | None:
    """Zeitpunkt zu „Apr 03 18:18“ (dieses Jahr, sonst letztes) oder „Apr 03 2026“ (mittags)."""
    parts = (text or "").split()
    if len(parts) != 3 or parts[0] not in _MONTHS or not parts[1].isdigit():
        return None
    month, day = _MONTHS.index(parts[0]) + 1, int(parts[1])
    now = time.time() if now is None else now
    try:
        if ":" in parts[2]:
            hour, minute = (int(x) for x in parts[2].split(":"))
            year = time.localtime(now).tm_year
            ts = time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))
            if ts > now + 2 * 86400:  # ohne Jahr und in der Zukunft: aus dem Vorjahr
                ts = time.mktime((year - 1, month, day, hour, minute, 0, 0, 0, -1))
            return ts
        return time.mktime((int(parts[2]), month, day, 12, 0, 0, 0, 0, -1))
    except (ValueError, OverflowError):
        return None


def _keep_printer_date(path: Path, date: str) -> None:
    """Datei trägt das Datum, an dem sie auf dem Drucker landete, nicht das des Herunterladens.

    So steht ein altes Projekt unter „Neueste zuerst“ nicht vorne, nur weil es (wieder)
    geholt wurde. Repariert auch Dateien, die frühere Versionen unnötig neu geholt haben.
    """
    ts = parse_remote_date(date)
    try:
        if ts and path.stat().st_mtime > ts + 86400:
            os.utime(path, (ts, ts))
    except OSError:
        pass


def _local_name(remote: str) -> str:
    return _SAFE_NAME.sub("_", remote).strip(" .") or "Unbenannt.gcode.3mf"


def _plate_file_name(remote: str, downloaded: Path) -> str:
    """Name der lokalen Datei, mit der gedruckten Platte im Namen.

    Der Drucker speichert jedes Projekt unter demselben Namen und überschreibt es,
    wenn eine andere Platte gedruckt wird. Mit der Plattennummer im Namen bleibt
    jede gedruckte Platte als eigene Datei erhalten.
    """
    base = _local_name(remote.removeprefix(FtpSource.CACHE + "/"))
    if is_gcode(base):
        return base  # einfacher G-Code hat nur eine Platte
    try:
        parsed = parse_3mf(downloaded)
    except Exception:  # noqa: BLE001
        return base
    if not base.lower().endswith(".gcode.3mf"):
        if not any(p.sliced for p in parsed.plates):
            return base  # ungeslictes Projekt: Name lassen
        base = base[:-4] + ".gcode.3mf"  # geslict aus dem Ordner cache: wie die übrigen Druckdateien
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

    # Was Bambu Studio oder die Cloud zum Drucken schickt, legt die P1- und A1-Serie in /cache ab
    # (als geslicte .3mf, daneben ausgepackter G-Code und .bbl, die braucht es nicht)
    CACHE = "cache"

    def list(self) -> list[dict]:
        lines: list[str] = []
        self.ftp.retrlines("LIST /", lines.append)
        files = parse_listing(lines)
        cached: list[str] = []
        try:
            self.ftp.retrlines(f"LIST /{self.CACHE}", cached.append)
        except ftplib.error_perm:
            return files  # kein Ordner cache auf dieser Karte
        for f in parse_listing(cached, sliced_3mf=True):
            files.append({**f, "name": f"{self.CACHE}/{f['name']}"})
        return files

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
        url = urllib.parse.urljoin(self.base + "/", path.lstrip("/"))
        request = urllib.request.Request(url, headers={"X-Api-Key": self.secret, "Accept": "application/json"})
        try:
            return self.opener.open(request, timeout=TIMEOUT)
        except ValueError as exc:
            # Firmware im API-Schlüssel-Modus lehnt mit „WWW-Authenticate: ApiKey“ ab; urllib kennt
            # das Verfahren nicht und wirft ValueError statt 401
            if "scheme" in str(exc):
                raise urllib.error.HTTPError(url, 401, "Unauthorized", None, None) from exc
            raise

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
            expected = int(resp.headers.get("Content-Length") or 0)
            got = 0
            while block := resp.read(256 * 1024):
                fh.write(block)
                got += len(block)
        if expected and got != expected:
            raise OSError(f"unvollständig ({got} von {expected} Byte)")

    def close(self) -> None:
        pass


class OctoPrintSource:
    """Dateiliste und Download aus dem lokalen Dateispeicher von OctoPrint (Application Key)."""

    def __init__(self, printer: dict, opener=urllib.request.urlopen):
        self.base = printer_url("octoprint", printer["ip"])
        self.key = printer.get("code") or ""
        self.opener = opener

    def _get(self, path: str):
        request = urllib.request.Request(self.base + path, headers={"X-Api-Key": self.key, "Accept": "application/json"})
        return self.opener(request, timeout=TIMEOUT)

    def list(self) -> list[dict]:
        with self._get("/api/files/local?recursive=true") as resp:
            data = json.load(resp)
        out: list[dict] = []
        self._walk(data.get("files") or [], out)
        return out

    def _walk(self, items: list, out: list) -> None:
        for item in items:
            path = str(item.get("path") or "")
            if not path or any(part.startswith(".") for part in path.split("/")):
                continue
            if item.get("type") == "folder":
                self._walk(item.get("children") or [], out)
            elif is_gcode(path):
                out.append({"name": path, "size": int(item.get("size") or 0),
                            "date": _remote_date(float(item.get("date") or 0))})

    def fetch(self, name: str, fh) -> None:
        with self._get("/downloads/files/local/" + urllib.parse.quote(name)) as resp:
            expected = int(resp.headers.get("Content-Length") or 0)
            got = 0
            while block := resp.read(256 * 1024):
                fh.write(block)
                got += len(block)
        if expected and got != expected:
            raise OSError(f"unvollständig ({got} von {expected} Byte)")

    def close(self) -> None:
        pass


SOURCES = {"moonraker": MoonrakerSource, "prusalink": PrusaLinkSource, "octoprint": OctoPrintSource}


def sync_printer(printer: dict, connect=None) -> dict:
    """Holt neue oder geänderte Dateien eines Druckers. Liefert eine kurze Bilanz."""
    kind = printer.get("kind", "bambu")
    result = {"name": printer["name"], "kind": kind, "ok": False, "new": 0, "updated": 0, "error": None,
              "files": 0, "error_msg": None, "error_args": {}, "auth_failed": False}

    def fail(msg: str, **kwargs) -> None:
        # error: deutscher Text für Log und Tests, error_msg/error_args zum Übersetzen
        result.update(error=msg.format(**kwargs), error_msg=msg, error_args=kwargs)

    def auth_failed(exc) -> None:
        if kind in ("moonraker", "octoprint"):
            fail("Anmeldung abgelehnt, API-Schlüssel prüfen ({detail})", detail=exc)
        elif kind == "prusalink":
            fail("Anmeldung abgelehnt, Passwort prüfen ({detail})", detail=exc)
        else:
            fail("Anmeldung abgelehnt, Zugangscode prüfen ({detail})", detail=exc)
        result["auth_failed"] = True

    def unreachable(exc) -> None:
        fail("nicht erreichbar, ausgeschaltet oder im Standby? ({detail})", detail=exc.__class__.__name__)

    if printer.get("code_error"):
        fail("Zugangsdaten lassen sich nicht entschlüsseln ({detail}). Wurde SECRET_KEY geändert? "
             "Dann in den Einstellungen neu eingeben.", detail=printer["code_error"])
        result["auth_failed"] = True
        return result
    target_dir = store.LIBRARY_DIR / import_dir() / _local_name(printer["name"])
    connect = connect or SOURCES.get(kind, _connect)
    try:
        source = connect(printer)
        if not hasattr(source, "list"):
            source = FtpSource(source)
    except ftplib.error_perm as exc:
        auth_failed(exc)
        return result
    except (TimeoutError, OSError, ftplib.Error) as exc:
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
        # Neueste zuerst: beim ersten Abgleich mit vielen Dateien ist der laufende Druck schnell da
        remote.sort(key=lambda f: parse_remote_date(f["date"]) or 0, reverse=True)
        with store.connect() as con:
            known = {r["remote_name"]: r for r in con.execute(
                "SELECT remote_name, size, remote_date, local_path FROM printer_files WHERE printer = ?",
                (printer["id"],))}
        for f in remote:
            prev = known.get(f["name"])
            if prev and prev["size"] == f["size"] and same_date(prev["remote_date"], f["date"]):
                if prev["remote_date"] != f["date"]:  # nur die Schreibweise hat sich geändert
                    with store.connect() as con:
                        con.execute("UPDATE printer_files SET remote_date = ? WHERE printer = ? AND remote_name = ?",
                                    (f["date"], printer["id"], f["name"]))
                if prev["local_path"]:
                    _keep_printer_date(store.LIBRARY_DIR / prev["local_path"], f["date"])
                continue  # schon geholt (oder bewusst gelöscht): nichts tun
            target_dir.mkdir(parents=True, exist_ok=True)
            tmp = target_dir / f".{_local_name(f['name'])}.part"
            same = None
            try:
                with open(tmp, "wb") as fh:
                    source.fetch(f["name"], fh)
                # Manche Drucker (Prusa MINI) nennen in der Liste keine Größe: dann prüft die Quelle selbst
                if f["size"] and tmp.stat().st_size != f["size"]:
                    raise OSError(f"unvollständig ({tmp.stat().st_size} von {f['size']} Byte)")
                if f["name"].startswith(FtpSource.CACHE + "/"):
                    same = _same_file(tmp)  # P2S legt dieselbe Datei auch im Hauptverzeichnis ab
                if same:
                    tmp.unlink()
                else:
                    final = target_dir / _plate_file_name(f["name"], tmp)
                    os.replace(tmp, final)
                    _keep_printer_date(final, f["date"])
            except (OSError, ftplib.Error) as exc:
                log.warning("%s: %s konnte nicht geholt werden: %s", printer["name"], f["name"], exc)
                tmp.unlink(missing_ok=True)
                continue
            rel = same or final.relative_to(store.LIBRARY_DIR).as_posix()
            with store.connect() as con:
                con.execute(
                    """INSERT INTO printer_files (printer, remote_name, size, remote_date, local_path, printer_name, imported)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(printer, remote_name) DO UPDATE SET size = excluded.size,
                         remote_date = excluded.remote_date, local_path = excluded.local_path,
                         printer_name = excluded.printer_name, imported = excluded.imported""",
                    (printer["id"], f["name"], f["size"], f["date"], rel, printer["name"], time.time()),
                )
                if same:
                    continue  # nur verknüpft: Drucke dieser Datei landen beim vorhandenen Projekt
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
    except (TimeoutError, OSError, ftplib.Error) as exc:
        fail("Fehler beim Abrufen ({detail})", detail=f"{exc.__class__.__name__}: {exc}")
    finally:
        source.close()
    return result


def _digest(path: Path) -> str:
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _same_file(path: Path) -> str | None:
    """Pfad einer schon vorhandenen Datei mit genau diesem Inhalt."""
    digest = _digest(path)
    with store.connect() as con:
        row = con.execute("SELECT path FROM files WHERE sha256 = ? AND missing = 0 ORDER BY added LIMIT 1",
                          (digest,)).fetchone()
    return row["path"] if row else None


def dedupe_cache_imports() -> int:
    """0.15.2 hat aus dem Ordner cache auch Dateien geholt, die schon aus dem Hauptverzeichnis da
    waren (der P2S legt beide ab). Solche Doppel entfernen und ihre Drucke auf das Original umhängen."""
    removed = 0
    with store.connect() as con:
        rows = con.execute(
            """SELECT pf.printer, pf.remote_name, pf.local_path, f.sha256 FROM printer_files pf
               JOIN files f ON f.path = pf.local_path AND f.missing = 0
               WHERE pf.remote_name LIKE ? AND f.sha256 IS NOT NULL""", (FtpSource.CACHE + "/%",)).fetchall()
        for r in rows:
            if con.execute("SELECT 1 FROM printer_files WHERE local_path = ? AND remote_name NOT LIKE ?",
                           (r["local_path"], FtpSource.CACHE + "/%")).fetchone():
                continue  # ist selbst das Original (schon umgehängt)
            orig = con.execute("""SELECT path FROM files WHERE sha256 = ? AND missing = 0 AND path != ?
                                  AND path NOT IN (SELECT local_path FROM printer_files WHERE remote_name LIKE ?
                                                   AND local_path IS NOT NULL)
                                  ORDER BY added LIMIT 1""",
                               (r["sha256"], r["local_path"], FtpSource.CACHE + "/%")).fetchone()
            if not orig or not (store.LIBRARY_DIR / orig["path"]).exists():
                continue
            (store.LIBRARY_DIR / r["local_path"]).unlink(missing_ok=True)
            con.execute("UPDATE printer_files SET local_path = ? WHERE local_path = ?", (orig["path"], r["local_path"]))
            con.execute("UPDATE print_jobs SET local_path = ? WHERE local_path = ?", (orig["path"], r["local_path"]))
            con.execute("DELETE FROM printer_imports WHERE local_path = ?", (r["local_path"],))
            removed += 1
    if removed:
        log.info("%d doppelte Dateien aus dem Ordner cache entfernt", removed)
    return removed


def sync_all() -> None:
    if not _lock.acquire(blocking=False):
        return
    state["running"] = True
    changed = False
    if dedupe_cache_imports():
        store.scan()
    try:
        printers = load_printers()
        for serial in set(state["printers"]) - {p["id"] for p in printers}:
            state["printers"].pop(serial, None)
        results: dict[str, dict] = {}

        def one(printer):  # alle Drucker gleichzeitig: ein ausgeschalteter hält die anderen nicht auf
            res = sync_printer(printer)
            res["at"] = time.time()
            results[printer["id"]] = res
            state["printers"][printer["id"]] = res

        threads = [threading.Thread(target=one, args=(p,), daemon=True) for p in printers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(TIMEOUT * 4 + 60)  # große Downloads dürfen dauern, hängen darf nichts ewig
        for printer in printers:
            res = results.get(printer["id"])
            if not res:
                continue
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
