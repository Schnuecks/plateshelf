# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""PlateShelf: app, background threads and protection layers in front of all pages.

The pages themselves live in routes_*.py, shared code in web.py and projects.py.
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

from . import auth, backup, crypto, diagnostics, i18n, oidc, print_history, printer_import, routes_account, \
    routes_library, routes_preview, routes_settings, routes_system, store, trash
from .i18n import gettext as _
from .web import APP_NAME, BASE, EARLY_SCRIPT, IMPORTMAP, SAFE_METHODS, allowed, current_url, current_user, \
    is_public
# For tests and tools that used to import these names from main
from .i18n import fmt_printer_date  # noqa: F401
from .projects import BOOL_WORDS, SETTING_LABELS, load_projects  # noqa: F401
from .web import APP_VERSION, JOB_STATUSES, JS_TEXTS, STATUSES, THEME_LABELS, _app_version  # noqa: F401

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
diagnostics.install()  # recent warnings for the diagnostics in the settings


@asynccontextmanager
async def lifespan(_app):
    store.init_db()
    auth.seed_from_env()
    auth.announce_setup()
    crypto.remove_legacy_key_file(store.DATA_DIR)
    printer_import.init()
    if os.environ.get("BACKGROUND_TASKS", "true").lower() in ("0", "false", "no", "off"):
        # Tests only: scan once, but no threads that keep running beyond the test
        # and later access another test's database
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


# --- Security headers --------------------------------------------------------
# The Content Security Policy only allows our own files; the two small inline scripts in
# the page (class "js" and the import map of the 3D preview) via hash, no inline styles (computed
# colours and bars are set by app.js via element.style). So injected code would not run even if
# there were a hole. Forms may only go to PlateShelf itself and to the sign-in pages in
# AUTH_PORTAL_URL and OIDC_ISSUER: a reverse proxy with a login page (e.g. Authelia) redirects a
# form there once its session expires, and „Mit … verbinden“ redirects to the provider; a
# redirect to any other address would be blocked, and submitting would silently hang.
def _sha256(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def form_action() -> str:
    """'self' plus the origin of the sign-in pages (HTTPS, or HTTP on localhost)."""
    sources = ["'self'"]
    urls = [u.strip() for u in os.environ.get("AUTH_PORTAL_URL", "").split(",")] + [oidc.issuer()]
    for url in filter(None, urls):
        parts = urlsplit(url)
        local = parts.hostname in ("localhost", "127.0.0.1")
        if parts.netloc and (parts.scheme == "https" or (parts.scheme == "http" and local)):
            origin = f"{parts.scheme}://{parts.netloc}".lower()
            if origin not in sources:
                sources.append(origin)
    return " ".join(sources)


def csp() -> str:
    return "; ".join((
        "default-src 'self'",
        f"script-src 'self' {_sha256(EARLY_SCRIPT)} {_sha256(IMPORTMAP)}",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "worker-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        f"form-action {form_action()}",
        "object-src 'none'",
    ))


SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    # Everything is loaded from PlateShelf itself: other sites may not embed its files, and
    # the pages load nothing that has not agreed to it
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", csp())
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    # Pages and API answers always fresh: otherwise the iOS home-screen app may show an old state
    kind = response.headers.get("content-type", "")
    if kind.startswith(("text/html", "application/json")) or request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-cache")
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
        # Dashboards: API token, read-only and only under /api/
        if request.method in SAFE_METHODS and path.startswith("/api/") and \
                auth.token_valid(auth.token_from_headers(request.headers)):
            return await call_next(request)
        if request.method == "GET" and not path.startswith("/api/"):
            if not auth.has_users():
                return RedirectResponse("/setup", status_code=303)
            return RedirectResponse(f"/login?next={quote(current_url(request))}", status_code=303)
        # Scripts: login via HTTP Basic Auth or API token
        return PlainTextResponse(_("Anmeldung erforderlich."), status_code=401,
                                 headers={"WWW-Authenticate": f'Basic realm="{APP_NAME}", charset="UTF-8"'})
    if not allowed(user["role"], request.method, path):
        return PlainTextResponse(_("Dafür fehlt deinem Konto die Berechtigung."), status_code=403)
    return await call_next(request)


# --- Protection against foreign sites (CSRF) ---------------------------------
# Without login (default), and even with a valid session, any web page opened in the
# same network could slip the browser a form aimed at PlateShelf
# (e.g. "Papierkorb leeren"). Browsers always send Origin or Referer with such
# requests; if their host does not match the requested one, the request is rejected.
TRUSTED_ORIGINS = {o.strip().rstrip("/").lower() for o in os.environ.get("TRUSTED_ORIGINS", "").split(",")
                   if o.strip()}


def same_origin(request: Request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return True  # not a browser form, e.g. curl or a script on the home network
    if source == "null":
        return False  # sandbox, file or redirect from a foreign site
    parts = urlsplit(source)
    if not parts.netloc:
        return False
    if f"{parts.scheme}://{parts.netloc}".lower() in TRUSTED_ORIGINS:
        return True
    # Behind a reverse proxy, the host forwarded by the proxy counts
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0]
    return parts.netloc.lower() == host.strip().lower()


mimetypes.add_type("application/manifest+json", ".webmanifest")  # web app manifest (home screen)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
for module in (routes_account, routes_library, routes_settings, routes_preview, routes_system):
    app.include_router(module.router)
