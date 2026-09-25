from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import filecmp
import shutil
import uuid
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import gcode_preview, printer_import, store

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")

BASE = Path(__file__).parent
def _app_version() -> str:
    """Version aus dem Image: bei Releases der Tag (v0.2.0 -> 0.2.0), sonst dev-<Commit>."""
    v = os.environ.get("APP_VERSION", "").strip() or "dev"
    if v.startswith("dev-"):
        return v[:11]  # dev- plus 7 Zeichen der Commit-Kennung, wie auf GitHub
    return v.removeprefix("v")


APP_VERSION = _app_version()
APP_AUTHOR = "Schnuecks"
APP_REPO = "https://github.com/schnuecks/druckarchiv"
STATUSES = {"": "Ohne Status", "geplant": "Geplant", "gedruckt": "Gedruckt", "fehlgeschlagen": "Fehlgeschlagen"}

@asynccontextmanager
async def lifespan(_app):
    store.init_db()
    interval = int(os.environ.get("SCAN_INTERVAL", "120"))
    threading.Thread(target=store.scan_loop, args=(interval,), daemon=True).start()
    if store.PREBUILD:
        threading.Thread(target=store.prebuild_loop, daemon=True).start()
    printer_import.init()
    threading.Thread(target=printer_import.sync_loop, daemon=True).start()
    yield


app = FastAPI(title="Druckarchiv", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


# --- Formatierung (deutsch) -------------------------------------------------

def fmt_duration(sec) -> str:
    if not sec:
        return "–"
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} d {h} h"
    if h:
        return f"{h} h {m:02d} min"
    return f"{max(m, 1)} min"


def fmt_num(value, digits=1, unit="") -> str:
    if value is None:
        return "–"
    out = f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{out} {unit}" if unit else out


def fmt_money(value) -> str:
    if value is None:
        return "–"
    if 0 < value < 0.005:
        return "< 0,01 €"
    return fmt_num(value, 2) + " €"


def fmt_date(ts) -> str:
    return time.strftime("%d.%m.%Y", time.localtime(ts)) if ts else "–"


def strand(filaments: list[dict]) -> str:
    """CSS-Verlauf mit harten Kanten: Filamentfarben anteilig nach Gewicht."""
    total = sum(max(f.get("used_g") or 0, 0) for f in filaments)
    if not filaments:
        return "var(--line)"
    if total <= 0:
        total = len(filaments)
        filaments = [{**f, "used_g": 1} for f in filaments]
    stops, pos = [], 0.0
    for f in filaments:
        share = (f.get("used_g") or 0) / total * 100
        stops.append(f"{f['color']} {pos:.2f}% {pos + share:.2f}%")
        pos += share
    return f"linear-gradient(90deg, {', '.join(stops)})"


templates.env.filters.update(duration=fmt_duration, num=fmt_num, money=fmt_money, date=fmt_date, strand=strand)
templates.env.globals["STATUSES"] = STATUSES
templates.env.globals.update(APP_VERSION=APP_VERSION, APP_AUTHOR=APP_AUTHOR, APP_REPO=APP_REPO)


def _asset_version() -> str:
    """Prüfsumme über CSS/JS: ändert sich bei jedem Update, damit Browser neu laden."""
    import hashlib
    h = hashlib.sha1()
    for name in ("style.css", "app.js", "viewer.js"):
        path = BASE / "static" / name
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:10]


templates.env.globals["ASSET_V"] = _asset_version()


# --- Datenaufbereitung ------------------------------------------------------

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


def load_projects(con) -> list[dict]:
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
        p["plates_total"] = len(plates) + len(p["extra_plates"])
        p["tag_list"] = [t.strip() for t in p["tags"].split(",") if t.strip()]
        out.append(p)
    return out


# --- Routen -----------------------------------------------------------------

@app.get("/")
def index(request: Request, q: str = "", material: str = "", printer: str = "", status: str = "", tag: str = "",
          sort: str = "neu"):
    with store.connect() as con:
        projects = load_projects(con)
    all_materials = sorted({m for p in projects for m in p["materials"]})
    all_printers = sorted({m for p in projects for m in p["printers"]})
    all_tags = sorted({t for p in projects for t in p["tag_list"]}, key=str.lower)

    ql = q.lower().strip()
    def match(p):
        if ql:
            hay = " ".join([p["name"], p["notes"], p["tags"], *(f["path"] for f in p["files"]),
                            *(o for x in p["plates"] for o in x["objects"])]).lower()
            if ql not in hay:
                return False
        return ((not material or material in p["materials"]) and (not printer or printer in p["printers"])
                and (status == "" or p["status"] == ("" if status == "ohne" else status))
                and (not tag or tag in p["tag_list"]))

    shown = [p for p in projects if match(p)]
    keys = {
        "neu": (lambda p: -p["latest"]),
        "name": (lambda p: p["name"].lower()),
        "dauer": (lambda p: -p["time_s"]),
        "gewicht": (lambda p: -p["weight_g"]),
    }
    shown.sort(key=keys.get(sort, keys["neu"]))
    totals = {
        "count": len(shown),
        "time_s": sum(p["time_s"] for p in shown),
        "weight_g": sum(p["weight_g"] for p in shown),
    }
    return templates.TemplateResponse(request, "index.html", {
        "projects": shown, "totals": totals, "q": q, "material": material, "printer": printer,
        "status": status, "tag": tag, "sort": sort, "all_materials": all_materials,
        "all_printers": all_printers, "all_tags": all_tags, "scan": store.last_scan,
        "psync": printer_import.state,
    })


@app.get("/p/{pid}")
def project(request: Request, pid: int):
    with store.connect() as con:
        projects = {p["id"]: p for p in load_projects(con)}
        if pid not in projects:
            raise HTTPException(404, "Projekt nicht gefunden")
        p = projects[pid]
        rows = con.execute("SELECT * FROM files WHERE project_id = ? AND missing = 0", (pid,)).fetchall()
        file_rows = {r["id"]: dict(r) for r in sorted(rows, key=lambda r: natural_key(r["path"]))}
    origins = printer_import.origin_for([f["path"] for f in file_rows.values()])
    designer = None
    for f in file_rows.values():
        f["origin"] = origins.get(f["path"])
        model = json.loads(f.pop("model_json") or "{}")
        f["v"] = file_stamp(f)
        model["photo_urls"] = [f"/photo/{f['id']}/{n}?v={f['v']}" for n in model.pop("photos", [])]
        f["model"] = model
        if designer is None and (model.get("title") or model["photo_urls"]):
            designer = model
        settings = json.loads(f.pop("settings_json") or "{}")
        f["key_settings"] = _key_settings(settings)
        f["plates"] = [x for x in p["plates"] if x["file_id"] == f["id"]]
        f["extra_plates"] = [x for x in p["extra_plates"] if x["file_id"] == f["id"]]
        f["all_plates"] = sorted(f["plates"] + f["extra_plates"], key=lambda x: x["idx"])
    # Plattenleiste über alle Dateien des Projekts, in natürlicher Reihenfolge
    strip = []
    for f in file_rows.values():
        source = (f["model"].get("title") or "").strip().lower()
        for pl in f["all_plates"]:
            strip.append({**pl, "fid": f["id"], "v": f["v"], "file_name": f["path"].split("/")[-1],
                          "_sort": (source, pl["idx"], natural_key(f["path"]))})
    strip.sort(key=lambda x: x["_sort"])
    others = sorted((o for o in projects.values() if o["id"] != pid), key=lambda o: o["name"].lower())
    return templates.TemplateResponse(request, "project.html", {
        "p": p, "files": list(file_rows.values()), "others": others, "designer": designer,
        "writable": _library_writable(), "strip": strip,
    })


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
        val = BOOL_WORDS.get(str(val), str(val))
        if val and unit and not val.endswith(unit.strip("°")):
            val = f"{val} {unit}"
        if val:
            out.append((label, val))
    return out


@app.post("/p/{pid}")
def update_project(pid: int, name: str = Form(...), notes: str = Form(""), tags: str = Form(""),
                   source_url: str = Form(""), status: str = Form("")):
    if status not in STATUSES:
        raise HTTPException(400, "Unbekannter Status")
    tags = ", ".join(dict.fromkeys(t.strip() for t in tags.split(",") if t.strip()))
    with store.connect() as con:
        con.execute(
            "UPDATE projects SET name=?, notes=?, tags=?, source_url=?, status=?, updated=? WHERE id=?",
            (name.strip() or "Ohne Namen", notes, tags, source_url.strip(), status, time.time(), pid),
        )
    return RedirectResponse(f"/p/{pid}?gespeichert=1", status_code=303)


@app.post("/f/{fid}/move")
def move_file(fid: int, target: str = Form(...), new_name: str = Form("")):
    with store.connect() as con:
        row = con.execute("SELECT project_id FROM files WHERE id = ?", (fid,)).fetchone()
        if not row:
            raise HTTPException(404, "Datei nicht gefunden")
        if target == "neu":
            now = time.time()
            name = new_name.strip() or "Neues Projekt"
            target_id = con.execute(
                "INSERT INTO projects (group_key, name, created, updated) VALUES (?, ?, ?, ?)",
                (f"manual:{now}", name, now, now),
            ).lastrowid
        else:
            target_id = int(target)
            if not con.execute("SELECT 1 FROM projects WHERE id = ?", (target_id,)).fetchone():
                raise HTTPException(404, "Zielprojekt nicht gefunden")
        con.execute("UPDATE files SET project_id = ?, pinned = 1 WHERE id = ?", (target_id, fid))
    return RedirectResponse(f"/p/{target_id}", status_code=303)


# --- Einstellungen -----------------------------------------------------------

@app.get("/einstellungen")
def settings_form(request: Request):
    with store.connect() as con:
        settings = store.get_settings(con)
        printers = [r[0] for r in con.execute(
            "SELECT DISTINCT printer FROM files WHERE missing = 0 AND printer IS NOT NULL AND printer != '' "
            "ORDER BY printer")]
    rows = [{"name": pr, "watts": settings.get(f"leistung:{pr}")} for pr in printers]
    return templates.TemplateResponse(request, "settings.html", {
        "s": settings, "printers": rows, "defaults": store.SETTING_DEFAULTS,
    })


def _number(value: str, low: float, high: float) -> float | None:
    value = (value or "").strip().replace(",", ".")
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        raise HTTPException(400, f"„{value}“ ist keine Zahl")
    if not low <= number <= high:
        raise HTTPException(400, f"{number} liegt außerhalb von {low} bis {high}")
    return number


@app.post("/einstellungen")
async def settings_save(request: Request):
    form = await request.form()
    def or_default(value, key):
        # 0 ist ein gültiger Strompreis (z. B. eigene Solaranlage), nur ein leeres Feld nimmt den Standard
        return store.SETTING_DEFAULTS[key] if value is None else value

    values = {
        "strompreis": or_default(_number(form.get("strompreis"), 0, 5), "strompreis"),
        "leistung_standard": or_default(_number(form.get("leistung_standard"), 1, 5000), "leistung_standard"),
    }
    for key, value in form.items():
        if key.startswith("leistung:"):
            values[key] = _number(value, 1, 5000)  # leer = Standardleistung verwenden
    store.save_settings(values)
    return RedirectResponse("/einstellungen?gespeichert=1", status_code=303)


# --- Upload -----------------------------------------------------------------

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "1024"))
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _library_writable() -> bool:
    return os.access(store.LIBRARY_DIR, os.W_OK)


def _clean_name(name: str) -> str:
    name = _BAD_CHARS.sub("_", Path(name or "").name).strip(" .")
    return name[:180]


def _library_folders() -> list[str]:
    try:
        return sorted((d.name for d in store.LIBRARY_DIR.iterdir()
                       if d.is_dir() and not d.name.startswith((".", "@"))), key=str.lower)
    except OSError:
        return []


def _unique_path(folder: Path, name: str) -> Path:
    """Nie überschreiben: bei Namensgleichheit ' (2)', ' (3)' … anhängen."""
    target = folder / name
    if not target.exists():
        return target
    stem, ext = (name[:-10], name[-10:]) if name.lower().endswith(".gcode.3mf") else (name[:-4], name[-4:])
    n = 2
    while (folder / f"{stem} ({n}){ext}").exists():
        n += 1
    return folder / f"{stem} ({n}){ext}"


def _save_upload(upload: UploadFile, folder: Path) -> tuple[str | None, str | None]:
    """Speichert eine Datei. Rückgabe: (relativer Pfad, Fehlermeldung)."""
    name = _clean_name(upload.filename)
    if not name.lower().endswith(".3mf"):
        return None, f"{upload.filename or 'Datei'}: keine .3mf-Datei"
    tmp = folder / f".upload-{uuid.uuid4().hex}.part"
    limit = MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with open(tmp, "wb") as out:
            while chunk := upload.file.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise ValueError(f"größer als {MAX_UPLOAD_MB} MB")
                out.write(chunk)
        if not zipfile.is_zipfile(tmp):
            raise ValueError("keine gültige 3MF-Datei")
        with zipfile.ZipFile(tmp) as z:
            if not any(n.startswith(("Metadata/", "3D/")) for n in z.namelist()):
                raise ValueError("keine gültige 3MF-Datei")
        existing = folder / name
        if existing.exists() and filecmp.cmp(tmp, existing, shallow=False):
            raise ValueError("ist bereits unverändert in der Bibliothek")
        target = _unique_path(folder, name)
        os.replace(tmp, target)
        return target.relative_to(store.LIBRARY_DIR).as_posix(), None
    except Exception as exc:
        return None, f"{name}: {exc}"
    finally:
        if tmp.exists():
            tmp.unlink()


# --- Löschen (in den Papierkorb) --------------------------------------------

TRASH_DIR = ".papierkorb"


def _trash_file(rel_path: str, stamp: str) -> None:
    """Verschiebt eine Datei nach .papierkorb/<Zeitstempel>/<ursprünglicher Pfad>."""
    lib = store.LIBRARY_DIR.resolve()
    src = (lib / rel_path).resolve()
    if not src.is_relative_to(lib):
        raise HTTPException(400, "Ungültiger Pfad")
    if src.exists():
        dst = lib / TRASH_DIR / stamp / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
    # Leer gewordene Projektordner aufräumen
    parent = src.parent
    while parent != lib and parent.is_relative_to(lib):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def _forget_file(con, fid: int) -> None:
    con.execute("DELETE FROM plates WHERE file_id = ?", (fid,))
    con.execute("DELETE FROM files WHERE id = ?", (fid,))
    shutil.rmtree(store.THUMB_DIR / str(fid), ignore_errors=True)
    for old in (store.DATA_DIR / "preview").glob(f"{fid}_*"):
        old.unlink(missing_ok=True)
    gcode_preview.forget(store.DATA_DIR / "preview", fid)


@app.post("/f/{fid}/delete")
def delete_file(fid: int):
    if not _library_writable():
        raise HTTPException(403, "Bibliothek ist schreibgeschützt eingebunden")
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    with store.connect() as con:
        row = con.execute("SELECT path, project_id FROM files WHERE id = ?", (fid,)).fetchone()
        if not row:
            raise HTTPException(404, "Datei nicht gefunden")
        _trash_file(row["path"], stamp)
        _forget_file(con, fid)
        left = con.execute("SELECT COUNT(*) FROM files WHERE project_id = ? AND missing = 0",
                           (row["project_id"],)).fetchone()[0]
        if not left:
            con.execute("DELETE FROM files WHERE project_id = ?", (row["project_id"],))
            con.execute("DELETE FROM projects WHERE id = ?", (row["project_id"],))
            return RedirectResponse("/?geloescht=datei", status_code=303)
    return RedirectResponse(f"/p/{row['project_id']}?geloescht=datei", status_code=303)


@app.post("/p/{pid}/delete")
def delete_project(pid: int):
    if not _library_writable():
        raise HTTPException(403, "Bibliothek ist schreibgeschützt eingebunden")
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    with store.connect() as con:
        if not con.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
            raise HTTPException(404, "Projekt nicht gefunden")
        for r in con.execute("SELECT id, path, missing FROM files WHERE project_id = ?", (pid,)).fetchall():
            if not r["missing"]:
                _trash_file(r["path"], stamp)
            _forget_file(con, r["id"])
        con.execute("DELETE FROM projects WHERE id = ?", (pid,))
    return RedirectResponse("/?geloescht=projekt", status_code=303)


@app.get("/upload")
def upload_form(request: Request):
    return templates.TemplateResponse(request, "upload.html", {
        "writable": _library_writable(), "folders": _library_folders(),
        "errors": [], "max_mb": MAX_UPLOAD_MB,
    })


@app.post("/upload")
async def upload(request: Request, files: list[UploadFile] = File(...), folder: str = Form("")):
    if not _library_writable():
        raise HTTPException(403, "Bibliothek ist schreibgeschützt eingebunden")
    folder_name = _clean_name(folder)
    target_dir = store.LIBRARY_DIR / folder_name if folder_name else store.LIBRARY_DIR
    await run_in_threadpool(target_dir.mkdir, parents=True, exist_ok=True)

    saved, errors = [], []
    for f in files:
        rel, err = await run_in_threadpool(_save_upload, f, target_dir)
        if rel:
            saved.append(rel)
        if err:
            errors.append(err)

    if saved:
        await run_in_threadpool(store.scan, False, True)
    if errors or not saved:
        return templates.TemplateResponse(request, "upload.html", {
            "writable": True, "folders": _library_folders(), "errors": errors,
            "saved": len(saved), "max_mb": MAX_UPLOAD_MB,
        }, status_code=400 if not saved else 200)
    ids = store.project_ids_for(saved)
    if len(ids) == 1:
        return RedirectResponse(f"/p/{ids[0]}?hochgeladen={len(saved)}", status_code=303)
    return RedirectResponse(f"/?hochgeladen={len(saved)}", status_code=303)


@app.post("/printer-sync")
def printer_sync():
    printer_import.request_sync()
    return RedirectResponse("/?drucker=1", status_code=303)


@app.post("/rescan")
def rescan(force: int = Form(0)):
    threading.Thread(target=store.scan, kwargs={"force": bool(force)}, daemon=True).start()
    return RedirectResponse("/", status_code=303)


@app.get("/thumb/{fid}/{kind}/{idx}")
def thumb(fid: int, kind: str, idx: int):
    if kind not in ("plate", "top"):
        raise HTTPException(404)
    path = store.THUMB_DIR / str(fid) / f"{kind}_{idx}.png"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=2592000"})


@app.get("/photo/{fid}/{name}")
def photo(fid: int, name: str):
    if not re.fullmatch(r"photo_\d+\.(webp|jpe?g|png)", name):
        raise HTTPException(404)
    path = store.THUMB_DIR / str(fid) / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "max-age=86400"})


@app.get("/f/{fid}/download")
def download(fid: int):
    with store.connect() as con:
        row = con.execute("SELECT path FROM files WHERE id = ?", (fid,)).fetchone()
    if not row:
        raise HTTPException(404)
    path = (store.LIBRARY_DIR / row["path"]).resolve()
    if not path.is_relative_to(store.LIBRARY_DIR.resolve()) or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


# --- 3D-Vorschau -------------------------------------------------------------

def _plate_for_view(fid: int, idx: int):
    with store.connect() as con:
        f = con.execute("SELECT id, path, project_id, nozzle FROM files WHERE id = ? AND missing = 0", (fid,)).fetchone()
        pl = con.execute("SELECT * FROM plates WHERE file_id = ? AND idx = ? AND sliced = 1", (fid, idx)).fetchone()
        if not f or not pl:
            raise HTTPException(404, "Platte nicht gefunden oder nicht gesliced")
        proj = con.execute("SELECT id, name FROM projects WHERE id = ?", (f["project_id"],)).fetchone()
    path = (store.LIBRARY_DIR / f["path"]).resolve()
    if not path.is_relative_to(store.LIBRARY_DIR.resolve()) or not path.exists():
        raise HTTPException(404, "Datei nicht gefunden")
    return f, _plate_dict(pl), proj, path


def _preview_base(fid: int, idx: int) -> Path:
    return store.DATA_DIR / "preview" / f"{fid}_{idx}"


@app.get("/view/{fid}/{idx}")
def viewer(request: Request, fid: int, idx: int):
    f, pl, proj, _ = _plate_for_view(fid, idx)
    siblings = []
    if proj:
        with store.connect() as con:
            rows = con.execute(
                """SELECT f.id AS fid, f.path, p.idx FROM plates p JOIN files f ON f.id = p.file_id
                   WHERE f.project_id = ? AND f.missing = 0 AND p.sliced = 1""", (proj["id"],)).fetchall()
        siblings = sorted((dict(r) for r in rows), key=lambda r: (natural_key(r["path"]), r["idx"]))
    pos = next((i for i, r in enumerate(siblings) if r["fid"] == fid and r["idx"] == idx), 0)
    nav = {
        "pos": pos + 1, "total": len(siblings),
        "prev": siblings[pos - 1] if pos > 0 else None,
        "next": siblings[pos + 1] if pos + 1 < len(siblings) else None,
    }
    return templates.TemplateResponse(request, "viewer.html", {
        "nav": nav,
        "file": dict(f), "plate": pl, "project": dict(proj) if proj else None,
        "filaments": [{"slot": x["slot"], "color": x["color"], "type": x["type"], "name": x.get("name", "")}
                      for x in pl["filaments"]],
    })


@app.get("/api/preview/{fid}/{idx}")
def preview_status(fid: int, idx: int):
    _, _, _, path = _plate_for_view(fid, idx)
    return gcode_preview.status(path, idx, _preview_base(fid, idx))


@app.get("/api/preview/{fid}/{idx}/meta")
def preview_meta(fid: int, idx: int):
    p = _preview_base(fid, idx).with_suffix(".json")
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/json", headers={"Cache-Control": "no-cache"})


@app.get("/api/preview/{fid}/{idx}/points")
def preview_points(fid: int, idx: int):
    p = _preview_base(fid, idx).with_suffix(".bin.gz")
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/octet-stream",
                        headers={"Content-Encoding": "gzip", "Cache-Control": "no-cache"})


@app.get("/healthz")
def healthz():
    return {"ok": True, "scan": store.last_scan, "printers": printer_import.state}


def fmt_ago(ts) -> str:
    if not ts:
        return "noch nie"
    mins = int((time.time() - ts) // 60)
    if mins < 1:
        return "gerade eben"
    if mins < 60:
        return f"vor {mins} min"
    return f"vor {mins // 60} h"


templates.env.filters["ago"] = fmt_ago
