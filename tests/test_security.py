"""Security headers, event log, passkeys (with an emulated device) and PASSWORD_LOGIN=off."""
import base64
import hashlib
import json
import secrets
import struct

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app import auth, passkeys, store

ORIGIN = "http://testserver"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class Authenticator:
    """A passkey: generates keys and signs like a real device."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = secrets.token_bytes(16)
        self.count = 0

    def _client(self, kind, challenge, origin):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin}).encode()

    def create(self, options, origin=ORIGIN):
        nums = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})
        data = (hashlib.sha256(options["rp"]["id"].encode()).digest() + bytes([0x45]) + struct.pack(">I", 0)
                + bytes(16) + struct.pack(">H", len(self.cred_id)) + self.cred_id + cose)
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": data})
        return {"id": b64(self.cred_id), "rawId": b64(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64(self._client("webauthn.create", options["challenge"], origin)),
                             "attestationObject": b64(att)}, "clientExtensionResults": {}}

    def get(self, options, origin=ORIGIN, verified=True):
        self.count += 1
        client = self._client("webauthn.get", options["challenge"], origin)
        data = hashlib.sha256(options["rpId"].encode()).digest() + bytes([0x05 if verified else 0x01]) \
            + struct.pack(">I", self.count)
        sig = self.key.sign(data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64(self.cred_id), "rawId": b64(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64(client), "authenticatorData": b64(data), "signature": b64(sig)},
                "clientExtensionResults": {}}


@pytest.fixture
def client(lib, monkeypatch):
    from app import main
    monkeypatch.setenv("AUTH", "on")
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    for name in ("AUTH_USER", "AUTH_PASSWORD", "AUTH_PASSWORD_HASH", "PASSWORD_LOGIN", "OIDC_ISSUER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(auth, "_failures", {})
    monkeypatch.setattr(auth, "_basic_ok", {})
    monkeypatch.setattr(passkeys, "_challenges", {})
    with TestClient(main.app) as c:
        auth.create_user("andreas", "andreas-pass-123", "admin")
        yield c


def login(c, name="andreas", password="andreas-pass-123"):
    return c.post("/login", data={"user": name, "password": password}, follow_redirects=False)


def add_passkey(c, name="Handy"):
    key = Authenticator()
    opts = c.post("/account/passkeys/options").json()
    assert opts["options"]["rp"]["id"] == "testserver"
    r = c.post("/account/passkeys", json={"token": opts["token"], "name": name,
                                          "credential": key.create(opts["options"])})
    return key, r


def passkey_login(c, key, **kwargs):
    opts = c.post("/login/passkey/options").json()
    assert opts["options"]["userVerification"] == "required"
    return c.post("/login/passkey", json={"token": opts["token"], "next": "/history",
                                          "credential": key.get(opts["options"], **kwargs)})


def test_security_headers_everywhere(client):
    for path in ("/login", "/static/style.css", "/healthz"):
        r = client.get(path)
        assert "default-src 'self'" in r.headers["content-security-policy"], path
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "same-origin"
        assert r.headers["cross-origin-resource-policy"] == "same-origin"
        assert r.headers["cross-origin-embedder-policy"] == "require-corp"
    assert "'unsafe-inline'" not in client.get("/login").headers["content-security-policy"].split("style-src")[0]
    # Pages and API answers are never taken from a cache (iOS home-screen app), static files are
    assert client.get("/login").headers["cache-control"] == "no-cache"
    assert client.get("/healthz").headers["cache-control"] == "no-cache"
    assert "no-cache" not in client.get("/static/style.css").headers.get("cache-control", "")


def test_passkey_register_login_and_remove(client):
    assert "data-passkey-login" not in client.get("/login").text      # no passkeys yet
    login(client)
    key, r = add_passkey(client)
    assert r.status_code == 200 and r.json()["redirect"].startswith("/account")
    assert "Handy" in client.get("/account").text
    client.post("/logout")
    assert "data-passkey-login" in client.get("/login").text

    r = passkey_login(client, key)
    assert r.status_code == 200 and r.json()["redirect"] == "/history"
    assert client.get("/account").status_code == 200                  # session established
    with store.connect() as con:
        assert con.execute("SELECT last_used FROM passkeys").fetchone()[0]

    # Foreign origin, no user verification, unknown device, reused challenge: rejected
    client.post("/logout")
    assert passkey_login(client, key, origin="https://evil.test").status_code == 401
    assert passkey_login(client, key, verified=False).status_code == 401
    assert passkey_login(client, Authenticator()).json()["error"] == "Dieser Passkey ist hier nicht bekannt."
    opts = client.post("/login/passkey/options").json()
    body = {"token": opts["token"], "credential": key.get(opts["options"])}
    assert client.post("/login/passkey", json=body).status_code == 200
    client.post("/logout")
    assert client.post("/login/passkey", json=body).status_code == 401  # each challenge is valid only once

    login(client)
    pid = passkeys.for_user(1)[0]["id"]
    client.post(f"/account/passkeys/{pid}/delete")
    assert passkeys.for_user(1) == []
    client.post("/logout")
    assert passkey_login(client, key).status_code == 401


def test_event_log(client):
    assert login(client, password="falsch").status_code == 401
    login(client)
    client.post("/settings/users", data={"name": "bea", "role": "user", "password": "bea-pass-123",
                                         "repeat": "bea-pass-123"})
    bea = next(u["id"] for u in auth.list_users() if u["name"] == "bea")
    client.post(f"/settings/users/{bea}", data={"role": "viewer"})
    client.post("/settings/tokens", data={"name": "Homepage"})
    add_passkey(client, "Laptop")
    kinds = [e["kind"] for e in auth.events()]
    assert kinds[::-1] == ["login_failed", "login", "user_created", "role_changed", "token_created", "passkey_added"]
    page = client.get("/settings").text
    assert 'id="log"' in page and "Anmeldung fehlgeschlagen" in page and "bea: user → viewer" in page
    assert 'class="is-warn"' in page


def test_password_login_off_needs_another_way_in(client, monkeypatch):
    monkeypatch.setenv("PASSWORD_LOGIN", "off")
    # No OIDC and no passkey: password stays allowed, otherwise nobody could get in anymore
    assert auth.password_login() and login(client).status_code == 303
    add_passkey(client)
    assert not auth.password_login()
    client.post("/logout")
    page = client.get("/login").text
    assert 'name="password"' not in page and "data-passkey-login" in page
    assert login(client).status_code == 403
    basic = base64.b64encode(b"andreas:andreas-pass-123").decode()
    assert client.get("/api/stats", headers={"Authorization": f"Basic {basic}"}).status_code == 401
