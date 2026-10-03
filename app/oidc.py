# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Anmeldung über OpenID Connect, z. B. mit Authelia, Authentik, Keycloak oder Pocket ID.

Ablauf: Authorization Code mit PKCE. Der Browser geht zum Anbieter und kommt mit einem
Code zurück; PlateShelf tauscht ihn direkt beim Anbieter gegen ein ID-Token, prüft dessen
Signatur (Schlüssel aus JWKS), Aussteller, Empfänger, Ablauf und Nonce und holt Name und
Gruppen zusätzlich vom Userinfo-Endpunkt (Authelia liefert Gruppen nur dort).

Konten: Beim ersten Anmelden wird das Konto mit gleichem Benutzernamen verknüpft oder ein
neues angelegt (siehe auth.login_external). Rollen kommen aus OIDC_ADMIN_GROUP und
OIDC_USER_GROUP, wenn gesetzt, und werden dann bei jeder Anmeldung übernommen.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from . import auth, crypto

log = logging.getLogger("plateshelf")

STATE_COOKIE = "oidc_state"
STATE_SECONDS = 600       # so lange darf die Anmeldung beim Anbieter dauern
CACHE_SECONDS = 3600      # Discovery und Schlüssel des Anbieters
CLOCK_SKEW = 60
TIMEOUT = 10

_ALGS = {
    "RS256": ("rsa", hashes.SHA256), "RS384": ("rsa", hashes.SHA384), "RS512": ("rsa", hashes.SHA512),
    "PS256": ("pss", hashes.SHA256), "PS384": ("pss", hashes.SHA384), "PS512": ("pss", hashes.SHA512),
    "ES256": ("ec", hashes.SHA256), "ES384": ("ec", hashes.SHA384), "ES512": ("ec", hashes.SHA512),
}
_CURVES = {"P-256": ec.SECP256R1, "P-384": ec.SECP384R1, "P-521": ec.SECP521R1}

_pending: dict[str, dict] = {}   # state -> Nonce, PKCE-Verifier, Rücksprung, gültig bis
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


class OIDCError(Exception):
    """Anmeldung beim Anbieter fehlgeschlagen; Einzelheiten stehen im Log, nicht auf der Seite."""


def issuer() -> str:
    return os.environ.get("OIDC_ISSUER", "").strip().rstrip("/")


def enabled() -> bool:
    return auth.enabled() and bool(issuer() and os.environ.get("OIDC_CLIENT_ID", "").strip())


def _hostname(value: str) -> str:
    value = value.strip().lower()
    value = value.split("://", 1)[-1].split("/", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def available(host: str | None) -> bool:
    """OIDC an dieser Adresse anbieten? OIDC_HOSTS beschränkt es auf bestimmte Hostnamen,
    etwa wenn PlateShelf auch von außen erreichbar ist, der Anbieter aber nur im Heimnetz."""
    if not enabled():
        return False
    hosts = {_hostname(h) for h in os.environ.get("OIDC_HOSTS", "").split(",") if h.strip()}
    return not hosts or _hostname(host or "") in hosts


def provider_name() -> str:
    return os.environ.get("OIDC_NAME", "").strip() or "SSO"


def admin_group_set() -> bool:
    return bool(os.environ.get("OIDC_ADMIN_GROUP", "").strip())


# --- HTTP und Zwischenspeicher --------------------------------------------------

def _request(url: str, data: dict | None = None, headers: dict | None = None) -> dict:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise OIDCError(f"{url}: HTTP {exc.code} {detail}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OIDCError(f"{url}: {exc}") from exc


def _cached(key: str, load, force: bool = False) -> dict:
    with _lock:
        hit = _cache.get(key)
    if hit and not force and hit[0] > time.time():
        return hit[1]
    value = load()
    with _lock:
        _cache[key] = (time.time() + CACHE_SECONDS, value)
    return value


def discovery() -> dict:
    def load():
        doc = _request(issuer() + "/.well-known/openid-configuration")
        if doc.get("issuer", "").rstrip("/") != issuer():
            raise OIDCError(f"Discovery nennt einen anderen Aussteller: {doc.get('issuer')!r}")
        return doc
    return _cached("discovery", load)


def _keys(force: bool = False) -> list[dict]:
    return _cached("jwks", lambda: _request(discovery()["jwks_uri"]), force).get("keys", [])


# --- ID-Token prüfen -----------------------------------------------------------

def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _int(text: str) -> int:
    return int.from_bytes(_unb64(text), "big")


def _verify_signature(alg: str, jwk: dict, signed: bytes, signature: bytes) -> None:
    kind, digest = _ALGS[alg]
    if kind in ("rsa", "pss"):
        if jwk.get("kty") != "RSA":
            raise OIDCError("Schlüsseltyp passt nicht zum Algorithmus")
        key = rsa.RSAPublicNumbers(_int(jwk["e"]), _int(jwk["n"])).public_key()
        pad = (padding.PKCS1v15() if kind == "rsa"
               else padding.PSS(mgf=padding.MGF1(digest()), salt_length=padding.PSS.DIGEST_LENGTH))
        key.verify(signature, signed, pad, digest())
    else:
        if jwk.get("kty") != "EC" or jwk.get("crv") not in _CURVES:
            raise OIDCError("Schlüsseltyp passt nicht zum Algorithmus")
        key = ec.EllipticCurvePublicNumbers(_int(jwk["x"]), _int(jwk["y"]), _CURVES[jwk["crv"]]()).public_key()
        half = len(signature) // 2
        der = encode_dss_signature(int.from_bytes(signature[:half], "big"), int.from_bytes(signature[half:], "big"))
        key.verify(der, signed, ec.ECDSA(digest()))


def verify_id_token(token: str, nonce: str) -> dict:
    try:
        head, payload, sig = token.split(".")
        header = json.loads(_unb64(head))
        claims = json.loads(_unb64(payload))
        signature = _unb64(sig)
    except ValueError as exc:
        raise OIDCError("ID-Token ist kein gültiges JWT") from exc
    alg = header.get("alg")
    if alg not in _ALGS:  # „none“ und HS* (geteiltes Geheimnis) gibt es hier nicht
        raise OIDCError(f"Signaturverfahren {alg!r} wird nicht unterstützt")
    kid = header.get("kid")
    for force in (False, True):  # unbekannte Schlüssel-ID: Anbieter hat vielleicht neue Schlüssel
        candidates = [k for k in _keys(force) if k.get("use", "sig") == "sig" and (kid is None or k.get("kid") == kid)]
        if candidates:
            break
    if not candidates:
        raise OIDCError(f"Kein Schlüssel {kid!r} beim Anbieter")
    for jwk in candidates:
        try:
            _verify_signature(alg, jwk, f"{head}.{payload}".encode(), signature)
            break
        except (InvalidSignature, KeyError, ValueError):
            continue
    else:
        raise OIDCError("Signatur des ID-Tokens ist ungültig")

    client_id = os.environ.get("OIDC_CLIENT_ID", "").strip()
    audience = claims.get("aud")
    audience = [audience] if isinstance(audience, str) else audience or []
    now = time.time()
    if claims.get("iss", "").rstrip("/") != issuer():
        raise OIDCError("ID-Token stammt von einem anderen Aussteller")
    if client_id not in audience or (len(audience) > 1 and claims.get("azp", client_id) != client_id):
        raise OIDCError("ID-Token ist nicht für PlateShelf ausgestellt")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - CLOCK_SKEW:
        raise OIDCError("ID-Token ist abgelaufen")
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCError("Nonce im ID-Token stimmt nicht")
    if not claims.get("sub"):
        raise OIDCError("ID-Token ohne sub")
    return claims


# --- Ablauf ---------------------------------------------------------------------

def start(redirect_uri: str, target: str) -> tuple[str, str]:
    """Adresse beim Anbieter und state (für das Cookie, das die Rückkehr an diesen Browser bindet)."""
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    now = time.time()
    with _lock:
        for key in [k for k, v in _pending.items() if v["expires"] < now]:
            del _pending[key]
        _pending[state] = {"nonce": nonce, "verifier": verifier, "target": target,
                           "redirect_uri": redirect_uri, "expires": now + STATE_SECONDS}
    params = {
        "response_type": "code",
        "client_id": os.environ.get("OIDC_CLIENT_ID", "").strip(),
        "redirect_uri": redirect_uri,
        "scope": os.environ.get("OIDC_SCOPES", "").strip() or "openid profile email groups",
        "state": state,
        "nonce": nonce,
        "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    }
    endpoint = discovery()["authorization_endpoint"]
    return endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode(params), state


def _token_request(pending: dict, code: str) -> dict:
    doc = discovery()
    client_id = os.environ.get("OIDC_CLIENT_ID", "").strip()
    secret = crypto.env_value("OIDC_CLIENT_SECRET")
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": pending["redirect_uri"],
            "code_verifier": pending["verifier"]}
    headers = {}
    methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    if not secret:
        data["client_id"] = client_id  # öffentlicher Client, nur PKCE
    elif "client_secret_basic" in methods:
        pair = f"{urllib.parse.quote(client_id, safe='')}:{urllib.parse.quote(secret, safe='')}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
    else:
        data.update(client_id=client_id, client_secret=secret)
    return _request(doc["token_endpoint"], data, headers)


def finish(state: str, cookie_state: str | None, code: str) -> tuple[dict, str]:
    """Code einlösen; liefert die Angaben zur Person und das Ziel nach der Anmeldung."""
    with _lock:
        pending = _pending.pop(state, None)
    if not pending or not cookie_state or not hmac.compare_digest(state, cookie_state):
        raise OIDCError("Unbekannter oder fremder state (abgelaufen, neu gestartet oder anderer Browser)")
    if pending["expires"] < time.time():
        raise OIDCError("Anmeldung hat zu lange gedauert")
    tokens = _token_request(pending, code)
    if "id_token" not in tokens:
        raise OIDCError("Antwort ohne id_token (Scope „openid“ fehlt?)")
    claims = verify_id_token(tokens["id_token"], pending["nonce"])
    endpoint = discovery().get("userinfo_endpoint")
    if endpoint and tokens.get("access_token"):
        info = _request(endpoint, headers={"Authorization": f"Bearer {tokens['access_token']}"})
        if info.get("sub") == claims["sub"]:
            claims = {**info, **claims}
    return claims, pending["target"]


def role_for(claims: dict) -> str | None:
    """Rolle aus den Gruppen; None, wenn keine Gruppen-Zuordnung eingestellt ist."""
    admin = os.environ.get("OIDC_ADMIN_GROUP", "").strip()
    user = os.environ.get("OIDC_USER_GROUP", "").strip()
    if not admin and not user:
        return None
    groups = claims.get(os.environ.get("OIDC_GROUPS_CLAIM", "").strip() or "groups") or []
    groups = [groups] if isinstance(groups, str) else groups
    if admin and admin in groups:
        return "admin"
    if user and user in groups:
        return "user"
    return "viewer"


def sign_in(claims: dict) -> int:
    name = (claims.get("preferred_username") or claims.get("name") or claims.get("email") or claims["sub"])
    return auth.login_external(f"{issuer()} {claims['sub']}", str(name), role_for(claims))
