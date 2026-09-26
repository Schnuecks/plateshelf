# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Optionale Anmeldung.

Eingeschaltet, wenn AUTH_USER und AUTH_PASSWORD gesetzt sind. Browser melden sich
über /login an und bekommen ein signiertes Sitzungs-Cookie; Skripte und Dashboards
(z. B. das Homepage-Widget) schicken dieselben Zugangsdaten per HTTP Basic Auth.

Die Sitzung ist mit einem geheimen Schlüssel signiert (SECRET_KEY, sonst einmalig
erzeugt und im Datenordner gespeichert) und enthält einen Fingerabdruck des
Passworts: Ändert sich das Passwort, sind alle alten Sitzungen ungültig.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time

from . import store

log = logging.getLogger("plateshelf")

COOKIE = "sitzung"
SESSION_DAYS = max(1, int(os.environ.get("SESSION_DAYS", "30")))
MAX_FAILURES = 5            # Fehlversuche je Adresse …
FAILURE_WINDOW = 15 * 60    # … innerhalb dieser Zeit (s) …
LOCKOUT = 5 * 60            # … sperren sie für diese Zeit (s)

_secret: bytes | None = None
_secret_lock = threading.Lock()
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def credentials() -> tuple[str, str]:
    return os.environ.get("AUTH_USER", "").strip(), os.environ.get("AUTH_PASSWORD", "")


def enabled() -> bool:
    user, password = credentials()
    return bool(user and password)


def _secret_key() -> bytes:
    global _secret
    with _secret_lock:
        if _secret is None:
            env = os.environ.get("SECRET_KEY", "")
            if env:
                _secret = env.encode()
            else:
                path = store.DATA_DIR / "secret.key"
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(secrets.token_hex(32))
                    try:
                        os.chmod(path, 0o600)
                    except OSError:
                        pass
                _secret = path.read_text().strip().encode()
        return _secret


def _sign(user: str, expires: int) -> str:
    fingerprint = hashlib.sha256(credentials()[1].encode()).hexdigest()[:16]
    msg = f"{user}|{expires}|{fingerprint}".encode()
    return hmac.new(_secret_key(), msg, hashlib.sha256).hexdigest()


def check(user: str, password: str) -> bool:
    good_user, good_password = credentials()
    # Beide Vergleiche immer ausführen, damit die Laufzeit nichts verrät
    ok_user = hmac.compare_digest(user.encode(), good_user.encode())
    ok_password = hmac.compare_digest(password.encode(), good_password.encode())
    return bool(good_user and good_password) and ok_user and ok_password


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
    try:
        user, _, password = base64.b64decode(header[6:].strip()).decode().partition(":")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return False
    return check(user, password)


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
