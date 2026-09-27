# Changelog

Entries up to version 0.6.0 (when the project was still called Druckarchiv) are in German.

## 0.7.3

- **Binary G-code** (`.bgcode`) from PrusaSlicer, the default for Prusa MK4, XL, Mini and
  Core One: print time, filament, colours, costs, printer, settings, object names and the
  preview image are read from the metadata blocks; the 3D preview unpacks the G-code
  (Heatshrink and MeatPack). Uploading accepts `.bgcode`.
- **Direct import from Prusa printers via PrusaLink** (MK4, MK3.9, XL, Mini, Core One):
  choose “Prusa (PrusaLink)” in the settings and enter the PrusaLink password, or use
  `PRINTER_<n>_TYPE=prusalink` with `PRINTER_<n>_IP` and `PRINTER_<n>_PASSWORD`. Files are
  fetched from the USB drive and internal storage, including subfolders.
- 3D preview: G-code without layer comments now gets its layers from the print height,
  and progress while preparing a preview is reported more evenly.
- New dependency `heatshrink2` (ISC license) for unpacking `.bgcode`.

## 0.7.2

- **Plain G-code files** (`.gcode`) from PrusaSlicer, SuperSlicer, OrcaSlicer, Cura,
  Creality Print and similar slicers. PlateShelf reads print time, layers, filament usage,
  colours, costs, printer and key settings from the slicer comments, shows the embedded
  preview image (including Prusa’s QOI thumbnails) and builds the 3D preview with line
  types for PrusaSlicer, OrcaSlicer and Cura. They can be uploaded like `.gcode.3mf`
  files.
- **Direct import from Klipper printers via Moonraker**, e.g. Creality K1/K2, Sovol SV08,
  Qidi, Elegoo Neptune 4 or Voron. Choose the printer type in the settings, or use
  `PRINTER_<n>_TYPE=moonraker` with `PRINTER_<n>_IP` and an optional
  `PRINTER_<n>_API_KEY` in the `.env`. Existing Bambu printers keep working unchanged.

## 0.7.1

- The trash folder inside the library is now called `.trash` (previously `.papierkorb`),
  independent of the interface language. Existing `.papierkorb` folders are renamed
  automatically on first access; deleted items can still be restored. If the library is
  mounted read-only, the old folder keeps being used.
- English URLs: `/settings`, `/trash` (with `/trash/<id>/restore`, `/trash/<id>/delete`,
  `/trash/empty`) and `/appearance`, plus English query parameters and anchors
  (e.g. `/settings#power`). The old German addresses redirect permanently, including
  submitted forms, so bookmarks and open tabs keep working.

## 0.7.0

- **Druckarchiv is now PlateShelf.** New name in the interface, the repository
  (`github.com/schnuecks/plateshelf`) and the image (`ghcr.io/schnuecks/plateshelf`).
  Existing installations keep their data: `druckarchiv.sqlite` is renamed to
  `plateshelf.sqlite` automatically, an existing `Vom Drucker` import folder keeps being
  used, and browser settings are kept. In your `.env`, replace `DRUCKARCHIV_IMAGE` with
  `PLATESHELF_IMAGE` (see “Upgrading from Druckarchiv” in the README).
- New logo, also used as the browser icon.
- New “About PlateShelf” page (link in the footer) with what PlateShelf does, version,
  links and license.
- English is now the default language when the browser language isn’t one of the six
  supported ones (previously German).
- The default folder for the direct import is now `From printer` (new installations).
- README in English with screenshots; the German README is kept as `README.de.md`.
  `.env.example` and `compose.yaml` are commented in English.
- `tools/make_screenshots.py` recreates the README screenshots.

## 0.6.0

- Optionale Anmeldung: mit `AUTH_USER` und `AUTH_PASSWORD` zeigt das Druckarchiv eine
  Anmeldeseite (alle Sprachen, hell/dunkel) und oben rechts „Abmelden“. Die Sitzung ist
  signiert und gilt `SESSION_DAYS` Tage; eine Passwortänderung beendet alle Sitzungen.
  Nach fünf Fehlversuchen wird eine Adresse fünf Minuten gesperrt.
- Skripte und Dashboards (z. B. das Homepage-Widget) melden sich per HTTP Basic Auth an.
- `/healthz` meldet ohne Anmeldung nur noch, dass das Druckarchiv läuft.
- Ohne `AUTH_USER`/`AUTH_PASSWORD` ändert sich nichts.

## 0.5.9

- Image zusätzlich für 64-Bit-ARM (arm64: Raspberry Pi 4/5, viele NAS) und
  32-Bit-ARM (arm/v7: ältere Raspberry Pi). Auf arm/v7 läuft uvicorn ohne die
  optionalen Beschleuniger uvloop und httptools, für die es dort keine fertigen
  Pakete gibt.
- Pull Requests bauen das Image für alle Plattformen probeweise mit, damit ein
  Fehler vor dem Release auffällt.
- Dependabot: wöchentliche Aktualisierungen für Python-Pakete, GitHub Actions und
  das Basisimage; Schwachstellen-Warnungen und automatische Sicherheitsupdates
  sind eingeschaltet.

## 0.5.8

- Browsertest in der CI: Playwright öffnet alle Seiten und die 3D-Vorschau in
  Chromium, klickt Einfärbungen, Einblendungen, Wiedergabe, Bildexport und
  Sprachwahl durch und schlägt bei jedem JavaScript-Fehler fehl. Ein Release wird
  nur gebaut, wenn Python- und Browsertests grün sind.
- Tests nutzen `httpx2` statt des veralteten `httpx` für den Testclient.

## 0.5.7

- Sicherheit: Änderungen werden nur noch von den eigenen Seiten angenommen (Prüfung
  von Origin bzw. Referer). Formulare fremder Webseiten, z. B. „Papierkorb leeren“,
  werden abgelehnt. Weitere erlaubte Adressen über `TRUSTED_ORIGINS`.
- Links zu GitHub und zur Lizenz öffnen in einem neuen Tab.
- 3D-Vorschau: zwei harmlose Shader-Warnungen unter Windows (X4000, X3595) behoben.
- GitHub Actions auf aktuelle Versionen (Node.js 24) gehoben, Build-System fest auf
  Ubuntu 24.04.

## 0.5.6

- 3D-Vorschau: Röhren in Schichtblöcken (je mindestens 40.000 Bahnstücke, höchstens
  etwa 32 Blöcke) mit eigenem Begrenzungsrahmen. Blöcke außerhalb des Bildes oder
  des Schichtbereichs werden übersprungen, z. B. zeichnet „Nur diese Schicht“ in
  der Mitte eines hohen Drucks nur noch einen Block statt alles darunter.

## 0.5.5

- 3D-Vorschau: Druckplatte mit Struktur, 10-mm- und 50-mm-Linien und Rand statt
  Linienraster; sie geht in die Tiefenschattierung ein, das Modell wirft einen
  weichen Kontaktschatten. Von unten gesehen wird sie ausgeblendet.
- 3D-Vorschau: korrekte Farbverrechnung. Filamentfarben, Farbskala, Leerfahrten und
  Markierungen werden linear beleuchtet und in sRGB ausgegeben; die Röhren haben
  jetzt dieselbe Farbe wie die Farbfelder in der Legende und im Slicer.
- 3D-Vorschau: Laden und Aufbereiten im Web Worker mit Fortschrittsanzeige, die
  Seite bleibt bedienbar; ohne Worker-Unterstützung wie bisher im Hauptthread.

## 0.5.4

- 3D-Vorschau: Vollbild (Knopf und Taste F), Bild als PNG speichern (mit
  Tiefenschattierung, Dateiname aus Projekt und Platte).
- 3D-Vorschau: Kameraposition wird pro Platte gemerkt, „Ansicht zurücksetzen“
  vergisst sie wieder.
- 3D-Vorschau: Zoom zum Mauszeiger bzw. zu den Fingern; Doppelklick oder
  Doppeltipp setzt die Ansicht zurück.
- Esc im Vollbild verlässt nur das Vollbild und nicht die Seite.

## 0.5.3

- 3D-Vorschau: Leerfahrten, Rückzüge und Nahtstellen lassen sich einblenden,
  jeweils mit Anzahl. Sie folgen dem Schichtregler; die Auswahl wird gemerkt.
- Vorschauformat Version 4 mit Leerfahrten und Rückzügen; vorhandene Vorschauen
  werden automatisch neu erzeugt.

## 0.5.2

- 3D-Vorschau: Einfärbung nach Geschwindigkeit, Volumenstrom, Bauteillüfter und
  Düsentemperatur mit Farbskala und Werten in der Legende. Messwerte, die im
  G-Code fehlen, sind ausgegraut.
- Vorschauformat Version 3 mit Messwerten je Punkt; vorhandene Vorschauen werden
  beim nächsten Öffnen bzw. im Hintergrund automatisch neu erzeugt.

## 0.5.1

- 3D-Vorschau: Wiedergabe des Drucks innerhalb der Schichten mit Düsenmarke,
  Regler „Position in der Schicht“, Tempo 1×/10×/50×, Leertaste zum Starten
  und Stoppen. Die Bahnen wachsen gleichmäßig nach Länge, auch mitten im Bahnstück.
- 3D-Vorschau: Tiefenschattierung (Ambient Occlusion) im Stillstand, aus den
  Normalen der Röhren berechnet und kantenbewahrend geglättet; aus der Ferne
  blendet sie sich aus, damit auf feinen Rillen kein Moiré entsteht. Abschaltbar.
- three.js von r160 auf r186 aktualisiert.

## 0.5.0

- Kennzahlen als JSON unter `/api/stats` (Projekte, Dateien, Druckzeit, Filament,
  Kosten, neue Dateien der letzten 7 Tage, Druckerstatus, Papierkorb), z. B. für
  das Widget `customapi` in Homepage. Vorlage für die `services.yaml` im README.

## 0.4.1

- Sprache und Design sitzen jetzt als kleine Symbole in der Titelleiste:
  Sprachmenü hinter der Weltkugel, ein Klick auf das Design-Symbol wechselt
  zwischen System, Hell und Dunkel. Der Abschnitt „Darstellung“ in den
  Einstellungen entfällt.
- Hinweis zum Direktimport in den Einstellungen zeigte `<code>` als Text.

## 0.4.0

- Oberfläche in Deutsch, Englisch, Französisch, Spanisch, Italienisch und
  Niederländisch, einschließlich Zahlen-, Betrags- und Datumsformaten, der
  3D-Vorschau und aller Meldungen.
- Design hell, dunkel oder wie das System.
- Sprache und Design unter „Einstellungen“ → „Darstellung“, gespeichert pro
  Browser; Vorgaben per `DEFAULT_LANGUAGE` und `DEFAULT_THEME`, ohne Vorgabe
  gilt die Sprache des Browsers.
- `tools/i18n_extract.py` prüft, ob alle Texte übersetzt sind.

## 0.3.7

- Datum der vom Drucker übernommenen Dateien auf Deutsch, z. B.
  „22. August 07:06“ statt „Aug 22 07:06“.

## 0.3.6

- Einstellungen zweispaltig: Drucker für den Direktimport links, Stromkosten
  rechts; auf schmalen Bildschirmen untereinander.
- Drucker werden ausschließlich unter „Einstellungen“ oder per Umgebungsvariablen
  eingerichtet, eine Druckerdatei wird nicht mehr gelesen.

## 0.3.5

- Drucker für den Direktimport werden unter „Einstellungen“ eingetragen und
  bearbeitet, mit Status des letzten Abrufs pro Drucker. Der Zugangscode wird
  nie wieder angezeigt.
- Alternativ Drucker per Umgebungsvariablen `PRINTER_<n>_NAME`, `_IP`, `_SERIAL`
  und `_CODE`; die `compose.yaml` reicht dafür die `.env` weiter.

## 0.3.0

- Papierkorb in der Oberfläche: gelöschte Dateien und Projekte auflisten,
  wiederherstellen, einzeln endgültig löschen oder den Papierkorb leeren.
- Wiederherstellen bringt Projektname, Notizen, Tags, Status, Quelle und die
  manuelle Zuordnung von Dateien zurück und überschreibt nie vorhandene Dateien.
- „Rückgängig“ direkt nach dem Löschen.
- Einträge werden nach `TRASH_DAYS` Tagen (Standard 30, 0 = nie) automatisch
  endgültig gelöscht. Papierkorb-Ordner älterer Versionen werden übernommen,
  ihre Frist beginnt mit dem Update.
- Kopfleiste auf schmalen Bildschirmen: Links überlappen nicht mehr.
- Tests für die Parser-Hilfsfunktionen `_norm_color` und `_to_float`.

## 0.2.1

- Lizenz: AGPL-3.0-or-later (Datei `LICENSE`, Hinweise in den Quelldateien,
  Link im Footer).

## 0.2.0

- Stromkosten: Strompreis und durchschnittliche Leistung pro Drucker unter
  „Einstellungen“; Kosten werden als Material plus Strom angezeigt.
- Footer mit Version und Autor auf allen Seiten, auch in der 3D-Vorschau.
- README überarbeitet; Compose-Vorlage und `.env.example` allgemein gehalten
  (Port statt fester Proxy-Anbindung, Beispieladressen statt eigener Umgebung).

## 0.1.1

- Image wird nur noch bei Versions-Tags hochgeladen (amd64), ältere Versionen
  werden automatisch entfernt; Pushes auf main bauen nur probeweise.
- Compose-Vorlage nutzt das Image aus der GitHub Container Registry.

## 0.1.0

Erste Version für den Eigenbetrieb.

- Liest Bambu-Studio-`.gcode.3mf` aus: Platten mit Vorschaubildern (schräg und von oben),
  Druckzeit, Gewicht, Schichten, Höhe, Filamente pro Slot mit Farbe, Menge und Kosten,
  wichtige Druckeinstellungen, MakerWorld-Angaben (Titel, Designer, Lizenz, Beschreibung, Foto).
- Projekte automatisch aus Ordnern oder Dateinamen (`_plate_3`, ` (2)`, `_gcode`, `.stl` usw.),
  manuelle Zuordnung, Notizen, Tags, Status, Quelle.
- Plattenleiste pro Projekt über alle Dateien, einzelne Platten direkt ansteuerbar;
  Vorschau-Duplikate einzeln exportierter Platten werden ausgeblendet.
- 3D-Vorschau der Druckbahnen als beleuchtete Röhren in echter Breite, Einfärbung nach
  Filament, Linientyp oder einfarbig, Schichtregler, automatische Anpassung an die
  Leistung des Geräts, Vorberechnung im Hintergrund.
- Upload per Drag & Drop, Löschen in einen Papierkorb-Ordner.
- Direktimport von Bambu-Druckern per FTPS (ohne LAN-only-Modus), jede gedruckte Platte
  bleibt als eigene Datei erhalten.
