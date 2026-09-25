# Druckarchiv

Ein selbstgehostetes Archiv für deine 3D-Drucke aus Bambu Studio. Das Druckarchiv
liest `.gcode.3mf`-Dateien aus und zeigt sie so übersichtlich an, wie du es aus dem
Slicer kennst: Platten mit Vorschaubildern, Druckzeit, Gewicht, Filamente mit Farbe
und Kosten, die wichtigsten Druckeinstellungen und eine 3D-Vorschau der Druckbahnen.

## Funktionen

- **Übersicht** aller Projekte mit Suche und Filtern nach Material, Drucker, Status und Tags
- **Projektseite** mit Plattenleiste, Vorschaubildern (schräg und von oben), Druckzeit,
  Gewicht, Schichten, Höhe, Filamenten pro Slot und Materialkosten
- **Angaben von MakerWorld**, sofern in der Datei enthalten: Titel, Designer, Lizenz,
  Druckprofil, Beschreibung und Foto des gedruckten Modells
- **3D-Vorschau** der Druckbahnen, eingefärbt nach Filament, Linientyp oder einfarbig,
  mit Schichtregler
- **Automatische Projekte** aus Ordnern oder Dateinamen, zusätzlich manuell zuordenbar
- **Upload** per Drag & Drop und **Papierkorb** statt endgültigem Löschen
- **Direktimport von Bambu-Druckern** per FTPS, auch ohne LAN-only-Modus
- **Notizen, Tags, Status und Quelle** pro Projekt

## Schnellstart

Voraussetzung ist Docker mit Compose.

```bash
mkdir druckarchiv && cd druckarchiv
curl -O https://raw.githubusercontent.com/schnuecks/druckarchiv/main/compose.yaml
curl -o .env https://raw.githubusercontent.com/schnuecks/druckarchiv/main/.env.example
mkdir -p library data
docker compose up -d
```

Anschließend ist das Druckarchiv unter `http://<server>:8000` erreichbar. Lege deine
`.gcode.3mf`-Dateien in den Ordner `library`, sie erscheinen nach spätestens zwei
Minuten. Sofort geht es über „Bibliothek neu einlesen“ in der Übersicht.

Die Ordner `library` und `data` müssen für den Benutzer beschreibbar sein, unter dem
der Container läuft (`PUID`/`PGID` in der `.env`, Standard 1000). Andernfalls startet
der Container mit dem Fehler „unable to open database file“.

### Aktualisieren

```bash
docker compose pull && docker compose up -d
```

Beim Start wird die Datenbank bei Bedarf automatisch auf den neuen Stand gebracht.

### Hinter einem Reverse-Proxy

Das Druckarchiv lauscht im Container auf Port 8000 und braucht keine besonderen
Einstellungen am Proxy. Ein Beispiel für Traefik (statt `ports:` in der Compose):

```yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.druckarchiv.rule=Host(`druckarchiv.example.com`)
      - traefik.http.services.druckarchiv.loadbalancer.server.port=8000
```

Das Druckarchiv hat keine eigene Anmeldung. Wenn es außerhalb deines Heimnetzes
erreichbar sein soll, schütze es mit einer Anmeldung am Proxy (z. B. Authelia oder
Basic Auth), denn jeder mit Zugriff kann Dateien hochladen und löschen.

## Einstellungen

Alle Einstellungen stehen in der `.env`.

| Variable | Standard | Bedeutung |
|---|---|---|
| `DRUCKARCHIV_IMAGE` | `ghcr.io/schnuecks/druckarchiv:latest` | Verwendetes Image |
| `PUID`, `PGID` | 1000 | Benutzer und Gruppe, unter denen der Container läuft |
| `TZ` | Europe/Berlin | Zeitzone |
| `PORT` | 8000 | Port auf dem Host |
| `LIBRARY` | `./library` | Ordner mit den `.gcode.3mf`-Dateien |
| `DATA` | `./data` | Ordner für Datenbank, Vorschaubilder und 3D-Vorschauen |
| `SCAN_INTERVAL` | 120 | Sekunden zwischen zwei Durchläufen durch die Bibliothek |
| `MAX_UPLOAD_MB` | 1024 | Größenlimit pro hochgeladener Datei in MB |
| `PREVIEW_PREBUILD` | true | 3D-Vorschauen nach dem Einlesen im Hintergrund vorbereiten |
| `PRINTERS_JSON` | – | Druckerliste für den Direktimport (optional) |
| `PRINTER_SYNC_INTERVAL` | 600 | Sekunden zwischen zwei Abfragen der Drucker |
| `PRINTER_IMPORT_DIR` | Vom Drucker | Zielordner für importierte Drucke in der Bibliothek |

## Bibliothek und Projekte

Am besten eignen sich **geslicte Dateien** (`.gcode.3mf`), die du in Bambu Studio über
„Exportieren → Geslicte Plattendatei exportieren“ bzw. „Alle geslicten Dateien
exportieren“ erzeugst. Nur sie enthalten Druckzeit, Gewicht und Filamentverbrauch.

So entstehen Projekte:

- **Jeder Unterordner** der Bibliothek ist ein Projekt.
- **Dateien im Hauptordner** werden über ihren Namen zusammengefasst.
  `Benchy_plate_1.gcode.3mf` und `Benchy_plate_2.gcode.3mf` landen beide im Projekt
  „Benchy“. Zusätze wie `_plate_3`, ` (2)`, `_v2`, `_gcode` oder `.stl` werden dabei
  ignoriert.
- **Nichtssagende Namen** wie `(Unsaved)` oder zufällige Kennungen werden durch den
  Titel aus der Datei ersetzt, sofern vorhanden.
- Auf der Projektseite lässt sich jede Datei **von Hand einem anderen Projekt
  zuordnen**. Diese Zuordnung bleibt auch nach einem erneuten Einlesen erhalten.

Exportierst du Platten einzeln, enthält jede Datei zusätzlich die Vorschaubilder aller
anderen Platten. Liegt eine Platte in einer anderen Datei des Projekts geslict vor,
wird ihre Vorschau nicht doppelt angezeigt.

### Kosten

Die angezeigten Kosten setzen sich aus Material und Strom zusammen:

- **Material:** Verbrauch pro Filament mal dem Preis aus dem jeweiligen
  Filamentprofil in Bambu Studio (Feld „Preis“). Ist dort nichts eingetragen, fehlt
  dieser Anteil.
- **Strom:** Druckzeit mal durchschnittlicher Leistung des Druckers mal Strompreis.
  Strompreis und Leistung stellst du unter „Einstellungen“ ein, auf Wunsch für jeden
  Drucker einzeln. Voreingestellt sind 0,35 € pro kWh und 100 Watt.

Die durchschnittliche Leistung misst du am genauesten mit einer Steckdose mit
Verbrauchsanzeige: Energie eines Drucks in Wh geteilt durch die Druckzeit in Stunden.
Verschleiß und Fehldrucke sind nicht enthalten.

## Hochladen und Löschen

Über „Hochladen“ oben rechts lassen sich eine oder mehrere Dateien per Drag & Drop
hochladen, auf Wunsch direkt in einen Projektordner. Vorhandene Dateien werden nie
überschrieben: Identische Dateien werden übersprungen, bei gleichem Namen mit anderem
Inhalt wird „(2)“ angehängt.

Dateien oder ganze Projekte löschst du auf der Projektseite. Sie werden nicht
endgültig entfernt, sondern mit ihrem ursprünglichen Pfad nach
`.papierkorb/<Datum_Uhrzeit>/` innerhalb der Bibliothek verschoben. Zum
Wiederherstellen schiebst du sie einfach zurück. Notizen und Tags eines gelöschten
Projekts gehen dabei allerdings verloren. Den Papierkorb leerst du bei Bedarf selbst.

## 3D-Vorschau

Eine `.gcode.3mf` enthält kein 3D-Modell mehr, sondern den fertigen G-Code. Die
3D-Vorschau zeigt deshalb die tatsächlichen Druckbahnen, ähnlich wie die Vorschau in
Bambu Studio:

- Einfärbung nach Filament, nach Linientyp (Außenwand, Infill, Stützen usw.) oder
  in einer frei wählbaren Farbe, praktisch bei hellen Filamenten
- Einzelne Filamente und Linientypen lassen sich ausblenden
- Schichtregler mit Anzeige der Höhe, auch per Pfeiltasten bedienbar
- Wechsel zwischen den Platten eines Projekts mit den Pfeiltasten links und rechts

Der G-Code wird nach dem Einlesen einmalig im Hintergrund aufbereitet und im
Datenordner zwischengespeichert. Bei sehr großen Drucken (100 MB G-Code) dauert das
etwa 10 bis 20 Sekunden, danach öffnet sich die Vorschau sofort. Beim Öffnen misst der
Viewer kurz die Leistung des Geräts und wählt beim Drehen automatisch die schönste
Darstellung, die noch flüssig läuft. three.js ist im Image enthalten, eine
Internetverbindung ist nicht nötig.

## Direktimport von Bambu-Druckern

Das Druckarchiv kann alle geslicten Drucke selbst von der SD-Karte deiner
Bambu-Drucker holen. Der Zugriff erfolgt per FTPS mit dem Zugangscode des Druckers.
Der LAN-only-Modus ist dafür nicht nötig, sofern die Firmware den Dateizugriff erlaubt.

**Einrichten:** Lege eine `printers.json` an. Das Format entspricht dem von
[bambulab-ams-spoolman-filamentstatus](https://github.com/Rdiger-36/bambulab-ams-spoolman-filamentstatus),
eine vorhandene Datei kannst du also weiterverwenden:

```json
[
  { "name": "P2S", "id": "<Seriennummer>", "code": "<Zugangscode>", "ip": "192.0.2.20" }
]
```

Aktiviere anschließend in der `compose.yaml` die Zeile mit `/config/printers.json` und
setze `PRINTERS_JSON` in der `.env` auf den Pfad der Datei.

**Ablauf:**

- Alle `PRINTER_SYNC_INTERVAL` Sekunden, oder sofort über „Von Druckern holen“ in der
  Übersicht, fragt das Druckarchiv die Dateien auf der SD-Karte ab.
- Neue Drucke landen unter `Vom Drucker/<Druckername>/` in der Bibliothek und werden
  über ihren Namen zu Projekten zusammengefasst.
- Der Drucker speichert ein Projekt immer unter demselben Namen und überschreibt es,
  wenn eine andere Platte gedruckt wird. Das Druckarchiv legt deshalb jede gedruckte
  Platte als eigene Datei ab (`…_plate_2.gcode.3mf`), so geht nichts verloren.
- Jede Datei wird nur einmal geholt. Was du im Archiv löschst, wird nicht erneut
  importiert.
- Die Dateien auf dem Drucker werden nur gelesen, nie verändert oder gelöscht.
- Ist ein Drucker ausgeschaltet oder im Standby, wird er beim nächsten Durchlauf
  erneut abgefragt. Nur ein falscher Zugangscode wird in der Übersicht als Warnung
  angezeigt.

## Daten und Sicherung

- Die Dateien in der Bibliothek werden nie verändert.
- Datenbank (SQLite), Vorschaubilder, 3D-Vorschauen und deine Notizen liegen im
  Datenordner. Für eine Sicherung genügt dieser Ordner, die 3D-Vorschauen darin
  werden bei Bedarf neu erzeugt.
- Neue und geänderte Dateien werden regelmäßig erkannt. Das funktioniert auch auf
  Netzwerkfreigaben (SMB/NFS). Den Datenordner solltest du dagegen lokal ablegen,
  da SQLite auf Netzwerkfreigaben unzuverlässig ist.

## Entwicklung

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q                        # Tests ausführen
python tools/make_sample.py ./library      # Beispieldateien erzeugen
LIBRARY_DIR=./library DATA_DIR=./data uvicorn app.main:app --reload
```

### Neue Version veröffentlichen

1. Änderungen in `CHANGELOG.md` unter einer neuen Versionsnummer eintragen.
2. Tag setzen und hochladen:

   ```bash
   git tag -a v0.2.0 -m "Druckarchiv 0.2.0" && git push --tags
   ```

3. GitHub Actions führt die Tests aus, baut das Image und veröffentlicht es als
   `0.2.0`, `0.2` und `latest`. Anschließend werden ältere Versionen entfernt, die
   letzten fünf bleiben erhalten.

Pushes auf `main` ohne Tag werden getestet und probeweise gebaut, aber nicht
veröffentlicht.

## Lizenz

Copyright (C) 2026 Schnuecks

Das Druckarchiv ist freie Software unter der
[GNU Affero General Public License, Version 3 oder später](LICENSE)
(AGPL-3.0-or-later). Du darfst es nutzen, verändern und weitergeben.

Wenn du eine veränderte Fassung weitergibst oder anderen über ein Netzwerk zur
Nutzung anbietest, musst du ihnen den Quellcode deiner Fassung unter derselben
Lizenz zugänglich machen. Der Link „Quellcode auf GitHub“ im Footer erfüllt das für
diese Fassung; bei einer eigenen Fassung passe ihn auf deinen Quellcode an
(`APP_REPO` in `app/main.py`).

Das Programm wird ohne jede Gewährleistung bereitgestellt, Einzelheiten stehen in
der [Lizenz](LICENSE).

### Drittanbieter

`app/static/vendor/` enthält [three.js](https://threejs.org) und OrbitControls,
beide unter der MIT-Lizenz (siehe `three-LICENSE`). Die MIT-Lizenz ist mit der
AGPL vereinbar.
