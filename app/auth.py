# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Optionale Anmeldung.

Eingeschaltet, wenn AUTH_USER und ein Passwort gesetzt sind: am besten als Hash
(AUTH_PASSWORD_HASH, erzeugt mit „python -m app.auth hash“), sonst im Klartext
(AUTH_PASSWORD). Alle Werte gehen auch als Datei über NAME_FILE (Docker-Secrets).
Browser melden sich über /login an und bekommen ein signiertes Sitzungs-Cookie;
Skripte und Dashboards (z. B. das Homepage-Widget) schicken dieselben Zugangsdaten
per HTTP Basic Auth.

Die Sitzung ist mit einem aus SECRET_KEY abgeleiteten Schlüssel signiert und
enthält einen Fingerabdruck des Passworts: Ändert sich das Passwort, sind alle
alten Sitzungen ungültig. Ohne SECRET_KEY gilt ein Schlüssel nur bis zum nächsten
Neustart; man muss sich danach neu anmelden.
"""
from __future__ import annotations

import base64
import binascii
import getpass
import hashlib
import hmac
import logging
import os
import secrets
import sys
import threading
import time

from . import crypto

log = logging.getLogger("plateshelf")

COOKIE = "sitzung"
SESSION_DAYS = max(1, int(os.environ.get("SESSION_DAYS", "30")))
MAX_FAILURES = 5            # Fehlversuche je Adresse …
FAILURE_WINDOW = 15 * 60    # … innerhalb dieser Zeit (s) …
LOCKOUT = 5 * 60            # … sperren sie für diese Zeit (s)
SCRYPT_N_LOG2, SCRYPT_R, SCRYPT_P = 15, 8, 1
BASIC_CACHE_SECONDS = 300   # geprüfte Basic-Auth-Kopfzeilen, damit Dashboards nicht jedes Mal scrypt rechnen

_ephemeral = secrets.token_bytes(32)
_warned_ephemeral = False
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()
_basic_ok: dict[str, float] = {}


def credentials() -> tuple[str, str, str]:
    """(Benutzer, Klartext-Passwort, Passwort-Hash); leer, was nicht gesetzt ist."""
    return (crypto.env_value("AUTH_USER"), crypto.env_value("AUTH_PASSWORD"),
            crypto.env_value("AUTH_PASSWORD_HASH"))


def enabled() -> bool:
    user, password, hashed = credentials()
    return bool(user and (hashed or password))


# --- Passwort-Hash (scrypt, in Python enthalten) ------------------------------
# Format ohne „$“, weil Docker Compose „$“ in der .env als Variable liest:
#   scrypt:<log2 N>:<r>:<p>:<Salz>:<Hash>   (Salz und Hash als Base64 für URLs)

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt(password: str, salt: bytes, n_log2: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** n_log2, r=r, p=p, maxmem=256 * 1024 * 1024, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_N_LOG2, SCRYPT_R, SCRYPT_P)
    return f"scrypt:{SCRYPT_N_LOG2}:{SCRYPT_R}:{SCRYPT_P}:{_b64(salt)}:{_b64(digest)}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        kind, n_log2, r, p, salt, digest = hashed.split(":")
        if kind != "scrypt":
            return False
        return hmac.compare_digest(_scrypt(password, _unb64(salt), int(n_log2), int(r), int(p)), _unb64(digest))
    except (ValueError, binascii.Error):
        log.warning("AUTH_PASSWORD_HASH hat kein gültiges Format; neu erzeugen mit „python -m app.auth hash“")
        return False


# --- Sitzungen ----------------------------------------------------------------

def _secret_key() -> bytes:
    global _warned_ephemeral
    key = crypto.derive("plateshelf session v1")
    if key:
        return key
    if not _warned_ephemeral:
        _warned_ephemeral = True
        log.warning("SECRET_KEY fehlt: Anmeldungen gelten nur bis zum nächsten Neustart")
    return _ephemeral


def _sign(user: str, expires: int) -> str:
    _user, password, hashed = credentials()
    fingerprint = hashlib.sha256((hashed or password).encode()).hexdigest()[:16]
    msg = f"{user}|{expires}|{fingerprint}".encode()
    return hmac.new(_secret_key(), msg, hashlib.sha256).hexdigest()


def check(user: str, password: str) -> bool:
    good_user, good_password, hashed = credentials()
    # Beide Vergleiche immer ausführen, damit die Laufzeit nichts verrät
    ok_user = hmac.compare_digest(user.encode(), good_user.encode())
    if hashed:
        ok_password = verify_password(password, hashed)
    else:
        ok_password = hmac.compare_digest(password.encode(), good_password.encode()) and bool(good_password)
    return bool(good_user) and ok_user and ok_password


def new_session(user: str) -> str:
    expires = int(time.time()) + SESSION_DAYS * 86400
    raw = f"{user}|{expires}|{_sign(user, expires)}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def session_valid(token: str | None) -> bool:
    if not token:
        return False
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        user, expires_s, sig = raw.rsplit("|", 2)
        expires = int(expires_s)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return False
    if expires < time.time() or user != credentials()[0]:
        return False
    return hmac.compare_digest(sig, _sign(user, expires))


def basic_auth_valid(header: str | None) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    user_now, password_now, hashed_now = credentials()
    # Zwischenspeicher nur für erfolgreiche Prüfungen, gebunden an die aktuellen Zugangsdaten
    cache_key = hashlib.sha256(f"{header}|{user_now}|{hashed_now or password_now}".encode()).hexdigest()
    now = time.time()
    if _basic_ok.get(cache_key, 0) > now:
        return True
    try:
        user, _, password = base64.b64decode(header[6:].strip()).decode().partition(":")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return False
    if not check(user, password):
        return False
    if len(_basic_ok) > 100:
        _basic_ok.clear()
    _basic_ok[cache_key] = now + BASIC_CACHE_SECONDS
    return True


# --- Schutz gegen Durchprobieren ---------------------------------------------

def locked_out(address: str) -> int:
    """Sekunden, die diese Adresse noch gesperrt ist (0 = nicht gesperrt)."""
    now = time.time()
    with _failures_lock:
        times = [t for t in _failures.get(address, []) if now - t < FAILURE_WINDOW]
        _failures[address] = times
        if len(times) >= MAX_FAILURES:
            return max(0, int(times[-1] + LOCKOUT - now))
    return 0


def record_failure(address: str) -> None:
    with _failures_lock:
        _failures.setdefault(address, []).append(time.time())
    log.warning("Anmeldung fehlgeschlagen von %s", address)


def record_success(address: str) -> None:
    with _failures_lock:
        _failures.pop(address, None)


# --- Befehle für die Einrichtung ------------------------------------------------

def main(argv: list[str]) -> int:
    """python -m app.auth hash | secret (Ausgaben englisch, wie das README)"""
    command = argv[1] if len(argv) > 1 else ""
    if command == "hash":
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat: "):
            print("The passwords do not match.", file=sys.stderr)
            return 1
        if len(password) < 8:
            print("Please use at least 8 characters.", file=sys.stderr)
            return 1
        print(f"AUTH_PASSWORD_HASH={hash_password(password)}")
        return 0
    if command == "secret":
        print(f"SECRET_KEY={secrets.token_urlsafe(48)}")
        return 0
    print("Usage: python -m app.auth hash   (password hash for AUTH_PASSWORD_HASH)\n"
          "       python -m app.auth secret (random SECRET_KEY)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
