"""Multi-select in the overview, project as ZIP and web app manifest."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import store, trash


@pytest.fixture
def client(lib, monkeypatch):
    from app import main
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as c:
        yield c


def projects():
    with store.connect() as con:
        return {r["name"]: dict(r) for r in con.execute("SELECT id, name, status, tags FROM projects")}


@pytest.fixture
def three(lib, sample):
    for name in ("Benchy", "Clip", "Vase"):
        sample(lib / f"{name}.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    with store.connect() as con:
        con.execute("UPDATE projects SET tags = 'alt' WHERE name = 'Clip'")
    return projects()


def test_bulk_status_and_tag(client, three):
    page = client.get("/").text
    assert page.count('form="bulk"') == 3 and 'action="/bulk"' in page
    ids = [three["Benchy"]["id"], three["Clip"]["id"]]
    r = client.post("/bulk", data={"pid": ids, "status": "geplant", "add_tag": "Geschenke, alt",
                                   "action": "apply", "next": "/?sort=name"}, follow_redirects=False)
    assert r.headers["location"] == "/?sort=name&bulk=2"
    now = projects()
    assert (now["Benchy"]["status"], now["Benchy"]["tags"]) == ("geplant", "Geschenke, alt")
    assert (now["Clip"]["status"], now["Clip"]["tags"]) == ("geplant", "alt, Geschenke")  # no duplicate tags
    assert now["Vase"]["status"] == "" and now["Vase"]["tags"] == ""
    assert "2 Projekte geändert" in client.get(r.headers["location"]).text
    # „unverändert“ leaves the status as is
    client.post("/bulk", data={"pid": ids, "status": "-", "add_tag": "neu", "action": "apply"})
    assert projects()["Benchy"]["status"] == "geplant"
    assert client.post("/bulk", data={"pid": ids, "status": "quatsch", "action": "apply"}).status_code == 400


def test_bulk_trash(client, three):
    ids = [three["Benchy"]["id"], three["Vase"]["id"]]
    r = client.post("/bulk", data={"pid": ids, "action": "trash"}, follow_redirects=False)
    assert r.headers["location"] == "/?bulk_trashed=2"
    assert sorted(projects()) == ["Clip"]
    assert len(trash.entries()) == 2  # restorable individually
    assert client.post("/bulk", data={"action": "trash"}, follow_redirects=False).headers["location"] == "/"


def test_project_zip(client, lib, sample):
    folder = lib / "Halter"
    folder.mkdir()
    for n in (1, 2):
        sample(folder / f"Halter_plate_{n}.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    pid = projects()["Halter"]["id"]
    with store.connect() as con:
        con.execute("UPDATE projects SET notes = 'Mit Brim', tags = 'Werkstatt', status = 'gedruckt' WHERE id = ?", (pid,))
    assert f'/p/{pid}/download' in client.get(f"/p/{pid}").text
    r = client.get(f"/p/{pid}/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="Halter.zip"' in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert sorted(z.namelist()) == ["Halter_plate_1.gcode.3mf", "Halter_plate_2.gcode.3mf", "Projekt.txt"]
        info = z.read("Projekt.txt").decode()
        assert "Projekt: Halter" in info and "Status: Gedruckt" in info and "Tags: Werkstatt" in info and "Mit Brim" in info
        assert z.read("Halter_plate_1.gcode.3mf") == (folder / "Halter_plate_1.gcode.3mf").read_bytes()
    assert client.get("/p/99999/download").status_code == 404


def test_web_app_manifest(client):
    page = client.get("/").text
    assert '<link rel="manifest" href="/static/manifest.webmanifest">' in page and "apple-touch-icon" in page
    r = client.get("/static/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    manifest = json.loads(r.text)
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    for icon in manifest["icons"]:
        png = client.get("/static/" + icon["src"])
        assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    assert {i["purpose"] for i in manifest["icons"]} == {"any", "maskable"}
