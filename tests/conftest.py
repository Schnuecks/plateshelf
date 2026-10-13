"""Shared test helpers: every test function gets its own library and database."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# No connections to printers from the tests (MQTT, history queries), not even
# from background threads that a test started and that keep running afterwards
os.environ["PRINT_HISTORY"] = "false"
# No background threads (scanning, printer polling, trash): they outlive the test and
# would otherwise access the database of later tests, which occasionally caused failures
os.environ["BACKGROUND_TASKS"] = "false"
# Without login, so the other tests don't have to create accounts first; test_auth.py enables it
os.environ["AUTH"] = "off"


@pytest.fixture(autouse=True)
def german_by_default(monkeypatch):
    """The tests check the German texts; without this setting, English would be the default language."""
    monkeypatch.setenv("DEFAULT_LANGUAGE", "de")


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """Empty library + database in a temporary folder."""
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
    """Creates mock Bambu .gcode.3mf files (see tools/make_sample.py)."""
    from make_sample import make
    return make
