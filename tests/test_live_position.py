"""Live position of a running print in the 3D preview: layer positions, link and status."""
import zipfile

from fastapi.testclient import TestClient

from app import print_history as ph
from app import store
from app.gcode_preview import convert
from test_print_history import BAMBU, imported, jobs, ready  # noqa: F401  (fixture)

KLIPPER = {"name": "Voron", "id": "voron", "kind": "moonraker"}


def test_layer_positions_follow_the_file(tmp_path):
    head = "".join(f"; thumbnail {'A' * 70}\n" for _ in range(500))  # start of the file without paths
    body = "".join(f";LAYER_CHANGE\n;Z:{0.2 * (n + 1):.1f}\nG1 Z{0.2 * (n + 1):.1f}\n"
                   + "".join(f"G1 X{10 + i % 2 * 20} Y{10 + i * 0.1:.1f} E0.5\n" for i in range(50))
                   for n in range(3))
    src = tmp_path / "t.gcode"
    src.write_text("M83\n" + head + body + "G1 X0 Y0\n; end\n")
    meta = convert(src, 1, tmp_path / "out" / "1_1")
    data = src.read_bytes()
    starts, at = [], 0
    while (at := data.find(b";LAYER_CHANGE", at + 1)) > 0:
        starts.append(at / len(data))
    assert len(meta["layer_pos"]) == len(meta["layers"]) + 1  # plus the end of the last path
    for got, want in zip(meta["layer_pos"][:-1], starts, strict=True):
        assert abs(got - want) < 1e-4
    assert meta["layer_pos"][-1] > starts[-1]


def test_layer_positions_inside_a_3mf(tmp_path):
    src = tmp_path / "t.gcode.3mf"
    gcode = "M83\n" + "".join(f"; CHANGE_LAYER\n; Z_HEIGHT: {0.2 * (n + 1):.1f}\nG1 Z{0.2 * (n + 1):.1f}\n"
                              "G1 X0 Y0\nG1 X10 Y0 E0.5\nG1 X10 Y10 E0.5\n" for n in range(4))
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("Metadata/plate_1.gcode", gcode)
    meta = convert(src, 1, tmp_path / "out" / "1_1")
    assert meta["layer_pos"] == sorted(meta["layer_pos"]) and len(meta["layer_pos"]) == 5
    assert abs(meta["layer_pos"][1] - gcode.index("; CHANGE_LAYER", 5) / len(gcode)) < 1e-4


def test_bambu_reports_the_layer_until_the_end(ready):
    imported(ready, BAMBU, "Benchy.gcode.3mf", "From printer/P2S/Benchy_plate_1.gcode.3mf")
    t = ph.BambuTracker(BAMBU)
    t.update({"print": {"gcode_state": "RUNNING", "gcode_file": "/sdcard/Benchy.gcode.3mf", "task_id": "5",
                        "mc_percent": 10, "layer_num": 3}})
    assert jobs()[0]["layer"] == 3
    t.update({"print": {"layer_num": 4}})  # new layer, same percentage: still saved
    assert jobs()[0]["layer"] == 4
    t.update({"print": {"gcode_state": "FINISH", "mc_percent": 100}})
    assert jobs()[0]["layer"] is None


def _running_print(lib, sample, plates=(1,), plate=None):
    path = "Benchy.gcode.3mf"
    sample(lib / path, [{"idx": n, "sec": 60, "layers": 2, "fil": [(1, "#FFFFFFFF", 1.0)]} for n in plates])
    store.scan(wait=True)
    ph.record(KLIPPER, "j1", "running", remote_name="x.gcode", progress=42.0, plate=plate)
    with store.connect() as con:
        con.execute("UPDATE print_jobs SET local_path = ?", (path,))
        fid = con.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()[0]
    return jobs()[0]["id"], fid


def test_link_opens_the_preview_of_the_printed_plate(ready, sample):
    from app.main import app
    job, fid = _running_print(ready, sample)
    client = TestClient(app)
    res = client.get(f"/live/{job}", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == f"/view/{fid}/1?job={job}"
    assert f'data-job="{job}"' in client.get(f"/view/{fid}/1?job={job}").text
    assert 'data-job="' not in client.get(f"/view/{fid}/1?job=999").text  # unknown print: no live hint
    live = client.get(f"/api/jobs/{job}/live").json()
    assert (live["status"], live["progress"], live["fid"], live["plate"]) == ("running", 42.0, fid, 1)
    page = client.get("/history").text
    assert f'href="/live/{job}"' in page


def test_no_link_target_with_several_plates_and_none_reported(ready, sample):
    from app.main import app
    job, fid = _running_print(ready, sample, plates=(1, 2))
    assert ph.view_target(ph.get(job)) is None
    assert TestClient(app).get(f"/live/{job}", follow_redirects=False).status_code == 404
    with store.connect() as con:
        con.execute("UPDATE print_jobs SET plate = 2")
    assert ph.view_target(ph.get(job)) == (fid, 2)
