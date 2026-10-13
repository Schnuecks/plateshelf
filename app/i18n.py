# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Translations and language-dependent formats.

The German texts in the code are also the keys. app/locales/<code>.json
maps them to the translation; if an entry is missing, the German text remains.
Placeholders are in curly braces: _("Platte {n}", n=3).

The language applies per request (cookie "sprache", otherwise DEFAULT_LANGUAGE, otherwise
the browser language, otherwise English), the theme per browser (cookie "design", otherwise DEFAULT_THEME).
"""
from __future__ import annotations

import contextvars
import json
import os
import time
from pathlib import Path

LANGUAGES = {
    "de": "Deutsch",
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "it": "Italiano",
    "nl": "Nederlands",
}
FALLBACK = "de"   # language of the texts in the code, also the keys of the translations
DEFAULT = "en"    # if the browser language is not one of the supported ones
THEMES = ("system", "light", "dark")

LOCALE_DIR = Path(__file__).parent / "locales"
_catalogs: dict[str, dict[str, str]] = {}
_lang: contextvars.ContextVar[str] = contextvars.ContextVar("lang", default=FALLBACK)


def _env_choice(name: str, allowed) -> str | None:
    value = os.environ.get(name, "").strip().lower()
    return value if value in allowed else None


def default_language() -> str | None:
    """DEFAULT_LANGUAGE from the environment, otherwise None (= browser language)."""
    return _env_choice("DEFAULT_LANGUAGE", LANGUAGES)


def default_theme() -> str:
    return _env_choice("DEFAULT_THEME", THEMES) or "system"


def catalog(lang: str) -> dict[str, str]:
    if lang not in _catalogs:
        path = LOCALE_DIR / f"{lang}.json"
        _catalogs[lang] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _catalogs[lang]


def set_language(lang: str) -> None:
    _lang.set(lang if lang in LANGUAGES else FALLBACK)


def current() -> str:
    return _lang.get()


def gettext(msg: str, **kwargs) -> str:
    lang = current()
    text = msg if lang == FALLBACK else catalog(lang).get(msg) or msg
    return text.format(**kwargs) if kwargs else text


def ngettext(singular: str, plural: str, n: int, **kwargs) -> str:
    return gettext(singular if n == 1 else plural, n=n, **kwargs)


class UserError(ValueError):
    """Error with a translatable message. str() returns the German text."""

    def __init__(self, msg: str, **kwargs):
        self.msg, self.kwargs = msg, kwargs
        super().__init__(msg.format(**kwargs) if kwargs else msg)

    def translated(self) -> str:
        # Also translate field names like "Zugangscode" in the placeholders
        args = {k: gettext(v) if isinstance(v, str) else v for k, v in self.kwargs.items()}
        return gettext(self.msg, **args)


def pick_language(cookie: str | None, accept: str | None) -> str:
    if cookie in LANGUAGES:
        return cookie
    if env := default_language():
        return env
    # Accept-Language: "fr-CH,fr;q=0.9,en;q=0.8"
    ranked = []
    for part in (accept or "").split(","):
        code, _, q = part.strip().partition(";q=")
        try:
            weight = float(q) if q else 1.0
        except ValueError:
            weight = 0.0
        ranked.append((weight, code.split("-")[0].lower()))
    for _weight, code in sorted(ranked, key=lambda r: -r[0]):
        if code in LANGUAGES:
            return code
    return DEFAULT


# --- Formats -------------------------------------------------------------------

# Thousands and decimal separators, date format, euro before or after the amount
NUMBER_FORMAT = {
    "de": (".", ",", "%d.%m.%Y", False),
    "en": (",", ".", "%Y-%m-%d", True),
    "fr": (" ", ",", "%d/%m/%Y", False),
    "es": (".", ",", "%d/%m/%Y", False),
    "it": (".", ",", "%d/%m/%Y", False),
    "nl": (".", ",", "%d-%m-%Y", True),
}

MONTHS = {
    "de": ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
           "November", "Dezember"),
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"),
    "fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre"),
    "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"),
    "it": ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
           "ottobre", "novembre", "dicembre"),
    "nl": ("januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober",
           "november", "december"),
}
# Day and month in the language's usual order
DAY_MONTH = {
    "de": "{d}. {m}",
    "en": "{m} {d}",
    "fr": "{d} {m}",
    "es": "{d} de {m}",
    "it": "{d} {m}",
    "nl": "{d} {m}",
}
_FTP_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def fmt_num(value, digits: int = 1, unit: str = "") -> str:
    if value is None:
        return "–"
    thousands, decimal, _date, _euro = NUMBER_FORMAT[current()]
    out = f"{value:,.{digits}f}".replace(",", "\0").replace(".", decimal).replace("\0", thousands)
    return f"{out} {unit}" if unit else out


def fmt_money(value) -> str:
    if value is None:
        return "–"
    euro_first = NUMBER_FORMAT[current()][3]
    if 0 < value < 0.005:
        small = fmt_num(0.01, 2)
        return f"< € {small}" if euro_first else f"< {small} €"
    return f"€ {fmt_num(value, 2)}" if euro_first else fmt_num(value, 2) + " €"


def fmt_date(ts) -> str:
    return time.strftime(NUMBER_FORMAT[current()][2], time.localtime(ts)) if ts else "–"


def fmt_printer_date(text) -> str:
    """Date from the printer's file list: 'Aug 22 07:06' -> '22. August 07:06'.

    The printer lists older files with the year instead of the time: 'Nov 29 2025' -> '29. November 2025'.
    """
    parts = str(text or "").split()
    if len(parts) != 3 or parts[0][:3].lower() not in _FTP_MONTHS or not parts[1].isdigit():
        return str(text or "")
    lang = current()
    month = MONTHS[lang][_FTP_MONTHS.index(parts[0][:3].lower())]
    return f"{DAY_MONTH[lang].format(d=int(parts[1]), m=month)} {parts[2]}"
