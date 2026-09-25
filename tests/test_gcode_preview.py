import gzip
import json
import zipfile

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
