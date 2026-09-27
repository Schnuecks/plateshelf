# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

import filecmp
import uuid
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, gcode_file, gcode_preview, i18n, printer_import, store, trash
from .i18n import fmt_date, fmt_money, fmt_num, fmt_printer_date, gettext as _, ngettext

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
APP_NAME = "PlateShelf"
APP_REPO = "https://github.com/schnuecks/plateshelf"
APP_LICENSE = "AGPL-3.0"
# Beschriftungen werden beim Anzeigen übersetzt
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
    threading.Thread(target=trash.purge_loop, daemon=True).start()
    yield


app = FastAPI(title="PlateShelf", docs_url=None, redoc_url=None, lifespan=lifespan)


# Deutsche Adressen bis Version 0.7.0, dauerhaft auf die englischen umgeleitet
LEGACY_PATHS = [(re.compile(old), new) for old, new in (
    (r"/einstellungen", "/settings"),
    (r"/einstellungen/drucker", "/settings/printers"),
    (r"/papierkorb", "/trash"),
    (r"/papierkorb/leeren", "/trash/empty"),
    (r"/papierkorb/(\d+)/wiederherstellen", r"/trash/\1/restore"),
    (r"/papierkorb/(\d+)/loeschen", r"/trash/\1/delete"),
    (r"/darstellung", "/appearance"),
)]


@app.middleware("http")
async def language_middleware(request: Request, call_next):
    for pattern, target in LEGACY_PATHS:
        if m := pattern.fullmatch(request.url.path):
            url = m.expand(target) + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(url, status_code=308)  # 308: Formulare bleiben Formulare
    i18n.set_language(i18n.pick_language(request.cookies.get("sprache"), request.headers.get("accept-language")))
    if request.method not in SAFE_METHODS and not same_origin(request):
        logging.getLogger("plateshelf").warning("Anfrage von fremder Seite abgelehnt: %s %s (Origin %s)",
                                                 request.method, request.url.path,
                                                 request.headers.get("origin") or request.headers.get("referer"))
        return PlainTextResponse(_("Anfrage von einer fremden Seite abgelehnt."), status_code=403)
    if auth.enabled() and not is_public(request.url.path) and not signed_in(request):
        if request.method == "GET" and not request.url.path.startswith("/api/"):
            return RedirectResponse(f"/login?next={quote(current_url(request))}", status_code=303)
        # Skripte und Dashboards: Anmeldung per HTTP Basic Auth
        return PlainTextResponse(_("Anmeldung erforderlich."), status_code=401,
                                 headers={"WWW-Authenticate": f'Basic realm="{APP_NAME}", charset="UTF-8"'})
    return await call_next(request)


# --- Optionale Anmeldung (siehe app/auth.py) --------------------------------
PUBLIC_PREFIXES = ("/static/",)
PUBLIC_PATHS = {"/login", "/healthz", "/appearance"}


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def signed_in(request: Request) -> bool:
    return (auth.session_valid(request.cookies.get(auth.COOKIE))
            or auth.basic_auth_valid(request.headers.get("authorization")))


def _local_target(target: str) -> str:
    """Nur Pfade dieser Seite als Ziel nach der Anmeldung, keine fremden Adressen."""
    return target if target.startswith("/") and not target.startswith("//") and "\\" not in target else "/"


# --- Schutz vor fremden Seiten (CSRF) ----------------------------------------
# Ohne Anmeldung (Standard) und selbst mit gültiger Sitzung könnte jede Webseite, die
# man im selben Netz öffnet, dem Browser ein Formular an PlateShelf unterschieben
# (z. B. „Papierkorb leeren“). Browser schicken bei solchen Anfragen immer Origin oder
# Referer mit; stimmt deren Host nicht mit dem angefragten überein, wird abgelehnt.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TRUSTED_ORIGINS = {o.strip().rstrip("/").lower() for o in os.environ.get("TRUSTED_ORIGINS", "").split(",")
                   if o.strip()}


def same_origin(request: Request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return True  # kein Browser-Formular, z. B. curl oder ein Skript im Heimnetz
    if source == "null":
        return False  # Sandbox, Datei oder Weiterleitung von fremder Seite
    parts = urlsplit(source)
    if not parts.netloc:
        return False
    if f"{parts.scheme}://{parts.netloc}".lower() in TRUSTED_ORIGINS:
        return True
    # Hinter einem Reverse-Proxy zählt der Host, den der Proxy weiterreicht
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0]
    return parts.netloc.lower() == host.strip().lower()


def current_theme(request: Request) -> str:
    theme = request.cookies.get("design")
    return theme if theme in i18n.THEMES else i18n.default_theme()
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


# --- Formatierung (sprachabhängig, siehe i18n) -----------------------------

def fmt_duration(sec) -> str:
    if not sec:
        return "–"
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return _("{d} d {h} h", d=d, h=h)
    if h:
        return f"{h} h {m:02d} min"
    return f"{max(m, 1)} min"


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


def fmt_size(n) -> str:
    if not n:
        return "0 MB"
    return fmt_num(n / 1024 / 1024, 1, "MB") if n >= 100 * 1024 else fmt_num(n / 1024, 0, "KB")


templates.env.filters.update(duration=fmt_duration, num=fmt_num, money=fmt_money, date=fmt_date, strand=strand,
                             printer_date=fmt_printer_date,
                             size=fmt_size)
templates.env.globals["STATUSES"] = STATUSES
templates.env.globals.update(_=_, ngettext=ngettext, lang=i18n.current, theme=current_theme,
                             LANGUAGES=i18n.LANGUAGES)

# Texte, die das JavaScript braucht
JS_TEXTS = [
    "{pct} % übertragen", "Wird eingelesen …", "Übertragung fehlgeschlagen. Bitte erneut versuchen.",
    "G-Code wird aufbereitet: {pct} %", "Druckbahnen werden geladen", "Darstellung wird abgestimmt",
    "Die Vorschau konnte nicht erstellt werden: {error}", "Vorschau nicht verfügbar", "Aufbereitung fehlgeschlagen",
    "Abspielen", "Anhalten", "Geometrie wird aufgebaut: {pct} %", "Vollbild", "Vollbild beenden", "Platte {n}", "Geschwindigkeit", "Volumenstrom", "Lüfter", "Düsentemperatur", "{n} von {total} ({mm} mm)", "Werkzeug {n}",
    "Außenwand", "Innenwand", "Überhangwand", "Infill", "Massives Infill", "Oberfläche", "Unterseite", "Brücke",
    "Lückenfüllung", "Schwebende Hülle", "Stützen", "Stützen-Kontakt", "Stützen-Übergang", "Reinigungsturm",
    "Brim", "Skirt", "Glätten", "Sonstiges",
]


def js_texts() -> dict[str, str]:
    return {t: _(t) for t in JS_TEXTS}


templates.env.globals["js_texts"] = js_texts
templates.env.globals.update(APP_NAME=APP_NAME, APP_VERSION=APP_VERSION, APP_AUTHOR=APP_AUTHOR, APP_REPO=APP_REPO,
                             APP_LICENSE=APP_LICENSE)


def _asset_version() -> str:
    """Prüfsumme über CSS/JS: ändert sich bei jedem Update, damit Browser neu laden."""
    import hashlib
    h = hashlib.sha1()
    for name in ("style.css", "app.js", "viewer.js", "geometry.js", "geometry-worker.js"):
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
            raise HTTPException(404, _("Projekt nicht gefunden"))
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


@app.post("/p/{pid}")
def update_project(pid: int, name: str = Form(...), notes: str = Form(""), tags: str = Form(""),
                   source_url: str = Form(""), status: str = Form("")):
    if status not in STATUSES:
        raise HTTPException(400, _("Unbekannter Status"))
    tags = ", ".join(dict.fromkeys(t.strip() for t in tags.split(",") if t.strip()))
    with store.connect() as con:
        con.execute(
            "UPDATE projects SET name=?, notes=?, tags=?, source_url=?, status=?, updated=? WHERE id=?",
            (name.strip() or _("Ohne Namen"), notes, tags, source_url.strip(), status, time.time(), pid),
        )
    return RedirectResponse(f"/p/{pid}?saved=1", status_code=303)


@app.post("/f/{fid}/move")
def move_file(fid: int, target: str = Form(...), new_name: str = Form("")):
    with store.connect() as con:
        row = con.execute("SELECT project_id FROM files WHERE id = ?", (fid,)).fetchone()
        if not row:
            raise HTTPException(404, _("Datei nicht gefunden"))
        if target == "neu":
            now = time.time()
            name = new_name.strip() or _("Neues Projekt")
            target_id = con.execute(
                "INSERT INTO projects (group_key, name, created, updated) VALUES (?, ?, ?, ?)",
                (f"manual:{now}", name, now, now),
            ).lastrowid
        else:
            target_id = int(target)
            if not con.execute("SELECT 1 FROM projects WHERE id = ?", (target_id,)).fetchone():
                raise HTTPException(404, _("Zielprojekt nicht gefunden"))
        con.execute("UPDATE files SET project_id = ?, pinned = 1 WHERE id = ?", (target_id, fid))
    return RedirectResponse(f"/p/{target_id}", status_code=303)


# --- Einstellungen -----------------------------------------------------------

def _printer_rows() -> list[dict]:
    """Drucker für die Einstellungen, ohne Zugangscode, mit dem letzten Abruf."""
    rows = []
    for p in printer_import.load_printers():
        status = printer_import.state["printers"].get(p["id"])
        rows.append({"db_id": p.get("db_id"), "kind": p["kind"], "name": p["name"], "ip": p["ip"],
                     "serial": p["id"] if p["kind"] == "bambu" else "",
                     "url": printer_import.moonraker_url(p["ip"]) if p["kind"] == "moonraker" else "",
                     "source": p["source"], "status": status})
    return rows


@app.get("/settings")
def settings_form(request: Request):
    return _render_settings(request)


def _render_settings(request: Request, error: str = "", form_rows: list | None = None, status_code: int = 200):
    with store.connect() as con:
        settings = store.get_settings(con)
        printers = [r[0] for r in con.execute(
            "SELECT DISTINCT printer FROM files WHERE missing = 0 AND printer IS NOT NULL AND printer != '' "
            "ORDER BY printer")]
    rows = [{"name": pr, "watts": settings.get(f"leistung:{pr}")} for pr in printers]
    return templates.TemplateResponse(request, "settings.html", {
        "s": settings, "printers": rows, "defaults": store.SETTING_DEFAULTS,
        "import_printers": form_rows if form_rows is not None else _printer_rows(),
        "printer_error": error, "psync": printer_import.state, "sync_minutes": printer_import.SYNC_INTERVAL // 60,
        "import_dir": printer_import.import_dir(),
    }, status_code=status_code)


THEME_LABELS = {"system": "Wie das System", "light": "Hell", "dark": "Dunkel"}
NEXT_THEME = {"system": "light", "light": "dark", "dark": "system"}
ONE_YEAR = 365 * 86400


def language_choice(request: Request) -> str:
    """Ausdrücklich gewählte Sprache dieses Browsers, leer = automatisch."""
    lang = request.cookies.get("sprache")
    return lang if lang in i18n.LANGUAGES else ""


def current_url(request: Request) -> str:
    return request.url.path + (f"?{request.url.query}" if request.url.query else "")


templates.env.globals.update(auth_enabled=auth.enabled)
templates.env.globals.update(language_choice=language_choice, current_url=current_url, THEME_LABELS=THEME_LABELS,
                             NEXT_THEME=NEXT_THEME)


@app.post("/appearance")
def appearance_save(sprache: str | None = Form(None), design: str | None = Form(None), next: str = Form("/")):
    """Sprache und Design aus der Titelleiste, gespeichert pro Browser als Cookie.

    Geändert wird nur, was mitgeschickt wurde; danach geht es zurück zur Seite.
    """
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=303)
    if sprache in i18n.LANGUAGES:
        response.set_cookie("sprache", sprache, max_age=ONE_YEAR, samesite="lax")
    elif sprache == "auto":
        response.delete_cookie("sprache")  # Vorgabe oder Browsersprache
    if design in i18n.THEMES:
        response.set_cookie("design", design, max_age=ONE_YEAR, samesite="lax")
    return response


@app.post("/settings/printers")
async def printers_save(request: Request):
    form = await request.form()
    ids, deletes, kinds = form.getlist("db_id"), set(form.getlist("delete")), form.getlist("kind")
    rows = []
    for i, (name, ip, serial, code) in enumerate(zip(form.getlist("name"), form.getlist("ip"),
                                                      form.getlist("serial"), form.getlist("code"))):
        db_id = int(ids[i]) if i < len(ids) and ids[i].isdigit() else None
        kind = kinds[i] if i < len(kinds) and kinds[i] in printer_import.KINDS else "bambu"
        rows.append({"db_id": db_id, "kind": kind, "name": name, "ip": ip, "serial": serial, "code": code,
                     "delete": str(db_id) in deletes if db_id else False})
    try:
        await run_in_threadpool(printer_import.save_printers, rows)
    except i18n.UserError as exc:
        # Eingaben behalten (ohne Zugangscodes) und Fehler anzeigen
        env = [r for r in _printer_rows() if r["source"] == "env"]
        kept = env + [{**r, "source": "db", "status": None} for r in rows if not r["delete"]
                      and (r["db_id"] or any((r["name"], r["ip"], r["serial"])))]
        return _render_settings(request, error=exc.translated(), form_rows=kept, status_code=400)
    return RedirectResponse("/settings?printers=1#printers", status_code=303)


def _number(value: str, low: float, high: float) -> float | None:
    value = (value or "").strip().replace(",", ".")
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        raise HTTPException(400, _("„{value}“ ist keine Zahl", value=value))
    if not low <= number <= high:
        raise HTTPException(400, _("{number} liegt außerhalb von {low} bis {high}", number=number, low=low, high=high))
    return number


@app.post("/settings")
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
    return RedirectResponse("/settings?saved=1", status_code=303)


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


def _save_upload(upload: UploadFile, folder: Path) -> tuple[str | None, str | None]:
    """Speichert eine Datei. Rückgabe: (relativer Pfad, Fehlermeldung)."""
    name = _clean_name(upload.filename)
    if not store.is_library_file(name):
        return None, _("{name}: keine .3mf- oder .gcode-Datei", name=upload.filename or _("Datei"))
    tmp = folder / f".upload-{uuid.uuid4().hex}.part"
    limit = MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with open(tmp, "wb") as out:
            while chunk := upload.file.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise i18n.UserError("größer als {mb} MB", mb=MAX_UPLOAD_MB)
                out.write(chunk)
        if gcode_file.is_gcode(name):
            with open(tmp, "rb") as fh:
                head = fh.read(256 * 1024)
            if b"\0" in head or not re.search(rb"^G[0-3][ \t]", head, re.M):
                raise i18n.UserError("keine gültige G-Code-Datei")
        elif not zipfile.is_zipfile(tmp):
            raise i18n.UserError("keine gültige 3MF-Datei")
        else:
            with zipfile.ZipFile(tmp) as z:
                if not any(n.startswith(("Metadata/", "3D/")) for n in z.namelist()):
                    raise i18n.UserError("keine gültige 3MF-Datei")
        existing = folder / name
        if existing.exists() and filecmp.cmp(tmp, existing, shallow=False):
            raise i18n.UserError("ist bereits unverändert in der Bibliothek")
        target = store.unique_path(folder, name)
        os.replace(tmp, target)
        return target.relative_to(store.LIBRARY_DIR).as_posix(), None
    except Exception as exc:
        reason = exc.translated() if isinstance(exc, i18n.UserError) else str(exc)
        return None, f"{name}: {reason}"
    finally:
        if tmp.exists():
            tmp.unlink()


# --- Löschen und Papierkorb -------------------------------------------------

def _require_writable() -> None:
    if not _library_writable():
        raise HTTPException(403, _("Bibliothek ist schreibgeschützt eingebunden"))


@app.post("/f/{fid}/delete")
def delete_file(fid: int):
    _require_writable()
    with store.connect() as con:
        row = con.execute("SELECT project_id FROM files WHERE id = ?", (fid,)).fetchone()
        if not row:
            raise HTTPException(404, _("Datei nicht gefunden"))
        try:
            entry = trash.delete(con, [fid], "datei")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        still_there = con.execute("SELECT 1 FROM projects WHERE id = ?", (row["project_id"],)).fetchone()
    if still_there:
        return RedirectResponse(f"/p/{row['project_id']}?deleted={entry}", status_code=303)
    return RedirectResponse(f"/?deleted={entry}", status_code=303)


@app.post("/p/{pid}/delete")
def delete_project(pid: int):
    _require_writable()
    with store.connect() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM files WHERE project_id = ?", (pid,))]
        if not ids:
            raise HTTPException(404, _("Projekt nicht gefunden"))
        try:
            entry = trash.delete(con, ids, "projekt")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return RedirectResponse(f"/?deleted={entry}", status_code=303)


def _deleted_entry(request: Request) -> dict | None:
    """Eintrag zur Meldung „… in den Papierkorb verschoben“ mit Rückgängig-Knopf."""
    raw = request.query_params.get("deleted", "")
    if not raw.isdigit():
        return None
    with store.connect() as con:
        row = con.execute("SELECT id, kind, label FROM trash WHERE id = ?", (int(raw),)).fetchone()
    return dict(row) if row else None


templates.env.globals["deleted_entry"] = _deleted_entry


@app.get("/trash")
def trash_page(request: Request):
    return templates.TemplateResponse(request, "trash.html", {
        "entries": trash.entries(), "days": trash.TRASH_DAYS, "writable": _library_writable(),
    })


@app.post("/trash/{entry_id}/restore")
def trash_restore(entry_id: int):
    _require_writable()
    try:
        result = trash.restore(entry_id)
    except LookupError:
        raise HTTPException(404, _("Eintrag nicht gefunden"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if result["project_id"]:
        return RedirectResponse(f"/p/{result['project_id']}?restored={result['restored']}", status_code=303)
    return RedirectResponse(f"/?restored={result['restored']}", status_code=303)


@app.post("/trash/{entry_id}/delete")
def trash_purge(entry_id: int):
    _require_writable()
    try:
        trash.purge(entry_id)
    except LookupError:
        raise HTTPException(404, _("Eintrag nicht gefunden"))
    return RedirectResponse("/trash?purged=1", status_code=303)


@app.post("/trash/empty")
def trash_purge_all():
    _require_writable()
    n = trash.purge_all()
    return RedirectResponse(f"/trash?purged={n}", status_code=303)


@app.get("/upload")
def upload_form(request: Request):
    return templates.TemplateResponse(request, "upload.html", {
        "writable": _library_writable(), "folders": _library_folders(),
        "errors": [], "max_mb": MAX_UPLOAD_MB,
    })


@app.post("/upload")
async def upload(request: Request, files: list[UploadFile] = File(...), folder: str = Form("")):
    if not _library_writable():
        raise HTTPException(403, _("Bibliothek ist schreibgeschützt eingebunden"))
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
        return RedirectResponse(f"/p/{ids[0]}?uploaded={len(saved)}", status_code=303)
    return RedirectResponse(f"/?uploaded={len(saved)}", status_code=303)


@app.post("/printer-sync")
def printer_sync():
    printer_import.request_sync()
    return RedirectResponse("/?printers=1", status_code=303)


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
    with open(path, "rb") as fh:  # Vorschaubilder aus G-Code können auch JPEG sein
        jpeg = fh.read(2) == b"\xff\xd8"
    return FileResponse(path, media_type="image/jpeg" if jpeg else "image/png", headers={"Cache-Control": "max-age=2592000"})


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
            raise HTTPException(404, _("Platte nicht gefunden oder nicht gesliced"))
        proj = con.execute("SELECT id, name FROM projects WHERE id = ?", (f["project_id"],)).fetchone()
    path = (store.LIBRARY_DIR / f["path"]).resolve()
    if not path.is_relative_to(store.LIBRARY_DIR.resolve()) or not path.exists():
        raise HTTPException(404, _("Datei nicht gefunden"))
    return f, _plate_dict(pl), proj, path


def _preview_base(fid: int, idx: int) -> Path:
    return store.DATA_DIR / "preview" / f"{fid}_{idx}"


@app.get("/view/{fid}/{idx}")
def viewer(request: Request, fid: int, idx: int):
    f, pl, proj, _path = _plate_for_view(fid, idx)
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
    *_rest, path = _plate_for_view(fid, idx)  # _ ist die Übersetzungsfunktion
    st = gcode_preview.status(path, idx, _preview_base(fid, idx))
    if st.get("error"):
        st = {**st, "error": _(st["error"])}
    return st


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


@app.get("/api/stats")
def api_stats():
    """Kennzahlen für Dashboards wie Homepage (Widget „customapi“).

    Dieselben Summen wie auf der Startseite, dazu Kosten, neue Dateien und
    Druckerstatus. Zahlen bleiben unformatiert, das Dashboard formatiert selbst.
    """
    week_ago = time.time() - 7 * 86400
    with store.connect() as con:
        projects = load_projects(con)
        files = con.execute("SELECT COUNT(*), MAX(added), SUM(added >= ?) FROM files WHERE missing = 0",
                            (week_ago,)).fetchone()
        trash_items = con.execute("SELECT COUNT(*) FROM trash").fetchone()[0]
    time_s = sum(p["time_s"] for p in projects)
    weight_g = sum(p["weight_g"] for p in projects)
    costs = [p["total_cost"] for p in projects if p["total_cost"] is not None]
    printers = printer_import.state["printers"].values()
    return {
        "projects": len(projects),
        "files": files[0],
        "plates": sum(len(p["plates"]) for p in projects),
        "print_time_s": time_s,
        "print_time_h": round(time_s / 3600, 1),
        "filament_g": round(weight_g, 1),
        "filament_kg": round(weight_g / 1000, 2),
        "cost_eur": round(sum(costs), 2) if costs else None,
        "energy_kwh": round(sum(p["energy_kwh"] for p in projects), 2),
        "new_files_7d": files[2] or 0,
        "last_added": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(files[1])) if files[1] else None,
        "printers_total": len(printer_import.load_printers()),
        "printers_ok": sum(1 for p in printers if p.get("ok")),
        "last_printer_sync": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(printer_import.state["last_run"]))
        if printer_import.state["last_run"] else None,
        "trash_items": trash_items,
        "version": APP_VERSION,
    }


@app.get("/about")
def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {"license_url": f"{APP_REPO}/blob/main/LICENSE",
                                                              "changelog_url": f"{APP_REPO}/blob/main/CHANGELOG.md"})


@app.get("/login")
def login_form(request: Request, next: str = "/"):
    if not auth.enabled():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": _local_target(next), "error": ""})


@app.post("/login")
def login(request: Request, user: str = Form(""), password: str = Form(""), next: str = Form("/")):
    if not auth.enabled():
        return RedirectResponse("/", status_code=303)
    address = request.client.host if request.client else "?"
    context = {"next": _local_target(next), "user": user}
    wait = auth.locked_out(address)
    if wait:
        minutes = max(1, (wait + 59) // 60)
        return templates.TemplateResponse(request, "login.html", {
            **context, "error": ngettext("Zu viele Fehlversuche. Bitte in {n} Minute erneut versuchen.",
                                         "Zu viele Fehlversuche. Bitte in {n} Minuten erneut versuchen.", minutes),
        }, status_code=429)
    if not auth.check(user.strip(), password):
        auth.record_failure(address)
        return templates.TemplateResponse(request, "login.html", {
            **context, "error": _("Benutzername oder Passwort ist falsch."),
        }, status_code=401)
    auth.record_success(address)
    response = RedirectResponse(_local_target(next), status_code=303)
    response.set_cookie(auth.COOKIE, auth.new_session(user.strip()), max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE)
    return response


@app.get("/healthz")
def healthz(request: Request):
    # Mit Anmeldung verrät der offene Healthcheck nur, dass PlateShelf läuft
    if auth.enabled() and not signed_in(request):
        return {"ok": True}
    return {"ok": True, "scan": store.last_scan, "printers": printer_import.state}


def fmt_ago(ts) -> str:
    if not ts:
        return _("noch nie")
    mins = int((time.time() - ts) // 60)
    if mins < 1:
        return _("gerade eben")
    if mins < 60:
        return _("vor {n} min", n=mins)
    return _("vor {n} h", n=mins // 60)


templates.env.filters["ago"] = fmt_ago
