"""Browsertest: Seiten und 3D-Vorschau in echtem Chromium, ohne JavaScript-Fehler.

Läuft in der CI als eigener Job. Lokal nur, wenn Playwright installiert ist:
    pip install playwright && python -m playwright install chromium
"""
import math
import socket
import threading
import time
import zipfile
from pathlib import Path

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
    from test_gcode_file import prusa_gcode
    (library / "Benchy_MK4S.gcode").write_text(prusa_gcode())  # einfacher G-Code aus PrusaSlicer

    from app import main
    store.init_db()
    store.scan(wait=True)
    with store.connect() as con:  # ein Druck mit Notiz, damit Verlauf und Projektseite ihre Formulare zeigen
        con.execute("INSERT INTO print_jobs (printer, printer_name, job_key, local_path, status, started, ended, note, "
                    "rating) VALUES ('S', 'P1S', 'b1', 'Zylinder.gcode.3mf', 'completed', ?, ?, 'Sauber gedruckt', 1)",
                    (time.time() - 3600, time.time()))
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
        gcode_fid = con.execute("SELECT id FROM files WHERE path = 'Benchy_MK4S.gcode'").fetchone()[0]
    yield {"url": f"http://127.0.0.1:{port}", "fid": fid, "pid": pid, "gcode_fid": gcode_fid}
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
    # Die Tests spielen selbst Skripte ein (axe-core, Warten per Funktion); die strenge
    # Content-Security-Policy der App würde das blocken. Sie prüft test_pages_run_under_csp.
    context = browser.new_context(viewport={"width": 1280, "height": 800}, locale="de-DE", bypass_csp=True)
    pg = context.new_page()
    pg.errors = []
    pg.on("pageerror", lambda exc: pg.errors.append(f"Fehler: {exc}"))
    pg.on("console", lambda msg: pg.errors.append(f"Konsole: {msg.text}") if msg.type == "error" else None)
    yield pg
    context.close()


@pytest.mark.parametrize("path", ["/", "/p/{pid}", "/settings", "/history", "/trash", "/upload", "/about"])
def test_pages_without_errors(server, page, path):
    foreign = []
    page.on("request", lambda req: foreign.append(req.url) if not req.url.startswith((server["url"], "data:")) else None)
    response = page.goto(server["url"] + path.format(**server))
    assert response.status == 200
    page.wait_for_load_state("networkidle")
    assert page.locator("footer.site-footer").is_visible()
    assert page.errors == []
    assert foreign == []  # nichts von fremden Servern (Schriften, Skripte …), läuft auch ohne Internet


AXE = Path(__file__).parent / "vendor" / "axe.min.js"  # axe-core 4.13 (MPL-2.0), nur für Tests


@pytest.mark.parametrize("path", ["/", "/p/{pid}", "/settings", "/history", "/statistics", "/trash", "/upload",
                                  "/about", "/view/{fid}/1"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_accessibility(server, page, path, theme):
    """Barrierefreiheit nach WCAG 2 A/AA mit axe-core: keine ernsten oder kritischen Verstöße."""
    page.context.add_cookies([{"name": "design", "value": theme, "url": server["url"]}])
    _check_accessibility(page, server["url"] + path.format(**server))


def test_accessibility_sign_in_pages(server, page, monkeypatch):
    """Einrichtung und Anmeldung sind das Erste, was neue Nutzer sehen."""
    from app import auth, store
    monkeypatch.setenv("AUTH", "on")
    _check_accessibility(page, server["url"] + "/setup")
    user_id = auth.create_user("tester", "geheim-123", "admin")
    try:
        _check_accessibility(page, server["url"] + "/login")
    finally:
        with store.connect() as con:
            con.execute("DELETE FROM users WHERE id = ?", (user_id,))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_browser_bar_matches_top_bar(server, page, theme):
    """Handy: Browser-/Statusleiste in der Farbe der Kopfleiste, auch wenn das Design fest gewählt ist."""
    page.context.add_cookies([{"name": "design", "value": theme, "url": server["url"]}])
    page.goto(server["url"] + "/")
    colors = page.evaluate("""() => ({
        meta: [...document.querySelectorAll('meta[name=theme-color]')].map(m => m.content),
        bar: getComputedStyle(document.querySelector('.topbar')).backgroundColor })""")
    expected = {"light": ("#FFFFFF", "rgb(255, 255, 255)"), "dark": ("#1D2227", "rgb(29, 34, 39)")}[theme]
    assert colors["meta"] == [expected[0]] and colors["bar"] == expected[1], colors
    assert "viewport-fit=cover" in page.get_attribute("meta[name=viewport]", "content")


def _check_accessibility(page, url):
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.add_script_tag(path=str(AXE))
    result = page.evaluate("""async () => {
        const r = await axe.run(document, {runOnly: {type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]}});
        return {passes: r.passes.length, violations: r.violations.filter(v => ["serious", "critical"].includes(v.impact))
            .map(v => `${v.id} (${v.impact}): ${v.help} -> ${v.nodes.slice(0, 3).map(n => n.target.join(" ")).join(" | ")}`)};
    }""")
    assert result["passes"] > 10  # axe hat wirklich geprüft
    assert result["violations"] == [], "\n".join(result["violations"])


def test_fonts_are_served_locally(server, page):
    page.goto(server["url"] + "/")
    page.wait_for_load_state("networkidle")
    loaded = page.evaluate("""async () => {
        await document.fonts.ready;
        return [document.fonts.check('400 16px "Barlow"'), document.fonts.check('600 16px "Barlow Semi Condensed"'),
                [...document.fonts].filter(f => f.status === "loaded").map(f => f.family + " " + f.weight)];
    }""")
    assert loaded[0] and loaded[1], loaded
    assert any(name.startswith('"Barlow Semi Condensed"') or name.startswith("Barlow Semi Condensed") for name in loaded[2]), loaded


def test_viewer_loading_on_phone(server, browser):
    """Handy: Ladeanzeige mittig, 3D-Fläche mit Abstand zur Kopfleiste und nicht breiter als der Bildschirm."""
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="de-DE")
    pg = context.new_page()
    pg.route("**/api/preview/**", lambda route: None)  # nie beantworten: die Ladeanzeige bleibt stehen
    pg.goto(f"{server['url']}/view/{server['fid']}/1", wait_until="domcontentloaded")
    pg.wait_for_selector("#viewer-loading-bar")
    m = pg.evaluate("""() => {
        const r = s => document.querySelector(s).getBoundingClientRect();
        const stage = r('.viewer-stage'), bar = r('#viewer-loading-bar'), top = r('.topbar');
        return {offset: (bar.left + bar.width / 2) - (stage.left + stage.width / 2), gap: stage.top - top.bottom,
                left: stage.left, right: innerWidth - stage.right, wide: document.documentElement.scrollWidth > innerWidth};
    }""")
    context.close()
    assert abs(m["offset"]) <= 1, m
    assert m["gap"] >= 8 and m["left"] >= 8 and m["right"] >= 8 and not m["wide"], m


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
    # Geschwindigkeit: bis 1000× für große Drucke; damit läuft der ganze Druck in Sekunden durch
    speed = page.locator("#play-speed")
    labels = []
    for _ in range(5):
        speed.click()
        labels.append(speed.inner_text())
    assert labels == ["50×", "200×", "1000×", "1×", "10×"], labels
    for _ in range(3):  # wieder 1000×
        speed.click()
    page.locator("#layer-max").fill("0")
    page.locator("#play").click()
    page.wait_for_function("document.getElementById('play').getAttribute('aria-label') === 'Abspielen'", timeout=15_000)
    assert "12 von 12" in page.locator("#layer-label").inner_text()

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


def test_viewer_playback_draws_nothing_outside_the_print(server, page):
    """Beim Abspielen endet das gerade gedruckte Bahnstück an der Düse: früher ragte es bei kleinem
    Fortschritt als langer Strich über die Bahn hinaus, auch weit neben das Modell."""
    import io

    from PIL import Image

    page.set_viewport_size({"width": 1000, "height": 800})
    page.goto(f"{server['url']}/view/{server['fid']}/1")
    page.wait_for_function("document.getElementById('viewer-loading').hidden", timeout=60_000)
    page.locator("#view-top").click()
    page.locator("#layer-single").check()
    page.locator("#layer-max").fill("5")
    page.locator("#layer-max").dispatch_event("input")
    canvas = page.locator("#viewer-canvas")

    def orange_columns(value):
        page.locator("#layer-progress").fill(str(value))
        page.locator("#layer-progress").dispatch_event("input")
        page.wait_for_timeout(60)
        img = Image.open(io.BytesIO(canvas.screenshot())).convert("RGB").resize((200, 160))
        px = img.load()
        return {x for x in range(200) for y in range(160) if px[x, y][0] > 170 and px[x, y][2] < 110}

    full = orange_columns(1000)
    lo, hi = min(full), max(full)
    for value in range(760, 1000, 6):  # Infill-Bahnen am Ende der Schicht, kurz nach ihrem Anfang
        cols = orange_columns(value)
        assert not cols or (min(cols) >= lo - 3 and max(cols) <= hi + 3), value


def test_viewer_for_plain_gcode(server, page):
    page.goto(f"{server['url']}/view/{server['gcode_fid']}/1")
    page.wait_for_function("document.getElementById('viewer-loading').hidden", timeout=60_000)
    assert "2 von 2" in page.locator("#layer-label").inner_text()
    assert page.locator("#viewer").get_attribute("data-bed") == "0.0,-4.0,250.0,210.0"  # Platte des MK4S
    page.locator('[data-mode="feature"]').click()
    assert "Außenwand" in page.locator("#viewer-panel").inner_text()
    assert page.errors == []


def test_printer_type_switches_fields(server, page):
    page.goto(server["url"] + "/settings")
    row = page.locator(".printer-new [data-printer]")
    page.locator(".printer-new").evaluate("d => { d.open = true; }")  # ohne Drucker schon offen
    assert row.locator("[name=serial]").is_visible()
    row.locator("[name=kind]").select_option("moonraker")
    assert not row.locator("[name=serial]").is_visible()
    assert row.get_by_text("API-Schlüssel (optional)").is_visible()
    row.locator("[name=kind]").select_option("prusalink")
    assert not row.locator("[name=serial]").is_visible()
    assert row.get_by_text("Passwort", exact=True).is_visible()
    assert not row.get_by_text("API-Schlüssel (optional)").is_visible()
    row.locator("[name=kind]").select_option("bambu")
    assert row.locator("[name=serial]").is_visible()
    assert not row.get_by_text("Passwort", exact=True).is_visible()
    assert page.errors == []


def test_settings_sections(server, page, monkeypatch):
    """Einstellungen: ein Bereich zur Zeit, Wechsel über die Seitennavigation, Schalter speichert sofort."""
    monkeypatch.delenv("PRINT_HISTORY", raising=False)
    page.goto(server["url"] + "/settings")
    assert page.locator("#printers").is_visible() and not page.locator("#power").is_visible()
    page.locator('.settings-nav a[href="#power"]').click()
    assert page.locator("#power").is_visible() and not page.locator("#printers").is_visible()
    assert page.locator('.settings-nav a[href="#power"]').get_attribute("aria-current") == "page"
    page.locator('.settings-nav a[href="#history"]').click()
    assert not page.locator("[data-autosubmit-button]").is_visible()
    page.locator(".switch").click()  # ausschalten, speichert ohne Knopf
    page.wait_for_url("**/settings?history=1#history")
    assert page.locator("#history").is_visible() and not page.locator("[data-autosubmit]").is_checked()
    page.locator(".switch").click()
    page.wait_for_load_state("load")
    assert page.locator("[data-autosubmit]").is_checked()
    assert page.errors == []


def test_mobile_layout(server, browser):
    """Handy: flache Kopfzeile mit Menüknopf, nichts ragt über den Rand, Vorschau passt in den Bildschirm."""
    context = browser.new_context(viewport={"width": 375, "height": 740}, locale="de-DE", has_touch=True)
    pg = context.new_page()
    errors = []
    pg.on("pageerror", lambda exc: errors.append(str(exc)))
    try:
        for path in ("/", "/p/{pid}", "/history", "/statistics", "/settings", "/trash", "/upload"):
            pg.goto(server["url"] + path.format(**server))
            assert pg.evaluate("document.documentElement.scrollWidth") <= 375, path
            assert pg.locator(".topbar").bounding_box()["height"] < 130, path
        assert not pg.locator("#site-nav").is_visible()
        pg.locator("[data-menu-toggle]").click()
        assert pg.get_by_role("link", name="Einstellungen").is_visible()
        pg.goto(server["url"] + "/")
        assert not pg.locator("select[name=material]").is_visible()
        pg.locator("[data-filter-toggle]").click()
        assert pg.locator("select[name=material]").is_visible()
        pg.goto(f"{server['url']}/view/{server['fid']}/1")
        stage = pg.locator(".viewer-stage").bounding_box()
        assert stage["y"] + stage["height"] < 740  # die Bedienung darunter schaut noch heraus
        assert errors == []
    finally:
        context.close()


def test_long_names_do_not_widen_the_project_page(server, browser):
    """Lange Namen ohne Leerzeichen (Profile, Objekte, Platten) sprengten auf dem Handy die Projektseite."""
    import json
    from app import store
    long = "X" * 90
    with store.connect() as con:
        fid = server["fid"]
        old = con.execute("SELECT name, objects_json FROM plates WHERE file_id = ?", (fid,)).fetchone()
        con.execute("UPDATE plates SET name = ?, objects_json = ? WHERE file_id = ?",
                    (long, json.dumps(["Objekt_" + long]), fid))
    context = browser.new_context(viewport={"width": 390, "height": 844}, locale="de-DE")
    pg = context.new_page()
    try:
        pg.goto(f"{server['url']}/p/{server['pid']}")
        pg.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
        assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    finally:
        context.close()
        with store.connect() as con:
            con.execute("UPDATE plates SET name = ?, objects_json = ? WHERE file_id = ?", (*old, fid))


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
    """Mit Benutzerkonto: Anmeldeseite, Anmelden, zurück zum Ziel, eigenes Konto, Abmelden."""
    from app import auth, store
    monkeypatch.setenv("AUTH", "on")
    user_id = auth.create_user("tester", "geheim-123", "admin")
    try:
        _login_flow(server, page)
    finally:
        with store.connect() as con:  # am letzten Admin vorbei, den delete_user schützt
            con.execute("DELETE FROM users WHERE id = ?", (user_id,))


def _login_flow(server, page):
    page.goto(server["url"] + "/settings")
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
    page.wait_for_url("**/settings")
    page.click('a[href="#users"]')
    assert page.locator("#users .printer-name", has_text="tester").is_visible()
    page.click(".account-link")
    page.wait_for_url("**/account")
    assert page.locator(".device-current").is_visible()
    page.click('form[action="/logout"] button')
    page.wait_for_url("**/login")
    assert page.errors == []


def test_plate_versions_can_be_filtered(server, page):
    """Neu gesliced (andere Schichthöhe): über der Plattenleiste die Fassung wählen, anfangs die neueste."""
    import os
    import shutil
    from make_sample import make

    from app import store
    folder = store.LIBRARY_DIR / "Eevee"
    folder.mkdir()
    plate = {"sec": 600, "layers": 5, "fil": [(1, "#C29572FF", 3.0)]}
    files = {"Eevee_plate_2.gcode.3mf": (2, 1_790_000_000, "0.2"), "Eevee_plate_3.gcode.3mf": (3, 1_790_000_100, "0.2"),
             "Eevee_plate_2 (2).gcode.3mf": (2, 1_790_600_000, "0.16")}
    for name, (idx, stamp, _) in files.items():
        make(folder / name, [{**plate, "idx": idx}])
        os.utime(folder / name, (stamp, stamp))
    try:
        store.scan(wait=True)
        with store.connect() as con:
            for name, (_, _, layer) in files.items():
                con.execute("UPDATE files SET layer_height = ? WHERE path = ?", (layer, f"Eevee/{name}"))
            pid = con.execute("SELECT id FROM projects WHERE name = 'Eevee'").fetchone()[0]
        page.goto(f"{server['url']}/p/{pid}")
        chips = page.locator("[data-version-filter] button")
        assert chips.all_inner_texts() == ["0,16 mm", "0,20 mm", "Alle Fassungen"]
        visible = lambda: page.locator(".plate-strip > li:visible .strip-title").all_inner_texts()  # noqa: E731
        assert visible() == ["Platte 2"]                       # neueste Fassung: nur 0,16 mm
        chips.nth(1).click()
        assert visible() == ["Platte 2", "Platte 3"]
        assert page.locator("section.file:visible").count() == 1
        chips.nth(2).click()
        assert len(visible()) == 3
    finally:
        shutil.rmtree(folder)
        store.scan(wait=True)


def test_pages_run_under_csp(server, browser):
    """Alle Seiten samt 3D-Vorschau laufen mit der Content-Security-Policy ohne einen Verstoß."""
    context = browser.new_context(viewport={"width": 1280, "height": 800}, locale="de-DE")
    pg = context.new_page()
    problems = []
    pg.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: problems.append(str(e)))
    for path in ("/", "/p/{pid}", "/settings", "/history", "/statistics", "/upload", "/about"):
        response = pg.goto(server["url"] + path.format(**server))
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
    pg.goto(f"{server['url']}/view/{server['fid']}/1")
    pg.wait_for_selector("#viewer-panel:not([hidden])", timeout=60_000)   # Importmap, Module, Worker, WebGL
    context.close()
    assert problems == [], problems


def test_pages_notice_when_printer_sync_is_done(server, page):
    """Nach „Von Druckern holen“ oder dem Speichern der Drucker lädt die Seite neu, sobald der Abgleich
    fertig ist, auch ohne neue Dateien (sonst bliebe „wird abgefragt“ stehen bzw. der Status grau)."""
    import time

    from app import printer_import
    before = printer_import.state["last_run"]
    try:
        for path in ("/?printers=1", "/settings?printers=1#printers"):
            page.goto(server["url"] + path)
            printer_import.state["last_run"] = time.time() + 5     # Abgleich durch, nichts Neues
            page.wait_for_url(lambda url: "printers=1" not in url, timeout=15_000)
            printer_import.state["last_run"] = before
    finally:
        printer_import.state["last_run"] = before


def test_overview_tile_shows_print_progress(server, page, monkeypatch):
    """Die Prozente des laufenden Drucks auf der Kachel gehen ohne Neuladen weiter."""
    import time

    from app import print_history, store
    monkeypatch.setenv("PRINT_HISTORY", "true")
    printer = {"name": "P1S", "id": "S", "kind": "bambu"}
    with store.connect() as con:
        con.execute("INSERT INTO printer_files (printer, remote_name, size, remote_date, local_path, printer_name, "
                    "imported) VALUES ('S', 'Zylinder.gcode.3mf', 1, '', 'Zylinder.gcode.3mf', 'P1S', ?)", (time.time(),))
    try:
        print_history.record(printer, "live1", "running", remote_name="Zylinder.gcode.3mf", progress=10)
        page.clock.install()
        page.goto(server["url"] + "/")
        badge = page.locator("[data-live-project]")
        assert "10 %" in badge.inner_text()
        print_history.record(printer, "live1", "running", remote_name="Zylinder.gcode.3mf", progress=55)
        page.clock.run_for(16_000)
        page.wait_for_function("document.querySelector('[data-live-project]').textContent.includes('55')",
                               timeout=5_000)
    finally:
        with store.connect() as con:
            con.execute("DELETE FROM print_jobs WHERE job_key = 'live1'")
            con.execute("DELETE FROM printer_files WHERE remote_name = 'Zylinder.gcode.3mf' AND printer = 'S'")


def test_overview_shows_new_files_by_itself(server, page):
    """Neue Drucke vom Drucker (hier: neue Datei + Einlesen) erscheinen ohne Neuladen von Hand;
    ist gerade etwas markiert, kommt nur ein Hinweis."""
    from make_sample import make

    from app import store
    page.goto(server["url"] + "/?printers=1")                    # nach „Von Druckern holen“: schnell nachsehen
    new = store.LIBRARY_DIR / "Frisch_vom_Drucker.gcode.3mf"
    make(new, [{"idx": 1, "sec": 300, "layers": 3, "fil": [(1, "#3A7BD5FF", 2.0)]}])
    try:
        store.scan(wait=True)
        page.wait_for_selector("text=Frisch vom Drucker", timeout=15_000)
        assert "printers=1" not in page.url                       # Hinweis „wird abgefragt“ verschwindet
        page.goto(server["url"] + "/?printers=1")
        page.locator("[data-bulk-item]").first.check()            # Auswahl darf nicht verloren gehen
        new.unlink()
        store.scan(wait=True)
        page.wait_for_selector("[data-library-changed]:not([hidden])", timeout=15_000)
        assert page.locator("[data-bulk-item]").first.is_checked()
    finally:
        new.unlink(missing_ok=True)
        store.scan(wait=True)
