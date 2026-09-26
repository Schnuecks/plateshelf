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


def test_energy_costs_and_settings(client, lib, sample):
    # 2 Stunden auf einem P1S, 10 g Filament zu 20 €/kg
    sample(lib / "Teil.gcode.3mf", [{"idx": 1, "sec": 7200, "layers": 5, "fil": [(1, "#FF0000FF", 10.0)]}],
           costs=("20",))
    store.scan(wait=True)
    from app import main
    with store.connect() as con:
        p = main.load_projects(con)[0]
    # Standard: 100 W, 0,35 €/kWh -> 0,2 kWh -> 0,07 €; Material 0,20 €
    assert round(p["energy_kwh"], 3) == 0.2
    assert round(p["energy_cost"], 3) == 0.07
    assert round(p["total_cost"], 3) == 0.27

    page = client.get("/einstellungen")
    assert page.status_code == 200 and "Bambu Lab P1S" in page.text
    r = client.post("/einstellungen", data={
        "strompreis": "0,40", "leistung_standard": "100", "leistung:Bambu Lab P1S": "150"}, follow_redirects=False)
    assert r.status_code == 303
    with store.connect() as con:
        p = main.load_projects(con)[0]
    assert round(p["energy_cost"], 3) == round(0.3 * 0.40, 3)  # 150 W × 2 h = 0,3 kWh
    # Leeres Feld: wieder Standardleistung
    client.post("/einstellungen", data={"strompreis": "0,40", "leistung_standard": "100", "leistung:Bambu Lab P1S": ""})
    with store.connect() as con:
        assert round(main.load_projects(con)[0]["energy_kwh"], 3) == 0.2
    assert client.post("/einstellungen", data={"strompreis": "abc", "leistung_standard": "100"}).status_code == 400
    # 0 € (z. B. Solarstrom) bleibt 0 und wird nicht durch den Standard ersetzt
    client.post("/einstellungen", data={"strompreis": "0", "leistung_standard": "100"})
    with store.connect() as con:
        assert main.load_projects(con)[0]["energy_cost"] == 0


def test_footer_shows_version(client):
    from app import main
    page = client.get("/").text
    assert f"Druckarchiv {main.APP_VERSION} von Schnuecks" in page
    assert "blob/main/LICENSE" in page and "AGPL-3.0" in page


def test_version_formatting(monkeypatch):
    from app import main
    for raw, shown in [("v0.2.0", "0.2.0"), ("", "dev"), ("dev-0123456789abcdef", "dev-0123456")]:
        monkeypatch.setenv("APP_VERSION", raw)
        assert main._app_version() == shown


def test_printer_settings_page(client, monkeypatch):
    from app import printer_import as pi
    monkeypatch.setattr(pi, "sync_all", lambda: None)
    r = client.post("/einstellungen/drucker", data={
        "db_id": [""], "name": ["P1S"], "ip": ["192.0.2.30"], "serial": ["S1"], "code": ["geheim123"]},
        follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/einstellungen").text
    assert "P1S" in page and "192.0.2.30" in page and "S1" in page
    assert "geheim123" not in page  # Zugangscode wird nie ausgegeben
    db_id = pi.db_printers()[0]["db_id"]
    r = client.post("/einstellungen/drucker", data={
        "db_id": [str(db_id), ""], "name": ["P1S", "Neu"], "ip": ["192.0.2.30", "192.0.2.31"],
        "serial": ["S1", "S2"], "code": ["", ""]})
    assert r.status_code == 400 and "Neu: Zugangscode fehlt" in r.text and "192.0.2.31" in r.text
    r = client.post("/einstellungen/drucker", data={
        "db_id": [str(db_id), ""], "name": ["P1S", ""], "ip": ["192.0.2.30", ""],
        "serial": ["S1", ""], "code": ["", ""], "delete": [str(db_id)]}, follow_redirects=False)
    assert r.status_code == 303 and pi.db_printers() == []


def test_env_printers_are_read_only(client, monkeypatch):
    for k, v in {"PRINTER_1_NAME": "A1 mini", "PRINTER_1_IP": "192.0.2.60",
                 "PRINTER_1_SERIAL": "ENV1", "PRINTER_1_CODE": "envcode9"}.items():
        monkeypatch.setenv(k, v)
    page = client.get("/einstellungen").text
    assert "A1 mini" in page and "über Umgebungsvariablen eingerichtet" in page
    assert "envcode9" not in page and 'value="ENV1"' not in page  # kein Eingabefeld


def test_printer_date_in_german():
    from app.main import fmt_printer_date
    assert fmt_printer_date("Aug 22 07:06") == "22. August 07:06"
    assert fmt_printer_date("Mar 3 2025") == "3. März 2025"
    assert fmt_printer_date("Nov 29 2025") == "29. November 2025"
    assert fmt_printer_date("unbekannt") == "unbekannt"
    assert fmt_printer_date(None) == ""


def test_settings_hint_shows_import_folder_as_code(client):
    """Regression: Verketten mit ~ hatte das <code> im Hinweis mit escapt."""
    page = client.get("/einstellungen").text
    assert "<code>Vom Drucker/&lt;Name&gt;/</code>" in page
    assert "&lt;code&gt;" not in page
