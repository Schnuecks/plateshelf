# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Print history: which file was printed on which printer, when, and how it turned out.

Sources per printer type:
  Bambu Lab   persistent MQTT connection to the printer (port 8883, user "bblp",
              access code). The printer reports its state; the entries are derived
              from the transitions between RUNNING, FINISH and FAILED.
  Klipper     Moonraker keeps its own history (/server/history/list).
  Prusa       PrusaLink only knows the current print (/api/v1/job); it is
              polled regularly and recorded here.

A print is matched to the imported file via the file name on the printer
(table printer_files), and thereby to its project.
"""
from __future__ import annotations

import json
import logging
import os
import re
import ssl
import threading
import time
import uuid

from . import captures, printer_import, store

log = logging.getLogger("plateshelf.history")

FINAL = ("completed", "cancelled", "failed")
_lock = threading.Lock()
_wakeup = threading.Event()


def env_setting() -> bool | None:
    """PRINT_HISTORY from the environment; None if unset (the setting applies then)."""
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
    """Toggle from the settings; the connections adapt immediately."""
    store.save_settings({"druckverlauf": 1 if on else 0})
    _wakeup.set()


def interval() -> int:
    return max(15, int(os.environ.get("PRINT_HISTORY_INTERVAL", "60")))


# --- Storing ------------------------------------------------------------------

_PLATE_GCODE = re.compile(r"(?:^|/)plate_(\d+)\.gcode$", re.I)
_PLATE_SUFFIX = re.compile(r"_plate_(\d+)\.gcode\.3mf$", re.I)
UNNAMED_SLACK = 300  # max. time before print start that a file without plate number may have been fetched
LIBRARY_GRACE = 1800  # a print without a fetched file is matched to the library after this long
PLATE_SLACK = 3600  # the same for a single plate of a project (plates are uploaded one by one, then printed)
_EXT = re.compile(r"(\.gcode)?\.3mf$|\.b?gcode$", re.I)


def _norm(name: str) -> str:
    """Only letters and digits, lowercase: "Goofy series – X" and "Goofy_series_–_X" are equal."""
    return re.sub(r"[\W_]+", "", _EXT.sub("", name).lower())


def _resolve(con, printer_id: str, remote_name: str | None, plate: int | None = None,
             printer_name: str | None = None, started: float | None = None) -> str | None:
    """Local file for a print: fetched from the printer, otherwise any file in the library.

    The library only counts once the import had time to fetch the file of this very print,
    otherwise an older file of the same project would win over the new one.
    """
    local = _resolve_import(con, printer_id, remote_name, plate, printer_name, started)
    if local or started is None or started > time.time() - LIBRARY_GRACE:
        return local
    return _from_library(con, remote_name, plate, started)


def _from_library(con, remote_name: str | None, plate: int | None, started: float | None) -> str | None:
    """File in the library with the project name the printer reported and this plate sliced.

    For prints whose file never reached the printer's SD card (e.g. the P2S keeps files sent
    to its internal storage there, out of reach for FTP): uploading the sliced file later is
    enough. Only by name, never by time, so a generic name like a process profile matches nothing.
    """
    wanted = _norm(remote_name or "")
    if not wanted or plate is None or _PLATE_GCODE.search(remote_name or ""):
        return None
    rows = con.execute("""SELECT f.path, f.added FROM files f JOIN plates p ON p.file_id = f.id
                          WHERE f.missing = 0 AND p.idx = ? AND p.sliced = 1""", (plate,)).fetchall()
    found = [r for r in rows if _norm(_PLATE_SUFFIX.sub("", r["path"].rsplit("/", 1)[-1])) == wanted]
    if not found:
        return None
    return min(found, key=lambda r: abs((r["added"] or 0) - (started or 0)))["path"]


def _resolve_import(con, printer_id: str, remote_name: str | None, plate: int | None = None,
                    printer_name: str | None = None, started: float | None = None) -> str | None:
    """File fetched from the printer for a print.

    Usual case: the file name on the printer (or just the last part of the path).
    Bambu printers such as the P2S, however, sometimes only report the plate inside the file
    ("Metadata/plate_2.gcode") and the project name separately. Then the file created by the
    import, "<Name>_plate_2.gcode.3mf", matches; if the name is missing (prints from
    0.7.4 to 0.7.11), the last import before the print start applies.
    """
    if remote_name and (m := _PLATE_GCODE.search(remote_name)):
        plate, remote_name = int(m[1]), None
    if remote_name and plate is None:
        row = con.execute("SELECT local_path FROM printer_files WHERE printer = ? AND remote_name = ?",
                          (printer_id, remote_name)).fetchone()
        if not row:
            base = remote_name.rsplit("/", 1)[-1]
            row = con.execute("SELECT local_path FROM printer_files WHERE printer = ? AND (remote_name = ? OR "
                              "remote_name LIKE ?) ORDER BY imported DESC", (printer_id, base, "%/" + base)).fetchone()
        if row and row["local_path"]:
            return row["local_path"]
    if plate is None or not printer_name:
        return None
    # All imports from this printer with the time of the latest import (when the same plate is
    # uploaded again, the new import counts, not the first scan of the file)
    imports = [dict(r) for r in con.execute(
        """SELECT i.local_path, i.remote_name,
                  COALESCE((SELECT MAX(pf.imported) FROM printer_files pf WHERE pf.local_path = i.local_path),
                           f.added) AS at
           FROM printer_imports i JOIN files f ON f.path = i.local_path AND f.missing = 0
           WHERE i.printer_name = ?""", (printer_name,))]

    def plates_of(r) -> set[int]:
        if m := _PLATE_SUFFIX.search(r["local_path"]):
            return {int(m[1])}
        # Without a number in the name (project with one plate or all plates at once): the
        # sliced plates of the file
        return {x["idx"] for x in con.execute("""SELECT p.idx FROM plates p JOIN files f ON f.id = p.file_id
                                                 WHERE f.path = ? AND p.sliced = 1""", (r["local_path"],))}

    candidates = [r for r in imports if plate in plates_of(r)]
    if remote_name:  # project name known: same plate of the same project, the closest in time if several
        wanted = _norm(remote_name)
        named = [r for r in candidates if _norm(r["remote_name"] or "") == wanted
                 or _norm(_PLATE_SUFFIX.sub("", r["local_path"].rsplit("/", 1)[-1])) == wanted]
        if named:
            return min(named, key=lambda r: abs((r["at"] or 0) - (started or 0)))["local_path"]
    if started is None:
        return None
    # Without a name (prints from 0.7.4 to 0.7.11): Bambu Studio uploads each plate right before
    # the print, so the last import before the start applies (with some slack for the sync) if it
    # is the same plate
    before = [r for r in imports if (r["at"] or 0) <= started + 900]
    if not before:
        return None
    last = max(before, key=lambda r: r["at"] or 0)
    if plate not in plates_of(last):
        return None
    # File without a plate number in the name (project with one plate): plate 1 matches almost any
    # file. Only match if it was fetched for this very print; otherwise it would be the previous
    # print (at print start the new file is not imported yet, link_pending catches up on that)
    if not _PLATE_SUFFIX.search(last["local_path"]) and (last["at"] or 0) < started - UNNAMED_SLACK:
        return None
    # A plate fetched long before is the previous print as well (the P2S reports only the process
    # profile as name when printing from Bambu Studio)
    if (last["at"] or 0) < started - PLATE_SLACK:
        return None
    return last["local_path"]


def _guessed(con, job) -> bool:
    """Match based on import time only (the name doesn't match the file)."""
    local = job["local_path"] or ""
    if _PLATE_SUFFIX.search(local) and _PLATE_GCODE.search(job["remote_name"] or ""):
        return False  # only the plate is known (prints from 0.7.4 to 0.7.11)
    base = _norm(_PLATE_SUFFIX.sub("", local.rsplit("/", 1)[-1]))
    name = job["remote_name"] or ""
    if name and (_norm(name.rsplit("/", 1)[-1]) == base or con.execute(
            "SELECT 1 FROM printer_files WHERE printer = ? AND remote_name = ? AND local_path = ?",
            (job["printer"], name, local)).fetchone()):
        return False
    return True


def _apply_project_status(con, job) -> None:
    """A finished print sets a project without status to "gedruckt", once per print."""
    if job["status"] != "completed" or not job["local_path"] or job["applied"] or job["hidden"]:
        return
    con.execute("""UPDATE projects SET status = 'gedruckt', updated = ? WHERE status = '' AND id =
                   (SELECT project_id FROM files WHERE path = ? AND missing = 0)""", (time.time(), job["local_path"]))
    con.execute("UPDATE print_jobs SET applied = 1 WHERE id = ?", (job["id"],))


def record(printer: dict, job_key: str, status: str, *, remote_name: str | None = None,
           started: float | None = None, ended: float | None = None, duration_s: float | None = None,
           filament_mm: float | None = None, progress: float | None = None,
           remaining_s: float | None = None, plate: int | None = None) -> None:
    """Creates a print or updates it with its new state."""
    now = time.time()
    with _lock, store.connect() as con:
        row = con.execute("SELECT * FROM print_jobs WHERE printer = ? AND job_key = ?",
                          (printer["id"], job_key)).fetchone()
        if row and row["status"] in FINAL and status == "running":
            return  # already finished, late report
        local = (row["local_path"] if row else None) or _resolve(
            con, printer["id"], remote_name or (row["remote_name"] if row else None),
            plate if plate is not None else (row["plate"] if row else None), printer["name"],
            (row["started"] if row else None) or started or now)
        if row:
            values = {"status": status, "local_path": local, "printer_name": printer["name"], "updated": now}
            if remote_name:
                values["remote_name"] = remote_name
            if plate is not None:
                values["plate"] = plate
            for key, value in (("duration_s", duration_s), ("filament_mm", filament_mm), ("progress", progress),
                               ("remaining_s", remaining_s)):
                if value is not None:
                    values[key] = value
            if status in FINAL:
                values["remaining_s"] = None
            if status == "completed":
                values["progress"] = 100.0  # Klipper sends no final progress, the last value would remain
            if status in FINAL and not row["ended"]:
                values["ended"] = ended or now
            sets = ", ".join(f"{k} = ?" for k in values)
            con.execute(f"UPDATE print_jobs SET {sets} WHERE id = ?", (*values.values(), row["id"]))
        else:
            con.execute(
                """INSERT INTO print_jobs (printer, printer_name, job_key, remote_name, local_path, status,
                       started, ended, duration_s, filament_mm, progress, remaining_s, updated, plate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (printer["id"], printer["name"], job_key, remote_name, local, status, started or now,
                 (ended or now) if status in FINAL else None, duration_s, filament_mm,
                 100.0 if status == "completed" else progress,
                 None if status in FINAL else remaining_s, now, plate))
        job = con.execute("SELECT * FROM print_jobs WHERE printer = ? AND job_key = ?",
                          (printer["id"], job_key)).fetchone()
        _apply_project_status(con, job)
    if not row and not local and status == "running" and remote_name:
        # New print of a file that is not in the library yet: fetch it right away so the print is
        # matched to its project (otherwise only at the next sync, up to 10 minutes)
        printer_import.request_sync()


def delete(job_id: int) -> None:
    """Remove a print from the history, e.g. a test print.

    The entry stays stored as hidden so that Moonraker or PrusaLink do not create it
    again on the next poll. If it had set the project status to "gedruckt" and no
    other finished print of the project remains, the status is reverted.
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


LIVE_MAX_AGE = 30 * 60  # without a report for 30 minutes a print no longer counts as live


def printing_now() -> list[dict]:
    """Running prints with a recent report, for title bar, overview and dashboard; the print that
    finishes next comes first (those without a known remaining time last)."""
    with store.connect() as con:
        return [dict(r) for r in con.execute(
            """SELECT j.id, j.printer_name, j.remote_name, j.progress, j.remaining_s, j.started, j.local_path,
                      f.project_id, p.name AS project_name
               FROM print_jobs j
               LEFT JOIN files f ON f.path = j.local_path AND f.missing = 0
               LEFT JOIN projects p ON p.id = f.project_id
               WHERE j.status = 'running' AND j.hidden = 0 AND COALESCE(j.updated, j.started) > ?
               ORDER BY j.remaining_s IS NULL, j.remaining_s, j.started""", (time.time() - LIVE_MAX_AGE,))]


def running_job(printer_id: str):
    with store.connect() as con:
        return con.execute("SELECT * FROM print_jobs WHERE printer = ? AND status = 'running' "
                           "ORDER BY started DESC", (printer_id,)).fetchone()


def link_pending() -> None:
    """Match prints without a file afterwards, as soon as the import has fetched it.

    0.8.13 and 0.8.14 sometimes matched running prints of single-plate projects to the file of the
    previous print. Such merely guessed matches are re-checked and corrected here.
    """
    with _lock, store.connect() as con:
        for job in con.execute("SELECT * FROM print_jobs WHERE local_path IS NOT NULL AND remote_name IS NOT NULL "
                               "AND started > ?", (time.time() - 30 * 86400,)).fetchall():
            if not _guessed(con, job):
                continue
            local = _resolve(con, job["printer"], job["remote_name"], job["plate"], job["printer_name"],
                             job["started"])
            if local and local != job["local_path"]:
                log.info("Druck %s: Zuordnung %s -> %s berichtigt", job["id"], job["local_path"], local)
                con.execute("UPDATE print_jobs SET local_path = ?, applied = 0 WHERE id = ?", (local, job["id"]))
        for job in con.execute("SELECT * FROM print_jobs WHERE local_path IS NULL AND remote_name IS NOT NULL "
                               "AND started > ?", (time.time() - 30 * 86400,)).fetchall():
            if local := _resolve(con, job["printer"], job["remote_name"], job["plate"], job["printer_name"],
                                 job["started"]):
                con.execute("UPDATE print_jobs SET local_path = ? WHERE id = ?", (local, job["id"]))
                _apply_project_status(con, {**dict(job), "local_path": local})


def set_note(job_id: int, note: str, rating: int | None) -> None:
    """Note and rating (1 successful, -1 failed, None not specified) for a print."""
    with _lock, store.connect() as con:
        cur = con.execute("UPDATE print_jobs SET note = ?, rating = ? WHERE id = ?",
                          (note.strip()[:1000] or None, rating, job_id))
        if not cur.rowcount:
            raise KeyError(job_id)


# --- Queries -----------------------------------------------------------------

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


LIVE_KEY = "live:"  # print only known to Klipper's print_stats, not to Moonraker's history
KLIPPER_END = {"complete": "completed", "cancelled": "cancelled", "error": "failed"}


def poll_moonraker(printer: dict, source=None) -> int:
    """Takes over the latest entries from Moonraker's history and the running print.

    If the history component is disabled or has not recorded the print (yet), e.g. after a
    Klipper restart, the running print comes from print_stats; it then gets its own
    ID and is finished as soon as Klipper stops printing.
    """
    source = source or printer_import.MoonrakerSource(printer)
    with source._get("/server/history/list?limit=50&order=desc") as resp:
        jobs = (json.load(resp).get("result") or {}).get("jobs") or []
    status = _moonraker_status(source)
    captures.remember(printer["id"], "history (latest 5)", jobs[:5])
    captures.remember(printer["id"], "status", status)
    live = _moonraker_progress(status)
    in_history = any(job.get("status") == "in_progress" for job in jobs)
    for job in jobs:
        running = job.get("status") == "in_progress"
        record(printer, str(job.get("job_id")), MOONRAKER_STATUS.get(job.get("status"), "failed"),
               remote_name=job.get("filename"), started=job.get("start_time"), ended=job.get("end_time"),
               duration_s=job.get("print_duration"), filament_mm=job.get("filament_used"),
               **(live if running else {}))
    _track_klipper_live(printer, status, live, in_history)
    return len(jobs)


def _moonraker_status(source) -> dict:
    try:
        with source._get("/printer/objects/query?virtual_sdcard&print_stats") as resp:
            return (json.load(resp).get("result") or {}).get("status") or {}
    except (OSError, ValueError):
        return {}


def _moonraker_progress(status: dict) -> dict:
    """Progress and estimated remaining time of the running print from Klipper."""
    if not status:
        return {}
    fraction = float((status.get("virtual_sdcard") or {}).get("progress") or 0)
    printed = float((status.get("print_stats") or {}).get("print_duration") or 0)
    out = {"progress": round(fraction * 100, 1)}
    if fraction >= 0.02 and printed > 0:  # the extrapolation is too inaccurate at the start
        out["remaining_s"] = printed / fraction - printed
    return out


def _track_klipper_live(printer: dict, status: dict, live: dict, in_history: bool) -> None:
    stats = status.get("print_stats") or {}
    state, filename = stats.get("state"), stats.get("filename")
    with store.connect() as con:
        rows = con.execute("SELECT * FROM print_jobs WHERE printer = ? AND status = 'running' AND job_key LIKE ?",
                           (printer["id"], LIVE_KEY + "%")).fetchall()
    if in_history:  # Moonraker now tracks the print itself: hide our own entry
        with store.connect() as con:
            con.executemany("UPDATE print_jobs SET hidden = 1, status = 'cancelled' WHERE id = ?",
                            [(r["id"],) for r in rows])
        return
    current = next((r for r in rows if r["remote_name"] == filename), None)
    if state in ("printing", "paused") and filename:
        key = current["job_key"] if current else             f"{LIVE_KEY}{int(time.time() - float(stats.get('total_duration') or 0))}"
        record(printer, key, "running", remote_name=filename, duration_s=stats.get("print_duration"),
               started=None if current else time.time() - float(stats.get("total_duration") or 0),
               filament_mm=stats.get("filament_used"), **live)
        rows = [r for r in rows if r["job_key"] != key]
    for row in rows:  # Klipper no longer prints (or prints something else): finish
        end = KLIPPER_END.get(state) if row["remote_name"] == filename else None
        if end is None:
            end = "completed" if (row["progress"] or 0) >= 99 else "cancelled"
        record(printer, row["job_key"], end, ended=row["updated"] or time.time(),
               progress=100.0 if end == "completed" else row["progress"])


# --- Prusa (PrusaLink) ----------------------------------------------------------

PRUSALINK_STATUS = {"PRINTING": "running", "PAUSED": "running", "FINISHED": "completed",
                    "STOPPED": "cancelled", "ERROR": "failed"}


def _close_lost_prusalink_job(printer: dict, current_key: str | None) -> None:
    """Finish prints whose end PrusaLink never reported (printer switched off before,
    new print started). Nearly done counts as completed, otherwise as cancelled."""
    with store.connect() as con:
        rows = con.execute("SELECT * FROM print_jobs WHERE printer = ? AND status = 'running' AND job_key != ?",
                           (printer["id"], current_key or "")).fetchall()
    for row in rows:  # all older ones, not only the latest: the latest is usually the running one
        done = (row["progress"] or 0) >= 99 or (row["remaining_s"] == 0 and (row["progress"] or 0) >= 80)
        record(printer, row["job_key"], "completed" if done else "cancelled",
               ended=row["updated"] or time.time(), progress=row["progress"])


def poll_prusalink(printer: dict, source=None) -> None:
    """Query the current print; without a print PrusaLink answers with 204 and an empty body."""
    source = source or printer_import.PrusaLinkSource(printer)
    with source._get("/api/v1/job") as resp:
        body = resp.read()
    captures.remember(printer["id"], "job", json.loads(body) if body.strip() else "(204, no print)")
    if not body.strip():
        _close_lost_prusalink_job(printer, None)
        return
    job = json.loads(body)
    status = PRUSALINK_STATUS.get(str(job.get("state") or "").upper())
    if not status or job.get("id") is None:
        return
    _close_lost_prusalink_job(printer, str(job["id"]))
    file = job.get("file") or {}
    name = file.get("display_name") or file.get("name")
    printing = job.get("time_printing")
    record(printer, str(job["id"]), status, remote_name=name, duration_s=printing,
           started=time.time() - printing if printing else None,
           progress=job.get("progress"), remaining_s=job.get("time_remaining"))


# --- OctoPrint -------------------------------------------------------------------

OCTO_KEY = "octo:"  # OctoPrint keeps no history without a plugin: ID from the start time
OCTO_RUNNING = ("printing", "paused", "pausing", "resuming", "starting", "cancelling", "finishing")
OCTO_UNKNOWN = ("offline", "closed", "connecting", "opening serial connection", "detecting serial connection")


def poll_octoprint(printer: dict, source=None) -> None:
    """Current print from /api/job. If one is running, it is created or updated; if none is running
    anymore, the last one seen is finished (completed at 100 %, failed on "Error", otherwise
    cancelled). If OctoPrint is currently not connected to the printer, everything stays as is."""
    source = source or printer_import.OctoPrintSource(printer)
    with source._get("/api/job") as resp:
        data = json.load(resp)
    captures.remember(printer["id"], "job", data)
    state = str(data.get("state") or "").lower()
    file = ((data.get("job") or {}).get("file") or {})
    name = file.get("path") or file.get("name")
    prog = data.get("progress") or {}
    completion = prog.get("completion")
    with store.connect() as con:
        rows = con.execute("SELECT * FROM print_jobs WHERE printer = ? AND status = 'running' AND job_key LIKE ?",
                           (printer["id"], OCTO_KEY + "%")).fetchall()
    if state.startswith(OCTO_RUNNING) and name:
        current = next((r for r in rows if r["remote_name"] == name), None)
        printed = float(prog.get("printTime") or 0)
        key = current["job_key"] if current else f"{OCTO_KEY}{int(time.time() - printed)}"
        record(printer, key, "running", remote_name=name, duration_s=printed or None,
               started=None if current else time.time() - printed,
               progress=round(float(completion), 1) if completion is not None else None,
               remaining_s=prog.get("printTimeLeft"))
        rows = [r for r in rows if r["job_key"] != key]
    elif state.startswith(OCTO_UNKNOWN):
        return
    for row in rows:
        same = row["remote_name"] == name
        if state.startswith("error"):
            end = "failed"
        elif (same and (completion or 0) >= 99.9) or (row["progress"] or 0) >= 99:
            end = "completed"
        else:
            end = "cancelled"
        record(printer, row["job_key"], end, ended=time.time(),
               progress=100.0 if end == "completed" else (completion if same and completion is not None
                                                          else row["progress"]))


# --- Bambu Lab (MQTT) -----------------------------------------------------------

BAMBU_ACTIVE = ("PREPARE", "RUNNING", "PAUSE", "SLICING")
BAMBU_CANCELLED = (0, 50348044)  # 0300400C: cancelled by the user


class BambuTracker:
    """Evaluates the status reports of a Bambu printer (P1/A1 only send changes)."""

    def __init__(self, printer: dict):
        self.printer = printer
        self.state: dict = {}
        self.job_key: str | None = None
        self.progress = None  # last saved: (percent, remaining minutes)

    def _key(self, remote: str) -> str:
        for field in ("subtask_id", "task_id"):
            value = str(self.state.get(field) or "")
            if value and value != "0":
                return f"{field}:{value}"
        job = running_job(self.printer["id"])
        if job and job["remote_name"] == remote:
            return job["job_key"]  # continue after a PlateShelf restart
        return f"{remote}@{int(time.time())}"

    def update(self, message: dict) -> None:
        report = message.get("print")
        if not isinstance(report, dict):
            return
        self.state.update(report)
        gstate = str(self.state.get("gcode_state") or "").upper()
        # Usually the file name on the SD card; the P2S instead reports "…/Metadata/plate_2.gcode"
        # and the project name separately in subtask_name
        gfile = str(self.state.get("gcode_file") or "")
        plate_match = _PLATE_GCODE.search(gfile)
        plate = int(plate_match[1]) if plate_match else None
        subtask = str(self.state.get("subtask_name") or "")
        remote = subtask if plate is not None else (gfile.rsplit("/", 1)[-1] or subtask)
        if gstate in BAMBU_ACTIVE:
            minutes = self.state.get("mc_remaining_time")
            remaining = minutes * 60 if isinstance(minutes, (int, float)) and minutes >= 0 else None
            current = (self.state.get("mc_percent"), minutes)
            if self.job_key is None:
                self.job_key = self._key(remote)
                printer_import.request_sync()  # fetch the file so the print can be matched to it
                record(self.printer, self.job_key, "running", remote_name=remote or None,
                       progress=current[0], remaining_s=remaining, plate=plate)
                self.progress = current
            elif current != self.progress:  # at most about once per minute
                self.progress = current
                record(self.printer, self.job_key, "running", progress=current[0], remaining_s=remaining)
        elif gstate in ("FINISH", "FAILED"):
            key = self.job_key
            if key is None and (job := running_job(self.printer["id"])):
                key = job["job_key"]  # print ran while PlateShelf was off
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
    """MQTT connection to a Bambu printer; reconnects by itself after drops."""

    def __init__(self, printer: dict):
        import paho.mqtt.client as mqtt
        self.printer = printer
        self.tracker = BambuTracker(printer)
        serial = printer["id"]
        self.topic = f"device/{serial}/report"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"plateshelf-{uuid.uuid4().hex[:8]}")
        self.client.username_pw_set("bblp", printer["code"])
        self.client.tls_set(cert_reqs=ssl.CERT_NONE)  # the printer's self-signed certificate
        self.client.tls_insecure_set(True)
        self.client.reconnect_delay_set(5, 300)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, _userdata, _flags, reason, _props=None):
        if reason.is_failure:
            log.info("%s: MQTT-Verbindung abgelehnt (%s)", self.printer["name"], reason)
            return
        client.subscribe(self.topic)
        # Request the full state; afterwards only changes arrive
        client.publish(f"device/{self.printer['id']}/request",
                       json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

    def _on_message(self, _client, _userdata, msg):
        try:
            payload = json.loads(msg.payload)
            captures.remember(self.printer["id"], "mqtt report", payload, merge=True)
            self.tracker.update(payload)
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
    """Adapt the MQTT connections to the configured Bambu printers."""
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


POLLERS = {"moonraker": "poll_moonraker", "prusalink": "poll_prusalink", "octoprint": "poll_octoprint"}


def _poll(p: dict) -> None:
    try:
        globals()[POLLERS[p["kind"]]](p)
    except Exception as exc:  # noqa: BLE001  (unreachable: the import already reports that)
        log.debug("%s: Verlauf nicht abrufbar: %s", p["name"], exc)


def poll_once() -> None:
    """Poll all printers concurrently: one that is switched off does not slow down the others.
    Printers with unreadable credentials are skipped; the import reports that in the settings."""
    printers = printer_import.load_printers()
    refresh_watchers(printers)
    todo = [p for p in printers if p["kind"] in POLLERS and not p.get("code_error")]
    threads = [threading.Thread(target=_poll, args=(p,), daemon=True) for p in todo]
    for t in threads:
        t.start()
    for t in threads:
        t.join(printer_import.TIMEOUT + 5)


def loop() -> None:
    while True:
        try:
            if enabled():
                poll_once()
            else:
                refresh_watchers([])  # disabled: close MQTT connections
        except Exception:  # noqa: BLE001
            log.exception("Druckverlauf fehlgeschlagen")
        _wakeup.wait(interval())
        _wakeup.clear()
