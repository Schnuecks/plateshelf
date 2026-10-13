# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Secrets: SECRET_KEY, encrypted printer credentials and values from files.

SECRET_KEY only comes from the environment (SECRET_KEY or SECRET_KEY_FILE), never from
the data folder: that way a copy of the database alone is useless to anyone.

Access codes, passwords and API keys of the printers are stored AES-256-GCM
encrypted in the database ("enc:v1:" + Base64 of nonce and ciphertext). The
key for this is derived via HKDF from SECRET_KEY, separate from the key
for the sign-in sessions.
"""
from __future__ import annotations

import base64
import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

log = logging.getLogger("plateshelf")

PREFIX = "enc:v1:"
MIN_KEY_LENGTH = 32
_AAD = b"plateshelf printer secret"


class SecretError(Exception):
    """A stored secret cannot be decrypted."""


def env_value(name: str, environ=None) -> str:
    """Value of NAME, otherwise content of the file from NAME_FILE (for Docker secrets)."""
    environ = os.environ if environ is None else environ
    value = environ.get(name, "")
    if value:
        return value.strip()
    path = environ.get(f"{name}_FILE", "").strip()
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        log.warning("%s_FILE nicht lesbar: %s", name, exc)
        return ""


def secret_key() -> str:
    return env_value("SECRET_KEY")


@lru_cache(maxsize=8)
def _derive(secret: str, purpose: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose.encode()).derive(secret.encode())


def derive(purpose: str) -> bytes | None:
    """Separate key per purpose from SECRET_KEY; None if SECRET_KEY is missing."""
    secret = secret_key()
    return _derive(secret, purpose) if secret else None


def is_encrypted(value: str | None) -> bool:
    return bool(value) and value.startswith(PREFIX)


def encrypt(text: str) -> str:
    """Encrypts if SECRET_KEY is set; otherwise the text stays unchanged."""
    if not text or is_encrypted(text):
        return text
    key = derive("plateshelf printer secrets v1")
    if key is None:
        return text
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(key).encrypt(nonce, text.encode(), _AAD)
    return PREFIX + base64.urlsafe_b64encode(nonce + sealed).decode()


def decrypt(value: str) -> str:
    if not is_encrypted(value):
        return value  # old plaintext or empty
    key = derive("plateshelf printer secrets v1")
    if key is None:
        raise SecretError("SECRET_KEY fehlt")
    try:
        raw = base64.urlsafe_b64decode(value[len(PREFIX):])
        return AESGCM(key).decrypt(raw[:12], raw[12:], _AAD).decode()
    except (InvalidTag, ValueError) as exc:
        raise SecretError("SECRET_KEY passt nicht") from exc


def status() -> dict:
    """For settings and diagnostics: is a usable SECRET_KEY set?"""
    key = secret_key()
    return {"set": bool(key), "short": bool(key) and len(key) < MIN_KEY_LENGTH}


def remove_legacy_key_file(data_dir: Path) -> None:
    """Up to 0.7.7, SECRET_KEY was stored as a file in the data folder when not set."""
    path = data_dir / "secret.key"
    if path.exists():
        try:
            path.unlink()
            log.info("secret.key im Datenordner entfernt; SECRET_KEY kommt jetzt nur noch aus der Umgebung")
        except OSError as exc:
            log.warning("secret.key konnte nicht entfernt werden: %s", exc)
