<p align="center"><img src="app/static/logo.svg" width="96" alt="PlateShelf"></p>

# PlateShelf

[English](README.md) · **Deutsch**

PlateShelf ist ein selbstgehostetes Archiv für deine 3D-Drucke. Es liest
`.gcode.3mf`-Dateien aus Bambu Studio und OrcaSlicer, `.gcode`-Dateien aus PrusaSlicer,
OrcaSlicer, Cura und ähnlichen Slicern sowie binäre `.bgcode`-Dateien aus PrusaSlicer aus
und zeigt sie so übersichtlich an, wie du es aus dem
Slicer kennst: Platten mit Vorschaubildern, Druckzeit, Gewicht, Filamente mit Farbe und
Kosten, die wichtigsten Druckeinstellungen und eine 3D-Vorschau der Druckbahnen.

![Übersicht aller Projekte](docs/screenshots/overview.png)

## Inhalt

- [Funktionen](#funktionen)
- [Screenshots](#screenshots)
- [Installation](#installation)
- [Bedienung](#bedienung): Bibliothek und Projekte, Hochladen, Direktimport von
  Druckern, Druckverlauf, 3D-Vorschau, Kosten, Papierkorb, Sprache und Design
- [Betrieb](#betrieb): Einstellungen, Zugriffsschutz, Reverse-Proxy, Dashboard-Widget,
  Daten und Sicherung
- [Entwicklung](#entwicklung)
- [Lizenz](#lizenz)

## Funktionen

- **Übersicht** aller Projekte mit Suche und Filtern nach Material, Drucker, Status und Tags
- **Projektseite** mit allen Platten, Vorschaubildern (schräg und von oben), Druckzeit,
  Gewicht, Schichten, Höhe, Filamenten pro Slot und Kosten
- **Angaben von MakerWorld**, sofern in der Datei enthalten: Titel, Designer, Lizenz,
  Druckprofil, Beschreibung und Foto des gedruckten Modells
- **3D-Vorschau** der Druckbahnen mit Schichtregler, Wiedergabe des Drucks und Einfärbung
  nach Filament, Linientyp, Geschwindigkeit, Volumenstrom, Lüfter oder Temperatur
- **Automatische Projekte** aus Ordnern oder Dateinamen, zusätzlich manuell zuordenbar
- **Notizen, Tags, Status und Quelle** pro Projekt
- **Hochladen** per Drag & Drop und **Papierkorb** mit Wiederherstellen
- **Direktimport von Druckern:** Bambu Lab per FTPS (auch ohne LAN-only-Modus), Prusa
  über PrusaLink (MK4, MK3.9, XL, Mini, Core One) und Klipper-Drucker über Moonraker,
  etwa Creality K1/K2, Sovol SV08, Qidi oder Voron
- **Druckverlauf:** wann welche Datei auf welchem Drucker gedruckt wurde und ob der Druck
  durchlief
- **Kosten** aus Material und Strom
- **Sechs Sprachen** (Deutsch, English, Français, Español, Italiano, Nederlands) und
  **Design** hell, dunkel oder wie das System
- **Optionale Anmeldung** und Schutz vor Anfragen fremder Webseiten
- **Kennzahlen als JSON**, z. B. für ein Widget in [Homepage](https://gethomepage.dev)

## Screenshots

| Projekt | 3D-Vorschau |
|---|---|
| ![Projektseite](docs/screenshots/project.png) | ![3D-Vorschau, eingefärbt nach Linientyp](docs/screenshots/preview.png) |

Die Screenshots zeigen die englische Oberfläche; die Sprache lässt sich oben rechts umstellen.

## Installation

### Voraussetzungen

Du brauchst Docker mit Docker Compose 2.24 oder neuer. Das Image gibt es für PCs und
Server (amd64), für 64-Bit-ARM (arm64, z. B. Raspberry Pi 4/5 mit 64-Bit-System und
viele NAS) und für 32-Bit-ARM (arm/v7, ältere Raspberry Pi). Docker wählt automatisch
das passende.

### Schnellstart

```bash
mkdir plateshelf && cd plateshelf
curl -O https://raw.githubusercontent.com/schnuecks/plateshelf/main/compose.yaml
curl -o .env https://raw.githubusercontent.com/schnuecks/plateshelf/main/.env.example
mkdir -p library data
docker compose up -d
```

Danach ist PlateShelf unter `http://<server>:8000` erreichbar. Lege deine
`.gcode.3mf`-, `.gcode`- oder `.bgcode`-Dateien in den Ordner `library`. Sie erscheinen nach spätestens zwei
Minuten, sofort über „Bibliothek neu einlesen“ in der Übersicht.

Die Ordner `library` und `data` müssen für den Benutzer beschreibbar sein, unter dem
der Container läuft (`PUID` und `PGID` in der `.env`, Standard 1000). Sonst bricht der
Start mit dem Fehler „unable to open database file“ ab.

### Aktualisieren

```bash
docker compose pull && docker compose up -d
```

Die Datenbank wird beim Start bei Bedarf automatisch auf den neuen Stand gebracht.
Wenn sich das Format der 3D-Vorschau ändert, werden die Vorschauen danach einmal im
Hintergrund neu erzeugt. Was sich in einer Version geändert hat, steht im
[CHANGELOG](CHANGELOG.md).

### Umstieg vom Druckarchiv

Bis Version 0.6 hieß PlateShelf *Druckarchiv*. Deine Daten bleiben erhalten:

- Ersetze in der `.env` `DRUCKARCHIV_IMAGE` durch
  `PLATESHELF_IMAGE=ghcr.io/schnuecks/plateshelf:latest`, oder lade `compose.yaml` und
  `.env.example` wie im Schnellstart neu und übernimm deine Werte.
- Die Datenbank `druckarchiv.sqlite` im Datenordner wird beim ersten Start automatisch
  in `plateshelf.sqlite` umbenannt.
- Gibt es in der Bibliothek schon den Ordner `Vom Drucker` aus dem Direktimport, wird
  er weiter benutzt. Neue Installationen verwenden `From printer`.
- Im Browser gespeicherte Einstellungen (Einfärbung, Kameraposition usw.) bleiben
  erhalten.
- Der Papierkorb-Ordner `.papierkorb` in der Bibliothek wird in `.trash` umbenannt;
  gelöschte Einträge lassen sich weiter wiederherstellen.

## Bedienung

### Bibliothek und Projekte

Am besten eignen sich **geslicte Dateien**, denn nur sie enthalten Druckzeit, Gewicht
und Filamentverbrauch:

- **`.gcode.3mf`** aus Bambu Studio oder OrcaSlicer, über „Exportieren → Geslicte
  Plattendatei exportieren“ bzw. „Alle geslicten Dateien exportieren“. Eine Datei kann
  mehrere Platten enthalten.
- **`.gcode`** aus PrusaSlicer, SuperSlicer, OrcaSlicer, Cura, Creality Print und
  anderen Slicern. Eine Datei ist eine Platte. PlateShelf liest Druckzeit,
  Filamentverbrauch, Farben, Kosten, Drucker und die wichtigsten Einstellungen aus den
  Kommentaren, die der Slicer in die Datei schreibt, dazu das eingebettete
  Vorschaubild. Cura speichert nur die Filamentlänge; das Gewicht wird dann für PLA mit
  1,75 mm geschätzt.
- **`.bgcode`** (binärer G-Code) aus PrusaSlicer, Standard bei Prusa MK4, XL, Mini und
  Core One. Er enthält dieselben Angaben in kompakter Form, samt Vorschaubildern.

So entstehen Projekte:

- **Jeder Unterordner** der Bibliothek ist ein Projekt.
- **Dateien im Hauptordner** werden über ihren Namen zusammengefasst:
  `Benchy_plate_1.gcode.3mf` und `Benchy_plate_2.gcode.3mf` landen beide im Projekt
  „Benchy“. Zusätze wie `_plate_3`, ` (2)`, `_v2`, `_gcode` oder `.stl` werden dabei
  ignoriert.
- **Nichtssagende Namen** wie `(Unsaved)` oder zufällige Kennungen werden durch den
  Titel aus der Datei ersetzt, sofern vorhanden.
- Auf der Projektseite lässt sich jede Datei **von Hand einem anderen Projekt
  zuordnen**. Diese Zuordnung bleibt auch nach einem erneuten Einlesen erhalten.

Exportierst du Platten einzeln, enthält jede Datei zusätzlich die Vorschaubilder aller
anderen Platten. Liegt eine Platte in einer anderen Datei des Projekts geslict vor, wird
ihre Vorschau nicht doppelt angezeigt.

### Hochladen

Über „Hochladen“ oben rechts lädst du eine oder mehrere Dateien per Drag & Drop hoch,
auf Wunsch direkt in einen Projektordner. Vorhandene Dateien werden nie überschrieben:
Identische Dateien werden übersprungen, bei gleichem Namen mit anderem Inhalt wird
„(2)“ angehängt.

### Direktimport von Druckern

PlateShelf kann geslicte Drucke selbst von deinen Druckern holen:

- **Bambu Lab** per FTPS von der SD-Karte, mit dem Zugangscode des Druckers. Der
  LAN-only-Modus ist dafür nicht nötig, sofern die Firmware den Dateizugriff erlaubt.
- **Prusa-Drucker** über PrusaLink (MK4, MK3.9, XL, Mini, Core One). PlateShelf holt
  die `.bgcode`- und `.gcode`-Dateien vom USB-Stick oder internen Speicher.
- **Klipper-Drucker** über [Moonraker](https://moonraker.readthedocs.io), die
  Schnittstelle hinter Mainsail und Fluidd. Dazu gehören etwa Creality K1 und K2,
  Sovol SV08, Qidi, Elegoo Neptune 4 und Voron. PlateShelf holt die `.gcode`-Dateien
  aus dem G-Code-Ordner des Druckers.

**Einrichten in der Oberfläche:** Trage deine Drucker unter „Einstellungen“ →
„Drucker für den Direktimport“ ein und wähle den Typ:

- *Bambu Lab:* Name, IP-Adresse, Seriennummer und Zugangscode. Die Angaben findest du
  am Drucker in den Netzwerkeinstellungen.
- *Prusa (PrusaLink):* Name, IP-Adresse und das PrusaLink-Passwort. Du findest es am
  Drucker unter Einstellungen → Netzwerk → PrusaLink, der Benutzername ist `maker`.
  Ältere Firmware nutzt stattdessen einen API-Schlüssel: Trag ihn als Passwort ein.
- *Klipper (Moonraker):* Name und Adresse des Druckers. Ohne Portangabe nutzt
  PlateShelf den Moonraker-Standardport 7125; du kannst auch eine vollständige Adresse
  wie `http://192.0.2.21:7125` eintragen. Einen API-Schlüssel brauchst du nur, wenn
  Moonraker den PlateShelf-Rechner nicht unter `trusted_clients` führt. Den Schlüssel
  findest du in Mainsail oder Fluidd oder von einem vertrauenswürdigen Gerät aus unter
  `http://<drucker>:7125/access/api_key`.

Zugangscodes und API-Schlüssel werden gespeichert, aber nie wieder angezeigt; lässt du
das Feld beim Ändern leer, bleiben sie erhalten. Nach dem Speichern werden die Drucker
sofort abgefragt, das Ergebnis steht bei jedem Drucker.

**Einrichten über die `.env`:** Alternativ gibst du die Drucker durchnummeriert ab 1 vor:

```bash
PRINTER_1_NAME=P2S
PRINTER_1_IP=192.0.2.20
PRINTER_1_SERIAL=<Seriennummer>
PRINTER_1_CODE=<Zugangscode>
PRINTER_2_TYPE=moonraker
PRINTER_2_NAME=K1
PRINTER_2_IP=192.0.2.21
PRINTER_2_API_KEY=<optional>
PRINTER_3_TYPE=prusalink
PRINTER_3_NAME=MK4S
PRINTER_3_IP=192.0.2.22
PRINTER_3_PASSWORD=<PrusaLink-Passwort>
# …
```

`PRINTER_<n>_TYPE` ist `bambu` (Standard), `prusalink` oder `moonraker`. So eingerichtete Drucker
erscheinen in den Einstellungen schreibgeschützt. Ist ein Drucker an beiden Stellen
eingetragen, hat die `.env` Vorrang.

**Ablauf:**

- Alle `PRINTER_SYNC_INTERVAL` Sekunden (Standard 600) oder sofort über „Von Druckern
  holen“ in der Übersicht fragt PlateShelf die Dateien auf dem Drucker ab.
- Neue Drucke landen unter `From printer/<Druckername>/` in der Bibliothek und werden
  über ihren Namen zu Projekten zusammengefasst.
- Der Drucker speichert ein Projekt immer unter demselben Namen und überschreibt es,
  wenn eine andere Platte gedruckt wird. PlateShelf legt deshalb jede gedruckte
  Platte als eigene Datei ab (`…_plate_2.gcode.3mf`), so geht nichts verloren.
  Bei Prusa- und Klipper-Druckern bleibt der Dateiname erhalten; eine geänderte Datei
  ersetzt die bisherige Kopie.
- Jede Datei wird nur einmal geholt. Was du im Archiv löschst, wird nicht erneut
  importiert.
- Die Dateien auf dem Drucker werden nur gelesen, nie verändert oder gelöscht.
- Ist ein Drucker ausgeschaltet oder im Standby, wird er beim nächsten Durchlauf erneut
  abgefragt. Nur ein falscher Zugangscode, ein falsches Passwort oder ein falscher
  API-Schlüssel erscheint in der Übersicht als Warnung.

### Druckverlauf

Für die Drucker aus dem Direktimport schreibt PlateShelf jeden Druck mit: Start, Ende,
Dauer, Drucker und Ausgang (fertig, abgebrochen, fehlgeschlagen). „Verlauf“ in der
Titelleiste zeigt alle Drucke, die Projektseite die Drucke ihrer Dateien, die Übersicht,
wie oft ein Projekt gedruckt wurde.

- **Bambu Lab:** PlateShelf hält eine MQTT-Verbindung zum Drucker (Port 8883, derselbe
  Zugangscode) und erfährt so sofort von jedem Druck.
- **Klipper:** Moonraker führt selbst einen Verlauf, den PlateShelf jede Minute abruft.
- **Prusa:** PrusaLink kennt nur den aktuellen Druck; PlateShelf fragt ihn jede Minute ab
  und schreibt ihn mit.

Ein Druck wird der vom Drucker geholten Datei zugeordnet und damit ihrem Projekt. Liegt
die Datei noch nicht in der Bibliothek, holt PlateShelf sie sofort und ordnet den Druck
danach zu. Ein fertiger Druck setzt ein Projekt ohne Status auf „Gedruckt“.

Testdrucke und Ähnliches entfernst du mit „×“ aus dem Verlauf, auf der Verlaufsseite
oder der Projektseite. Sie zählen dann nicht mehr mit; hatte so ein Druck das Projekt
auf „Gedruckt“ gesetzt und bleibt kein anderer fertiger Druck übrig, wird der Status
zurückgesetzt.

Abschalten lässt sich der Verlauf unter „Einstellungen“ → „Druckverlauf“, etwa wenn ein
Bambu-Drucker keine zusätzliche MQTT-Verbindung bekommen soll. `PRINT_HISTORY=true` oder
`false` in der `.env` legt die Einstellung fest; der Schalter in der Oberfläche
entfällt dann.

### 3D-Vorschau

Eine geslicte Datei enthält kein 3D-Modell mehr, sondern den fertigen G-Code. Die
3D-Vorschau zeigt deshalb die tatsächlichen Druckbahnen, ähnlich wie dein Slicer.
Linientypen erkennt sie bei Bambu Studio, OrcaSlicer, PrusaSlicer und Cura.

**Einfärbung**

- nach Filament, nach Linientyp (Außenwand, Infill, Stützen usw.) oder einfarbig in
  einer frei wählbaren Farbe, praktisch bei hellen Filamenten
- nach Messwert wie im Slicer: Geschwindigkeit (mm/s), Volumenstrom (mm³/s),
  Bauteillüfter (%) und Düsentemperatur (°C), mit einer Farbskala von Blau (niedrig)
  nach Rot (hoch). Einzelne Ausreißer verzerren die Skala nicht.
- Einzelne Filamente und Linientypen lassen sich ausblenden.
- Leerfahrten, Rückzüge und Nahtstellen (Beginn jeder Außenwand) lassen sich
  einblenden, jeweils mit Anzahl. Das hilft, Fäden oder eine unruhige Naht zu erklären.

**Schichten und Wiedergabe**

- Schichtregler mit Anzeige der Höhe, auch mit den Pfeiltasten hoch und runter
- „Nur diese Schicht“ zeigt eine einzelne Schicht.
- Die Wiedergabe lässt den Druckkopf die Bahnen Schicht für Schicht abfahren (▶ oder
  Leertaste, Tempo 1×, 10× oder 50×). Mit „Position in der Schicht“ steuerst du jede
  Stelle einer Schicht von Hand an.
- Pfeiltasten links und rechts wechseln zwischen den Platten eines Projekts.

**Ansicht**

- Drehen mit der linken, Verschieben mit der rechten Maustaste, Zoomen mit dem Mausrad
  zum Mauszeiger hin; auf Touch-Geräten mit einem bzw. zwei Fingern
- Doppelklick oder Doppeltipp setzt die Ansicht zurück, die Kameraposition wird pro
  Platte gemerkt.
- Vollbild (auch Taste F) und „Bild speichern“ als PNG
- Das Modell steht auf einer Druckplatte mit 10- und 50-mm-Linien. Von unten gesehen
  wird die Platte ausgeblendet, damit die erste Schicht sichtbar bleibt.
- Die Tiefenschattierung dunkelt Rillen zwischen Bahnen und Schichten ab und lässt das
  Modell plastischer wirken. Sie ist nur im Stillstand aktiv, schaltet sich auf
  langsamen Geräten automatisch ab und lässt sich auch von Hand abschalten.

**Leistung:** Der G-Code wird nach dem Einlesen einmalig im Hintergrund aufbereitet und
im Datenordner zwischengespeichert. Bei sehr großen Drucken (100 MB G-Code) dauert das
etwa 10 bis 20 Sekunden, danach öffnet sich die Vorschau sofort. Beim Öffnen misst die
Vorschau kurz die Leistung des Geräts und wählt beim Drehen automatisch die schönste
Darstellung, die noch flüssig läuft. Eine Internetverbindung ist nicht nötig.

### Kosten

Die angezeigten Kosten setzen sich aus Material und Strom zusammen:

- **Material:** Verbrauch pro Filament mal dem Preis aus dem Filamentprofil im Slicer
  (etwa „Preis“ in Bambu Studio, „Kosten“ in PrusaSlicer). Ist dort nichts eingetragen,
  fehlt dieser Anteil. Cura speichert keinen Preis.
- **Strom:** Druckzeit mal durchschnittliche Leistung des Druckers mal Strompreis.
  Strompreis und Leistung stellst du unter „Einstellungen“ ein, auf Wunsch für jeden
  Drucker einzeln. Voreingestellt sind 0,35 € pro kWh und 100 Watt.

Die durchschnittliche Leistung misst du am genauesten mit einer Steckdose mit
Verbrauchsanzeige: Energie eines Drucks in Wh geteilt durch die Druckzeit in Stunden.
Verschleiß und Fehldrucke sind nicht enthalten.

### Papierkorb

Dateien oder ganze Projekte löschst du auf der Projektseite. Sie werden nicht sofort
entfernt, sondern mit ihrem ursprünglichen Pfad in den Ordner `.trash/` innerhalb
der Bibliothek verschoben. Direkt nach dem Löschen kannst du es mit „Rückgängig“
zurücknehmen.

Unter „Papierkorb“ oben rechts siehst du alle gelöschten Dateien und Projekte:

- **Wiederherstellen** legt die Dateien an ihren ursprünglichen Ort zurück.
  Projektname, Notizen, Tags, Status, Quelle und die manuelle Zuordnung kommen mit
  zurück. Liegt dort inzwischen eine andere Datei mit gleichem Namen, wird die
  wiederhergestellte als „(2)“ abgelegt. Überschrieben wird nie etwas.
- **Endgültig löschen** entfernt einen Eintrag, **Papierkorb leeren** alle.
- Nach `TRASH_DAYS` Tagen (Standard 30) werden Einträge automatisch endgültig gelöscht.
  Mit `TRASH_DAYS=0` bleiben sie, bis du sie selbst löschst.

### Sprache und Design

Oben rechts in der Titelleiste wählst du die Sprache (Weltkugel mit Kürzel, z. B. „DE“)
und das Design: Ein Klick auf das Symbol wechselt zwischen System, Hell und Dunkel.
Beides gilt für den jeweiligen Browser, jeder im Haushalt kann also seine eigene
Einstellung haben. Zahlen, Beträge und Datumsangaben folgen der gewählten Sprache.

Ohne eigene Auswahl gelten `DEFAULT_LANGUAGE` und `DEFAULT_THEME` aus der `.env`. Ist
keine Sprache vorgegeben, gilt die Sprache des Browsers, und Englisch, wenn diese keine
der sechs ist.

## Betrieb

### Einstellungen

Alle Einstellungen stehen in der `.env`. Die `compose.yaml` reicht sie an den Container
weiter.

**Container und Ordner**

| Variable | Standard | Bedeutung |
|---|---|---|
| `PLATESHELF_IMAGE` | `ghcr.io/schnuecks/plateshelf:latest` | Verwendetes Image |
| `PUID`, `PGID` | 1000 | Benutzer und Gruppe, unter denen der Container läuft |
| `TZ` | Europe/Berlin | Zeitzone |
| `PORT` | 8000 | Port auf dem Host |
| `LIBRARY` | `./library` | Ordner mit den `.gcode.3mf`-, `.gcode`- und `.bgcode`-Dateien |
| `DATA` | `./data` | Ordner für Datenbank, Vorschaubilder und 3D-Vorschauen |
| `LOG_LEVEL` | INFO | Ausführlichkeit der Protokolle (`docker compose logs`): `DEBUG`, `INFO` oder `WARNING` |

**Bibliothek**

| Variable | Standard | Bedeutung |
|---|---|---|
| `SCAN_INTERVAL` | 120 | Sekunden zwischen zwei Durchläufen durch die Bibliothek |
| `MAX_UPLOAD_MB` | 1024 | Größenlimit pro hochgeladener Datei in MB |
| `PREVIEW_PREBUILD` | true | 3D-Vorschauen nach dem Einlesen im Hintergrund vorbereiten |
| `TRASH_DAYS` | 30 | Tage bis zum endgültigen Löschen aus dem Papierkorb (0 = nie) |

**Direktimport**

| Variable | Standard | Bedeutung |
|---|---|---|
| `PRINTER_<n>_NAME`, `_IP`, `_SERIAL`, `_CODE` | – | Bambu-Drucker für den Direktimport (optional) |
| `PRINTER_<n>_TYPE` | `bambu` | `prusalink` für Prusa, `moonraker` für Klipper-Drucker |
| `PRINTER_<n>_PASSWORD` | – | PrusaLink-Passwort (Prusa) |
| `PRINTER_<n>_API_KEY` | – | Moonraker-API-Schlüssel, nur wenn nötig (Klipper) |
| `PRINTER_SYNC_INTERVAL` | 600 | Sekunden zwischen zwei Abfragen der Drucker |
| `PRINTER_IMPORT_DIR` | From printer | Zielordner für importierte Drucke in der Bibliothek |
| `PRINT_HISTORY` | – | `true` oder `false` legt den Druckverlauf fest; ohne Angabe gilt der Schalter in den Einstellungen (Standard an) |
| `PRINT_HISTORY_INTERVAL` | 60 | Sekunden zwischen zwei Abfragen von Klipper- und Prusa-Druckern |

**Oberfläche**

| Variable | Standard | Bedeutung |
|---|---|---|
| `DEFAULT_LANGUAGE` | – | Voreingestellte Sprache: `de`, `en`, `fr`, `es`, `it` oder `nl`; leer: Sprache des Browsers |
| `DEFAULT_THEME` | system | Voreingestelltes Design: `system`, `light` oder `dark` |

**Zugriffsschutz**

| Variable | Standard | Bedeutung |
|---|---|---|
| `AUTH_USER`, `AUTH_PASSWORD` | – | Anmeldung einschalten (beide setzen) |
| `SESSION_DAYS` | 30 | Wie lange eine Anmeldung im Browser gilt |
| `SECRET_KEY` | – | Schlüssel für die Anmeldung; leer: wird einmalig erzeugt und im Datenordner gespeichert |
| `TRUSTED_ORIGINS` | – | Weitere Adressen, die Änderungen schicken dürfen, durch Komma getrennt |

### Zugriffsschutz

Ohne weitere Einstellungen hat PlateShelf keine Anmeldung: Jeder, der es erreicht,
kann Dateien hochladen und löschen. Im Heimnetz ist das meist in Ordnung. Für mehr
Schutz gibt es zwei Wege, die sich auch kombinieren lassen.

**Eigene Anmeldung:** Setze `AUTH_USER` und `AUTH_PASSWORD` in der `.env`.

- Der Browser zeigt dann eine Anmeldeseite. Die Anmeldung gilt `SESSION_DAYS` Tage,
  oben rechts gibt es „Abmelden“.
- Nach fünf Fehlversuchen ist eine Adresse für fünf Minuten gesperrt.
- Ändert sich das Passwort, sind alle bestehenden Anmeldungen ungültig.
- Skripte und Dashboards melden sich mit denselben Zugangsdaten per HTTP Basic Auth an.
- `/healthz` bleibt für den Docker-Healthcheck erreichbar, meldet ohne Anmeldung aber
  nur, dass PlateShelf läuft.

**Anmeldung am Proxy**, z. B. mit Authelia oder Basic Auth. Das empfiehlt sich
besonders, wenn PlateShelf aus dem Internet erreichbar ist.

**Schutz vor fremden Webseiten:** Unabhängig davon nimmt PlateShelf Änderungen
(Hochladen, Löschen, Einstellungen …) nur von seinen eigenen Seiten an. Formulare, die
eine fremde Webseite im Hintergrund an PlateShelf schickt, werden abgelehnt.
Maßgeblich ist die Adresse, die der Browser mitsendet; hinter einem Proxy zählt der
weitergereichte Host (`X-Forwarded-Host`). Skripte ohne Browser (z. B. `curl`) sind
nicht betroffen. Sollen weitere Seiten Änderungen schicken dürfen, trage sie in
`TRUSTED_ORIGINS` ein, etwa `TRUSTED_ORIGINS=https://dashboard.example.com`.

### Hinter einem Reverse-Proxy

PlateShelf lauscht im Container auf Port 8000 und braucht keine besonderen
Einstellungen am Proxy. Ein Beispiel für Traefik (statt `ports:` in der
`compose.yaml`):

```yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.plateshelf.rule=Host(`plateshelf.example.com`)
      - traefik.http.services.plateshelf.loadbalancer.server.port=8000
```

### Widget für Homepage und andere Dashboards

Unter `/api/stats` liefert PlateShelf seine Kennzahlen als JSON:

| Feld | Bedeutung |
|---|---|
| `projects`, `files`, `plates` | Anzahl Projekte, Dateien und geslicte Platten |
| `print_time_h`, `print_time_s` | Druckzeit aller Projekte in Stunden bzw. Sekunden |
| `filament_kg`, `filament_g` | Filamentverbrauch |
| `cost_eur`, `energy_kwh` | Kosten (Material und Strom) und Stromverbrauch |
| `new_files_7d`, `last_added` | Neue Dateien der letzten 7 Tage, Zeitpunkt der neuesten |
| `printers_ok`, `printers_total`, `last_printer_sync` | Erreichbare und eingerichtete Drucker, letzter Abruf |
| `prints_30d`, `prints_failed_30d` | Fertige und abgebrochene oder fehlgeschlagene Drucke der letzten 30 Tage |
| `printing_now`, `last_print_end` | Gerade laufende Drucke, Ende des letzten Drucks |
| `trash_items`, `version` | Einträge im Papierkorb, Version von PlateShelf |

Für [Homepage](https://gethomepage.dev) genügt das eingebaute Widget `customapi` in der
`services.yaml`. Eine Kachel zeigt bis zu vier Felder:

```yaml
- PlateShelf:
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

- Statt der Kosten passen auch `new_files_7d` („Neu (7 Tage)“) oder `printers_ok`
  („Drucker online“).
- Ist die eigene Anmeldung eingeschaltet, trage im Widget zusätzlich `username` und
  `password` ein, also dieselben Werte wie `AUTH_USER` und `AUTH_PASSWORD`.
- Laufen Homepage und PlateShelf im selben Docker-Netz, nimm bei `url` die interne
  Adresse, z. B. `http://plateshelf:8000/api/stats`. Das funktioniert auch, wenn das
  PlateShelf nach außen hinter einer Anmeldung am Proxy liegt.

### Daten und Sicherung

- Die Dateien in der Bibliothek werden nie verändert, nur beim Löschen in den
  Papierkorb verschoben.
- Datenbank (SQLite), Vorschaubilder, 3D-Vorschauen und deine Notizen liegen im
  Datenordner. Für eine Sicherung genügt dieser Ordner; die 3D-Vorschauen darin werden
  bei Bedarf neu erzeugt.
- Neue und geänderte Dateien werden regelmäßig erkannt, auch auf Netzwerkfreigaben
  (SMB/NFS). Den Datenordner solltest du dagegen lokal ablegen, weil SQLite auf
  Netzwerkfreigaben unzuverlässig ist.

## Entwicklung

### Lokal starten und testen

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q                        # Tests ausführen
python tools/make_sample.py ./library      # Beispieldateien erzeugen
LIBRARY_DIR=./library DATA_DIR=./data uvicorn app.main:app --reload
```

`python tools/make_screenshots.py` erzeugt die Screenshots in diesem README neu.

Zusätzlich gibt es einen Browsertest, der alle Seiten und die 3D-Vorschau in Chromium
öffnet und bei jedem JavaScript-Fehler fehlschlägt:

```bash
pip install -r requirements-browser.txt
python -m playwright install chromium
python -m pytest -q tests/test_browser.py
```

### Übersetzungen

Die deutschen Texte im Code sind zugleich die Schlüssel für die Übersetzungen in
`app/locales/<Sprache>.json`. Nach dem Ändern oder Hinzufügen von Texten zeigt

```bash
python tools/i18n_extract.py
```

je Sprache, was fehlt oder nicht mehr gebraucht wird. Die Tests schlagen fehl, solange
eine Übersetzung fehlt oder ihre Platzhalter (`{n}`, `{name}` …) nicht zum deutschen
Text passen. Eine neue Sprache braucht einen Eintrag in `LANGUAGES`, `NUMBER_FORMAT`,
`MONTHS` und `DAY_MONTH` in `app/i18n.py` sowie eine eigene JSON-Datei.

### Tests, Builds und Releases

- Jeder Pull Request und jeder Push auf `main` durchläuft die Python-Tests und den
  Browsertest und baut das Image probeweise für amd64, arm64 und arm/v7. Veröffentlicht
  wird dabei nichts.
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

## Lizenz

Copyright (C) 2026 Schnuecks

PlateShelf ist freie Software unter der
[GNU Affero General Public License, Version 3 oder später](LICENSE)
(AGPL-3.0-or-later). Du darfst es nutzen, verändern und weitergeben.

Gibst du eine veränderte Fassung weiter oder bietest sie anderen über ein Netzwerk zur
Nutzung an, musst du ihnen den Quellcode deiner Fassung unter derselben Lizenz
zugänglich machen. Der Link „Quellcode auf GitHub“ im Footer erfüllt das für diese
Fassung; bei einer eigenen Fassung passe ihn auf deinen Quellcode an (`APP_REPO` in
`app/main.py`).

Das Programm wird ohne jede Gewährleistung bereitgestellt. Einzelheiten stehen in der
[Lizenz](LICENSE).

### Drittanbieter

`app/static/vendor/` enthält [three.js](https://threejs.org) und OrbitControls, beide
unter der MIT-Lizenz (siehe `three-LICENSE`). Die MIT-Lizenz ist mit der AGPL vereinbar.
