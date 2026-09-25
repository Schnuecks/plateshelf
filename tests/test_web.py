import io

import pytest
from fastapi.testclient import TestClient

from app import store


@pytest.fixture
def client(lib, monkeypatch):
    from app import main
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as c:
        yield c


def test_pages_and_upload_and_trash(client, lib, sample, tmp_path):
    assert client.get("/").status_code == 200
    src = tmp_path / "Benchy_plate_1.gcode.3mf"
    sample(src, [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    r = client.post("/upload", files={"files": (src.name, src.read_bytes())}, follow_redirects=False)
    assert r.status_code == 303 and "/p/" in r.headers["location"]
    page = client.get(r.headers["location"])
    assert page.status_code == 200 and "Benchy" in page.text
    # Dieselbe Datei nochmal: wird als unverändert erkannt
    r2 = client.post("/upload", files={"files": (src.name, src.read_bytes())})
    assert "bereits unverändert" in r2.text
    # Keine 3MF
    r3 = client.post("/upload", files={"files": ("x.3mf", io.BytesIO(b"nope"))})
    assert "keine gültige 3MF-Datei" in r3.text

    with store.connect() as con:
        fid = con.execute("SELECT id FROM files").fetchone()[0]
    assert client.get(f"/thumb/{fid}/plate/1").status_code == 200
    assert client.get(f"/f/{fid}/download").status_code == 200
    r = client.post(f"/f/{fid}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert not (lib / src.name).exists()
    assert list((lib / ".papierkorb").rglob(src.name))


def test_path_traversal_is_blocked(client):
    assert client.get("/photo/1/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/thumb/1/../1").status_code == 404
