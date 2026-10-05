# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Sammelt alle übersetzbaren Texte und prüft die Sprachdateien.

    python tools/i18n_extract.py          # fehlende und überzählige Einträge je Sprache
    python tools/i18n_extract.py --list   # alle Texte ausgeben

Quellen: _("…") und ngettext("…", "…", n) in Vorlagen und Python, UserError("…")
und fail("…") in Python sowie die Listen und Beschriftungen in app/web.py und app/projects.py.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Jinja: _('…') bzw. _("…"), ngettext('…', '…', …)
_TPL_CALL = re.compile(r"""\b(?:_|ngettext)\(\s*((?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")(?:\s*,\s*(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"))?)""")
_STR = re.compile(r"""'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)\"""")
PY_FUNCS = {"_", "gettext", "ngettext", "UserError", "fail"}


def _from_templates() -> set[str]:
    found = set()
    for path in (ROOT / "app" / "templates").glob("*.html"):
        for m in _TPL_CALL.finditer(path.read_text(encoding="utf-8")):
            for a, b in _STR.findall(m.group(1)):
                found.add(a or b)
    return found


def _from_python() -> set[str]:
    found = set()
    for path in (ROOT / "app").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name not in PY_FUNCS:
                continue
            count = 2 if name == "ngettext" else 1
            for arg in node.args[:count]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.add(arg.value)
    return found


def _from_tables() -> set[str]:
    from app import main, printer_import, web  # noqa: F401  (Beschriftungen und Listen)
    found = set(main.JS_TEXTS) | set(main.STATUSES.values()) | set(main.THEME_LABELS.values()) | set(main.JOB_STATUSES.values())
    found |= {entry[1] for entry in main.SETTING_LABELS} | set(main.BOOL_WORDS.values())
    found |= {"IP-Adresse", "Seriennummer", "Zugangscode"}  # Feldnamen in Fehlermeldungen
    found |= set(web.ROLE_LABELS.values())
    found |= {text for pair in web.PRICE_SOURCES.values() for text in pair if text}
    return found


def messages() -> set[str]:
    return _from_templates() | _from_python() | _from_tables()


def check() -> dict[str, dict[str, list[str]]]:
    from app.i18n import FALLBACK, LANGUAGES, LOCALE_DIR
    wanted = messages()
    report = {}
    for lang in LANGUAGES:
        if lang == FALLBACK:
            continue
        path = LOCALE_DIR / f"{lang}.json"
        have = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        report[lang] = {
            "missing": sorted(wanted - {k for k, v in have.items() if v}),
            "unused": sorted(set(have) - wanted),
        }
    return report


if __name__ == "__main__":
    if "--list" in sys.argv:
        print(json.dumps(sorted(messages()), ensure_ascii=False, indent=1))
    else:
        for lang, r in check().items():
            print(f"{lang}: {len(r['missing'])} fehlen, {len(r['unused'])} überzählig")
            for m in r["missing"]:
                print("  fehlt:", m)
            for m in r["unused"]:
                print("  überzählig:", m)
