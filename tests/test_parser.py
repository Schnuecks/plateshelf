import zipfile

from app.parser import _html_to_text, parse_3mf, parse_duration


def test_parse_duration():
    assert parse_duration("1d 2h 3m 4s") == 93784
    assert parse_duration("45m 10s") == 2710
    assert parse_duration("unbekannt") is None


def test_plates_filaments_costs(tmp_path, sample):
    path = tmp_path / "Kabelclips.gcode.3mf"
    sample(path, [
        {"idx": 1, "sec": 1500, "layers": 40, "fil": [(1, "#F2C94CFF", 6.4)], "objects": ["clip.stl"] * 3, "name": "Clips"},
        {"idx": 2, "sec": 1620, "layers": 40, "fil": [(1, "#F2C94CFF", 3.0), (2, "#2D9C74FF", 3.6)]},
    ], colours=("#F2C94CFF", "#2D9C74FF"), types=("PLA", "PETG"), costs=("20", "25"))
    r = parse_3mf(path)
    assert r.sliced and r.printer == "Bambu Lab P1S" and r.nozzle == "0.4"
    assert r.slicer.startswith("Bambu Studio")
    p1, p2 = r.plates
    assert (p1.index, p1.name, p1.prediction_s, p1.layers) == (1, "Clips", 1500, 40)
    assert p1.objects == ["clip.stl"] * 3
    assert [f.color for f in p2.filaments] == ["#F2C94C", "#2D9C74"]  # Alpha abgeschnitten
    assert [f.type for f in p2.filaments] == ["PLA", "PETG"]
    assert round(p2.weight_g, 2) == 6.6
    assert round(p2.cost, 4) == round(3.0 / 1000 * 20 + 3.6 / 1000 * 25, 4)
    assert p1.thumb and p1.top


def test_preview_only_plates_are_not_sliced(tmp_path, sample):
    """Einzeln exportierte Platte: Vorschaubilder aller Platten, G-Code nur für eine."""
    from make_sample import png
    path = tmp_path / "Yoshi_plate_1.gcode.3mf"
    sample(path, [{"idx": 1, "sec": 100, "layers": 5, "fil": [(1, "#00FF00FF", 1.0)]}])
    with zipfile.ZipFile(path, "a") as z:
        for i in (2, 3):
            z.writestr(f"Metadata/plate_{i}.png", png((0, 200, 0, 255)))
    r = parse_3mf(path)
    assert [(p.index, p.sliced) for p in r.plates] == [(1, True), (2, False), (3, False)]


def test_designer_metadata(tmp_path, sample):
    path = tmp_path / "x.gcode.3mf"
    sample(path, [{"idx": 1, "sec": 60, "layers": 1, "fil": [(1, "#000000FF", 1.0)]}])
    desc = "&lt;p&gt;Hallo&lt;/p&gt;&lt;boostme&gt;Bitte boosten&lt;/boostme&gt;&lt;ul&gt;&lt;li&gt;Eins&lt;/li&gt;&lt;/ul&gt;"
    model = (
        '<?xml version="1.0"?><model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<metadata name="Title">Crochet Yoshi</metadata><metadata name="Designer">NaraaPrints</metadata>'
        f'<metadata name="Description">{desc}</metadata></model>'
    )
    with zipfile.ZipFile(path) as z:
        items = {n: z.read(n) for n in z.namelist()}
    items["3D/3dmodel.model"] = model.encode()
    items["Auxiliaries/Model Pictures/foto.webp"] = b"RIFF0000WEBP"
    with zipfile.ZipFile(path, "w") as z:
        for n, d in items.items():
            z.writestr(n, d)
    r = parse_3mf(path)
    assert r.model["title"] == "Crochet Yoshi" and r.model["designer"] == "NaraaPrints"
    assert "boosten" not in r.model["description"] and "• Eins" in r.model["description"]
    assert r.photos and r.photos[0][0] == ".webp"


def test_html_to_text_double_escaped():
    assert _html_to_text("&amp;lt;b&amp;gt;fett&amp;lt;/b&amp;gt;") == "fett"
