import os
import shutil
import socket
import subprocess
import threading

import pytest

from app import printer_import as pi
from app import store

LISTING = """-rwxr-xr-x    1 103      107       1795953 Apr 03 18:18 (Unsaved).gcode.3mf
-rwxr-xr-x    1 103      107      16139550 Nov 29  2025 Tealight_-_Santa´s_flight.gcode.3mf
drwxr-xr-x    2 103      107         16384 Jun 10 09:14 System Volume Information
drwxr-xr-x    2 103      107         16384 Sep 24 15:33 cache
-rwxr-xr-x    1 103      107        462518 Feb 20  2026 Custom_tags_Plate 1.gcode.3mf
-rwxr-xr-x    1 103      107         12345 Feb 20  2026 notiz.txt"""


def test_parse_listing():
    files = pi.parse_listing(LISTING.splitlines())
    assert [f["name"] for f in files] == [
        "(Unsaved).gcode.3mf", "Tealight_-_Santa´s_flight.gcode.3mf", "Custom_tags_Plate 1.gcode.3mf"]
    assert files[1]["size"] == 16139550 and files[1]["date"] == "Nov 29 2025"


class FakePrinter:
    """Simuliert die SD-Karte: Name -> (Quelldatei, Datum)."""

    def __init__(self, files):
        self.files = files

    def __call__(self, printer):
        return self

    def retrlines(self, cmd, cb):
        for name, (src, date) in self.files.items():
            cb(f"-rwxr-xr-x    1 103      107  {os.path.getsize(src):>12} {date} {name}")

    def retrbinary(self, cmd, cb, blocksize=8192):
        cb(open(self.files[cmd[6:]][0], "rb").read())

    def quit(self):
        pass


PRINTER = {"name": "P2S", "id": "SER1", "code": "x", "ip": "x"}


def test_sync_keeps_every_printed_plate(lib, sample, tmp_path):
    from make_sample import png
    import zipfile
    srcs = {}
    for i in (1, 3):
        p = tmp_path / f"plate{i}.gcode.3mf"
        sample(p, [{"idx": i, "sec": 60, "layers": 2, "fil": [(1, "#FFFFFFFF", 1.0)]}])
        with zipfile.ZipFile(p, "a") as z:  # Vorschau der jeweils anderen Platte
            z.writestr(f"Metadata/plate_{4 - i}.png", png((1, 1, 1, 255)))
        srcs[i] = p
    pi.init()
    fake = FakePrinter({"Jade_Mountains.gcode.3mf": (srcs[1], "Jan 18  2026")})
    assert pi.sync_printer(PRINTER, connect=fake)["new"] == 1
    assert pi.sync_printer(PRINTER, connect=fake)["new"] == 0  # unverändert
    fake.files["Jade_Mountains.gcode.3mf"] = (srcs[3], "Jan 20  2026")  # Drucker hat überschrieben
    assert pi.sync_printer(PRINTER, connect=fake)["updated"] == 1
    target = lib / "From printer" / "P2S"
    assert sorted(os.listdir(target)) == ["Jade_Mountains_plate_1.gcode.3mf", "Jade_Mountains_plate_3.gcode.3mf"]
    # Im Archiv gelöscht: kommt nicht zurück
    (target / "Jade_Mountains_plate_3.gcode.3mf").unlink()
    pi.sync_printer(PRINTER, connect=fake)
    assert not (target / "Jade_Mountains_plate_3.gcode.3mf").exists()
    store.scan(wait=True)
    with store.connect() as con:
        assert con.execute("SELECT DISTINCT p.name FROM projects p JOIN files f ON f.project_id = p.id").fetchall()[0][0] == "Jade Mountains"


def test_offline_printer_is_reported(lib):
    pi.init()

    def boom(p):
        raise TimeoutError("timed out")
    res = pi.sync_printer(PRINTER, connect=boom)
    assert not res["ok"] and "nicht erreichbar" in res["error"]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl nicht installiert")
def test_implicit_ftps_against_real_tls_server(lib, sample, tmp_path):
    """Echter Server mit implizitem TLS und verschlüsseltem Datenkanal, wie am Drucker."""
    pytest.importorskip("OpenSSL")
    ftpd = pytest.importorskip("pyftpdlib")  # noqa: F841
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import TLS_FTPHandler
    from pyftpdlib.servers import FTPServer

    root = tmp_path / "sd"
    root.mkdir()
    sample(root / "Turtle.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#000000FF", 1.0)]}])
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(crt), "-days", "1", "-subj", "/CN=printer"], check=True, capture_output=True)

    class Implicit(TLS_FTPHandler):
        def handle(self):
            self.secure_connection(self.ssl_context)

        def handle_ssl_established(self):
            TLS_FTPHandler.handle(self)

    auth = DummyAuthorizer()
    auth.add_user("bblp", "12345678", str(root), perm="elr")
    Implicit.authorizer, Implicit.certfile, Implicit.keyfile = auth, str(crt), str(key)
    Implicit.tls_control_required = Implicit.tls_data_required = True
    port = _free_port()
    server = FTPServer(("127.0.0.1", port), Implicit)
    threading.Thread(target=server.serve_forever, kwargs={"timeout": 0.2}, daemon=True).start()
    try:
        def connect(p):
            ftp = pi.ImplicitFTPS(timeout=10)
            ftp.connect("127.0.0.1", port)
            ftp.login("bblp", p["code"])
            ftp.prot_p()
            return ftp
        pi.init()
        ok = pi.sync_printer({**PRINTER, "code": "12345678"}, connect=connect)
        assert ok["new"] == 1, ok
        bad = pi.sync_printer({**PRINTER, "code": "falsch"}, connect=connect)
        assert bad["error"].startswith("Anmeldung abgelehnt")
    finally:
        server.close_all()


def test_env_printers():
    env = {"PRINTER_2_IP": "192.0.2.21", "PRINTER_2_SERIAL": "SER2", "PRINTER_2_CODE": "b",
           "PRINTER_1_NAME": "P2S", "PRINTER_1_IP": "192.0.2.20", "PRINTER_1_SERIAL": "SER1", "PRINTER_1_CODE": "a",
           "PRINTER_3_IP": "192.0.2.22", "PRINTER_3_SERIAL": "SER3",  # Zugangscode fehlt: wird übersprungen
           "PRINTER_SYNC_INTERVAL": "600"}
    printers = pi.env_printers(env)
    assert [(p["name"], p["id"], p["ip"], p["code"]) for p in printers] == [
        ("P2S", "SER1", "192.0.2.20", "a"), ("SER2", "SER2", "192.0.2.21", "b")]


def test_save_printers_from_settings(lib, monkeypatch):
    pi.init()
    pi.save_printers([{"db_id": None, "name": "P1S", "ip": "192.0.2.30", "serial": "S1", "code": "geheim"},
                      {"db_id": None, "name": "", "ip": "", "serial": "", "code": ""}])  # leere Zeile ignoriert
    (p,) = pi.db_printers()
    assert (p["name"], p["ip"], p["id"], p["code"]) == ("P1S", "192.0.2.30", "S1", "geheim")
    # Leerer Zugangscode behält den gespeicherten
    pi.save_printers([{"db_id": p["db_id"], "name": "P1S Werkstatt", "ip": "192.0.2.31", "serial": "S1", "code": ""}])
    (p,) = pi.db_printers()
    assert (p["name"], p["ip"], p["code"]) == ("P1S Werkstatt", "192.0.2.31", "geheim")
    with pytest.raises(ValueError, match="Zugangscode fehlt"):
        pi.save_printers([{"db_id": None, "name": "Neu", "ip": "192.0.2.40", "serial": "S2", "code": ""}])
    with pytest.raises(ValueError, match="doppelt"):
        pi.save_printers([{"db_id": p["db_id"], "name": "A", "ip": "x", "serial": "S1", "code": ""},
                          {"db_id": None, "name": "B", "ip": "y", "serial": "S1", "code": "c"}])
    assert len(pi.db_printers()) == 1  # nach Fehler unverändert
    # Umgebungsvariable hat Vorrang und blockiert dieselbe Seriennummer
    monkeypatch.setenv("PRINTER_1_IP", "192.0.2.50")
    monkeypatch.setenv("PRINTER_1_SERIAL", "S1")
    monkeypatch.setenv("PRINTER_1_CODE", "env")
    assert [(p["source"], p["ip"]) for p in pi.load_printers()] == [("env", "192.0.2.50")]
    with pytest.raises(ValueError, match="Umgebungsvariablen"):
        pi.save_printers([{"db_id": p["db_id"], "name": "A", "ip": "x", "serial": "S1", "code": ""}])
    pi.save_printers([{"db_id": p["db_id"], "delete": True}])
    assert pi.db_printers() == []



def test_legacy_import_folder_is_kept(lib, monkeypatch):
    """Bestehende Installationen mit „Vom Drucker“ importieren weiter dorthin."""
    monkeypatch.delenv("PRINTER_IMPORT_DIR", raising=False)
    assert pi.import_dir() == "From printer"
    (lib / "Vom Drucker").mkdir()
    assert pi.import_dir() == "Vom Drucker"
    monkeypatch.setenv("PRINTER_IMPORT_DIR", "Drucke")
    assert pi.import_dir() == "Drucke"
    # Dateien in beiden Importordnern werden über den Namen gruppiert, nicht als ein Projekt
    from pathlib import Path
    assert store.group_key_for(Path("Vom Drucker/P2S/Benchy.gcode.3mf"))[1] == "Benchy"
    assert store.group_key_for(Path("From printer/P2S/Benchy.gcode.3mf"))[1] == "Benchy"


# --- Klipper (Moonraker) -------------------------------------------------------

def test_moonraker_url():
    assert pi.moonraker_url("192.0.2.21") == "http://192.0.2.21:7125"
    assert pi.moonraker_url("k1.local:4408/") == "http://k1.local:4408"
    assert pi.moonraker_url("https://drucker.example") == "https://drucker.example:7125"
    assert pi.moonraker_url("http://192.0.2.21/moonraker") == "http://192.0.2.21/moonraker"


def test_env_moonraker_printer():
    env = {"PRINTER_1_TYPE": "moonraker", "PRINTER_1_NAME": "K1", "PRINTER_1_IP": "192.0.2.21",
           "PRINTER_2_TYPE": "moonraker", "PRINTER_2_IP": "192.0.2.22", "PRINTER_2_API_KEY": "abc",
           "PRINTER_3_TYPE": "octoprint", "PRINTER_3_IP": "192.0.2.23"}  # unbekannt: übersprungen
    printers = pi.env_printers(env)
    assert [(p["name"], p["id"], p["kind"], p["code"]) for p in printers] == [
        ("K1", "moonraker:http://192.0.2.21:7125", "moonraker", ""),
        ("192.0.2.22", "moonraker:http://192.0.2.22:7125", "moonraker", "abc")]


def test_save_moonraker_printer(lib):
    pi.init()
    pi.save_printers([{"db_id": None, "kind": "moonraker", "name": "SV08", "ip": "192.0.2.60", "serial": "",
                       "code": ""}])
    (p,) = pi.db_printers()
    assert (p["kind"], p["id"], p["code"]) == ("moonraker", "moonraker:http://192.0.2.60:7125", "")
    pi.save_printers([{"db_id": p["db_id"], "kind": "moonraker", "name": "SV08", "ip": "192.0.2.60", "code": "key"}])
    assert pi.db_printers()[0]["code"] == "key"
    # Wechsel auf Bambu: der API-Schlüssel wird nicht zum Zugangscode
    with pytest.raises(ValueError, match="Seriennummer fehlt"):
        pi.save_printers([{"db_id": p["db_id"], "kind": "bambu", "name": "SV08", "ip": "192.0.2.60", "code": ""}])
    with pytest.raises(ValueError, match="Zugangscode fehlt"):
        pi.save_printers([{"db_id": p["db_id"], "kind": "bambu", "name": "SV08", "ip": "192.0.2.60",
                           "serial": "S9", "code": ""}])
    with pytest.raises(ValueError, match="doppelt"):
        pi.save_printers([{"db_id": p["db_id"], "kind": "moonraker", "name": "A", "ip": "192.0.2.60"},
                          {"db_id": None, "kind": "moonraker", "name": "B", "ip": "http://192.0.2.60:7125"}])


def test_old_printer_table_gets_kind(lib):
    with store.connect() as con:
        con.execute("CREATE TABLE printers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                    "serial TEXT UNIQUE NOT NULL, ip TEXT NOT NULL, code TEXT NOT NULL)")
        con.execute("INSERT INTO printers (name, serial, ip, code) VALUES ('P1S', 'S1', 'x', 'c')")
    pi.init()
    assert pi.db_printers()[0]["kind"] == "bambu"


@pytest.fixture
def moonraker(tmp_path):
    """Kleiner HTTP-Server mit den beiden Moonraker-Aufrufen, die PlateShelf nutzt."""
    import json
    import urllib.parse
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    files = {}  # Pfad -> (Inhalt, Änderungszeit)
    server_state = {"key": "", "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            server_state["requests"].append(self.path)
            if server_state["key"] and self.headers.get("X-Api-Key") != server_state["key"]:
                self.send_response(401)
                self.end_headers()
                return
            if self.path == "/server/files/list?root=gcodes":
                body = json.dumps({"result": [{"path": p, "modified": m, "size": len(c), "permissions": "rw"}
                                              for p, (c, m) in files.items()]}).encode()
            elif self.path.startswith("/server/files/gcodes/"):
                name = urllib.parse.unquote(self.path[len("/server/files/gcodes/"):])
                if name not in files:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = files[name][0]
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"address": f"127.0.0.1:{server.server_address[1]}", "files": files, "state": server_state}
    server.shutdown()


def test_moonraker_sync(lib, moonraker):
    from test_gcode_file import prusa_gcode
    import time as _time
    now = _time.time()
    moonraker["files"].update({
        "Benchy_PLA_1h2m.gcode": (prusa_gcode().encode(), now - 60),
        "Ordner/Clip mit Umlaut ä.gcode": (b"G1 X1 Y1 E1\n", now - 30),
        ".thumbs/Benchy-300x300.png": (b"png", now),
        "notiz.txt": (b"x", now),
    })
    printer = {"name": "SV08", "id": "moonraker:x", "code": "", "ip": moonraker["address"], "kind": "moonraker"}
    pi.init()
    res = pi.sync_printer(printer)
    assert res["ok"] and res["new"] == 2 and res["files"] == 2, res
    target = lib / "From printer" / "SV08"
    assert sorted(os.listdir(target)) == ["Benchy_PLA_1h2m.gcode", "Ordner_Clip mit Umlaut ä.gcode"]
    assert (target / "Benchy_PLA_1h2m.gcode").read_bytes() == prusa_gcode().encode()
    assert pi.sync_printer(printer)["new"] == 0  # unverändert
    moonraker["files"]["Benchy_PLA_1h2m.gcode"] = (prusa_gcode().encode() + b"; neu\n", now + 120)
    assert pi.sync_printer(printer)["updated"] == 1
    origin = pi.origin_for(["From printer/SV08/Benchy_PLA_1h2m.gcode"])
    assert origin["From printer/SV08/Benchy_PLA_1h2m.gcode"]["printer_name"] == "SV08"
    store.scan(wait=True)
    with store.connect() as con:
        row = con.execute("SELECT printer FROM files WHERE path LIKE '%Benchy%'").fetchone()
    assert row["printer"] == "Original Prusa MK4S"


def test_moonraker_api_key(lib, moonraker):
    moonraker["state"]["key"] = "geheim"
    moonraker["files"]["a.gcode"] = (b"G1 X1 E1\n", 1_700_000_000)
    printer = {"name": "K1", "id": "moonraker:y", "code": "falsch", "ip": moonraker["address"], "kind": "moonraker"}
    pi.init()
    bad = pi.sync_printer(printer)
    assert bad["auth_failed"] and bad["error"].startswith("Anmeldung abgelehnt, API-Schlüssel")
    good = pi.sync_printer({**printer, "code": "geheim"})
    assert good["ok"] and good["new"] == 1


def test_moonraker_unreachable(lib):
    printer = {"name": "K1", "id": "moonraker:z", "code": "", "ip": f"127.0.0.1:{_free_port()}", "kind": "moonraker"}
    res = pi.sync_printer(printer)
    assert not res["ok"] and "nicht erreichbar" in res["error"]
