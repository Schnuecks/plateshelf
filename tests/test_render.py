"""Eigene Vorschaubilder aus den Druckbahnen."""
import zipfile

from PIL import Image

from app import render
from app.gcode_preview import convert
from test_gcode_preview import GCODE

RED, GREEN = "#FF2020", "#20E020"


def _preview(tmp_path, gcode=GCODE, name="1_1"):
    src = tmp_path / f"{name}.gcode.3mf"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("Metadata/plate_1.gcode", gcode)
    base = tmp_path / "preview" / name
    convert(src, 1, base)
    return base


def _colors(path):
    with Image.open(path) as img:
        return {c for _n, c in img.convert("RGBA").getcolors(1 << 20)}


def _has(colors, test):
    return any(test(*c) for c in colors)


def test_render_draws_toolpaths_in_filament_colors(tmp_path):
    base = _preview(tmp_path)
    out = tmp_path / "iso.png"
    render.render(base, out, {0: RED, 1: GREEN}, [0, 0, 180, 180])
    with Image.open(out) as img:
        assert img.size == (render.SIZE, render.SIZE) and img.mode == "RGBA"
    colors = _colors(out)
    assert _has(colors, lambda r, g, b, a: a == 255 and g > 150 and r < 90)      # Werkzeug 1: grün
    assert not _has(colors, lambda r, g, b, a: a == 255 and r > 150 and g < 90)  # Stützen (Werkzeug 0) fehlen
    assert (*render.PLATE, 255) in colors                                       # Druckplatte
    render.render(base, out, {0: RED, 1: GREEN}, None, top=True, plate=False)
    with Image.open(out) as img:
        assert img.convert("RGBA").getpixel((0, 0))[3] == 0                     # ohne Platte: durchsichtig
    assert (*render.PLATE, 255) not in _colors(out)


def test_render_leaves_out_the_wipe_tower(tmp_path):
    """Wie die Slicer-Bilder: kein Reinigungsturm, und das Modell bleibt mittig im Bild."""
    tower = GCODE + "; FEATURE: Prime tower\nG1 X200 Y200\nG1 X215 Y200 E1\nG1 X215 Y215 E1\n"
    plain, with_tower = tmp_path / "a.png", tmp_path / "b.png"
    render.render(_preview(tmp_path), plain, {0: RED, 1: GREEN}, None, plate=False)
    render.render(_preview(tmp_path, tower, "2_1"), with_tower, {0: RED, 1: GREEN}, None, plate=False)
    assert plain.read_bytes() == with_tower.read_bytes()


def test_own_thumbnails_replace_the_slicer_image(lib, sample, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main, store
    from app.gcode_preview import status
    from app.projects import load_projects
    sample(lib / "Würfel.gcode.3mf", [{"idx": 1, "sec": 60, "layers": 2, "fil": [(1, "#FF2020FF", 1.0)]}])
    path = lib / "Würfel.gcode.3mf"
    with zipfile.ZipFile(path) as z:
        items = {n: z.read(n) for n in z.namelist()}
    items["Metadata/plate_1.gcode"] = GCODE.encode()
    with zipfile.ZipFile(path, "w") as z:
        for n, d in items.items():
            z.writestr(n, d)
    store.scan(wait=True)
    with store.connect() as con:
        fid = con.execute("SELECT id FROM files").fetchone()[0]
    slicer_png = (store.THUMB_DIR / str(fid) / "plate_1.png").read_bytes()
    with TestClient(main.app) as client:
        assert client.get(f"/thumb/{fid}/plate/1").content == slicer_png      # noch nicht gezeichnet
        assert status(path, 1, store.DATA_DIR / "preview" / f"{fid}_1", background=False)["state"] == "ready"
        render.ensure(fid, 1)
        own = client.get(f"/thumb/{fid}/plate/1")
        assert own.status_code == 200 and own.content != slicer_png
        assert client.get(f"/thumb/{fid}/top/1").status_code == 200           # auch „von oben“
        with store.connect() as con:
            (project,) = load_projects(con)
        (plate,) = project["plates"]
        assert plate["has_top"] and plate["v"].endswith(f"r{render.VERSION}e")  # neue Bildadresse
        page = client.get(f"/p/{project['id']}").text
        assert f"/thumb/{fid}/top/1?v={plate['v']}" in page

        client.post("/settings/thumbnails", data={"vorschaubilder": "slicer"})
        assert client.get(f"/thumb/{fid}/plate/1").content == slicer_png
        client.post("/settings/thumbnails", data={"vorschaubilder": "ohne_platte"})
        render.ensure(fid, 1)
        assert client.get(f"/thumb/{fid}/plate/1").content not in (slicer_png, own.content)
        assert 'value="ohne_platte" checked' in client.get("/settings").text
