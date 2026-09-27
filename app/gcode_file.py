# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Liest einfache G-Code-Dateien (.gcode) aus PrusaSlicer, OrcaSlicer, Cura und Co.

Die Slicer schreiben ihre Angaben als Kommentare in die Datei:
  Anfang    Vorschaubilder (Base64), bei Orca und Cura auch Druckzeit und Schichten
  Ende      Druckzeit, Filamentverbrauch und alle Einstellungen als "; key = value"
Gelesen werden nur Anfang und Ende, damit auch große Dateien schnell gehen.
Eine G-Code-Datei ergibt eine Platte mit der Nummer 1.
"""
from __future__ import annotations

import base64
import math
import re
import struct
import zlib
from pathlib import Path

from .parser import Filament, ParsedFile, Plate, _norm_color, _to_float, parse_duration

EXTENSIONS = (".gcode",)
HEAD_BYTES = 1024 * 1024
TAIL_BYTES = 512 * 1024
DEFAULT_DENSITY = 1.24   # g/cm³ (PLA), falls der Slicer nur die Länge angibt
DEFAULT_DIAMETER = 1.75  # mm

# Werte pro Extruder, die der Slicer mit ";" oder "," getrennt schreibt
LIST_KEYS = {
    "filament_colour", "extruder_colour", "filament_type", "filament_settings_id", "filament_cost",
    "filament_density", "filament_diameter", "nozzle_diameter", "temperature", "first_layer_temperature",
    "bed_temperature", "first_layer_bed_temperature", "nozzle_temperature", "hot_plate_temp",
}
_CONFIG_LINE = re.compile(r"^; ?([A-Za-z0-9_]+) = (.*)$", re.M)
_THUMB_BEGIN = re.compile(r"^; ?(?:thumbnail(?:_(PNG|JPG|QOI))?|(png|jpg)) begin (\d+)[x ](\d+)", re.I)
_THUMB_END = re.compile(r"^; ?(?:thumbnail(?:_\w+)?|png|jpg) end", re.I)
_SLICER = re.compile(r"^; ?generated (?:by|with) ([A-Za-z][\w .-]*?)[ _-]?(\d+(?:\.\d+)+)", re.I | re.M)
_NOZZLE_SUFFIX = re.compile(r"\s+(?:HF)?\d+(?:\.\d+)?\s*(?:mm\s*)?nozzle$", re.I)
_OBJECT = re.compile(r"^EXCLUDE_OBJECT_DEFINE NAME=['\"]?([^\s'\"]+)", re.M)


def is_gcode(name: str) -> bool:
    return name.lower().endswith(EXTENSIONS)


def _values(value: str) -> list[str]:
    parts = re.split(r"[;,]", value)
    return [p.strip().strip('"').strip() for p in parts]


def read_config(text: str) -> dict:
    """Slicer-Einstellungen aus den "; key = value"-Zeilen."""
    config = {}
    for key, value in _CONFIG_LINE.findall(text):
        value = value.strip()
        config[key] = _values(value) if key in LIST_KEYS else value.strip('"')
    return config


def head_and_tail(path: Path) -> tuple[str, str]:
    with open(path, "rb") as fh:
        head = fh.read(HEAD_BYTES)
        fh.seek(0, 2)
        size = fh.tell()
        tail = b""
        if size > HEAD_BYTES:
            fh.seek(max(HEAD_BYTES, size - TAIL_BYTES))
            tail = fh.read()
    return head.decode("utf-8", "replace"), tail.decode("utf-8", "replace")


def diameters(path: Path) -> dict[int, float]:
    """Filamentdurchmesser je Werkzeug, für die Volumenstrom-Berechnung der Vorschau."""
    head, tail = head_and_tail(path)
    values = read_config(head + "\n" + tail).get("filament_diameter") or []
    out = {}
    for i, v in enumerate(values):
        d = _to_float(v)
        if d and d > 0:
            out[i] = d
    return out


# --- Vorschaubilder -----------------------------------------------------------

def _png(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    stride = width * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def qoi_to_png(data: bytes) -> bytes | None:
    """QOI (Prusa-Vorschaubilder) in PNG umwandeln; Browser können QOI nicht anzeigen."""
    if len(data) < 22 or data[:4] != b"qoif":
        return None
    width, height = struct.unpack(">II", data[4:12])
    if not 0 < width <= 2048 or not 0 < height <= 2048:
        return None
    total = width * height
    out = bytearray(total * 4)
    index = [(0, 0, 0, 0)] * 64
    r, g, b, a = 0, 0, 0, 255
    pos, px, end = 14, 0, len(data) - 8
    run = 0
    while px < total:
        if run:
            run -= 1
        elif pos < end:
            b1 = data[pos]
            pos += 1
            if b1 == 0xFE:
                r, g, b = data[pos], data[pos + 1], data[pos + 2]
                pos += 3
            elif b1 == 0xFF:
                r, g, b, a = data[pos], data[pos + 1], data[pos + 2], data[pos + 3]
                pos += 4
            elif b1 >> 6 == 0:
                r, g, b, a = index[b1]
            elif b1 >> 6 == 1:
                r = (r + ((b1 >> 4) & 3) - 2) & 255
                g = (g + ((b1 >> 2) & 3) - 2) & 255
                b = (b + (b1 & 3) - 2) & 255
            elif b1 >> 6 == 2:
                b2 = data[pos]
                pos += 1
                dg = (b1 & 63) - 32
                r = (r + dg - 8 + ((b2 >> 4) & 15)) & 255
                g = (g + dg) & 255
                b = (b + dg - 8 + (b2 & 15)) & 255
            else:
                run = b1 & 63
            index[(r * 3 + g * 5 + b * 7 + a * 11) % 64] = (r, g, b, a)
        o = px * 4
        out[o], out[o + 1], out[o + 2], out[o + 3] = r, g, b, a
        px += 1
    return _png(width, height, bytes(out))


def thumbnail(text: str) -> bytes | None:
    """Größtes eingebettetes Vorschaubild als PNG oder JPEG."""
    best, best_area = None, 0
    lines = iter(text.splitlines())
    for line in lines:
        m = _THUMB_BEGIN.match(line)
        if not m:
            continue
        kind = (m[1] or m[2] or "PNG").upper()
        area = int(m[3]) * int(m[4])
        chunks = []
        for inner in lines:
            if _THUMB_END.match(inner):
                break
            chunks.append(inner.lstrip("; ").strip())
        if area <= best_area:
            continue
        try:
            data = base64.b64decode("".join(chunks), validate=False)
        except ValueError:
            continue
        if kind == "QOI":
            data = qoi_to_png(data)
        if data and (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8")):
            best, best_area = data, area
    return best


# --- Auslesen -----------------------------------------------------------------

def _first_match(pattern: str, text: str, flags=re.M | re.I):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _numbers(value: str | None) -> list[float]:
    if not value:
        return []
    return [_to_float(v) or 0.0 for v in re.split(r"[;,]", value.replace("m", "")) if v.strip()]


def _printer_name(config: dict, text: str) -> str:
    model = str(config.get("printer_model") or "").strip()
    if " " in model:
        return model
    profile = _NOZZLE_SUFFIX.sub("", str(config.get("printer_settings_id") or "").strip())
    return profile or model or _first_match(r"^;TARGET_MACHINE\.NAME:(.+)$", text) or ""


def _count_layers(path: Path) -> tuple[int | None, float | None]:
    """PrusaSlicer nennt keine Schichtzahl: Schichtwechsel in der ganzen Datei zählen."""
    count, last_z, carry = 0, None, b""
    with open(path, "rb") as fh:
        while block := fh.read(4 * 1024 * 1024):
            block = carry + block
            cut = block.rfind(b"\n") + 1
            block, carry = block[:cut], block[cut:]
            count += block.count(b"\n;LAYER_CHANGE")
            if (i := block.rfind(b"\n;Z:")) >= 0:
                last_z = _to_float(block[i + 4:block.find(b"\n", i + 1)].decode("ascii", "ignore"))
    return (count or None), last_z


def parse_gcode(path: str | Path) -> ParsedFile:
    path = Path(path)
    head, tail = head_and_tail(path)
    text = head + "\n" + tail
    config = read_config(text)
    result = ParsedFile(sliced=True, settings=config)

    if m := _SLICER.search(head):
        name = m[1].replace("_SteamEngine", "").strip()
        result.slicer = f"{name} {m[2]}"
    elif m := re.search(r"^; ?(BambuStudio|OrcaSlicer)\s+([\d.]+)", head, re.M):
        result.slicer = f"{m[1]} {m[2]}"
    result.printer = _printer_name(config, head)
    nozzle = config.get("nozzle_diameter") or []
    result.nozzle = str(nozzle[0] if nozzle else "")
    result.layer_height = str(config.get("layer_height") or _first_match(r"^;Layer height:\s*([\d.]+)", head) or "")
    result.bed_type = str(config.get("curr_bed_type") or "")
    result.process = str(config.get("print_settings_id") or "")

    plate = Plate(index=1, sliced=True)
    duration = (_first_match(r"^; estimated printing time(?: \(normal mode\))?\s*=\s*(.+)$", text)
                or _first_match(r"total estimated time:\s*([^;\n]+)", head))
    if duration:
        plate.prediction_s = parse_duration(duration)
    elif seconds := _first_match(r"^;TIME:(\d+)", head):
        plate.prediction_s = int(seconds)

    layers = _first_match(r"total layer number:\s*(\d+)", head) or _first_match(r"^;LAYER_COUNT:(\d+)", head)
    plate.layers = int(layers) if layers else None
    max_z = _first_match(r"max_z_height:\s*([\d.]+)", head) or _first_match(r"^;MAXZ:([\d.]+)", head)
    plate.max_z = _to_float(max_z)
    if plate.layers is None and ";LAYER_CHANGE" in head:
        plate.layers, last_z = _count_layers(path)
        plate.max_z = plate.max_z or last_z

    grams = _numbers(_first_match(r"^; filament used \[g\]\s*=\s*(.+)$", text))
    lengths = [v / 1000 for v in _numbers(_first_match(r"^; filament used \[mm\]\s*=\s*(.+)$", text))]
    if not lengths:
        lengths = _numbers(_first_match(r"^;Filament used:\s*(.+)$", head))  # Cura, in Metern
    colours = config.get("filament_colour") or config.get("extruder_colour") or []
    types = config.get("filament_type") or []
    names = config.get("filament_settings_id") or []
    costs = [_to_float(c) for c in config.get("filament_cost") or []]
    densities = [_to_float(d) for d in config.get("filament_density") or []]
    dias = [_to_float(d) for d in config.get("filament_diameter") or []]

    def at(values, i, default=None):
        return values[i] if i < len(values) and values[i] not in (None, "") else default

    for i in range(max(len(grams), len(lengths), 1)):
        used_m = at(lengths, i, 0.0)
        used_g = at(grams, i)
        if used_g is None and used_m:
            dia = at(dias, i) or DEFAULT_DIAMETER
            used_g = used_m * 100 * math.pi * (dia / 20) ** 2 * (at(densities, i) or DEFAULT_DENSITY)
        if not used_g and not used_m and i > 0:
            continue  # Extruder oder Slot, der in diesem Druck nicht benutzt wird
        plate.filaments.append(Filament(
            slot=i + 1, type=at(types, i, ""), color=_norm_color(at(colours, i)),
            used_g=round(used_g or 0.0, 2), used_m=round(used_m, 2), name=at(names, i, ""),
            cost_per_kg=at(costs, i),
        ))
    if plate.filaments:
        plate.weight_g = sum(f.used_g for f in plate.filaments) or None

    objects = []
    for name in _OBJECT.findall(head):
        name = re.sub(r"(\.(stl|3mf|obj|step))?_id_\d+_copy_\d+$", "", name, flags=re.I)
        objects.append(name.replace("_", " ").strip() or name)
    plate.objects = objects
    plate.thumb = thumbnail(head)
    result.plates = [plate]
    return result
