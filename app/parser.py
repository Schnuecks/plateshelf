# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Reads Bambu Studio / OrcaSlicer 3MF files (preferably .gcode.3mf).

Sources inside the ZIP container:
  Metadata/slice_info.config     Print time, weight, filaments per plate
  Metadata/project_settings.config  all slicer settings (JSON)
  Metadata/model_settings.config Plate names
  Metadata/plate_N.gcode         Header: layers, max. height, filament costs
  Metadata/plate_N.png / top_N.png  Thumbnails
"""
from __future__ import annotations

import html
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

GCODE_HEAD_BYTES = 512 * 1024
# Limits for what is read from a 3MF into memory: a small, highly compressed file could
# otherwise unpack to gigabytes (zip bomb). The plate G-code is only streamed.
MAX_MEMBER = 32 * 1024 * 1024       # one settings file or the model metadata
MAX_IMAGE = 10 * 1024 * 1024        # one preview image or photo
MAX_TOTAL = 256 * 1024 * 1024       # everything read from one file


class TooLarge(ValueError):
    """A part of the file unpacks to more than allowed (possibly a zip bomb)."""


def read_member(z: zipfile.ZipFile, name: str, limit: int = MAX_MEMBER) -> bytes:
    """Content of one ZIP entry, at most limit bytes; the size in the header can lie, so
    the read itself is capped too."""
    info = z.getinfo(name)
    if info.file_size > limit:
        raise TooLarge(f"{name}: {info.file_size} Byte (höchstens {limit})")
    with z.open(info) as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise TooLarge(f"{name}: mehr als {limit} Byte")
    return data

# Fallback in case project_settings.config is missing
PRINTER_MODEL_IDS = {
    "BL-P001": "Bambu Lab X1 Carbon",
    "BL-P002": "Bambu Lab X1",
    "C13": "Bambu Lab X1E",
    "C11": "Bambu Lab P1P",
    "C12": "Bambu Lab P1S",
    "N1": "Bambu Lab A1 mini",
    "N2S": "Bambu Lab A1",
    "O1D": "Bambu Lab H2D",
    "N7": "Bambu Lab P2S",
}


@dataclass
class Filament:
    slot: int
    type: str = ""
    color: str = "#888888"
    used_g: float = 0.0
    used_m: float = 0.0
    name: str = ""
    cost_per_kg: float | None = None

    @property
    def cost(self) -> float | None:
        if self.cost_per_kg is None:
            return None
        return self.used_g / 1000.0 * self.cost_per_kg


@dataclass
class Plate:
    index: int
    name: str = ""
    prediction_s: int | None = None
    weight_g: float | None = None
    layers: int | None = None
    max_z: float | None = None
    filaments: list[Filament] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    support_used: bool | None = None
    sliced: bool = False
    thumb: bytes | None = None
    top: bytes | None = None

    @property
    def cost(self) -> float | None:
        costs = [f.cost for f in self.filaments if f.cost is not None]
        return sum(costs) if costs else None


@dataclass
class ParsedFile:
    sliced: bool = False
    slicer: str = ""
    printer: str = ""
    nozzle: str = ""
    layer_height: str = ""
    bed_type: str = ""
    process: str = ""
    settings: dict = field(default_factory=dict)
    plates: list[Plate] = field(default_factory=list)
    model: dict = field(default_factory=dict)          # MakerWorld/designer data
    photos: list[tuple[str, bytes]] = field(default_factory=list)  # (extension, image data)


MODEL_KEYS = {
    "Title": "title", "Designer": "designer", "License": "license",
    "ProfileTitle": "profile_title", "ProfileUserName": "profile_user",
    "Description": "description", "Origin": "origin", "CreationDate": "created",
}
PHOTO_EXT = (".webp", ".jpg", ".jpeg", ".png")


def _html_to_text(raw: str) -> str:
    """Designer description is double-escaped HTML: convert to readable text."""
    text = raw
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = re.sub(r"<boostme>.*?</boostme>", "", text, flags=re.I | re.S)
    text = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "• ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [ln.strip() for ln in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _model_meta(z: zipfile.ZipFile, name: str) -> dict:
    """Designer data from the model file. It can also contain the whole mesh (hundreds of MB),
    so it is read as a stream and only up to the start of the meshes (<resources>)."""
    out: dict = {}
    with z.open(name) as raw:
        fh = _Capped(raw, MAX_MEMBER)
        try:
            for event, el in ET.iterparse(fh, events=("start", "end")):
                tag = el.tag.rsplit("}", 1)[-1]
                if event == "start" and tag == "resources":
                    break
                if event == "end" and tag == "metadata":
                    key = MODEL_KEYS.get(el.get("name", ""))
                    val = (el.text or "").strip()
                    if key and val and val != "[]":
                        out[key] = _html_to_text(val) if key == "description" else html.unescape(val)
                    el.clear()
        except ET.ParseError:
            pass
    return out


class _Capped:
    """File-like wrapper that fails after limit bytes."""

    def __init__(self, fh, limit: int):
        self.fh, self.left = fh, limit

    def read(self, n: int = -1) -> bytes:
        data = self.fh.read(self.left + 1 if n is None or n < 0 else min(n, self.left + 1))
        self.left -= len(data)
        if self.left < 0:
            raise TooLarge("Modelldatei: Metadaten zu groß")
        return data


def _norm_color(value: str | None) -> str:
    if not value:
        return "#888888"
    v = value.strip()
    if not v.startswith("#"):
        v = "#" + v
    if len(v) == 9:  # #RRGGBBAA
        v = v[:7]
    return v.upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", v) else "#888888"


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip().rstrip("%").replace(",", "."))
    except ValueError:
        return None


def _first(value):
    if isinstance(value, list):
        return value[0] if value else ""
    return value if value is not None else ""


def _list(value) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [v.strip() for v in str(value).split(",")]


def parse_duration(text: str) -> int | None:
    """'1d 2h 3m 4s' -> seconds."""
    total, found = 0, False
    for num, unit in re.findall(r"(\d+)\s*([dhms])", text):
        found = True
        total += int(num) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total if found else None


def _meta(el: ET.Element) -> dict[str, str]:
    return {m.get("key"): m.get("value", "") for m in el.findall("metadata")}


def _parse_gcode_head(raw: bytes) -> dict:
    text = raw.decode("utf-8", errors="replace")
    out: dict = {}
    if m := re.search(r"total layer number:\s*(\d+)", text):
        out["layers"] = int(m.group(1))
    if m := re.search(r"max_z_height:\s*([\d.]+)", text):
        out["max_z"] = float(m.group(1))
    if m := re.search(r"total estimated time:\s*([^;\n]+)", text):
        out["prediction_s"] = parse_duration(m.group(1))
    if m := re.search(r"^;\s*filament_cost\s*=\s*(.+)$", text, re.M):
        out["filament_cost"] = [_to_float(v) for v in m.group(1).split(",")]
    if m := re.search(r"^;\s*(BambuStudio|OrcaSlicer)\s+([\d.]+)", text, re.M):
        out["slicer"] = f"{m.group(1)} {m.group(2)}"
    return out


def parse_3mf(path: str | Path) -> ParsedFile:
    result = ParsedFile()
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        budget = [MAX_TOTAL]

        def read(name: str, limit: int = MAX_MEMBER) -> bytes | None:
            if name not in names:
                return None
            data = read_member(z, name, min(limit, budget[0]))
            budget[0] -= len(data)
            return data

        # --- Settings ------------------------------------------------------
        if raw := read("Metadata/project_settings.config"):
            try:
                result.settings = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                result.settings = {}
        s = result.settings

        result.printer = str(s.get("printer_model") or "")
        result.nozzle = str(_first(s.get("nozzle_diameter")) or "")
        result.layer_height = str(s.get("layer_height") or "")
        result.bed_type = str(s.get("curr_bed_type") or "")
        result.process = str(s.get("print_settings_id") or "")

        colours = _list(s.get("filament_colour"))
        types = _list(s.get("filament_type"))
        fil_names = _list(s.get("filament_settings_id"))
        costs = [_to_float(c) for c in _list(s.get("filament_cost"))]

        # --- Plate names ----------------------------------------------------
        plate_names: dict[int, str] = {}
        if raw := read("Metadata/model_settings.config"):
            try:
                root = ET.fromstring(raw)
                for p in root.findall("plate"):
                    md = _meta(p)
                    if md.get("plater_id", "").isdigit():
                        plate_names[int(md["plater_id"])] = md.get("plater_name", "")
            except ET.ParseError:
                pass

        # --- Slice info -----------------------------------------------------
        plates: dict[int, Plate] = {}
        printer_model_id = ""
        if raw := read("Metadata/slice_info.config"):
            try:
                root = ET.fromstring(raw)
                for h in root.iter("header_item"):
                    if h.get("key") == "X-BBL-Client-Version":
                        result.slicer = f"Bambu Studio {h.get('value', '')}"
                for p in root.findall("plate"):
                    md = _meta(p)
                    idx = int(md.get("index", "0") or 0)
                    if not idx:
                        continue
                    printer_model_id = printer_model_id or md.get("printer_model_id", "")
                    if not result.nozzle:
                        result.nozzle = md.get("nozzle_diameters", "")
                    pred = _to_float(md.get("prediction"))
                    plate = Plate(
                        index=idx,
                        prediction_s=int(pred) if pred is not None else None,
                        weight_g=_to_float(md.get("weight")),
                        support_used=md.get("support_used") == "true" if "support_used" in md else None,
                    )
                    for f in p.findall("filament"):
                        slot = int(f.get("id", "0") or 0)
                        i = slot - 1
                        plate.filaments.append(
                            Filament(
                                slot=slot,
                                type=f.get("type") or (types[i] if 0 <= i < len(types) else ""),
                                color=_norm_color(f.get("color") or (colours[i] if 0 <= i < len(colours) else None)),
                                used_g=_to_float(f.get("used_g")) or 0.0,
                                used_m=_to_float(f.get("used_m")) or 0.0,
                                name=fil_names[i] if 0 <= i < len(fil_names) else "",
                                cost_per_kg=costs[i] if 0 <= i < len(costs) else None,
                            )
                        )
                    plate.objects = [o.get("name", "") for o in p.findall("object")]
                    plate.warnings = [w.get("msg", "") for w in p.findall("warning") if w.get("msg")]
                    plates[idx] = plate
            except ET.ParseError:
                pass

        if not result.printer and printer_model_id:
            result.printer = PRINTER_MODEL_IDS.get(printer_model_id, printer_model_id)

        # Plates that only exist as a thumbnail (unsliced projects)
        for n in names:
            if m := re.fullmatch(r"Metadata/plate_(\d+)\.png", n):
                plates.setdefault(int(m.group(1)), Plate(index=int(m.group(1))))

        # --- G-code header, images, names -----------------------------------
        for idx, plate in plates.items():
            plate.name = plate_names.get(idx, "")
            gname = f"Metadata/plate_{idx}.gcode"
            if gname in names:
                result.sliced = True
                plate.sliced = True
                with z.open(gname) as fh:
                    head = _parse_gcode_head(fh.read(GCODE_HEAD_BYTES))
                plate.layers = head.get("layers")
                plate.max_z = head.get("max_z")
                if plate.prediction_s is None:
                    plate.prediction_s = head.get("prediction_s")
                if not result.slicer and head.get("slicer"):
                    result.slicer = head["slicer"]
                gcosts = head.get("filament_cost") or []
                for f in plate.filaments:
                    if f.cost_per_kg is None and 0 <= f.slot - 1 < len(gcosts):
                        f.cost_per_kg = gcosts[f.slot - 1]
            if plate.weight_g is None and plate.filaments:
                plate.weight_g = sum(f.used_g for f in plate.filaments)
            plate.thumb = read(f"Metadata/plate_{idx}.png", MAX_IMAGE)
            plate.top = read(f"Metadata/top_{idx}.png", MAX_IMAGE)

        # --- Designer data and photos (MakerWorld downloads) --------------
        if "3D/3dmodel.model" in names:
            result.model = _model_meta(z, "3D/3dmodel.model")
        seen_sizes = set()
        for n in sorted(names):
            if n.startswith("Auxiliaries/Model Pictures/") and n.lower().endswith(PHOTO_EXT):
                data = read(n, MAX_IMAGE)
                if len(data) in seen_sizes:
                    continue
                seen_sizes.add(len(data))
                result.photos.append((Path(n).suffix.lower(), data))
                if len(result.photos) >= 8:
                    break

        if plates and any(p.prediction_s for p in plates.values()):
            result.sliced = True
        result.plates = [plates[k] for k in sorted(plates)]
    return result
