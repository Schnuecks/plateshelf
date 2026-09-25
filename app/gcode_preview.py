"""Wandelt den G-Code einer Platte in eine kompakte Vorschau für den 3D-Viewer.

Ausgabe pro Platte (im Cache unter DATA_DIR/preview/):
  <fid>_<plate>.json   Schichthöhen, Filamente, Linientypen, Tabelle der Bahnen
  <fid>_<plate>.bin.gz Punkte als Uint16 (x, y in 0,01 mm, um 100 mm verschoben)

Nur Extrusionsbewegungen werden übernommen, Leerfahrten fallen weg. Fast
gerade Punktfolgen werden zusammengefasst, damit auch 100-MB-G-Code im
Browser flüssig bleibt.
"""
from __future__ import annotations

import gzip
import io
import json
import math
import threading
import zipfile
from array import array
from pathlib import Path

VERSION = 2
OFFSET_MM = 100.0
SCALE = 100.0  # 0,01 mm

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
}
OTHER = "Sonstiges"

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


def convert(zip_path: Path, plate: int, out_base: Path, progress: dict | None = None) -> dict:
    progress = progress if progress is not None else {}
    name = f"Metadata/plate_{plate}.gcode"
    with zipfile.ZipFile(zip_path) as z:
        info = z.getinfo(name)
        total = info.file_size or 1
        tol = _tolerance(total)
        tol2 = tol * tol

        pts = array("H")          # x, y abwechselnd
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
        feat = -1
        layer = -1
        cur_start = -1  # Punktindex der aktuellen Bahn, -1 = keine offen
        # Letzte zwei Punkte der offenen Bahn (für Vereinfachung), in mm
        ax = ay = bx = by = 0.0
        cur_n = 0
        minx = miny = 1e9
        maxx = maxy = -1e9

        def feature_id(label: str) -> int:
            label = FEATURES.get(label, OTHER)
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
            cur_start = -1
            cur_n = 0

        append = pts.append

        def q(v):
            i = int((v + OFFSET_MM) * SCALE + 0.5)
            return 0 if i < 0 else (65535 if i > 65535 else i)

        def put(px, py):
            append(q(px))
            append(q(py))

        def extrude_to(nx, ny):
            nonlocal cur_start, cur_n, ax, ay, bx, by, minx, miny, maxx, maxy
            if cur_start < 0:
                cur_start = len(pts) // 2
                put(x, y)
                cur_n = 1
                ax, ay, bx, by = x, y, x, y
            if cur_n >= 3:
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
            if nx < minx: minx = nx
            if nx > maxx: maxx = nx
            if ny < miny: miny = ny
            if ny > maxy: maxy = ny

        with z.open(name) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            for n_line, line in enumerate(text):
                if not line:
                    continue
                c0 = line[0]
                if c0 == ";":
                    if line.startswith("; FEATURE:"):
                        close()
                        feat = feature_id(line[10:].strip())
                    elif line.startswith("; CHANGE_LAYER"):
                        close()
                        layer += 1
                        layer_z.append(zpos)
                    elif line.startswith("; Z_HEIGHT:") and layer >= 0:
                        try:
                            layer_z[layer] = float(line[11:].strip().replace(",", "."))
                        except ValueError:
                            pass
                    continue
                if c0 == "G":
                    if ";" in line:
                        line = line.partition(";")[0]
                    parts = line.split()
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
                        moved = nx != x or ny != y
                        if layer >= 0 and de > 0 and moved and code != "G0":
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
                    elif code == "G92":
                        p = _params(parts[1:])
                        if "E" in p:
                            e_abs = p["E"]
                    continue
                if c0 == "M":
                    if line.startswith("M620 S"):
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
                if n_line % 200_000 == 0:
                    progress["value"] = min(0.99, raw.tell() / total) if hasattr(raw, "tell") else 0
            close()

    if not polys:
        raise ValueError("Keine Druckbahnen im G-Code gefunden")

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
    }
    out_base.parent.mkdir(parents=True, exist_ok=True)
    tmp_bin = out_base.with_suffix(".bin.gz.tmp")
    with gzip.open(tmp_bin, "wb", compresslevel=5) as fh:
        fh.write(pts.tobytes())  # little endian auf allen üblichen Plattformen
        if len(pts) % 2:  # Uint32-Block auf 4 Byte ausrichten
            fh.write(b"\x00\x00")
        fh.write(polys.tobytes())
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


def _run_job(key, job, zip_path, plate, out_base):
    try:
        convert(zip_path, plate, out_base, job)
        job["state"] = "ready"
    except Exception as exc:  # noqa: BLE001
        job.update(state="error", error=str(exc))
    finally:
        if job["state"] == "ready":
            with _jobs_lock:
                _jobs.pop(key, None)


def status(zip_path: Path, plate: int, out_base: Path, background: bool = True) -> dict:
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
                threading.Thread(target=_run_job, args=(key, job, zip_path, plate, out_base), daemon=True).start()
            else:
                run_here = True
    if run_here:
        _run_job(key, job, zip_path, plate, out_base)
    return {"state": job["state"], "progress": round(job.get("value", 0), 3), "error": job.get("error")}


def forget(preview_dir: Path, fid: int) -> None:
    """Vergisst laufende oder fehlgeschlagene Aufbereitungen einer gelöschten Datei."""
    prefix = str(preview_dir / f"{fid}_")
    with _jobs_lock:
        for key in [k for k in _jobs if k.startswith(prefix)]:
            _jobs.pop(key, None)
