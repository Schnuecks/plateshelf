<p align="center"><img src="app/static/logo.svg" width="96" alt="PlateShelf"></p>

# PlateShelf

[English](README.md) · **Deutsch**

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12">
  <a href="https://github.com/Schnuecks/plateshelf/pkgs/container/plateshelf"><img src="https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white" alt="Docker ready"></a>
  <img src="https://img.shields.io/badge/platform-amd64%20%7C%20arm64-E4572E" alt="Platform amd64 | arm64">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-4C9A2A" alt="License AGPL-3.0"></a>
  <img src="https://img.shields.io/badge/maintained-yes-4C9A2A" alt="Maintained">
</p>

PlateShelf ist ein selbstgehostetes Archiv für deine 3D-Drucke. Es liest
`.gcode.3mf`-Dateien aus Bambu Studio und OrcaSlicer, `.gcode`-Dateien aus PrusaSlicer,
OrcaSlicer, Cura und ähnlichen Slicern sowie binäre `.bgcode`-Dateien aus PrusaSlicer aus
und zeigt sie so übersichtlich an, wie du es aus dem
Slicer kennst: Platten mit Vorschaubildern, Druckzeit, Gewicht, Filamente mit Farbe und
Kosten, die wichtigsten Druckeinstellungen und eine 3D-Vorschau der Druckbahnen.

![Übersicht aller Projekte](docs/screenshots/overview.png)

## Funktionen

- **Übersicht** aller Projekte mit Suche und Filtern nach Material, Drucker, Status und Tags
- **Projektseite** mit allen Platten, Vorschaubildern, Druckzeit, Gewicht, Filamenten und
  Kosten, dazu Angaben von MakerWorld, sofern in der Datei enthalten
- **3D-Vorschau** der Druckbahnen mit Schichtregler, Wiedergabe des Drucks und Einfärbung
  nach Filament, Linientyp, Geschwindigkeit, Volumenstrom, Lüfter oder Temperatur
- **Automatische Projekte** aus Ordnern oder Dateinamen, Notizen, Tags und Status pro Projekt,
  eigene **Fotos vom Druck** als Titelbild
- **Hochladen** per Drag & Drop, **Papierkorb** mit Wiederherstellen, mehrere Projekte auf
  einmal bearbeiten und Projekte als ZIP
- **Tägliche automatische Sicherungen**, Zurückspielen in der Oberfläche
- **Direktimport von Druckern:** Bambu Lab, Prusa über PrusaLink und Klipper-Drucker über
  Moonraker (etwa Creality K1/K2, Sovol SV08, Qidi, Voron)
- **Druckverlauf und Statistik:** was wann auf welchem Drucker gedruckt wurde und ob der
  Druck durchlief, mit Notiz und Bewertung pro Druck
- **Benutzerkonten** mit Rollen (Admin, Benutzer, Nur lesen), Geräteliste und API-Tokens
  nur zum Lesen
- **Sechs Sprachen**, helles und dunkles Design, als App auf dem Homescreen, Kennzahlen als JSON
  für Dashboards

| Projekt | 3D-Vorschau |
|---|---|
| ![Projektseite](docs/screenshots/project.png) | ![3D-Vorschau, eingefärbt nach Linientyp](docs/screenshots/preview.png) |

| Druckverlauf | Statistik |
|---|---|
| ![Druckverlauf mit laufendem Druck](docs/screenshots/history.png) | ![Statistik der letzten zwölf Monate](docs/screenshots/statistics.png) |

Die Screenshots zeigen die englische Oberfläche; die Sprache lässt sich oben rechts umstellen.

## Schnellstart

Du brauchst Docker mit Docker Compose 2.24 oder neuer (amd64 oder arm64, z. B. Raspberry
Pi 4/5).

```bash
mkdir plateshelf && cd plateshelf
curl -O https://raw.githubusercontent.com/schnuecks/plateshelf/main/compose.yaml
curl -o .env https://raw.githubusercontent.com/schnuecks/plateshelf/main/.env.example
mkdir -p library data
docker compose up -d
```

Öffne `http://<server>:8000` und lege das Admin-Konto an; den Einrichtungscode findest du im
Protokoll (`docker compose logs plateshelf`). Danach legst du deine Druckdateien in den Ordner `library`.
Aktualisieren: `docker compose pull && docker compose up -d`.

## Dokumentation

- [Installation](docs/de/installation.md): Aktualisieren, Reverse-Proxy, Daten und Sicherung
- [Konfiguration](docs/de/configuration.md): alle Einstellungen, Benutzerkonten und Geheimnisse
- [Bedienung](docs/de/usage.md): Bibliothek, Projekte, 3D-Vorschau, Kosten, Papierkorb
- [Drucker und Druckverlauf](docs/de/printers.md): Direktimport, Verlauf, Statistik
- [Dashboard-Widget](docs/de/dashboard.md): Kennzahlen für Homepage und andere
- [Entwicklung](docs/de/development.md): lokal starten, Tests, Übersetzungen

Was sich in einer Version geändert hat, steht im [CHANGELOG](CHANGELOG.md) (auf Englisch).

## Unterstützen

PlateShelf ist kostenlos und bleibt es. Wenn es dir Zeit spart, kannst du mir einen Kaffee
ausgeben; das hält das Projekt am Laufen. Danke!

<p>
  <a href="https://buymeacoffee.com/il6hhwtzr6"><img src="https://img.shields.io/badge/Buy_me_a_coffee-10_%E2%82%AC-FFDD00?logo=buymeacoffee&logoColor=black" alt="Buy me a coffee: 10 €"></a>
  <a href="https://buymeacoffee.com/il6hhwtzr6"><img src="https://img.shields.io/badge/Buy_me_a_coffee-25_%E2%82%AC-FFDD00?logo=buymeacoffee&logoColor=black" alt="Buy me a coffee: 25 €"></a>
  <a href="https://buymeacoffee.com/il6hhwtzr6"><img src="https://img.shields.io/badge/Buy_me_a_coffee-50_%E2%82%AC-FFDD00?logo=buymeacoffee&logoColor=black" alt="Buy me a coffee: 50 €"></a>
</p>

## Rückmeldungen

Fehler und Ideen sind als [Issue auf GitHub](https://github.com/schnuecks/plateshelf/issues)
willkommen. Bei Fehlern füge bitte den Text aus „Einstellungen“ → „Diagnose“ → „Kopieren“
ein; er enthält keine Zugangsdaten, Adressen oder Druckernamen. Siehe auch
[CONTRIBUTING](CONTRIBUTING.md) und [SECURITY](SECURITY.md).

## Lizenz

Copyright (C) 2026 Schnuecks

PlateShelf ist freie Software unter der
[GNU Affero General Public License, Version 3 oder später](LICENSE)
(AGPL-3.0-or-later). Du darfst es nutzen, verändern und weitergeben.

Gibst du eine veränderte Fassung weiter oder bietest sie anderen über ein Netzwerk zur
Nutzung an, musst du ihnen den Quellcode deiner Fassung unter derselben Lizenz
zugänglich machen. Der Link „Quellcode auf GitHub“ im Footer erfüllt das für diese
Fassung; bei einer eigenen Fassung passe ihn auf deinen Quellcode an (`APP_REPO` in
`app/web.py`).

Das Programm wird ohne jede Gewährleistung bereitgestellt. Einzelheiten stehen in der
[Lizenz](LICENSE). `app/static/vendor/` enthält [three.js](https://threejs.org) und
OrbitControls, beide unter der MIT-Lizenz (siehe `three-LICENSE`), die mit der AGPL
vereinbar ist. Die Schriften Barlow in `app/static/fonts/` stehen unter der SIL Open Font
License (siehe `OFL.txt` dort); PlateShelf liefert sie selbst aus, keine Seite lädt etwas von
Google oder anderen Servern.
