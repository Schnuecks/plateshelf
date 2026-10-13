"""Binary G-code (.bgcode): files are built here according to the libbgcode specification."""
import struct
import zlib

import heatshrink2
import pytest

from app import bgcode, gcode_file, store
from app.gcode_preview import convert
from test_gcode_file import PRUSA_BODY, qoi

MP_CHARS = {c: i for i, c in enumerate("0123456789. \nGX")}


def meatpack(text: str, nospace: bool = True) -> bytes:
    """MeatPack as in the binarizer: packed, optionally without spaces in command lines."""
    table = dict(MP_CHARS)
    if nospace:
        del table[" "]
        table["E"] = 11
        lines = [ln if ln.startswith(";") else ln.replace(" ", "") for ln in text.split("\n")]
        text = "\n".join(lines)
    out = bytearray(b"\xff\xff\xfb" + (b"\xff\xff\xf7" if nospace else b""))
    i = 0
    while i < len(text):
        c1 = text[i]
        if c1 == "\n":  # after the line end the second nibble is just padding
            out.append(table["\n"])
            i += 1
            continue
        c2 = text[i + 1] if i + 1 < len(text) else "\n"
        code1, code2 = table.get(c1, 15), table.get(c2, 15)
        out.append(code1 | code2 << 4)
        out += bytes(c for c, code in ((c1, code1), (c2, code2)) if code == 15 for c in [ord(c)])
        i += 2
    return bytes(out)


def block(btype, data, comp=0, params=b"\x00\x00", crc=True):
    if comp == 0:
        packed = data
    elif comp == 1:
        packed = zlib.compress(data)
    else:
        packed = heatshrink2.compress(data, window_sz2=11 if comp == 2 else 12, lookahead_sz2=4)
    head = struct.pack("<HHI", btype, comp, len(data)) + (struct.pack("<I", len(packed)) if comp else b"")
    body = head + params + packed
    return body + (struct.pack("<I", zlib.crc32(body)) if crc else b"")


def ini(values: dict) -> bytes:
    return "".join(f"{k}={v}\n" for k, v in values.items()).encode()


def make_bgcode(path, gcode: str, *, comp=3, encoding=2, nospace=True, blocks_of=400):
    thumb = qoi(4, 2, [(255, 128, 0, 255)] * 8)
    parts = [b"GCDE" + struct.pack("<IH", 1, 1),
             block(bgcode.FILE_META, ini({"Producer": "PrusaSlicer 2.8.1+win64"}), comp=1),
             block(bgcode.PRINTER_META, ini({
                 "printer_model": "MK4S", "filament_type": "PETG", "nozzle_diameter": "0.4",
                 "extruder_colour": '""', "max_layer_z": "0.40",
                 "objects_info": '{"objects":[{"name":"Clip.stl","polygon":[[1,2]]},{"name":"Clip.stl","polygon":[]}]}',
             }), comp=1),
             block(bgcode.THUMBNAIL, thumb, params=struct.pack("<HHH", 2, 4, 2)),
             block(bgcode.PRINT_META, ini({
                 "filament used [mm]": "2345.60", "filament used [g]": "6.95",
                 "estimated printing time (normal mode)": "38m 12s",
             }), comp=1),
             block(bgcode.SLICER_META, ini({
                 "filament_colour": "#FF8000", "filament_type": "PETG", "filament_cost": "30",
                 "filament_diameter": "1.75", "filament_settings_id": '"Prusament PETG"',
                 "layer_height": "0.2", "nozzle_diameter": "0.4", "printer_model": "MK4S",
                 "printer_settings_id": "Original Prusa MK4S HF0.4 nozzle", "fill_density": "20%",
             }), comp=1)]
    lines = gcode.splitlines(keepends=True)
    packer_text = ["".join(lines[i:i + blocks_of]) for i in range(0, len(lines), blocks_of)]
    for n, chunk in enumerate(packer_text):
        data = chunk.encode()
        if encoding in (1, 2):
            data = meatpack(chunk, nospace)
            if n:  # enabling commands only in the first block
                data = data[6 if nospace else 3:]
        parts.append(block(bgcode.GCODE, data, comp=comp, params=struct.pack("<H", encoding)))
    path.write_bytes(b"".join(parts))
    return path


def test_meatpack_roundtrip():
    text = "G1X10.5Y20E.25F1800\n;TYPE:External perimeter\nM106S255\nG1 Z.2\n"
    for nospace in (False, True):
        packed = meatpack(text, nospace)
        expected = text if not nospace else "G1X10.5Y20E.25F1800\n;TYPE:External perimeter\nM106S255\nG1Z.2\n"
        assert bgcode.MeatPack().decode(packed) == expected
    # State is kept across block boundaries, even in the middle of a signal
    packed = meatpack(text)
    mp = bgcode.MeatPack()
    assert mp.decode(packed[:1]) + mp.decode(packed[1:20]) + mp.decode(packed[20:]) == \
        "G1X10.5Y20E.25F1800\n;TYPE:External perimeter\nM106S255\nG1Z.2\n"


def test_read_meta_and_parse(tmp_path):
    path = make_bgcode(tmp_path / "Clip_0.2mm_PETG_MK4S_38m.bgcode", PRUSA_BODY)
    parsed = gcode_file.parse_gcode(path)
    plate = parsed.plates[0]
    assert parsed.slicer == "PrusaSlicer 2.8.1" and parsed.printer == "Original Prusa MK4S"
    assert parsed.nozzle == "0.4" and parsed.layer_height == "0.2"
    assert plate.prediction_s == 38 * 60 + 12 and plate.max_z == 0.4
    (fil,) = plate.filaments
    assert (fil.type, fil.color, fil.used_g, fil.used_m, fil.name, fil.cost_per_kg) == (
        "PETG", "#FF8000", 6.95, 2.35, "Prusament PETG", 30.0)
    assert plate.objects == ["Clip", "Clip"]
    assert plate.thumb.startswith(b"\x89PNG")  # QOI converted
    assert parsed.settings["fill_density"] == "20%"
    assert gcode_file.diameters(path) == {0: 1.75}


@pytest.mark.parametrize("comp,encoding,nospace", [(3, 2, True), (2, 2, False), (1, 0, False), (0, 0, False)])
def test_preview(tmp_path, comp, encoding, nospace):
    path = make_bgcode(tmp_path / "clip.bgcode", PRUSA_BODY, comp=comp, encoding=encoding, nospace=nospace,
                       blocks_of=7)
    meta = convert(path, 1, tmp_path / "out" / "1_1")
    assert meta["layers"] == [0.2, 0.4]
    assert meta["features"] == ["Außenwand", "Infill", "Innenwand"]
    assert meta["tools"] == [1] and meta["retracts"] == 1
    plain = tmp_path / "clip.gcode"
    plain.write_text(PRUSA_BODY)
    assert convert(plain, 1, tmp_path / "out" / "2_1")["points"] == meta["points"]


def test_preview_without_layer_comments(tmp_path):
    """MeatPack without comments: layers are derived from the print height."""
    body = "\n".join(line for line in PRUSA_BODY.splitlines() if not line.startswith(";")) + "\n"
    path = make_bgcode(tmp_path / "clip.bgcode", body, encoding=1)
    meta = convert(path, 1, tmp_path / "out" / "1_1")
    assert len(meta["layers"]) == 3  # purge line at start height, then two layers


def test_not_a_bgcode(tmp_path):
    path = tmp_path / "x.bgcode"
    path.write_bytes(b"G1 X1\n")
    with pytest.raises(ValueError):
        bgcode.read_meta(path)


def test_scan_and_upload(client, lib, tmp_path):
    src = make_bgcode(tmp_path / "Clip.bgcode", PRUSA_BODY)
    r = client.post("/upload", files=[("files", ("Clip.bgcode", src.read_bytes(), "application/octet-stream"))],
                    follow_redirects=False)
    assert r.status_code == 303 and (lib / "Clip.bgcode").exists()
    with store.connect() as con:
        row = con.execute("SELECT f.id, f.printer, p.name FROM files f JOIN projects p ON p.id = f.project_id").fetchone()
    assert row["printer"] == "Original Prusa MK4S" and row["name"] == "Clip"
    assert client.get(f"/api/preview/{row['id']}/1").status_code == 200
    r = client.post("/upload", files=[("files", ("kaputt.bgcode", b"G1 X1", "application/octet-stream"))])
    assert r.status_code == 400 and "keine gültige G-Code-Datei" in r.text


@pytest.fixture
def client(lib, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setenv("SCAN_INTERVAL", "3600")
    with TestClient(main.app) as c:
        yield c
