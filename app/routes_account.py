# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Einrichtung des ersten Kontos, Anmelden, Abmelden und das eigene Konto."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from . import auth, oidc
from .i18n import UserError, gettext as _, ngettext
from .web import _local_target, current_user, templates

router = APIRouter()
log = logging.getLogger("plateshelf")


def _address(request: Request) -> str:
    return request.client.host if request.client else "?"


def _start_session(request: Request, user_id: int, target: str) -> RedirectResponse:
    token = auth.new_session(user_id, request.headers.get("user-agent", ""), _address(request))
    response = RedirectResponse(_local_target(target), status_code=303)
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return response


def _lockout_text(request: Request) -> str:
    wait = auth.locked_out(_address(request))
    if not wait:
        return ""
    minutes = max(1, (wait + 59) // 60)
    return ngettext("Zu viele Fehlversuche. Bitte in {n} Minute erneut versuchen.",
                    "Zu viele Fehlversuche. Bitte in {n} Minuten erneut versuchen.", minutes)


# --- Einrichtung ------------------------------------------------------------

def _setup_open() -> bool:
    return auth.enabled() and not auth.has_users()


@router.get("/setup")
def setup_form(request: Request):
    if not _setup_open():
        return RedirectResponse("/", status_code=303)
    auth.setup_code()  # sicherstellen, dass es einen gibt (steht auch im Log)
    return templates.TemplateResponse(request, "setup.html", {"error": "", "name": ""})


@router.post("/setup")
def setup(request: Request, code: str = Form(""), name: str = Form(""), password: str = Form(""),
          repeat: str = Form("")):
    if not _setup_open():
        return RedirectResponse("/", status_code=303)
    context = {"name": name}
    locked = _lockout_text(request)
    if locked:
        return templates.TemplateResponse(request, "setup.html", {**context, "error": locked}, status_code=429)
    if not auth.setup_code_valid(code):
        auth.record_failure(_address(request))
        return templates.TemplateResponse(request, "setup.html", {
            **context, "error": _("Der Einrichtungscode stimmt nicht. Du findest ihn im Protokoll des Containers.")},
            status_code=401)
    try:
        user_id = auth.finish_setup(name, password, repeat)
    except UserError as exc:
        return templates.TemplateResponse(request, "setup.html", {**context, "error": exc.translated()},
                                          status_code=400)
    auth.record_success(_address(request))
    return _start_session(request, user_id, "/")


# --- Anmelden und Abmelden --------------------------------------------------

@router.get("/login")
def login_form(request: Request, next: str = "/"):
    if not auth.enabled():
        return RedirectResponse("/", status_code=303)
    if not auth.has_users():
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": _local_target(next), "error": ""})


@router.post("/login")
def login(request: Request, user: str = Form(""), password: str = Form(""), next: str = Form("/")):
    if not auth.enabled():
        return RedirectResponse("/", status_code=303)
    context = {"next": _local_target(next), "user": user}
    locked = _lockout_text(request)
    if locked:
        return templates.TemplateResponse(request, "login.html", {**context, "error": locked}, status_code=429)
    account = auth.authenticate(user, password)
    if not account:
        auth.record_failure(_address(request))
        return templates.TemplateResponse(request, "login.html", {
            **context, "error": _("Benutzername oder Passwort ist falsch."),
        }, status_code=401)
    auth.record_success(_address(request))
    return _start_session(request, account["id"], next)


# --- Anmelden über OIDC (z. B. Authelia) -------------------------------------

@router.get("/login/oidc")
def oidc_start(request: Request, next: str = "/"):
    if not oidc.available(request.url.hostname):
        return RedirectResponse("/login", status_code=303)
    try:
        url, state = oidc.start(str(request.url_for("oidc_callback")), _local_target(next))
    except oidc.OIDCError as exc:
        log.warning("OIDC: Anbieter nicht erreichbar: %s", exc)
        return _login_page(request, _("Die Anmeldung über {name} ist gerade nicht möglich.",
                                      name=oidc.provider_name()), next, 502)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(oidc.STATE_COOKIE, state, max_age=oidc.STATE_SECONDS, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/login/oidc")
    return response


@router.get("/login/oidc/callback", name="oidc_callback")
def oidc_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    if not oidc.enabled():
        return RedirectResponse("/login", status_code=303)
    name = oidc.provider_name()
    try:
        if error:
            raise oidc.OIDCError(f"Anbieter meldet {error}: {request.query_params.get('error_description', '')}")
        claims, target = oidc.finish(state, request.cookies.get(oidc.STATE_COOKIE), code)
        user_id = oidc.sign_in(claims)
    except oidc.OIDCError as exc:
        log.warning("OIDC-Anmeldung fehlgeschlagen: %s", exc)
        return _login_page(request, _("Die Anmeldung über {name} ist fehlgeschlagen. Bitte erneut versuchen.",
                                      name=name), "/", 401)
    except UserError as exc:
        return _login_page(request, exc.translated(), "/", 403)
    response = _start_session(request, user_id, target)
    response.delete_cookie(oidc.STATE_COOKIE, path="/login/oidc")
    return response


def _login_page(request: Request, error: str, next: str, status_code: int):
    page = "login.html" if auth.has_users() else "setup.html"
    return templates.TemplateResponse(request, page, {"next": _local_target(next), "error": error, "name": ""},
                                      status_code=status_code)


@router.post("/logout")
def logout(request: Request):
    auth.end_session(request.cookies.get(auth.COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE)
    return response


# --- Eigenes Konto ----------------------------------------------------------

def _render_account(request: Request, error: str = "", status_code: int = 200):
    user = current_user(request)
    return templates.TemplateResponse(request, "account.html", {
        "me": user, "sessions": auth.user_sessions(user["id"]), "error": error,
        "has_password": auth.has_password(user["id"]),
    }, status_code=status_code)


@router.get("/account/password")
def account_password_url():
    """Neu laden nach einer Fehlermeldung beim Passwortwechsel: zurück zum Konto."""
    return RedirectResponse("/account", status_code=303)


@router.get("/account")
def account(request: Request):
    user = current_user(request)
    if not user or user["id"] is None:  # AUTH=off: es gibt kein Konto
        return RedirectResponse("/", status_code=303)
    return _render_account(request)


@router.post("/account/password")
def account_password(request: Request, current: str = Form(""), password: str = Form(""), repeat: str = Form("")):
    user = current_user(request)
    if not user or user["id"] is None:
        return RedirectResponse("/", status_code=303)
    locked = _lockout_text(request)
    if locked:
        return _render_account(request, locked, 429)
    if not auth.authenticate(user["name"], current):
        auth.record_failure(_address(request))
        return _render_account(request, _("Das bisherige Passwort ist falsch."), 400)
    try:
        auth.check_password_rules(password, repeat)
        auth.set_password(user["id"], password, keep_session=request.cookies.get(auth.COOKIE))
    except UserError as exc:
        return _render_account(request, exc.translated(), 400)
    return RedirectResponse("/account?password=1", status_code=303)


@router.post("/account/sessions/others")
def account_end_others(request: Request):
    user = current_user(request)
    if user and user["id"] is not None:
        auth.end_other_sessions(user["id"], user.get("session_id"))
    return RedirectResponse("/account?ended=1", status_code=303)


@router.post("/account/sessions/{session_id}/end")
def account_end_session(request: Request, session_id: int):
    user = current_user(request)
    if user and user["id"] is not None:
        auth.end_user_session(user["id"], session_id)
    return RedirectResponse("/account?ended=1", status_code=303)
