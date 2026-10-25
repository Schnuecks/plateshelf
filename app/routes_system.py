# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Metrics for dashboards, About page and health check."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request

from . import auth, print_history, printer_import, store
from .projects import load_projects
from .i18n import gettext as _
from .web import live_prints, APP_REPO, APP_VERSION, signed_in, templates

router = APIRouter()


@router.get("/api/printing")
def api_printing(idle_placeholder: bool = False):
    """Running prints as finished text, for display in the title bar and dashboards.

    With ?idle_placeholder=1, an entry „Kein Druck aktiv“ is returned when idle instead of an
    empty list: otherwise Homepage shows a fixed "No items found" for an empty list.
    """
    items = live_prints() if print_history.enabled() else []
    if not items and idle_placeholder:
        idle = _("Kein Druck aktiv")
        items = [{"text": idle, "short": idle, "name": idle, "url": "/", "progress": None, "printer": None,
                  "remaining_min": None, "remaining": "", "idle": True}]
    return {"items": items}


@router.get("/api/printers/status")
def api_printer_status():
    """Printer reachability for the settings page, which uses it to refresh the dot every few seconds."""
    return {"printers": printer_import.reachability(), "sync_running": bool(printer_import.state.get("running")),
            "last_sync": last_sync()}


@router.get("/api/library")
def api_library():
    """Library state, so that the overview shows new prints by itself (static/app.js)."""
    return {"version": store.library_version(), "sync_running": bool(printer_import.state.get("running")),
            "last_sync": last_sync()}


def last_sync() -> int | None:
    """End of the last printer sync (whole seconds), so that pages can detect its end."""
    return int(printer_import.state["last_run"]) if printer_import.state.get("last_run") else None


@router.get("/api/stats")
def api_stats():
    """Metrics for dashboards such as Homepage (widget "customapi").

    The same totals as on the start page, plus costs, new files and
    printer status. Numbers stay unformatted; the dashboard formats them itself.
    """
    live = print_history.printing_now()
    week_ago = time.time() - 7 * 86400
    month_ago = time.time() - 30 * 86400
    with store.connect() as con:
        jobs = con.execute(
            """SELECT SUM(status = 'completed' AND started >= ?), SUM(status IN ('cancelled', 'failed') AND started >= ?),
                      SUM(status = 'running'), MAX(ended) FROM print_jobs WHERE hidden = 0""",
            (month_ago, month_ago)).fetchone()
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
        "plates": sum(p["plate_count"] for p in projects),
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
        "prints_30d": jobs[0] or 0,
        "prints_failed_30d": jobs[1] or 0,
        "printing_now": jobs[2] or 0,
        "last_print_end": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(jobs[3])) if jobs[3] else None,
        "printing_name": live[0]["project_name"] or (live[0]["remote_name"] or "").rsplit("/", 1)[-1] if live else None,
        "printing_printer": live[0]["printer_name"] if live else None,
        "printing_progress": round(live[0]["progress"]) if live and live[0]["progress"] is not None else None,
        "printing_remaining_min": round(live[0]["remaining_s"] / 60) if live and live[0]["remaining_s"] else None,
        "version": APP_VERSION,
    }


@router.get("/about")
def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {"license_url": f"{APP_REPO}/blob/main/LICENSE",
                                                              "changelog_url": f"{APP_REPO}/blob/main/CHANGELOG.md"})


@router.get("/healthz")
def healthz(request: Request):
    # With sign-in enabled, the open health check only reveals that PlateShelf is running
    if auth.enabled() and not signed_in(request):
        return {"ok": True}
    return {"ok": True, "scan": store.last_scan, "printers": printer_import.state}
