"""Druckverlauf: Bambu (MQTT-Meldungen), Klipper (Moonraker-Verlauf), Prusa (PrusaLink-Job)."""
import time

import pytest
from fastapi.testclient import TestClient

from app import print_history as ph
from app import printer_import as pi
from app import store
from test_printer_import import moonraker, prusalink  # noqa: F401  (Fixtures)

BAMBU = {"name": "P2S", "id": "SER1", "code": "x", "ip": "x", "kind": "bambu"}


def imported(lib, printer, remote, local):
    """Datei, als hätte der Import sie vom Drucker geholt."""
    path = lib / local
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("G1 X1 Y1 E1\n")
    with store.connect() as con:
        con.execute("INSERT INTO printer_files (printer, remote_name, size, remote_date, local_path, printer_name, "
                    "imported) VALUES (?, ?, 1, 'Jan 01 00:00', ?, ?, ?)",
                    (printer["id"], remote, local, printer["name"], time.time()))
    store.scan(wait=True)


def jobs():
    with store.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM print_jobs ORDER BY id")]


def project_status(local):
    with store.connect() as con:
        return con.execute("SELECT p.status FROM projects p JOIN files f ON f.project_id = p.id WHERE f.path = ?",
                           (local,)).fetchone()[0]


@pytest.fixture
def ready(lib, monkeypatch):
    pi.init()
    monkeypatch.setattr(pi, "request_sync", lambda: None)
    return lib


def test_bambu_print_completed(ready):
    imported(ready, BAMBU, "Benchy.gcode.3mf", "From printer/P2S/Benchy_plate_1.gcode.3mf")
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "IDLE"}})
    assert jobs() == []
    t.update({"print": {"gcode_state": "PREPARE", "gcode_file": "/sdcard/Benchy.gcode.3mf", "task_id": "0",
                        "subtask_id": "0", "mc_percent": 0}})
    t.update({"print": {"gcode_state": "RUNNING", "mc_percent": 5}})
    t.update({"print": {"mc_percent": 50}})  # P1/A1: nur Änderungen
    (job,) = jobs()
    assert (job["status"], job["progress"], job["remote_name"]) == ("running", 50, "Benchy.gcode.3mf")
    assert job["local_path"] == "From printer/P2S/Benchy_plate_1.gcode.3mf"
    t.update({"print": {"gcode_state": "FINISH", "mc_percent": 100}})
    (job,) = jobs()
    assert job["status"] == "completed" and job["ended"] >= job["started"]
    assert project_status(job["local_path"]) == "gedruckt"
    t.update({"print": {"gcode_state": "FINISH"}})  # bleibt stehen: kein zweiter Eintrag
    assert len(jobs()) == 1


def test_bambu_cancel_and_failure(ready):
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "A.gcode.3mf", "task_id": "111"}})
    t.update({"print": {"gcode_state": "FAILED", "print_error": 50348044}})
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "B.gcode.3mf", "task_id": "222"}})
    t.update({"print": {"gcode_state": "FAILED", "print_error": 117473304}})
    assert [(j["job_key"], j["status"]) for j in jobs()] == [("task_id:111", "cancelled"), ("task_id:222", "failed")]


def test_bambu_restart_while_printing(ready):
    """PlateShelf startet neu, während gedruckt wird: derselbe Druck läuft weiter und endet."""
    ph.BambuTracker(BAMBU).update({"print": {"gcode_state": "RUNNING", "gcode_file": "C.gcode.3mf"}})
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "C.gcode.3mf", "mc_percent": 80}})
    assert len(jobs()) == 1
    ph.BambuTracker(BAMBU).update({"print": {"gcode_state": "FINISH", "gcode_file": "C.gcode.3mf"}})
    (job,) = jobs()
    assert job["status"] == "completed" and job["progress"] == 80


def test_link_after_import(ready):
    """Die Datei kommt erst nach dem Druckstart vom Drucker: Zuordnung beim nächsten Import."""
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "Clip.gcode.3mf", "task_id": "5"}})
    t.update({"print": {"gcode_state": "FINISH"}})
    assert jobs()[0]["local_path"] is None
    imported(ready, BAMBU, "Clip.gcode.3mf", "From printer/P2S/Clip.gcode.3mf")
    ph.link_pending()
    assert jobs()[0]["local_path"] == "From printer/P2S/Clip.gcode.3mf"
    assert project_status("From printer/P2S/Clip.gcode.3mf") == "gedruckt"


def test_status_is_only_set_when_empty(ready):
    imported(ready, BAMBU, "D.gcode.3mf", "From printer/P2S/D.gcode.3mf")
    with store.connect() as con:
        con.execute("UPDATE projects SET status = 'geplant'")
    ph.record(BAMBU, "k1", "completed", remote_name="D.gcode.3mf")
    assert project_status("From printer/P2S/D.gcode.3mf") == "geplant"


def test_watcher_requests_full_state(ready):
    """Nach dem Verbinden: Meldungen abonnieren und den ganzen Zustand anfordern."""
    w = ph.BambuWatcher(BAMBU)
    calls = []

    class Client:
        def subscribe(self, topic):
            calls.append(("sub", topic))

        def publish(self, topic, payload):
            calls.append(("pub", topic, payload))

    class Ok:
        is_failure = False

    w._on_connect(Client(), None, None, Ok())
    assert calls[0] == ("sub", "device/SER1/report")
    assert calls[1][1] == "device/SER1/request" and "pushall" in calls[1][2]

    class Msg:
        payload = b'{"print": {"gcode_state": "RUNNING", "gcode_file": "E.gcode.3mf", "task_id": "9"}}'

    w._on_message(None, None, Msg())
    assert jobs()[0]["job_key"] == "task_id:9"


def test_moonraker_history(ready, moonraker):
    printer = {"name": "SV08", "id": "moonraker:x", "code": "", "ip": moonraker["address"], "kind": "moonraker"}
    imported(ready, printer, "Ordner/Clip.gcode", "From printer/SV08/Ordner_Clip.gcode")
    now = time.time()
    moonraker["state"]["history"] = [
        {"job_id": "00002A", "filename": "Ordner/Clip.gcode", "status": "in_progress", "start_time": now - 60,
         "end_time": None, "print_duration": 50, "filament_used": 120.5},
        {"job_id": "000029", "filename": "Ordner/Clip.gcode", "status": "completed", "start_time": now - 7200,
         "end_time": now - 3600, "print_duration": 3500, "filament_used": 1500.0},
        {"job_id": "000028", "filename": "weg.gcode", "status": "klippy_shutdown", "start_time": now - 9000,
         "end_time": now - 8000, "print_duration": 900, "filament_used": 10.0},
    ]
    assert ph.poll_moonraker(printer) == 3
    got = {j["job_key"]: j for j in jobs()}
    assert got["00002A"]["status"] == "running" and got["00002A"]["ended"] is None
    assert got["000029"]["status"] == "completed" and got["000029"]["duration_s"] == 3500
    assert got["000029"]["local_path"] == "From printer/SV08/Ordner_Clip.gcode"
    assert got["000028"]["status"] == "failed" and got["000028"]["local_path"] is None
    moonraker["state"]["history"][0].update(status="cancelled", end_time=now)
    ph.poll_moonraker(printer)
    assert {j["job_key"]: j["status"] for j in jobs()}["00002A"] == "cancelled"
    assert len(jobs()) == 3


def test_prusalink_job(ready, prusalink):
    printer = {"name": "MK4S", "id": "prusalink:x", "code": "geheim", "ip": prusalink["address"], "kind": "prusalink"}
    ph.poll_prusalink(printer)  # kein Druck: 204
    assert jobs() == []
    imported(ready, printer, "Kunden/Halter.bgcode", "From printer/MK4S/Kunden_Halter.bgcode")
    prusalink["state"]["job"] = {"id": 42, "state": "PRINTING", "progress": 12.0, "time_printing": 600,
                                 "file": {"name": "HALTER~1.BGC", "display_name": "Halter.bgcode", "path": "/usb/KUNDEN"}}
    ph.poll_prusalink(printer)
    (job,) = jobs()
    assert (job["status"], job["progress"], job["local_path"]) == ("running", 12.0, "From printer/MK4S/Kunden_Halter.bgcode")
    assert abs(job["started"] - (time.time() - 600)) < 5
    prusalink["state"]["job"].update(state="FINISHED", progress=100.0, time_printing=1800)
    ph.poll_prusalink(printer)
    ph.poll_prusalink(printer)  # bleibt als letzter Druck stehen
    (job,) = jobs()
    assert job["status"] == "completed" and job["duration_s"] == 1800


def test_pages_and_stats(ready, monkeypatch):
    imported(ready, BAMBU, "F.gcode.3mf", "From printer/P2S/F.gcode.3mf")
    ph.record(BAMBU, "a", "completed", remote_name="F.gcode.3mf", started=time.time() - 3600, ended=time.time())
    ph.record(BAMBU, "b", "failed", remote_name="F.gcode.3mf", started=time.time() - 7200, ended=time.time() - 7000)
    ph.record(BAMBU, "c", "running", remote_name="F.gcode.3mf", progress=30)
    from app import main
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as client:
        with store.connect() as con:
            pid = con.execute("SELECT project_id FROM files").fetchone()[0]
        page = client.get(f"/p/{pid}").text
        assert "Druckverlauf" in page and "Läuft 30 %" in page and "Fehlgeschlagen" in page
        assert "1× gedruckt" in client.get("/").text
        history = client.get("/history").text
        assert f'href="/p/{pid}"' in history and history.count("job-status") == 3
        stats = client.get("/api/stats").json()
        assert (stats["prints_30d"], stats["prints_failed_30d"], stats["printing_now"]) == (1, 1, 1)
