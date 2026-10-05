import io
import re

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
    assert list((lib / ".trash").rglob(src.name))


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

    page = client.get("/settings")
    assert page.status_code == 200 and "Bambu Lab P1S" in page.text
    r = client.post("/settings", data={
        "strompreis": "0,40", "leistung_standard": "100", "leistung:Bambu Lab P1S": "150"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings?saved=1#power"  # zurück zu „Kosten“
    with store.connect() as con:
        p = main.load_projects(con)[0]
    assert round(p["energy_cost"], 3) == round(0.3 * 0.40, 3)  # 150 W × 2 h = 0,3 kWh
    # Leeres Feld: wieder Standardleistung
    client.post("/settings", data={"strompreis": "0,40", "leistung_standard": "100", "leistung:Bambu Lab P1S": ""})
    with store.connect() as con:
        assert round(main.load_projects(con)[0]["energy_kwh"], 3) == 0.2
    assert client.post("/settings", data={"strompreis": "abc", "leistung_standard": "100"}).status_code == 400
    # 0 € (z. B. Solarstrom) bleibt 0 und wird nicht durch den Standard ersetzt
    client.post("/settings", data={"strompreis": "0", "leistung_standard": "100"})
    with store.connect() as con:
        assert main.load_projects(con)[0]["energy_cost"] == 0



def test_filament_price_rules(client, lib, sample):
    """Preisregeln nach Stichwort (längstes gewinnt) vor Dateipreis vor Standardpreis; alles rückwirkend."""
    from app import main, statistics
    sample(lib / "Ohne.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 10.0)]}],
           types=("PLA",), costs=("0",))   # Profil „Bambu PLA Basic @BBL P1P“, kein Preis
    sample(lib / "Mit.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 10.0)]}],
           types=("PETG",), costs=("20",))  # Profil „Bambu PETG Basic @BBL P1P“, 20 €/kg
    store.scan(wait=True)

    def material():
        with store.connect() as con:
            return {p["name"]: round(p["plates"][0]["cost"], 3) for p in main.load_projects(con)}

    def save(**fields):
        r = client.post("/settings", data={"strompreis": "0,35", "leistung_standard": "100", **fields},
                        follow_redirects=False)
        assert r.status_code == 303

    assert material() == {"Ohne": 0, "Mit": 0.2}
    page = client.get("/settings").text
    assert "<datalist" not in page and 'name="neues_stichwort"' in page   # freies Feld, keine Vorschläge

    save(neues_stichwort="  pla ", neuer_filamentpreis="20")   # Groß-/Kleinschreibung egal, Leerzeichen weg
    assert material() == {"Ohne": 0.2, "Mit": 0.2}
    page = client.get("/settings").text
    assert re.search(r'<td>pla</td>\s*<td class="hint">1 Platte</td>', page)

    save(**{"filamentpreis:pla": "20", "neues_stichwort": "Basic", "neuer_filamentpreis": "25,50"})
    assert material() == {"Ohne": 0.255, "Mit": 0.255}   # „Basic“ ist länger als „pla“ und schlägt die Datei
    page = client.get("/settings").text
    assert re.search(r'<td>pla</td>\s*<td class="hint">trifft nichts</td>', page)
    assert re.search(r'<td>Basic</td>\s*<td class="hint">2 Platten</td>', page)
    with store.connect() as con:
        facts = statistics._file_facts(con, store.get_settings(con))
    assert round(facts["Ohne.gcode.3mf"]["cost"], 3) == 0.255

    rules = {"filamentpreis:pla": "20", "filamentpreis:Basic": ""}   # Feld leeren = Regel entfernen
    save(**rules)
    assert material() == {"Ohne": 0.2, "Mit": 0.2} and "Basic</td>" not in client.get("/settings").text
    save(**{"filamentpreis:pla": ""})
    assert material() == {"Ohne": 0, "Mit": 0.2}

    # Standardpreis: nur ohne Regel und ohne Preis in der Datei (z. B. Cura)
    save(filamentpreis_standard="18")
    assert material() == {"Ohne": 0.18, "Mit": 0.2}
    assert re.search(r'name="filamentpreis_standard"\s+value="18,00"', client.get("/settings").text)
    # Schalter: Preise aus den Dateien ignorieren, dann gilt ohne Regel überall der Standardpreis
    save(filamentpreis_standard="18", dateipreise_ignorieren="1")
    assert material() == {"Ohne": 0.18, "Mit": 0.18}
    assert 'name="dateipreise_ignorieren" value="1" checked' in client.get("/settings").text
    save(filamentpreis_standard="18", dateipreise_ignorieren="1", neues_stichwort="PETG", neuer_filamentpreis="30")
    assert material() == {"Ohne": 0.18, "Mit": 0.3}
    save(filamentpreis_standard="", **{"filamentpreis:PETG": ""})   # Schalter aus (nicht angehakt)
    assert material() == {"Ohne": 0, "Mit": 0.2}


def test_footer_shows_version(client):
    from app import main
    page = client.get("/").text
    assert f"PlateShelf {main.APP_VERSION} von Schnuecks" in page
    assert "blob/main/LICENSE" in page and "AGPL-3.0" in page


def test_version_formatting(monkeypatch):
    from app import main
    for raw, shown in [("v0.2.0", "0.2.0"), ("", "dev"), ("dev-0123456789abcdef", "dev-0123456")]:
        monkeypatch.setenv("APP_VERSION", raw)
        assert main._app_version() == shown


def test_printer_settings_page(client, monkeypatch):
    from app import printer_import as pi
    monkeypatch.setattr(pi, "sync_all", lambda: None)
    r = client.post("/settings/printers", data={
        "db_id": [""], "name": ["P1S"], "ip": ["192.0.2.30"], "serial": ["S1"], "code": ["geheim123"]},
        follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/settings").text
    assert "P1S" in page and "192.0.2.30" in page and "S1" in page
    assert "geheim123" not in page  # Zugangscode wird nie ausgegeben
    db_id = pi.db_printers()[0]["db_id"]
    r = client.post("/settings/printers", data={
        "db_id": [str(db_id), ""], "name": ["P1S", "Neu"], "ip": ["192.0.2.30", "192.0.2.31"],
        "serial": ["S1", "S2"], "code": ["", ""]})
    assert r.status_code == 400 and "Neu: Zugangscode fehlt" in r.text and "192.0.2.31" in r.text
    r = client.post("/settings/printers", data={
        "db_id": [str(db_id), ""], "name": ["P1S", ""], "ip": ["192.0.2.30", ""],
        "serial": ["S1", ""], "code": ["", ""], "delete": [str(db_id)]}, follow_redirects=False)
    assert r.status_code == 303 and pi.db_printers() == []


def test_env_printers_are_read_only(client, monkeypatch):
    for k, v in {"PRINTER_1_NAME": "A1 mini", "PRINTER_1_IP": "192.0.2.60",
                 "PRINTER_1_SERIAL": "ENV1", "PRINTER_1_CODE": "envcode9"}.items():
        monkeypatch.setenv(k, v)
    page = client.get("/settings").text
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
    page = client.get("/settings").text
    assert "<code>From printer/&lt;Name&gt;/</code>" in page
    assert "&lt;code&gt;" not in page


def test_api_stats_for_dashboards(client, lib, sample, monkeypatch):
    stats = client.get("/api/stats").json()
    assert (stats["projects"], stats["files"], stats["print_time_s"], stats["cost_eur"]) == (0, 0, 0, None)
    # 2 Stunden, 10 g zu 20 €/kg; Strom 100 W × 2 h × 0,35 €/kWh = 0,07 €
    sample(lib / "Teil.gcode.3mf", [{"idx": 1, "sec": 7200, "layers": 5, "fil": [(1, "#FF0000FF", 10.0)]}],
           costs=("20",))
    (lib / "Box").mkdir()
    sample(lib / "Box" / "Deckel.gcode.3mf", [{"idx": 1, "sec": 1800, "layers": 5, "fil": [(1, "#00FF00FF", 1500.0)]},
                                              {"idx": 2, "sec": 1800, "layers": 5, "fil": [(1, "#00FF00FF", 500.0)]}])
    store.scan(wait=True)
    stats = client.get("/api/stats").json()
    assert (stats["projects"], stats["files"], stats["plates"]) == (2, 2, 3)
    assert stats["print_time_s"] == 10800 and stats["print_time_h"] == 3.0
    assert stats["filament_g"] == 2010.0 and stats["filament_kg"] == 2.01
    assert stats["new_files_7d"] == 2 and stats["last_added"]
    assert stats["cost_eur"] is not None and stats["energy_kwh"] == 0.3
    assert stats["trash_items"] == 0 and stats["printers_total"] == 0


def test_foreign_sites_cannot_change_anything(client, lib, sample, monkeypatch):
    """CSRF: Formulare von fremden Seiten werden abgelehnt, eigene und Skripte ohne Origin nicht."""
    from app import main
    sample(lib / "Teil.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    with store.connect() as con:
        fid = con.execute("SELECT id FROM files").fetchone()[0]
    for headers in ({"Origin": "https://boese.example"}, {"Origin": "null"},
                    {"Referer": "http://boese.example/seite"}, {"Origin": "http://testserver.boese.example"}):
        r = client.post(f"/f/{fid}/delete", headers=headers, follow_redirects=False)
        assert r.status_code == 403 and "fremden Seite" in r.text, headers
    assert (lib / "Teil.gcode.3mf").exists()
    # Hinter einem Proxy: der weitergereichte Host zählt
    r = client.post("/settings", data={"strompreis": "0,35", "leistung_standard": "100"},
                    headers={"Origin": "https://druck.example", "X-Forwarded-Host": "druck.example"},
                    follow_redirects=False)
    assert r.status_code == 303
    # Ausdrücklich erlaubte Adresse
    monkeypatch.setattr(main, "TRUSTED_ORIGINS", {"https://dashboard.example"})
    r = client.post("/rescan", headers={"Origin": "https://dashboard.example"}, follow_redirects=False)
    assert r.status_code == 303
    # Eigene Seite und Anfragen ohne Origin (Skripte, Tests) funktionieren
    r = client.post(f"/f/{fid}/delete", headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert r.status_code == 303 and not (lib / "Teil.gcode.3mf").exists()
    assert client.get("/", headers={"Origin": "https://boese.example"}).status_code == 200  # Lesen bleibt erlaubt


def test_about_page_and_logo(client):
    from app import main
    page = client.get("/about").text
    assert "Über PlateShelf" in page and main.APP_VERSION in page and "github.com/schnuecks/plateshelf" in page
    assert 'href="/about"' in client.get("/").text  # Link im Footer
    logo = client.get("/static/logo.svg")
    assert logo.status_code == 200 and "<svg" in logo.text


def test_moonraker_printer_in_settings(client, monkeypatch):
    from app import printer_import as pi
    monkeypatch.setattr(pi, "sync_all", lambda: None)
    r = client.post("/settings/printers", data={
        "db_id": [""], "kind": ["moonraker"], "name": ["SV08"], "ip": ["192.0.2.60"], "serial": [""],
        "code": [""]}, follow_redirects=False)
    assert r.status_code == 303
    (p,) = pi.db_printers()
    assert (p["kind"], p["id"]) == ("moonraker", "moonraker:http://192.0.2.60:7125")
    page = client.get("/settings").text
    assert 'data-kind="moonraker"' in page and '<option value="moonraker" selected>' in page
    # Ohne Typ (ältere Formulare) bleibt es Bambu mit Pflichtfeldern
    r = client.post("/settings/printers", data={
        "db_id": [str(p["db_id"]), ""], "kind": ["moonraker"], "name": ["SV08", "X1C"], "ip": ["192.0.2.60", "192.0.2.61"],
        "serial": ["", ""], "code": ["", ""]})
    assert r.status_code == 400 and "X1C: Seriennummer fehlt" in r.text


def test_upload_gcode(client, lib):
    from test_gcode_file import prusa_gcode
    r = client.post("/upload", files=[("files", ("Benchy.gcode", prusa_gcode().encode(), "text/plain"))],
                    follow_redirects=False)
    assert r.status_code == 303 and (lib / "Benchy.gcode").exists()
    r = client.post("/upload", files=[("files", ("kaputt.gcode", b"\x00\x01binary", "text/plain"))])
    assert r.status_code == 400 and "keine gültige G-Code-Datei" in r.text
    r = client.post("/upload", files=[("files", ("bild.png", b"x", "image/png"))])
    assert "keine .3mf-, .gcode- oder .bgcode-Datei" in r.text
    with store.connect() as con:
        fid = con.execute("SELECT id FROM files").fetchone()[0]
    r = client.get(f"/thumb/{fid}/plate/1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert client.get(f"/api/preview/{fid}/1").status_code == 200


def test_prusalink_printer_in_settings(client, monkeypatch):
    from app import printer_import as pi
    monkeypatch.setattr(pi, "sync_all", lambda: None)
    r = client.post("/settings/printers", data={
        "db_id": [""], "kind": ["prusalink"], "name": ["MK4S"], "ip": ["192.0.2.80"], "serial": [""],
        "code": ["pw"]}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/settings").text
    assert '<option value="prusalink" selected>' in page and 'value="pw"' not in page
    assert pi.db_printers()[0]["id"] == "prusalink:http://192.0.2.80"


def test_backup_download(client, lib, sample, tmp_path):
    import sqlite3
    sample(lib / "Teil.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    with store.connect() as con:
        con.execute("UPDATE projects SET notes = 'Wichtige Notiz'")
    r = client.get("/settings/backup")
    assert r.status_code == 200 and r.headers["content-type"] == "application/vnd.sqlite3"
    assert 'filename="plateshelf-backup-' in r.headers["content-disposition"]
    copy = tmp_path / "backup.sqlite"
    copy.write_bytes(r.content)
    con = sqlite3.connect(copy)
    try:
        assert con.execute("SELECT notes FROM projects").fetchone()[0] == "Wichtige Notiz"
        assert con.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    finally:
        con.close()
    assert "#backup" in client.get("/settings").text


def test_settings_explain_sections_not_single_fields(client):
    """Einheitlich: Erklärungen stehen unter den Überschriften, nicht unter einzelnen Eingabefeldern."""
    page = client.get("/settings").text
    for label in re.findall(r"<label\b.*?</label>", page, re.S):
        assert 'class="hint"' not in label, label
