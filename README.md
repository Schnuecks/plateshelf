# Druckarchiv

Selbstgehostete Übersicht über deine `.gcode.3mf`-Dateien aus Bambu Studio:
Plattenvorschau (schräg und von oben), Druckzeit, Gewicht, Schichten, Höhe,
Filamente pro Slot mit Farbe und Kosten, dazu die wichtigsten Druckeinstellungen.
Keine Druckerverbindung, kein LAN-Modus nötig.

## Start

    cp .env.example .env      # Pfade anpassen, vor allem LIBRARY
    docker compose up -d --build

Danach unter https://druckarchiv.home.lan (bzw. deinem Traefik-Host).

Das Image kommt aus der GitHub Container Registry. In der `.env` den Namen setzen:

    DRUCKARCHIV_IMAGE=ghcr.io/<benutzer>/druckarchiv:latest

Bei einem privaten Repository einmalig am Server anmelden, mit einem Personal
Access Token (classic) mit der Berechtigung `read:packages`:

    echo <TOKEN> | docker login ghcr.io -u <benutzer> --password-stdin

Aktualisieren:

    docker compose pull && docker compose up -d

## Neue Version veröffentlichen

1. Änderungen in `CHANGELOG.md` unter einer neuen Versionsnummer eintragen.
2. Taggen und pushen:

       git tag -a v0.2.0 -m "Druckarchiv 0.2.0" && git push --tags

3. GitHub Actions testet, baut das Image und lädt es als `0.2.0`, `0.2` und
   `latest` hoch. Danach werden ältere Versionen gelöscht, die letzten 5 bleiben.

Pushes auf `main` ohne Tag werden nur getestet und probeweise gebaut, aber nicht
hochgeladen. So bleiben Speicher und Datenvolumen des kostenlosen Plans geschont.

## Einstellungen

| Variable | Standard | Bedeutung |
|---|---|---|
| `LIBRARY` (Compose) | – | Ordner mit den `.gcode.3mf`, im Container `/library` |
| `SCAN_INTERVAL` | 120 | Sekunden zwischen zwei Durchläufen durch die Bibliothek |
| `MAX_UPLOAD_MB` | 1024 | Größenlimit pro hochgeladener Datei |
| `PREVIEW_PREBUILD` | true | 3D-Vorschauen nach dem Einlesen im Hintergrund vorbereiten |
| `PRINTERS_JSON` (Compose) | – | `printers.json` für den Direktimport, im Container `/config/printers.json` |
| `PRINTER_SYNC_INTERVAL` | 600 | Sekunden zwischen zwei Abfragen der Drucker |
| `PRINTER_IMPORT_DIR` | Vom Drucker | Zielordner für importierte Drucke in der Bibliothek |

## Wie Projekte entstehen

- Jeder Unterordner der Bibliothek ist ein Projekt.
- Dateien direkt im Hauptordner werden über den Namen zusammengefasst:
  `Benchy_plate_1.gcode.3mf` und `Benchy_plate_2.gcode.3mf` landen in „Benchy“.
  Endungen wie `_plate_3`, ` (2)` oder `_v2` werden dafür ignoriert.
- Auf der Projektseite lässt sich jede Datei per Hand einem anderen Projekt
  zuordnen. Diese Zuordnung bleibt auch nach einem Neu-Einlesen bestehen.

## Hochladen

Über „Hochladen“ oben rechts lassen sich eine oder mehrere Dateien per Drag & Drop
hochladen. Sie werden direkt in der Bibliothek gespeichert, optional in einem
Projektordner. Nichts wird überschrieben: identische Dateien werden übersprungen,
bei gleichem Namen mit anderem Inhalt wird „(2)“ angehängt. Dafür muss die
Bibliothek beschreibbar eingebunden sein (kein `:ro` am Volume). Größenlimit
pro Datei über `MAX_UPLOAD_MB` (Standard 1024).

## 3D-Vorschau

Auf der Projektseite öffnet „3D-Vorschau“ die Druckbahnen einer geslicten Platte,
wie in der Vorschau von Bambu Studio: einfärbbar nach Filament oder Linientyp,
einzelne Filamente und Linientypen ausblendbar, Schichtregler (auch per Pfeiltasten).
Eine `.gcode.3mf` enthält kein 3D-Modell mehr, deshalb wird der G-Code angezeigt.

Nach dem Einlesen oder Hochladen wird der G-Code einmalig im Hintergrund aufbereitet
(abschaltbar mit `PREVIEW_PREBUILD=false`, dann erst beim ersten Öffnen) und unter
`${VOL}/druckarchiv/data/preview` zwischengespeichert (bei 100 MB G-Code etwa
10 bis 20 Sekunden, danach sofort). Ändert sich die Datei, wird die Vorschau neu erstellt.
Fast gerade Bahnstücke werden dabei zusammengefasst, damit auch große Drucke im
Browser flüssig bleiben. Die Bahnen werden als beleuchtete Röhren in echter Breite
gezeichnet. Beim Öffnen misst der Viewer kurz die Leistung des Geräts und wählt beim
Drehen automatisch die schönste Darstellung, die noch flüssig läuft (Röhren, Linien
oder ausgedünnte Linien); im Stillstand werden immer die Röhren gezeigt, sofern das
Gerät ein Bild in vertretbarer Zeit schafft. three.js liegt lokal bei, eine Internetverbindung ist nicht nötig.

## Direktimport von den Druckern

Das Druckarchiv holt sich alle geslicten Drucke selbst von der SD-Karte der
Bambu-Drucker, per FTPS mit dem Zugangscode. Der LAN-only-Modus ist dafür nicht
nötig, solange die Firmware den Dateizugriff erlaubt.

Die Drucker kommen aus einer `printers.json` im Format von
bambulab-ams-spoolman-filamentstatus, die nur lesend eingebunden wird:

    [{"name": "P2S", "id": "<Seriennummer>", "code": "<Zugangscode>", "ip": "192.168.1.20"}]

Ablauf:
- Alle `PRINTER_SYNC_INTERVAL` Sekunden (Standard 600) oder per „Von Druckern holen“
  in der Übersicht werden die Dateien im Hauptverzeichnis der SD-Karte abgefragt.
- Neue Dateien landen unter `Vom Drucker/<Druckername>/` und werden über den
  Dateinamen zu Projekten gruppiert (nicht als ein Sammelprojekt).
- Jede Datei wird nur einmal geholt. Löschst du sie im Archiv, kommt sie nicht wieder.
  Ändert sie sich auf dem Drucker (neu gesliced und gedruckt), wird die neue Fassung übernommen.
- Ist ein Drucker aus oder im Standby, wird das in der Übersicht angezeigt und beim
  nächsten Durchlauf erneut versucht.

## Löschen

Auf der Projektseite lassen sich einzelne Dateien („Datei löschen“) oder das ganze
Projekt löschen. Nichts wird endgültig entfernt: Die Dateien wandern nach
`.papierkorb/<Datum_Uhrzeit>/` innerhalb der Bibliothek, mit ihrem ursprünglichen
Pfad. Zum Wiederherstellen einfach zurückschieben, beim nächsten Scan sind sie
wieder da (Notizen und Tags eines gelöschten Projekts sind dann allerdings weg).
Den Papierkorb leerst du bei Bedarf selbst.

## Daten

- Bestehende Dateien werden nie verändert. Gelöschte Dateien landen im Papierkorb-Ordner.
- Metadaten, Vorschaubilder und deine Notizen liegen in `${VOL}/druckarchiv/data`
  (SQLite). Für ein Backup reicht dieser Ordner.
- Neue oder geänderte Dateien werden alle `SCAN_INTERVAL` Sekunden erkannt
  (Polling, funktioniert auch auf SMB/NFS). Sofort geht es über
  „Bibliothek neu einlesen“.

## Kosten

Die Materialkosten kommen aus dem Feld „Preis“ deiner Filamentprofile in
Bambu Studio. Ist dort nichts eingetragen, bleibt die Spalte leer.

## Testen ohne echte Dateien

    python tools/make_sample.py ./library    # erzeugt Beispiel-Dateien

## Entwicklung

    python -m venv .venv && . .venv/bin/activate
    pip install -r requirements-dev.txt
    python -m pytest -q                      # Tests
    python tools/make_sample.py ./library    # Beispieldateien erzeugen
    LIBRARY_DIR=./library DATA_DIR=./data uvicorn app.main:app --reload

Die Tests laufen bei jedem Push automatisch über GitHub Actions; nur wenn sie grün
sind, wird das Docker-Image gebaut (amd64 und arm64).

## Drittanbieter

`app/static/vendor/` enthält three.js und OrbitControls (MIT-Lizenz, siehe
`three-LICENSE`).
