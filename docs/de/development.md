# Entwicklung

[English](../en/development.md) · **Deutsch** · [← Dokumentation](README.md)

## Lokal starten und testen

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m ruff check                       # Linter, läuft auch in der CI
python -m pytest -q                        # Tests ausführen
python tools/make_sample.py ./library      # Beispieldateien erzeugen
LIBRARY_DIR=./library DATA_DIR=./data uvicorn app.main:app --reload
```

`python tools/make_screenshots.py` erzeugt die Screenshots für README und Dokumentation neu.
`python tools/load_test.py 2000` legt eine Bibliothek mit 2000 Dateien an und misst
Einlesen und die wichtigsten Seiten. `python tools/make_icons.py` erzeugt die Symbole für
den Homescreen aus `app/static/logo.svg`.

Zusätzlich gibt es einen Browsertest, der alle Seiten und die 3D-Vorschau in Chromium
öffnet. Er schlägt fehl bei jedem JavaScript-Fehler, bei jeder Anfrage an einen fremden
Server und bei ernsten oder kritischen Mängeln der Barrierefreiheit (WCAG 2 A/AA, geprüft
mit axe-core aus `tests/vendor/`, im hellen und im dunklen Design):

```bash
pip install -r requirements-browser.txt
python -m playwright install chromium
python -m pytest -q tests/test_browser.py
```

## Übersetzungen

Die deutschen Texte im Code sind zugleich die Schlüssel für die Übersetzungen in
`app/locales/<Sprache>.json`. Nach dem Ändern oder Hinzufügen von Texten zeigt

```bash
python tools/i18n_extract.py
```

je Sprache, was fehlt oder nicht mehr gebraucht wird. Die Tests schlagen fehl, solange
eine Übersetzung fehlt oder ihre Platzhalter (`{n}`, `{name}` …) nicht zum deutschen
Text passen. Eine neue Sprache braucht einen Eintrag in `LANGUAGES`, `NUMBER_FORMAT`,
`MONTHS` und `DAY_MONTH` in `app/i18n.py` sowie eine eigene JSON-Datei.

## Probleme melden

Fehler und Ideen sind als Issue auf GitHub willkommen; die Formulare fragen die Angaben ab,
die meist gebraucht werden. Bei Fehlern öffnest du in PlateShelf „Einstellungen“ →
„Diagnose“, klickst auf „Kopieren“ und fügst den Text ins Issue ein. Er enthält Version,
Plattform, Zahlen zur Bibliothek, die Druckertypen mit ihrem letzten Ergebnis, welche
Einstellungen gesetzt sind und die letzten Warnungen, aber keine Zugangsdaten,
IP-Adressen, Hostnamen oder Druckernamen.

## Tests, Builds und Releases

- Jeder Pull Request und jeder Push auf `main` durchläuft Ruff, die Python-Tests und den
  Browsertest und baut das Image probeweise für amd64 und arm64. Veröffentlicht
  wird dabei nichts.
- Danach prüft Trivy das Image auf bekannte Sicherheitslücken. Kritische und hohe, für die
  es schon eine Korrektur gibt, werden aufgelistet; abgebrochen wird nur bei kritischen,
  damit ein neues „hoch“ im Basis-Image kein Release blockiert, bis Debian nachgezogen hat.
- Ein Release entsteht durch einen Versions-Tag:

  1. Änderungen in `CHANGELOG.md` unter der neuen Versionsnummer eintragen.
  2. Tag setzen und hochladen:

     ```bash
     git tag -a v1.2.3 -m "PlateShelf 1.2.3" && git push --tags
     ```

  3. GitHub Actions testet, baut das Image für alle Plattformen und veröffentlicht es
     als `1.2.3`, `1.2` und `latest`. Anschließend werden ältere Versionen entfernt, die
     letzten fünf Releases bleiben erhalten.

- Dependabot (`.github/dependabot.yml`) prüft wöchentlich Python-Pakete, GitHub Actions
  und das Basisimage und schlägt Aktualisierungen als Pull Request vor. Bekannte
  Schwachstellen werden sofort gemeldet und automatisch als Pull Request behoben. In das
  veröffentlichte Image gelangen solche Aktualisierungen mit dem nächsten Release.
- Die Dokumentation liegt in `docs/` und wird bei jedem Release und auf Knopfdruck
  (Actions → Wiki → Run workflow) ins GitHub-Wiki gespiegelt (`.github/workflows/wiki.yml`,
  `tools/wiki_sync.py`). Bearbeitet wird nur `docs/`: Änderungen im Wiki überschreibt die
  nächste Spiegelung.
