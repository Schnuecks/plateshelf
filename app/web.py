# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Gemeinsames für alle Seiten: Vorlagen, Formatierung, Versions- und Anfragehilfen."""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import auth, i18n, oidc, print_history, store
from .i18n import fmt_date, fmt_money, fmt_num, fmt_printer_date, gettext as _, ngettext

BASE = Path(__file__).parent
def _app_version() -> str:
    """Version aus dem Image: bei Releases der Tag (v0.2.0 -> 0.2.0), sonst dev-<Commit>."""
    v = os.environ.get("APP_VERSION", "").strip() or "dev"
    if v.startswith("dev-"):
        return v[:11]  # dev- plus 7 Zeichen der Commit-Kennung, wie auf GitHub
    return v.removeprefix("v")


APP_VERSION = _app_version()
APP_AUTHOR = "Schnuecks"
APP_NAME = "PlateShelf"
APP_REPO = "https://github.com/schnuecks/plateshelf"
APP_LICENSE = "AGPL-3.0"
# Beschriftungen werden beim Anzeigen übersetzt
ROLE_LABELS = {"admin": "Admin", "user": "Benutzer", "viewer": "Nur lesen"}
STATUSES = {"": "Ohne Status", "geplant": "Geplant", "gedruckt": "Gedruckt", "fehlgeschlagen": "Fehlgeschlagen"}


PUBLIC_PREFIXES = ("/static/",)
PUBLIC_PATHS = {"/login", "/login/oidc", "/login/oidc/callback", "/setup", "/healthz", "/appearance"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
NOBODY = {"id": None, "name": "", "role": "admin", "session_id": None}  # mit AUTH=off darf jeder alles
_UNSET = object()


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def current_user(request: Request) -> dict | None:
    """Angemeldeter Benutzer (Sitzungs-Cookie oder Basic Auth), einmal pro Anfrage ermittelt."""
    user = getattr(request.state, "user", _UNSET)
    if user is _UNSET:
        if not auth.enabled():
            user = NOBODY
        else:
            user = (auth.session_user(request.cookies.get(auth.COOKIE))
                    or auth.basic_auth_user(request.headers.get("authorization")))
        request.state.user = user
    return user


def signed_in(request: Request) -> bool:
    return current_user(request) is not None


def can_edit(request: Request) -> bool:
    user = current_user(request)
    return bool(user) and user["role"] in ("admin", "user")


def is_admin(request: Request) -> bool:
    user = current_user(request)
    return bool(user) and user["role"] == "admin"


def allowed(role: str, method: str, path: str) -> bool:
    """Admins dürfen alles, Benutzer alles außer den Einstellungen, „Nur lesen“ nur ansehen.

    Mit „Nur lesen“ bleiben Abmelden, Sprache und Design sowie das eigene Konto erlaubt.
    """
    if role == "admin":
        return True
    if path == "/settings" or path.startswith("/settings/"):
        return False
    if role == "user":
        return True
    if method in SAFE_METHODS:
        return path != "/upload"
    return path in ("/logout", "/appearance") or path == "/account" or path.startswith("/account/")


def device_label(agent: str | None) -> str:
    """„Firefox auf Windows“ aus der User-Agent-Kopfzeile, für die Geräteliste."""
    agent = agent or ""
    browsers = (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                ("Safari/", "Safari"), ("curl/", "curl"), ("python", "Python"))
    systems = (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
               ("Mac OS X", "macOS"), ("Linux", "Linux"))
    browser = next((name for key, name in browsers if key in agent), "")
    system = next((name for key, name in systems if key in agent), "")
    if browser and system:
        return _("{browser} auf {system}", browser=browser, system=system)
    return browser or system or _("Unbekanntes Gerät")


def _local_target(target: str) -> str:
    """Nur Pfade dieser Seite als Ziel nach der Anmeldung, keine fremden Adressen."""
    return target if target.startswith("/") and not target.startswith("//") and "\\" not in target else "/"


def current_theme(request: Request) -> str:
    theme = request.cookies.get("design")
    return theme if theme in i18n.THEMES else i18n.default_theme()


templates = Jinja2Templates(directory=BASE / "templates")


# --- Formatierung (sprachabhängig, siehe i18n) -----------------------------

def fmt_duration(sec) -> str:
    if not sec:
        return "–"
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return _("{d} d {h} h", d=d, h=h)
    if h:
        return f"{h} h {m:02d} min"
    return f"{max(m, 1)} min"


def strand(filaments: list[dict]) -> str:
    """CSS-Verlauf mit harten Kanten: Filamentfarben anteilig nach Gewicht."""
    total = sum(max(f.get("used_g") or 0, 0) for f in filaments)
    if not filaments:
        return "var(--line)"
    if total <= 0:
        total = len(filaments)
        filaments = [{**f, "used_g": 1} for f in filaments]
    stops, pos = [], 0.0
    for f in filaments:
        share = (f.get("used_g") or 0) / total * 100
        stops.append(f"{f['color']} {pos:.2f}% {pos + share:.2f}%")
        pos += share
    return f"linear-gradient(90deg, {', '.join(stops)})"


def fmt_size(n) -> str:
    if not n:
        return "0 MB"
    return fmt_num(n / 1024 / 1024, 1, "MB") if n >= 100 * 1024 else fmt_num(n / 1024, 0, "KB")


def fmt_datetime(ts) -> str:
    return f"{fmt_date(ts)} {time.strftime('%H:%M', time.localtime(ts))}" if ts else "–"


JOB_STATUSES = {"running": "Läuft", "completed": "Fertig", "cancelled": "Abgebrochen", "failed": "Fehlgeschlagen"}
templates.env.globals["JOB_STATUSES"] = JOB_STATUSES
templates.env.filters.update(duration=fmt_duration, num=fmt_num, money=fmt_money, date=fmt_date, strand=strand,
                             datetime=fmt_datetime,
                             printer_date=fmt_printer_date,
                             size=fmt_size)
templates.env.globals["STATUSES"] = STATUSES
templates.env.globals.update(_=_, ngettext=ngettext, lang=i18n.current, theme=current_theme,
                             LANGUAGES=i18n.LANGUAGES)

# Texte, die das JavaScript braucht
JS_TEXTS = [
    "{pct} % übertragen", "Wird eingelesen …", "Übertragung fehlgeschlagen. Bitte erneut versuchen.",
    "G-Code wird aufbereitet: {pct} %", "Druckbahnen werden geladen", "Darstellung wird abgestimmt",
    "Die Vorschau konnte nicht erstellt werden: {error}", "Vorschau nicht verfügbar", "Aufbereitung fehlgeschlagen",
    "Abspielen", "Anhalten", "Geometrie wird aufgebaut: {pct} %", "Vollbild", "Vollbild beenden", "Platte {n}", "Geschwindigkeit", "Volumenstrom", "Lüfter", "Düsentemperatur", "{n} von {total} ({mm} mm)", "Werkzeug {n}",
    "Außenwand", "Innenwand", "Überhangwand", "Infill", "Massives Infill", "Oberfläche", "Unterseite", "Brücke",
    "Lückenfüllung", "Schwebende Hülle", "Stützen", "Stützen-Kontakt", "Stützen-Übergang", "Reinigungsturm",
    "Brim", "Skirt", "Glätten", "Sonstiges",
]


def js_texts() -> dict[str, str]:
    return {t: _(t) for t in JS_TEXTS}


templates.env.globals["js_texts"] = js_texts
templates.env.globals.update(APP_NAME=APP_NAME, APP_VERSION=APP_VERSION, APP_AUTHOR=APP_AUTHOR, APP_REPO=APP_REPO,
                             APP_LICENSE=APP_LICENSE)


def month_label(month: dict, with_year: bool = False, january_year: bool = True) -> str:
    """Kurzer Monatsname für Diagramme, im Januar (oder auf Wunsch immer) mit Jahr."""
    name = i18n.MONTHS[i18n.current()][month["month"] - 1]
    short = name if len(name) <= 4 else name[:3]
    if with_year:
        return f"{name} {month['year']}"
    return f"{short} {month['year']}" if january_year and month["month"] == 1 else short


templates.env.filters["month_label"] = month_label


def live_text(job: dict) -> str:
    """„P2S: Benchy · 45 % · noch 32 min“ für einen laufenden Druck."""
    name = job.get("project_name") or (job.get("remote_name") or "").rsplit("/", 1)[-1]
    parts = [_("{printer}: {name}", printer=job["printer_name"], name=name) if name else job["printer_name"]]
    if job.get("progress") is not None:
        parts.append(f"{fmt_num(job['progress'], 0)} %")
    if job.get("remaining_s"):
        parts.append(_("noch {time}", time=fmt_duration(job["remaining_s"])))
    return " · ".join(parts)


def live_prints() -> list[dict]:
    """Laufende Drucke für die Titelleiste: Text und Ziel des Links."""
    return [{"text": live_text(j), "url": f"/p/{j['project_id']}#history" if j.get("project_id") else "/history",
             "progress": j.get("progress")} for j in print_history.printing_now()]


templates.env.globals["live_prints"] = live_prints


def _asset_version() -> str:
    """Prüfsumme über CSS/JS: ändert sich bei jedem Update, damit Browser neu laden."""
    import hashlib
    h = hashlib.sha1()
    for name in ("style.css", "app.js", "viewer.js", "geometry.js", "geometry-worker.js"):
        path = BASE / "static" / name
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:10]


templates.env.globals["ASSET_V"] = _asset_version()


THEME_LABELS = {"system": "Wie das System", "light": "Hell", "dark": "Dunkel"}
NEXT_THEME = {"system": "light", "light": "dark", "dark": "system"}
ONE_YEAR = 365 * 86400


def language_choice(request: Request) -> str:
    """Ausdrücklich gewählte Sprache dieses Browsers, leer = automatisch."""
    lang = request.cookies.get("sprache")
    return lang if lang in i18n.LANGUAGES else ""


def current_url(request: Request) -> str:
    return request.url.path + (f"?{request.url.query}" if request.url.query else "")


templates.env.globals.update(auth_enabled=auth.enabled, history_enabled=print_history.enabled,
                             current_user=current_user, can_edit=can_edit, is_admin=is_admin,
                             device_label=device_label, ROLE_LABELS=ROLE_LABELS)
def oidc_offered(request: Request) -> bool:
    return oidc.available(request.url.hostname)


templates.env.globals.update(oidc_offered=oidc_offered, oidc_name=oidc.provider_name,
                             oidc_admin_group=oidc.admin_group_set)
templates.env.globals.update(language_choice=language_choice, current_url=current_url, THEME_LABELS=THEME_LABELS,
                             NEXT_THEME=NEXT_THEME)


def _deleted_entry(request: Request) -> dict | None:
    """Eintrag zur Meldung „… in den Papierkorb verschoben“ mit Rückgängig-Knopf."""
    raw = request.query_params.get("deleted", "")
    if not raw.isdigit():
        return None
    with store.connect() as con:
        row = con.execute("SELECT id, kind, label FROM trash WHERE id = ?", (int(raw),)).fetchone()
    return dict(row) if row else None


templates.env.globals["deleted_entry"] = _deleted_entry


def fmt_ago(ts) -> str:
    if not ts:
        return _("noch nie")
    mins = int((time.time() - ts) // 60)
    if mins < 1:
        return _("gerade eben")
    if mins < 60:
        return _("vor {n} min", n=mins)
    return _("vor {n} h", n=mins // 60)


templates.env.filters["ago"] = fmt_ago
