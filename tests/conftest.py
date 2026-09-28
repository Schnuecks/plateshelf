"""Gemeinsame Test-Hilfen: jede Test-Funktion bekommt eine eigene Bibliothek und Datenbank."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# Keine Verbindungen zu Druckern aus den Tests heraus (MQTT, Verlaufsabfragen), auch nicht
# aus Hintergrund-Threads, die ein Test gestartet hat und die danach weiterlaufen
os.environ["PRINT_HISTORY"] = "false"
# Keine Hintergrund-Threads (Einlesen, Druckerabruf, Papierkorb): sie überdauern den Test und
# griffen sonst auf die Datenbank späterer Tests zu, was selten zu Fehlern führte
os.environ["BACKGROUND_TASKS"] = "false"
# Ohne Anmeldung, damit die übrigen Tests nicht erst Konten anlegen müssen; test_auth.py schaltet sie ein
os.environ["AUTH"] = "off"


@pytest.fixture(autouse=True)
def german_by_default(monkeypatch):
    """Die Tests prüfen die deutschen Texte; ohne Vorgabe wäre Englisch die Standardsprache."""
    monkeypatch.setenv("DEFAULT_LANGUAGE", "de")


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """Leere Bibliothek + Datenbank in einem temporären Ordner."""
    from app import store

    library = tmp_path / "library"
    data = tmp_path / "data"
    library.mkdir()
    monkeypatch.setattr(store, "LIBRARY_DIR", library)
    monkeypatch.setattr(store, "DATA_DIR", data)
    monkeypatch.setattr(store, "DB_PATH", data / "plateshelf.sqlite")
    monkeypatch.setattr(store, "THUMB_DIR", data / "thumbs")
    monkeypatch.setattr(store, "PREBUILD", False)
    store.init_db()
    return library


@pytest.fixture
def sample():
    """Erzeugt nachgebaute Bambu-.gcode.3mf-Dateien (siehe tools/make_sample.py)."""
    from make_sample import make
    return make
