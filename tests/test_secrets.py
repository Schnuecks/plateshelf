"""Geheimnisse: SECRET_KEY aus der Umgebung, verschlüsselte Druckerzugänge, Passwort-Hash, *_FILE."""
import base64

import pytest
from fastapi.testclient import TestClient

from app import auth, crypto
from app import printer_import as pi
from app import store

KEY = "k" * 48
OTHER_KEY = "x" * 48


def raw_codes():
    with store.connect() as con:
        return [r[0] for r in con.execute("SELECT code FROM printers ORDER BY id")]


def test_encrypt_roundtrip(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert crypto.encrypt("geheim") == "geheim"  # ohne Schlüssel: unverändert
    monkeypatch.setenv("SECRET_KEY", KEY)
    sealed = crypto.encrypt("geheim")
    assert sealed.startswith("enc:v1:") and "geheim" not in sealed
    assert sealed != crypto.encrypt("geheim")  # zufällige Nonce
    assert crypto.decrypt(sealed) == "geheim" and crypto.decrypt("klartext") == "klartext"
    assert crypto.encrypt(sealed) == sealed  # nie doppelt
    monkeypatch.setenv("SECRET_KEY", OTHER_KEY)
    with pytest.raises(crypto.SecretError, match="passt nicht"):
        crypto.decrypt(sealed)
    monkeypatch.delenv("SECRET_KEY")
    with pytest.raises(crypto.SecretError, match="fehlt"):
        crypto.decrypt(sealed)


def test_env_value_from_file(tmp_path, monkeypatch):
    secret = tmp_path / "key"
    secret.write_text("aus-datei\n")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY_FILE", str(secret))
    assert crypto.secret_key() == "aus-datei"
    monkeypatch.setenv("SECRET_KEY", "direkt")  # direkter Wert hat Vorrang
    assert crypto.secret_key() == "direkt"
    monkeypatch.delenv("SECRET_KEY")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / "fehlt"))
    assert crypto.secret_key() == ""


def test_printer_codes_are_encrypted(lib, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", KEY)
    pi.init()
    pi.save_printers([{"db_id": None, "kind": "bambu", "name": "P1S", "ip": "192.0.2.1", "serial": "S1",
                       "code": "zugang12"}])
    (stored,) = raw_codes()
    assert stored.startswith("enc:v1:") and "zugang12" not in stored
    (p,) = pi.db_printers()
    assert p["code"] == "zugang12" and p["code_error"] is None
    # Leeres Feld beim Bearbeiten: gespeicherter Wert bleibt, ohne doppelte Verschlüsselung
    pi.save_printers([{"db_id": p["db_id"], "kind": "bambu", "name": "P1S neu", "ip": "192.0.2.1", "serial": "S1",
                       "code": ""}])
    assert raw_codes() == [stored] and pi.db_printers()[0]["code"] == "zugang12"
    assert pi.stored_plaintext() == 0


def test_plaintext_is_migrated_and_wrong_key_is_reported(lib, monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    pi.init()
    pi.save_printers([{"db_id": None, "kind": "prusalink", "name": "MK4S", "ip": "192.0.2.2", "code": "pw-alt"}])
    assert raw_codes() == ["pw-alt"] and pi.stored_plaintext() == 1  # ohne Schlüssel: Klartext
    monkeypatch.setenv("SECRET_KEY", KEY)
    pi.init()  # beim Start: vorhandene Zugangsdaten verschlüsseln
    assert raw_codes()[0].startswith("enc:v1:") and pi.db_printers()[0]["code"] == "pw-alt"
    monkeypatch.setenv("SECRET_KEY", OTHER_KEY)
    (p,) = pi.db_printers()
    assert p["code"] == "" and "passt nicht" in p["code_error"]
    res = pi.sync_printer(p)
    assert res["auth_failed"] and "nicht entschlüsseln" in res["error"]


def test_env_printer_values_from_files(tmp_path):
    code = tmp_path / "code"
    code.write_text("dateicode\n")
    env = {"PRINTER_1_NAME": "P2S", "PRINTER_1_IP": "192.0.2.3", "PRINTER_1_SERIAL": "S3",
           "PRINTER_1_CODE_FILE": str(code)}
    (p,) = pi.env_printers(env)
    assert p["code"] == "dateicode"


def test_password_hash():
    hashed = auth.hash_password("richtig-lang")
    assert hashed.startswith("scrypt:15:8:1:") and "$" not in hashed  # „$“ würde Compose als Variable lesen
    assert auth.verify_password("richtig-lang", hashed) and not auth.verify_password("falsch", hashed)
    assert not auth.verify_password("x", "kaputt")


@pytest.fixture
def client(lib, monkeypatch):
    from app import main
    monkeypatch.setattr(auth, "_failures", {})
    monkeypatch.setattr(auth, "_basic_ok", {})
    with TestClient(main.app) as c:
        yield c


def test_login_with_hash_and_file(client, monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", KEY)
    monkeypatch.setenv("AUTH_USER", "andreas")
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    monkeypatch.setenv("AUTH_PASSWORD_HASH", auth.hash_password("geheim-123"))
    assert client.get("/", follow_redirects=False).status_code == 303
    r = client.post("/login", data={"user": "andreas", "password": "falsch"})
    assert r.status_code == 401
    r = client.post("/login", data={"user": "andreas", "password": "geheim-123"}, follow_redirects=False)
    assert r.status_code == 303 and auth.COOKIE in r.cookies
    assert client.get("/").status_code == 200
    # Anderer SECRET_KEY: bisherige Sitzungen gelten nicht mehr
    monkeypatch.setenv("SECRET_KEY", OTHER_KEY)
    assert client.get("/", follow_redirects=False).status_code == 303
    client.cookies.clear()
    header = {"Authorization": "Basic " + base64.b64encode(b"andreas:geheim-123").decode()}
    assert client.get("/api/stats", headers=header).status_code == 200
    assert client.get("/api/stats", headers=header).status_code == 200  # zweites Mal aus dem Zwischenspeicher
    # Passwort aus einer Datei (Docker-Secret)
    monkeypatch.delenv("AUTH_PASSWORD_HASH")
    secret = tmp_path / "pw"
    secret.write_text("aus-datei-99\n")
    monkeypatch.setenv("AUTH_PASSWORD_FILE", str(secret))
    assert auth.enabled() and auth.check("andreas", "aus-datei-99")
    assert client.get("/api/stats", headers=header).status_code == 401  # Zwischenspeicher gilt nicht mehr


def test_without_key_sessions_still_work(client, monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("AUTH_USER", "andreas")
    monkeypatch.setenv("AUTH_PASSWORD", "geheim-123")
    r = client.post("/login", data={"user": "andreas", "password": "geheim-123"}, follow_redirects=False)
    assert r.status_code == 303 and client.get("/").status_code == 200


def test_settings_warn_without_key(client, monkeypatch):
    from app import printer_import
    monkeypatch.setattr(printer_import, "sync_all", lambda: None)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert "settings-key" not in client.get("/settings").text  # ohne Drucker und Anmeldung kein Hinweis
    client.post("/settings/printers", data={"db_id": [""], "kind": ["bambu"], "name": ["P1S"], "ip": ["192.0.2.9"],
                                            "serial": ["S9"], "code": ["abc12345"]})
    page = client.get("/settings").text
    assert 'class="flash flash-warn settings-key"' in page and "python -m app.auth secret" in page
    monkeypatch.setenv("SECRET_KEY", KEY)
    printer_import.init()
    page = client.get("/settings").text
    assert "settings-key" not in page and "werden verschlüsselt gespeichert" in page
    monkeypatch.setenv("SECRET_KEY", "kurz")
    assert "kürzer als 32 Zeichen" in client.get("/settings").text


def test_legacy_key_file_is_removed(lib):
    legacy = store.DATA_DIR / "secret.key"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("alt")
    from app import main
    with TestClient(main.app):
        pass
    assert not legacy.exists()


def test_cli(monkeypatch, capsys):
    assert auth.main(["auth", "secret"]) == 0
    key = capsys.readouterr().out.strip().removeprefix("SECRET_KEY=")
    assert len(key) >= 48
    answers = iter(["neues-passwort", "neues-passwort"])
    monkeypatch.setattr(auth.getpass, "getpass", lambda prompt: next(answers))
    assert auth.main(["auth", "hash"]) == 0
    hashed = capsys.readouterr().out.strip().removeprefix("AUTH_PASSWORD_HASH=")
    assert auth.verify_password("neues-passwort", hashed)
    answers = iter(["kurz", "kurz"])
    assert auth.main(["auth", "hash"]) == 1
    assert auth.main(["auth"]) == 2
