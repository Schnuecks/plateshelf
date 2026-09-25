from pathlib import Path

import pytest

from app.store import _MEANINGLESS, group_key_for


@pytest.mark.parametrize("path, name", [
    ("Benchy_plate_1.gcode.3mf", "Benchy"),
    ("Benchy_plate_12.gcode.3mf", "Benchy"),
    ("Yoshi_plate_1_gcode.3mf", "Yoshi"),
    ("Yoshi_plate_1_gcode (2).3mf", "Yoshi"),
    ("Benchy (2).gcode.3mf", "Benchy"),
    ("Custom_tags_Plate 1.gcode.3mf", "Custom tags"),
    ("Vom Drucker/P2S/gingerbread_house_3mf_plate_3.gcode.3mf", "gingerbread house"),
    ("Vom Drucker/P2S/krampus.stl.gcode.3mf", "krampus"),
    ("Werkstatt Wandhalter/Halter_links.gcode.3mf", "Werkstatt Wandhalter"),
])
def test_group_names(path, name):
    assert group_key_for(Path(path))[1] == name


def test_same_group_for_plate_files():
    a = group_key_for(Path("ferris_wheel_3mf_plate_1.gcode.3mf"))
    b = group_key_for(Path("ferris_wheel_3mf_plate_6.gcode.3mf"))
    assert a == b


@pytest.mark.parametrize("name", ["(Unsaved)", "af3ae75a-47e2-4565-9f46-32f9bd4e0ba6", "Untitled"])
def test_meaningless_names(name):
    assert _MEANINGLESS.fullmatch(name)
