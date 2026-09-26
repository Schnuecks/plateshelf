"""Browsertest: Seiten und 3D-Vorschau in echtem Chromium, ohne JavaScript-Fehler.

Läuft in der CI als eigener Job. Lokal nur, wenn Playwright installiert ist:
    pip install playwright && python -m playwright install chromium
"""
import math
import socket
import threading
import time
import zipfile

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")


def _gcode(layers: int = 12) -> str:
    """Kleiner Zylinder mit echten Druckbahnen, Geschwindigkeiten, Lüfter, Rückzügen und Naht."""
    epm = 0.0349
    out = ["; HEADER_BLOCK_START", f"; total layer number: {layers}", "; HEADER_BLOCK_END",
           "M83", "M104 S220", "M106 P1 S0"]
    cx = cy = 128.0
    for k in range(layers):
        z = round(0.2 * (k + 1), 2)
        out += ["; CHANGE_LAYER", f"; Z_HEIGHT: {z}", f"G1 Z{z}"]
        if k == 1:
            out += ["M104 S210", "M106 P1 S255"]
        for r, feat, f in ((10.0, "Outer wall", 3000), (9.58, "Inner wall", 6000)):
            out += [f"; FEATURE: {feat}", "G1 E-0.8 F2400", f"G0 X{cx + r:.3f} Y{cy:.3f}", "G1 E0.8 F2400"]
            last = (cx + r, cy)
            for i in range(1, 61):
                a = 2 * math.pi * i / 60
                x, y = cx + r * math.cos(a), cy + r * math.sin(a)
                out.append(f"G1 X{x:.3f} Y{y:.3f} E{math.dist(last, (x, y)) * epm:.5f} F{f}")
                last = (x, y)
        out.append("; FEATURE: Sparse infill")
        for d in range(-8, 9, 4):
            out += [f"G0 X{cx - 8:.3f} Y{cy + d:.3f}", f"G1 X{cx + 8:.3f} Y{cy + d:.3f} E{16 * epm:.5f} F12000"]
    return "\n".join(out) + "\n"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """PlateShelf mit Beispielbibliothek in einem eigenen Thread."""
    import uvicorn
    from make_sample import make

    from app import store

    root = tmp_path_factory.mktemp("browser")
    library, data = root / "library", root / "data"
    library.mkdir()
    saved = {k: getattr(store, k) for k in ("LIBRARY_DIR", "DATA_DIR", "DB_PATH", "THUMB_DIR", "PREBUILD")}
    store.LIBRARY_DIR, store.DATA_DIR = library, data
    store.DB_PATH, store.THUMB_DIR, store.PREBUILD = data / "plateshelf.sqlite", data / "thumbs", False

    # Datei mit echten Druckbahnen für die 3D-Vorschau
    model = library / "Zylinder.gcode.3mf"
    make(model, [{"idx": 1, "sec": 900, "layers": 12, "fil": [(1, "#E0404AFF", 5.0)]}], colours=("#E0404AFF",))
    with zipfile.ZipFile(model) as z:
        items = {n: z.read(n) for n in z.namelist()}
    items["Metadata/plate_1.gcode"] = _gcode().encode()
    with zipfile.ZipFile(model, "w") as z:
        for n, d in items.items():
            z.writestr(n, d)
    make(library / "Clips.gcode.3mf", [{"idx": 1, "sec": 600, "layers": 5, "fil": [(1, "#F2C94CFF", 3.0)]}])

    from app import main
    store.init_db()
    store.scan(wait=True)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not srv.started:
        if time.time() > deadline:
            raise RuntimeError("Server startet nicht")
        time.sleep(0.05)
    with store.connect() as con:
        fid = con.execute("SELECT id FROM files WHERE path = 'Zylinder.gcode.3mf'").fetchone()[0]
        pid = con.execute("SELECT project_id FROM files WHERE id = ?", (fid,)).fetchone()[0]
    yield {"url": f"http://127.0.0.1:{port}", "fid": fid, "pid": pid}
    srv.should_exit = True
    thread.join(timeout=10)
    for k, v in saved.items():
        setattr(store, k, v)


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        # Software-WebGL, damit die 3D-Vorschau auch ohne Grafikkarte (CI) läuft
        b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """Neue Seite; sammelt JavaScript-Fehler und Fehlermeldungen der Konsole."""
    context = browser.new_context(viewport={"width": 1280, "height": 800}, locale="de-DE")
    pg = context.new_page()
    pg.errors = []
    pg.on("pageerror", lambda exc: pg.errors.append(f"Fehler: {exc}"))
    pg.on("console", lambda msg: pg.errors.append(f"Konsole: {msg.text}") if msg.type == "error" else None)
    yield pg
    context.close()


@pytest.mark.parametrize("path", ["/", "/p/{pid}", "/einstellungen", "/papierkorb", "/upload", "/about"])
def test_pages_without_errors(server, page, path):
    response = page.goto(server["url"] + path.format(**server))
    assert response.status == 200
    page.wait_for_load_state("networkidle")
    assert page.locator("footer.site-footer").is_visible()
    assert page.errors == []


def test_viewer_loads_and_all_controls_work(server, page):
    page.goto(f"{server['url']}/view/{server['fid']}/1")
    # Vorschau fertig: Ladehinweis weg, Bedienfeld da, Schichtanzeige gesetzt.
    # Bei einem JavaScript-Fehler sofort abbrechen und ihn nennen, statt bis zum Zeitlimit zu warten.
    deadline = time.time() + 60
    while page.locator("#viewer-loading").is_visible():
        assert page.errors == [], page.errors
        assert time.time() < deadline, "Vorschau lädt nicht: " + page.locator("#viewer-loading-text").inner_text()
        page.wait_for_timeout(250)
    assert page.locator("#viewer-panel").is_visible()
    assert "12 von 12" in page.locator("#layer-label").inner_text()

    # Alle Einfärbungen, die Daten haben
    for mode in ("feature", "uni", "speed", "flow", "fan", "temp", "tool"):
        button = page.locator(f'[data-mode="{mode}"]')
        assert button.is_enabled(), mode
        button.click()
        assert button.get_attribute("aria-pressed") == "true", mode
    page.locator('[data-mode="speed"]').click()
    assert "mm/s" in page.locator(".metric-legend").inner_text()
    page.locator('[data-mode="tool"]').click()

    # Einblendungen mit Anzahl
    for key in ("travel", "retract", "seam"):
        box = page.locator(f"#show-{key}")
        assert box.is_enabled(), key
        box.check()
    assert page.locator("#show-seam").locator("xpath=..").locator(".extra-count").inner_text() == "12"

    # Schichtregler und „Nur diese Schicht“
    page.locator("#layer-max").fill("5")
    assert "6 von 12" in page.locator("#layer-label").inner_text()
    page.locator("#layer-single").check()
    page.locator("#layer-single").uncheck()

    # Wiedergabe: Leertaste startet, Position läuft, Leertaste stoppt
    page.locator("#viewer-canvas").click()
    page.keyboard.press("Space")
    page.wait_for_function("document.getElementById('play').getAttribute('aria-label') === 'Anhalten'")
    page.wait_for_function("Number(document.getElementById('layer-progress').value) < 1000", timeout=10_000)
    page.keyboard.press("Space")

    # Ansicht, Tiefenschattierung, Bild speichern
    page.locator("#view-top").click()
    page.locator("#ao-toggle").uncheck()
    page.locator("#ao-toggle").check()
    page.locator("#view-reset").click()
    with page.expect_download() as download:
        page.locator("#view-png").click()
    assert download.value.suggested_filename.endswith(".png")

    page.wait_for_timeout(500)  # verzögerte Fehler (z. B. im Zeichenaufruf) mitnehmen
    assert page.errors == []


def test_language_and_theme_from_topbar(server, page):
    page.goto(server["url"] + "/")
    page.locator(".lang-menu summary").click()
    page.locator('.lang-menu button[value="en"]').click()
    page.wait_for_load_state("load")
    assert page.locator("html").get_attribute("lang") == "en"
    assert page.get_by_role("link", name="Settings").is_visible()
    page.locator(".theme-btn").click()  # System -> Hell
    page.wait_for_load_state("load")
    assert page.locator("html").get_attribute("data-theme") == "light"
    assert page.errors == []


def test_login_flow(server, page, monkeypatch):
    """Mit AUTH_USER/AUTH_PASSWORD: Anmeldeseite, Anmelden, zurück zum Ziel, Abmelden."""
    monkeypatch.setenv("AUTH_USER", "tester")
    monkeypatch.setenv("AUTH_PASSWORD", "geheim-123")
    page.goto(server["url"] + "/einstellungen")
    assert "/login" in page.url
    assert page.locator(".tb-right .settings-link").count() == 0  # keine Navigation vor der Anmeldung
    page.fill('input[name="user"]', "tester")
    page.fill('input[name="password"]', "falsch")
    page.click('.login-form button[type="submit"]')
    assert "falsch" in page.locator(".login-form .flash").inner_text()
    # Die Seite kommt absichtlich mit Status 401, das meldet Chrome in der Konsole
    assert all("401" in e for e in page.errors), page.errors
    page.errors.clear()
    page.fill('input[name="password"]', "geheim-123")
    page.click('.login-form button[type="submit"]')
    page.wait_for_url("**/einstellungen")
    page.click('form[action="/logout"] button')
    page.wait_for_url("**/login")
    assert page.errors == []
