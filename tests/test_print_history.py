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


def test_delete_resets_status_only_without_other_prints(ready, monkeypatch):
    imported(ready, BAMBU, "G.gcode.3mf", "From printer/P2S/G.gcode.3mf")
    ph.record(BAMBU, "a", "completed", remote_name="G.gcode.3mf")
    ph.record(BAMBU, "b", "completed", remote_name="G.gcode.3mf")
    ids = [j["id"] for j in jobs()]
    ph.delete(ids[0])
    assert project_status("From printer/P2S/G.gcode.3mf") == "gedruckt"  # zweiter Druck zählt noch
    ph.delete(ids[1])
    assert project_status("From printer/P2S/G.gcode.3mf") == ""
    assert ph.recent() == [] and ph.jobs_for(["From printer/P2S/G.gcode.3mf"]) == []
    with pytest.raises(KeyError):
        ph.delete(999)


def test_delete_keeps_own_status(ready):
    imported(ready, BAMBU, "H.gcode.3mf", "From printer/P2S/H.gcode.3mf")
    ph.record(BAMBU, "a", "completed", remote_name="H.gcode.3mf")
    with store.connect() as con:
        con.execute("UPDATE projects SET status = 'fehlgeschlagen'")  # später von Hand geändert
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
        assert r.headers["location"] == "/?job_deleted=1"  # nur Ziele dieser Seite
        assert client.post("/history/999/delete").status_code == 404
        assert "× gedruckt" not in client.get("/").text and project_status("From printer/P2S/K.gcode.3mf") == ""


def test_setting_and_env(ready, monkeypatch):
    from app import main
    monkeypatch.delenv("PRINT_HISTORY")
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    assert ph.enabled()  # Standard: an
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
        # Umgebungsvariable hat Vorrang, der Schalter verschwindet
        monkeypatch.setenv("PRINT_HISTORY", "false")
        assert not ph.enabled()
        page = client.get("/settings").text
        assert "<code>PRINT_HISTORY</code> festgelegt: ausgeschaltet" in page and 'name="enabled"' not in page
        client.post("/settings/history", data={"enabled": "1"})
        monkeypatch.delenv("PRINT_HISTORY")
        assert ph.enabled()  # Einstellung blieb unverändert (an)


def test_disabled_closes_watchers(ready, monkeypatch):
    stopped = []

    class FakeWatcher:
        def stop(self):
            stopped.append(1)

    monkeypatch.setitem(ph._watchers, "SER1", (("x", "x", "P2S"), FakeWatcher()))
    ph.refresh_watchers([BAMBU])  # PRINT_HISTORY=false in den Tests: ausgeschaltet
    assert stopped == [1] and ph._watchers == {}


# --- Laufende Drucke, Sortierung, Diagnose (0.7.7) ------------------------------

def test_remaining_time_from_all_printers(ready, moonraker, prusalink):
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "A.gcode.3mf", "task_id": "1",
                        "mc_percent": 10, "mc_remaining_time": 95}})
    t.update({"print": {"mc_remaining_time": 94}})  # nur die Restzeit ändert sich
    assert jobs()[-1]["remaining_s"] == 94 * 60
    t.update({"print": {"gcode_state": "FINISH"}})
    assert jobs()[-1]["remaining_s"] is None  # fertig: keine Restzeit mehr

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
    with store.connect() as con:  # seit einer Stunde keine Meldung: nicht mehr live
        con.execute("UPDATE print_jobs SET updated = ?, started = ? WHERE job_key = 'old'",
                    (time.time() - 3600, time.time() - 3600))
    (job,) = ph.printing_now()
    assert job["project_name"] == "Live" and job["progress"] == 45
    from app import main
    with TestClient(main.app) as client:
        items = client.get("/api/printing").json()["items"]
        assert items == [{"text": "P2S: Live · 45 % · noch 32 min", "short": "P2S: Live · 32 min",
                          "url": f"/p/{job['project_id']}#history", "progress": 45, "printer": "P2S",
                          "name": "Live", "remaining_min": 32}]
        page = client.get("/").text
        assert "P2S: Live · 45 % · noch 32 min" in page and 'class="bed-live">Druckt 45 %' in page
        stats = client.get("/api/stats").json()
        assert (stats["printing_name"], stats["printing_printer"], stats["printing_progress"],
                stats["printing_remaining_min"]) == ("Live", "P2S", 45, 32)
        monkeypatch.setenv("PRINT_HISTORY", "false")
        assert client.get("/api/printing").json() == {"items": []}
        assert "data-live-print" not in client.get("/").text


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
            """Projektnamen in der Reihenfolge, in der die Übersicht sie zeigt."""
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


# --- Statistik und Übersicht in Schritten (0.7.9) --------------------------------

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
              ended=now - 400 * 86400 + 60)  # älter als zwölf Monate: zählt nicht
    st = statistics.compute(now)
    assert len(st["months"]) == 12 and st["months"][-1]["completed"] == 1 and st["months"][-1]["failed"] == 1
    assert st["totals"]["completed"] == 1 and st["totals"]["hours"] == pytest.approx(1.0)
    assert st["materials"] == [{"name": "PETG", "grams": pytest.approx(20.0)}]
    # 20 g × 20 €/kg + 1 h × 100 W × 0,35 €/kWh
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


# --- Bambu P2S: meldet die Platte statt des Dateinamens (0.7.12) -------------------

def imported_plate(lib, printer, remote, local, added):
    """Wie der Import: printer_imports-Eintrag und Datei mit Importzeitpunkt."""
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
    with store.connect() as con:  # Dateinamen wie vom Import: …_plate_N.gcode.3mf
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
    """Drucke aus 0.7.4 bis 0.7.11 heißen nur „plate_3.gcode“: Zuordnung über den Importzeitpunkt."""
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
    """Wie beim P2S am 27.09.: jede Platte kurz vor ihrem Druck hochgeladen; Platte 1 lag schon früher
    in der Bibliothek und wurde neu importiert (files.added bleibt alt, printer_files.imported ist neu)."""
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
    """Wie am 04.10.: Projekt mit einer Platte heißt lokal ohne „_plate_N“, der Drucker meldet als Namen
    nur das Prozessprofil („0.2mm layer, 2 walls, 15% infill“) und Platte 1. Zuordnung über den Import."""
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
    assert got == {"stift": local, "stern": other, "p2": None}   # Platte 2 gibt es in der Datei nicht


def test_running_print_does_not_take_the_previous_file(ready, sample):
    """Bug nach 0.8.13: Beim Druckstart ist die neue Datei noch nicht geholt; der laufende Druck
    landete deshalb beim vorigen Projekt. Jetzt bleibt er offen und wird nach dem Import zugeordnet.
    Schon falsch zugeordnete Drucke berichtigt link_pending."""
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
    imported_at(old, now - 3600)                      # voriger Druck vor einer Stunde
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "/data/Metadata/plate_1.gcode",
                        "subtask_name": "0.2mm layer, 2 walls, 15% infill", "task_id": "88"}})
    assert jobs()[0]["local_path"] is None            # nicht der vorige Druck
    imported_at(new, now + 5)                          # Import kurz nach dem Start
    ph.link_pending()
    assert jobs()[0]["local_path"] == new

    # Alte, falsch geratene Zuordnung (wie mit 0.8.13/0.8.14) wird berichtigt
    with store.connect() as con:
        con.execute("UPDATE print_jobs SET local_path = ?", (old,))
    ph.link_pending()
    assert jobs()[0]["local_path"] == new
