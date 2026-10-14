# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Setup of the first account, signing in, signing out and the own account."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import auth, oidc, passkeys
from .i18n import UserError, gettext as _, ngettext
from .web import _local_target, audit, current_user, templates

router = APIRouter()
log = logging.getLogger("plateshelf")


def _address(request: Request) -> str:
    return request.client.host if request.client else "?"


def _start_session(request: Request, user_id: int, target: str, response=None):
    token = auth.new_session(user_id, request.headers.get("user-agent", ""), _address(request))
    response = response or RedirectResponse(_local_target(target), status_code=303)
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return response


def _lockout_text(request: Request, user: str = "") -> str:
    wait = auth.locked_out(_address(request), user)
    if not wait:
        return ""
    minutes = max(1, (wait + 59) // 60)
    return ngettext("Zu viele Fehlversuche. Bitte in {n} Minute erneut versuchen.",
                    "Zu viele Fehlversuche. Bitte in {n} Minuten erneut versuchen.", minutes)


# --- Setup ------------------------------------------------------------------

def _setup_open() -> bool:
    return auth.enabled() and not auth.has_users()


@router.get("/setup")
def setup_form(request: Request):
    if not _setup_open():
        return RedirectResponse("/", status_code=303)
    auth.setup_code()  # make sure one exists (also shown in the log)
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
        audit(request, "setup_failed")
        return templates.TemplateResponse(request, "setup.html", {
            **context, "error": _("Der Einrichtungscode stimmt nicht. Du findest ihn im Protokoll des Containers.")},
            status_code=401)
    try:
        user_id = auth.finish_setup(name, password, repeat)
    except UserError as exc:
        return templates.TemplateResponse(request, "setup.html", {**context, "error": exc.translated()},
                                          status_code=400)
    auth.record_success(_address(request))
    audit(request, "setup", " ".join(name.split()))
    return _start_session(request, user_id, "/")


# --- Sign in and sign out ---------------------------------------------------

@router.get("/login")
def login_form(request: Request, next: str = "/"):
    if not auth.enabled():
        return RedirectResponse("/", status_code=303)
    if not auth.has_users():
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": _local_target(next), "error": ""})


# --- Passkeys (static/passkeys.js sends and expects JSON) ------------------------

def _json_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": _(message)}, status_code=status_code)


@router.post("/login/passkey/options")
def passkey_login_options(request: Request):
    if not auth.enabled():
        return _json_error(passkeys.EXPIRED)
    rp_id, _origin = passkeys.relying_party(request.url)
    return JSONResponse(passkeys.authentication_options(rp_id))


@router.post("/login/passkey")
async def passkey_login(request: Request):
    if not auth.enabled():
        return _json_error(passkeys.EXPIRED)
    if auth.locked_out(_address(request)):
        return JSONResponse({"error": _lockout_text(request)}, status_code=429)
    body = await request.json()
    rp_id, origin = passkeys.relying_party(request.url)
    try:
        user_id = passkeys.authenticate(body.get("token", ""), body.get("credential") or {}, rp_id, origin)
    except passkeys.PasskeyError as exc:
        auth.record_failure(_address(request))
        audit(request, "login_failed", "", "Passkey")
        return _json_error(str(exc), 401)
    auth.record_success(_address(request))
    user = auth.get_user(user_id) or {}
    audit(request, "login", user.get("name", ""), "Passkey")
    target = _local_target(str(body.get("next") or "/"))
    return _start_session(request, user_id, target, JSONResponse({"redirect": target}))


@router.post("/account/passkeys/options")
def passkey_register_options(request: Request):
    user = current_user(request)
    if not user or user["id"] is None:
        return _json_error(passkeys.EXPIRED, 403)
    rp_id, _origin = passkeys.relying_party(request.url)
    try:
        return JSONResponse(passkeys.registration_options(user, rp_id))
    except passkeys.PasskeyError as exc:
        return _json_error(str(exc))


@router.post("/account/passkeys")
async def passkey_register(request: Request):
    user = current_user(request)
    if not user or user["id"] is None:
        return _json_error(passkeys.EXPIRED, 403)
    body = await request.json()
    rp_id, origin = passkeys.relying_party(request.url)
    try:
        name = passkeys.register(user, body.get("token", ""), body.get("credential") or {}, body.get("name", ""),
                                 rp_id, origin)
    except passkeys.PasskeyError as exc:
        return _json_error(str(exc))
    audit(request, "passkey_added", user["name"], name)
    return JSONResponse({"redirect": "/account?passkey=1#passkeys"})


@router.post("/account/passkeys/{passkey_id}/delete")
def passkey_delete(request: Request, passkey_id: int):
    user = current_user(request)
    if user and user["id"] is not None and (name := passkeys.delete(user["id"], passkey_id)) is not None:
        audit(request, "passkey_removed", user["name"], name)
    return RedirectResponse("/account?passkey_removed=1#passkeys", status_code=303)


@router.post("/login")
def login(request: Request, user: str = Form(""), password: str = Form(""), next: str = Form("/")):
    if not auth.enabled():
        return RedirectResponse("/", status_code=303)
    context = {"next": _local_target(next), "user": user}
    if not auth.password_login():
        return templates.TemplateResponse(request, "login.html", {
            **context, "error": _("Die Anmeldung mit Passwort ist abgeschaltet.")}, status_code=403)
    locked = _lockout_text(request, user)
    if locked:
        audit(request, "locked", user)
        return templates.TemplateResponse(request, "login.html", {**context, "error": locked}, status_code=429)
    account = auth.authenticate(user, password)
    if not account:
        auth.record_failure(_address(request), user)
        audit(request, "login_failed", user, "Passwort")
        return templates.TemplateResponse(request, "login.html", {
            **context, "error": _("Benutzername oder Passwort ist falsch."),
        }, status_code=401)
    auth.record_success(_address(request))
    audit(request, "login", account["name"], "Passwort")
    return _start_session(request, account["id"], next)


# --- Sign in via OIDC (e.g. Authelia) ----------------------------------------

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
        claims, target, link_user = oidc.finish(state, request.cookies.get(oidc.STATE_COOKIE), code)
        if link_user is not None:
            return _finish_link(request, claims, link_user, target)
        user_id = oidc.sign_in(claims)
    except oidc.OIDCError as exc:
        log.warning("OIDC-Anmeldung fehlgeschlagen: %s", exc)
        audit(request, "login_failed", "", name)
        return _login_page(request, _("Die Anmeldung über {name} ist fehlgeschlagen. Bitte erneut versuchen.",
                                      name=name), "/", 401)
    except UserError as exc:
        audit(request, "login_failed", "", name)
        return _login_page(request, exc.translated(), "/", 403)
    audit(request, "login", (auth.get_user(user_id) or {}).get("name", ""), name)
    response = _start_session(request, user_id, target)
    response.delete_cookie(oidc.STATE_COOKIE, path="/login/oidc")
    return response


def _finish_link(request: Request, claims: dict, link_user: int, target: str):
    """Return from the provider after „Verbinden“ on the account page."""
    me = current_user(request)
    if not me or me["id"] != link_user:  # signed out or a different account in the meantime
        raise oidc.OIDCError("Verbinden: nicht mehr mit demselben Konto angemeldet")
    try:
        auth.link_external(link_user, oidc.subject(claims))
    except UserError as exc:
        return _render_account(request, exc.translated(), 409)
    audit(request, "sso_linked", me["name"], oidc.provider_name())
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(oidc.STATE_COOKIE, path="/login/oidc")
    return response


@router.post("/account/sso")
def account_link_sso(request: Request):
    """Link the signed-in account with the OIDC provider; the provider then signs the person in."""
    user = current_user(request)
    if not user or user["id"] is None or not oidc.available(request.url.hostname):
        return RedirectResponse("/account", status_code=303)
    try:
        url, state = oidc.start(str(request.url_for("oidc_callback")), "/account?sso=1", link_user=user["id"])
    except oidc.OIDCError as exc:
        log.warning("OIDC: Anbieter nicht erreichbar: %s", exc)
        return _render_account(request, _("Die Anmeldung über {name} ist gerade nicht möglich.",
                                          name=oidc.provider_name()), 502)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(oidc.STATE_COOKIE, state, max_age=oidc.STATE_SECONDS, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/login/oidc")
    return response


def _login_page(request: Request, error: str, next: str, status_code: int):
    page = "login.html" if auth.has_users() else "setup.html"
    return templates.TemplateResponse(request, page, {"next": _local_target(next), "error": error, "name": ""},
                                      status_code=status_code)


@router.post("/logout")
def logout(request: Request):
    user = current_user(request)
    if user and user.get("id") is not None:
        audit(request, "logout", user["name"])
    auth.end_session(request.cookies.get(auth.COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE)
    return response


# --- Own account ------------------------------------------------------------

def _render_account(request: Request, error: str = "", status_code: int = 200):
    user = current_user(request)
    return templates.TemplateResponse(request, "account.html", {
        "me": user, "sessions": auth.user_sessions(user["id"]), "error": error,
        "has_password": auth.has_password(user["id"]), "password_login": auth.password_login(),
        "passkeys": passkeys.for_user(user["id"]), "sso_linked": auth.is_linked(user["id"]),
    }, status_code=status_code)


@router.get("/account/password")
def account_password_url():
    """Reload after an error message when changing the password: back to the account."""
    return RedirectResponse("/account", status_code=303)


@router.get("/account")
def account(request: Request):
    user = current_user(request)
    if not user or user["id"] is None:  # AUTH=off: there is no account
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
        audit(request, "password_failed", user["name"])
        return _render_account(request, _("Das bisherige Passwort ist falsch."), 400)
    try:
        auth.check_password_rules(password, repeat)
        auth.set_password(user["id"], password, keep_session=request.cookies.get(auth.COOKIE))
    except UserError as exc:
        return _render_account(request, exc.translated(), 400)
    audit(request, "password_changed", user["name"])
    return RedirectResponse("/account?password=1", status_code=303)


@router.post("/account/sessions/others")
def account_end_others(request: Request):
    user = current_user(request)
    if user and user["id"] is not None:
        auth.end_other_sessions(user["id"], user.get("session_id"))
        audit(request, "sessions_ended", user["name"])
    return RedirectResponse("/account?ended=1", status_code=303)


@router.post("/account/sessions/{session_id}/end")
def account_end_session(request: Request, session_id: int):
    user = current_user(request)
    if user and user["id"] is not None:
        auth.end_user_session(user["id"], session_id)
    return RedirectResponse("/account?ended=1", status_code=303)
