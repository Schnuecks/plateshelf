# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Aufbereitung der Projekte, Dateien und Platten für die Seiten."""
from __future__ import annotations

import json
import re
import threading
import time

from . import print_history, store
from .i18n import gettext as _

def plate_source(model_json: str | None) -> str:
    """Kennung des Ursprungsmodells einer Datei (Titel aus der 3MF), um Platten zuzuordnen."""
    try:
        return (json.loads(model_json or "{}").get("title") or "").strip().lower()
    except ValueError:
        return ""


def natural_key(text: str):
    """„plate_2“ vor „plate_10“: Zahlen in Namen als Zahlen sortieren."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text or "")]


def file_stamp(f) -> str:
    """Kurzer Stempel je Dateistand für Bildadressen: neue oder geänderte Datei = neue Adresse."""
    return format(int((f["mtime"] or 0) * 1000) ^ (f["size"] or 0), "x")


def _merge_filaments(plates) -> list[dict]:
    merged: dict[tuple, dict] = {}
    for p in plates:
        for f in p["filaments"]:
            key = (f["color"], f["type"])
            m = merged.setdefault(key, {**f, "used_g": 0.0, "used_m": 0.0, "cost": 0.0, "has_cost": False})
            m["used_g"] += f.get("used_g") or 0
            m["used_m"] += f.get("used_m") or 0
            if f.get("cost_per_kg") is not None:
                m["cost"] += (f.get("used_g") or 0) / 1000 * f["cost_per_kg"]
                m["has_cost"] = True
    return sorted(merged.values(), key=lambda f: -f["used_g"])


def _plate_dict(row) -> dict:
    d = dict(row)
    d["filaments"] = json.loads(d.pop("filaments_json") or "[]")
    objs = json.loads(d.pop("objects_json") or "[]")
    counts = dict.fromkeys(objs, 0)
    for o in objs:
        counts[o] += 1
    d["objects"] = objs
    d["objects_label"] = ", ".join(f"{n}× {o}" if n > 1 else o for o, n in counts.items())
    d["warnings"] = json.loads(d.pop("warnings_json") or "[]")
    return d


def _total(material, energy):
    parts = [x for x in (material, energy) if x is not None]
    return sum(parts) if parts else None


CACHE_SECONDS = 60  # höchstens so lange, auch wenn sich scheinbar nichts geändert hat
_cache: dict = {"sig": None, "at": 0.0, "projects": []}
_cache_lock = threading.Lock()


def load_projects(con) -> list[dict]:
    """Alle Projekte mit Dateien, Platten, Kosten und Druckverlauf.

    Die Aufbereitung wird zwischengespeichert, bis sich die Datenbank ändert: Bei
    großen Bibliotheken spart das bei jedem Seitenaufruf die meiste Zeit. Laufende
    Drucke kommen immer frisch dazu. Die Projekte nur lesen, nicht verändern.
    """
    sig = store.db_signature()
    with _cache_lock:
        fresh = _cache["sig"] == sig and time.monotonic() - _cache["at"] < CACHE_SECONDS
        if not fresh:
            _cache.update(projects=_build(con), sig=sig, at=time.monotonic())
        cached = _cache["projects"]
    live = {}
    for job in print_history.printing_now():
        live.setdefault(job["project_id"], job)
    return [{**p, "printing": live.get(p["id"])} if p["id"] in live else p for p in cached]


def _build(con) -> list[dict]:
    settings = store.get_settings(con)
    projects = {r["id"]: {**dict(r), "files": [], "plates": [], "extra_plates": []}
                for r in con.execute("SELECT * FROM projects")}
    files = {}
    for r in con.execute(
        "SELECT id, project_id, path, printer, nozzle, layer_height, added, mtime, size, sliced, model_json FROM files WHERE missing = 0"
    ):
        if r["project_id"] in projects:
            files[r["id"]] = dict(r)
            files[r["id"]]["source"] = plate_source(files[r["id"]].pop("model_json"))
            projects[r["project_id"]]["files"].append(files[r["id"]])
    for r in con.execute("SELECT * FROM plates ORDER BY file_id, idx"):
        f = files.get(r["file_id"])
        if f:
            pd = _plate_dict(r)
            pd["file_id"] = f["id"]
            pd["v"] = file_stamp(f)
            # Strom: Druckzeit × durchschnittliche Leistung des Druckers × Strompreis
            watts = store.printer_watts(settings, f["printer"])
            pd["energy_kwh"] = (pd["prediction_s"] or 0) / 3600 * watts / 1000
            pd["energy_cost"] = pd["energy_kwh"] * settings["strompreis"]
            pd["total_cost"] = _total(pd["cost"], pd["energy_cost"])
            # Nicht geslicte Platten einer geslicten Datei: nur Vorschau, zählen nicht mit
            bucket = "plates" if pd["sliced"] else "extra_plates"
            projects[f["project_id"]][bucket].append(pd)

    for p in projects.values():
        # Einzeln exportierte Platten enthalten Vorschauen aller anderen Platten.
        # Diese nur zeigen, wenn die Platte nirgends im Projekt gesliced vorliegt, und nur einmal.
        src = {f["id"]: f["source"] for f in p["files"]}
        covered = {(src[x["file_id"]], x["idx"]) for x in p["plates"]}
        seen, extras = set(), []
        for x in p["extra_plates"]:
            key = (src[x["file_id"]], x["idx"])
            if key in covered or key in seen:
                continue
            seen.add(key)
            extras.append(x)
        p["extra_plates"] = extras

    printed = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT local_path, COUNT(*), MAX(COALESCE(ended, started)) FROM print_jobs WHERE status = 'completed' "
        "AND local_path IS NOT NULL AND hidden = 0 GROUP BY local_path")}
    out = []
    for p in projects.values():
        if not p["files"]:
            continue
        plates = p["plates"]
        p["time_s"] = sum(x["prediction_s"] or 0 for x in plates)
        p["weight_g"] = sum(x["weight_g"] or 0 for x in plates)
        costs = [x["cost"] for x in plates if x["cost"] is not None]
        p["cost"] = sum(costs) if costs else None
        p["energy_cost"] = sum(x["energy_cost"] for x in plates) if plates else None
        p["energy_kwh"] = sum(x["energy_kwh"] for x in plates)
        p["total_cost"] = _total(p["cost"], p["energy_cost"])
        p["filaments"] = _merge_filaments(plates)
        p["materials"] = sorted({f["type"] for f in p["filaments"] if f["type"]})
        p["printers"] = sorted({f["printer"] for f in p["files"] if f["printer"]})
        p["latest"] = max(f["mtime"] or 0 for f in p["files"])
        cover = next((x for x in plates if x["has_thumb"]), None)
        p["cover"] = f"/thumb/{cover['file_id']}/plate/{cover['idx']}?v={cover['v']}" if cover else None
        p["cover_is_photo"] = bool(p.get("cover_photo"))
        if p["cover_is_photo"]:  # eigenes Foto vom Druck statt Slicer-Vorschau
            p["cover"] = f"/p/{p['id']}/photos/{p['cover_photo'].removesuffix('.jpg')}.small.jpg"
        p["plates_total"] = len(plates) + len(p["extra_plates"])
        p["tag_list"] = [t.strip() for t in p["tags"].split(",") if t.strip()]
        prints = [printed[f["path"]] for f in p["files"] if f["path"] in printed]
        p["prints"] = sum(n for n, _last in prints)
        p["last_printed"] = max((last or 0 for _n, last in prints), default=0)
        p["printing"] = None
        out.append(p)
    return out


SETTING_LABELS = [
    ("print_settings_id", "Prozessprofil"),
    ("layer_height", "Schichthöhe", "mm"),
    ("initial_layer_print_height", "Erste Schicht", "mm"),
    ("wall_loops", "Wände"),
    ("top_shell_layers", "Deckschichten oben"),
    ("bottom_shell_layers", "Deckschichten unten"),
    ("sparse_infill_density", "Infill"),
    ("sparse_infill_pattern", "Infill-Muster"),
    ("enable_support", "Stützen"),
    ("support_type", "Stützentyp"),
    ("brim_type", "Brim"),
    ("curr_bed_type", "Druckplatte"),
    ("nozzle_diameter", "Düse", "mm"),
    ("filament_settings_id", "Filamentprofile"),
    ("nozzle_temperature", "Düsentemperatur", "°C"),
    ("hot_plate_temp", "Betttemperatur", "°C"),
    ("outer_wall_speed", "Außenwand", "mm/s"),
    ("seam_position", "Naht"),
    ("ironing_type", "Glätten"),
    # PrusaSlicer und SuperSlicer nennen einige Einstellungen anders
    ("first_layer_height", "Erste Schicht", "mm"),
    ("perimeters", "Wände"),
    ("top_solid_layers", "Deckschichten oben"),
    ("bottom_solid_layers", "Deckschichten unten"),
    ("fill_density", "Infill"),
    ("fill_pattern", "Infill-Muster"),
    ("support_material", "Stützen"),
    ("temperature", "Düsentemperatur", "°C"),
    ("bed_temperature", "Betttemperatur", "°C"),
    ("external_perimeter_speed", "Außenwand", "mm/s"),
]
BOOL_WORDS = {"0": "aus", "1": "an", "false": "aus", "true": "an"}


def _key_settings(settings: dict) -> list[tuple[str, str]]:
    out = []
    for entry in SETTING_LABELS:
        key, label = entry[0], entry[1]
        unit = entry[2] if len(entry) > 2 else ""
        if key not in settings:
            continue
        val = settings[key]
        if isinstance(val, list):
            uniq = list(dict.fromkeys(str(v) for v in val if str(v) != ""))
            val = ", ".join(uniq)
        val = _(BOOL_WORDS[str(val)]) if str(val) in BOOL_WORDS else str(val)
        if val and unit and not val.endswith(unit.strip("°")):
            val = f"{val} {unit}"
        if val:
            out.append((_(label), val))
    return out
