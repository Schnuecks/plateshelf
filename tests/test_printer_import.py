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
