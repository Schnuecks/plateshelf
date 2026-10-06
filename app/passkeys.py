# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Passkeys und Sicherheitsschlüssel (WebAuthn) zum Anmelden ohne Passwort.

Ablauf in beide Richtungen: Der Server erzeugt eine Herausforderung (Optionen als JSON),
der Browser lässt sie vom Passkey unterschreiben (static/passkeys.js), der Server prüft die
Antwort. Die eigentliche Prüfung übernimmt die Bibliothek webauthn. Ein Passkey gilt für
genau die Adresse (Hostname), unter der er angelegt wurde, und braucht HTTPS (oder localhost).
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from typing import Any

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from . import store

RP_NAME = "PlateShelf"
CHALLENGE_SECONDS = 300
MAX_PER_USER = 20

# Meldungen (deutscher Text = Übersetzungsschlüssel)
FAILED = "Der Passkey konnte nicht geprüft werden. Bitte erneut versuchen."
UNKNOWN = "Dieser Passkey ist hier nicht bekannt."
EXPIRED = "Die Anfrage ist abgelaufen. Bitte erneut versuchen."
TOO_MANY = "Du hast schon die höchste Zahl an Passkeys."
MESSAGES = (FAILED, UNKNOWN, EXPIRED, TOO_MANY)

_challenges: dict[str, dict] = {}   # token -> Herausforderung, Zweck, Benutzer, gültig bis
_lock = threading.Lock()


class PasskeyError(Exception):
    """Fehler, die angezeigt werden (Meldung aus MESSAGES)."""


def relying_party(url) -> tuple[str, str]:
    """(RP-ID, Origin) aus der angefragten Adresse; hinter einem Proxy die weitergereichte."""
    port = f":{url.port}" if url.port else ""
    return url.hostname or "", f"{url.scheme}://{url.hostname}{port}"


def any_registered() -> bool:
    with store.connect() as con:
        return con.execute("SELECT 1 FROM passkeys LIMIT 1").fetchone() is not None


def for_user(user_id: int) -> list[dict]:
    with store.connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, name, created, last_used FROM passkeys WHERE user_id = ? ORDER BY created", (user_id,))]


def delete(user_id: int, passkey_id: int) -> str | None:
    """Entfernt einen eigenen Passkey; liefert seinen Namen (None, wenn es ihn nicht gibt)."""
    with store.connect() as con:
        row = con.execute("SELECT name FROM passkeys WHERE id = ? AND user_id = ?", (passkey_id, user_id)).fetchone()
        if row:
            con.execute("DELETE FROM passkeys WHERE id = ?", (passkey_id,))
        return row["name"] if row else None


def _descriptors(con, user_id: int) -> list[PublicKeyCredentialDescriptor]:
    return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["credential_id"]))
            for r in con.execute("SELECT credential_id FROM passkeys WHERE user_id = ?", (user_id,))]


def _store_challenge(challenge: bytes, purpose: str, user_id: int | None) -> str:
    token = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        for key in [k for k, v in _challenges.items() if v["expires"] < now]:
            del _challenges[key]
        _challenges[token] = {"challenge": challenge, "purpose": purpose, "user_id": user_id,
                              "expires": now + CHALLENGE_SECONDS}
    return token


def _take_challenge(token: str, purpose: str) -> dict:
    """Holt die Herausforderung und löscht sie; jede gilt genau einmal."""
    with _lock:
        row = _challenges.pop(str(token), None)
    if not row or row["purpose"] != purpose or row["expires"] < time.time():
        raise PasskeyError(EXPIRED)
    return row


def registration_options(user: dict, rp_id: str) -> dict[str, Any]:
    with store.connect() as con:
        if con.execute("SELECT COUNT(*) FROM passkeys WHERE user_id = ?", (user["id"],)).fetchone()[0] >= MAX_PER_USER:
            raise PasskeyError(TOO_MANY)
        exclude = _descriptors(con, user["id"])
    options = generate_registration_options(
        rp_id=rp_id, rp_name=RP_NAME, user_name=user["name"], user_display_name=user["name"],
        user_id=f"plateshelf-user-{user['id']}".encode(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,          # ohne Benutzernamen anmelden
            user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=exclude,
    )
    token = _store_challenge(options.challenge, "register", user["id"])
    return {"token": token, "options": json.loads(options_to_json(options))}


def register(user: dict, token: str, credential: dict, name: str, rp_id: str, origin: str) -> str:
    row = _take_challenge(token, "register")
    if row["user_id"] != user["id"]:
        raise PasskeyError(EXPIRED)
    try:
        verified = verify_registration_response(credential=credential, expected_challenge=row["challenge"],
                                                expected_rp_id=rp_id, expected_origin=origin,
                                                require_user_verification=True)
    except (InvalidRegistrationResponse, ValueError, KeyError, TypeError) as exc:
        raise PasskeyError(FAILED) from exc
    name = " ".join(str(name or "").split())[:60] or "Passkey"
    with store.connect() as con:
        try:
            con.execute("""INSERT INTO passkeys (user_id, credential_id, public_key, sign_count, name, created)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (user["id"], bytes_to_base64url(verified.credential_id), verified.credential_public_key,
                         verified.sign_count, name, time.time()))
        except Exception as exc:  # schon registriert (Eindeutigkeit der Credential-ID)
            raise PasskeyError(FAILED) from exc
    return name


def authentication_options(rp_id: str) -> dict[str, Any]:
    """Anmeldung ohne Benutzernamen: der Browser bietet die Passkeys für diese Adresse an."""
    options = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED)
    token = _store_challenge(options.challenge, "login", None)
    return {"token": token, "options": json.loads(options_to_json(options))}


def authenticate(token: str, credential: dict, rp_id: str, origin: str) -> int:
    """Prüft die Antwort und liefert die Benutzer-ID des Passkeys."""
    row = _take_challenge(token, "login")
    with store.connect() as con:
        key = con.execute("SELECT * FROM passkeys WHERE credential_id = ?", (str(credential.get("id", "")),)).fetchone()
        if key is None:
            raise PasskeyError(UNKNOWN)
        try:
            verified = verify_authentication_response(
                credential=credential, expected_challenge=row["challenge"], expected_rp_id=rp_id,
                expected_origin=origin, credential_public_key=key["public_key"],
                credential_current_sign_count=key["sign_count"],
                require_user_verification=True)  # ohne Passwort muss der Passkey die Person prüfen
        except (InvalidAuthenticationResponse, ValueError, KeyError, TypeError) as exc:
            raise PasskeyError(FAILED) from exc
        con.execute("UPDATE passkeys SET sign_count = ?, last_used = ? WHERE id = ?",
                    (verified.new_sign_count, time.time(), key["id"]))
        return key["user_id"]
