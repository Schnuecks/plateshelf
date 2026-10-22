import gzip
import json
import zipfile
from array import array

from app.gcode_preview import convert

GCODE = """; HEADER_BLOCK_START
; total layer number: 2
; HEADER_BLOCK_END
M83
G1 X10 Y10 E1 ; Anfahrt vor der ersten Schicht, darf nicht erscheinen
M620 S1A
; CHANGE_LAYER
; Z_HEIGHT: 0.2
; FEATURE: Outer wall
G1 Z0.2
G0 X0 Y0
G1 X10 Y0 E0.5
G1 X20 Y0 E0.5
G1 X20 Y10 E0.5
G1 X30 Y10 E-0.8 ; Rückzug, keine Bahn
; FEATURE: Sparse infill
G1 X30 Y10
G2 X40 Y10 I5 J0 E0.3
; CHANGE_LAYER
; Z_HEIGHT: 0.4
M620 S0A
; FEATURE: Support
G1 X0 Y0
G1 X5 Y5 E0.2
"""


def test_convert(tmp_path):
    src = tmp_path / "t.gcode.3mf"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("Metadata/plate_1.gcode", GCODE)
    meta = convert(src, 1, tmp_path / "out" / "1_1")
    assert meta["layers"] == [0.2, 0.4]
    assert meta["tools"] == [0, 1]
    assert meta["features"] == ["Außenwand", "Infill", "Stützen"]
    assert meta["polys"] == 3  # wall (simplified), arc, support
    saved = json.loads((tmp_path / "out" / "1_1.json").read_text())
    assert saved["version"] == meta["version"]
    raw = gzip.open(tmp_path / "out" / "1_1.bin.gz").read()
    assert len(raw) >= meta["points"] * 4 + meta["polys"] * 20
    # The three points of the outer wall lie on a bend: nothing may be dropped
    assert meta["points"] >= 3 + 3 + 2


METRIC_GCODE = """; HEADER_BLOCK_START
; total layer number: 1
; HEADER_BLOCK_END
M83
M104 S220
M106 P1 S255
; CHANGE_LAYER
; Z_HEIGHT: 0.2
; FEATURE: Outer wall
G1 Z0.2
G0 X0 Y0
G1 X10 Y0 E0.5 F3000
G1 X20 Y0 E0.5 F6000
M106 P1 S128
M106 P2 S255 ; Zusatzlüfter zählt nicht
M104 S230
G1 X30 Y0 E0.5
"""


def _read_preview(base, meta):
    raw = gzip.open(base.with_suffix(".bin.gz")).read()
    n = meta["points"] * 2
    off = n * 2 + (2 if n % 2 else 0) + meta["polys"] * 5 * 4
    return array("H", raw[off:off + meta["points"] * 4 * 2])


def test_metrics_speed_flow_fan_temp(tmp_path):
    src = tmp_path / "m.gcode.3mf"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("Metadata/plate_1.gcode", METRIC_GCODE)
    meta = convert(src, 1, tmp_path / "out" / "1_1")
    # Straight line, but three different path segments: nothing may be merged
    assert meta["points"] == 4
    mets = _read_preview(tmp_path / "out" / "1_1", meta)
    per_point = [tuple(mets[i:i + 4]) for i in range(0, len(mets), 4)]
    # 0.5 mm of filament over 10 mm at 1.75 mm diameter: 0.1203 mm³ per mm of path
    assert per_point[1] == (500, 601, 100, 220)   # 50 mm/s, 6.01 mm³/s, fan 100 %, 220 °C
    assert per_point[2] == (1000, 1203, 100, 220)
    assert per_point[3] == (1000, 1203, 50, 230)  # auxiliary fan P2 does not change the part cooling fan
    assert per_point[0] == per_point[1]            # start point carries the values of the first segment
    r = meta["metrics"]
    assert (r["speed"]["min"], r["speed"]["max"], r["speed"]["unit"]) == (50.0, 100.0, "mm/s")
    assert (r["fan"]["min"], r["fan"]["max"]) == (50, 100)
    assert r["temp"]["max"] == 230 and r["flow"]["scale"] == 100


def test_travels_and_retractions(tmp_path):
    src = tmp_path / "t.gcode.3mf"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("Metadata/plate_1.gcode", GCODE + "G1 E-0.8\nG1 E-0.2 ; zweiter Rückzug direkt danach zählt nicht\n")
    meta = convert(src, 1, tmp_path / "out" / "1_1")
    raw = gzip.open(tmp_path / "out" / "1_1.bin.gz").read()
    n = meta["points"] * 2
    off = n * 2 + (2 if n % 2 else 0) + meta["polys"] * 20 + meta["points"] * 8
    travels = array("H", raw[off:off + meta["travels"] * 10])
    retracts = array("H", raw[off + meta["travels"] * 10:])
    q = lambda v: int((v + 100) * 100 + 0.5)  # noqa: E731
    # Approach in layer 0, retraction with wipe, travel to the start of layer 1
    assert [tuple(travels[i:i + 5]) for i in range(0, len(travels), 5)] == [
        (q(10), q(10), q(0), q(0), 0), (q(20), q(10), q(30), q(10), 0), (q(40), q(10), q(0), q(0), 1)]
    # Retraction during wipe and one at the end; the second one right after it is dropped
    assert [tuple(retracts[i:i + 3]) for i in range(0, len(retracts), 3)] == [(q(20), q(10), 0), (q(5), q(5), 1)]


def test_indented_filament_select(tmp_path):
    # Bambu's P2S start G-code selects the first filament in an indented block
    gcode = """M83
  M620 S3A   ; switch material if AMS exist
  T3
; CHANGE_LAYER
; Z_HEIGHT: 0.2
; FEATURE: Outer wall
G1 Z0.2
G0 X0 Y0
G1 X10 Y0 E0.5
"""
    src = tmp_path / "i.gcode.3mf"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("Metadata/plate_1.gcode", gcode)
    meta = convert(src, 1, tmp_path / "out" / "1_1")
    assert meta["tools"] == [3]
