import json
import re

import pytest
from fastapi.testclient import TestClient

from app import i18n


@pytest.fixture
def client(lib, monkeypatch):
    from app import main
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def lang():
    """Sprache für Formatierungstests setzen und danach zurück auf Deutsch."""
    yield i18n.set_language
    i18n.set_language("de")


def test_every_text_is_translated():
    from i18n_extract import check
    for code, report in check().items():
        assert report["missing"] == [], f"{code}: fehlende Übersetzungen"
        assert report["unused"] == [], f"{code}: überzählige Einträge"


def test_placeholders_match():
    fields = re.compile(r"\{(\w+)\}")
    for code in i18n.LANGUAGES:
        if code == i18n.FALLBACK:
            continue
        for msg, text in i18n.catalog(code).items():
            assert sorted(fields.findall(msg)) == sorted(fields.findall(text)), f"{code}: {msg!r} -> {text!r}"


def test_pick_language(monkeypatch):
    monkeypatch.delenv("DEFAULT_LANGUAGE", raising=False)
    assert i18n.pick_language(None, "fr-CH,fr;q=0.9,en;q=0.8") == "fr"
    assert i18n.pick_language(None, "pt-BR,en;q=0.5") == "en"
    assert i18n.pick_language(None, "ja") == "en"  # nicht unterstützt: Englisch
    assert i18n.pick_language(None, None) == "en"
    assert i18n.pick_language("it", "fr") == "it"  # Cookie hat Vorrang
    monkeypatch.setenv("DEFAULT_LANGUAGE", "nl")
    assert i18n.pick_language(None, "fr") == "nl"  # Vorgabe schlägt Browsersprache
    assert i18n.pick_language("es", "fr") == "es"


def test_formats_per_language(lang):
    lang("en")
    assert i18n.fmt_num(1234.5, 1) == "1,234.5"
    assert i18n.fmt_money(2.3) == "€ 2.30"
    assert i18n.fmt_printer_date("Aug 22 07:06") == "August 22 07:06"
    lang("fr")
    assert i18n.fmt_num(1234.5, 1) == "1 234,5"
    assert i18n.fmt_printer_date("Nov 29 2025") == "29 novembre 2025"
    lang("es")
    assert i18n.fmt_printer_date("Aug 22 07:06") == "22 de agosto 07:06"
    assert i18n.gettext("Platte {n}", n=2) == "Placa 2"
    assert i18n.ngettext("{n} Datei", "{n} Dateien", 1) == "1 archivo"
    lang("de")
    assert i18n.fmt_money(2.3) == "2,30 €"
    assert i18n.fmt_money(0.001) == "< 0,01 €"


def test_user_error_translates_fields(lang):
    err = i18n.UserError("{label}: {field} fehlt", label="P1S", field="Zugangscode")
    assert str(err) == "P1S: Zugangscode fehlt"
    lang("en")
    assert err.translated() == "P1S: Access code is missing"


def test_language_and_theme_in_pages(client, monkeypatch):
    monkeypatch.delenv("DEFAULT_LANGUAGE", raising=False)
    monkeypatch.delenv("DEFAULT_THEME", raising=False)
    page = client.get("/", headers={"Accept-Language": "en-US,en;q=0.9"}).text
    assert '<html lang="en">' in page and "Settings" in page and "Upload" in page
    assert "data-theme" not in page  # System ist die Vorgabe
    assert 'name="design" value="light"' in page  # Klick auf das Symbol wechselt zu Hell

    # Sprache aus dem Menü der Titelleiste: nur die Sprache ändert sich, zurück zur Seite
    r = client.post("/appearance", data={"sprache": "it", "next": "/trash"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/trash"
    assert "sprache=it" in r.headers["set-cookie"] and "design=" not in r.headers["set-cookie"]
    r = client.post("/appearance", data={"design": "dark", "next": "/settings"}, follow_redirects=False)
    assert "design=dark" in r.headers["set-cookie"] and "sprache" not in r.headers["set-cookie"]
    client.cookies.set("sprache", "it")
    client.cookies.set("design", "dark")
    page = client.get("/settings").text
    assert '<html lang="it" data-theme="dark">' in page and "Impostazioni" in page
    assert 'name="design" value="system"' in page  # nach Dunkel kommt wieder System
    assert 'value="it" lang="it" role="menuitemradio" aria-checked="true"' in page
    texts = json.loads(re.search(r'<script id="i18n" type="application/json">(.*?)</script>', page).group(1))
    assert texts["Außenwand"] == "Parete esterna"

    # Automatisch: Cookie wird entfernt, danach gilt wieder die Browsersprache
    r = client.post("/appearance", data={"sprache": "auto"}, follow_redirects=False)
    assert 'sprache=""' in r.headers["set-cookie"] or "sprache=;" in r.headers["set-cookie"]
    # Nur Pfade dieser Seite als Ziel, keine fremden Adressen
    for bad in ("https://example.com/", "//example.com/"):
        r = client.post("/appearance", data={"design": "light", "next": bad}, follow_redirects=False)
        assert r.headers["location"] == "/"
    client.cookies.clear()
    monkeypatch.setenv("DEFAULT_THEME", "light")
    assert '<html lang="en" data-theme="light">' in client.get("/").text  # ohne Vorgabe: Englisch


def test_printer_errors_are_translated(client, monkeypatch):
    from app import printer_import as pi
    monkeypatch.setattr(pi, "sync_all", lambda: None)
    client.cookies.set("sprache", "fr")
    r = client.post("/settings/printers", data={
        "db_id": [""], "name": ["Neu"], "ip": ["192.0.2.31"], "serial": ["S2"], "code": [""]})
    assert r.status_code == 400 and "Neu : Code d’accès manquant" in r.text


def test_preview_status_translates_errors(client, lib, sample, monkeypatch):
    """Regression: die Vorschau-API überschrieb die Übersetzungsfunktion _ mit einer Variablen."""
    from app import gcode_preview, store
    sample(lib / "Teil.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    with store.connect() as con:
        fid = con.execute("SELECT id FROM files").fetchone()[0]
    monkeypatch.setattr(gcode_preview, "status",
                        lambda *a, **k: {"state": "error", "progress": 0, "error": "Keine Druckbahnen im G-Code gefunden"})
    client.cookies.set("sprache", "en")
    r = client.get(f"/api/preview/{fid}/1")
    assert r.status_code == 200 and r.json()["error"] == "No toolpaths found in the G-code"
