# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Print a sliced plate again: choose printer and trays, confirm, start."""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import print_send
from .i18n import UserError, gettext as _
from .web import can_edit, templates

router = APIRouter()


def _running(state: dict | None) -> dict | None:
    """What the printer is printing right now (name, percent, remaining seconds), like the top bar."""
    if not state or print_send.is_idle(state) or not state.get("subtask_name"):
        return None
    minutes = state.get("mc_remaining_time")
    return {"name": state["subtask_name"], "percent": state.get("mc_percent"),
            "remaining_s": minutes * 60 if isinstance(minutes, (int, float)) and minutes >= 0 else None}


def _form_data(fid: int, plate: int, printer_id: str | None):
    info = print_send.plate_info(fid, plate)
    if info is None:
        raise HTTPException(404, _("Diese Platte ist nicht gesliced oder die Datei fehlt."))
    file_row, plate_row = info
    printers = print_send.printers()
    chosen = next((p for p in printers if p["id"] == printer_id), printers[0] if printers else None)
    state = print_send.status(chosen["id"]) if chosen else None
    loaded = print_send.trays(state)
    return {
        "file": file_row, "plate": plate_row, "printers": printers, "printer": chosen,
        "connected": state is not None, "idle": print_send.is_idle(state),
        "gcode_state": (state or {}).get("gcode_state"), "trays": loaded,
        "running": _running(state),
        "suggestion": (suggestion := print_send.suggest(plate_row["filaments"], loaded)),
        # filament slots whose proposed tray has a clearly different colour
        "color_off": {f["slot"] for f in plate_row["filaments"]
                      for t in loaded if t["slot"] == suggestion.get(f["slot"])
                      and print_send.color_distance(f.get("color", ""), t["color"]) > print_send.COLOR_WARN},
        "external": print_send.EXTERNAL,
    }


@router.get("/print/{fid}/{plate}")
def print_form(request: Request, fid: int, plate: int, printer: str | None = None, error: str | None = None):
    if not can_edit(request):
        raise HTTPException(403)
    if not print_send.enabled():
        raise HTTPException(404)
    data = _form_data(fid, plate, printer)
    data["error"] = error
    data["back"] = f"/p/{data['file']['project_id']}#platte-{fid}-{plate}"
    return templates.TemplateResponse(request, "print.html", data)


@router.post("/print/{fid}/{plate}")
async def print_start(request: Request, fid: int, plate: int, printer: str = Form(...),
                      bed_clear: str = Form(""), bed_leveling: str = Form(""), timelapse: str = Form("")):
    if not can_edit(request):
        raise HTTPException(403)
    if not print_send.enabled():
        raise HTTPException(404)
    data = _form_data(fid, plate, printer)
    target = next((p for p in data["printers"] if p["id"] == printer), None)
    if target is None:
        raise HTTPException(400, _("Drucker nicht gefunden"))
    form = await request.form()
    try:
        if not bed_clear:
            raise UserError("Bitte bestätige, dass das Druckbett leer ist.")
        mapping: dict[int, int] = {}
        for fil in data["plate"]["filaments"]:
            raw = str(form.get(f"slot_{fil['slot']}", ""))
            if raw.lstrip("-").isdigit():
                mapping[fil["slot"]] = int(raw)
        print_send.send(target, fid, plate, mapping, bed_leveling=bool(bed_leveling),
                        timelapse=bool(timelapse))
    except UserError as exc:
        data.update(error=exc.translated(), back=f"/p/{data['file']['project_id']}#platte-{fid}-{plate}")
        # 200, not 400: reverse proxies with their own error pages would hide the message
        return templates.TemplateResponse(request, "print.html", data)
    return RedirectResponse(f"/p/{data['file']['project_id']}?print_started={plate}#platte-{fid}-{plate}",
                            status_code=303)
