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
    assert meta["polys"] == 3  # Wand (vereinfacht), Bogen, Stütze
    saved = json.loads((tmp_path / "out" / "1_1.json").read_text())
    assert saved["version"] == meta["version"]
    raw = gzip.open(tmp_path / "out" / "1_1.bin.gz").read()
    assert len(raw) >= meta["points"] * 4 + meta["polys"] * 20
    # Die drei Punkte der Außenwand liegen auf einer Knickstelle: nichts darf wegfallen
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
    # Gerade Linie, aber drei verschiedene Bahnstücke: nichts darf zusammengefasst werden
    assert meta["points"] == 4
    mets = _read_preview(tmp_path / "out" / "1_1", meta)
    per_point = [tuple(mets[i:i + 4]) for i in range(0, len(mets), 4)]
    # 0,5 mm Filament auf 10 mm bei 1,75 mm Durchmesser: 0,1203 mm³ pro mm Bahn
    assert per_point[1] == (500, 601, 100, 220)   # 50 mm/s, 6,01 mm³/s, Lüfter 100 %, 220 °C
    assert per_point[2] == (1000, 1203, 100, 220)
    assert per_point[3] == (1000, 1203, 50, 230)  # Zusatzlüfter P2 ändert den Bauteillüfter nicht
    assert per_point[0] == per_point[1]            # Startpunkt trägt die Werte des ersten Stücks
    r = meta["metrics"]
    assert (r["speed"]["min"], r["speed"]["max"], r["speed"]["unit"]) == (50.0, 100.0, "mm/s")
    assert (r["fan"]["min"], r["fan"]["max"]) == (50, 100)
    assert r["temp"]["max"] == 230 and r["flow"]["scale"] == 100
