# Bedienung

[English](../en/usage.md) · **Deutsch** · [← Dokumentation](README.md)

## Bibliothek und Projekte

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

**Doppelte Dateien:** Liegt genau dieselbe Datei mehrfach in der Bibliothek, sagt das die
Projektseite unter dem Dateinamen und verlinkt die anderen Kopien. Das hilft beim Aufräumen,
etwa wenn derselbe Druck hochgeladen und zusätzlich vom Drucker geholt wurde.

## Hochladen

Über „Hochladen“ oben rechts lädst du eine oder mehrere Dateien per Drag & Drop hoch,
auf Wunsch direkt in einen Projektordner. Vorhandene Dateien werden nie überschrieben:
Identische Dateien werden übersprungen, bei gleichem Namen mit anderem Inhalt wird
„(2)“ angehängt.

## Mehrere Projekte bearbeiten

Fahr in der Übersicht mit der Maus über eine Karte (auf Handy und Tablet ist das Kästchen
immer da) und setze oben rechts den Haken. Sobald ein Projekt ausgewählt ist, erscheint
unten eine Leiste: Status setzen, Tag hinzufügen oder alle ausgewählten Projekte in den
Papierkorb verschieben. Jedes Projekt landet dort einzeln und lässt sich einzeln
wiederherstellen.

## Projekt herunterladen

Projekte mit mehreren Dateien haben neben dem Namen „Alle Dateien als ZIP“. Das ZIP enthält
die Druckdateien und eine `Projekt.txt` mit Status, Tags, Quelle und Notizen (der Dateiname
folgt der Sprache der Oberfläche).

## Fotos vom Druck

Auf der Projektseite gibt es den Bereich „Fotos vom Druck“ für Bilder vom fertigen Ergebnis,
etwa gleich am Drucker mit dem Handy aufgenommen. Das erste Foto wird in der Übersicht zum
Titelbild statt der Slicer-Vorschau; „Als Titelbild“ wählt ein anderes, „Nicht mehr
Titelbild“ kehrt zur Slicer-Vorschau zurück.

- JPEG, PNG und WebP bis 30 MB. iPhones wandeln HEIC-Fotos beim Hochladen im Browser
  automatisch um.
- PlateShelf dreht jedes Foto so, wie die Kamera es vermerkt hat, verkleinert es auf
  höchstens 2400 Pixel und entfernt alle Metadaten, auch die GPS-Position.
- Die Fotos liegen in der Bibliothek unter `.photos/`, wie der Papierkorb unter `.trash/`,
  und sind damit Teil deiner Bibliothekssicherung. Wandert ein Projekt in den Papierkorb,
  kommen seine Fotos beim Wiederherstellen mit zurück; wird es endgültig gelöscht,
  verschwinden sie ebenfalls.

## 3D-Vorschau

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

## Kosten

Die angezeigten Kosten setzen sich aus Material und Strom zusammen. Beides rechnet
PlateShelf bei jedem Aufruf mit den aktuellen Einstellungen: Änderst du einen Preis oder die
Leistung eines Druckers, stimmen sofort alle Projekte, die Statistik und die API, auch für
Drucke von vor Monaten.

### Material

Verbrauch pro Filament (aus der Datei) mal Preis pro kg. PlateShelf nimmt den ersten Preis,
der zutrifft:

1. eine **Preisregel**, die zum Filament passt,
2. sonst den **Preis aus der Datei**, also aus dem Filamentprofil im Slicer („Preis“ in
   Bambu Studio, „Kosten“ in PrusaSlicer); Cura speichert keinen,
3. sonst den **Standardpreis** pro kg.

Ohne all das fehlt der Materialanteil.

**Preisregeln** legst du unter „Einstellungen“ → „Kosten“ → „Preisregeln“ an: ein Stichwort
und ein Preis pro kg. Eine Regel gilt für jedes Filament, dessen Profilname oder Material
das Stichwort enthält. Groß- und Kleinschreibung spielen keine Rolle, und das Stichwort darf
ein Wortteil sein. Passen mehrere Regeln, gewinnt die mit dem **längsten Stichwort**, weil
sie am genauesten ist. Beispiel:

| Stichwort | € pro kg | gilt für |
|---|---|---|
| `PLA` | 20,00 | jedes PLA ohne genauere Regel |
| `PETG` | 22,00 | jedes PETG |
| `Silk` | 28,00 | z. B. „Bambu PLA Silk+“ („Silk“ ist länger als „PLA“) |
| `Panchroma` | 18,00 | jedes Polymaker Panchroma, z. B. „Panchroma PLA Matte“ |

Der Profilname ist das im Slicer gewählte Filamentprofil, etwa „Panchroma PLA Matte
@BBL P2S“, das Material der Filamenttyp, etwa „PLA“ oder „PETG“. Beides steht auf der
Projektseite in der Filamenttabelle jeder Platte. Neben jeder Regel zeigen die
Einstellungen, auf wie viele Platten sie gerade zutrifft; „trifft nichts“ heißt meist
Tippfehler. Zum Entfernen den Preis einer Regel leeren und speichern.

**Standardpreis:** für Filamente ohne passende Regel, wenn auch die Datei keinen Preis
mitbringt, etwa bei Cura oder Slicer-Profilen mit 0 €. Leer lassen, wenn diese Drucke keine
Materialkosten haben sollen.

**Preise aus den Slicer-Dateien ignorieren:** praktisch, wenn du die Preise in deinen
Slicer-Profilen nie gepflegt hast (Bambu Studio bringt eigene Beispielpreise mit). Dann
zählen nur Regeln und der Standardpreis, und „ein Standardpreis plus ein paar Regeln für
besondere Filamente“ deckt alles ab.

### Strom

Druckzeit mal durchschnittliche Leistung des Druckers mal Strompreis. Strompreis und
Leistung stellst du unter „Einstellungen“ → „Kosten“ ein, auf Wunsch für jeden Drucker
einzeln. Voreingestellt sind 0,35 € pro kWh und 100 Watt.

Die durchschnittliche Leistung misst du am genauesten mit einer Steckdose mit
Verbrauchsanzeige: Energie eines Drucks in Wh geteilt durch die Druckzeit in Stunden.
Verschleiß und Fehldrucke sind nicht enthalten.

## Papierkorb

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

## Sprache und Design

Oben rechts in der Titelleiste wählst du die Sprache (Weltkugel mit Kürzel, z. B. „DE“)
und das Design: Ein Klick auf das Symbol wechselt zwischen System, Hell und Dunkel.
Beides gilt für den jeweiligen Browser, jeder im Haushalt kann also seine eigene
Einstellung haben. Zahlen, Beträge und Datumsangaben folgen der gewählten Sprache.

Ohne eigene Auswahl gelten `DEFAULT_LANGUAGE` und `DEFAULT_THEME` aus der `.env`. Ist
keine Sprache vorgegeben, gilt die Sprache des Browsers, und Englisch, wenn diese keine
der sechs ist.

## Auf dem Homescreen

PlateShelf lässt sich wie eine App auf den Homescreen legen und öffnet dann ohne
Browserleiste: auf iPhone und iPad über Teilen → „Zum Home-Bildschirm“, auf Android und in
Chrome oder Edge über das Menü → „App installieren“ bzw. „Zum Startbildschirm hinzufügen“.
