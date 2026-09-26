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
    target = lib / "Vom Drucker" / "P2S"
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

