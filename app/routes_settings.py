# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Settings: printers, electricity costs, print history as well as language and theme."""
from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse
from starlette.background import BackgroundTask

from . import auth, backup, crypto, diagnostics, i18n, print_history, printer_import, render, store
from .i18n import gettext as _
from .web import APP_VERSION, ONE_YEAR, audit, current_user, templates

router = APIRouter()


def _printer_rows() -> list[dict]:
    """Printers for the settings, without access code, with the last poll."""
    rows = []
    for p in printer_import.load_printers():
        status = printer_import.state["printers"].get(p["id"])
        if p.get("code_error") and not status:  # show even before the first poll
            status = {"error": p["code_error"], "kind": p["kind"], "error_args": {"detail": p["code_error"]},
                      "error_msg": "Zugangsdaten lassen sich nicht entschlüsseln ({detail}). Wurde SECRET_KEY "
                                   "geändert? Dann in den Einstellungen neu eingeben."}
        rows.append({"id": p["id"], "db_id": p.get("db_id"), "kind": p["kind"], "name": p["name"], "ip": p["ip"],
                     "serial": p["id"] if p["kind"] == "bambu" else "",
                     "url": printer_import.printer_url(p["kind"], p["ip"]) if p["kind"] != "bambu" else "",
                     "address": printer_import.printer_address(p["kind"], p["ip"]),
                     "source": p["source"], "status": status})
    return rows


@router.get("/settings")
def settings_form(request: Request):
    return _render_settings(request)


def _render_settings(request: Request, error: str = "", form_rows: list | None = None, status_code: int = 200,
                     users_error: str = "", new_token: str = "", tokens_error: str = "", backup_error: str = ""):
    with store.connect() as con:
        settings = store.get_settings(con)
        printers = [r[0] for r in con.execute(
            "SELECT DISTINCT printer FROM files WHERE missing = 0 AND printer IS NOT NULL AND printer != '' "
            "ORDER BY printer")]
        rules = store.price_rules(settings)
        hits = store.rule_hits(con, rules)
    rows = [{"name": pr, "watts": settings.get(f"leistung:{pr}")} for pr in printers]
    price_rules = [{"keyword": k, "price": price, "hits": hits.get(k, 0)}
                   for k, price in sorted(rules, key=lambda r: r[0].lower())]
    return templates.TemplateResponse(request, "settings.html", {
        "s": settings, "printers": rows, "defaults": store.SETTING_DEFAULTS, "price_rules": price_rules,
        "events": auth.events() if auth.enabled() else [], "event_days": auth.EVENT_DAYS,
        "import_printers": form_rows if form_rows is not None else _printer_rows(),
        "psync_last": printer_import.state.get("last_run"),
        "printer_error": error, "psync": printer_import.state, "sync_minutes": printer_import.SYNC_INTERVAL // 60,
        "import_dir": printer_import.import_dir(),
        "thumbs_mode": render.mode(settings),
        "history_on": print_history.enabled(), "history_env": print_history.env_setting(),
        "diagnostics": diagnostics.report(APP_VERSION),
        "key": crypto.status(), "plaintext_codes": printer_import.stored_plaintext(), "auth_on": auth.enabled(),
        "users": auth.list_users() if auth.enabled() else [], "tokens": auth.list_tokens(),
        "me": current_user(request), "users_error": users_error,
        "new_token": new_token, "tokens_error": tokens_error,
        "backups": backup.list_backups(), "backup_keep": backup.KEEP, "backup_error": backup_error,
    }, status_code=status_code)


@router.get("/settings/printers")
@router.get("/settings/history")
@router.get("/settings/users")
@router.get("/settings/tokens")
@router.get("/settings/backup/restore")
def settings_form_url(request: Request):
    """Form URLs, in case the browser reloads the page afterwards: to the matching section."""
    section = request.url.path.removeprefix("/settings/").split("/")[0]
    return RedirectResponse(f"/settings#{section}", status_code=303)


@router.get("/settings/backup")
def backup_download():
    """Backup of the database as a single file, consistent even while PlateShelf is writing.

    Contains projects, notes, tags, status, settings, printers (credentials
    encrypted if SECRET_KEY is set) and the print history. Thumbnails and
    3D previews are deliberately missing; they are regenerated after restoring.
    """
    fd, name = tempfile.mkstemp(suffix=".sqlite", prefix="plateshelf-backup-")
    os.close(fd)
    target = Path(name)
    backup.write(target)
    return FileResponse(target, filename=f"plateshelf-backup-{time.strftime('%Y-%m-%d')}.sqlite",
                        media_type="application/vnd.sqlite3", background=BackgroundTask(target.unlink))


@router.get("/settings/backup/file/{name}")
def backup_file(name: str):
    """Download one of the automatic backups."""
    path = backup.path_of(name)
    if not path:
        raise HTTPException(404, _("Sicherung nicht gefunden"))
    return FileResponse(path, filename=name, media_type="application/vnd.sqlite3")


def _after_restore(request: Request, safety: str) -> RedirectResponse:
    user = current_user(request)
    audit(request, "backup_restored", user["name"] if user else "")
    printer_import.init()  # Printers from the backup, encrypt credentials if needed
    # Regenerate thumbnails; like „Bibliothek neu einlesen“ in the background
    if os.environ.get("BACKGROUND_TASKS", "true").lower() not in ("0", "false", "no", "off"):
        threading.Thread(target=store.scan, kwargs={"force": True}, daemon=True).start()
    else:
        store.scan(force=True, wait=True)
    return RedirectResponse(f"/settings?restored={safety}#backup", status_code=303)


@router.post("/settings/backup/restore")
async def backup_restore_upload(request: Request, file: UploadFile = File(...)):
    fd, name = tempfile.mkstemp(suffix=".sqlite", prefix="plateshelf-restore-")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        safety = await run_in_threadpool(backup.restore, tmp)
    except i18n.UserError as exc:
        return _render_settings(request, backup_error=exc.translated(), status_code=400)
    finally:
        tmp.unlink(missing_ok=True)
    return _after_restore(request, safety)


@router.post("/settings/backup/file/{name}/restore")
def backup_restore_file(request: Request, name: str):
    path = backup.path_of(name)
    if not path:
        raise HTTPException(404, _("Sicherung nicht gefunden"))
    try:
        safety = backup.restore(path)
    except i18n.UserError as exc:
        return _render_settings(request, backup_error=exc.translated(), status_code=400)
    return _after_restore(request, safety)


@router.post("/settings/thumbnails")
def thumbnails_save(vorschaubilder: str = Form("")):
    if vorschaubilder in render.MODES:
        store.save_settings({"vorschaubilder": vorschaubilder})
        if store.PREBUILD and vorschaubilder != "slicer":
            store.request_prebuild()  # draw missing images for the new choice
    return RedirectResponse("/settings?thumbs=1#thumbs", status_code=303)


@router.post("/settings/history")
def history_settings_save(enabled: str = Form("")):
    if print_history.env_setting() is None:
        print_history.set_enabled(enabled == "1")
    return RedirectResponse("/settings?history=1#history", status_code=303)


@router.post("/appearance")
def appearance_save(sprache: str | None = Form(None), design: str | None = Form(None), next: str = Form("/")):
    """Language and theme from the title bar, stored per browser as a cookie.

    Only what was sent is changed; afterwards it goes back to the page.
    """
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=303)
    if sprache in i18n.LANGUAGES:
        response.set_cookie("sprache", sprache, max_age=ONE_YEAR, samesite="lax")
    elif sprache == "auto":
        response.delete_cookie("sprache")  # default or browser language
    if design in i18n.THEMES:
        response.set_cookie("design", design, max_age=ONE_YEAR, samesite="lax")
    return response


@router.post("/settings/printers")
async def printers_save(request: Request):
    form = await request.form()
    ids, deletes, kinds = form.getlist("db_id"), set(form.getlist("delete")), form.getlist("kind")
    rows = []
    for i, (name, ip, serial, code) in enumerate(zip(form.getlist("name"), form.getlist("ip"),
                                                      form.getlist("serial"), form.getlist("code"), strict=False)):
        db_id = int(ids[i]) if i < len(ids) and ids[i].isdigit() else None
        kind = kinds[i] if i < len(kinds) and kinds[i] in printer_import.KINDS else "bambu"
        rows.append({"db_id": db_id, "kind": kind, "name": name, "ip": ip, "serial": serial, "code": code,
                     "delete": str(db_id) in deletes if db_id else False})
    try:
        await run_in_threadpool(printer_import.save_printers, rows)
    except i18n.UserError as exc:
        # Keep the input (without access codes) and show errors
        env = [r for r in _printer_rows() if r["source"] == "env"]
        kept = env + [{**r, "source": "db", "status": None} for r in rows if not r["delete"]
                      and (r["db_id"] or any((r["name"], r["ip"], r["serial"])))]
        return _render_settings(request, error=exc.translated(), form_rows=kept, status_code=400)
    return RedirectResponse("/settings?printers=1#printers", status_code=303)


@router.post("/settings/users")
def user_add(request: Request, name: str = Form(""), role: str = Form("user"), password: str = Form(""),
             repeat: str = Form("")):
    try:
        auth.check_password_rules(password, repeat)
        auth.create_user(name, password, role)
    except i18n.UserError as exc:
        return _render_settings(request, users_error=exc.translated(), status_code=400)
    audit(request, "user_created", current_user(request)["name"], f"{' '.join(name.split())} ({role})")
    return RedirectResponse("/settings?users=1#users", status_code=303)


@router.post("/settings/users/{user_id}")
def user_save(request: Request, user_id: int, role: str = Form(""), password: str = Form("")):
    target = auth.get_user(user_id)
    try:
        if role:
            auth.set_role(user_id, role)
        if password:
            auth.set_password(user_id, password, keep_session=request.cookies.get(auth.COOKIE))
    except i18n.UserError as exc:
        return _render_settings(request, users_error=exc.translated(), status_code=400)
    if target and role and role != target["role"]:
        audit(request, "role_changed", current_user(request)["name"], f"{target['name']}: {target['role']} → {role}")
    if target and password:
        audit(request, "password_reset", current_user(request)["name"], target["name"])
    return RedirectResponse("/settings?users=1#users", status_code=303)


@router.post("/settings/users/{user_id}/delete")
def user_delete(request: Request, user_id: int):
    target = auth.get_user(user_id)
    try:
        auth.delete_user(user_id, current_user(request)["id"])
    except i18n.UserError as exc:
        return _render_settings(request, users_error=exc.translated(), status_code=400)
    if target:
        audit(request, "user_deleted", current_user(request)["name"], target["name"])
    return RedirectResponse("/settings?users=1#users", status_code=303)


@router.post("/settings/tokens")
def token_add(request: Request, name: str = Form("")):
    try:
        token = auth.create_token(name, current_user(request)["name"])
    except i18n.UserError as exc:
        return _render_settings(request, tokens_error=exc.translated(), status_code=400)
    audit(request, "token_created", current_user(request)["name"], " ".join(name.split()))
    # Only visible now, hence no redirect; the browser must not cache the page
    response = _render_settings(request, new_token=token)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/settings/tokens/{token_id}/delete")
def token_delete(request: Request, token_id: int):
    name = next((t["name"] for t in auth.list_tokens() if t["id"] == token_id), "")
    auth.delete_token(token_id)
    audit(request, "token_revoked", current_user(request)["name"], name)
    return RedirectResponse("/settings?tokens=1#tokens", status_code=303)


def _number(value: str, low: float, high: float) -> float | None:
    value = (value or "").strip().replace(",", ".")
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        raise HTTPException(400, _("„{value}“ ist keine Zahl", value=value)) from None
    if not low <= number <= high:
        raise HTTPException(400, _("{number} liegt außerhalb von {low} bis {high}", number=number, low=low, high=high))
    return number


@router.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    def or_default(value, key):
        # 0 is a valid electricity price (e.g. own solar system), only an empty field takes the default
        return store.SETTING_DEFAULTS[key] if value is None else value

    values = {
        "strompreis": or_default(_number(form.get("strompreis"), 0, 5), "strompreis"),
        "leistung_standard": or_default(_number(form.get("leistung_standard"), 1, 5000), "leistung_standard"),
        "dateipreise_ignorieren": 1 if form.get("dateipreise_ignorieren") else None,
    }
    for key, value in form.items():
        if key.startswith("leistung:"):
            values[key] = _number(value, 1, 5000)  # empty = use default power
        elif key.startswith("filamentpreis:") or key == "filamentpreis_standard":
            values[key] = _number(value, 0, 1000)  # empty = remove price
    keyword = " ".join((form.get("neues_stichwort") or "").split())
    new_price = _number(form.get("neuer_filamentpreis"), 0, 1000)
    if keyword and new_price is not None:
        values[f"filamentpreis:{keyword}"] = new_price
    store.save_settings(values)
    return RedirectResponse("/settings?saved=1#power", status_code=303)


# --- Upload -----------------------------------------------------------------
