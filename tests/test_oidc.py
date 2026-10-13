"""Login via OpenID Connect against a mock provider (see app/oidc.py)."""
import base64
import hashlib
import json
import time
import urllib.parse

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi.testclient import TestClient

from app import auth, oidc, store

ISSUER = "https://auth.example.test"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def num(n: int) -> str:
    return b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))


class Provider:
    """Discovery, JWKS, token and userinfo endpoints as in Authelia."""

    def __init__(self):
        self.rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.ec = ec.generate_private_key(ec.SECP256R1())
        self.codes = {}       # code -> (claims, nonce, PKCE challenge)
        self.requests = []
        self.alg = "RS256"
        self.tamper = {}      # claims smuggled into the ID token

    def jwks(self):
        r, e = self.rsa.public_key().public_numbers(), self.ec.public_key().public_numbers()
        return {"keys": [{"kty": "RSA", "kid": "r1", "use": "sig", "n": num(r.n), "e": num(r.e)},
                         {"kty": "EC", "kid": "e1", "use": "sig", "crv": "P-256", "x": num(e.x), "y": num(e.y)}]}

    def sign(self, claims, alg=None):
        alg = alg or self.alg
        header = {"alg": alg, "kid": "e1" if alg.startswith("ES") else "r1", "typ": "JWT"}
        signed = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(claims).encode())}"
        if alg == "RS256":
            sig = self.rsa.sign(signed.encode(), padding.PKCS1v15(), hashes.SHA256())
        elif alg == "ES256":
            r, s = decode_dss_signature(self.ec.sign(signed.encode(), ec.ECDSA(hashes.SHA256())))
            sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        else:
            sig = b""
        return f"{signed}.{b64(sig)}"

    def authorize(self, url, sub, name, groups=()):
        """What the provider does after login: issue a code and redirect back."""
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
        code = f"code-{len(self.codes)}"
        self.codes[code] = ({"sub": sub, "preferred_username": name, "groups": list(groups)},
                            q["nonce"], q["code_challenge"])
        return f"{q['redirect_uri']}?code={code}&state={q['state']}"

    def __call__(self, url, data=None, headers=None):
        self.requests.append((url, data, headers))
        if url == ISSUER + "/.well-known/openid-configuration":
            return {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                    "token_endpoint": ISSUER + "/token", "userinfo_endpoint": ISSUER + "/userinfo",
                    "jwks_uri": ISSUER + "/jwks", "token_endpoint_auth_methods_supported": ["client_secret_basic"]}
        if url == ISSUER + "/jwks":
            return self.jwks()
        if url == ISSUER + "/token":
            info, nonce, challenge = self.codes.pop(data["code"])
            assert b64(hashlib.sha256(data["code_verifier"].encode()).digest()) == challenge
            assert headers["Authorization"] == "Basic " + base64.b64encode(b"plateshelf:geheim").decode()
            now = int(time.time())
            claims = {"iss": ISSUER, "aud": "plateshelf", "sub": info["sub"], "iat": now, "exp": now + 300,
                      "nonce": nonce, **self.tamper}
            return {"id_token": self.sign(claims), "access_token": "at-" + info["sub"], "userinfo": info}
        if url == ISSUER + "/userinfo":
            assert headers["Authorization"] == "Bearer at-" + self.last_info["sub"]
            return self.last_info
        raise AssertionError(url)


@pytest.fixture
def provider(monkeypatch):
    p = Provider()

    def fake(url, data=None, headers=None):
        result = p(url, data, headers)
        if url.endswith("/token"):
            p.last_info = result.pop("userinfo")
        return result
    monkeypatch.setattr(oidc, "_request", fake)
    monkeypatch.setattr(oidc, "_cache", {})
    monkeypatch.setattr(oidc, "_pending", {})
    for name, value in (("OIDC_ISSUER", ISSUER + "/"), ("OIDC_CLIENT_ID", "plateshelf"),
                        ("OIDC_CLIENT_SECRET", "geheim"), ("OIDC_NAME", "Authelia")):
        monkeypatch.setenv(name, value)
    for name in ("OIDC_ADMIN_GROUP", "OIDC_USER_GROUP", "OIDC_GROUPS_CLAIM", "OIDC_SCOPES"):
        monkeypatch.delenv(name, raising=False)
    return p


@pytest.fixture
def client(lib, monkeypatch, provider):
    from app import main
    monkeypatch.setenv("AUTH", "on")
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    for name in ("AUTH_USER", "AUTH_PASSWORD", "AUTH_PASSWORD_HASH", "SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(auth, "_failures", {})
    with TestClient(main.app) as c:
        yield c


def sso(c, provider, sub, name, groups=(), next="/"):
    """Full login in the browser: PlateShelf → provider → back."""
    r = c.get(f"/login/oidc?next={next}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(ISSUER + "/authorize?"), r.text
    back = provider.authorize(r.headers["location"], sub, name, groups)
    return c.get(back.removeprefix("http://testserver"), follow_redirects=False)


def user_row(name):
    with store.connect() as con:
        return con.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()


def test_sign_in_creates_read_only_account(client, provider):
    auth.create_user("andreas", "andreas-pass-123", "admin")
    page = client.get("/login").text
    assert "Anmelden mit Authelia" in page and "/login/oidc?next=" in page

    r = sso(client, provider, "u-42", "bea", next="/history")
    assert r.status_code == 303 and r.headers["location"] == "/history"
    assert client.get("/account").status_code == 200
    row = user_row("bea")
    assert row["role"] == "viewer" and row["oidc_sub"] == f"{ISSUER} u-42"
    # No password: neither login form nor Basic Auth, and only the notice in the account page
    assert auth.authenticate("bea", "-") is None and not auth.has_password(row["id"])
    assert "dein Passwort änderst du dort" in client.get("/account").text
    # State is valid only once
    assert not oidc._pending


def test_existing_account_is_linked_and_survives_rename(client, provider):
    auth.create_user("andreas", "andreas-pass-123", "admin")
    assert sso(client, provider, "u-1", "andreas").status_code == 303
    assert user_row("andreas")["oidc_sub"] == f"{ISSUER} u-1"
    assert user_row("andreas")["role"] == "admin"   # without a group mapping the role is kept
    client.cookies.clear()
    assert sso(client, provider, "u-1", "andreas.neu").status_code == 303
    assert user_row("andreas.neu") is None          # same person, same account
    # A different account at the provider with the same name does not take over the linked account
    client.cookies.clear()
    r = sso(client, provider, "u-2", "andreas")
    assert r.status_code == 403 and "gibt es schon" in r.text


def test_roles_from_groups(client, provider, monkeypatch):
    monkeypatch.setenv("OIDC_ADMIN_GROUP", "admins")
    monkeypatch.setenv("OIDC_USER_GROUP", "family")
    auth.create_user("andreas", "andreas-pass-123", "admin")
    sso(client, provider, "u-7", "bea", ["family"])
    assert user_row("bea")["role"] == "user"
    client.cookies.clear()
    sso(client, provider, "u-7", "bea", ["admins", "family"])
    assert user_row("bea")["role"] == "admin"
    client.cookies.clear()
    sso(client, provider, "u-7", "bea", [])
    assert user_row("bea")["role"] == "viewer"
    # The last admin does not lose the role due to a missing group
    client.cookies.clear()
    sso(client, provider, "u-1", "andreas", [])
    assert user_row("andreas")["role"] == "admin"


def test_first_account_needs_setup_or_admin_group(client, provider, monkeypatch):
    assert "Anmelden mit Authelia" not in client.get("/setup").text
    r = sso(client, provider, "u-1", "andreas")
    assert r.status_code == 403 and "Lege zuerst das Admin-Konto an" in r.text
    assert not auth.has_users()

    monkeypatch.setenv("OIDC_ADMIN_GROUP", "admins")
    assert "Anmelden mit Authelia" in client.get("/setup").text
    assert sso(client, provider, "u-1", "andreas", ["admins"]).status_code == 303
    assert user_row("andreas")["role"] == "admin"
    with store.connect() as con:
        assert con.execute("SELECT 1 FROM settings WHERE key = ?", (auth.SETUP_KEY,)).fetchone() is None


def test_callback_needs_the_same_browser(client, provider):
    auth.create_user("andreas", "andreas-pass-123", "admin")
    r = client.get("/login/oidc", follow_redirects=False)
    back = provider.authorize(r.headers["location"], "u-9", "mallory")
    client.cookies.clear()   # e.g. a planted link in a different browser
    r = client.get(back.removeprefix("http://testserver"), follow_redirects=False)
    assert r.status_code == 401 and "fehlgeschlagen" in r.text
    assert user_row("mallory") is None
    # Errors from the provider (e.g. denied) also end up gracefully on the login page
    r = client.get("/login/oidc/callback?error=access_denied&state=x", follow_redirects=False)
    assert r.status_code == 401


@pytest.mark.parametrize("tamper", [{"aud": "anderer-dienst"}, {"iss": "https://evil.test"},
                                    {"exp": 1000}, {"nonce": "falsch"}])
def test_forged_or_foreign_tokens_are_rejected(client, provider, tamper):
    auth.create_user("andreas", "andreas-pass-123", "admin")
    provider.tamper = tamper
    r = sso(client, provider, "u-3", "eve")
    assert r.status_code == 401 and user_row("eve") is None


def test_signature_checks(provider):
    claims = {"iss": ISSUER, "aud": ["plateshelf"], "sub": "u-1", "exp": time.time() + 60, "nonce": "n"}
    assert oidc.verify_id_token(provider.sign(claims, "ES256"), "n")["sub"] == "u-1"
    for bad in (provider.sign(claims, "none"), provider.sign(claims)[:-6] + "AAAAAA", "kein.jwt"):
        with pytest.raises(oidc.OIDCError):
            oidc.verify_id_token(bad, "n")


def test_without_configuration_nothing_changes(client, monkeypatch):
    monkeypatch.delenv("OIDC_ISSUER")
    auth.create_user("andreas", "andreas-pass-123", "admin")
    assert "/login/oidc" not in client.get("/login").text
    assert client.get("/login/oidc", follow_redirects=False).headers["location"] == "/login"


def test_button_only_on_listed_hosts(client, provider, monkeypatch):
    """Provider reachable only on the home network: from outside (tunnel) only password login."""
    auth.create_user("andreas", "andreas-pass-123", "admin")
    monkeypatch.setenv("OIDC_HOSTS", "https://plateshelf.home.lan:443, other.lan")
    outside = {"host": "bambu.example.net"}
    assert "/login/oidc" not in client.get("/login", headers=outside).text
    assert client.get("/login/oidc", headers=outside, follow_redirects=False).headers["location"] == "/login"
    inside = {"host": "plateshelf.home.lan"}
    assert "Anmelden mit Authelia" in client.get("/login", headers=inside).text
    r = client.get("/login/oidc", headers=inside, follow_redirects=False)
    assert r.headers["location"].startswith(ISSUER + "/authorize?")
    assert "redirect_uri=http%3A%2F%2Fplateshelf.home.lan%2Flogin%2Foidc%2Fcallback" in r.headers["location"]
