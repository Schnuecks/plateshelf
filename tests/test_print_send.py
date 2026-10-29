"""Print a sliced plate again (print_send, routes_print)."""
import json

import pytest
from fastapi.testclient import TestClient

from app import print_history as ph
from app import print_send as ps
from app import printer_import as pi
from app import store

P2S = {"name": "P2S", "id": "22E00A000000001", "code": "x", "ip": "192.0.2.20", "kind": "bambu", "source": "env"}

AMS = {"ams": {"ams": [{"id": "0", "tray": [
    {"id": "0", "tray_type": "PLA", "tray_color": "6F5034FF", "tray_sub_brands": "PLA Basic"},
    {"id": "1", "tray_type": "PLA", "tray_color": "FFFFFFFF", "tray_sub_brands": "PLA Basic"},
    {"id": "2", "tray_type": "PETG", "tray_color": "000000FF", "tray_sub_brands": "PETG HF"},
    {"id": "3", "tray_type": ""},  # empty tray
]}, {"id": "128", "tray": [{"id": "0", "tray_type": "TPU", "tray_color": "FF0000FF"}]}]}}


class FakeFtp:
    def __init__(self):
        self.stored = {}

    def storbinary(self, cmd, fh, blocksize=None):
        self.stored[cmd] = fh.read()

    def quit(self):
        pass

    def close(self):
        pass


class FakeClient:
    def __init__(self, replies, answer=None):
        self.sent, self.replies, self.answer = [], replies, answer

    def publish(self, topic, payload):
        self.sent.append((topic, json.loads(payload)))
        if self.answer is not None:  # the printer answers right away
            seq = json.loads(payload)["print"]["sequence_id"]
            self.replies[seq] = {"sequence_id": seq, **self.answer}

        class Info:
            rc = 0
        return Info()


class FakeWatcher:
    def __init__(self, state, answer=None):
        self.tracker = type("T", (), {"state": state})()
        self.replies = {}
        self.client = FakeClient(self.replies, answer)


@pytest.fixture
def printer(lib, monkeypatch):
    """P2S configured, MQTT state idle with an AMS, uploads go to FakeFtp; the printer accepts."""
    monkeypatch.setattr(pi, "load_printers", lambda: [P2S])
    monkeypatch.setattr(ps, "REPLY_WAIT", 0.3)
    monkeypatch.setenv("PRINT_SEND", "true")
    watcher = FakeWatcher({"gcode_state": "IDLE", **AMS}, answer={"command": "project_file", "result": "success"})
    monkeypatch.setitem(ph._watchers, P2S["id"], ((), watcher))
    ftp = FakeFtp()
    monkeypatch.setattr(pi, "_connect", lambda p: ftp)
    return watcher, ftp


@pytest.fixture
def plate(lib, sample):
    path = lib / "Vase" / "Vase mit Henkel.gcode.3mf"
    path.parent.mkdir()
    sample(path, [{"idx": 1, "sec": 3600, "layers": 50, "fil": [(1, "#FFFFFF", 30.0), (2, "#6F5034", 5.0)]},
                  {"idx": 2, "sec": 600, "layers": 10, "fil": [(1, "#FFFFFF", 4.0)]}],
           types=("PLA", "PLA"), colours=("#FFFFFFFF", "#6F5034FF"))
    store.scan(wait=True)
    with store.connect() as con:
        return con.execute("SELECT id, project_id FROM files").fetchone()


def test_trays_flat_slots_and_empty_trays():
    trays = ps.trays(AMS)
    assert [(t["slot"], t["type"], t["color"]) for t in trays] == [
        (0, "PLA", "#6F5034"), (1, "PLA", "#FFFFFF"), (2, "PETG", "#000000"), (16, "TPU", "#FF0000")]


def test_suggest_matches_type_and_closest_colour():
    filaments = [{"slot": 1, "type": "PLA", "color": "#FAFAFA", "used_g": 30},
                 {"slot": 2, "type": "PLA", "color": "#704f30", "used_g": 5},
                 {"slot": 3, "type": "ABS", "color": "#000000", "used_g": 1}]
    assert ps.suggest(filaments, ps.trays(AMS)) == {1: 1, 2: 0, 3: None}


def test_command_for_new_and_old_models():
    new = ps.command(P2S["id"], "Vase.gcode.3mf", 2, {1: 1, 2: 16})["print"]
    assert new["url"] == "ftp:///Vase.gcode.3mf" and new["param"] == "Metadata/plate_2.gcode"
    assert new["use_ams"] is True and new["ams_mapping"] == [1, 16]
    assert new["ams_mapping2"] == [{"ams_id": 0, "slot_id": 1}, {"ams_id": 128, "slot_id": 0}]
    old = ps.command("01P00A123456789", "Vase.gcode.3mf", 1, {1: ps.EXTERNAL})["print"]
    assert old["url"] == "file:///sdcard/Vase.gcode.3mf" and old["use_ams"] is False and old["ams_mapping"] == []


def test_remote_name_is_safe():
    assert ps.remote_name("Vase/Vase mit Henkel (v2).gcode.3mf") == "Vase_mit_Henkel_v2.gcode.3mf"
    assert ps.remote_name("../../etc/passwd.gcode.3mf") == "passwd.gcode.3mf"


def test_send_uploads_and_starts(plate, printer):
    watcher, ftp = printer
    name = ps.send(P2S, plate["id"], 1, {1: 1, 2: 0})
    assert name == "Vase_mit_Henkel.gcode.3mf" and list(ftp.stored) == ["STOR /Vase_mit_Henkel.gcode.3mf"]
    topic, payload = watcher.client.sent[0]
    assert topic == f"device/{P2S['id']}/request"
    assert payload["print"]["command"] == "project_file" and payload["print"]["ams_mapping"] == [1, 0]


def test_send_refuses_busy_printer_and_missing_slot(plate, printer):
    watcher, ftp = printer
    with pytest.raises(ps.UserError, match="Fach"):
        ps.send(P2S, plate["id"], 1, {1: 1})
    watcher.tracker.state["gcode_state"] = "RUNNING"
    with pytest.raises(ps.UserError, match="beschäftigt"):
        ps.send(P2S, plate["id"], 1, {1: 1, 2: 0})
    with pytest.raises(ps.UserError, match="externe Spule"):
        watcher.tracker.state["gcode_state"] = "IDLE"
        ps.send(P2S, plate["id"], 1, {1: ps.EXTERNAL, 2: 0})
    assert not ftp.stored and not watcher.client.sent


def test_page_and_start(plate, printer, monkeypatch):
    from app import main
    watcher, _ = printer
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as client:
        project = client.get(f"/p/{plate['project_id']}").text
        assert f'href="/print/{plate["id"]}/1"' in project
        page = client.get(f"/print/{plate['id']}/1").text
        assert "Platte 1 drucken" in page and 'name="slot_1"' in page and "Druck starten" in page
        # without confirming the empty bed nothing is sent
        r = client.post(f"/print/{plate['id']}/1", data={"printer": P2S["id"], "slot_1": "1", "slot_2": "0"})
        assert r.status_code == 200 and "Druckbett leer" in r.text and not watcher.client.sent
        r = client.post(f"/print/{plate['id']}/1", follow_redirects=False,
                        data={"printer": P2S["id"], "slot_1": "1", "slot_2": "0", "bed_clear": "1", "bed_leveling": "1"})
        assert r.status_code == 303 and "print_started=1" in r.headers["location"]
        assert watcher.client.sent[0][1]["print"]["bed_leveling"] is True


def test_viewers_may_not_print():
    from app.web import allowed
    assert not allowed("viewer", "GET", "/print/1/1")
    assert allowed("user", "POST", "/print/1/1")


def test_refused_start_command_is_reported(plate, printer):
    watcher, _ = printer
    # what a P2S without developer mode answered on 2026-10-29
    watcher.client.answer = {"command": "project_file", "result": "failed", "reason": "mqtt message verify failed",
                             "err_code": 84033543, "is_from_mqtt": True}
    with pytest.raises(ps.UserError, match="Entwicklermodus"):
        ps.send(P2S, plate["id"], 1, {1: 1, 2: 0})
    watcher.client.answer = {"command": "project_file", "result": "failed", "reason": "sdcard busy"}
    with pytest.raises(ps.UserError, match="sdcard busy"):
        ps.send(P2S, plate["id"], 1, {1: 1, 2: 0})
    watcher.client.answer = None  # no answer at all: counts as accepted
    assert ps.send(P2S, plate["id"], 1, {1: 1, 2: 0})


def test_page_points_out_a_different_colour(plate, printer, monkeypatch):
    from app import main
    watcher, _ = printer
    # only a brown PLA loaded, the plate wants white and brown
    watcher.tracker.state["ams"] = {"ams": [{"id": "0", "tray": [{"id": "0", "tray_type": "PLA", "tray_color": "6F5034FF"}]}]}
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as client:
        page = client.get(f"/print/{plate['id']}/1").text
    assert page.count("Farbe weicht ab") == 1


def test_busy_printer_shows_the_running_print(plate, printer, monkeypatch):
    from app import main
    watcher, _ = printer
    watcher.tracker.state.update(gcode_state="RUNNING", subtask_name="Benchy", mc_percent=42, mc_remaining_time=25)
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as client:
        page = client.get(f"/print/{plate['id']}/1").text
    assert "druckt gerade „Benchy“: 42 %, noch 25 min" in page and 'type="submit" disabled' in page


def test_print_button_greyed_out_while_printing(plate, printer, monkeypatch):
    from app import main
    watcher, _ = printer
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as client:
        assert f'href="/print/{plate["id"]}/1"' in client.get(f"/p/{plate['project_id']}").text
        watcher.tracker.state["gcode_state"] = "RUNNING"
        page = client.get(f"/p/{plate['project_id']}").text
        assert f'href="/print/{plate["id"]}/1"' not in page and "is-disabled" in page


def test_off_by_default(plate, printer, monkeypatch):
    from app import main
    monkeypatch.delenv("PRINT_SEND")
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as client:
        assert "/print/" not in client.get(f"/p/{plate['project_id']}").text
        assert client.get(f"/print/{plate['id']}/1").status_code == 404
        assert "Drucken aus PlateShelf erlauben" in client.get("/settings").text
        client.post("/settings/print-send", data={"send_enabled": "1"})
        assert ps.enabled() and f'href="/print/{plate["id"]}/1"' in client.get(f"/p/{plate['project_id']}").text
