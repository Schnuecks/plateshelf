# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Prints a sliced plate from the library again ("Print again").

Bambu Lab only for now: the file goes to the printer's SD card via FTPS (the same connection the
import uses), then an MQTT "project_file" command starts the chosen plate. Nothing is sliced here;
the file already holds the G-code.

The printer must be idle, the user confirms that the bed is clear, and filaments are mapped to
the AMS trays the printer currently reports. Newer Bambu firmware only accepts these commands
from other programs in LAN mode with developer mode switched on.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from . import print_history, printer_import, store
from .i18n import UserError

log = logging.getLogger("plateshelf.print")

# States in which a new print may be started
IDLE_STATES = ("IDLE", "FINISH", "FAILED")
# Serial number prefixes of models that address the SD card as file:///sdcard/ (X1, P1, A1);
# newer models (P2S, H2D, H2S, H2C …) use ftp:///
LEGACY_SERIAL_PREFIXES = ("00M", "00W", "03W", "01S", "01P", "030", "039")
EXTERNAL = -1          # mapping value for the external spool
TRAYS_PER_UNIT = 4
HT_UNIT_BASE = 128     # AMS HT units report ids 128-135 and use flat slots 16-23
HT_SLOT_BASE = 16
REPLY_WAIT = 10        # seconds to wait for the printer to accept or refuse the start command


def env_setting() -> bool | None:
    """PRINT_SEND from the environment; None if unset (the setting applies then)."""
    value = os.environ.get("PRINT_SEND", "").strip().lower()
    if not value:
        return None
    return value not in ("0", "false", "no", "off")


def enabled() -> bool:
    """Off by default: starting prints from PlateShelf needs developer mode on the printer."""
    env = env_setting()
    if env is not None:
        return env
    return bool(store.get_settings().get("drucken_senden", 0))


def set_enabled(on: bool) -> None:
    store.save_settings({"drucken_senden": 1 if on else 0})


def printers() -> list[dict]:
    """Bambu printers that could receive a print (credentials readable); none while switched off."""
    if not enabled():
        return []
    return [p for p in printer_import.load_printers() if p["kind"] == "bambu" and not p.get("code_error")]


def _watcher(printer_id: str):
    entry = print_history._watchers.get(printer_id)
    return entry[1] if entry else None


def status(printer_id: str) -> dict | None:
    """Last state the printer reported over MQTT, or None without a connection."""
    watcher = _watcher(printer_id)
    if watcher is None or not watcher.tracker.state:
        return None
    return watcher.tracker.state


def all_busy(candidates: list[dict]) -> bool:
    """True if every printer reports a print (or pause) right now; unknown states count as free."""
    states = [status(p["id"]) for p in candidates]
    return bool(states) and all(s is not None and not is_idle(s) for s in states)


def is_idle(state: dict | None) -> bool:
    return bool(state) and str(state.get("gcode_state") or "").upper() in IDLE_STATES


def _color(raw: str) -> str:
    raw = (raw or "").lstrip("#")[:6]
    return f"#{raw.upper()}" if re.fullmatch(r"[0-9A-Fa-f]{6}", raw) else "#888888"


def trays(state: dict | None) -> list[dict]:
    """Loaded AMS trays as {slot, unit, tray, type, color, name}; slot is the flat index the
    printer expects in ams_mapping (unit * 4 + tray, AMS HT from 16)."""
    out = []
    for unit in ((state or {}).get("ams") or {}).get("ams") or []:
        try:
            unit_id = int(unit.get("id"))
        except (TypeError, ValueError):
            continue
        for tray in unit.get("tray") or []:
            if not tray.get("tray_type"):
                continue  # empty tray
            try:
                tray_id = int(tray.get("id"))
            except (TypeError, ValueError):
                continue
            if unit_id >= HT_UNIT_BASE:
                slot = HT_SLOT_BASE + unit_id - HT_UNIT_BASE
            else:
                slot = unit_id * TRAYS_PER_UNIT + tray_id
            out.append({"slot": slot, "unit": unit_id, "tray": tray_id, "type": tray["tray_type"],
                        "color": _color(tray.get("tray_color", "")), "name": tray.get("tray_sub_brands") or ""})
    return sorted(out, key=lambda t: t["slot"])


def _rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def color_distance(a: str, b: str) -> float:
    """Distance of two #RRGGBB colours (0 = equal, about 441 = black to white)."""
    return sum((x - y) ** 2 for x, y in zip(_rgb(_color(a)), _rgb(_color(b)), strict=True)) ** 0.5


COLOR_WARN = 80  # from this distance the page points out that the colour differs


def suggest(filaments: list[dict], loaded: list[dict]) -> dict[int, int | None]:
    """Proposed tray per filament slot: same type and the closest colour; None if no tray has
    that type. Each tray is proposed once as long as there are enough."""
    out: dict[int, int | None] = {}
    taken: set[int] = set()
    for fil in sorted(filaments, key=lambda f: -(f.get("used_g") or 0)):  # most used filament first
        kind = (fil.get("type") or "").upper()
        same = [t for t in loaded if t["type"].upper() == kind]
        if not same:
            out[fil["slot"]] = None
            continue
        target = _rgb(_color(fil.get("color", "")))
        free = [t for t in same if t["slot"] not in taken] or same
        best = min(free, key=lambda t: sum((a - b) ** 2 for a, b in zip(_rgb(t["color"]), target, strict=True)))
        out[fil["slot"]] = best["slot"]
        taken.add(best["slot"])
    return out


def remote_name(local_path: str) -> str:
    """File name on the SD card: the library file name, reduced to safe characters."""
    name = Path(local_path).name
    stem = name[:-len(".gcode.3mf")] if name.lower().endswith(".gcode.3mf") else Path(name).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "plateshelf"
    return f"{stem[:80]}.gcode.3mf"


def command(printer_id: str, name: str, plate: int, mapping: dict[int, int], *, bed_leveling: bool = True,
            timelapse: bool = False) -> dict:
    """MQTT command that starts plate `plate` of the file `name` on the SD card.

    mapping: filament slot (1-based, as in the file) -> flat AMS slot, or EXTERNAL."""
    url = f"file:///sdcard/{name}" if printer_id[:3] in LEGACY_SERIAL_PREFIXES else f"ftp:///{name}"
    use_ams = any(v != EXTERNAL for v in mapping.values())
    size = max(mapping) if mapping else 0
    flat = [-1] * size
    detailed = [{"ams_id": 255, "slot_id": 255} for _ in range(size)]
    for slot, target in mapping.items():
        if target == EXTERNAL:
            flat[slot - 1] = 254
            detailed[slot - 1] = {"ams_id": 255, "slot_id": 0}
        elif target >= HT_SLOT_BASE:
            flat[slot - 1] = target
            detailed[slot - 1] = {"ams_id": HT_UNIT_BASE + target - HT_SLOT_BASE, "slot_id": 0}
        else:
            flat[slot - 1] = target
            detailed[slot - 1] = {"ams_id": target // TRAYS_PER_UNIT, "slot_id": target % TRAYS_PER_UNIT}
    return {"print": {
        "sequence_id": str(int(time.time())),
        "command": "project_file",
        "param": f"Metadata/plate_{plate}.gcode",
        "url": url,
        "subtask_name": name[:-len(".gcode.3mf")],
        "bed_type": "auto",
        "bed_leveling": bed_leveling,
        "timelapse": timelapse,
        "flow_cali": False,
        "vibration_cali": False,
        "layer_inspect": False,
        "use_ams": use_ams,
        "ams_mapping": flat if use_ams else [],
        "ams_mapping2": detailed if use_ams else [],
        "profile_id": "0", "project_id": "0", "subtask_id": "0", "task_id": "0",
    }}


def _check_reply(printer: dict, replies: dict, seq: str) -> None:
    """Waits for the printer's answer to the start command. No answer counts as accepted (not every
    firmware answers); a refusal becomes a UserError."""
    deadline = time.monotonic() + REPLY_WAIT
    while (reply := replies.get(seq)) is None and time.monotonic() < deadline:
        time.sleep(0.2)
    if reply is None or str(reply.get("result", "")).lower() in ("success", "ok"):
        return
    reason = str(reply.get("reason") or reply.get("err_code") or reply.get("result") or "")
    log.warning("%s: Startbefehl abgelehnt: %s", printer["name"], reply)
    # P2S, firmware 2026-10: {"result": "failed", "reason": "mqtt message verify failed", "err_code": 84033543}
    if reply.get("err_code") == 84033543 or "verif" in reason.lower() or "sign" in reason.lower():
        raise UserError("Der Drucker hat den Startbefehl abgelehnt. Neuere Bambu-Firmware nimmt ihn nur im "
                        "LAN-Modus mit eingeschaltetem Entwicklermodus an.")
    raise UserError("Der Drucker hat den Startbefehl abgelehnt: {reason}", reason=reason)


def plate_info(fid: int, plate: int) -> tuple[dict, dict] | None:
    """(file row, plate row with filaments) of a sliced plate in a .gcode.3mf, or None."""
    with store.connect() as con:
        f = con.execute("SELECT id, path, project_id FROM files WHERE id = ? AND missing = 0", (fid,)).fetchone()
        p = con.execute("SELECT idx, name, prediction_s, weight_g, filaments_json FROM plates "
                        "WHERE file_id = ? AND idx = ? AND sliced = 1", (fid, plate)).fetchone()
    if not f or not p or not f["path"].lower().endswith(".gcode.3mf"):
        return None
    plate_row = dict(p)
    plate_row["filaments"] = json.loads(plate_row.pop("filaments_json") or "[]")
    return dict(f), plate_row


def send(printer: dict, fid: int, plate: int, mapping: dict[int, int], *, bed_leveling: bool = True,
         timelapse: bool = False, connect=None) -> str:
    """Uploads the file and starts the plate. Returns the file name on the printer.

    Raises UserError with a message for the user if the printer is busy, unreachable or the
    mapping is incomplete."""
    info = plate_info(fid, plate)
    if info is None:
        raise UserError("Diese Platte ist nicht gesliced oder die Datei fehlt.")
    file_row, plate_row = info
    used = {f["slot"] for f in plate_row["filaments"]}
    if used - set(mapping):
        raise UserError("Jedem Filament muss ein Fach zugeordnet sein.")
    if sum(1 for s in used if mapping[s] == EXTERNAL) > 1 or (
            any(mapping[s] == EXTERNAL for s in used) and len(used) > 1):
        raise UserError("Die externe Spule geht nur bei Platten mit einem einzigen Filament.")
    watcher = _watcher(printer["id"])
    if watcher is None:
        raise UserError("Keine Verbindung zum Drucker. Ist der Druckverlauf in den Einstellungen eingeschaltet?")
    if not is_idle(watcher.tracker.state):
        raise UserError("Der Drucker ist gerade beschäftigt.")

    path = (store.LIBRARY_DIR / file_row["path"]).resolve()
    if not path.is_relative_to(store.LIBRARY_DIR.resolve()) or not path.exists():
        raise UserError("Diese Platte ist nicht gesliced oder die Datei fehlt.")
    name = remote_name(file_row["path"])
    ftp = (connect or printer_import._connect)(printer)
    try:
        with path.open("rb") as fh:
            ftp.storbinary(f"STOR /{name}", fh, blocksize=256 * 1024)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: Upload von %s fehlgeschlagen: %s", printer["name"], name, exc)
        raise UserError("Die Datei ließ sich nicht auf den Drucker laden.") from exc
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            ftp.close()

    payload = command(printer["id"], name, plate, {s: mapping[s] for s in used},
                      bed_leveling=bed_leveling, timelapse=timelapse)
    seq = payload["print"]["sequence_id"]
    replies = getattr(watcher, "replies", {})
    replies.pop(seq, None)
    info = watcher.client.publish(f"device/{printer['id']}/request", json.dumps(payload))
    if getattr(info, "rc", 0) != 0:
        raise UserError("Der Startbefehl ließ sich nicht an den Drucker senden.")
    _check_reply(printer, replies, seq)
    log.info("%s: Druck von %s, Platte %d gestartet", printer["name"], name, plate)
    return name

