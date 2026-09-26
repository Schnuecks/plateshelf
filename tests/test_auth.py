import base64

import pytest
from fastapi.testclient import TestClient

from app import auth, store


@pytest.fixture
def client(lib, monkeypatch):
    from app import main
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    monkeypatch.setattr(auth, "_secret", None)
    monkeypatch.setattr(auth, "_failures", {})
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def secured(client, monkeypatch):
    monkeypatch.setenv("AUTH_USER", "andreas")
    monkeypatch.setenv("AUTH_PASSWORD", "geheim-123")
    return client


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_without_credentials_everything_stays_open(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/stats").status_code == 200
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"
    assert "Abmelden" not in client.get("/").text


def test_browser_login_and_logout(secured, lib, sample):
    sample(lib / "Teil.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    # Ohne Anmeldung: Weiterleitung zur Anmeldung, das Ziel bleibt erhalten
    r = secured.get("/settings?x=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/settings%3Fx%3D1"
    page = secured.get("/login?next=/settings").text
    assert "Benutzername" in page and "Abmelden" not in page and "Papierkorb</a>" not in page
    # Falsches Passwort
    r = secured.post("/login", data={"user": "andreas", "password": "falsch", "next": "/settings"})
    assert r.status_code == 401 and "falsch" in r.text and "sitzung" not in r.headers.get("set-cookie", "")
    # Richtig: Cookie und zurück zum Ziel
    r = secured.post("/login", data={"user": "andreas", "password": "geheim-123", "next": "/settings"},
                     follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    cookie = r.headers["set-cookie"]
    assert "sitzung=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    page = secured.get("/")
    assert page.status_code == 200 and "Teil" in page.text and "Abmelden" in page.text
    # Abmelden
    r = secured.post("/logout", follow_redirects=False)
    assert r.headers["location"] == "/login"
    secured.cookies.clear()
    assert secured.get("/", follow_redirects=False).status_code == 303


def test_open_redirects_are_blocked(secured):
    for target in ("https://boese.example/", "//boese.example/", "/\\boese.example"):
        r = secured.post("/login", data={"user": "andreas", "password": "geheim-123", "next": target},
                         follow_redirects=False)
        assert r.headers["location"] == "/", target
        secured.cookies.clear()


def test_scripts_and_dashboards_use_basic_auth(secured):
    r = secured.get("/api/stats")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert secured.get("/api/stats", headers=basic("andreas", "falsch")).status_code == 401
    r = secured.get("/api/stats", headers=basic("andreas", "geheim-123"))
    assert r.status_code == 200 and "projects" in r.json()
    # Ändernde Anfragen ohne Anmeldung: abgelehnt, nicht weitergeleitet
    assert secured.post("/rescan").status_code == 401
    assert secured.post("/rescan", headers=basic("andreas", "geheim-123"), follow_redirects=False).status_code == 303


def test_public_paths_and_reduced_healthcheck(secured):
    assert secured.get("/static/style.css").status_code == 200
    assert secured.get("/healthz").json() == {"ok": True}  # verrät ohne Anmeldung nichts
    full = secured.get("/healthz", headers=basic("andreas", "geheim-123")).json()
    assert "scan" in full and "printers" in full
    # Sprache und Design lassen sich auch auf der Anmeldeseite umstellen
    assert secured.post("/appearance", data={"sprache": "en", "next": "/login"}, follow_redirects=False).status_code == 303


def test_password_change_ends_sessions(secured, monkeypatch):
    r = secured.post("/login", data={"user": "andreas", "password": "geheim-123"}, follow_redirects=False)
    assert r.status_code == 303 and secured.get("/", follow_redirects=False).status_code == 200
    monkeypatch.setenv("AUTH_PASSWORD", "neues-passwort")
    assert secured.get("/", follow_redirects=False).status_code == 303
    # Manipuliertes oder abgelaufenes Cookie gilt nicht
    assert not auth.session_valid("kaputt")
    assert not auth.session_valid(base64.urlsafe_b64encode(b"andreas|1|abc").decode())


def test_lockout_after_repeated_failures(secured):
    for _ in range(auth.MAX_FAILURES):
        assert secured.post("/login", data={"user": "andreas", "password": "x"}).status_code == 401
    r = secured.post("/login", data={"user": "andreas", "password": "geheim-123"})
    assert r.status_code == 429 and "Zu viele Fehlversuche" in r.text
