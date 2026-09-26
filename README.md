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
- **Upload** per Drag & Drop und **Papierkorb** mit Wiederherstellen statt endgültigem Löschen
- **Direktimport von Bambu-Druckern** per FTPS, auch ohne LAN-only-Modus
- **Notizen, Tags, Status und Quelle** pro Projekt
- **Sprachen:** Deutsch, English, Français, Español, Italiano und Nederlands
- **Design** hell, dunkel oder wie das System
- **Kennzahlen als JSON** unter `/api/stats`, z. B. für ein Widget in [Homepage](https://gethomepage.dev)

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
| `TRASH_DAYS` | 30 | Tage, nach denen Einträge im Papierkorb endgültig gelöscht werden (0 = nie) |
| `PRINTER_<n>_NAME`, `_IP`, `_SERIAL`, `_CODE` | – | Drucker für den Direktimport (optional, siehe unten) |
| `PRINTER_SYNC_INTERVAL` | 600 | Sekunden zwischen zwei Abfragen der Drucker |
| `PRINTER_IMPORT_DIR` | Vom Drucker | Zielordner für importierte Drucke in der Bibliothek |
| `DEFAULT_LANGUAGE` | – | Voreingestellte Sprache: `de`, `en`, `fr`, `es`, `it` oder `nl`. Leer: Sprache des Browsers |
| `DEFAULT_THEME` | system | Voreingestelltes Design: `system`, `light` oder `dark` |

### Sprache und Design

Oben rechts in der Titelleiste wählst du die Sprache (Weltkugel mit Kürzel, z. B. „DE“)
und das Design: Ein Klick auf das Symbol wechselt zwischen System, Hell und Dunkel.
Beides gilt für den jeweiligen Browser, jeder im Haushalt kann also seine eigene
Einstellung haben.
Ohne Auswahl gelten `DEFAULT_LANGUAGE` und `DEFAULT_THEME` aus der `.env`, ohne
`DEFAULT_LANGUAGE` die Sprache des Browsers. Zahlen, Beträge und Datumsangaben
folgen der gewählten Sprache.

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
endgültig entfernt, sondern mit ihrem ursprünglichen Pfad nach `.papierkorb/`
innerhalb der Bibliothek verschoben. Direkt nach dem Löschen lässt es sich mit
„Rückgängig“ zurücknehmen.

Unter „Papierkorb“ oben rechts siehst du alle gelöschten Dateien und Projekte:

- **Wiederherstellen** legt die Dateien an ihren ursprünglichen Ort zurück. Projektname,
  Notizen, Tags, Status, Quelle und die manuelle Zuordnung von Dateien kommen mit
  zurück. Liegt dort inzwischen eine andere Datei mit gleichem Namen, wird die
  wiederhergestellte als „(2)“ abgelegt. Überschrieben wird nie etwas.
- **Endgültig löschen** entfernt einen Eintrag, **Papierkorb leeren** alle.
- Nach `TRASH_DAYS` Tagen (Standard 30) werden Einträge automatisch endgültig gelöscht.
  Mit `TRASH_DAYS=0` bleiben sie, bis du sie selbst löschst.

Ordner, die ältere Versionen im Papierkorb abgelegt haben, erscheinen dort ebenfalls.
Ihre Frist beginnt mit dem Update, es verschwindet also nichts sofort.

## 3D-Vorschau

Eine `.gcode.3mf` enthält kein 3D-Modell mehr, sondern den fertigen G-Code. Die
3D-Vorschau zeigt deshalb die tatsächlichen Druckbahnen, ähnlich wie die Vorschau in
Bambu Studio:

- Einfärbung nach Filament, nach Linientyp (Außenwand, Infill, Stützen usw.) oder
  in einer frei wählbaren Farbe, praktisch bei hellen Filamenten
- Einfärbung nach Messwert wie im Slicer: Geschwindigkeit (mm/s), Volumenstrom
  (mm³/s), Bauteillüfter (%) und Düsentemperatur (°C), mit Farbskala von Blau (niedrig)
  nach Rot (hoch). Die Skala reicht vom 1. bis zum 99. Perzentil, einzelne Ausreißer
  verzerren sie also nicht.
- Einzelne Filamente und Linientypen lassen sich ausblenden
- Schichtregler mit Anzeige der Höhe, auch per Pfeiltasten bedienbar
- Wechsel zwischen den Platten eines Projekts mit den Pfeiltasten links und rechts
- Wiedergabe: Der Druckkopf fährt die Bahnen Schicht für Schicht ab (Taste ▶ oder
  Leertaste, Tempo 1×, 10× oder 50×). Mit dem Regler „Position in der Schicht“ lässt
  sich jede Stelle einer Schicht von Hand ansteuern.
- Tiefenschattierung (Ambient Occlusion): Rillen zwischen Bahnen und Schichten werden
  im Stillstand abgedunkelt, das Modell wirkt plastischer. Sie schaltet sich nur ein,
  wenn das Gerät schnell genug ist, und lässt sich abschalten.

Der G-Code wird nach dem Einlesen einmalig im Hintergrund aufbereitet und im
Datenordner zwischengespeichert. Bei sehr großen Drucken (100 MB G-Code) dauert das
etwa 10 bis 20 Sekunden, danach öffnet sich die Vorschau sofort. Beim Öffnen misst der
Viewer kurz die Leistung des Geräts und wählt beim Drehen automatisch die schönste
Darstellung, die noch flüssig läuft. three.js (r186) ist im Image enthalten, eine
Internetverbindung ist nicht nötig.

## Direktimport von Bambu-Druckern

Das Druckarchiv kann alle geslicten Drucke selbst von der SD-Karte deiner
Bambu-Drucker holen. Der Zugriff erfolgt per FTPS mit dem Zugangscode des Druckers.
Der LAN-only-Modus ist dafür nicht nötig, sofern die Firmware den Dateizugriff erlaubt.

**Einrichten:** Trage deine Drucker unter „Einstellungen“ → „Drucker für den
Direktimport“ ein: Name, IP-Adresse, Seriennummer und Zugangscode. Die Angaben findest
du am Drucker in den Netzwerkeinstellungen. Der Zugangscode wird gespeichert, aber nie
wieder angezeigt. Lässt du das Feld beim Ändern leer, bleibt er erhalten. Nach dem
Speichern werden die Drucker sofort abgefragt, das Ergebnis steht bei jedem Drucker.

Alternativ gibst du die Drucker in der `.env` vor, durchnummeriert ab 1:

```bash
PRINTER_1_NAME=P2S
PRINTER_1_IP=192.0.2.20
PRINTER_1_SERIAL=<Seriennummer>
PRINTER_1_CODE=<Zugangscode>
PRINTER_2_NAME=A1 mini
# …
```

So eingerichtete Drucker erscheinen in den Einstellungen schreibgeschützt. Bei gleicher
Seriennummer hat die `.env` Vorrang. Die `compose.yaml` reicht die `.env` über
`env_file` an den Container weiter, dafür ist Docker Compose 2.24 oder neuer nötig.

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

## Widget für Homepage und andere Dashboards

Unter `/api/stats` liefert das Druckarchiv seine Kennzahlen als JSON:

| Feld | Bedeutung |
|---|---|
| `projects`, `files`, `plates` | Anzahl Projekte, Dateien und geslicte Platten |
| `print_time_h`, `print_time_s` | Druckzeit aller Projekte in Stunden bzw. Sekunden |
| `filament_kg`, `filament_g` | Filamentverbrauch |
| `cost_eur`, `energy_kwh` | Kosten (Material und Strom) und Stromverbrauch |
| `new_files_7d`, `last_added` | Neue Dateien der letzten 7 Tage, Zeitpunkt der neuesten |
| `printers_ok`, `printers_total`, `last_printer_sync` | Erreichbare und eingerichtete Drucker, letzter Abruf |
| `trash_items`, `version` | Einträge im Papierkorb, Version des Druckarchivs |

Für [Homepage](https://gethomepage.dev) genügt das eingebaute Widget `customapi` in der
`services.yaml`. Eine Kachel zeigt bis zu vier Felder:

```yaml
- Druckarchiv:
    icon: mdi-printer-3d
    href: http://<server>:8000
    description: 3D-Drucke
    widget:
      type: customapi
      url: http://<server>:8000/api/stats
      refreshInterval: 60000
      mappings:
        - field: projects
          label: Projekte
          format: number
        - field: print_time_h
          label: Druckzeit
          format: float
          suffix: h
        - field: filament_kg
          label: Filament
          format: float
          suffix: kg
        - field: cost_eur
          label: Kosten
          format: float
          suffix: €
```

Statt der Kosten passen auch `new_files_7d` („Neu (7 Tage)“) oder `printers_ok`
(„Drucker online“). Laufen Homepage und Druckarchiv im selben Docker-Netz, trägst du bei
`url` die interne Adresse ein, z. B. `http://druckarchiv:8000/api/stats`. Das
funktioniert auch dann, wenn das Druckarchiv nach außen hinter einer Anmeldung am Proxy
liegt.

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

### Übersetzungen

Die deutschen Texte im Code sind zugleich die Schlüssel für die Übersetzungen in
`app/locales/<Sprache>.json`. Nach dem Ändern oder Hinzufügen von Texten zeigt

```bash
python tools/i18n_extract.py
```

je Sprache, was fehlt oder nicht mehr gebraucht wird. Die Tests schlagen fehl,
solange eine Übersetzung fehlt oder ihre Platzhalter (`{n}`, `{name}` …) nicht zum
deutschen Text passen. Eine neue Sprache braucht einen Eintrag in `LANGUAGES`,
`NUMBER_FORMAT`, `MONTHS` und `DAY_MONTH` in `app/i18n.py` sowie ihre JSON-Datei.

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
