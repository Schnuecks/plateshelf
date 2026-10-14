"""Protection against oversized content (zip bombs) and guessing passwords (security review 2026-10)."""
import re
import struct
import zipfile
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth, bgcode, parser, store
from test_bgcode import block


def bomb(path: Path, member: str, size: int, extra: dict | None = None) -> Path:
    """3MF whose member unpacks to size bytes of zeros: a few hundred KB on disk."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("3D/3dmodel.model", '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"/>')
        for name, data in (extra or {}).items():
            z.writestr(name, data)
        with z.open(member, "w", force_zip64=True) as fh:
            chunk = b"\0" * (1024 * 1024)
            for _ in range(size // len(chunk)):
                fh.write(chunk)
    return path


def test_zip_bomb_in_settings_is_refused(tmp_path):
    path = bomb(tmp_path / "Bombe.gcode.3mf", "Metadata/project_settings.config", parser.MAX_MEMBER + 1024 * 1024)
    assert path.stat().st_size < 200_000
    with pytest.raises(parser.TooLarge):
        parser.parse_3mf(path)


def test_zip_bomb_with_lying_header_is_capped(tmp_path):
    """The size in the ZIP header can be faked: reading stops after the limit anyway."""
    path = bomb(tmp_path / "Luege.3mf", "Metadata/plate_1.png", 4 * 1024 * 1024)
    with zipfile.ZipFile(path) as z:
        info = z.getinfo("Metadata/plate_1.png")
        info.file_size = 100           # what a forged header would claim
        with pytest.raises((parser.TooLarge, zipfile.BadZipFile)):
            parser.read_member(z, info.filename, limit=1024 * 1024)


def test_bomb_in_library_becomes_a_read_error(lib):
    bomb(lib / "Bombe.gcode.3mf", "Metadata/project_settings.config", parser.MAX_MEMBER + 1024 * 1024)
    store.scan(wait=True)
    with store.connect() as con:
        error = con.execute("SELECT error FROM files WHERE path = 'Bombe.gcode.3mf'").fetchone()[0]
    assert error and "höchstens" in error


def test_large_model_mesh_does_not_hide_the_designer_data(tmp_path):
    """A 3MF with the whole mesh inline is fine: only the metadata before <resources> is read."""
    mesh = "<vertex x=\"1\" y=\"2\" z=\"3\"/>" * 2_000_000   # about 60 MB unpacked
    model = ('<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<metadata name="Title">Leuchtturm</metadata><metadata name="Designer">Bea</metadata>'
             f'<resources><object id="1"><mesh><vertices>{mesh}</vertices></mesh></object></resources></model>')
    path = tmp_path / "Gross.3mf"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("3D/3dmodel.model", model)
    parsed = parser.parse_3mf(path)
    assert parsed.model["title"] == "Leuchtturm" and parsed.model["designer"] == "Bea"


def test_bgcode_block_bomb_is_refused(tmp_path):
    huge = block(bgcode.FILE_META, b"\0" * (bgcode.MAX_BLOCK + 1), comp=1)
    path = tmp_path / "Bombe.bgcode"
    path.write_bytes(b"GCDE" + struct.pack("<IH", 1, 1) + huge)
    assert path.stat().st_size < 100_000
    with pytest.raises(ValueError, match="zu groß"):
        bgcode.read_meta(path)
    # A block that claims to be small but unpacks to more is refused as well
    data = zlib.compress(b"x=1\n" * 100_000)
    head = struct.pack("<HHI", bgcode.FILE_META, 1, 16) + struct.pack("<I", len(data))
    path.write_bytes(b"GCDE" + struct.pack("<IH", 1, 0) + head + b"\0\0" + data)
    with pytest.raises(ValueError, match="größer als angegeben"):
        bgcode.read_meta(path)


# --- Guessing passwords ------------------------------------------------------------

@pytest.fixture
def fresh_failures(monkeypatch):
    monkeypatch.setattr(auth, "_failures", {})


def test_user_name_is_locked_whatever_the_address(lib, fresh_failures):
    """Changing addresses (e.g. a made-up X-Forwarded-For) do not allow unlimited attempts."""
    auth.create_user("andreas", "andreas-pass-123", "admin")
    for i in range(auth.MAX_USER_FAILURES):
        assert auth.locked_out(f"203.0.113.{i}", "andreas") == 0
        auth.record_failure(f"203.0.113.{i}", "Andreas ")    # case and spaces do not matter
    assert auth.locked_out("198.51.100.7", "andreas") > 0
    assert auth.locked_out("198.51.100.7", "bea") == 0          # other names are not affected
    # The owner can still sign in from an address used before
    auth.log_event("login", "andreas", "192.168.1.20")
    assert auth.locked_out("192.168.1.20", "andreas") == 0


def test_basic_auth_is_locked_after_failures(lib, monkeypatch, fresh_failures):
    from app import main
    monkeypatch.setenv("AUTH", "on")
    auth.create_user("andreas", "andreas-pass-123", "admin")
    with TestClient(main.app) as client:
        for _ in range(auth.MAX_FAILURES):
            assert client.get("/api/stats", auth=("andreas", "falsch")).status_code == 401
        # Now even the right password is refused from this address for a while
        assert client.get("/api/stats", auth=("andreas", "andreas-pass-123")).status_code == 401
        monkeypatch.setattr(auth, "_failures", {})
        assert client.get("/api/stats", auth=("andreas", "andreas-pass-123")).status_code == 200


def test_proxy_address_is_taken_from_the_right():
    """With the trusted networks from the Dockerfile, uvicorn uses the address the proxy added,
    not the first one, which the client can make up."""
    from uvicorn.middleware.proxy_headers import _TrustedHosts
    dockerfile = (Path(__file__).parent.parent / "Dockerfile").read_text(encoding="utf-8")
    trusted = re.search(r"FORWARDED_ALLOW_IPS=(\S+)", dockerfile).group(1)
    assert "*" not in trusted and "--forwarded-allow-ips" not in dockerfile
    hosts = _TrustedHosts(trusted)
    assert hosts.get_trusted_client_address("1.2.3.4, 203.0.113.9")[0] == "203.0.113.9"
    assert hosts.get_trusted_client_address("6.6.6.6, 203.0.113.9, 172.20.0.5")[0] == "203.0.113.9"


def test_printer_download_stops_beyond_the_listed_size(tmp_path):
    from app import printer_import as pi
    with open(tmp_path / "x.part", "wb") as fh:
        out = pi._CappedWriter(fh, 10)
        out.write(b"12345")
        out.write(b"67890")
        with pytest.raises(OSError):
            out.write(b"!")
