"""Gemeinsame Test-Hilfen: jede Test-Funktion bekommt eine eigene Bibliothek und Datenbank."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """Leere Bibliothek + Datenbank in einem temporären Ordner."""
    from app import store

    library = tmp_path / "library"
    data = tmp_path / "data"
    library.mkdir()
    monkeypatch.setattr(store, "LIBRARY_DIR", library)
    monkeypatch.setattr(store, "DATA_DIR", data)
    monkeypatch.setattr(store, "DB_PATH", data / "druckarchiv.sqlite")
    monkeypatch.setattr(store, "THUMB_DIR", data / "thumbs")
    monkeypatch.setattr(store, "PREBUILD", False)
    store.init_db()
    return library


@pytest.fixture
def sample():
    """Erzeugt nachgebaute Bambu-.gcode.3mf-Dateien (siehe tools/make_sample.py)."""
    from make_sample import make
    return make
