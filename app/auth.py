# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Login with user accounts, sessions and API tokens.

Login is the default. As long as no account exists, every page leads to the
setup (/setup), which requires a setup code from the container log; that way
nobody on the owner's network can get there first. Existing credentials from
AUTH_USER and AUTH_PASSWORD_HASH (or AUTH_PASSWORD) become the first admin
account once at startup. AUTH=off disables login, e.g. when the reverse proxy
already handles login.

Roles: admin (everything), user (everything except settings and user management),
viewer (view and download only).

Sessions live in the database; the cookie only holds a random value, the
database only its hash. Logging out invalidates a session immediately, a new
password ends all others. API tokens for dashboards are read-only
(GET under /api/) and can be revoked individually. Scripts can still log in
via HTTP Basic Auth with username and password.
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

from . import crypto, store
from .i18n import UserError

log = logging.getLogger("plateshelf")

COOKIE = "sitzung"
SESSION_DAYS = max(1, int(os.environ.get("SESSION_DAYS", "30")))
MAX_FAILURES = 5            # failed attempts per address …
FAILURE_WINDOW = 15 * 60    # … within this time (s) …
LOCKOUT = 5 * 60            # … lock it out for this time (s)
SCRYPT_N_LOG2, SCRYPT_R, SCRYPT_P = 15, 8, 1
BASIC_CACHE_SECONDS = 300   # verified Basic Auth headers, so dashboards don't compute scrypt every time
TOUCH_SECONDS = 60          # write "last active" at most once per minute
MIN_PASSWORD = 8
ROLES = ("admin", "user", "viewer")
TOKEN_PREFIX = "ps_"
SETUP_KEY = "auth:setup_code"
NO_PASSWORD = "-"  # accounts that log in only via OIDC
_SETUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # without 0/O and 1/I

_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()
_basic_ok: dict[str, tuple[int, str, float]] = {}   # hash of the header -> (user, password hash, valid until)
_DUMMY_HASH = ""


EVENT_DAYS = 180   # how long the log is kept


def password_login_wanted_off() -> bool:
    return os.environ.get("PASSWORD_LOGIN", "").strip().lower() in ("off", "false", "0", "no")


def password_login() -> bool:
    """Login with password (form and Basic Auth). PASSWORD_LOGIN=off disables it, but
    only if there is another way in (OIDC configured or a passkey present) – otherwise
    nobody could get in anymore."""
    if not password_login_wanted_off():
        return True
    from . import oidc  # oidc uses auth; import it only here
    if oidc.enabled():
        return False
    with store.connect() as con:
        return con.execute("SELECT 1 FROM passkeys LIMIT 1").fetchone() is None


def log_event(kind: str, user_name: str = "", address: str = "", agent: str = "", detail: str = "") -> None:
    """Entry in the log (Einstellungen → Protokoll); entries older than EVENT_DAYS are dropped."""
    now = time.time()
    with store.connect() as con:
        con.execute("INSERT INTO auth_events (at, kind, user_name, address, agent, detail) VALUES (?, ?, ?, ?, ?, ?)",
                    (now, kind, user_name[:64], address[:64], agent[:200], detail[:200]))
        con.execute("DELETE FROM auth_events WHERE at < ?", (now - EVENT_DAYS * 86400,))


def events(limit: int = 300) -> list[dict]:
    with store.connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM auth_events ORDER BY at DESC, id DESC LIMIT ?", (limit,))]


def enabled() -> bool:
    """Login active unless AUTH=off (or false, 0, no)."""
    return os.environ.get("AUTH", "").strip().lower() not in ("off", "false", "0", "no")


# --- Password hash (scrypt, included in Python) -------------------------------
# Format without "$", because Docker Compose reads "$" in the .env as a variable:
#   scrypt:<log2 N>:<r>:<p>:<salt>:<hash>   (salt and hash as URL-safe Base64)

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
    if hashed == NO_PASSWORD:
        return False
    try:
        kind, n_log2, r, p, salt, digest = hashed.split(":")
        if kind != "scrypt":
            return False
        return hmac.compare_digest(_scrypt(password, _unb64(salt), int(n_log2), int(r), int(p)), _unb64(digest))
    except (ValueError, binascii.Error):
        log.warning("Passwort-Hash hat kein gültiges Format; neu erzeugen mit „python -m app.auth hash“")
        return False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- Users ----------------------------------------------------------------------

def _user(row) -> dict | None:
    return {"id": row["id"], "name": row["name"], "role": row["role"]} if row else None


def has_users() -> bool:
    with store.connect() as con:
        return con.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def list_users() -> list[dict]:
    with store.connect() as con:
        return [dict(r) for r in con.execute(
            """SELECT u.id, u.name, u.role, u.created, u.last_login,
                      (SELECT COUNT(*) FROM sessions s WHERE s.user_id = u.id AND s.expires > ?) AS sessions
               FROM users u ORDER BY u.name COLLATE NOCASE""", (time.time(),))]


def get_user(user_id: int) -> dict | None:
    with store.connect() as con:
        return _user(con.execute("SELECT id, name, role FROM users WHERE id = ?", (user_id,)).fetchone())


def has_password(user_id: int) -> bool:
    with store.connect() as con:
        row = con.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row) and row["password_hash"] != NO_PASSWORD


def check_password_rules(password: str, repeat: str | None = None) -> None:
    if repeat is not None and password != repeat:
        raise UserError("Die Passwörter stimmen nicht überein.")
    if len(password) < MIN_PASSWORD:
        raise UserError("Das Passwort braucht mindestens {n} Zeichen.", n=MIN_PASSWORD)


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name or len(name) > 64 or any(ord(c) < 32 for c in name):
        raise UserError("Bitte einen Benutzernamen mit 1 bis 64 Zeichen angeben.")
    return name


def create_user(name: str, password: str, role: str = "user", *, hashed: str | None = None) -> int:
    name = _clean_name(name)
    if role not in ROLES:
        raise UserError("Unbekannte Rolle.")
    if hashed is None:
        check_password_rules(password)
        hashed = hash_password(password)
    with store.connect() as con:
        if con.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
            raise UserError("Den Benutzernamen „{name}“ gibt es schon.", name=name)
        cur = con.execute("INSERT INTO users (name, password_hash, role, created) VALUES (?, ?, ?, ?)",
                          (name, hashed, role, time.time()))
        return cur.lastrowid


def _admins(con) -> int:
    return con.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]


def set_role(user_id: int, role: str) -> None:
    if role not in ROLES:
        raise UserError("Unbekannte Rolle.")
    with store.connect() as con:
        row = con.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return
        if row["role"] == "admin" and role != "admin" and _admins(con) <= 1:
            raise UserError("Es muss mindestens ein Admin bleiben.")
        con.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def set_password(user_id: int, password: str, keep_session: str | None = None) -> None:
    """New password; ends all sessions of the user except keep_session (cookie value)."""
    check_password_rules(password)
    hashed = hash_password(password)
    with store.connect() as con:
        con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed, user_id))
        con.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                    (user_id, _digest(keep_session) if keep_session else ""))


def delete_user(user_id: int, acting_id: int | None = None) -> None:
    if user_id == acting_id:
        raise UserError("Das eigene Konto lässt sich nicht löschen.")
    with store.connect() as con:
        row = con.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return
        if row["role"] == "admin" and _admins(con) <= 1:
            raise UserError("Es muss mindestens ein Admin bleiben.")
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))


def authenticate(name: str, password: str) -> dict | None:
    """User if the password is correct, otherwise None; same runtime whether the name exists or not."""
    global _DUMMY_HASH
    if not password_login():
        return None
    with store.connect() as con:
        row = con.execute("SELECT id, name, role, password_hash FROM users WHERE name = ?",
                          (" ".join(name.split()),)).fetchone()
    if not row:
        _DUMMY_HASH = _DUMMY_HASH or hash_password(secrets.token_hex(8))
        verify_password(password, _DUMMY_HASH)
        return None
    return _user(row) if verify_password(password, row["password_hash"]) else None


def login_external(subject: str, name: str, role: str | None) -> int:
    """Account for an OIDC login: linked, same name or new; returns the user ID.

    subject is issuer + sub and stays the same even if the name changes at the provider.
    role comes from the groups (None: no mapping configured, role stays as in PlateShelf).
    """
    name = _clean_name(name)
    with store.connect() as con:
        row = (con.execute("SELECT id, role FROM users WHERE oidc_sub = ?", (subject,)).fetchone()
               or con.execute("SELECT id, role FROM users WHERE name = ? AND oidc_sub IS NULL", (name,)).fetchone())
        if row:
            con.execute("UPDATE users SET oidc_sub = ? WHERE id = ?", (subject, row["id"]))
            user_id, current = row["id"], row["role"]
        else:
            if con.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
                raise UserError("Den Benutzernamen „{name}“ gibt es schon.", name=name)
            first = _admins(con) == 0
            if first and role != "admin":
                # The owner creates the first account with the setup code (or via the admin group)
                raise UserError("Lege zuerst das Admin-Konto an.")
            cur = con.execute("INSERT INTO users (name, password_hash, role, created, oidc_sub) VALUES (?, ?, ?, ?, ?)",
                              (name, NO_PASSWORD, role or "viewer", time.time(), subject))
            if first:
                con.execute("DELETE FROM settings WHERE key = ?", (SETUP_KEY,))
            log.info("Konto „%s“ bei der ersten Anmeldung über OIDC angelegt (%s)", name, role or "viewer")
            return cur.lastrowid
    if role and role != current:
        try:
            set_role(user_id, role)
        except UserError:
            log.warning("Rolle von „%s“ bleibt %s: es wäre kein Admin mehr übrig", name, current)
    return user_id


def seed_from_env() -> None:
    """Credentials from AUTH_USER and AUTH_PASSWORD_HASH/AUTH_PASSWORD become the first admin once."""
    if has_users():
        return
    user = crypto.env_value("AUTH_USER")
    hashed = crypto.env_value("AUTH_PASSWORD_HASH")
    password = crypto.env_value("AUTH_PASSWORD")
    if not user or not (hashed or password):
        return
    create_user(user, "", "admin", hashed=hashed or hash_password(password))
    log.info("Admin-Konto „%s“ aus AUTH_USER angelegt. AUTH_USER und das Passwort können jetzt aus der .env; "
             "das Passwort änderst du ab sofort in PlateShelf.", user)


# --- Setup ----------------------------------------------------------------------

def setup_code() -> str:
    """Setup code while no account exists (in the database, so the console command knows it)."""
    with store.connect() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (SETUP_KEY,)).fetchone()
        if row:
            return row["value"]
        code = "-".join("".join(secrets.choice(_SETUP_ALPHABET) for _ in range(4)) for _ in range(3))
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (SETUP_KEY, code))
        return code


def announce_setup() -> None:
    """At startup without an account: write the setup code to the log."""
    if enabled() and not has_users():
        # Bilingual: every owner must find this one line, even with the English docs
        code = setup_code()
        log.warning("No user account yet: open PlateShelf in the browser and create the admin account. "
                    "Setup code: %s", code)
        log.warning("Noch kein Benutzerkonto: Öffne PlateShelf im Browser und lege das Admin-Konto an. "
                    "Einrichtungscode: %s", code)


def setup_code_valid(code: str) -> bool:
    wanted = setup_code().replace("-", "")
    given = "".join(code.upper().split()).replace("-", "")
    return hmac.compare_digest(given.encode(), wanted.encode())


def finish_setup(name: str, password: str, repeat: str) -> int:
    if has_users():
        raise UserError("Die Einrichtung ist schon abgeschlossen.")
    check_password_rules(password, repeat)
    user_id = create_user(name, password, "admin")
    store.save_settings({SETUP_KEY: None})
    return user_id


# --- Sessions -------------------------------------------------------------------

def new_session(user_id: int, agent: str = "", address: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with store.connect() as con:
        con.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        con.execute("""INSERT INTO sessions (token_hash, user_id, created, last_seen, expires, agent, address)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (_digest(token), user_id, now, now, now + SESSION_DAYS * 86400, agent[:200], address[:64]))
        con.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user_id))
    return token


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    now = time.time()
    with store.connect() as con:
        row = con.execute(
            """SELECT u.id, u.name, u.role, s.id AS sid, s.last_seen FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires > ?""", (_digest(token), now)).fetchone()
        if row and now - row["last_seen"] > TOUCH_SECONDS:
            con.execute("UPDATE sessions SET last_seen = ? WHERE id = ?", (now, row["sid"]))
    return {**_user(row), "session_id": row["sid"]} if row else None


def end_session(token: str | None) -> None:
    if token:
        with store.connect() as con:
            con.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(token),))


def user_sessions(user_id: int) -> list[dict]:
    with store.connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, created, last_seen, agent, address FROM sessions WHERE user_id = ? AND expires > ? "
            "ORDER BY last_seen DESC", (user_id, time.time()))]


def end_user_session(user_id: int, session_id: int) -> None:
    with store.connect() as con:
        con.execute("DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id))


def end_other_sessions(user_id: int, keep_id: int | None) -> None:
    with store.connect() as con:
        con.execute("DELETE FROM sessions WHERE user_id = ? AND id != ?", (user_id, keep_id or 0))


def basic_auth_user(header: str | None) -> dict | None:
    if not header or not header.lower().startswith("basic ") or not password_login():
        return None
    cache_key = _digest(header)
    now = time.time()
    cached = _basic_ok.get(cache_key)
    if cached and cached[2] > now:
        # Valid only while user and password are unchanged
        with store.connect() as con:
            row = con.execute("SELECT id, name, role, password_hash FROM users WHERE id = ?", (cached[0],)).fetchone()
        if row and row["password_hash"] == cached[1]:
            return _user(row)
        _basic_ok.pop(cache_key, None)
    try:
        name, _, password = base64.b64decode(header[6:].strip()).decode().partition(":")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    user = authenticate(name, password)
    if not user:
        return None
    with store.connect() as con:
        hashed = con.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()[0]
    if len(_basic_ok) > 100:
        _basic_ok.clear()
    _basic_ok[cache_key] = (user["id"], hashed, now + BASIC_CACHE_SECONDS)
    return user


# --- API tokens (read-only) -----------------------------------------------------

def create_token(name: str, created_by: str = "") -> str:
    """New token; the plaintext is returned only this once."""
    name = " ".join(name.split())[:64]
    if not name:
        raise UserError("Bitte einen Namen für das Token angeben, z. B. „Homepage“.")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with store.connect() as con:
        con.execute("INSERT INTO api_tokens (name, token_hash, hint, created, created_by) VALUES (?, ?, ?, ?, ?)",
                    (name, _digest(token), token[-4:], time.time(), created_by))
    return token


def list_tokens() -> list[dict]:
    with store.connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, name, hint, created, created_by, last_used FROM api_tokens ORDER BY created")]


def delete_token(token_id: int) -> None:
    with store.connect() as con:
        con.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))


def token_from_headers(headers) -> str:
    value = headers.get("authorization") or ""
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return (headers.get("x-api-key") or "").strip()


def token_valid(token: str) -> bool:
    if not token.startswith(TOKEN_PREFIX):
        return False
    now = time.time()
    with store.connect() as con:
        row = con.execute("SELECT id, last_used FROM api_tokens WHERE token_hash = ?", (_digest(token),)).fetchone()
        if row and now - (row["last_used"] or 0) > TOUCH_SECONDS:
            con.execute("UPDATE api_tokens SET last_used = ? WHERE id = ?", (now, row["id"]))
    return row is not None


# --- Protection against brute force ------------------------------------------

def locked_out(address: str) -> int:
    """Seconds this address is still locked out (0 = not locked)."""
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


# --- Console commands -----------------------------------------------------------

def _ask_password() -> str | None:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat: "):
        print("The passwords do not match.", file=sys.stderr)
        return None
    if len(password) < MIN_PASSWORD:
        print(f"Please use at least {MIN_PASSWORD} characters.", file=sys.stderr)
        return None
    return password


def main(argv: list[str]) -> int:
    """python -m app.auth hash | secret | setup-code | reset <name> (output in English, like the docs)"""
    command = argv[1] if len(argv) > 1 else ""
    if command == "hash":
        password = _ask_password()
        if password is None:
            return 1
        print(f"AUTH_PASSWORD_HASH={hash_password(password)}")
        return 0
    if command == "secret":
        print(f"SECRET_KEY={secrets.token_urlsafe(48)}")
        return 0
    if command == "setup-code":
        store.init_db()
        if has_users():
            print("Setup is already done; sign in with your account.", file=sys.stderr)
            return 1
        print(setup_code())
        return 0
    if command == "reset" and len(argv) > 2:
        store.init_db()
        name = argv[2]
        with store.connect() as con:
            row = con.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
        password = _ask_password()
        if password is None:
            return 1
        if row:
            set_password(row["id"], password)
            print(f"New password set for {name}; all their sessions have been ended.")
        else:
            create_user(name, password, "admin")
            print(f"Admin account {name} created.")
        return 0
    print("Usage: python -m app.auth reset <name>  (set a new password, or create an admin account)\n"
          "       python -m app.auth setup-code    (show the setup code before the first account exists)\n"
          "       python -m app.auth secret        (random SECRET_KEY)\n"
          "       python -m app.auth hash          (password hash, only needed for AUTH_PASSWORD_HASH)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
