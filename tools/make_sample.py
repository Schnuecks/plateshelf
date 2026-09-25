"""Erzeugt nachgebaute Bambu-.gcode.3mf-Dateien zum Testen (kein echter G-Code)."""
import json
import sys
import zipfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None


def png(color, shape="box"):
    if Image is None:
        return b""
    from io import BytesIO
    im = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if shape == "box":
        d.polygon([(256, 120), (400, 200), (400, 340), (256, 420), (112, 340), (112, 200)], fill=color)
        d.polygon([(256, 120), (400, 200), (256, 280), (112, 200)], fill=tuple(min(255, c + 40) for c in color[:3]) + (255,))
    else:
        d.ellipse((150, 150, 362, 362), fill=color)
    buf = BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def make(path, plates, printer="Bambu Lab P1S", colours=("#FFFFFFFF",), types=("PLA",), costs=("24.99",)):
    settings = {
        "printer_model": printer, "layer_height": "0.2", "nozzle_diameter": ["0.4"],
        "curr_bed_type": "Textured PEI Plate", "print_settings_id": "0.20mm Standard @BBL P1P",
        "filament_colour": list(colours), "filament_type": list(types), "filament_cost": list(costs),
        "filament_settings_id": [f"Bambu {t} Basic @BBL P1P" for t in types],
        "sparse_infill_density": "15%", "sparse_infill_pattern": "grid", "wall_loops": "2",
        "enable_support": "0", "nozzle_temperature": ["220"] * len(types), "hot_plate_temp": ["55"] * len(types),
        "brim_type": "auto_brim", "seam_position": "aligned",
    }
    si = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>", "  <header>",
          '    <header_item key="X-BBL-Client-Type" value="slicer"/>',
          '    <header_item key="X-BBL-Client-Version" value="02.02.01.60"/>', "  </header>"]
    ms = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("Metadata/project_settings.config", json.dumps(settings, indent=2))
        for p in plates:
            i = p["idx"]
            weight = sum(f[2] for f in p["fil"])
            si += ["  <plate>", f'    <metadata key="index" value="{i}"/>',
                   '    <metadata key="printer_model_id" value="C12"/>',
                   '    <metadata key="nozzle_diameters" value="0.4"/>',
                   f'    <metadata key="prediction" value="{p["sec"]}"/>',
                   f'    <metadata key="weight" value="{weight:.2f}"/>',
                   '    <metadata key="support_used" value="false"/>']
            for o in p.get("objects", ["Teil.stl"]):
                si.append(f'    <object identify_id="1" name="{o}" skipped="false" />')
            for slot, col, g in p["fil"]:
                t = types[slot - 1]
                si.append(f'    <filament id="{slot}" tray_info_idx="GFA00" type="{t}" color="{col}" used_m="{g / 2.98:.2f}" used_g="{g:.2f}" />')
            si.append("  </plate>")
            ms += ["  <plate>", f'    <metadata key="plater_id" value="{i}"/>',
                   f'    <metadata key="plater_name" value="{p.get("name", "")}"/>', "  </plate>"]
            h, m = divmod(p["sec"] // 60, 60)
            gcode = (f"; HEADER_BLOCK_START\n; BambuStudio 02.02.01.60\n"
                     f"; model printing time: {h}h {m}m 0s; total estimated time: {h}h {m + 4}m 12s\n"
                     f"; total layer number: {p['layers']}\n; total filament weight [g] : {weight:.2f}\n"
                     f"; max_z_height: {p['layers'] * 0.2:.2f}\n; HEADER_BLOCK_END\n"
                     f"; CONFIG_BLOCK_START\n; filament_cost = {','.join(costs)}\n; CONFIG_BLOCK_END\nG28\n")
            z.writestr(f"Metadata/plate_{i}.gcode", gcode)
            rgb = tuple(int(p["fil"][0][1][k:k + 2], 16) for k in (1, 3, 5)) + (255,)
            z.writestr(f"Metadata/plate_{i}.png", png(rgb, p.get("shape", "box")))
            z.writestr(f"Metadata/top_{i}.png", png(rgb, "round"))
        z.writestr("Metadata/slice_info.config", "\n".join(si + ["</config>"]))
        z.writestr("Metadata/model_settings.config", "\n".join(ms + ["</config>"]))


if __name__ == "__main__":
    lib = Path(sys.argv[1] if len(sys.argv) > 1 else "library")
    (lib / "Werkstatt Wandhalter").mkdir(parents=True, exist_ok=True)
    make(lib / "Werkstatt Wandhalter" / "Halter_links.gcode.3mf",
         [{"idx": 1, "sec": 7380, "layers": 210, "fil": [(1, "#1F1F1FFF", 48.3)], "name": "Linke Seite"}],
         colours=("#1F1F1FFF",), types=("PETG",), costs=("22.50",))
    make(lib / "Werkstatt Wandhalter" / "Halter_rechts.gcode.3mf",
         [{"idx": 2, "sec": 7390, "layers": 210, "fil": [(1, "#1F1F1FFF", 48.9)], "name": "Rechte Seite"}],
         colours=("#1F1F1FFF",), types=("PETG",), costs=("22.50",))
    make(lib / "Benchy_plate_1.gcode.3mf",
         [{"idx": 1, "sec": 2940, "layers": 240, "fil": [(1, "#F95D73FF", 11.2), (2, "#FFFFFFFF", 2.1)],
           "objects": ["3DBenchy.stl"], "shape": "round"}],
         colours=("#F95D73FF", "#FFFFFFFF"), types=("PLA", "PLA"), costs=("24.99", "24.99"))
    make(lib / "Benchy_plate_2.gcode.3mf",
         [{"idx": 2, "sec": 3120, "layers": 240, "fil": [(1, "#3A7BD5FF", 12.8)], "objects": ["3DBenchy.stl"]}],
         colours=("#3A7BD5FF",), types=("PLA",), costs=("24.99",))
    make(lib / "Kabelclips.gcode.3mf",
         [{"idx": 1, "sec": 1500, "layers": 40, "fil": [(1, "#F2C94CFF", 6.4)], "objects": ["clip.stl"] * 3},
          {"idx": 2, "sec": 1620, "layers": 40, "fil": [(1, "#F2C94CFF", 3.0), (2, "#2D9C74FF", 3.6)]}],
         colours=("#F2C94CFF", "#2D9C74FF"), types=("PLA", "PLA"), costs=("19.99", "19.99"))
    print("Beispiele in", lib.resolve())
