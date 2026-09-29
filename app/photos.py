# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Eigene Fotos vom fertigen Druck, pro Projekt.

Die Fotos liegen in der Bibliothek unter .photos/<Schlüssel>/, wie der Papierkorb unter
.trash/: Sie gehören zu den eigenen Dateien und werden mit dem Bibliotheksordner
gesichert. Der Schlüssel kommt aus dem festen Gruppenschlüssel des Projekts, deshalb
sind die Fotos nach dem Wiederherstellen aus dem Papierkorb automatisch wieder da.

Beim Hochladen wird jedes Bild gedreht, wie die Kamera es vermerkt hat, auf höchstens
2400 Pixel verkleinert und als JPEG ohne Metadaten gespeichert. So landen keine
GPS-Position und keine Kameradaten in der Bibliothek. Dazu kommt eine kleine Fassung
für die Übersicht.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from . import store
from .i18n import UserError

PHOTO_DIR = ".photos"
MAX_SIDE = 2400
SMALL_SIDE = 640
MAX_UPLOAD_MB = 30
NAME = re.compile(r"^[0-9a-f]{12}(\.small)?\.jpg$")


def folder_for(group_key: str) -> Path:
    return store.LIBRARY_DIR / PHOTO_DIR / hashlib.sha1(group_key.encode()).hexdigest()[:16]


def list_photos(group_key: str) -> list[str]:
    """Namen der Fotos (große Fassung), älteste zuerst."""
    folder = folder_for(group_key)
    if not folder.is_dir():
        return []
    items = [p for p in folder.iterdir() if NAME.match(p.name) and ".small." not in p.name]
    return [p.name for p in sorted(items, key=lambda p: (p.stat().st_mtime, p.name))]


def small_name(name: str) -> str:
    return name.removesuffix(".jpg") + ".small.jpg"


def path_of(group_key: str, name: str) -> Path | None:
    if not NAME.match(name):
        return None
    path = folder_for(group_key) / name
    return path if path.is_file() else None


def save(group_key: str, fh) -> str:
    """Bild prüfen, verkleinern und ohne Metadaten ablegen. Gibt den Namen zurück."""
    head = fh.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(head) > MAX_UPLOAD_MB * 1024 * 1024:
        raise UserError("Das Bild ist größer als {mb} MB.", mb=MAX_UPLOAD_MB)
    try:
        with Image.open(BytesIO(head)) as probe:
            probe.verify()
        img = Image.open(BytesIO(head))
        img = ImageOps.exif_transpose(img)  # wie fotografiert, nicht wie gespeichert
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError, ValueError):
        raise UserError("Dieses Bildformat wird nicht unterstützt (JPEG, PNG oder WebP).") from None
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.getchannel("A"))
        img = background
    folder = folder_for(group_key)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(6)}.jpg"
    big = img.copy()
    big.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    tmp = folder / f".{name}.part"
    big.save(tmp, "JPEG", quality=85, optimize=True, progressive=True)  # ohne exif=…: keine Metadaten
    os.replace(tmp, folder / name)
    img.thumbnail((SMALL_SIDE, SMALL_SIDE), Image.Resampling.LANCZOS)
    img.save(folder / small_name(name), "JPEG", quality=80, optimize=True)
    return name


def delete(group_key: str, name: str) -> None:
    if not NAME.match(name) or ".small." in name:
        return
    folder = folder_for(group_key)
    (folder / name).unlink(missing_ok=True)
    (folder / small_name(name)).unlink(missing_ok=True)
    try:
        folder.rmdir()  # nur wenn leer
    except OSError:
        pass


def remove_all(group_key: str) -> None:
    shutil.rmtree(folder_for(group_key), ignore_errors=True)
