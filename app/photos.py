# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Own photos of the finished print, per project.

The photos are stored in the library under .photos/<key>/, like the trash under
.trash/: they belong to the user's own files and are backed up with the library
folder. The key comes from the project's fixed group key, so the photos are
automatically back after restoring from the trash.

On upload, each image is rotated as noted by the camera, scaled down to at most
2400 pixels and saved as JPEG without metadata. That way no GPS position and no
camera data end up in the library. In addition there is a small version
for the overview.
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
    """Names of the photos (large version), oldest first."""
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
    """Check the image, scale it down and store it without metadata. Returns the name."""
    head = fh.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(head) > MAX_UPLOAD_MB * 1024 * 1024:
        raise UserError("Das Bild ist größer als {mb} MB.", mb=MAX_UPLOAD_MB)
    try:
        with Image.open(BytesIO(head)) as probe:
            probe.verify()
        img = Image.open(BytesIO(head))
        img = ImageOps.exif_transpose(img)  # as photographed, not as stored
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
    big.save(tmp, "JPEG", quality=85, optimize=True, progressive=True)  # without exif=…: no metadata
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
        folder.rmdir()  # only if empty
    except OSError:
        pass


def remove_all(group_key: str) -> None:
    shutil.rmtree(folder_for(group_key), ignore_errors=True)
