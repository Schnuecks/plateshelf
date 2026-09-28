"""Anmeldung: Einrichtung, Sitzungen, Rollen, API-Tokens (siehe app/auth.py)."""
import base64
import re

import pytest
from fastapi.testclient import TestClient

from app import auth, store


@pytest.fixture
def client(lib, monkeypatch):
    from app import main
    monkeypatch.setenv("AUTH", "on")
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    for name in ("AUTH_USER", "AUTH_PASSWORD", "AUTH_PASSWORD_HASH", "SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(auth, "_failures", {})
    monkeypatch.setattr(auth, "_basic_ok", {})
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def accounts(client):
    """Ein Konto je Rolle; Passwort ist jeweils Name + „-pass-123“."""
    for name, role in (("andreas", "admin"), ("bea", "user"), ("gast", "viewer")):
        auth.create_user(name, f"{name}-pass-123", role)
    return client


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def login(c, name, password=None):
    r = c.post("/login", data={"user": name, "password": password or f"{name}-pass-123"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r


def test_auth_off_keeps_everything_open(client, monkeypatch):
    monkeypatch.setenv("AUTH", "off")
    assert client.get("/").status_code == 200
    assert client.get("/api/stats").status_code == 200
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"
    assert client.get("/setup", follow_redirects=False).headers["location"] == "/"
    page = client.get("/").text
    assert "Abmelden" not in page and 'href="/settings"' in page


def test_first_start_needs_setup_code(client, caplog):
    # Ohne Konto führt jede Seite zur Einrichtung, die API verlangt eine Anmeldung
    assert client.get("/", follow_redirects=False).headers["location"] == "/setup"
    assert client.get("/login", follow_redirects=False).headers["location"] == "/setup"
    assert client.get("/api/stats").status_code == 401
    page = client.get("/setup").text
    assert "Einrichtungscode" in page and "docker compose logs plateshelf" in page
    code = auth.setup_code()
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code)
    # Falscher Code
    data = {"code": "AAAA-BBBB-CCCC", "name": "andreas", "password": "geheim-123", "repeat": "geheim-123"}
    r = client.post("/setup", data=data)
    assert r.status_code == 401 and "Einrichtungscode stimmt nicht" in r.text and not auth.has_users()
    # Passwörter passen nicht
    r = client.post("/setup", data={**data, "code": code, "repeat": "anders-123"})
    assert r.status_code == 400 and "stimmen nicht überein" in r.text
    # Richtig, auch klein geschrieben und ohne Bindestriche: Admin angelegt und gleich angemeldet
    r = client.post("/setup", data={**data, "code": code.lower().replace("-", " ")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert [(u["name"], u["role"]) for u in auth.list_users()] == [("andreas", "admin")]
    assert client.get("/").status_code == 200
    # Danach ist die Einrichtung zu und der Code gelöscht
    assert client.get("/setup", follow_redirects=False).headers["location"] == "/"
    with store.connect() as con:
        assert not con.execute("SELECT 1 FROM settings WHERE key = ?", (auth.SETUP_KEY,)).fetchone()


def test_setup_code_is_logged_on_start(lib, monkeypatch, caplog):
    from app import main
    monkeypatch.setenv("AUTH", "on")
    with caplog.at_level("WARNING"), TestClient(main.app):
        pass
    assert auth.setup_code() in caplog.text


def test_existing_env_credentials_become_admin(lib, monkeypatch):
    from app import main
    monkeypatch.setenv("AUTH", "on")
    monkeypatch.setenv("AUTH_USER", "andreas")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", auth.hash_password("aus-der-env-1"))
    with TestClient(main.app) as c:
        assert [(u["name"], u["role"]) for u in auth.list_users()] == [("andreas", "admin")]
        login(c, "andreas", "aus-der-env-1")
        assert c.get("/settings").status_code == 200
    # Das Passwort gehört jetzt der Datenbank: eine geänderte .env überschreibt es nicht
    monkeypatch.setenv("AUTH_PASSWORD_HASH", auth.hash_password("anderes-pass-1"))
    with TestClient(main.app):
        assert auth.authenticate("andreas", "aus-der-env-1") and not auth.authenticate("andreas", "anderes-pass-1")


def test_login_and_logout(accounts, lib, sample):
    sample(lib / "Teil.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    # Ohne Anmeldung: Weiterleitung zur Anmeldung, das Ziel bleibt erhalten
    r = accounts.get("/settings?x=1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/settings%3Fx%3D1"
    page = accounts.get("/login?next=/settings").text
    assert "Benutzername" in page and "Abmelden" not in page and "Papierkorb</a>" not in page
    r = accounts.post("/login", data={"user": "andreas", "password": "falsch", "next": "/settings"})
    assert r.status_code == 401 and "falsch" in r.text and "sitzung" not in r.headers.get("set-cookie", "")
    r = accounts.post("/login", data={"user": "Andreas", "password": "andreas-pass-123", "next": "/settings"},
                      follow_redirects=False)  # Groß- und Kleinschreibung im Namen egal
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    cookie = r.headers["set-cookie"]
    assert "sitzung=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    token = accounts.cookies.get("sitzung")
    page = accounts.get("/")
    assert page.status_code == 200 and "Teil" in page.text and "Abmelden" in page.text and 'href="/account"' in page.text
    # Abmelden beendet die Sitzung auf dem Server: ein kopiertes Cookie hilft nicht mehr
    assert accounts.post("/logout", follow_redirects=False).headers["location"] == "/login"
    accounts.cookies.set("sitzung", token)
    assert accounts.get("/", follow_redirects=False).status_code == 303
    assert auth.session_user(token) is None and auth.session_user("kaputt") is None


def test_open_redirects_are_blocked(accounts):
    for target in ("https://boese.example/", "//boese.example/", "/\\boese.example"):
        r = accounts.post("/login", data={"user": "andreas", "password": "andreas-pass-123", "next": target},
                          follow_redirects=False)
        assert r.headers["location"] == "/", target
        accounts.cookies.clear()


def test_roles(accounts):
    # Admin: alles
    login(accounts, "andreas")
    assert accounts.get("/settings").status_code == 200
    accounts.cookies.clear()
    # Benutzer: bearbeiten ja, Einstellungen nein
    login(accounts, "bea")
    page = accounts.get("/").text
    assert 'href="/upload"' in page and 'href="/settings"' not in page and 'action="/rescan"' in page
    assert accounts.get("/settings").status_code == 403
    assert accounts.post("/settings", data={"strompreis": "1"}).status_code == 403
    assert accounts.get("/settings/backup").status_code == 403
    assert accounts.post("/rescan", follow_redirects=False).status_code == 303
    assert accounts.get("/upload").status_code == 200
    accounts.cookies.clear()
    # Nur lesen: ansehen, herunterladen, Sprache, eigenes Konto; sonst nichts
    login(accounts, "gast")
    page = accounts.get("/").text
    assert 'href="/upload"' not in page and 'href="/trash"' not in page and 'action="/rescan"' not in page
    assert accounts.get("/history").status_code == 200 and accounts.get("/api/stats").status_code == 200
    assert accounts.get("/upload").status_code == 403
    assert accounts.post("/rescan").status_code == 403
    assert accounts.post("/trash/empty").status_code == 403
    assert "Berechtigung" in accounts.post("/rescan").text
    assert accounts.post("/appearance", data={"sprache": "en", "next": "/"}, follow_redirects=False).status_code == 303
    assert accounts.get("/account").status_code == 200


def test_viewer_sees_notes_but_no_forms(accounts, lib, sample):
    sample(lib / "Teil.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF0000FF", 1.0)]}])
    store.scan(wait=True)
    with store.connect() as con:
        pid = con.execute("SELECT id FROM projects").fetchone()[0]
        con.execute("UPDATE projects SET notes = 'Mit Brim drucken', status = 'gedruckt' WHERE id = ?", (pid,))
    login(accounts, "gast")
    page = accounts.get(f"/p/{pid}").text
    assert "Mit Brim drucken" in page and "Gedruckt" in page
    assert 'class="edit"' not in page and "/delete" not in page and "/move" not in page
    assert accounts.post(f"/p/{pid}", data={"name": "Neu"}).status_code == 403


def test_basic_auth_for_scripts(accounts):
    r = accounts.get("/api/stats")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert accounts.get("/api/stats", headers=basic("andreas", "falsch")).status_code == 401
    assert accounts.get("/api/stats", headers=basic("andreas", "andreas-pass-123")).status_code == 200
    assert accounts.get("/api/stats", headers=basic("andreas", "andreas-pass-123")).status_code == 200  # Zwischenspeicher
    # Ändernde Anfragen ohne Anmeldung: abgelehnt, nicht weitergeleitet; Rollen gelten auch hier
    assert accounts.post("/rescan").status_code == 401
    assert accounts.post("/rescan", headers=basic("bea", "bea-pass-123"), follow_redirects=False).status_code == 303
    assert accounts.post("/rescan", headers=basic("gast", "gast-pass-123")).status_code == 403
    # Neues Passwort: der Zwischenspeicher gilt nicht mehr
    auth.set_password(auth.authenticate("andreas", "andreas-pass-123")["id"], "neues-pass-123")
    assert accounts.get("/api/stats", headers=basic("andreas", "andreas-pass-123")).status_code == 401


def test_api_tokens_only_read(accounts):
    login(accounts, "andreas")
    page = accounts.post("/settings/tokens", data={"name": "Homepage"}).text
    token = re.search(r'value="(ps_[^"]+)"', page)[1]
    assert "nur dieses eine Mal" in page
    with store.connect() as con:
        assert not con.execute("SELECT 1 FROM api_tokens WHERE token_hash = ?", (token,)).fetchone()  # nur der Hash
    accounts.cookies.clear()
    bearer = {"Authorization": f"Bearer {token}"}
    assert accounts.get("/api/stats", headers=bearer).status_code == 200
    assert accounts.get("/api/printing", headers={"X-API-Key": token}).status_code == 200
    assert accounts.get("/api/stats", headers={"Authorization": "Bearer ps_falsch"}).status_code == 401
    # Nur lesen, nur unter /api/
    assert accounts.get("/", headers=bearer, follow_redirects=False).status_code == 303
    assert accounts.post("/rescan", headers=bearer).status_code == 401
    (listed,) = auth.list_tokens()
    assert listed["name"] == "Homepage" and listed["hint"] == token[-4:] and listed["last_used"]
    # Widerrufen
    login(accounts, "andreas")
    accounts.post(f"/settings/tokens/{listed['id']}/delete")
    accounts.cookies.clear()
    assert accounts.get("/api/stats", headers=bearer).status_code == 401


def test_user_management(accounts):
    login(accounts, "andreas")
    r = accounts.post("/settings/users", data={"name": "carla", "role": "viewer", "password": "carla-pass-1",
                                               "repeat": "carla-pass-1"}, follow_redirects=False)
    assert r.status_code == 303
    r = accounts.post("/settings/users", data={"name": "CARLA", "role": "user", "password": "x" * 8, "repeat": "x" * 8})
    assert r.status_code == 400 and "gibt es schon" in r.text
    r = accounts.post("/settings/users", data={"name": "dora", "role": "user", "password": "kurz", "repeat": "kurz"})
    assert r.status_code == 400 and "mindestens 8 Zeichen" in r.text
    carla = next(u for u in auth.list_users() if u["name"] == "carla")
    me = next(u for u in auth.list_users() if u["name"] == "andreas")
    # Rolle ändern und Passwort zurücksetzen: Carla wird überall abgemeldet
    other = TestClient(accounts.app)
    login(other, "carla", "carla-pass-1")
    accounts.post(f"/settings/users/{carla['id']}", data={"role": "user", "password": "neu-fuer-carla"})
    assert auth.get_user(carla["id"])["role"] == "user"
    assert other.get("/", follow_redirects=False).status_code == 303
    assert auth.authenticate("carla", "neu-fuer-carla")
    # Den letzten Admin kann man weder herabstufen noch löschen, sich selbst nicht löschen
    r = accounts.post(f"/settings/users/{me['id']}", data={"role": "viewer"})
    assert r.status_code == 400 and "mindestens ein Admin" in r.text
    r = accounts.post(f"/settings/users/{me['id']}/delete")
    assert r.status_code == 400 and "eigene Konto" in r.text
    accounts.post(f"/settings/users/{carla['id']}/delete")
    assert "carla" not in [u["name"] for u in auth.list_users()]


def test_own_account_password_and_devices(accounts):
    phone = TestClient(accounts.app)
    login(phone, "bea")
    login(accounts, "bea")
    page = accounts.get("/account").text
    assert "Angemeldete Geräte" in page and "dieses Gerät" in page and page.count('action="/account/sessions/') == 2
    r = accounts.post("/account/password", data={"current": "falsch", "password": "neu-pass-123", "repeat": "neu-pass-123"})
    assert r.status_code == 400 and "bisherige Passwort ist falsch" in r.text
    r = accounts.post("/account/password", data={"current": "bea-pass-123", "password": "neu-pass-123",
                                                 "repeat": "neu-pass-123"}, follow_redirects=False)
    assert r.headers["location"] == "/account?password=1"
    # Dieses Gerät bleibt angemeldet, das andere nicht
    assert accounts.get("/account").status_code == 200
    assert phone.get("/", follow_redirects=False).status_code == 303
    login(phone, "bea", "neu-pass-123")
    accounts.post("/account/sessions/others")
    assert phone.get("/", follow_redirects=False).status_code == 303 and accounts.get("/").status_code == 200


def test_public_paths_and_reduced_healthcheck(accounts):
    assert accounts.get("/static/style.css").status_code == 200
    assert accounts.get("/healthz").json() == {"ok": True}  # verrät ohne Anmeldung nichts
    full = accounts.get("/healthz", headers=basic("andreas", "andreas-pass-123")).json()
    assert "scan" in full and "printers" in full
    assert accounts.post("/appearance", data={"sprache": "en", "next": "/login"}, follow_redirects=False).status_code == 303


def test_lockout_after_repeated_failures(accounts):
    for _ in range(auth.MAX_FAILURES):
        assert accounts.post("/login", data={"user": "andreas", "password": "x"}).status_code == 401
    r = accounts.post("/login", data={"user": "andreas", "password": "andreas-pass-123"})
    assert r.status_code == 429 and "Zu viele Fehlversuche" in r.text


def test_cli_reset_and_setup_code(lib, monkeypatch, capsys):
    answers = iter(["notfall-pass-1", "notfall-pass-1"])
    monkeypatch.setattr(auth.getpass, "getpass", lambda prompt: next(answers))
    assert auth.main(["auth", "setup-code"]) == 0
    assert capsys.readouterr().out.strip() == auth.setup_code()
    assert auth.main(["auth", "reset", "notfall"]) == 0  # ohne Konto: legt einen Admin an
    assert auth.get_user(auth.authenticate("notfall", "notfall-pass-1")["id"])["role"] == "admin"
    assert auth.main(["auth", "setup-code"]) == 1
    answers = iter(["wieder-da-123", "wieder-da-123"])
    assert auth.main(["auth", "reset", "notfall"]) == 0
    assert auth.authenticate("notfall", "wieder-da-123")
    assert auth.main(["auth", "reset"]) == 2
