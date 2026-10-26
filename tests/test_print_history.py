"""Print history: Bambu (MQTT messages), Klipper (Moonraker history), Prusa (PrusaLink job)."""
import time

import pytest
from fastapi.testclient import TestClient

from app import print_history as ph
from app import printer_import as pi
from app import store
from test_printer_import import moonraker, octoprint, prusalink  # noqa: F401  (fixtures)

BAMBU = {"name": "P2S", "id": "SER1", "code": "x", "ip": "x", "kind": "bambu"}


def imported(lib, printer, remote, local):
    """File as if the import had fetched it from the printer."""
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
    t.update({"print": {"mc_percent": 50}})  # P1/A1: changes only
    (job,) = jobs()
    assert (job["status"], job["progress"], job["remote_name"]) == ("running", 50, "Benchy.gcode.3mf")
    assert job["local_path"] == "From printer/P2S/Benchy_plate_1.gcode.3mf"
    t.update({"print": {"gcode_state": "FINISH", "mc_percent": 100}})
    (job,) = jobs()
    assert job["status"] == "completed" and job["ended"] >= job["started"]
    assert project_status(job["local_path"]) == "gedruckt"
    t.update({"print": {"gcode_state": "FINISH"}})  # stays put: no second entry
    assert len(jobs()) == 1


def test_bambu_cancel_and_failure(ready):
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "A.gcode.3mf", "task_id": "111"}})
    t.update({"print": {"gcode_state": "FAILED", "print_error": 50348044}})
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "B.gcode.3mf", "task_id": "222"}})
    t.update({"print": {"gcode_state": "FAILED", "print_error": 117473304}})
    assert [(j["job_key"], j["status"]) for j in jobs()] == [("task_id:111", "cancelled"), ("task_id:222", "failed")]


def test_bambu_restart_while_printing(ready):
    """PlateShelf restarts while printing: the same print continues and ends."""
    ph.BambuTracker(BAMBU).update({"print": {"gcode_state": "RUNNING", "gcode_file": "C.gcode.3mf"}})
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "C.gcode.3mf", "mc_percent": 80}})
    assert len(jobs()) == 1
    ph.BambuTracker(BAMBU).update({"print": {"gcode_state": "FINISH", "gcode_file": "C.gcode.3mf"}})
    (job,) = jobs()
    assert job["status"] == "completed" and job["progress"] == 100.0  # done is done


def test_link_after_import(ready):
    """The file arrives from the printer only after the print starts: matched on the next import."""
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
    """After connecting: subscribe to messages and request the full state."""
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


def test_moonraker_live_print_without_history(ready, moonraker):
    """Klipper is printing, but Moonraker's history doesn't know the print (e.g. after a restart):
    the print still shows as running, with project and progress, and ends cleanly."""
    printer = {"name": "K1", "id": "moonraker:k", "code": "", "ip": moonraker["address"], "kind": "moonraker"}
    imported(ready, printer, "Wuerfel.gcode", "From printer/K1/Wuerfel.gcode")
    moonraker["state"]["history"] = [
        {"job_id": "000001", "filename": "Wuerfel.gcode", "status": "klippy_shutdown", "start_time": time.time() - 600,
         "end_time": time.time() - 590, "print_duration": 0, "filament_used": 0}]
    moonraker["state"]["objects"] = {
        "print_stats": {"filename": "Wuerfel.gcode", "state": "printing", "total_duration": 170, "print_duration": 65},
        "virtual_sdcard": {"progress": 0.69}}
    ph.poll_moonraker(printer)
    ph.poll_moonraker(printer)  # same print, no second entry
    (live,) = [j for j in jobs() if j["status"] == "running"]
    assert live["job_key"].startswith(ph.LIVE_KEY) and live["progress"] == 69.0
    assert live["local_path"] == "From printer/K1/Wuerfel.gcode"
    (now,) = ph.printing_now()
    assert now["project_name"] and now["progress"] == 69.0
    moonraker["state"]["objects"]["print_stats"]["state"] = "complete"
    ph.poll_moonraker(printer)
    done = {j["job_key"]: j for j in jobs()}[live["job_key"]]
    assert done["status"] == "completed" and done["progress"] == 100.0   # don't stop at the last value
    assert ph.printing_now() == []


def test_prusalink_job(ready, prusalink):
    printer = {"name": "MK4S", "id": "prusalink:x", "code": "geheim", "ip": prusalink["address"], "kind": "prusalink"}
    ph.poll_prusalink(printer)  # no print: 204
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
    ph.poll_prusalink(printer)  # remains as the last print
    (job,) = jobs()
    assert job["status"] == "completed" and job["duration_s"] == 1800


def test_prusalink_closes_a_print_whose_end_was_missed(ready, prusalink):
    """Printer off before the end was reported: the old print doesn't stay „läuft“ forever."""
    printer = {"name": "MINI", "id": "prusalink:m", "code": "geheim", "ip": prusalink["address"], "kind": "prusalink"}
    prusalink["state"]["job"] = {"id": 2, "state": "PRINTING", "progress": 90.0, "time_printing": 500,
                                 "time_remaining": 0, "file": {"display_name": "Zylinder.bgcode"}}
    ph.poll_prusalink(printer)
    prusalink["state"]["job"] = {"id": 3, "state": "PRINTING", "progress": 5.0, "time_printing": 60,
                                 "file": {"display_name": "Seestern.bgcode"}}
    ph.record(printer, "3", "running", remote_name="Seestern.bgcode", progress=4.0)  # recorded before the update
    ph.poll_prusalink(printer)  # new print: the previous one was almost done
    ph.poll_prusalink(printer)  # also when the new one was already recorded (update mid-print)
    by_key = {j["job_key"]: j for j in jobs()}
    assert by_key["2"]["status"] == "completed" and by_key["3"]["status"] == "running"
    prusalink["state"]["job"] = None
    ph.poll_prusalink(printer)  # no print anymore, vanished at 5 %: cancelled
    assert {j["job_key"]: j["status"] for j in jobs()}["3"] == "cancelled"


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


def test_delete_resets_status_only_without_other_prints(ready, monkeypatch):
    imported(ready, BAMBU, "G.gcode.3mf", "From printer/P2S/G.gcode.3mf")
    ph.record(BAMBU, "a", "completed", remote_name="G.gcode.3mf")
    ph.record(BAMBU, "b", "completed", remote_name="G.gcode.3mf")
    ids = [j["id"] for j in jobs()]
    ph.delete(ids[0])
    assert project_status("From printer/P2S/G.gcode.3mf") == "gedruckt"  # second print still counts
    ph.delete(ids[1])
    assert project_status("From printer/P2S/G.gcode.3mf") == ""
    assert ph.recent() == [] and ph.jobs_for(["From printer/P2S/G.gcode.3mf"]) == []
    with pytest.raises(KeyError):
        ph.delete(999)


def test_delete_keeps_own_status(ready):
    imported(ready, BAMBU, "H.gcode.3mf", "From printer/P2S/H.gcode.3mf")
    ph.record(BAMBU, "a", "completed", remote_name="H.gcode.3mf")
    with store.connect() as con:
        con.execute("UPDATE projects SET status = 'fehlgeschlagen'")  # changed manually later
    ph.delete(jobs()[0]["id"])
    assert project_status("From printer/P2S/H.gcode.3mf") == "fehlgeschlagen"


def test_deleted_job_does_not_come_back(ready, moonraker):
    printer = {"name": "SV08", "id": "moonraker:x", "code": "", "ip": moonraker["address"], "kind": "moonraker"}
    moonraker["state"]["history"] = [{"job_id": "0001", "filename": "test.gcode", "status": "completed",
                                      "start_time": time.time() - 600, "end_time": time.time(),
                                      "print_duration": 500, "filament_used": 10.0}]
    ph.poll_moonraker(printer)
    ph.delete(jobs()[0]["id"])
    ph.poll_moonraker(printer)
    assert len(jobs()) == 1 and ph.recent() == []


def test_delete_from_pages(ready, monkeypatch):
    imported(ready, BAMBU, "K.gcode.3mf", "From printer/P2S/K.gcode.3mf")
    ph.record(BAMBU, "a", "completed", remote_name="K.gcode.3mf")
    ph.record(BAMBU, "b", "failed", remote_name="K.gcode.3mf")
    from app import main
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as client:
        with store.connect() as con:
            pid = con.execute("SELECT project_id FROM files").fetchone()[0]
        a, b = (j["id"] for j in jobs())
        r = client.post(f"/history/{a}/delete", data={"next": f"/p/{pid}"}, follow_redirects=False)
        assert r.headers["location"] == f"/p/{pid}?job_deleted=1#history"
        assert "Druck aus dem Verlauf entfernt." in client.get(r.headers["location"]).text
        r = client.post(f"/history/{b}/delete", data={"next": "https://example.com/"}, follow_redirects=False)
        assert r.headers["location"] == "/?job_deleted=1"  # only targets on this site
        assert client.post("/history/999/delete").status_code == 404
        assert "× gedruckt" not in client.get("/").text and project_status("From printer/P2S/K.gcode.3mf") == ""


def test_setting_and_env(ready, monkeypatch):
    from app import main
    monkeypatch.delenv("PRINT_HISTORY")
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    assert ph.enabled()  # default: on
    with TestClient(main.app) as client:
        assert 'href="/history"' in client.get("/").text
        r = client.post("/settings/history", data={}, follow_redirects=False)
        assert r.headers["location"] == "/settings?history=1#history"
        assert not ph.enabled()
        assert 'href="/history"' not in client.get("/").text
        client.post("/settings/history", data={"enabled": "1"})
        assert ph.enabled()
        page = client.get("/settings").text
        assert 'name="enabled" value="1" checked' in page
        # Environment variable takes precedence, the switch disappears
        monkeypatch.setenv("PRINT_HISTORY", "false")
        assert not ph.enabled()
        page = client.get("/settings").text
        assert "<code>PRINT_HISTORY</code> festgelegt: ausgeschaltet" in page and 'name="enabled"' not in page
        client.post("/settings/history", data={"enabled": "1"})
        monkeypatch.delenv("PRINT_HISTORY")
        assert ph.enabled()  # setting remained unchanged (on)


def test_disabled_closes_watchers(ready, monkeypatch):
    stopped = []

    class FakeWatcher:
        def stop(self):
            stopped.append(1)

    monkeypatch.setitem(ph._watchers, "SER1", (("x", "x", "P2S"), FakeWatcher()))
    ph.refresh_watchers([BAMBU])  # PRINT_HISTORY=false in the tests: disabled
    assert stopped == [1] and ph._watchers == {}


# --- Running prints, sorting, diagnostics (0.7.7) -------------------------------

def test_remaining_time_from_all_printers(ready, moonraker, prusalink):
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "A.gcode.3mf", "task_id": "1",
                        "mc_percent": 10, "mc_remaining_time": 95}})
    t.update({"print": {"mc_remaining_time": 94}})  # only the remaining time changes
    assert jobs()[-1]["remaining_s"] == 94 * 60
    t.update({"print": {"gcode_state": "FINISH"}})
    assert jobs()[-1]["remaining_s"] is None  # done: no remaining time anymore

    sv08 = {"name": "SV08", "id": "moonraker:x", "code": "", "ip": moonraker["address"], "kind": "moonraker"}
    moonraker["state"]["history"] = [{"job_id": "0009", "filename": "b.gcode", "status": "in_progress",
                                      "start_time": time.time() - 600, "end_time": None, "print_duration": 500,
                                      "filament_used": 1.0}]
    moonraker["state"]["objects"] = {"virtual_sdcard": {"progress": 0.25}, "print_stats": {"print_duration": 500}}
    ph.poll_moonraker(sv08)
    job = {j["job_key"]: j for j in jobs()}["0009"]
    assert job["progress"] == 25.0 and job["remaining_s"] == 1500

    mk4 = {"name": "MK4S", "id": "prusalink:x", "code": "geheim", "ip": prusalink["address"], "kind": "prusalink"}
    prusalink["state"]["job"] = {"id": 7, "state": "PRINTING", "progress": 40.0, "time_printing": 400,
                                 "time_remaining": 600, "file": {"display_name": "c.bgcode"}}
    ph.poll_prusalink(mk4)
    assert {j["job_key"]: j for j in jobs()}["7"]["remaining_s"] == 600


def test_printing_now_and_pages(ready, monkeypatch):
    monkeypatch.setenv("PRINT_HISTORY", "true")
    imported(ready, BAMBU, "Live.gcode.3mf", "From printer/P2S/Live.gcode.3mf")
    ph.record(BAMBU, "live", "running", remote_name="Live.gcode.3mf", progress=45, remaining_s=32 * 60)
    ph.record(BAMBU, "old", "running", remote_name="Weg.gcode.3mf", progress=5)
    with store.connect() as con:  # no message for an hour: no longer live
        con.execute("UPDATE print_jobs SET updated = ?, started = ? WHERE job_key = 'old'",
                    (time.time() - 3600, time.time() - 3600))
    (job,) = ph.printing_now()
    assert job["project_name"] == "Live" and job["progress"] == 45
    from app import main
    with TestClient(main.app) as client:
        items = client.get("/api/printing").json()["items"]
        assert items == [{"text": "P2S: Live · 45 % · noch 32 min", "short": "P2S: Live · 32 min",
                          "url": f"/p/{job['project_id']}#history", "progress": 45, "printer": "P2S",
                          "name": "Live", "remaining_min": 32, "remaining": "32 min",
                          "project": job["project_id"], "job": job["id"]}]
        assert client.get("/api/printing?idle_placeholder=1").json()["items"] == items  # only when idle
        page = client.get("/").text
        assert "P2S: Live · 45 % · noch 32 min" in page and 'data-label="Druckt">Druckt 45 %' in page
        stats = client.get("/api/stats").json()
        assert (stats["printing_name"], stats["printing_printer"], stats["printing_progress"],
                stats["printing_remaining_min"]) == ("Live", "P2S", 45, 32)
        monkeypatch.setenv("PRINT_HISTORY", "false")
        assert client.get("/api/printing").json() == {"items": []}
        (idle,) = client.get("/api/printing?idle_placeholder=1").json()["items"]
        assert idle["name"] == idle["short"] == "Kein Druck aktiv" and idle["remaining"] == "" and idle["idle"]
        assert "data-live-print" not in client.get("/").text


def test_running_prints_come_first_in_the_project_list(ready, monkeypatch):
    """Sorted by newest or last printed, running prints are at the top, the one finishing next first."""
    monkeypatch.setenv("PRINT_HISTORY", "true")
    for name in ("Anker", "Mitte", "Yak", "Zebra"):
        imported(ready, BAMBU, f"{name}.gcode.3mf", f"From printer/P2S/{name}.gcode.3mf")
    ph.record(BAMBU, "z", "running", remote_name="Zebra.gcode.3mf", progress=10, remaining_s=3600)
    ph.record(BAMBU, "y", "running", remote_name="Yak.gcode.3mf", progress=80, remaining_s=300)
    from app import main
    with TestClient(main.app) as client:
        def order(sort):
            page = client.get(f"/?sort={sort}").text
            grid = page[page.index('<ul class="grid">'):]
            return sorted(("Anker", "Mitte", "Yak", "Zebra"), key=lambda n: grid.index(f"<h2>{n}</h2>"))
        with store.connect() as con:  # Zebra is the newest file, Anker the oldest
            for age, name in enumerate(("Zebra", "Mitte", "Yak", "Anker")):
                con.execute("UPDATE files SET mtime = ?, added = ? WHERE path LIKE ?",
                            (time.time() - age * 86400, time.time() - age * 86400, f"%/{name}.gcode.3mf"))
        assert order("neu")[:2] == ["Yak", "Zebra"] and order("gedruckt")[:2] == ["Yak", "Zebra"]
        assert order("name") == ["Anker", "Mitte", "Yak", "Zebra"]   # other sort orders stay as chosen


def test_files_from_a_printer_show_its_configured_name(ready, monkeypatch):
    """Not the slicer profile ("Original Prusa MINI & MINI+ Input Shaper"), but the name from the settings."""
    profile = "; printer_settings_id = Original Prusa MINI & MINI+ Input Shaper 0.4 nozzle\nG1 X1 Y1 E1\n"
    imported(ready, BAMBU, "Haken.gcode", "From printer/P2S/Haken.gcode")
    (ready / "From printer/P2S/Haken.gcode").write_text(profile)
    (ready / "Upload.gcode").write_text(profile)
    store.scan(wait=True)
    monkeypatch.setattr(pi, "load_printers", lambda: [{**BAMBU, "name": "Prusa Mini"}])
    from app import main
    with TestClient(main.app) as client:
        page = client.get("/").text
        assert ">Prusa Mini</option>" in page                             # from the printer
        assert ">Original Prusa MINI &amp; MINI+ Input Shaper</option>" in page   # uploaded
        assert client.get("/?printer=Prusa+Mini").text.count("<h2>") == 1
    monkeypatch.setattr(pi, "load_printers", lambda: [])                       # printer removed meanwhile
    with store.connect() as con:
        assert pi.printer_labels(con) == {"From printer/P2S/Haken.gcode": "P2S"}


def test_short_live_text_drops_progress_and_shortens_name():
    from app.web import live_text
    job = {"printer_name": "P2S", "project_name": "Sehr langer Modellname mit vielen Teilen und Varianten",
           "progress": 83.0, "remaining_s": 95 * 60}
    assert "83 %" in live_text(job)
    short = live_text(job, short=True)
    assert "%" not in short and short.endswith("· 1 h 35 min")
    assert short.startswith("P2S: Sehr langer Modellname") and "…" in short and "Varianten" not in short
    assert live_text({"printer_name": "P2S", "remote_name": "a/b.gcode", "progress": None}, short=True) == "P2S: b.gcode"


def test_sort_and_filter_by_prints(ready, sample, monkeypatch):
    monkeypatch.setenv("PRINT_HISTORY", "true")
    for name in ("Oft", "Einmal", "Nie"):
        sample(ready / f"{name}.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
        with store.connect() as con:
            con.execute("INSERT INTO printer_files (printer, remote_name, size, remote_date, local_path, printer_name, "
                        "imported) VALUES ('SER1', ?, 1, 'x', ?, 'P2S', 0)", (f"{name}.gcode.3mf", f"{name}.gcode.3mf"))
    store.scan(wait=True)
    now = time.time()
    ph.record(BAMBU, "o1", "completed", remote_name="Oft.gcode.3mf", started=now - 9000, ended=now - 8000)
    ph.record(BAMBU, "o2", "completed", remote_name="Oft.gcode.3mf", started=now - 7000, ended=now - 6000)
    ph.record(BAMBU, "e1", "completed", remote_name="Einmal.gcode.3mf", started=now - 500, ended=now - 100)
    from app import main
    with TestClient(main.app) as client:
        def order(query):
            """Project names in the order the overview shows them."""
            page = client.get("/?" + query).text
            shown = [(page.index(f"<h2>{n}</h2>"), n) for n in ("Oft", "Einmal", "Nie") if f"<h2>{n}</h2>" in page]
            return [n for _, n in sorted(shown)]
        assert order("sort=gedruckt") == ["Einmal", "Oft", "Nie"]
        assert order("sort=haeufig") == ["Oft", "Einmal", "Nie"]
        assert order("printed=nein") == ["Nie"]
        assert sorted(order("printed=ja")) == ["Einmal", "Oft"]


def test_diagnostics(ready, monkeypatch, caplog):
    from app import diagnostics
    monkeypatch.setenv("PRINTER_1_NAME", "Werkstatt Keller")
    monkeypatch.setenv("PRINTER_1_IP", "192.168.178.44")
    monkeypatch.setenv("PRINTER_1_SERIAL", "01P00A123456789")
    monkeypatch.setenv("PRINTER_1_CODE", "geheim99")
    monkeypatch.setenv("SCAN_INTERVAL", "300")
    diagnostics.install()
    import logging
    logging.getLogger("plateshelf.printers").warning("SV08: http://192.168.178.50:7125 nicht erreichbar")
    text = diagnostics.report("9.9.9")
    assert text.startswith("PlateShelf 9.9.9") and "bambu (env): not queried yet" in text
    assert "SCAN_INTERVAL" in text and "PRINTER_<n>_* (1)" in text
    assert "http://<host>:7125 nicht erreichbar" in text
    for secret in ("Werkstatt Keller", "192.168.178", "01P00A123456789", "geheim99", "300"):
        assert secret not in text, secret
    from app import main
    with TestClient(main.app) as client:
        page = client.get("/settings").text
        assert 'id="diagnostics"' in page and "PlateShelf" in page and "/issues/new/choose" in page


def test_diagnostics_download(ready, monkeypatch, caplog):
    """ZIP with log and raw printer answers, without credentials, serials, addresses and names."""
    import io
    import json
    import logging
    import zipfile
    from app import captures, diagnostics
    monkeypatch.setenv("PRINTER_1_NAME", "Werkstatt Keller")
    monkeypatch.setenv("PRINTER_1_IP", "bambu-keller.home.lan")
    monkeypatch.setenv("PRINTER_1_SERIAL", "01P00A123456789")
    monkeypatch.setenv("PRINTER_1_CODE", "geheim99")
    diagnostics.install()
    captures.clear()
    caplog.set_level(logging.INFO, logger="plateshelf.printers")
    logging.getLogger("plateshelf.printers").info("Werkstatt Keller: 3 files fetched from bambu-keller.home.lan")
    captures.remember("01P00A123456789", "mqtt report",
                      {"print": {"gcode_state": "RUNNING", "mc_percent": 12, "sn": "01P00A123456789",
                                 "net": {"info": [{"ip": 3232235777}]}, "wifi_signal": "-40dBm"}}, merge=True)
    captures.remember("01P00A123456789", "mqtt report", {"print": {"mc_percent": 13}}, merge=True)
    captures.remember("01P00A123456789", "files on the printer", [{"name": "Benchy.gcode.3mf", "size": 12}])
    from app import main
    with TestClient(main.app) as client:
        resp = client.get("/settings/diagnostics.zip")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    assert sorted(z.namelist()) == ["diagnostics.txt", "log.txt", "printer-1-bambu.json"]
    everything = "".join(z.read(n).decode() for n in z.namelist())
    for secret in ("Werkstatt Keller", "bambu-keller", "01P00A123456789", "geheim99", "3232235777", "-40dBm"):
        assert secret not in everything, secret
    assert "printer 1: 3 files fetched from <host>" in z.read("log.txt").decode()
    printer = json.loads(z.read("printer-1-bambu.json"))
    report = printer["last answers"]["mqtt report"]["data"]["print"]
    assert report["gcode_state"] == "RUNNING" and report["mc_percent"] == 13 and report["sn"] == "<removed>"
    assert printer["last answers"]["files on the printer"]["data"] == [{"name": "Benchy.gcode.3mf", "size": 12}]


# --- Statistics and overview in batches (0.7.9) ---------------------------------

def test_statistics(ready, sample, monkeypatch):
    from app import statistics
    sample(ready / "Rot.gcode.3mf", [{"idx": 1, "sec": 3600, "layers": 2, "fil": [(1, "#FF0000FF", 20.0)]}],
           types=("PETG",), costs=("20",))
    with store.connect() as con:
        con.execute("INSERT INTO printer_files (printer, remote_name, size, remote_date, local_path, printer_name, "
                    "imported) VALUES ('SER1', 'Rot.gcode.3mf', 1, 'x', 'Rot.gcode.3mf', 'P2S', 0)")
    store.scan(wait=True)
    now = time.time()
    ph.record(BAMBU, "a", "completed", remote_name="Rot.gcode.3mf", started=now - 7200, ended=now - 3600,
              duration_s=3600)
    ph.record(BAMBU, "b", "failed", remote_name="Rot.gcode.3mf", started=now - 600, ended=now - 300)
    ph.record(BAMBU, "c", "completed", remote_name="Rot.gcode.3mf", started=now - 400 * 86400,
              ended=now - 400 * 86400 + 60)  # older than twelve months: doesn't count
    st = statistics.compute(now)
    assert len(st["months"]) == 12 and st["months"][-1]["completed"] == 1 and st["months"][-1]["failed"] == 1
    assert st["totals"]["completed"] == 1 and st["totals"]["hours"] == pytest.approx(1.0)
    assert st["materials"] == [{"name": "PETG", "grams": pytest.approx(20.0)}]
    # 20 g × 20 €/kg + 1 h × 100 W × 0.35 €/kWh
    assert st["totals"]["cost"] == pytest.approx(0.4 + 0.035)
    (printer,) = st["printers"]
    assert (printer["name"], printer["completed"], printer["failed"], printer["rate"]) == ("P2S", 1, 1, 50)
    from app import main
    with TestClient(main.app) as client:
        page = client.get("/statistics").text
        assert "Drucke pro Monat" in page and "PETG" in page and "50 %" in page
        assert 'href="/statistics"' in client.get("/history").text


def test_statistics_empty(ready):
    from app import main
    with TestClient(main.app) as client:
        assert "Noch keine Drucke" in client.get("/statistics").text


def test_overview_in_steps(ready, sample):
    for i in range(65):
        sample(ready / f"Teil_{i:02d}.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    from app import main
    with TestClient(main.app) as client:
        page = client.get("/?sort=name").text
        assert page.count('class="card"') == 60 and "Weitere 5 Projekte anzeigen" in page
        assert "limit=120" in page and "sort=name" in page
        page = client.get("/?sort=name&limit=120").text
        assert page.count('class="card"') == 65 and "anzeigen</a>" not in page


# --- Bambu P2S: reports the plate instead of the file name (0.7.12) ---------------

def imported_plate(lib, printer, remote, local, added):
    """Like the import: printer_imports entry and file with import time."""
    path = lib / local
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("G1 X1 Y1 E1\n")
    store.scan(wait=True)
    with store.connect() as con:
        con.execute("INSERT INTO printer_imports (local_path, printer_name, remote_name, remote_date) "
                    "VALUES (?, ?, ?, 'x')", (local, printer["name"], remote))
        con.execute("UPDATE files SET added = ? WHERE path = ?", (added, local))


def test_bambu_plate_and_subtask_name(ready):
    now = time.time()
    base = "From printer/P2S/Goofy_series_–_GRIM_REAPER"
    imported_plate(ready, BAMBU, "Goofy_series_–_GRIM_REAPER.gcode.3mf", base + "_plate_1.gcode", now - 9000)
    imported_plate(ready, BAMBU, "Goofy_series_–_GRIM_REAPER.gcode.3mf", base + "_plate_2.gcode", now - 60)
    with store.connect() as con:  # file names as from the import: …_plate_N.gcode.3mf
        for n in (1, 2):
            con.execute("UPDATE printer_imports SET local_path = ? WHERE local_path = ?",
                        (f"{base}_plate_{n}.gcode.3mf", f"{base}_plate_{n}.gcode"))
            con.execute("UPDATE files SET path = ? WHERE path = ?",
                        (f"{base}_plate_{n}.gcode.3mf", f"{base}_plate_{n}.gcode"))
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "/data/Metadata/plate_2.gcode",
                        "subtask_name": "Goofy series – GRIM REAPER", "task_id": "77"}})
    t.update({"print": {"gcode_state": "FINISH"}})
    (job,) = jobs()
    assert (job["plate"], job["remote_name"]) == (2, "Goofy series – GRIM REAPER")
    assert job["local_path"] == base + "_plate_2.gcode.3mf"


def test_old_plate_jobs_are_linked_by_import_time(ready):
    """Prints from 0.7.4 to 0.7.11 are only named "plate_3.gcode": matched via the import time."""
    now = time.time()
    imported_plate(ready, BAMBU, "Eevee.gcode.3mf", "From printer/P2S/Eevee_plate_3.gcode.3mf", now - 7200 + 40)
    imported_plate(ready, BAMBU, "Truck.gcode.3mf", "From printer/P2S/Truck_plate_3.gcode.3mf", now - 86400)
    with store.connect() as con:
        con.execute("INSERT INTO print_jobs (printer, printer_name, job_key, remote_name, status, started, ended) "
                    "VALUES ('SER1', 'P2S', 'alt', 'plate_3.gcode', 'completed', ?, ?)", (now - 7200, now - 3600))
        con.execute("INSERT INTO print_jobs (printer, printer_name, job_key, remote_name, status, started, ended) "
                    "VALUES ('SER1', 'P2S', 'ohne', 'plate_9.gcode', 'completed', ?, ?)", (now - 7200, now - 3600))
    ph.link_pending()
    got = {j["job_key"]: j["local_path"] for j in jobs()}
    assert got == {"alt": "From printer/P2S/Eevee_plate_3.gcode.3mf", "ohne": None}


def test_schema_step_adds_plate_column(lib):
    with store.connect() as con:
        con.execute("ALTER TABLE print_jobs DROP COLUMN plate")
        con.execute("PRAGMA user_version = 1")
    store.init_db()
    with store.connect() as con:
        assert "plate" in {r["name"] for r in con.execute("PRAGMA table_info(print_jobs)")}
        assert con.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION


def test_plates_uploaded_one_by_one(ready):
    """As with the P2S on 27.09.: each plate uploaded shortly before its print; plate 1 was already
    in the library and was re-imported (files.added stays old, printer_files.imported is new)."""
    now = time.time()
    base = "From printer/P2S/Grim_plate_"
    for n, up in ((2, now - 6 * 3600), (3, now - 5 * 3600), (1, now - 3600)):
        imported_plate(ready, BAMBU, "Grim.gcode.3mf", f"{base}{n}.gcode.3mf", up)
        with store.connect() as con:
            con.execute("INSERT INTO printer_files (printer, remote_name, size, remote_date, local_path, "
                        "printer_name, imported) VALUES ('SER1', ?, 1, 'x', ?, 'P2S', ?)",
                        (f"Grim{n}.gcode.3mf", f"{base}{n}.gcode.3mf", up))
    with store.connect() as con:
        con.execute("UPDATE files SET added = ? WHERE path = ?", (now - 86400, base + "1.gcode.3mf"))
        for key, plate, start in (("p2", 2, now - 6 * 3600 + 26 * 60), ("p3", 3, now - 5 * 3600 + 60),
                                  ("p1", 1, now - 3600 + 5), ("p9", 9, now - 1800)):
            con.execute("INSERT INTO print_jobs (printer, printer_name, job_key, remote_name, status, started, "
                        "ended) VALUES ('SER1', 'P2S', ?, ?, 'completed', ?, ?)",
                        (key, f"plate_{plate}.gcode", start, start + 600))
    ph.link_pending()
    got = {j["job_key"]: j["local_path"] for j in jobs()}
    assert got == {"p2": base + "2.gcode.3mf", "p3": base + "3.gcode.3mf", "p1": base + "1.gcode.3mf", "p9": None}


def test_single_plate_project_without_plate_number(ready, sample):
    """As on 04.10.: a single-plate project is named locally without "_plate_N", the printer reports as name
    only the process profile ("0.2mm layer, 2 walls, 15% infill") and plate 1. Matched via the import."""
    now = time.time()
    local = "From printer/P2S/Minimalist_Pencil_Holder.gcode.3mf"
    other = "From printer/P2S/Flexi_Playful_Star.gcode.3mf"
    (ready / "From printer" / "P2S").mkdir(parents=True)
    sample(ready / local, [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FFFFFFFF", 1.0)]}])
    sample(ready / other, [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FFFFFFFF", 1.0)]}])
    store.scan(wait=True)
    with store.connect() as con:
        for path, at in ((local, now - 3600 + 3), (other, now - 600 + 10)):
            name = path.rsplit("/", 1)[-1]
            con.execute("INSERT INTO printer_imports (local_path, printer_name, remote_name, remote_date) "
                        "VALUES (?, 'P2S', ?, 'x')", (path, name))
            con.execute("INSERT INTO printer_files (printer, remote_name, size, remote_date, local_path, "
                        "printer_name, imported) VALUES ('SER1', ?, 1, 'x', ?, 'P2S', ?)", (name, path, at))
        for key, plate, start in (("stift", 1, now - 3600), ("stern", 1, now - 600), ("p2", 2, now - 600)):
            con.execute("INSERT INTO print_jobs (printer, printer_name, job_key, remote_name, plate, status, "
                        "started, ended) VALUES ('SER1', 'P2S', ?, '0.2mm layer, 2 walls, 15% infill', ?, "
                        "'completed', ?, ?)", (key, plate, start, start + 300))
    ph.link_pending()
    got = {j["job_key"]: j["local_path"] for j in jobs()}
    assert got == {"stift": local, "stern": other, "p2": None}   # plate 2 doesn't exist in the file


def test_running_print_does_not_take_the_previous_file(ready, sample):
    """Bug after 0.8.13: at print start the new file has not been fetched yet; the running print
    therefore ended up with the previous project. Now it stays open and is matched after the import.
    link_pending corrects prints that were already mismatched."""
    now = time.time()
    folder = ready / "From printer" / "P2S"
    folder.mkdir(parents=True)
    old, new = "From printer/P2S/Pencil_Holder.gcode.3mf", "From printer/P2S/Flexi_Star.gcode.3mf"
    for path in (old, new):
        sample(ready / path, [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FFFFFFFF", 1.0)]}])
    store.scan(wait=True)

    def imported_at(path, at):
        name = path.rsplit("/", 1)[-1]
        with store.connect() as con:
            con.execute("INSERT INTO printer_imports (local_path, printer_name, remote_name, remote_date) "
                        "VALUES (?, 'P2S', ?, 'x')", (path, name))
            con.execute("INSERT INTO printer_files (printer, remote_name, size, remote_date, local_path, "
                        "printer_name, imported) VALUES ('SER1', ?, 1, 'x', ?, 'P2S', ?)", (name, path, at))
    imported_at(old, now - 3600)                      # previous print an hour ago
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "/data/Metadata/plate_1.gcode",
                        "subtask_name": "0.2mm layer, 2 walls, 15% infill", "task_id": "88"}})
    assert jobs()[0]["local_path"] is None            # not the previous print
    imported_at(new, now + 5)                          # import shortly after the start
    ph.link_pending()
    assert jobs()[0]["local_path"] == new

    # Old, wrongly guessed match (as with 0.8.13/0.8.14) is corrected
    with store.connect() as con:
        con.execute("UPDATE print_jobs SET local_path = ?", (old,))
    ph.link_pending()
    assert jobs()[0]["local_path"] == new


def test_running_print_does_not_take_an_old_plate(ready, sample):
    """As on 21.10.: the previous print was a plate of a project ("…_plate_1.gcode.3mf", a day
    earlier), both reported only the process profile as name. The new print must not end up there."""
    now = time.time()
    folder = ready / "From printer" / "P2S"
    folder.mkdir(parents=True)
    old, new = "From printer/P2S/Echo_Dot_plate_1.gcode.3mf", "From printer/P2S/Apple.gcode.3mf"
    for path in (old, new):
        sample(ready / path, [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FFFFFFFF", 1.0)]}])
    store.scan(wait=True)

    def imported_at(path, remote, at):
        with store.connect() as con:
            con.execute("INSERT INTO printer_imports (local_path, printer_name, remote_name, remote_date) "
                        "VALUES (?, 'P2S', ?, 'x')", (path, remote))
            con.execute("INSERT INTO printer_files (printer, remote_name, size, remote_date, local_path, "
                        "printer_name, imported) VALUES ('SER1', ?, 1, 'x', ?, 'P2S', ?)", (remote, path, at))
    imported_at(old, "Echo_Dot.gcode.3mf", now - 86400)
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "/data/Metadata/plate_1.gcode",
                        "subtask_name": "0.2mm layer, 2 walls, 15% infill", "task_id": "89"}})
    assert jobs()[0]["local_path"] is None
    imported_at(new, "Apple.gcode.3mf", now + 1)
    ph.link_pending()
    assert jobs()[0]["local_path"] == new

    # Matched to the old plate by an earlier version: corrected
    with store.connect() as con:
        con.execute("UPDATE print_jobs SET local_path = ?", (old,))
    ph.link_pending()
    assert jobs()[0]["local_path"] == new


def test_print_without_fetched_file_is_matched_to_an_uploaded_file(ready, sample):
    """As on 25.10.: the P2S kept the file sent from Bambu Studio in its internal storage, so
    nothing could be fetched. Uploading the sliced file later links the print by project name
    and plate, but only once the import had time to fetch a file of its own."""
    now = time.time()
    with store.connect() as con:
        for key, plate, start in (("p1", 1, now - 4 * 3600), ("p2", 2, now - 600), ("neu", 1, now - 60),
                                  ("profil", 1, now - 4 * 3600)):
            name = "0.2mm layer, 2 walls, 15% infill" if key == "profil" else "Gnome Trio"
            con.execute("INSERT INTO print_jobs (printer, printer_name, job_key, remote_name, plate, status, "
                        "started, ended) VALUES ('SER1', 'P2S', ?, ?, ?, 'completed', ?, ?)",
                        (key, name, plate, start, start + 300))
    ph.link_pending()
    assert all(j["local_path"] is None for j in jobs())
    upload = "Gnome/Gnome_Trio.gcode.3mf"
    (ready / "Gnome").mkdir()
    sample(ready / upload, [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FFFFFFFF", 1.0)]},
                            {"idx": 2, "sec": 60, "layers": 2, "fil": [(1, "#FFFFFFFF", 1.0)]}])
    store.scan(wait=True)
    ph.link_pending()
    got = {j["job_key"]: j["local_path"] for j in jobs()}
    # p2 and "neu" started less than LIBRARY_GRACE ago: their own file may still come from the printer
    assert got == {"p1": upload, "p2": None, "neu": None, "profil": None}


def test_library_never_takes_another_printers_file_or_replaces_a_match(ready, sample):
    """As on 25.10.: the P2S reports only the process profile as name, and a file from the P1S's cache
    folder is named exactly like that. The library must not pick it, and must not replace the
    match of an older print whose own file was fetched again later (newer import time)."""
    now = time.time()
    p1s = "From printer/P1S/0.2mm layer, 2 walls, 15% infill.gcode.3mf"
    tissue = "From printer/P2S/Tissue_Box.gcode.3mf"
    for path in (p1s, tissue):
        (ready / path).parent.mkdir(parents=True, exist_ok=True)
        sample(ready / path, [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FFFFFFFF", 1.0)]}])
    store.scan(wait=True)
    with store.connect() as con:
        for path, printer, remote, at in ((p1s, "P1S", "cache/0.2mm layer, 2 walls, 15% infill.gcode.3mf", now - 20 * 86400),
                                          (tissue, "P2S", "Tissue_Box.gcode.3mf", now - 60)):  # fetched again today
            con.execute("INSERT INTO printer_imports (local_path, printer_name, remote_name, remote_date) "
                        "VALUES (?, ?, ?, 'x')", (path, printer, remote))
            con.execute("INSERT INTO printer_files (printer, remote_name, size, remote_date, local_path, printer_name, "
                        "imported) VALUES (?, ?, 1, 'x', ?, ?, ?)", (printer, remote, path, printer, at))
        for key, local in (("alt", tissue), ("ohne", None)):
            con.execute("INSERT INTO print_jobs (printer, printer_name, job_key, remote_name, plate, local_path, "
                        "status, started, ended) VALUES ('SER1', 'P2S', ?, '0.2mm layer, 2 walls, 15% infill', 1, ?, "
                        "'completed', ?, ?)", (key, local, now - 20 * 86400, now - 20 * 86400 + 300))
    ph.link_pending()
    got = {j["job_key"]: j["local_path"] for j in jobs()}
    assert got == {"alt": tissue, "ohne": None}


def test_overview_version_follows_prints_but_not_progress(ready, monkeypatch):
    """The overview reloads when a print starts or ends, not on every percent."""
    monkeypatch.setenv("PRINT_HISTORY", "true")
    imported(ready, BAMBU, "Live.gcode.3mf", "From printer/P2S/Live.gcode.3mf")
    before = store.library_version()
    ph.record(BAMBU, "v1", "running", remote_name="Live.gcode.3mf", progress=5)
    started = store.library_version()
    assert started != before
    ph.record(BAMBU, "v1", "running", remote_name="Live.gcode.3mf", progress=60, remaining_s=600)
    assert store.library_version() == started
    ph.record(BAMBU, "v1", "completed", remote_name="Live.gcode.3mf", progress=100)
    done = store.library_version()
    assert done != started
    store.save_settings({"vorschaubilder_stand": "1.1"})  # new custom thumbnail drawn
    assert store.library_version() != done


def test_new_print_of_unknown_file_fetches_it_right_away(ready, monkeypatch):
    """If a printer prints a file PlateShelf doesn't have yet, a sync runs immediately."""
    calls = []
    monkeypatch.setattr(pi, "request_sync", lambda: calls.append(1))
    octo = {"name": "Ender", "id": "octoprint:x", "kind": "octoprint"}
    ph.record(octo, "octo:1", "running", remote_name="Neu.gcode", progress=1)
    assert calls == [1]
    ph.record(octo, "octo:1", "running", remote_name="Neu.gcode", progress=5)   # same print: not a second time
    ph.record(octo, "octo:0", "completed", remote_name="Alt.gcode")            # old history entry: no
    imported(ready, octo, "Bekannt.gcode", "From printer/Ender/Bekannt.gcode")
    ph.record(octo, "octo:2", "running", remote_name="Bekannt.gcode")          # file already there: no
    assert calls == [1]


def test_octoprint_job(ready, octoprint):
    """OctoPrint without history: running print from /api/job, ending completed, cancelled or failed."""
    printer = {"name": "Ender", "id": "octoprint:x", "code": "geheim", "ip": octoprint["address"], "kind": "octoprint"}
    imported(ready, printer, "Kunden/Halter.gcode", "From printer/Ender/Kunden_Halter.gcode")
    st = octoprint["state"]
    ph.poll_octoprint(printer)
    assert jobs() == []                                                 # nothing printed
    st["job"] = {"state": "Printing", "job": {"file": {"name": "Halter.gcode", "path": "Kunden/Halter.gcode"}},
                 "progress": {"completion": 12.5, "printTime": 120, "printTimeLeft": 840}}
    ph.poll_octoprint(printer)
    st["job"]["progress"].update(completion=40.0, printTime=400, printTimeLeft=600)
    ph.poll_octoprint(printer)
    (job,) = jobs()
    assert job["job_key"].startswith(ph.OCTO_KEY) and job["status"] == "running"
    assert (job["progress"], job["remaining_s"], job["local_path"]) == (40.0, 600, "From printer/Ender/Kunden_Halter.gcode")
    (live,) = ph.printing_now()
    assert live["project_name"]
    st["job"]["state"] = "Offline"                                      # connection lost: don't end anything
    ph.poll_octoprint(printer)
    assert jobs()[0]["status"] == "running"
    st["job"].update(state="Operational", progress={"completion": 100.0, "printTime": 1000, "printTimeLeft": 0})
    ph.poll_octoprint(printer)
    assert jobs()[0]["status"] == "completed"
    st["job"] = {"state": "Printing", "job": {"file": {"path": "Kunden/Halter.gcode"}},
                 "progress": {"completion": 5.0, "printTime": 30}}
    ph.poll_octoprint(printer)
    st["job"].update(state="Operational", progress={"completion": 7.0})
    ph.poll_octoprint(printer)
    assert sorted(j["status"] for j in jobs()) == ["cancelled", "completed"]


def test_printing_now_soonest_first_and_parallel_polls(ready, monkeypatch):
    """The print that finishes next comes first; polling happens concurrently."""
    monkeypatch.setenv("PRINT_HISTORY", "true")
    ph.record(BAMBU, "lang", "running", remote_name="A.gcode.3mf", progress=10, remaining_s=7200)
    ph.record(BAMBU, "kurz", "running", remote_name="B.gcode.3mf", progress=90, remaining_s=120)
    ph.record(BAMBU, "offen", "running", remote_name="C.gcode.3mf", progress=1)
    assert [j["remote_name"] for j in ph.printing_now()] == ["B.gcode.3mf", "A.gcode.3mf", "C.gcode.3mf"]

    printers = [{"name": f"K{n}", "id": f"moonraker:{n}", "kind": "moonraker", "ip": "x", "code": ""} for n in range(4)]
    monkeypatch.setattr(pi, "load_printers", lambda: printers)
    monkeypatch.setattr(ph, "refresh_watchers", lambda _p: None)
    seen = []

    def slow(p, source=None):
        time.sleep(1)
        seen.append(p["name"])

    monkeypatch.setattr(ph, "poll_moonraker", slow)
    t0 = time.time()
    ph.poll_once()
    assert sorted(seen) == ["K0", "K1", "K2", "K3"] and time.time() - t0 < 2.5   # not 4 s in sequence


def test_completed_print_counts_as_100_percent(ready):
    """Moonraker reports no progress from the history at the end: completed means 100 %."""
    ph.record(BAMBU, "e1", "running", remote_name="E.gcode.3mf", progress=81.9)
    ph.record(BAMBU, "e1", "completed", remote_name="E.gcode.3mf")
    ph.record(BAMBU, "e2", "completed", remote_name="F.gcode.3mf")          # created as completed right away
    assert {j["job_key"]: j["progress"] for j in jobs()} == {"e1": 100.0, "e2": 100.0}
