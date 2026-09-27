# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Einstellungen: Drucker, Stromkosten, Druckverlauf sowie Sprache und Design."""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

from . import i18n, print_history, printer_import, store
from .i18n import gettext as _
from .web import ONE_YEAR, templates

router = APIRouter()


def _printer_rows() -> list[dict]:
    """Drucker für die Einstellungen, ohne Zugangscode, mit dem letzten Abruf."""
    rows = []
    for p in printer_import.load_printers():
        status = printer_import.state["printers"].get(p["id"])
        rows.append({"db_id": p.get("db_id"), "kind": p["kind"], "name": p["name"], "ip": p["ip"],
                     "serial": p["id"] if p["kind"] == "bambu" else "",
                     "url": printer_import.printer_url(p["kind"], p["ip"]) if p["kind"] != "bambu" else "",
                     "source": p["source"], "status": status})
    return rows


@router.get("/settings")
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
        "history_on": print_history.enabled(), "history_env": print_history.env_setting(),
    }, status_code=status_code)


@router.post("/settings/history")
def history_settings_save(enabled: str = Form("")):
    if print_history.env_setting() is None:
        print_history.set_enabled(enabled == "1")
    return RedirectResponse("/settings?history=1#history", status_code=303)


@router.post("/appearance")
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


@router.post("/settings/printers")
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


@router.post("/settings")
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
