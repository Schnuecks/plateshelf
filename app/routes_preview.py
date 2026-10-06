# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""3D-Vorschau: Seite und die Daten für den Viewer."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from . import gcode_preview, store
from .i18n import gettext as _
from .projects import _plate_dict, natural_key
from .web import templates

router = APIRouter()


def _plate_for_view(fid: int, idx: int):
    with store.connect() as con:
        f = con.execute("SELECT id, path, project_id, nozzle, printer FROM files WHERE id = ? AND missing = 0", (fid,)).fetchone()
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


@router.get("/view/{fid}/{idx}")
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


@router.get("/api/preview/{fid}/{idx}")
def preview_status(fid: int, idx: int):
    *_rest, path = _plate_for_view(fid, idx)  # _ ist die Übersetzungsfunktion
    st = gcode_preview.status(path, idx, _preview_base(fid, idx))
    if st.get("error"):
        st = {**st, "error": _(st["error"])}
    return st


@router.get("/api/preview/{fid}/{idx}/meta")
def preview_meta(fid: int, idx: int):
    p = _preview_base(fid, idx).with_suffix(".json")
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/json", headers={"Cache-Control": "no-cache"})


@router.get("/api/preview/{fid}/{idx}/points")
def preview_points(fid: int, idx: int):
    p = _preview_base(fid, idx).with_suffix(".bin.gz")
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/octet-stream",
                        headers={"Content-Encoding": "gzip", "Cache-Control": "no-cache"})
