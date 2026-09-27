# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Druckverlauf: wann welche Datei auf welchem Drucker gedruckt wurde und wie es ausging.

Quellen je Druckertyp:
  Bambu Lab   dauerhafte MQTT-Verbindung zum Drucker (Port 8883, Benutzer "bblp",
              Zugangscode). Der Drucker meldet seinen Zustand; aus den Wechseln
              zwischen RUNNING, FINISH und FAILED entstehen die Einträge.
  Klipper     Moonraker führt selbst einen Verlauf (/server/history/list).
  Prusa       PrusaLink kennt nur den aktuellen Druck (/api/v1/job); er wird
              regelmäßig abgefragt und hier mitgeschrieben.

Ein Druck wird über den Dateinamen auf dem Drucker der importierten Datei
zugeordnet (Tabelle printer_files) und damit ihrem Projekt.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
import uuid

from . import printer_import, store

log = logging.getLogger("plateshelf.history")

FINAL = ("completed", "cancelled", "failed")
_lock = threading.Lock()
_wakeup = threading.Event()


def env_setting() -> bool | None:
    """PRINT_HISTORY aus der Umgebung; None, wenn nicht gesetzt (dann gilt die Einstellung)."""
    value = os.environ.get("PRINT_HISTORY", "").strip().lower()
    if not value:
        return None
    return value not in ("0", "false", "no", "off")


def enabled() -> bool:
    env = env_setting()
    if env is not None:
        return env
    return bool(store.get_settings().get("druckverlauf", 1))


def set_enabled(on: bool) -> None:
    """Schalter aus den Einstellungen; die Verbindungen passen sich sofort an."""
    store.save_settings({"druckverlauf": 1 if on else 0})
    _wakeup.set()


def interval() -> int:
    return max(15, int(os.environ.get("PRINT_HISTORY_INTERVAL", "60")))


# --- Speichern ----------------------------------------------------------------

def _resolve(con, printer_id: str, remote_name: str | None) -> str | None:
    """Lokale Datei zu einem Dateinamen auf dem Drucker (auch nur der letzte Teil des Pfads)."""
    if not remote_name:
        return None
    row = con.execute("SELECT local_path FROM printer_files WHERE printer = ? AND remote_name = ?",
                      (printer_id, remote_name)).fetchone()
    if not row:
        base = remote_name.rsplit("/", 1)[-1]
        row = con.execute("SELECT local_path FROM printer_files WHERE printer = ? AND (remote_name = ? OR "
                          "remote_name LIKE ?) ORDER BY imported DESC", (printer_id, base, "%/" + base)).fetchone()
    return row["local_path"] if row and row["local_path"] else None


def _apply_project_status(con, job) -> None:
    """Ein fertiger Druck setzt ein Projekt ohne Status auf „gedruckt“, einmal pro Druck."""
    if job["status"] != "completed" or not job["local_path"] or job["applied"] or job["hidden"]:
        return
    con.execute("""UPDATE projects SET status = 'gedruckt', updated = ? WHERE status = '' AND id =
                   (SELECT project_id FROM files WHERE path = ? AND missing = 0)""", (time.time(), job["local_path"]))
    con.execute("UPDATE print_jobs SET applied = 1 WHERE id = ?", (job["id"],))


def record(printer: dict, job_key: str, status: str, *, remote_name: str | None = None,
           started: float | None = None, ended: float | None = None, duration_s: float | None = None,
           filament_mm: float | None = None, progress: float | None = None,
           remaining_s: float | None = None) -> None:
    """Legt einen Druck an oder schreibt seinen neuen Stand fort."""
    now = time.time()
    with _lock, store.connect() as con:
        row = con.execute("SELECT * FROM print_jobs WHERE printer = ? AND job_key = ?",
                          (printer["id"], job_key)).fetchone()
        if row and row["status"] in FINAL and status == "running":
            return  # schon abgeschlossen, verspätete Meldung
        local = (row["local_path"] if row else None) or _resolve(con, printer["id"], remote_name)
        if row:
            values = {"status": status, "local_path": local, "printer_name": printer["name"], "updated": now}
            if remote_name:
                values["remote_name"] = remote_name
            for key, value in (("duration_s", duration_s), ("filament_mm", filament_mm), ("progress", progress),
                               ("remaining_s", remaining_s)):
                if value is not None:
                    values[key] = value
            if status in FINAL:
                values["remaining_s"] = None
            if status in FINAL and not row["ended"]:
                values["ended"] = ended or now
            sets = ", ".join(f"{k} = ?" for k in values)
            con.execute(f"UPDATE print_jobs SET {sets} WHERE id = ?", (*values.values(), row["id"]))
        else:
            con.execute(
                """INSERT INTO print_jobs (printer, printer_name, job_key, remote_name, local_path, status,
                       started, ended, duration_s, filament_mm, progress, remaining_s, updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (printer["id"], printer["name"], job_key, remote_name, local, status, started or now,
                 (ended or now) if status in FINAL else None, duration_s, filament_mm, progress,
                 None if status in FINAL else remaining_s, now))
        job = con.execute("SELECT * FROM print_jobs WHERE printer = ? AND job_key = ?",
                          (printer["id"], job_key)).fetchone()
        _apply_project_status(con, job)


def delete(job_id: int) -> None:
    """Druck aus dem Verlauf entfernen, etwa einen Testdruck.

    Der Eintrag bleibt als verborgen gespeichert, damit Moonraker oder PrusaLink ihn
    beim nächsten Abruf nicht wieder anlegen. Hatte er den Projektstatus auf
    „gedruckt“ gesetzt und bleibt kein anderer fertiger Druck des Projekts übrig,
    wird der Status zurückgenommen.
    """
    with _lock, store.connect() as con:
        job = con.execute("SELECT * FROM print_jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise KeyError(job_id)
        con.execute("UPDATE print_jobs SET hidden = 1 WHERE id = ?", (job_id,))
        if not (job["applied"] and job["local_path"]):
            return
        project = con.execute("SELECT project_id FROM files WHERE path = ? AND missing = 0",
                              (job["local_path"],)).fetchone()
        if not project:
            return
        others = con.execute(
            """SELECT COUNT(*) FROM print_jobs j JOIN files f ON f.path = j.local_path AND f.missing = 0
               WHERE f.project_id = ? AND j.status = 'completed' AND j.hidden = 0""", (project[0],)).fetchone()[0]
        if not others:
            con.execute("UPDATE projects SET status = '', updated = ? WHERE id = ? AND status = 'gedruckt'",
                        (time.time(), project[0]))


LIVE_MAX_AGE = 30 * 60  # ohne Meldung seit 30 Minuten gilt ein Druck nicht mehr als live


def printing_now() -> list[dict]:
    """Laufende Drucke mit frischer Meldung, für Titelleiste, Übersicht und Dashboard."""
    with store.connect() as con:
        return [dict(r) for r in con.execute(
            """SELECT j.id, j.printer_name, j.remote_name, j.progress, j.remaining_s, j.started, j.local_path,
                      f.project_id, p.name AS project_name
               FROM print_jobs j
               LEFT JOIN files f ON f.path = j.local_path AND f.missing = 0
               LEFT JOIN projects p ON p.id = f.project_id
               WHERE j.status = 'running' AND j.hidden = 0 AND COALESCE(j.updated, j.started) > ?
               ORDER BY j.started""", (time.time() - LIVE_MAX_AGE,))]


def running_job(printer_id: str):
    with store.connect() as con:
        return con.execute("SELECT * FROM print_jobs WHERE printer = ? AND status = 'running' "
                           "ORDER BY started DESC", (printer_id,)).fetchone()


def link_pending() -> None:
    """Drucke ohne Datei nachträglich zuordnen, sobald der Import sie geholt hat."""
    with _lock, store.connect() as con:
        for job in con.execute("SELECT * FROM print_jobs WHERE local_path IS NULL AND remote_name IS NOT NULL "
                               "AND started > ?", (time.time() - 30 * 86400,)).fetchall():
            if local := _resolve(con, job["printer"], job["remote_name"]):
                con.execute("UPDATE print_jobs SET local_path = ? WHERE id = ?", (local, job["id"]))
                _apply_project_status(con, {**dict(job), "local_path": local})


# --- Abfragen ----------------------------------------------------------------

def jobs_for(paths: list[str]) -> list[dict]:
    if not paths:
        return []
    with store.connect() as con:
        marks = ",".join("?" * len(paths))
        return [dict(r) for r in con.execute(
            f"SELECT * FROM print_jobs WHERE local_path IN ({marks}) AND hidden = 0 ORDER BY started DESC", paths)]


def recent(limit: int = 200) -> list[dict]:
    with store.connect() as con:
        return [dict(r) for r in con.execute(
            """SELECT j.*, f.project_id, p.name AS project_name FROM print_jobs j
               LEFT JOIN files f ON f.path = j.local_path AND f.missing = 0
               LEFT JOIN projects p ON p.id = f.project_id
               WHERE j.hidden = 0 ORDER BY j.started DESC LIMIT ?""", (limit,))]


# --- Klipper (Moonraker) --------------------------------------------------------

MOONRAKER_STATUS = {"completed": "completed", "cancelled": "cancelled", "in_progress": "running"}


def poll_moonraker(printer: dict, source=None) -> int:
    """Übernimmt die letzten Einträge aus dem Verlauf von Moonraker."""
    source = source or printer_import.MoonrakerSource(printer)
    with source._get("/server/history/list?limit=50&order=desc") as resp:
        jobs = (json.load(resp).get("result") or {}).get("jobs") or []
    live = {}
    if any(job.get("status") == "in_progress" for job in jobs):
        live = _moonraker_progress(source)
    for job in jobs:
        running = job.get("status") == "in_progress"
        record(printer, str(job.get("job_id")), MOONRAKER_STATUS.get(job.get("status"), "failed"),
               remote_name=job.get("filename"), started=job.get("start_time"), ended=job.get("end_time"),
               duration_s=job.get("print_duration"), filament_mm=job.get("filament_used"),
               **(live if running else {}))
    return len(jobs)


def _moonraker_progress(source) -> dict:
    """Fortschritt und geschätzte Restzeit des laufenden Drucks aus Klipper."""
    try:
        with source._get("/printer/objects/query?virtual_sdcard&print_stats") as resp:
            status = (json.load(resp).get("result") or {}).get("status") or {}
    except (OSError, ValueError):
        return {}
    fraction = float((status.get("virtual_sdcard") or {}).get("progress") or 0)
    printed = float((status.get("print_stats") or {}).get("print_duration") or 0)
    out = {"progress": round(fraction * 100, 1)}
    if fraction >= 0.02 and printed > 0:  # am Anfang ist die Hochrechnung zu ungenau
        out["remaining_s"] = printed / fraction - printed
    return out


# --- Prusa (PrusaLink) ----------------------------------------------------------

PRUSALINK_STATUS = {"PRINTING": "running", "PAUSED": "running", "FINISHED": "completed",
                    "STOPPED": "cancelled", "ERROR": "failed"}


def poll_prusalink(printer: dict, source=None) -> None:
    """Aktuellen Druck abfragen; ohne Druck antwortet PrusaLink mit 204 und leerem Inhalt."""
    source = source or printer_import.PrusaLinkSource(printer)
    with source._get("/api/v1/job") as resp:
        body = resp.read()
    if not body.strip():
        return
    job = json.loads(body)
    status = PRUSALINK_STATUS.get(str(job.get("state") or "").upper())
    if not status or job.get("id") is None:
        return
    file = job.get("file") or {}
    name = file.get("display_name") or file.get("name")
    printing = job.get("time_printing")
    record(printer, str(job["id"]), status, remote_name=name, duration_s=printing,
           started=time.time() - printing if printing else None,
           progress=job.get("progress"), remaining_s=job.get("time_remaining"))


# --- Bambu Lab (MQTT) -----------------------------------------------------------

BAMBU_ACTIVE = ("PREPARE", "RUNNING", "PAUSE", "SLICING")
BAMBU_CANCELLED = (0, 50348044)  # 0300400C: vom Nutzer abgebrochen


class BambuTracker:
    """Wertet die Zustandsmeldungen eines Bambu-Druckers aus (P1/A1 schicken nur Änderungen)."""

    def __init__(self, printer: dict):
        self.printer = printer
        self.state: dict = {}
        self.job_key: str | None = None
        self.progress = None  # zuletzt gespeichert: (Prozent, Restminuten)

    def _key(self, remote: str) -> str:
        for field in ("subtask_id", "task_id"):
            value = str(self.state.get(field) or "")
            if value and value != "0":
                return f"{field}:{value}"
        job = running_job(self.printer["id"])
        if job and job["remote_name"] == remote:
            return job["job_key"]  # nach einem Neustart von PlateShelf weiterführen
        return f"{remote}@{int(time.time())}"

    def update(self, message: dict) -> None:
        report = message.get("print")
        if not isinstance(report, dict):
            return
        self.state.update(report)
        gstate = str(self.state.get("gcode_state") or "").upper()
        remote = str(self.state.get("gcode_file") or self.state.get("subtask_name") or "").rsplit("/", 1)[-1]
        if gstate in BAMBU_ACTIVE:
            minutes = self.state.get("mc_remaining_time")
            remaining = minutes * 60 if isinstance(minutes, (int, float)) and minutes >= 0 else None
            current = (self.state.get("mc_percent"), minutes)
            if self.job_key is None:
                self.job_key = self._key(remote)
                printer_import.request_sync()  # Datei holen, damit der Druck ihr zugeordnet werden kann
                record(self.printer, self.job_key, "running", remote_name=remote or None,
                       progress=current[0], remaining_s=remaining)
                self.progress = current
            elif current != self.progress:  # höchstens etwa einmal pro Minute
                self.progress = current
                record(self.printer, self.job_key, "running", progress=current[0], remaining_s=remaining)
        elif gstate in ("FINISH", "FAILED"):
            key = self.job_key
            if key is None and (job := running_job(self.printer["id"])):
                key = job["job_key"]  # Druck lief, während PlateShelf aus war
            if key is None:
                return
            if gstate == "FINISH":
                status = "completed"
            else:
                status = "cancelled" if int(self.state.get("print_error") or 0) in BAMBU_CANCELLED else "failed"
            record(self.printer, key, status, progress=self.state.get("mc_percent"))
            self.job_key = None
        elif gstate == "IDLE":
            self.job_key = None


class BambuWatcher:
    """MQTT-Verbindung zu einem Bambu-Drucker; baut sich bei Abbrüchen selbst wieder auf."""

    def __init__(self, printer: dict):
        import paho.mqtt.client as mqtt
        self.printer = printer
        self.tracker = BambuTracker(printer)
        serial = printer["id"]
        self.topic = f"device/{serial}/report"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"plateshelf-{uuid.uuid4().hex[:8]}")
        self.client.username_pw_set("bblp", printer["code"])
        self.client.tls_set(cert_reqs=ssl.CERT_NONE)  # selbstsigniertes Zertifikat des Druckers
        self.client.tls_insecure_set(True)
        self.client.reconnect_delay_set(5, 300)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, _userdata, _flags, reason, _props=None):
        if reason.is_failure:
            log.info("%s: MQTT-Verbindung abgelehnt (%s)", self.printer["name"], reason)
            return
        client.subscribe(self.topic)
        # Vollständigen Zustand anfordern, danach kommen nur Änderungen
        client.publish(f"device/{self.printer['id']}/request",
                       json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

    def _on_message(self, _client, _userdata, msg):
        try:
            self.tracker.update(json.loads(msg.payload))
        except Exception:  # noqa: BLE001
            log.exception("%s: Meldung nicht auswertbar", self.printer["name"])

    def start(self) -> None:
        self.client.connect_async(self.printer["ip"], 8883, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()


_watchers: dict[str, tuple[tuple, BambuWatcher]] = {}


def refresh_watchers(printers: list[dict]) -> None:
    """MQTT-Verbindungen an die eingerichteten Bambu-Drucker anpassen."""
    wanted = {p["id"]: p for p in printers if p.get("kind", "bambu") == "bambu" and not p.get("code_error")} \
        if enabled() else {}
    for pid in list(_watchers):
        config, watcher = _watchers[pid]
        p = wanted.get(pid)
        if not p or (p["ip"], p["code"], p["name"]) != config:
            watcher.stop()
            del _watchers[pid]
    for pid, p in wanted.items():
        if pid not in _watchers:
            watcher = BambuWatcher(p)
            watcher.start()
            _watchers[pid] = ((p["ip"], p["code"], p["name"]), watcher)


def poll_once() -> None:
    printers = printer_import.load_printers()
    refresh_watchers(printers)
    for p in printers:
        if p.get("code_error"):
            continue  # Zugangsdaten nicht lesbar, der Import meldet das in den Einstellungen
        try:
            if p["kind"] == "moonraker":
                poll_moonraker(p)
            elif p["kind"] == "prusalink":
                poll_prusalink(p)
        except Exception as exc:  # noqa: BLE001  (nicht erreichbar: der Import meldet das schon)
            log.debug("%s: Verlauf nicht abrufbar: %s", p["name"], exc)


def loop() -> None:
    while True:
        try:
            if enabled():
                poll_once()
            else:
                refresh_watchers([])  # ausgeschaltet: MQTT-Verbindungen schließen
        except Exception:  # noqa: BLE001
            log.exception("Druckverlauf fehlgeschlagen")
        _wakeup.wait(interval())
        _wakeup.clear()
