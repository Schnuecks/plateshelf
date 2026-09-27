# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""PlateShelf: App, Hintergrund-Threads und Schutzschichten vor allen Seiten.

Die Seiten selbst liegen in routes_*.py, Gemeinsames in web.py und projects.py.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, crypto, diagnostics, i18n, print_history, printer_import, routes_library, routes_preview, routes_settings, \
    routes_system, store, trash
from .i18n import gettext as _
from .web import APP_NAME, BASE, current_url, is_public, signed_in
# Für Tests und Werkzeuge, die diese Namen bisher aus main holten
from .i18n import fmt_printer_date  # noqa: F401
from .projects import BOOL_WORDS, SETTING_LABELS, load_projects  # noqa: F401
from .web import APP_VERSION, JOB_STATUSES, JS_TEXTS, STATUSES, THEME_LABELS, _app_version  # noqa: F401

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
diagnostics.install()  # letzte Warnungen für die Diagnose in den Einstellungen


@asynccontextmanager
async def lifespan(_app):
    store.init_db()
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


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
for module in (routes_library, routes_settings, routes_preview, routes_system):
    app.include_router(module.router)
