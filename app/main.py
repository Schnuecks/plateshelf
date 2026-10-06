# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""PlateShelf: App, Hintergrund-Threads und Schutzschichten vor allen Seiten.

Die Seiten selbst liegen in routes_*.py, Gemeinsames in web.py und projects.py.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import os
import threading
from contextlib import asynccontextmanager
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, backup, crypto, diagnostics, i18n, print_history, printer_import, routes_account, \
    routes_library, routes_preview, routes_settings, routes_system, store, trash
from .i18n import gettext as _
from .web import APP_NAME, BASE, EARLY_SCRIPT, IMPORTMAP, SAFE_METHODS, allowed, current_url, current_user, \
    is_public
# Für Tests und Werkzeuge, die diese Namen bisher aus main holten
from .i18n import fmt_printer_date  # noqa: F401
from .projects import BOOL_WORDS, SETTING_LABELS, load_projects  # noqa: F401
from .web import APP_VERSION, JOB_STATUSES, JS_TEXTS, STATUSES, THEME_LABELS, _app_version  # noqa: F401

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
diagnostics.install()  # letzte Warnungen für die Diagnose in den Einstellungen


@asynccontextmanager
async def lifespan(_app):
    store.init_db()
    auth.seed_from_env()
    auth.announce_setup()
    crypto.remove_legacy_key_file(store.DATA_DIR)
    printer_import.init()
    if os.environ.get("BACKGROUND_TASKS", "true").lower() in ("0", "false", "no", "off"):
        # Nur für Tests: einmal einlesen, aber keine Threads, die über den Test hinaus weiterlaufen
        # und später auf die Datenbank eines anderen Tests zugreifen
        store.scan(wait=True)
        yield
        return
    interval = int(os.environ.get("SCAN_INTERVAL", "120"))
    threading.Thread(target=store.scan_loop, args=(interval,), daemon=True).start()
    if store.PREBUILD:
        threading.Thread(target=store.prebuild_loop, daemon=True).start()
    threading.Thread(target=printer_import.sync_loop, daemon=True).start()
    threading.Thread(target=print_history.loop, daemon=True).start()
    threading.Thread(target=trash.purge_loop, daemon=True).start()
    threading.Thread(target=backup.loop, daemon=True).start()
    if store.WATCH:
        threading.Thread(target=store.watch_loop, daemon=True).start()
    yield


app = FastAPI(title="PlateShelf", docs_url=None, redoc_url=None, lifespan=lifespan)


# --- Sicherheits-Header ------------------------------------------------------
# Die Content-Security-Policy erlaubt nur eigene Dateien; die zwei kleinen Skripte direkt in
# der Seite (Klasse „js“ und Importmap der 3D-Vorschau) per Hash. Eingeschleuster Code liefe
# damit selbst bei einer Lücke nicht. Stile direkt am Element (Farbfelder, Balken) bleiben
# erlaubt. form-action auch https:, weil ein Reverse-Proxy mit Anmeldeseite (z. B. Authelia)
# Formulare nach Ablauf seiner Sitzung dorthin umleitet; sonst bliebe das Absenden still hängen.
def _sha256(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


CSP = "; ".join((
    "default-src 'self'",
    f"script-src 'self' {_sha256(EARLY_SCRIPT)} {_sha256(IMPORTMAP)}",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "worker-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self' https:",
    "object-src 'none'",
))
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.middleware("http")
async def language_middleware(request: Request, call_next):
    i18n.set_language(i18n.pick_language(request.cookies.get("sprache"), request.headers.get("accept-language")))
    if request.method not in SAFE_METHODS and not same_origin(request):
        logging.getLogger("plateshelf").warning("Anfrage von fremder Seite abgelehnt: %s %s (Origin %s)",
                                                 request.method, request.url.path,
                                                 request.headers.get("origin") or request.headers.get("referer"))
        return PlainTextResponse(_("Anfrage von einer fremden Seite abgelehnt."), status_code=403)
    path = request.url.path
    if not auth.enabled() or is_public(path):
        return await call_next(request)
    user = current_user(request)
    if user is None:
        # Dashboards: API-Token, nur lesend und nur unter /api/
        if request.method in SAFE_METHODS and path.startswith("/api/") and \
                auth.token_valid(auth.token_from_headers(request.headers)):
            return await call_next(request)
        if request.method == "GET" and not path.startswith("/api/"):
            if not auth.has_users():
                return RedirectResponse("/setup", status_code=303)
            return RedirectResponse(f"/login?next={quote(current_url(request))}", status_code=303)
        # Skripte: Anmeldung per HTTP Basic Auth oder API-Token
        return PlainTextResponse(_("Anmeldung erforderlich."), status_code=401,
                                 headers={"WWW-Authenticate": f'Basic realm="{APP_NAME}", charset="UTF-8"'})
    if not allowed(user["role"], request.method, path):
        return PlainTextResponse(_("Dafür fehlt deinem Konto die Berechtigung."), status_code=403)
    return await call_next(request)


# --- Schutz vor fremden Seiten (CSRF) ----------------------------------------
# Ohne Anmeldung (Standard) und selbst mit gültiger Sitzung könnte jede Webseite, die
# man im selben Netz öffnet, dem Browser ein Formular an PlateShelf unterschieben
# (z. B. „Papierkorb leeren“). Browser schicken bei solchen Anfragen immer Origin oder
# Referer mit; stimmt deren Host nicht mit dem angefragten überein, wird abgelehnt.
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


mimetypes.add_type("application/manifest+json", ".webmanifest")  # Web-App-Manifest (Homescreen)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
for module in (routes_account, routes_library, routes_settings, routes_preview, routes_system):
    app.include_router(module.router)
