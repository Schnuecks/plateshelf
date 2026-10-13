# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Last raw answer of each printer query, kept in memory for the diagnostics download.

Nothing is written to disk; diagnostics.py removes credentials, serial numbers, addresses and
printer names before anything leaves the app.
"""
from __future__ import annotations

import json
import threading
import time

MAX_CHARS = 200_000  # per answer; a long file list or history is cut off

_lock = threading.Lock()
_answers: dict[str, dict[str, dict]] = {}


def remember(printer_id: str, label: str, data, merge: bool = False) -> None:
    """Keep the answer to one query of a printer. merge=True adds to the previous answer
    (Bambu sends only changes after the first full report)."""
    try:
        with _lock:
            slot = _answers.setdefault(str(printer_id), {})
            if merge and isinstance(data, dict) and isinstance(slot.get(label, {}).get("data"), dict):
                data = _merged(slot[label]["data"], data)
            if len(json.dumps(data, default=str)) > MAX_CHARS:
                data = {"truncated": True, "start": json.dumps(data, default=str)[:MAX_CHARS]}
            slot[label] = {"at": time.time(), "data": data}
    except Exception:  # noqa: BLE001  (diagnostics must never interfere)
        pass


def _merged(old: dict, new: dict) -> dict:
    out = dict(old)
    for k, v in new.items():
        out[k] = _merged(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def answers(printer_id: str) -> dict[str, dict]:
    with _lock:
        return json.loads(json.dumps(_answers.get(str(printer_id), {}), default=str))


def clear() -> None:
    with _lock:
        _answers.clear()
