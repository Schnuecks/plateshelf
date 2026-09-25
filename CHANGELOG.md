# Änderungen

## Unveröffentlicht

- Image wird nur noch bei Versions-Tags hochgeladen (amd64), ältere Versionen
  werden automatisch entfernt; Pushes auf main bauen nur probeweise.
- Compose-Vorlage nutzt das Image aus der GitHub Container Registry.
- README überarbeitet; Compose-Vorlage und `.env.example` allgemein gehalten
  (Port statt fester Proxy-Anbindung, Beispieladressen statt eigener Umgebung).

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
