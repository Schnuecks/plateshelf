# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Übersicht, Projekte, Dateien, Hochladen, Papierkorb und Druckverlauf."""
from __future__ import annotations

import filecmp
import json
import os
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse

from . import gcode_file, i18n, print_history, printer_import, store, trash
from .i18n import gettext as _
from .projects import _key_settings, file_stamp, load_projects, natural_key
from .web import STATUSES, _local_target, templates

router = APIRouter()


@router.get("/")
def index(request: Request, q: str = "", material: str = "", printer: str = "", status: str = "", tag: str = "",
          sort: str = "neu", printed: str = ""):
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
                and (printed != "ja" or p["prints"] > 0) and (printed != "nein" or p["prints"] == 0)
                and (not tag or tag in p["tag_list"]))

    shown = [p for p in projects if match(p)]
    keys = {
        "neu": (lambda p: -p["latest"]),
        "name": (lambda p: p["name"].lower()),
        "dauer": (lambda p: -p["time_s"]),
        "gewicht": (lambda p: -p["weight_g"]),
        "gedruckt": (lambda p: (-p["last_printed"], -p["latest"])),  # nie gedruckte ans Ende
        "haeufig": (lambda p: (-p["prints"], -p["last_printed"])),
    }
    shown.sort(key=keys.get(sort, keys["neu"]))
    totals = {
        "count": len(shown),
        "time_s": sum(p["time_s"] for p in shown),
        "weight_g": sum(p["weight_g"] for p in shown),
    }
    return templates.TemplateResponse(request, "index.html", {
        "projects": shown, "totals": totals, "q": q, "material": material, "printer": printer,
        "status": status, "tag": tag, "sort": sort, "printed": printed, "all_materials": all_materials,
        "all_printers": all_printers, "all_tags": all_tags, "scan": store.last_scan,
        "psync": printer_import.state,
    })


@router.get("/p/{pid}")
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
    jobs = print_history.jobs_for([f["path"] for f in file_rows.values()])
    names = {f["path"]: f["path"].split("/")[-1] for f in file_rows.values()}
    for j in jobs:
        j["file_name"] = names.get(j["local_path"], "")
    return templates.TemplateResponse(request, "project.html", {
        "p": p, "files": list(file_rows.values()), "others": others, "designer": designer,
        "writable": _library_writable(), "strip": strip, "jobs": jobs,
    })


@router.get("/history")
def history(request: Request):
    return templates.TemplateResponse(request, "history.html", {
        "jobs": print_history.recent(), "enabled": print_history.enabled(),
        "printers": printer_import.load_printers(),
    })


@router.post("/history/{job_id}/delete")
def history_delete(job_id: int, next: str = Form("/history")):
    try:
        print_history.delete(job_id)
    except KeyError:
        raise HTTPException(404, _("Druck nicht gefunden"))
    target = _local_target(next.split("#")[0])
    anchor = "#history" if target.startswith("/p/") else ""
    return RedirectResponse(target + ("&" if "?" in target else "?") + "job_deleted=1" + anchor, status_code=303)


@router.post("/p/{pid}")
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


@router.post("/f/{fid}/move")
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
        return None, _("{name}: keine .3mf-, .gcode- oder .bgcode-Datei", name=upload.filename or _("Datei"))
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
        if name.lower().endswith(".bgcode"):
            with open(tmp, "rb") as fh:
                if fh.read(4) != b"GCDE":
                    raise i18n.UserError("keine gültige G-Code-Datei")
        elif gcode_file.is_gcode(name):
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


@router.post("/f/{fid}/delete")
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


@router.post("/p/{pid}/delete")
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


@router.get("/trash")
def trash_page(request: Request):
    return templates.TemplateResponse(request, "trash.html", {
        "entries": trash.entries(), "days": trash.TRASH_DAYS, "writable": _library_writable(),
    })


@router.post("/trash/{entry_id}/restore")
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


@router.post("/trash/{entry_id}/delete")
def trash_purge(entry_id: int):
    _require_writable()
    try:
        trash.purge(entry_id)
    except LookupError:
        raise HTTPException(404, _("Eintrag nicht gefunden"))
    return RedirectResponse("/trash?purged=1", status_code=303)


@router.post("/trash/empty")
def trash_purge_all():
    _require_writable()
    n = trash.purge_all()
    return RedirectResponse(f"/trash?purged={n}", status_code=303)


@router.get("/upload")
def upload_form(request: Request):
    return templates.TemplateResponse(request, "upload.html", {
        "writable": _library_writable(), "folders": _library_folders(),
        "errors": [], "max_mb": MAX_UPLOAD_MB,
    })


@router.post("/upload")
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


@router.post("/printer-sync")
def printer_sync():
    printer_import.request_sync()
    return RedirectResponse("/?printers=1", status_code=303)


@router.post("/rescan")
def rescan(force: int = Form(0)):
    threading.Thread(target=store.scan, kwargs={"force": bool(force)}, daemon=True).start()
    return RedirectResponse("/", status_code=303)


@router.get("/thumb/{fid}/{kind}/{idx}")
def thumb(fid: int, kind: str, idx: int):
    if kind not in ("plate", "top"):
        raise HTTPException(404)
    path = store.THUMB_DIR / str(fid) / f"{kind}_{idx}.png"
    if not path.exists():
        raise HTTPException(404)
    with open(path, "rb") as fh:  # Vorschaubilder aus G-Code können auch JPEG sein
        jpeg = fh.read(2) == b"\xff\xd8"
    return FileResponse(path, media_type="image/jpeg" if jpeg else "image/png", headers={"Cache-Control": "max-age=2592000"})


@router.get("/photo/{fid}/{name}")
def photo(fid: int, name: str):
    if not re.fullmatch(r"photo_\d+\.(webp|jpe?g|png)", name):
        raise HTTPException(404)
    path = store.THUMB_DIR / str(fid) / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "max-age=86400"})


@router.get("/f/{fid}/download")
def download(fid: int):
    with store.connect() as con:
        row = con.execute("SELECT path FROM files WHERE id = ?", (fid,)).fetchone()
    if not row:
        raise HTTPException(404)
    path = (store.LIBRARY_DIR / row["path"]).resolve()
    if not path.is_relative_to(store.LIBRARY_DIR.resolve()) or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")
