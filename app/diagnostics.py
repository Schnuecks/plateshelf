# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Diagnostics for copying into a GitHub issue.

Contains version, platform, settings (only whether set), library in numbers,
printer types with their last poll and the latest warnings from the log.
Deliberately not included: credentials, IP addresses, hostnames and printer names.
The text is in English so that it is understandable in any issue.
"""
from __future__ import annotations

import collections
import io
import json
import logging
import os
import platform
import re
import time
import zipfile

from . import auth, captures, crypto, i18n, print_history, printer_import, store

LOG_LINES = 30
FULL_LOG_LINES = 500  # for the download: also info messages
_log: collections.deque = collections.deque(maxlen=LOG_LINES)
_full_log: collections.deque = collections.deque(maxlen=FULL_LOG_LINES)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_URL = re.compile(r"\b(https?://)[^\s/:]+")
ENV_NAMES = ("TZ", "LOG_LEVEL", "SCAN_INTERVAL", "MAX_UPLOAD_MB", "PREVIEW_PREBUILD", "TRASH_DAYS",
             "PRINTER_SYNC_INTERVAL", "PRINTER_IMPORT_DIR", "PRINT_HISTORY", "PRINT_HISTORY_INTERVAL",
             "DEFAULT_LANGUAGE", "DEFAULT_THEME", "TRUSTED_ORIGINS", "AUTH_USER", "AUTH_PASSWORD",
             "AUTH_PASSWORD_HASH", "SESSION_DAYS", "SECRET_KEY")


def mask(text: str) -> str:
    """Mask addresses before anything is shared."""
    return _IPV4.sub("x.x.x.x", _URL.sub(r"\1<host>", text))


class _Recent(logging.Handler):
    """Remembers the latest log messages without writing them: warnings for the text,
    info messages as well for the download."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
            line = f"{when} {record.levelname} {record.name}: {mask(record.getMessage())}"
            _full_log.append(line[:1000])
            if record.levelno >= logging.WARNING:
                _log.append(line[:300])
        except Exception:  # noqa: BLE001  (diagnostics must never interfere itself)
            pass


def install() -> None:
    root = logging.getLogger()
    if not any(isinstance(h, _Recent) for h in root.handlers):
        handler = _Recent(level=logging.INFO)
        root.addHandler(handler)


def _yes(flag) -> str:
    return "on" if flag else "off"


def _sign_in() -> str:
    """Sign-in with number of accounts per role and tokens, without names."""
    if not auth.enabled():
        return "off (AUTH=off)"
    with store.connect() as con:
        roles = dict(con.execute("SELECT role, COUNT(*) FROM users GROUP BY role").fetchall())
        tokens = con.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0]
    if not roles:
        return "on, setup pending"
    return ("on, accounts " + ", ".join(f"{role} {roles[role]}" for role in auth.ROLES if role in roles)
            + f", API tokens {tokens}")


def report(version: str) -> str:
    lines = [f"PlateShelf {version}",
             f"Python {platform.python_version()} on {platform.system()} {platform.machine()}"
             f"{' (container)' if os.path.exists('/.dockerenv') else ''}",
             f"Language {i18n.current()}, sign-in {_sign_in()}, "
             f"print history {_yes(print_history.enabled())}"
             f"{' (set via env)' if print_history.env_setting() is not None else ''}",
             f"Library writable: {_yes(os.access(store.LIBRARY_DIR, os.W_OK))}",
             f"SECRET_KEY {'set' if crypto.secret_key() else 'missing'}"
             f"{' (short)' if crypto.status()['short'] else ''}, "
             f"printer secrets stored in plain text: {printer_import.stored_plaintext()}"]

    with store.connect() as con:
        kinds = collections.Counter()
        for (path,) in con.execute("SELECT path FROM files WHERE missing = 0"):
            low = path.lower()
            kinds[".gcode.3mf" if low.endswith(".gcode.3mf") else low.rsplit(".", 1)[-1]] += 1
        projects = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        errors = [r[0] for r in con.execute(
            "SELECT DISTINCT error FROM files WHERE error IS NOT NULL AND missing = 0 LIMIT 5")]
        jobs = con.execute("SELECT status, COUNT(*) FROM print_jobs WHERE hidden = 0 GROUP BY status").fetchall()
    files = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "none"
    lines.append(f"Library: {projects} projects, files: {files}")
    for e in errors:
        lines.append(f"  read error: {mask(e)[:200]}")
    lines.append("Print history: " + (", ".join(f"{n} {s}" for s, n in jobs) or "empty"))

    printers = printer_import.load_printers()
    lines.append(f"Printers: {len(printers) or 'none'}")
    for i, p in enumerate(printers, 1):
        st = printer_import.state["printers"].get(p["id"])
        if not st:
            result = "not queried yet"
        elif st.get("error"):
            result = "error: " + mask(st["error"])
        else:
            result = f"ok, {st.get('files', 0)} files"
        lines.append(f"  {i}. {p.get('kind', 'bambu')} ({p['source']}): {result}")

    env = [n for n in ENV_NAMES if os.environ.get(n)] + [f"{n}_FILE" for n in ENV_NAMES if os.environ.get(f"{n}_FILE")]
    numbered = len({k.split("_")[1] for k in os.environ if re.fullmatch(r"PRINTER_\d+_\w+", k)})
    if numbered:
        env.append(f"PRINTER_<n>_* ({numbered})")
    lines.append("Set in environment: " + (", ".join(env) or "nothing"))

    lines.append(f"Recent warnings ({len(_log)}):" if _log else "Recent warnings: none")
    lines += [f"  {line}" for line in _log]
    lines.append(f"Generated {time.strftime('%Y-%m-%d %H:%M')}")
    return "\n".join(lines)


# --- Download -------------------------------------------------------------------

# Keys in printer answers whose values are removed (exact names, lower case)
_SECRET_KEYS = {"sn", "dev_id", "ip", "host", "hostname", "ssid", "mac", "net", "user", "username", "owner", "user_id", "uid",
                "code", "password", "token", "key", "apikey", "api_key", "access_code", "url", "ipcam"}
_SECRET_PARTS = ("serial", "passw", "token", "secret", "wifi", "access_code")


def _scrub(value, hide):
    """Remove secret keys and replace names/addresses in all strings."""
    if isinstance(value, dict):
        return {k: "<removed>" if k.lower() in _SECRET_KEYS or any(p in k.lower() for p in _SECRET_PARTS)
                else _scrub(v, hide) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, hide) for v in value]
    if isinstance(value, str):
        return hide(value)
    return value


def _hider(printers: list[dict]):
    """Replaces serial numbers, addresses, access codes and names of the printers."""
    swaps: list[tuple[str, str]] = []
    for i, p in enumerate(printers, 1):
        for field, label in (("code", "<removed>"), ("id", f"<printer {i} id>"), ("ip", "<host>"),
                             ("name", f"printer {i}")):
            value = str(p.get(field) or "").strip()
            if len(value) >= 2:
                swaps.append((value, label))
    swaps.sort(key=lambda s: -len(s[0]))

    def hide(text: str) -> str:
        for value, label in swaps:
            text = text.replace(value, label)
        return mask(text)
    return hide


def bundle(version: str) -> bytes:
    """ZIP for a bug report: the diagnostics text, the last log messages and per printer
    its state and the last raw answers. Without credentials, addresses and printer names."""
    printers = printer_import.load_printers()
    hide = _hider(printers)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("diagnostics.txt", hide(report(version)))
        z.writestr("log.txt", "\n".join(hide(line) for line in _full_log) or "(no messages since the start)")
        for i, p in enumerate(printers, 1):
            data = {
                "printer": f"printer {i}", "kind": p.get("kind", "bambu"), "configured via": p.get("source"),
                "sync state": printer_import.state["printers"].get(p["id"]),
                "last answers": {label: {"at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(a["at"])),
                                         "data": a["data"]}
                                 for label, a in captures.answers(p["id"]).items()},
            }
            z.writestr(f"printer-{i}-{p.get('kind', 'bambu')}.json",
                       json.dumps(_scrub(data, hide), indent=2, ensure_ascii=False, default=str))
    return out.getvalue()
