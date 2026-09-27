# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Diagnose zum Kopieren in ein GitHub-Issue.

Enthält Version, Plattform, Einstellungen (nur ob gesetzt), Bibliothek in Zahlen,
Druckertypen mit ihrem letzten Abruf und die letzten Warnungen aus dem Log.
Bewusst nicht enthalten: Zugangsdaten, IP-Adressen, Hostnamen und Druckernamen.
Der Text ist englisch, damit er in jedem Issue verständlich ist.
"""
from __future__ import annotations

import collections
import logging
import os
import platform
import re

import time

from . import auth, i18n, print_history, printer_import, store

LOG_LINES = 30
_log: collections.deque = collections.deque(maxlen=LOG_LINES)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_URL = re.compile(r"\b(https?://)[^\s/:]+")
ENV_NAMES = ("TZ", "LOG_LEVEL", "SCAN_INTERVAL", "MAX_UPLOAD_MB", "PREVIEW_PREBUILD", "TRASH_DAYS",
             "PRINTER_SYNC_INTERVAL", "PRINTER_IMPORT_DIR", "PRINT_HISTORY", "PRINT_HISTORY_INTERVAL",
             "DEFAULT_LANGUAGE", "DEFAULT_THEME", "TRUSTED_ORIGINS", "AUTH_USER", "SESSION_DAYS")


def mask(text: str) -> str:
    """Adressen unkenntlich machen, bevor etwas geteilt wird."""
    return _IPV4.sub("x.x.x.x", _URL.sub(r"\1<host>", text))


class _Recent(logging.Handler):
    """Merkt sich die letzten Warnungen und Fehler, ohne sie zu schreiben."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(record.created))
            _log.append(f"{when} {record.levelname} {record.name}: {mask(record.getMessage())}"[:300])
        except Exception:  # noqa: BLE001  (Diagnose darf nie selbst stören)
            pass


def install() -> None:
    root = logging.getLogger()
    if not any(isinstance(h, _Recent) for h in root.handlers):
        handler = _Recent(level=logging.WARNING)
        root.addHandler(handler)


def _yes(flag) -> str:
    return "on" if flag else "off"


def report(version: str) -> str:
    lines = [f"PlateShelf {version}",
             f"Python {platform.python_version()} on {platform.system()} {platform.machine()}"
             f"{' (container)' if os.path.exists('/.dockerenv') else ''}",
             f"Language {i18n.current()}, sign-in {_yes(auth.enabled())}, "
             f"print history {_yes(print_history.enabled())}"
             f"{' (set via env)' if print_history.env_setting() is not None else ''}",
             f"Library writable: {_yes(os.access(store.LIBRARY_DIR, os.W_OK))}"]

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

    env = [n for n in ENV_NAMES if os.environ.get(n)]
    numbered = len({k.split("_")[1] for k in os.environ if re.fullmatch(r"PRINTER_\d+_\w+", k)})
    if numbered:
        env.append(f"PRINTER_<n>_* ({numbered})")
    lines.append("Set in environment: " + (", ".join(env) or "nothing"))

    lines.append(f"Recent warnings ({len(_log)}):" if _log else "Recent warnings: none")
    lines += [f"  {line}" for line in _log]
    lines.append(f"Generated {time.strftime('%Y-%m-%d %H:%M')}")
    return "\n".join(lines)
