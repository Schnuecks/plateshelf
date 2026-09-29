# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Wandelt den G-Code einer Platte (aus .gcode.3mf, .gcode oder .bgcode) in eine kompakte Vorschau für den 3D-Viewer.

Ausgabe pro Platte (im Cache unter DATA_DIR/preview/):
  <fid>_<plate>.json   Schichthöhen, Filamente, Linientypen, Wertebereiche
  <fid>_<plate>.bin.gz Punkte als Uint16 (x, y in 0,01 mm, um 100 mm verschoben),
                       die Tabelle der Bahnen (Uint32) und je Punkt vier Messwerte
                       (Uint16) für das Bahnstück, das an diesem Punkt endet:
                       Geschwindigkeit, Volumenstrom, Lüfter, Düsentemperatur;
                       danach Leerfahrten (Uint16: x1, y1, x2, y2, Schicht) und
                       Rückzüge (Uint16: x, y, Schicht)

Nur Extrusionsbewegungen werden übernommen, Leerfahrten fallen weg. Fast
gerade Punktfolgen werden zusammengefasst, damit auch 100-MB-G-Code im
Browser flüssig bleibt.
"""
from __future__ import annotations

import gzip
import io
import itertools
import json
import math
import re
import threading
import zipfile
from array import array
from contextlib import contextmanager
from pathlib import Path

from . import bgcode, gcode_file
from .i18n import UserError

VERSION = 4
OFFSET_MM = 100.0
SCALE = 100.0  # 0,01 mm
# Messwerte pro Punkt: Name, Faktor für die Speicherung als Uint16, Einheit
METRICS = (("speed", 10, "mm/s"), ("flow", 100, "mm³/s"), ("fan", 1, "%"), ("temp", 1, "°C"))
DEFAULT_DIAMETER = 1.75

FEATURES = {
    "Outer wall": "Außenwand",
    "Inner wall": "Innenwand",
    "Overhang wall": "Überhangwand",
    "Sparse infill": "Infill",
    "Internal solid infill": "Massives Infill",
    "Top surface": "Oberfläche",
    "Bottom surface": "Unterseite",
    "Bridge": "Brücke",
    "Gap infill": "Lückenfüllung",
    "Floating vertical shell": "Schwebende Hülle",
    "Support": "Stützen",
    "Support interface": "Stützen-Kontakt",
    "Support transition": "Stützen-Übergang",
    "Prime tower": "Reinigungsturm",
    "Brim": "Brim",
    "Skirt": "Skirt",
    "Ironing": "Glätten",
    # PrusaSlicer, SuperSlicer und OrcaSlicer bei Druckern anderer Hersteller
    "External perimeter": "Außenwand",
    "Perimeter": "Innenwand",
    "Overhang perimeter": "Überhangwand",
    "Internal infill": "Infill",
    "Solid infill": "Massives Infill",
    "Top solid infill": "Oberfläche",
    "Bottom solid infill": "Unterseite",
    "Bridge infill": "Brücke",
    "Internal bridge infill": "Brücke",
    "Gap fill": "Lückenfüllung",
    "Skirt/Brim": "Brim",
    "Support material": "Stützen",
    "Support material interface": "Stützen-Kontakt",
    "Wipe tower": "Reinigungsturm",
    "Internal Bridge": "Brücke",
    # Cura
    "WALL-OUTER": "Außenwand",
    "WALL-INNER": "Innenwand",
    "FILL": "Infill",
    "SKIN": "Oberfläche",
    "SUPPORT": "Stützen",
    "SUPPORT-INTERFACE": "Stützen-Kontakt",
    "SKIRT": "Brim",
    "PRIME-TOWER": "Reinigungsturm",
}
OTHER = "Sonstiges"
# Dieselben Namen ohne Leerzeichen und Groß-/Kleinschreibung, falls ein Kodierer sie verändert
_FEATURES_LOOSE = {k.replace(" ", "").lower(): v for k, v in FEATURES.items()}
# Wörter eines G-Code-Befehls ohne Leerzeichen, wie MeatPack sie speichert: "G1X10Y5E.4"
_WORD = re.compile(r"[A-Z][^A-Z\s]*")
_MWORD = re.compile(r"([A-Z])\s*(-?\d*\.?\d+)")
LAYER_MARKS = ("; CHANGE_LAYER", ";LAYER_CHANGE", ";LAYER:")

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _tolerance(gcode_bytes: int) -> float:
    """Vereinfachungstoleranz in mm, je nach Größe."""
    if gcode_bytes < 15e6:
        return 0.01
    if gcode_bytes < 60e6:
        return 0.03
    return 0.05


def _params(parts):
    out = {}
    for p in parts:
        if len(p) > 1:
            try:
                out[p[0]] = float(p[1:])
            except ValueError:
                pass
    return out


def _mparams(line: str) -> dict[str, float]:
    """Werte eines M-Befehls, mit oder ohne Leerzeichen ("M106 S255", "M106S255")."""
    words = _MWORD.findall(line.partition(";")[0])
    return {k: float(v) for k, v in words[1:]}


def _diameters(z: zipfile.ZipFile) -> dict[int, float]:
    """Filamentdurchmesser je Werkzeug aus den Projekteinstellungen (sonst 1,75 mm)."""
    try:
        settings = json.loads(z.read("Metadata/project_settings.config"))
        values = settings.get("filament_diameter") or []
        values = values if isinstance(values, list) else str(values).split(",")
        return {i: float(v) for i, v in enumerate(values) if float(v) > 0}
    except (KeyError, ValueError, TypeError):
        return {}


def _metrics(feed, de, x, y, nx, ny, p, code, fan, temp, diameter) -> tuple[int, int, int, int]:
    """Messwerte eines Bahnstücks in gespeicherter Form (siehe METRICS)."""
    speed = feed / 60.0
    if code == "G1":
        length = math.hypot(nx - x, ny - y)
    else:  # Bogen: Länge über den Winkel
        cx, cy = x + p.get("I", 0.0), y + p.get("J", 0.0)
        r = math.hypot(x - cx, y - cy)
        a = abs(math.atan2(ny - cy, nx - cx) - math.atan2(y - cy, x - cx))
        length = max(r * a, math.hypot(nx - x, ny - y))
    area = math.pi * diameter * diameter / 4
    flow = de * area / length * speed if length > 1e-6 else 0.0
    return (min(65535, round(speed * 10)), min(65535, round(flow * 100)), fan, temp)


def _ranges(mets: array) -> dict:
    """Typischer Wertebereich je Messwert (1. bis 99. Perzentil), in echten Einheiten."""
    out = {}
    for i, (key, factor, unit) in enumerate(METRICS):
        values = sorted(mets[i::4][::max(1, len(mets) // 4 // 20000)])  # Stichprobe genügt
        if not values:
            out[key] = {"min": 0, "max": 0, "unit": unit, "scale": factor}
            continue
        lo = values[int(len(values) * 0.01)]
        hi = values[min(len(values) - 1, int(len(values) * 0.99))]
        out[key] = {"min": lo / factor, "max": max(hi, lo) / factor, "unit": unit, "scale": factor}
    return out


@contextmanager
def _open_gcode(path: Path, plate: int):
    """G-Code zeilenweise: (Zeilen, Größe, Filamentdurchmesser je Werkzeug, Fortschritt 0..1)."""
    if bgcode.is_bgcode(path.name):
        done = [0.0]
        lines = bgcode.gcode_lines(path, progress=lambda v: done.__setitem__(0, v))
        yield lines, path.stat().st_size, gcode_file.diameters(path), lambda: done[0]
        return
    if gcode_file.is_gcode(path.name):
        size = path.stat().st_size or 1
        with open(path, "rb") as raw:
            yield (io.TextIOWrapper(raw, encoding="utf-8", errors="replace"), size, gcode_file.diameters(path),
                   lambda: raw.tell() / size)
        return
    with zipfile.ZipFile(path) as z:
        name = f"Metadata/plate_{plate}.gcode"
        size = z.getinfo(name).file_size or 1
        with z.open(name) as raw:
            yield (io.TextIOWrapper(raw, encoding="utf-8", errors="replace"), size, _diameters(z),
                   lambda: raw.tell() / size)


def convert(src_path: Path, plate: int, out_base: Path, progress: dict | None = None) -> dict:
    progress = progress if progress is not None else {}
    with _open_gcode(Path(src_path), plate) as (lines, total, diameters, position):
        tol = _tolerance(total)
        tol2 = tol * tol

        pts = array("H")          # x, y abwechselnd
        mets = array("H")         # Geschwindigkeit, Volumenstrom, Lüfter, Temperatur je Punkt
        travels = array("H")      # Leerfahrten: x1, y1, x2, y2, Schicht
        retracts = array("H")     # Rückzüge: x, y, Schicht
        retracted = False         # mehrere Rückzugsbefehle hintereinander zählen einmal
        polys = array("I")        # start, anzahl, schicht, werkzeug, linientyp (je 5 Werte)
        layer_z: list[float] = []
        feature_names: list[str] = []
        feature_idx: dict[str, int] = {}
        tools_used: set[int] = set()

        x = y = zpos = 0.0
        rel_xyz = False
        rel_e = True
        e_abs = 0.0
        tool = 0
        feed = 0.0       # mm/min
        fan = 0          # Prozent
        temp = 0         # °C
        met = (0, 0, 0, 0)  # Messwerte des aktuellen Bahnstücks (gespeicherte Form)
        feat = -1
        layer = -1
        layer_used = False  # Extrusion in der aktuellen Schicht, sonst ist ein Schichtwechsel doppelt
        # Ohne Schichtmarken (etwa MeatPack ohne Kommentare) zählt jede neue Druckhöhe als Schicht
        head = list(itertools.islice(lines, 50_000))
        markers = any(line.startswith(LAYER_MARKS) for line in head)
        lines = itertools.chain(head, lines)
        z_known = False     # Schichthöhe aus einem Kommentar statt aus der Z-Bewegung
        cur_start = -1  # Punktindex der aktuellen Bahn, -1 = keine offen
        # Letzte zwei Punkte der offenen Bahn (für Vereinfachung), in mm
        ax = ay = bx = by = 0.0
        cur_n = 0
        minx = miny = 1e9
        maxx = maxy = -1e9

        def feature_id(label: str) -> int:
            label = FEATURES.get(label) or _FEATURES_LOOSE.get(label.replace(" ", "").lower(), OTHER)
            if label not in feature_idx:
                feature_idx[label] = len(feature_names)
                feature_names.append(label)
            return feature_idx[label]

        def close():
            nonlocal cur_start, cur_n
            if cur_start >= 0 and cur_n >= 2:
                polys.extend((cur_start, cur_n, layer, tool, feat if feat >= 0 else feature_id("")))
            elif cur_start >= 0:
                del pts[cur_start * 2:]
                del mets[cur_start * 4:]
            cur_start = -1
            cur_n = 0

        append = pts.append

        def q(v):
            i = int((v + OFFSET_MM) * SCALE + 0.5)
            return 0 if i < 0 else (65535 if i > 65535 else i)

        def put(px, py):
            append(q(px))
            append(q(py))
            mets.extend(met)

        def extrude_to(nx, ny):
            nonlocal cur_start, cur_n, ax, ay, bx, by, minx, miny, maxx, maxy
            if cur_start < 0:
                cur_start = len(pts) // 2
                put(x, y)  # Startpunkt trägt die Werte des ersten Bahnstücks
                cur_n = 1
                ax, ay, bx, by = x, y, x, y
            # Zusammenfassen nur bei gleichen Messwerten, sonst gingen Wechsel verloren
            if cur_n >= 3 and tuple(mets[-4:]) == met:
                # Liegt der letzte Punkt b fast auf der Linie a -> neu? Dann ersetzen.
                dx, dy = nx - ax, ny - ay
                ll = dx * dx + dy * dy
                if ll > 1e-12:
                    cross = (bx - ax) * dy - (by - ay) * dx
                    dot = (bx - ax) * dx + (by - ay) * dy
                    if cross * cross <= tol2 * ll and 0 <= dot <= ll:
                        pts[-2] = q(nx)  # letzten Punkt ersetzen statt anhängen
                        pts[-1] = q(ny)
                        bx, by = nx, ny
                        return
            put(nx, ny)
            cur_n += 1
            ax, ay, bx, by = bx, by, nx, ny
            if nx < minx: minx = nx  # noqa: E701  (heiße Schleife, bewusst knapp)
            if nx > maxx: maxx = nx  # noqa: E701
            if ny < miny: miny = ny  # noqa: E701
            if ny > maxy: maxy = ny  # noqa: E701

        for n_line, line in enumerate(lines):
            if n_line % 200_000 == 0:
                progress["value"] = min(0.99, position())
            if not line:
                continue
            c0 = line[0]
            if c0 == ";":
                # Bambu/Orca: "; FEATURE:", Prusa/Orca/Cura: ";TYPE:"
                if line.startswith(("; FEATURE:", ";TYPE:")):
                    close()
                    feat = feature_id(line.partition(":")[2].strip())
                elif line.startswith(LAYER_MARKS):
                    close()
                    if layer < 0 or layer_used:  # Orca schreibt zwei Marken je Wechsel
                        layer += 1
                        layer_z.append(zpos)
                        layer_used = z_known = False
                elif line.startswith(("; Z_HEIGHT:", ";Z:")) and layer >= 0:
                    try:
                        layer_z[layer] = float(line.partition(":")[2].strip().replace(",", "."))
                        z_known = True
                    except ValueError:
                        pass
                continue
            if c0 == "G":
                if ";" in line:
                    line = line.partition(";")[0]
                parts = line.split()
                if len(parts[0]) > 3:  # ohne Leerzeichen gespeichert
                    parts = _WORD.findall(line)
                code = parts[0]
                if code in ("G1", "G0", "G2", "G3"):
                    p = {}
                    for tok in parts[1:]:
                        try:
                            p[tok[0]] = float(tok[1:])
                        except ValueError:
                            pass
                    nx = p.get("X", 0.0 if rel_xyz else x)
                    ny = p.get("Y", 0.0 if rel_xyz else y)
                    if rel_xyz:
                        nx, ny = x + nx, y + ny
                        if "Z" in p:
                            zpos += p["Z"]
                    elif "Z" in p:
                        zpos = p["Z"]
                    de = 0.0
                    if "E" in p:
                        if rel_e:
                            de = p["E"]
                        else:
                            de, e_abs = p["E"] - e_abs, p["E"]
                    if "F" in p:
                        feed = p["F"]
                    moved = nx != x or ny != y
                    if layer >= 0 and de < 0 and not retracted:
                        retracts.extend((q(x), q(y), min(layer, 65535)))
                        retracted = True
                    elif de > 0:
                        retracted = False
                    if layer >= 0 and moved and (de <= 0 or code == "G0"):
                        travels.extend((q(x), q(y), q(nx), q(ny), min(layer, 65535)))
                    if not markers and de > 0 and moved and code != "G0" and (
                            layer < 0 or zpos > layer_z[layer] + 1e-6):
                        close()  # G-Code ohne Schichtmarken: neue Höhe, neue Schicht
                        layer += 1
                        layer_z.append(zpos)
                        layer_used, z_known = False, True
                    if layer >= 0 and de > 0 and moved and code != "G0":
                        if not layer_used:
                            layer_used = True
                            if not z_known:  # Cura: Höhe erst mit der ersten Bewegung
                                layer_z[layer] = zpos
                        met = _metrics(feed, de, x, y, nx, ny, p, code, fan, temp,
                                       diameters.get(tool, DEFAULT_DIAMETER))
                        if code == "G1":
                            extrude_to(nx, ny)
                        else:
                            # Bogen in Geradenstücke zerlegen
                            cx, cy = x + p.get("I", 0.0), y + p.get("J", 0.0)
                            r = math.hypot(x - cx, y - cy)
                            a0 = math.atan2(y - cy, x - cx)
                            a1 = math.atan2(ny - cy, nx - cx)
                            if code == "G2":  # im Uhrzeigersinn
                                if a1 >= a0:
                                    a1 -= 2 * math.pi
                            elif a1 <= a0:
                                a1 += 2 * math.pi
                            seg = max(2, min(64, int(abs(a1 - a0) * max(r, 0.1) / 0.3) + 1))
                            for i in range(1, seg):
                                a = a0 + (a1 - a0) * i / seg
                                extrude_to(cx + r * math.cos(a), cy + r * math.sin(a))
                            extrude_to(nx, ny)
                    elif moved or "Z" in p:
                        close()
                    x, y = nx, ny
                elif code == "G90":
                    rel_xyz = False
                elif code == "G91":
                    rel_xyz = True
                elif code == "G10" and layer >= 0 and not retracted:  # Firmware-Rückzug
                    retracts.extend((q(x), q(y), min(layer, 65535)))
                    retracted = True
                elif code == "G92":
                    p = _params(parts[1:])
                    if "E" in p:
                        e_abs = p["E"]
                continue
            if c0 == "M":
                if line.startswith(("M106", "M107")):
                    mp = _mparams(line)
                    # Bauteillüfter: ohne P, P0 oder P1 (Bambu); P2/P3 sind Zusatz- und Kammerlüfter
                    if mp.get("P", 1) in (0, 1):
                        fan = 0 if line.startswith("M107") else round(max(0.0, min(255.0, mp.get("S", 255))) / 2.55)
                elif line.startswith(("M104", "M109")):
                    mp = _mparams(line)
                    if "S" in mp and mp["S"] > 0:
                        temp = round(mp["S"])
                elif line.startswith("M620 S"):
                    # Bambu wählt das AMS-Filament mit "M620 S<n>A"
                    num = line[6:].split("A", 1)[0].strip()
                    if num.isdigit() and int(num) < 32:
                        close()
                        tool = int(num)
                        tools_used.add(tool)
                elif line.startswith("M83"):
                    rel_e = True
                elif line.startswith("M82"):
                    rel_e = False
                continue
            if c0 == "T":
                try:
                    t = int(line[1:].split()[0])
                except (ValueError, IndexError):
                    continue
                if t < 32:
                    close()
                    tool = t
                    tools_used.add(t)
        close()

    if not polys:
        raise UserError("Keine Druckbahnen im G-Code gefunden")

    meta = {
        "version": VERSION,
        "offset": OFFSET_MM,
        "scale": SCALE,
        "tolerance": tol,
        "layers": layer_z,
        "features": feature_names,
        "tools": sorted(tools_used),
        "bbox": [minx, miny, maxx, maxy, max(layer_z) if layer_z else 0],
        "points": len(pts) // 2,
        "polys": len(polys) // 5,
        "metrics": _ranges(mets),
        "travels": len(travels) // 5,
        "retracts": len(retracts) // 3,
    }
    out_base.parent.mkdir(parents=True, exist_ok=True)
    tmp_bin = out_base.with_suffix(".bin.gz.tmp")
    with gzip.open(tmp_bin, "wb", compresslevel=5) as fh:
        fh.write(pts.tobytes())  # little endian auf allen üblichen Plattformen
        if len(pts) % 2:  # Uint32-Block auf 4 Byte ausrichten
            fh.write(b"\x00\x00")
        fh.write(polys.tobytes())
        fh.write(mets.tobytes())
        fh.write(travels.tobytes())
        fh.write(retracts.tobytes())
    tmp_json = out_base.with_suffix(".json.tmp")
    tmp_json.write_text(json.dumps(meta, separators=(",", ":")))
    tmp_bin.replace(out_base.with_suffix(".bin.gz"))
    tmp_json.replace(out_base.with_suffix(".json"))  # JSON zuletzt: gilt als "fertig"-Marke
    return meta


def _is_current(out_base: Path) -> bool:
    meta_path = out_base.with_suffix(".json")
    if not meta_path.exists():
        return False
    try:
        with open(meta_path, "rb") as fh:
            head = fh.read(40).decode("ascii", "ignore")
        return f'"version":{VERSION}' in head
    except OSError:
        return False


def _run_job(key, job, src_path, plate, out_base):
    try:
        convert(src_path, plate, out_base, job)
        job["state"] = "ready"
    except Exception as exc:  # noqa: BLE001
        job.update(state="error", error=str(exc))
    finally:
        if job["state"] == "ready":
            with _jobs_lock:
                _jobs.pop(key, None)


def status(src_path: Path, plate: int, out_base: Path, background: bool = True) -> dict:
    """Liefert den Stand und startet die Aufbereitung bei Bedarf.

    background=False blockiert, bis die Vorschau fertig ist (für die Vorberechnung).
    """
    if _is_current(out_base):
        return {"state": "ready"}
    key = str(out_base)
    run_here = False
    with _jobs_lock:
        job = _jobs.get(key)
        if job is None:
            job = {"state": "building", "value": 0.0}
            _jobs[key] = job
            if background:
                threading.Thread(target=_run_job, args=(key, job, src_path, plate, out_base), daemon=True).start()
            else:
                run_here = True
    if run_here:
        _run_job(key, job, src_path, plate, out_base)
    return {"state": job["state"], "progress": round(job.get("value", 0), 3), "error": job.get("error")}


def forget(preview_dir: Path, fid: int) -> None:
    """Vergisst laufende oder fehlgeschlagene Aufbereitungen einer gelöschten Datei."""
    prefix = str(preview_dir / f"{fid}_")
    with _jobs_lock:
        for key in [k for k in _jobs if k.startswith(prefix)]:
            _jobs.pop(key, None)
