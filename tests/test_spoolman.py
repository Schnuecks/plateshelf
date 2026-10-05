"""Optionale Spoolman-Anbindung gegen eine nachgebaute API (siehe app/spoolman.py)."""
import time

import pytest
from fastapi.testclient import TestClient

from app import spoolman, store

PEANUT = {"id": 42, "name": "Panchroma Pastell Peanut", "material": "PLA", "color_hex": "C29572",
          "price": 19.99, "weight": 1000, "vendor": {"name": "Polymaker"}}
BLACK_A = {"id": 7, "name": "PETG HF Black", "material": "PETG HF", "color_hex": "000000", "weight": 1000}
BLACK_B = {"id": 8, "name": "PETG Translucent Black", "material": "PETG Translucent", "color_hex": "000000",
           "weight": 1000}


class FakeSpoolman:
    def __init__(self):
        self.spools = [
            {"id": 39, "filament": PEANUT, "remaining_weight": 911.9, "initial_weight": 1000},
            {"id": 3, "filament": BLACK_A, "remaining_weight": 500, "price": 30, "initial_weight": 1000},
            {"id": 4, "filament": BLACK_B, "remaining_weight": 800},
            {"id": 5, "filament": PEANUT, "remaining_weight": 0, "archived": True},
        ]
        self.used = []
        self.down = False

    def __call__(self, method, path, base, body=None):
        if self.down:
            raise OSError("Verbindung abgelehnt")
        assert base == "http://spoolman:8000"
        if method == "GET" and path == "/api/v1/spool":
            return self.spools
        if method == "PUT" and path.endswith("/use"):
            sid = int(path.split("/")[-2])
            self.used.append((sid, body["use_weight"]))
            for s in self.spools:
                if s["id"] == sid:
                    s["remaining_weight"] -= body["use_weight"]
            return {}
        raise AssertionError((method, path))


@pytest.fixture
def api(monkeypatch):
    fake = FakeSpoolman()
    monkeypatch.setattr(spoolman, "_request", fake)
    monkeypatch.setattr(spoolman, "state", {"ok": None, "error": "", "at": 0.0, "spools": []})
    monkeypatch.delenv("SPOOLMAN_URL", raising=False)
    return fake


@pytest.fixture
def client(lib, monkeypatch, api):
    from app import main
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as c:
        yield c


def connect():
    store.save_settings({spoolman.URL_KEY: "http://spoolman:8000"})
    spoolman.state["at"] = 0


def test_matching_by_colour_and_base_material(api):
    connect()
    all_spools = spoolman.spools()
    assert [s["id"] for s in all_spools] == [39, 3, 4]                         # archivierte nicht
    assert spoolman.match({"color": "#C29572FF", "type": "PLA"}, all_spools)[0]["id"] == 39
    assert spoolman.match({"color": "#c29572", "type": "PLA Matte"}, all_spools)  # Grundmaterial zählt
    assert spoolman.match({"color": "#C29572FF", "type": "PETG"}, all_spools) is None
    assert spoolman.match({"color": "#000000FF", "type": "PETG"}, all_spools) is None  # zwei Filamente: nicht raten
    assert round(spoolman.price_for({"color": "#C29572FF", "type": "PLA"}, all_spools), 2) == 19.99
    assert spoolman.price_for({"color": "#000000FF", "type": "PETG HF"}, all_spools) == 30  # Spulenpreis


def test_prices_and_remaining_on_the_project_page(client, lib, sample, api):
    sample(lib / "Eevee.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#C29572FF", 43.5)]}],
           colours=("#C29572FF",), types=("PLA",), costs=("0",))
    store.scan(wait=True)
    from app import main
    with store.connect() as con:
        pid = main.load_projects(con)[0]["id"]
    assert "Spoolman" not in client.get(f"/p/{pid}").text                    # ohne Adresse: nichts
    connect()
    with store.connect() as con:
        assert round(main.load_projects(con)[0]["plates"][0]["cost"], 3) == round(43.5 * 19.99 / 1000, 3)
    page = client.get(f"/p/{pid}").text
    assert "#39 Polymaker Panchroma Pastell Peanut" in page and "noch 912 g auf der Rolle" in page
    # Preisregeln gehen weiterhin vor
    store.save_settings({"filamentpreis:PLA": 10})
    with store.connect() as con:
        assert round(main.load_projects(con)[0]["plates"][0]["cost"], 3) == 0.435
    # Nicht erreichbar: Seite bleibt heil, keine falschen Angaben
    api.down = True
    spoolman.state["at"] = 0
    page = client.get(f"/p/{pid}")
    assert page.status_code == 200 and "auf der Rolle" not in page.text


def add_print(lib, sample, name, colour, material, grams, ended):
    sample(lib / f"{name}.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, colour, grams)]}],
           colours=(colour,), types=(material,))
    store.scan(wait=True)
    with store.connect() as con:
        con.execute("INSERT INTO print_jobs (printer, printer_name, job_key, local_path, status, started, ended, "
                    "plate) VALUES ('S', 'P2S', ?, ?, 'completed', ?, ?, 1)",
                    (name, f"{name}.gcode.3mf", ended - 600, ended))
        return con.execute("SELECT id FROM print_jobs WHERE job_key = ?", (name,)).fetchone()[0]


def test_booking_only_when_switched_on_and_only_once(client, lib, sample, api):
    connect()
    old = add_print(lib, sample, "Alt", "#C29572FF", "PLA", 10.0, time.time() - 86400)
    assert spoolman.book_pending() == 0 and api.used == []                   # ausgeschaltet: nie buchen
    r = client.post("/settings", data={"strompreis": "0,35", "leistung_standard": "100",
                                       "spoolman_url": "http://spoolman:8000", "spoolman_buchen": "1"},
                    follow_redirects=False)
    assert r.status_code == 303
    new = add_print(lib, sample, "Neu", "#C29572FF", "PLA", 43.5, time.time() + 1)
    amb = add_print(lib, sample, "Schwarz", "#000000FF", "PETG", 12.0, time.time() + 2)
    assert spoolman.book_pending() == 1
    assert api.used == [(39, 43.5)]                                          # alter Druck vor dem Einschalten nicht
    assert spoolman.book_pending() == 0 and len(api.used) == 1              # nie doppelt
    rows = spoolman.bookings_for([old, new, amb])
    assert old not in rows and rows[new][0]["booked"] and rows[amb][0]["booked"] is None

    # Mehrdeutig: im Verlauf die Spule wählen
    page = client.get("/history").text
    assert "Spoolman: 43,5 g von #39" in page and f'name="spule:{rows[amb][0]["id"]}"' in page
    client.post(f"/history/{amb}/spoolman", data={f"spule:{rows[amb][0]['id']}": "4"})
    assert api.used[-1] == (4, 12.0)
    client.post(f"/history/{amb}/spoolman", data={f"spule:{rows[amb][0]['id']}": "4"})
    assert len(api.used) == 2                                                # schon gebucht: nichts mehr

    # Ausschalten: neue Drucke werden nicht mehr gebucht
    client.post("/settings", data={"strompreis": "0,35", "leistung_standard": "100",
                                   "spoolman_url": "http://spoolman:8000"})
    add_print(lib, sample, "Danach", "#C29572FF", "PLA", 5.0, time.time() + 3)
    assert spoolman.book_pending() == 0 and len(api.used) == 2


def test_unreachable_spoolman_books_later(client, lib, sample, api):
    connect()
    store.save_settings({spoolman.BOOK_KEY: time.time() - 60})
    job = add_print(lib, sample, "Später", "#C29572FF", "PLA", 20.0, time.time())
    api.down = True
    spoolman.state["at"] = 0
    assert spoolman.book_pending() == 0 and not spoolman.bookings_for([job])
    api.down = False
    spoolman.state["at"] = 0
    assert spoolman.book_pending() == 1 and api.used == [(39, 20.0)]


def test_settings_show_status_and_env_address(client, api, monkeypatch):
    connect()
    page = client.get("/settings").text
    assert "Verbunden: 3 Spulen" in page and 'name="spoolman_buchen"' in page
    monkeypatch.setenv("SPOOLMAN_URL", "http://spoolman:8000")
    page = client.get("/settings").text
    assert "Gesetzt über SPOOLMAN_URL" in page
    assert client.post("/settings", data={"strompreis": "0,35", "leistung_standard": "100",
                                          "spoolman_url": "ftp://x"}, follow_redirects=False).status_code == 303  # Env gilt
    monkeypatch.delenv("SPOOLMAN_URL")
    assert client.post("/settings", data={"strompreis": "0,35", "leistung_standard": "100",
                                          "spoolman_url": "ftp://x"}).status_code == 400
