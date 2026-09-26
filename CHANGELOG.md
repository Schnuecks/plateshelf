# Änderungen

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
